"""Cached prerequisite probes for guided init (gui-init spec §3, plan C1).

These feed the ``prerequisites`` block of ``GET /api/init-status``. All probes
are TTL-cached + single-flighted so a polling UI never hammers the chat
provider or Bilibili (validate_cookie alone is a ~30s round-trip). Bound to a
RuntimeContext and read ``ctx.llm_registry`` / ``ctx.config`` lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from openbiliclaw.bilibili.auth import AuthManager, resolve_runtime_cookie

logger = logging.getLogger(__name__)

# Strict readiness: a prereq is "ok" only when a REAL probe request succeeds.
# Success caches longer; a failure/timeout caches briefly so a service that
# just came up (or finished a cold model load) greens within seconds rather
# than staying red for the full success-TTL. Timeout is generous enough to
# cover a cold Ollama load but still fails (not optimistically passes) if the
# service never answers.
#
# The chat probe is a real (billable) completion, so its success TTL is
# generous: a green checkmark going stale for a few minutes is harmless,
# while a 30s TTL meant an open polling page burned a provider request
# every 30s — users spotted the recurring 5-in/10-out lines on their
# DeepSeek bill.
_CHAT_OK_TTL = 300.0
_CHAT_FAIL_TTL = 8.0
# A local 7B model can need more than 15s for its first response while Ollama
# loads weights from disk.  Thirty seconds still bounds a broken endpoint, but
# avoids a false red checklist immediately after selecting a real local model.
_CHAT_PROBE_TIMEOUT = 30.0
# Public so ``api.source_auth.verify`` can wrap its own B站 probe in the same
# bound. The two entry points write one shared verdict store, so a divergent
# timeout would mean they disagree about when a slow B站 counts as unreachable.
BILI_PROBE_TIMEOUT_SECONDS = 12.0
# The verdict TTLs that used to live here (60s ok / 10s fail) now belong to
# ``api.source_auth.probe_cache``, which owns the verdicts themselves; the
# freshness question is answered by ``ProbeVerdict.is_current()``.

# A cookie can be perfectly valid while the *probe request* dies in transit.
# The client already bypasses env/system proxies (BilibiliAPIClient
# trust_env=False — proxy exits trip B站 risk control; field report 2026-07),
# so a transport failure here means direct connectivity itself is broken:
# genuine network outage, a TUN/global-mode proxy intercepting at the network
# layer, or a misconfigured [bilibili].proxy override.
_BILI_NETWORK_HINT = (
    "检测已绕过系统代理直连 B站 仍失败：请检查本机网络；"
    "TUN / 全局模式代理请为 bilibili.com 添加直连分流规则；"
    "如果你的网络必须走代理才能访问 B站，可在 config.toml 的 [bilibili] proxy 单独指定。"
)

_PLATFORM_SOURCE_FIELDS = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "weibo",
    "instagram",
)


class InitPrereqs:
    """TTL-cached prerequisite probes bound to a RuntimeContext."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._chat_value = False
        self._chat_at = float("-inf")
        # Classified, user-facing reason the last chat probe failed ("" when
        # ready). Lets POST /api/init and /api/init-status distinguish 无效
        # API Key / 服务不可达 / 模型不存在 instead of one generic line.
        self._chat_detail = ""
        self._chat_lock = asyncio.Lock()
        # Fallback verdict, used only when the shared probe store holds nothing
        # for B站: before the first probe ("checking"), or when there is no
        # credential to probe at all ("failed"). Every verdict that came from an
        # actual probe lives in the shared store instead — see _bili_probes().
        self._bili_unprobed = "checking"
        self._bili_unprobed_detail = ""
        self._bili_lock = asyncio.Lock()

    @staticmethod
    def _bili_probes() -> Any:
        """The one store holding B站's live-probe verdict.

        Shared with ``GET /api/sources/status`` so a probe fired by either
        surface is visible to both. Before this, guided-init and the settings
        page each kept a private cache of the same question and could hold
        opposite answers about one cookie (spec D3 on the verdict axis).

        Imported lazily because ``openbiliclaw.api`` executes the whole FastAPI
        app at package-import time, and ``runtime`` must not drag that in — the
        same reason ``api.runtime_context`` imports *this* class lazily.
        """
        from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES

        return LIVE_PROBES

    @staticmethod
    def _bili_fingerprint(cookie: str) -> str:
        """Identity digest of *cookie*, tagging the verdicts recorded below.

        Recorded here as well as by the verify route because the store is
        shared: an unfingerprinted verdict is one the credential write gate
        must conservatively re-probe, which would quietly undo the "guided init
        already checked this cookie, don't check it twice" saving that having
        one store buys in the first place.
        """
        from openbiliclaw.api.source_auth.write import credential_fingerprint

        return credential_fingerprint("bilibili", cookie)

    def peek_chat(self) -> bool:
        """Last cached chat probe result, without firing a new probe.

        Used by already-initialized status reads where the checklist is
        informational only — a live (billable) probe is not justified.
        """
        return self._chat_value

    def peek_chat_detail(self) -> str:
        """Classified reason the last chat probe failed ("" when ready).

        Populated by :meth:`chat_ready` from the probe's own exception via
        ``describe_llm_failure``, so callers (POST /api/init 409 detail,
        /api/init-status ``llm_not_ready`` detail) can tell an invalid API key
        from an unreachable service from a missing model instead of surfacing
        one generic "not ready" banner (project rule 7: propagate the cause).
        """
        return self._chat_detail

    def peek_bilibili(self) -> str:
        """Last known Bilibili probe result, without firing a new probe.

        A transport failure still reads as ``failed`` here even though the same
        verdict reads as ``unverified`` in the source-auth contract. That is
        deliberate, not a leak: guided-init must block a setup it cannot
        confirm, while the settings page must not tell a user their cookie
        expired because a proxy flaked. One verdict, two honest readings.
        """
        verdict = self._bili_probes().peek("bilibili")
        if verdict is None:
            return str(self._bili_unprobed)
        return "ok" if verdict.authenticated else "failed"

    def peek_bilibili_detail(self) -> str:
        """Why the last Bilibili probe failed ("" when it succeeded)."""
        verdict = self._bili_probes().peek("bilibili")
        if verdict is None:
            return str(self._bili_unprobed_detail)
        return "" if verdict.authenticated else str(verdict.detail)

    def has_cached_readiness(self) -> bool:
        """Whether chat and Bilibili have both completed at least one probe.

        A guided-init run can only start after those probes complete.  Its
        terminal status can therefore reuse them immediately after failure or
        cancellation instead of blocking the UI on another cold chat request.
        A fresh process has no such cache and will correctly probe live.
        """
        return self._chat_at != float("-inf") and self.peek_bilibili() != "checking"

    async def chat_ready(self) -> bool:
        """Whether chat completions can *currently* be served.

        Registry-built is necessary-not-sufficient (a configured Ollama whose
        model was never pulled 404s at call time), so this does a real
        ``health_check`` (tiny completion) — cached, single-flighted, and
        strict on timeout (matches embedding readiness).

        The first instance is probed first; when it fails, every remaining
        usable instance in the configured ordered chain is probed in turn.
        A healthy fallback means chat genuinely works and init must not be
        blocked just because an earlier endpoint is down.
        """
        registry = getattr(self._ctx, "llm_registry", None)
        if registry is None:
            return False
        ttl = _CHAT_OK_TTL if self._chat_value else _CHAT_FAIL_TTL
        if time.monotonic() - self._chat_at < ttl:
            return self._chat_value
        async with self._chat_lock:
            ttl = _CHAT_OK_TTL if self._chat_value else _CHAT_FAIL_TTL
            if time.monotonic() - self._chat_at < ttl:
                return self._chat_value
            default_name = str(getattr(registry, "default_provider", "") or "").strip()
            is_chat_capable = getattr(registry, "is_chat_capable", None)
            default_is_chat_capable = not default_name or not callable(is_chat_capable)
            if callable(is_chat_capable) and default_name:
                default_is_chat_capable = bool(is_chat_capable(default_name))

            failure: BaseException | None = None
            if default_is_chat_capable:
                default_provider = registry.get(default_name) if default_name else registry.get()
                ready, failure = await self._probe_chat_provider(default_provider)
            else:
                # Defensive backstop for registries built by older code or
                # injected by tests/extensions. A readiness probe is a real
                # chat request and must never hit an embedding-only provider.
                logger.warning(
                    "Chat readiness skipped non-chat default provider %s",
                    default_name,
                )
                ready = False
            if not ready:
                configured_chain = getattr(registry, "fallback_chain", None)
                if isinstance(configured_chain, list) and configured_chain:
                    fallback_names = [
                        str(name or "").strip()
                        for name in configured_chain
                        if str(name or "").strip() and str(name or "").strip() != default_name
                    ]
                else:
                    fallback_name = str(getattr(registry, "fallback_provider", "") or "").strip()
                    fallback_names = [fallback_name] if fallback_name else []
                for fallback_name in fallback_names:
                    if not callable(is_chat_capable) or not is_chat_capable(fallback_name):
                        continue
                    ready, fallback_failure = await self._probe_chat_provider(
                        registry.get(fallback_name)
                    )
                    if ready:
                        logger.info(
                            "Chat readiness: earlier instance %s failed the probe but "
                            "fallback instance %s answered — chat is served via fallback.",
                            default_name,
                            fallback_name,
                        )
                        break
                    if failure is None:
                        # Keep the primary's cause when present; only fall back to
                        # the fallback provider's exception if the default failed
                        # without one (non-chat default / bare False health_check).
                        failure = fallback_failure
            self._chat_value = ready
            self._chat_at = time.monotonic()
            self._chat_detail = "" if ready else self._describe_chat_failure(failure)
            return ready

    @staticmethod
    def _describe_chat_failure(exc: BaseException | None) -> str:
        """Classified Chinese copy for a failed chat probe ("" when unknown)."""
        if exc is None:
            return ""
        # describe_llm_failure is the user-facing sibling of
        # classify_llm_unavailability: it emits ready-made distinguishing copy
        # for auth (无效 API Key) / connection (服务不可达) / model_not_found
        # (模型不存在), which classify_llm_unavailability (machine kind, no auth
        # bucket) cannot. Imported lazily — see _bili_probes() for the reason.
        from openbiliclaw.llm.base import describe_llm_failure

        return describe_llm_failure(exc) or ""

    async def _probe_chat_provider(self, provider: Any) -> tuple[bool, BaseException | None]:
        """One strict, bounded health_check.

        Returns ``(ok, failure)`` where ``failure`` is the exception that
        explains a False result (``None`` when ok, or when the provider merely
        returned a falsy health_check without raising) so callers can classify
        the cause instead of only knowing that chat is not ready.
        """
        try:
            ok = bool(await asyncio.wait_for(provider.health_check(), timeout=_CHAT_PROBE_TIMEOUT))
            return ok, None
        except TimeoutError as exc:
            # Strict: the prereq must confirm a REAL request succeeded. A
            # timeout means we could NOT confirm the provider answers within
            # a (generous, cold-load-tolerant) window → report not-ready so
            # the checklist never greenlights an unverified chat service.
            logger.debug("Chat readiness probe timed out; reporting not ready")
            return False, exc
        except Exception as exc:
            logger.debug("Chat readiness probe errored", exc_info=True)
            return False, exc

    async def bilibili_check(self) -> str:
        """``ok`` / ``failed`` / ``checking`` for the configured B站 cookie.

        Real validation (validate_cookie hits B站 nav) but TTL-cached so polls
        don't repeat the ~30s round-trip: success cached 60s, failure 10s. The
        verdict and its TTL both live in the shared probe store, so an explicit
        ``POST /api/sources/{slug}/verify`` satisfies this check too, and a
        probe fired here is immediately visible to the settings page.
        """
        cfg = getattr(self._ctx, "config", None)
        cookie = ""
        if cfg is not None:
            # Resolve through the same helper as sources_status /
            # sources_credentials / the runtime client (invariant I1): config.toml
            # is the mirror, data/bilibili_cookie.json is the runtime store, and
            # CLI ``auth login`` writes only the file. Reading config alone made
            # this probe report "not logged in" while the settings page reported
            # "ready" for the very same credential (spec D3).
            configured = str(getattr(getattr(cfg, "bilibili", None), "cookie", "") or "")
            data_path = getattr(cfg, "data_path", None)
            if data_path is None:
                cookie = configured.strip()
            else:
                try:
                    cookie = resolve_runtime_cookie(
                        data_dir=data_path, configured_cookie=configured
                    ).strip()
                except Exception:  # noqa: BLE001 - unreadable store must not crash the probe
                    logger.debug("bilibili cookie store unreadable; falling back to config")
                    cookie = configured.strip()
        probes = self._bili_probes()
        if cfg is None or not cookie:
            # No credential to probe. Any stored verdict described a cookie that
            # is no longer there, so it is dropped rather than left to answer
            # for a different one — and nothing is recorded in its place, since
            # a verdict about a credential that does not exist would be invented
            # evidence (invariant I3).
            probes.clear("bilibili")
            self._bili_unprobed = "failed"
            self._bili_unprobed_detail = "后端还没有收到 B站 Cookie。"
            return "failed"

        verdict = probes.peek("bilibili")
        if verdict is not None and verdict.is_current():
            return "ok" if verdict.authenticated else "failed"

        async with self._bili_lock:
            verdict = probes.peek("bilibili")
            if verdict is not None and verdict.is_current():
                return "ok" if verdict.authenticated else "failed"
            proxy = str(getattr(getattr(cfg, "bilibili", None), "proxy", "") or "").strip()
            # The hint must match the actual transport: default is a direct
            # connection (client bypasses env/system proxies), but an explicit
            # [bilibili].proxy override means the failure is on THAT proxy.
            network_hint = (
                f"当前经 config.toml [bilibili] proxy（{proxy}）检测 B站 失败："
                "请确认该代理可达且能访问 B站，或清空该配置改回直连。"
                if proxy
                else _BILI_NETWORK_HINT
            )
            fingerprint = self._bili_fingerprint(cookie)
            try:
                manager = AuthManager(data_dir=cfg.data_path, proxy=proxy or None)
                status = await asyncio.wait_for(
                    manager.validate_cookie(cookie), timeout=BILI_PROBE_TIMEOUT_SECONDS
                )
                if status.authenticated:
                    probes.record(
                        "bilibili",
                        authenticated=True,
                        detail="",
                        fingerprint=fingerprint,
                        username=str(getattr(status, "username", "") or "").strip(),
                        user_id=int(getattr(status, "user_id", 0) or 0),
                    )
                else:
                    message = str(getattr(status, "message", "") or "").strip()
                    # ``network_error`` is carried through rather than flattened
                    # into the detail string: the contract needs the distinction
                    # to keep a flaky proxy from reading as an expired cookie.
                    if getattr(status, "network_error", False):
                        probes.record(
                            "bilibili",
                            authenticated=False,
                            detail=f"检测请求失败（{message}）。{network_hint}",
                            network_error=True,
                            fingerprint=fingerprint,
                        )
                    else:
                        probes.record(
                            "bilibili",
                            authenticated=False,
                            detail=message or "当前 Cookie 未登录或已失效。",
                            fingerprint=fingerprint,
                        )
            except TimeoutError:
                logger.debug("Bilibili cookie probe timed out", exc_info=True)
                probes.record(
                    "bilibili",
                    authenticated=False,
                    detail=f"检测超时，B站 接口未在时限内响应。{network_hint}",
                    network_error=True,
                    fingerprint=fingerprint,
                )
            except Exception as exc:
                logger.debug("Bilibili cookie probe errored", exc_info=True)
                probes.record(
                    "bilibili",
                    authenticated=False,
                    detail=f"检测请求失败（{exc}）。{network_hint}",
                    network_error=True,
                    fingerprint=fingerprint,
                )
            return self.peek_bilibili()

    def enabled_platforms(self) -> list[str]:
        """Platform source families currently enabled in config."""
        sources = getattr(getattr(self._ctx, "config", None), "sources", None)
        if sources is None:
            return []
        return [
            name
            for name in _PLATFORM_SOURCE_FIELDS
            if getattr(getattr(sources, name, None), "enabled", False)
        ]

    def source_capability_readiness(self, slug: str, capability: str) -> str:
        """Return machine auth readiness for a guided-init capability.

        The provider is pure/local, so setup and init can share the same
        capability decision as ``GET /api/sources/status`` without adding a
        network probe to a polling path. Older providers without a capability
        map retain their source-wide contract semantics.
        """

        from openbiliclaw.api.source_auth.providers import (
            SOURCE_AUTH_PROVIDERS,
            SourceAuthContext,
        )

        provider = SOURCE_AUTH_PROVIDERS.get(str(slug).strip().lower())
        database = getattr(self._ctx, "database", None)
        config = getattr(self._ctx, "config", None)
        if provider is None or database is None or config is None:
            return "unverified"
        contract = provider(SourceAuthContext(cfg=config, database=database))
        state = contract.capabilities.get(str(capability).strip())
        if state is not None:
            if state.readiness is not None:
                return state.readiness
            if state.ready:
                return "ready"
            if state.state in {"login_required", "stale"}:
                return state.state
            return "unverified"
        return "ready" if contract.capability_ready(capability) else "login_required"

    def source_capability_ready(self, slug: str, capability: str) -> bool:
        """Whether *slug* is currently admissible for *capability*."""

        return self.source_capability_readiness(slug, capability) == "ready"
