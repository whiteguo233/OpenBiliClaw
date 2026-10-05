"""Providers for search-backed discovery query inspiration."""

from __future__ import annotations

import ast
import asyncio
import html
import json
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from xml.etree import ElementTree

import httpx

from openbiliclaw.discovery.inspiration import ExaPreviewItem
from openbiliclaw.proc import no_window_kwargs


def _overseas_routing_kwargs(transport: httpx.AsyncBaseTransport | None) -> dict[str, Any]:
    """Routing kwargs for an overseas search-API ``httpx.AsyncClient``.

    Exa / You.com / Serply are overseas SaaS endpoints, so they follow the
    process-wide ``[network]`` routing policy (``custom`` passes the explicit
    proxy, ``system`` inherits environment proxies, ``direct`` forces off).
    Hardwiring ``trust_env=False`` here left them direct-connected even when
    the user configured a proxy — api.exa.ai times out from CN networks
    without one, the same failure shape as the ytimg cover bug. Tests that
    inject a transport keep full determinism: no global policy is merged on
    that path.
    """
    if transport is not None:
        return {"transport": transport}
    from openbiliclaw.network import outbound_httpx_kwargs

    return outbound_httpx_kwargs()


logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_BACKENDS: tuple[str, ...] = (
    "local_cache",
    "platform_sources",
    "bing_rss",
    "exa",
    "you",
    "serply",
)


def _ledger_int(value: object) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return 0


def _preview_key(item: ExaPreviewItem) -> str:
    url = str(item.url or "").strip().lower()
    if url:
        return f"url:{url}"
    return f"title:{str(item.title or '').strip().lower()}"


def _provider_alias(provider: object) -> str:
    alias = str(getattr(provider, "backend_alias", "") or "").strip()
    return alias or provider.__class__.__name__


def _provider_last_search_outcome(provider: object) -> PlatformSearchOutcome | None:
    outcome = getattr(provider, "last_search_outcome", None)
    return outcome if isinstance(outcome, PlatformSearchOutcome) else None


class InspirationSearchProvider(Protocol):
    """Search backend used by the keyword planner's inspiration stage."""

    def begin_stage(self) -> None: ...

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]: ...


class PlatformSearchBackend(Protocol):
    """One enabled platform source usable as inspiration-only grounding."""

    platform: str
    risk_controlled: bool

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]: ...


@dataclass(frozen=True)
class PlatformSearchOutcome:
    """Typed completeness evidence from one platform inspiration probe.

    Legacy backends still return a plain list and are represented with unknown
    completeness. Backends that can observe authoritative terminal evidence
    use this shape so accepted previews survive without turning partial or
    schema-rejected responses into affirmative empty results.
    """

    items: tuple[ExaPreviewItem, ...] = ()
    scope_complete: bool | None = None
    affirmative_empty: bool = False
    degraded: bool = False
    terminal_evidence: str = ""


PlatformSearchCallable = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


class LocalInspirationProvider:
    """Use existing local discovery assets as inspiration-only grounding."""

    backend_alias = "local_cache"

    def __init__(
        self,
        database: object,
        *,
        lookback_days: int = 30,
        min_results: int = 2,
        min_distinct_sources: int = 1,
    ) -> None:
        self._database = database
        self._lookback_days = max(1, int(lookback_days))
        self._min_results = max(1, int(min_results))
        self._min_distinct_sources = max(1, int(min_distinct_sources))
        self._ledger = self._new_ledger()

    @staticmethod
    def _new_ledger() -> dict[str, object]:
        return {"local_hits": 0, "local_misses": 0, "local_sources": {}}

    def begin_stage(self) -> None:
        self._ledger = self._new_ledger()

    def grounding_ledger(self) -> dict[str, object]:
        return {
            "local_hits": _ledger_int(self._ledger.get("local_hits", 0)),
            "local_misses": _ledger_int(self._ledger.get("local_misses", 0)),
            "local_sources": dict(cast("dict[str, int]", self._ledger.get("local_sources", {}))),
        }

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        getter = getattr(self._database, "search_local_inspiration_evidence", None)
        if not callable(getter):
            self._ledger["local_misses"] = _ledger_int(self._ledger.get("local_misses", 0)) + 1
            return []

        rows = getter(query, limit=max(1, int(limit)), lookback_days=self._lookback_days)
        previews: list[ExaPreviewItem] = []
        distinct_sources: set[str] = set()
        source_counts: dict[str, int] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = _clean_title(row.get("title"))
            url = _first_text(row.get("url"), row.get("content_url"))
            if not title or not url:
                continue
            source_table = _first_text(row.get("source_table")) or "local"
            source_platform = _first_text(row.get("source_platform"))
            topic_label = _first_text(row.get("topic_label"))
            distinct_sources.add("|".join([source_table, source_platform, topic_label]))
            source_counts[source_table] = source_counts.get(source_table, 0) + 1
            previews.append(
                ExaPreviewItem(
                    title=title,
                    url=url,
                    highlights=tuple(_clean_highlights(row.get("highlights"))),
                )
            )

        previews = _dedupe_previews(previews, limit=max(1, int(limit)))
        if len(previews) < self._min_results or len(distinct_sources) < self._min_distinct_sources:
            self._ledger["local_misses"] = _ledger_int(self._ledger.get("local_misses", 0)) + 1
            return []

        self._ledger["local_hits"] = _ledger_int(self._ledger.get("local_hits", 0)) + 1
        ledger_sources = cast("dict[str, int]", self._ledger.setdefault("local_sources", {}))
        for source, count in source_counts.items():
            ledger_sources[source] = int(ledger_sources.get(source, 0)) + count
        return previews


class McporterExaInspirationProvider:
    """Exa provider implemented through the local `mcporter` command."""

    backend_alias = "exa"

    def __init__(
        self,
        *,
        runner: Callable[[list[str], float], Awaitable[str]] | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._runner = runner or _run_command
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query:
            return []
        args = [
            "mcporter",
            "call",
            "exa.web_search_exa",
            f"query={clean_query}",
            f"numResults={count}",
            "--output",
            "raw",
        ]
        output = await self._runner(args, self._timeout_seconds)
        _raise_for_mcporter_error(output, backend="Exa")
        return parse_exa_search_payload(output)


class McporterYouInspirationProvider:
    """You.com MCP provider implemented through the local `mcporter` command."""

    backend_alias = "you"

    def __init__(
        self,
        *,
        runner: Callable[[list[str], float], Awaitable[str]] | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._runner = runner or _run_command
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query:
            return []
        args = [
            "mcporter",
            "call",
            "you.you-search",
            f"query={clean_query}",
            "--output",
            "raw",
        ]
        output = await self._runner(args, self._timeout_seconds)
        _raise_for_mcporter_error(output, backend="You.com")
        return parse_you_search_payload(output)[:count]


class ExaInspirationProvider:
    """Exa provider implemented through Exa's direct HTTP search API."""

    backend_alias = "exa"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 8.0,
        base_url: str = "https://api.exa.ai/search",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._base_url = str(base_url or "").strip() or "https://api.exa.ai/search"
        self._client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            **_overseas_routing_kwargs(transport),
            headers={"x-api-key": self._api_key, "content-type": "application/json"},
        )

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query or not self._api_key:
            return []
        payload = {
            "query": clean_query,
            "numResults": count,
            "contents": {"text": {"maxCharacters": 400}},
        }
        response = await self._client.post(self._base_url, json=payload)
        response.raise_for_status()
        return parse_exa_search_payload(response.json())[:count]


class YouInspirationProvider:
    """You.com provider implemented through You.com's direct HTTP search API."""

    backend_alias = "you"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 8.0,
        base_url: str = "https://api.ydc-index.io/search",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._base_url = str(base_url or "").strip() or "https://api.ydc-index.io/search"
        self._client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            **_overseas_routing_kwargs(transport),
            headers={"x-api-key": self._api_key},
        )

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query or not self._api_key:
            return []
        response = await self._client.get(
            self._base_url,
            params={"query": clean_query, "limit": count},
        )
        response.raise_for_status()
        return parse_you_search_payload(response.json())[:count]


class SerplyInspirationProvider:
    """Serply provider implemented through Serply's direct HTTP search API."""

    backend_alias = "serply"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 8.0,
        base_url: str = "https://api.serply.io/v1/search",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._base_url = str(base_url or "").strip() or "https://api.serply.io/v1/search"
        self._client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            **_overseas_routing_kwargs(transport),
            headers={
                "X-Api-Key": self._api_key,
                "Accept": "application/json",
                # Serply sits behind Cloudflare, which rejects the default
                # httpx User-Agent, so send an explicit one.
                "User-Agent": "OpenBiliClaw",
            },
        )

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query or not self._api_key:
            return []
        response = await self._client.get(
            self._base_url,
            params={"q": clean_query, "num": count},
        )
        response.raise_for_status()
        return parse_serply_search_payload(response.json())[:count]


def _rss_element_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


class BingRssInspirationProvider:
    """No-key web-search fallback backed by Bing's RSS endpoint."""

    backend_alias = "bing_rss"

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        base_url: str = "https://www.bing.com/search",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._base_url = str(base_url or "").strip() or "https://www.bing.com/search"
        self._client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            # Bing is reachable directly from CN networks (cn.bing.com edge);
            # routing it through an overseas ladder would only add latency.
            # Deliberately NOT wired to openbiliclaw.network — same split as
            # the image cache's CN-CDN branch.
            trust_env=False,
            transport=transport,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )

    def begin_stage(self) -> None:
        return None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        clean_query = str(query or "").strip()
        count = max(1, min(10, int(limit)))
        if not clean_query:
            return []
        response = await self._client.get(
            self._base_url,
            params={"q": clean_query, "format": "rss", "count": count},
        )
        response.raise_for_status()
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise RuntimeError("bing_rss returned invalid XML") from exc
        previews: list[ExaPreviewItem] = []
        for item in root.findall(".//item"):
            title = _clean_title(_rss_element_text(item.find("title")))
            url = str(_rss_element_text(item.find("link")) or "").strip()
            if not title or not url:
                continue
            description = _clean_title(_rss_element_text(item.find("description")))
            previews.append(
                ExaPreviewItem(
                    title=title,
                    url=url,
                    highlights=(description,) if description else (),
                )
            )
            if len(previews) >= count:
                break
        return previews


class FallbackInspirationSearchProvider:
    """Try inspiration search providers in order until one yields results."""

    def __init__(
        self,
        providers: list[InspirationSearchProvider],
        *,
        fallback_on_empty: bool = True,
        error_cooldown_seconds: float = 60.0,
        pages_per_probe: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._providers = list(providers)
        self._fallback_on_empty = bool(fallback_on_empty)
        self._error_cooldown_seconds = max(0.0, float(error_cooldown_seconds))
        self._pages_per_probe = max(1, min(5, int(pages_per_probe)))
        self._clock = clock or time.monotonic
        self._disabled_until: dict[int, float] = {}
        self._ledger = self._new_ledger()
        self.last_search_provider: str | None = None

    @staticmethod
    def _new_ledger() -> dict[str, object]:
        return {
            "provider_successes": {},
            "provider_failures": {},
            "provider_empty": {},
            "provider_degraded": {},
            "provider_augmentations": 0,
        }

    def begin_stage(self) -> None:
        self._ledger = self._new_ledger()
        self.last_search_provider = None
        for provider in self._providers:
            begin = getattr(provider, "begin_stage", None)
            if callable(begin):
                begin()

    def grounding_ledger(self) -> dict[str, object]:
        combined_platforms: dict[str, int] = {}
        combined_platform_complete: dict[str, int] = {}
        combined_platform_affirmative_empty: dict[str, int] = {}
        combined_platform_partial: dict[str, int] = {}
        combined_platform_degraded: dict[str, int] = {}
        combined_terminal_evidence: dict[str, dict[str, int]] = {}
        skipped_cooldown = 0
        skipped_budget = 0
        timeouts = 0
        local_hits = 0
        local_misses = 0
        local_sources: dict[str, int] = {}
        for provider in self._providers:
            getter = getattr(provider, "grounding_ledger", None)
            if not callable(getter):
                continue
            try:
                ledger = getter()
            except Exception:
                logger.debug("inspiration provider ledger lookup failed", exc_info=True)
                continue
            if not isinstance(ledger, dict):
                continue
            for platform, count in dict(ledger.get("platforms", {})).items():
                combined_platforms[str(platform)] = combined_platforms.get(str(platform), 0) + int(
                    count or 0
                )
            for field, destination in (
                ("platform_complete", combined_platform_complete),
                ("platform_affirmative_empty", combined_platform_affirmative_empty),
                ("platform_partial", combined_platform_partial),
                ("platform_degraded", combined_platform_degraded),
            ):
                raw_counts = ledger.get(field, {})
                if not isinstance(raw_counts, dict):
                    continue
                for platform, count in raw_counts.items():
                    key = str(platform)
                    destination[key] = destination.get(key, 0) + _ledger_int(count or 0)
            raw_terminal_evidence = ledger.get("platform_terminal_evidence", {})
            if isinstance(raw_terminal_evidence, dict):
                for platform, raw_reasons in raw_terminal_evidence.items():
                    if not isinstance(raw_reasons, dict):
                        continue
                    reasons = combined_terminal_evidence.setdefault(str(platform), {})
                    for reason, count in raw_reasons.items():
                        key = str(reason)
                        reasons[key] = reasons.get(key, 0) + _ledger_int(count or 0)
            skipped_cooldown += _ledger_int(ledger.get("skipped_cooldown", 0) or 0)
            skipped_budget += _ledger_int(ledger.get("skipped_budget", 0) or 0)
            timeouts += _ledger_int(ledger.get("timeouts", 0) or 0)
            local_hits += _ledger_int(ledger.get("local_hits", 0) or 0)
            local_misses += _ledger_int(ledger.get("local_misses", 0) or 0)
            raw_local_sources = ledger.get("local_sources", {})
            if isinstance(raw_local_sources, dict):
                for source, count in raw_local_sources.items():
                    local_sources[str(source)] = local_sources.get(str(source), 0) + _ledger_int(
                        count or 0
                    )
        return {
            "platforms": combined_platforms,
            "platform_complete": combined_platform_complete,
            "platform_affirmative_empty": combined_platform_affirmative_empty,
            "platform_partial": combined_platform_partial,
            "platform_degraded": combined_platform_degraded,
            "platform_terminal_evidence": combined_terminal_evidence,
            "skipped_cooldown": skipped_cooldown,
            "skipped_budget": skipped_budget,
            "timeouts": timeouts,
            "local_hits": local_hits,
            "local_misses": local_misses,
            "local_sources": local_sources,
            "provider_failures": dict(
                cast("dict[str, int]", self._ledger.get("provider_failures", {}))
            ),
            "provider_successes": dict(
                cast("dict[str, int]", self._ledger.get("provider_successes", {}))
            ),
            "provider_empty": dict(cast("dict[str, int]", self._ledger.get("provider_empty", {}))),
            "provider_degraded": dict(
                cast("dict[str, int]", self._ledger.get("provider_degraded", {}))
            ),
            "provider_augmentations": _ledger_int(
                self._ledger.get("provider_augmentations", 0) or 0
            ),
        }

    def bilibili_search_cooldown_remaining(self) -> float:
        remaining = 0.0
        for provider in self._providers:
            getter = getattr(provider, "bilibili_search_cooldown_remaining", None)
            if not callable(getter):
                continue
            try:
                remaining = max(remaining, float(getter()))
            except Exception:
                logger.debug("inspiration provider cooldown lookup failed", exc_info=True)
        return remaining

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        self.last_search_provider = None
        if not self._providers:
            return []
        result_limit = max(1, int(limit)) * self._pages_per_probe
        last_error: Exception | None = None
        collected: list[ExaPreviewItem] = []
        seen: set[str] = set()
        for provider in self._providers:
            now = self._clock()
            if self._disabled_until.get(id(provider), 0.0) > now:
                continue
            try:
                results = await provider.search(query, limit=result_limit)
            except Exception as exc:
                last_error = exc
                self._increment_provider_counter("provider_failures", provider)
                if self._error_cooldown_seconds > 0:
                    self._disabled_until[id(provider)] = now + self._error_cooldown_seconds
                logger.debug(
                    "inspiration search provider %s failed",
                    provider.__class__.__name__,
                    exc_info=True,
                )
                continue
            provider_outcome = _provider_last_search_outcome(provider)
            if results:
                self._increment_provider_counter("provider_successes", provider)
                if provider_outcome is not None and provider_outcome.degraded:
                    self._increment_provider_counter("provider_degraded", provider)
                self.last_search_provider = _provider_alias(provider)
                for item in results:
                    key = _preview_key(item)
                    if key in seen:
                        continue
                    seen.add(key)
                    collected.append(item)
                if not self._should_augment_results(provider, len(collected), result_limit):
                    return collected[:result_limit]
                self._ledger["provider_augmentations"] = (
                    _ledger_int(self._ledger.get("provider_augmentations", 0) or 0) + 1
                )
                continue
            if provider_outcome is not None and provider_outcome.degraded:
                self._increment_provider_counter("provider_degraded", provider)
                if not self._fallback_on_empty:
                    return []
                continue
            self._increment_provider_counter("provider_empty", provider)
            if not self._fallback_on_empty:
                return []
        if collected:
            return collected[:result_limit]
        if last_error is not None:
            raise last_error
        return []

    def _should_augment_results(
        self,
        provider: InspirationSearchProvider,
        result_count: int,
        limit: int,
    ) -> bool:
        if not self._fallback_on_empty:
            return False
        if _provider_alias(provider) == "local_cache":
            return False
        getter = getattr(provider, "grounding_ledger", None)
        if not callable(getter):
            return False
        try:
            ledger = getter()
        except Exception:
            logger.debug("inspiration provider ledger lookup failed", exc_info=True)
            return False
        if not isinstance(ledger, dict):
            return False
        raw_platforms = ledger.get("platforms", {})
        if not isinstance(raw_platforms, dict):
            return False
        distinct_platforms = sum(1 for count in raw_platforms.values() if _ledger_int(count) > 0)
        if distinct_platforms >= 2:
            return False
        has_loss_signal = any(
            _ledger_int(ledger.get(key, 0) or 0) > 0
            for key in ("timeouts", "skipped_cooldown", "skipped_budget")
        )
        raw_platform_degraded = ledger.get("platform_degraded", {})
        if isinstance(raw_platform_degraded, dict):
            has_loss_signal = has_loss_signal or any(
                _ledger_int(count or 0) > 0 for count in raw_platform_degraded.values()
            )
        return result_count < max(1, int(limit)) or has_loss_signal

    def _increment_provider_counter(
        self,
        field: str,
        provider: InspirationSearchProvider,
    ) -> None:
        counters = cast("dict[str, int]", self._ledger.setdefault(field, {}))
        name = provider.__class__.__name__
        counters[name] = int(counters.get(name, 0)) + 1


class PlatformSourceInspirationProvider:
    """Search a rotating subset of enabled platform sources for grounding only."""

    backend_alias = "platform_sources"

    def __init__(
        self,
        backends: list[PlatformSearchBackend],
        *,
        platforms_per_query: int = 2,
        riskcontrolled_probe_budget: int = 4,
        pages_per_probe: int = 1,
        backend_timeout_seconds: float = 8.0,
    ) -> None:
        self._backends = list(backends)
        self._platforms_per_query = max(1, min(4, int(platforms_per_query)))
        self._riskcontrolled_probe_budget = max(0, min(32, int(riskcontrolled_probe_budget)))
        self._pages_per_probe = max(1, min(5, int(pages_per_probe)))
        self._backend_timeout_seconds = max(0.001, float(backend_timeout_seconds))
        self._cursor = 0
        self._riskcontrolled_used = 0
        self._ledger = self._new_ledger()
        self.last_search_outcome: PlatformSearchOutcome | None = None

    @staticmethod
    def _new_ledger() -> dict[str, object]:
        return {
            "platforms": {},
            "platform_complete": {},
            "platform_affirmative_empty": {},
            "platform_partial": {},
            "platform_degraded": {},
            "platform_terminal_evidence": {},
            "skipped_cooldown": 0,
            "skipped_budget": 0,
            "timeouts": 0,
        }

    def begin_stage(self) -> None:
        self._riskcontrolled_used = 0
        self._ledger = self._new_ledger()
        self.last_search_outcome = None

    def grounding_ledger(self) -> dict[str, object]:
        platforms = dict(cast("dict[str, int]", self._ledger.get("platforms", {})))
        return {
            "platforms": platforms,
            "platform_complete": dict(
                cast("dict[str, int]", self._ledger.get("platform_complete", {}))
            ),
            "platform_affirmative_empty": dict(
                cast(
                    "dict[str, int]",
                    self._ledger.get("platform_affirmative_empty", {}),
                )
            ),
            "platform_partial": dict(
                cast("dict[str, int]", self._ledger.get("platform_partial", {}))
            ),
            "platform_degraded": dict(
                cast("dict[str, int]", self._ledger.get("platform_degraded", {}))
            ),
            "platform_terminal_evidence": {
                str(platform): dict(evidence)
                for platform, evidence in cast(
                    "dict[str, dict[str, int]]",
                    self._ledger.get("platform_terminal_evidence", {}),
                ).items()
            },
            "skipped_cooldown": _ledger_int(self._ledger.get("skipped_cooldown", 0) or 0),
            "skipped_budget": _ledger_int(self._ledger.get("skipped_budget", 0) or 0),
            "timeouts": _ledger_int(self._ledger.get("timeouts", 0) or 0),
        }

    def bilibili_search_cooldown_remaining(self) -> float:
        remaining = 0.0
        for backend in self._backends:
            if str(getattr(backend, "platform", "")).strip().lower() != "bilibili":
                continue
            remaining = max(remaining, _backend_cooldown_remaining(backend))
        return remaining

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        self.last_search_outcome = None
        clean_query = str(query or "").strip()
        if not clean_query or not self._backends:
            return []
        selected = self._select_backends()
        if not selected:
            return []
        count = max(1, int(limit))
        per_backend_total = max(1, (count + len(selected) - 1) // len(selected))
        previews: list[ExaPreviewItem] = []
        outcomes: list[PlatformSearchOutcome] = []
        terminal_evidence: list[str] = []
        backend_failures = 0
        for backend in selected:
            try:
                platform = str(getattr(backend, "platform", "unknown") or "unknown")
                outcome = await asyncio.wait_for(
                    _call_platform_backend_search(
                        backend,
                        clean_query,
                        limit=per_backend_total,
                        pages=self._pages_per_probe,
                    ),
                    timeout=self._backend_timeout_seconds,
                )
                outcomes.append(outcome)
                previews.extend(outcome.items)
                platforms = cast("dict[str, int]", self._ledger["platforms"])
                platforms[platform] = int(platforms.get(platform, 0)) + 1
                self._record_platform_outcome(platform, outcome)
                if outcome.terminal_evidence:
                    terminal_evidence.append(outcome.terminal_evidence)
            except TimeoutError:
                backend_failures += 1
                terminal_evidence.append("timeout")
                self._ledger["timeouts"] = _ledger_int(self._ledger.get("timeouts", 0) or 0) + 1
                logger.debug(
                    "platform inspiration source %s timed out",
                    getattr(backend, "platform", backend.__class__.__name__),
                    exc_info=True,
                )
            except Exception:
                backend_failures += 1
                terminal_evidence.append("backend_error")
                logger.debug(
                    "platform inspiration source %s failed",
                    getattr(backend, "platform", backend.__class__.__name__),
                    exc_info=True,
                )
        combined = previews[:count]
        degraded = backend_failures > 0 or any(outcome.degraded for outcome in outcomes)
        scope_values = [outcome.scope_complete for outcome in outcomes]
        scope_complete: bool | None
        if backend_failures:
            scope_complete = False
        elif scope_values and all(value is True for value in scope_values):
            scope_complete = True
        elif any(value is False for value in scope_values):
            scope_complete = False
        else:
            scope_complete = None
        affirmative_empty = bool(
            outcomes
            and not combined
            and not degraded
            and all(outcome.affirmative_empty for outcome in outcomes)
        )
        self.last_search_outcome = PlatformSearchOutcome(
            items=tuple(combined),
            scope_complete=scope_complete,
            affirmative_empty=affirmative_empty,
            degraded=degraded,
            terminal_evidence="+".join(dict.fromkeys(terminal_evidence)),
        )
        return combined

    def _record_platform_outcome(
        self,
        platform: str,
        outcome: PlatformSearchOutcome,
    ) -> None:
        if outcome.scope_complete is True:
            self._increment_platform_counter("platform_complete", platform)
        if outcome.affirmative_empty:
            self._increment_platform_counter("platform_affirmative_empty", platform)
        if outcome.degraded:
            self._increment_platform_counter("platform_degraded", platform)
            if outcome.items:
                self._increment_platform_counter("platform_partial", platform)
        evidence = str(outcome.terminal_evidence or "").strip()
        if evidence:
            by_platform = cast(
                "dict[str, dict[str, int]]",
                self._ledger["platform_terminal_evidence"],
            )
            reasons = by_platform.setdefault(platform, {})
            reasons[evidence] = int(reasons.get(evidence, 0)) + 1

    def _increment_platform_counter(self, field: str, platform: str) -> None:
        counters = cast("dict[str, int]", self._ledger[field])
        counters[platform] = int(counters.get(platform, 0)) + 1

    def _select_backends(self) -> list[PlatformSearchBackend]:
        if not self._backends:
            return []
        count = min(self._platforms_per_query, len(self._backends))
        selected: list[PlatformSearchBackend] = []
        inspected = 0
        while inspected < len(self._backends) and len(selected) < count:
            backend = self._backends[(self._cursor + inspected) % len(self._backends)]
            inspected += 1
            if _backend_cooldown_remaining(backend) > 0:
                self._ledger["skipped_cooldown"] = (
                    _ledger_int(self._ledger.get("skipped_cooldown", 0) or 0) + 1
                )
                continue
            if bool(getattr(backend, "risk_controlled", False)):
                if self._riskcontrolled_used >= self._riskcontrolled_probe_budget:
                    self._ledger["skipped_budget"] = (
                        _ledger_int(self._ledger.get("skipped_budget", 0) or 0) + 1
                    )
                    continue
                self._riskcontrolled_used += 1
            selected.append(backend)
        self._cursor = (self._cursor + max(inspected, count)) % len(self._backends)
        return selected


async def _call_platform_backend_search(
    backend: PlatformSearchBackend,
    query: str,
    *,
    limit: int,
    pages: int,
) -> PlatformSearchOutcome:
    search = getattr(backend, "search_outcome", None)
    if not callable(search):
        search = backend.search
    try:
        result = await cast("Any", search)(query, limit=limit, pages=pages)
    except TypeError as exc:
        if "pages" not in str(exc):
            raise
        result = await cast("Any", search)(query, limit=limit)
    if isinstance(result, PlatformSearchOutcome):
        return result
    return PlatformSearchOutcome(items=tuple(result or ()))


def _dedupe_previews(items: list[ExaPreviewItem], *, limit: int) -> list[ExaPreviewItem]:
    seen: set[str] = set()
    result: list[ExaPreviewItem] = []
    for item in items:
        key = _preview_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max(1, int(limit)):
            break
    return result


class BilibiliPlatformSearchBackend:
    """Use Bilibili's existing API client as inspiration-only grounding."""

    platform = "bilibili"
    risk_controlled = True

    def __init__(self, client: object) -> None:
        self._client = client

    def cooldown_remaining(self) -> float:
        return _backend_cooldown_remaining(self._client)

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search", None)
        if not callable(search):
            return []
        previews: list[ExaPreviewItem] = []
        total = max(1, int(limit))
        page_count = max(1, min(5, int(pages)))
        page_size = max(1, (total + page_count - 1) // page_count)
        for page in range(1, page_count + 1):
            rows = await search(
                query,
                page=page,
                page_size=page_size,
            )
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                title = _clean_title(row.get("title"))
                url = _first_text(row.get("arcurl"), row.get("url"), row.get("uri"))
                bvid = _first_text(row.get("bvid"))
                if not url and bvid:
                    url = f"https://www.bilibili.com/video/{bvid}"
                if not title or not url:
                    continue
                highlights = _clean_highlights(
                    [
                        _clean_title(row.get("description")),
                        _clean_title(row.get("desc")),
                        _first_text(row.get("author"), row.get("up_name"), row.get("typename")),
                    ]
                )
                previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
            if len(previews) >= total:
                break
        return _dedupe_previews(previews, limit=total)


class YoutubePlatformSearchBackend:
    """Use the existing YouTube scraper client as inspiration-only grounding."""

    platform = "youtube"
    risk_controlled = False

    def __init__(self, client: object) -> None:
        self._client = client

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search_videos", None)
        if not callable(search):
            return []
        rows = await search(query, limit=max(1, int(limit)))
        previews: list[ExaPreviewItem] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = _youtube_text(row.get("title") or row.get("fulltitle"))
            video_id = _first_text(row.get("videoId"), row.get("id"))
            url = _first_text(row.get("url"), row.get("webpage_url"))
            if not url and video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            if not title or not url:
                continue
            channel = _youtube_text(
                row.get("ownerText")
                or row.get("shortBylineText")
                or row.get("channel")
                or row.get("uploader")
            )
            description = _youtube_text(row.get("descriptionSnippet") or row.get("description"))
            highlights = _clean_highlights([description, channel])
            previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
        return _dedupe_previews(previews, limit=max(1, int(limit)))


class XPlatformSearchBackend:
    """Use the existing X client as inspiration-only grounding."""

    platform = "twitter"
    risk_controlled = True

    def __init__(self, client: object) -> None:
        self._client = client

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search", None)
        if not callable(search):
            return []
        rows = await search(
            query,
            limit=max(1, int(limit)),
            product="Top",
        )
        previews = [_x_preview(row) for row in rows or [] if isinstance(row, dict)]
        return _dedupe_previews([item for item in previews if item is not None], limit=limit)


class RedditPlatformSearchBackend:
    """Use the existing Reddit command backend as inspiration-only grounding."""

    platform = "reddit"
    risk_controlled = False

    def __init__(
        self,
        *,
        backend: str = "opencli",
        runner: object | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._backend = str(backend or "opencli")
        self._runner = runner
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        from openbiliclaw.sources.reddit_tasks import (
            CommandRunner,
            build_reddit_command,
            run_reddit_command,
        )

        count = max(1, int(limit))
        args = build_reddit_command(self._backend, mode="search", query=query, limit=count)
        rows = await asyncio.to_thread(
            run_reddit_command,
            args,
            runner=cast("CommandRunner | None", self._runner),
            timeout=max(self._timeout_seconds, float(count) * 3.0),
        )
        previews: list[ExaPreviewItem] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = _clean_title(row.get("title") or row.get("body"))
            url = _first_text(row.get("url"), row.get("permalink"))
            if url.startswith("/"):
                url = f"https://www.reddit.com{url}"
            if not title or not url:
                continue
            subreddit = _first_text(row.get("subreddit"))
            if subreddit and not subreddit.startswith("r/"):
                subreddit = f"r/{subreddit}"
            highlights = _clean_highlights(
                [
                    _clean_title(row.get("selftext")),
                    _clean_title(row.get("body")),
                    subreddit,
                    _first_text(row.get("author")),
                ]
            )
            previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
        return _dedupe_previews(previews, limit=count)


class DouyinPlatformSearchBackend:
    """Use a Douyin search client as inspiration-only grounding."""

    platform = "douyin"
    risk_controlled = True

    def __init__(self, client: object) -> None:
        self._client = client

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search_aweme", None)
        if not callable(search):
            return []
        rows = await search(query, limit=max(1, int(limit)))
        previews = [_douyin_preview(row) for row in rows or [] if isinstance(row, dict)]
        return _dedupe_previews([item for item in previews if item is not None], limit=limit)


class XhsPlatformSearchBackend:
    """Use an injected xiaohongshu search bridge as inspiration-only grounding."""

    platform = "xiaohongshu"
    risk_controlled = False

    def __init__(self, search: PlatformSearchCallable) -> None:
        self._search = search

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        rows = await self._search(query, max(1, int(limit)))
        previews = [_xhs_preview(row) for row in rows or [] if isinstance(row, dict)]
        return _dedupe_previews([item for item in previews if item is not None], limit=limit)


class ZhihuPlatformSearchBackend:
    """Use an injected Zhihu search bridge as inspiration-only grounding."""

    platform = "zhihu"
    risk_controlled = False

    def __init__(self, search: PlatformSearchCallable) -> None:
        self._search = search

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        rows = await self._search(query, max(1, int(limit)))
        previews = [_zhihu_preview(row) for row in rows or [] if isinstance(row, dict)]
        return _dedupe_previews([item for item in previews if item is not None], limit=limit)


class BangumiPlatformSearchBackend:
    """Use the official Bangumi API as inspiration-only grounding."""

    platform = "bangumi"
    risk_controlled = False

    def __init__(self, client: object, *, subject_types: tuple[str, ...]) -> None:
        self._client = client
        self._subject_types = subject_types

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search_subjects", None)
        if not callable(search):
            return []
        page = await search(
            query,
            subject_types=self._subject_types,
            limit=max(1, int(limit)),
            offset=0,
            sort="match",
        )
        rows = getattr(page, "data", [])
        previews: list[ExaPreviewItem] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            subject_id = _first_text(row.get("id"))
            title = _first_text(row.get("name_cn"), row.get("name"))
            if not subject_id or not title:
                continue
            summary = _first_text(row.get("summary"), row.get("short_summary"))
            raw_tags_value = row.get("tags")
            raw_tags: list[Any] = raw_tags_value if isinstance(raw_tags_value, list) else []
            tags = ", ".join(
                _first_text(tag.get("name"))
                for tag in raw_tags[:5]
                if isinstance(tag, dict) and _first_text(tag.get("name"))
            )
            previews.append(
                ExaPreviewItem(
                    title=_clean_title(title),
                    url=f"https://bgm.tv/subject/{subject_id}",
                    highlights=tuple(_clean_highlights([summary, tags])),
                )
            )
        return _dedupe_previews(previews, limit=max(1, int(limit)))


class V2EXPlatformSearchBackend:
    """Use V2EX's bounded public search fallback as inspiration-only grounding."""

    platform = "v2ex"
    risk_controlled = False

    def __init__(self, client: object) -> None:
        self._client = client

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        del pages
        search = getattr(self._client, "search_topics", None)
        if not callable(search):
            return []
        page = await search(query, limit=max(1, int(limit)))
        rows = getattr(page, "data", [])
        previews: list[ExaPreviewItem] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            topic_id = _first_text(row.get("id"), row.get("content_id"))
            title = _clean_title(row.get("title"))
            url = _first_text(row.get("url"), row.get("content_url"))
            if not url and topic_id:
                url = f"https://www.v2ex.com/t/{topic_id}"
            if not title or not url:
                continue
            node_value = row.get("node")
            node: Mapping[str, Any] = node_value if isinstance(node_value, Mapping) else {}
            member_value = row.get("member")
            member: Mapping[str, Any] = member_value if isinstance(member_value, Mapping) else {}
            highlights = _clean_highlights(
                [
                    _clean_title(row.get("content") or row.get("description")),
                    _first_text(node.get("title"), node.get("name")),
                    _first_text(member.get("username"), row.get("author_name")),
                ]
            )
            previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
        return _dedupe_previews(previews, limit=max(1, int(limit)))


class WeiboPlatformSearchBackend:
    """Use anonymous Weibo search for inspiration-only grounding."""

    platform = "weibo"
    risk_controlled = True

    def __init__(self, client: object) -> None:
        self._client = client

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        search = getattr(self._client, "search_posts", None)
        if not callable(search):
            return []
        page = await search(query, page=1, limit=max(1, int(limit)))
        rows = getattr(page, "rows", getattr(page, "data", page))
        if not isinstance(rows, list):
            return []
        from openbiliclaw.sources.weibo import weibo_post_to_content

        previews: list[ExaPreviewItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = weibo_post_to_content(row, strategy="weibo-search")
            if content is None or not content.content_url:
                continue
            previews.append(
                ExaPreviewItem(
                    title=_clean_title(content.title),
                    url=content.content_url,
                    highlights=tuple(_clean_highlights([content.body_text, content.author_name])),
                )
            )
        return _dedupe_previews(previews, limit=max(1, int(limit)))


class GitHubPlatformSearchBackend:
    """Use GitHub's public repository search as inspiration-only grounding."""

    platform = "github"
    # Anonymous repository search is deliberately tight (10 requests/minute),
    # so share the planner's bounded probe budget with other fragile sources.
    risk_controlled = True

    def __init__(self, client: object, *, database: object | None = None) -> None:
        self._client = client
        self._database = database

    def cooldown_remaining(self) -> float:
        if self._database is None:
            return 0.0
        from openbiliclaw.runtime.github_producer import github_cooldown_remaining

        return github_cooldown_remaining(self._database)

    async def search(self, query: str, *, limit: int, pages: int = 1) -> list[ExaPreviewItem]:
        """Return accepted previews while retaining compatibility with list callers."""

        outcome = await self.search_outcome(query, limit=limit, pages=pages)
        return list(outcome.items)

    async def search_outcome(
        self,
        query: str,
        *,
        limit: int,
        pages: int = 1,
    ) -> PlatformSearchOutcome:
        """Return previews plus authoritative GitHub completeness evidence."""

        search = getattr(self._client, "search_repositories", None)
        if not callable(search):
            return PlatformSearchOutcome(
                degraded=True,
                scope_complete=False,
                terminal_evidence="backend_unavailable",
            )
        total = max(1, int(limit))
        page_count = max(1, min(5, int(pages)))
        page_size = min(100, max(1, (total + page_count - 1) // page_count))
        public_query = _github_public_query(query)
        if not public_query:
            return PlatformSearchOutcome(
                degraded=True,
                scope_complete=False,
                terminal_evidence="invalid_query",
            )
        previews: list[ExaPreviewItem] = []
        rows_seen = 0
        accepted_seen = 0
        rejected_seen = 0
        upstream_scope_complete = False
        stopped_for_limit = False
        exhausted_probe_pages = False
        degradation_reasons: list[str] = []
        from openbiliclaw.sources.github import github_repository_to_content

        for page_number in range(1, page_count + 1):
            try:
                page = await search(
                    public_query,
                    page=page_number,
                    per_page=page_size,
                )
            except Exception as exc:
                from openbiliclaw.sources.github_client import GitHubAPIError

                if (
                    isinstance(exc, GitHubAPIError)
                    and exc.code == "rate_limited"
                    and self._database is not None
                ):
                    from openbiliclaw.runtime.github_producer import persist_github_cooldown

                    persist_github_cooldown(
                        self._database,
                        exc.retry_after_seconds or 300,
                    )
                raise
            rows = getattr(page, "items", [])
            if not isinstance(rows, list):
                degradation_reasons.append("invalid_items_container")
                break
            page_rejections = 0
            for row in rows:
                rows_seen += 1
                content = github_repository_to_content(
                    row,
                    strategy="github-inspiration",
                )
                if content is None:
                    rejected_seen += 1
                    page_rejections += 1
                    continue
                accepted_seen += 1
                if len(previews) >= total:
                    continue
                tags = ", ".join(content.tags[:6])
                stars_label = f"stars: {content.favorite_count}"
                previews.append(
                    ExaPreviewItem(
                        title=_clean_title(content.title),
                        url=content.content_url,
                        highlights=tuple(
                            _clean_highlights(
                                [
                                    content.body_text,
                                    tags,
                                    stars_label,
                                    content.author_name,
                                ]
                            )
                        ),
                    )
                )
            incomplete_results = getattr(page, "incomplete_results", False) is True
            search_capped = getattr(page, "search_capped", False) is True
            if incomplete_results:
                degradation_reasons.append("incomplete_results")
            if search_capped:
                degradation_reasons.append("search_capped")
            if page_rejections:
                degradation_reasons.append("rejected_rows")

            next_page = getattr(page, "next_page", None)
            explicit_scope_complete = getattr(page, "scope_complete", None)
            page_scope_complete = (
                explicit_scope_complete
                if isinstance(explicit_scope_complete, bool)
                else not incomplete_results and not search_capped and next_page is None
            )
            if len(previews) >= total:
                stopped_for_limit = accepted_seen > len(previews) or next_page is not None
                upstream_scope_complete = page_scope_complete
                break
            if next_page is None:
                upstream_scope_complete = page_scope_complete
                break
        else:
            exhausted_probe_pages = True

        deduped = _dedupe_previews(previews, limit=total)
        if len(deduped) < len(previews):
            stopped_for_limit = True
        scope_complete = bool(
            upstream_scope_complete
            and not degradation_reasons
            and not stopped_for_limit
            and not exhausted_probe_pages
        )
        degraded = bool(rejected_seen or degradation_reasons or not scope_complete)
        affirmative_empty = bool(scope_complete and rows_seen == 0 and accepted_seen == 0)
        if degradation_reasons:
            terminal_evidence = "+".join(dict.fromkeys(degradation_reasons))
        elif affirmative_empty:
            terminal_evidence = "affirmative_empty"
        elif scope_complete:
            terminal_evidence = "scope_complete"
        elif stopped_for_limit:
            terminal_evidence = "limit_reached"
        elif exhausted_probe_pages:
            terminal_evidence = "probe_page_cap"
        else:
            terminal_evidence = "scope_incomplete"
        return PlatformSearchOutcome(
            items=tuple(deduped),
            scope_complete=scope_complete,
            affirmative_empty=affirmative_empty,
            degraded=degraded,
            terminal_evidence=terminal_evidence,
        )


def _github_public_query(value: object) -> str:
    """Return a bounded repository query whose visibility scope is server-owned."""

    from openbiliclaw.sources.github import github_public_repository_query

    return github_public_repository_query(value)


def _douyin_preview(row: dict[str, Any]) -> ExaPreviewItem | None:
    aweme_id = _first_text(row.get("aweme_id"), row.get("id"), row.get("item_id"))
    title = _first_text(
        row.get("desc"),
        _nested_text(row, ("share_info", "share_title")),
        _nested_text(row, ("share_info", "share_desc")),
        row.get("title"),
    )
    url = _first_text(row.get("url"), row.get("content_url"), row.get("share_url"))
    if not url and aweme_id:
        url = f"https://www.douyin.com/video/{aweme_id}"
    author = _first_text(
        row.get("author_name"),
        _nested_text(row, ("author", "nickname")),
        _nested_text(row, ("author", "unique_id")),
    )
    if not title or not url:
        return None
    return ExaPreviewItem(
        title=_clean_title(title),
        url=url,
        highlights=tuple(_clean_highlights([author, _statistics_highlight(row.get("statistics"))])),
    )


def _xhs_preview(row: dict[str, Any]) -> ExaPreviewItem | None:
    note_id = _first_text(row.get("note_id"), row.get("id"), row.get("content_id"))
    title = _first_text(
        row.get("title"),
        row.get("note_title"),
        row.get("display_title"),
        row.get("desc"),
        row.get("description"),
    )
    url = _first_text(row.get("url"), row.get("note_url"), row.get("content_url"), row.get("link"))
    if not url and note_id:
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
    author = _first_text(
        row.get("author"),
        row.get("author_name"),
        row.get("nickname"),
        _nested_text(row, ("user", "nickname")),
    )
    desc = _first_text(row.get("desc"), row.get("description"), row.get("summary"))
    if not title or not url:
        return None
    return ExaPreviewItem(
        title=_clean_title(title),
        url=url,
        highlights=tuple(_clean_highlights([desc, author])),
    )


def _zhihu_preview(row: dict[str, Any]) -> ExaPreviewItem | None:
    raw_id = _first_text(row.get("content_id"), row.get("id"), row.get("answer_id"))
    title = _first_text(row.get("title"), row.get("question_title"), row.get("name"))
    url = _first_text(row.get("url"), row.get("content_url"), row.get("link"))
    if not url and raw_id:
        url = f"https://www.zhihu.com/question/{raw_id}"
    summary = _first_text(row.get("summary"), row.get("excerpt"), row.get("description"))
    author = _first_text(row.get("author"), row.get("author_name"))
    if not title or not url:
        return None
    return ExaPreviewItem(
        title=_clean_title(title),
        url=url,
        highlights=tuple(_clean_highlights([summary, author])),
    )


def _x_preview(row: dict[str, Any]) -> ExaPreviewItem | None:
    tweet_id = _first_text(row.get("id"), row.get("tweet_id"), row.get("rest_id"))
    raw_text = _first_text(row.get("text"), row.get("full_text"), row.get("body"))
    article_title = _first_text(row.get("articleTitle"), row.get("article_title"))
    article_text = _first_text(row.get("articleText"), row.get("article_text"))
    title = _first_text(article_title, _first_nonempty_line(raw_text), article_text)
    author = row.get("author")
    author_dict = author if isinstance(author, dict) else {}
    screen_name = _first_text(
        author_dict.get("screenName"),
        author_dict.get("screen_name"),
        row.get("screenName"),
        row.get("screen_name"),
        row.get("author_screen_name"),
    )
    author_name = _first_text(
        author_dict.get("name"),
        row.get("author_name"),
        row.get("name"),
    )
    url = _first_text(row.get("url"), row.get("tweet_url"), row.get("content_url"))
    if not url and tweet_id:
        if screen_name:
            url = f"https://x.com/{screen_name}/status/{tweet_id}"
        else:
            url = f"https://x.com/i/web/status/{tweet_id}"
    if not title or not url:
        return None
    return ExaPreviewItem(
        title=_clean_title(title),
        url=url,
        highlights=tuple(
            _clean_highlights(
                [
                    _x_author_highlight(screen_name=screen_name, author_name=author_name),
                    article_text if article_text != raw_text else "",
                    _x_metrics_highlight(row.get("metrics")),
                ]
            )
        ),
    )


def _first_nonempty_line(value: object) -> str:
    for line in str(value or "").splitlines():
        clean = _clean_title(line)
        if clean:
            return clean
    return _clean_title(value)


def _x_author_highlight(*, screen_name: str, author_name: str) -> str:
    handle = f"@{screen_name}" if screen_name else ""
    if handle and author_name:
        return f"{handle} / {author_name}"
    return handle or author_name


def _x_metrics_highlight(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for label, key in (
        ("likes", "likes"),
        ("retweets", "retweets"),
        ("replies", "replies"),
        ("views", "views"),
        ("bookmarks", "bookmarks"),
    ):
        raw = value.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        parts.append(f"{label}: {raw}")
    return ", ".join(parts)


def _nested_text(row: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _first_text(current)


def _statistics_highlight(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for label, key in (
        ("likes", "digg_count"),
        ("comments", "comment_count"),
        ("shares", "share_count"),
    ):
        raw = value.get(key)
        if raw is None:
            continue
        parts.append(f"{label}: {raw}")
    return ", ".join(parts)


def build_platform_source_backends(
    config: object,
    *,
    database: object | None = None,
    bilibili_client: object | None = None,
    youtube_client: object | None = None,
    x_client: object | None = None,
    github_client: object | None = None,
    reddit_runner: object | None = None,
    douyin_client: object | None = None,
    xhs_search: PlatformSearchCallable | None = None,
    zhihu_search: PlatformSearchCallable | None = None,
    bangumi_client: object | None = None,
    v2ex_client: object | None = None,
    weibo_client: object | None = None,
) -> list[PlatformSearchBackend]:
    """Build inspiration-only backends for enabled synchronous platform sources."""

    sources = getattr(config, "sources", None)
    backends: list[PlatformSearchBackend] = []
    bili_cfg = getattr(sources, "bilibili", None)
    if bool(getattr(bili_cfg, "enabled", True)) and bilibili_client is not None:
        backends.append(BilibiliPlatformSearchBackend(bilibili_client))

    xhs_cfg = getattr(sources, "xiaohongshu", None)
    if bool(getattr(xhs_cfg, "enabled", False)) and xhs_search is not None:
        backends.append(XhsPlatformSearchBackend(xhs_search))

    douyin_cfg = getattr(sources, "douyin", None)
    if bool(getattr(douyin_cfg, "enabled", False)) and douyin_client is not None:
        backends.append(DouyinPlatformSearchBackend(douyin_client))

    youtube_cfg = getattr(sources, "youtube", None)
    if bool(getattr(youtube_cfg, "enabled", False)):
        yt_client = youtube_client
        if yt_client is None:
            try:
                from openbiliclaw.youtube.client import YtScraperClient

                yt_client = YtScraperClient()
            except Exception:
                logger.debug("youtube inspiration backend unavailable", exc_info=True)
                yt_client = None
        if yt_client is not None:
            backends.append(YoutubePlatformSearchBackend(yt_client))

    twitter_cfg = getattr(sources, "twitter", None)
    if bool(getattr(twitter_cfg, "enabled", False)) and x_client is not None:
        backends.append(XPlatformSearchBackend(x_client))

    github_cfg = getattr(sources, "github", None)
    if bool(getattr(github_cfg, "enabled", False)) and github_client is not None:
        backends.append(GitHubPlatformSearchBackend(github_client, database=database))

    reddit_cfg = getattr(sources, "reddit", None)
    reddit_backend = str(getattr(reddit_cfg, "backend", "rdt") or "rdt").strip().lower()
    if bool(getattr(reddit_cfg, "enabled", False)) and reddit_backend not in {
        "extension",
        "plugin",
        "browser",
    }:
        backends.append(
            RedditPlatformSearchBackend(
                backend=reddit_backend,
                runner=reddit_runner,
            )
        )

    zhihu_cfg = getattr(sources, "zhihu", None)
    if bool(getattr(zhihu_cfg, "enabled", False)) and zhihu_search is not None:
        backends.append(ZhihuPlatformSearchBackend(zhihu_search))

    bangumi_cfg = getattr(sources, "bangumi", None)
    if bool(getattr(bangumi_cfg, "enabled", False)) and bangumi_client is not None:
        backends.append(
            BangumiPlatformSearchBackend(
                bangumi_client,
                subject_types=tuple(
                    getattr(bangumi_cfg, "subject_types", ("anime", "book", "game"))
                ),
            )
        )

    v2ex_cfg = getattr(sources, "v2ex", None)
    if bool(getattr(v2ex_cfg, "enabled", False)) and v2ex_client is not None:
        backends.append(V2EXPlatformSearchBackend(v2ex_client))
    weibo_cfg = getattr(sources, "weibo", None)
    if bool(getattr(weibo_cfg, "enabled", False)) and weibo_client is not None:
        backends.append(WeiboPlatformSearchBackend(weibo_client))
    return backends


def build_inspiration_search_provider(
    backends: object = None,
    *,
    runner: Callable[[list[str], float], Awaitable[str]] | None = None,
    timeout_seconds: float = 6.0,
    database: object | None = None,
    platform_backends: list[PlatformSearchBackend] | None = None,
    platforms_per_probe: int = 2,
    riskcontrolled_probe_budget: int = 4,
    pages_per_probe: int = 1,
    exa_api_key: str = "",
    you_api_key: str = "",
    serply_api_key: str = "",
) -> InspirationSearchProvider | None:
    """Build the configured inspiration search provider chain."""

    providers: list[InspirationSearchProvider] = []
    for backend in _normalize_search_backends(backends):
        if backend == "local_cache":
            if database is not None:
                providers.append(LocalInspirationProvider(database))
        elif backend == "platform_sources":
            if platform_backends:
                providers.append(
                    PlatformSourceInspirationProvider(
                        platform_backends,
                        platforms_per_query=platforms_per_probe,
                        riskcontrolled_probe_budget=riskcontrolled_probe_budget,
                        pages_per_probe=pages_per_probe,
                    )
                )
        elif backend == "bing_rss":
            providers.append(
                BingRssInspirationProvider(
                    timeout_seconds=timeout_seconds,
                )
            )
        elif backend == "exa":
            if str(exa_api_key or "").strip():
                providers.append(
                    ExaInspirationProvider(
                        api_key=exa_api_key,
                        timeout_seconds=timeout_seconds,
                    )
                )
            elif runner is not None or not _mcporter_cli_missing():
                providers.append(
                    McporterExaInspirationProvider(
                        runner=runner,
                        timeout_seconds=timeout_seconds,
                    )
                )
            else:
                _warn_mcporter_missing_once("exa")
        elif backend == "you":
            if str(you_api_key or "").strip():
                providers.append(
                    YouInspirationProvider(
                        api_key=you_api_key,
                        timeout_seconds=timeout_seconds,
                    )
                )
            elif runner is not None or not _mcporter_cli_missing():
                providers.append(
                    McporterYouInspirationProvider(
                        runner=runner,
                        timeout_seconds=timeout_seconds,
                    )
                )
            else:
                _warn_mcporter_missing_once("you")
        elif backend == "serply" and str(serply_api_key or "").strip():
            providers.append(
                SerplyInspirationProvider(
                    api_key=serply_api_key,
                    timeout_seconds=timeout_seconds,
                )
            )
    if not providers:
        return None
    if len(providers) == 1:
        return providers[0]
    return FallbackInspirationSearchProvider(providers, pages_per_probe=pages_per_probe)


def _normalize_search_backends(value: object) -> tuple[str, ...]:
    raw_values: list[str]
    if value is None:
        raw_values = list(_DEFAULT_SEARCH_BACKENDS)
    elif isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_values = []

    aliases = {
        "bing": "bing_rss",
        "bing_rss": "bing_rss",
        "bing-rss": "bing_rss",
        "exa": "exa",
        "local": "local_cache",
        "cache": "local_cache",
        "local_cache": "local_cache",
        "local-cache": "local_cache",
        "platform_sources": "platform_sources",
        "platform-source": "platform_sources",
        "platform": "platform_sources",
        "you": "you",
        "you.com": "you",
        "youcom": "you",
        "you-search": "you",
        "you_search": "you",
        "serply": "serply",
        "serply.io": "serply",
    }
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        backend = aliases.get(raw.strip().lower())
        if backend is None or backend in seen:
            continue
        normalized.append(backend)
        seen.add(backend)
    return tuple(normalized or _DEFAULT_SEARCH_BACKENDS)


def _mcporter_cli_missing() -> bool:
    """Whether the local ``mcporter`` command cannot be found on PATH."""
    return shutil.which("mcporter") is None


_MCPORTER_MISSING_WARNED: set[str] = set()


def _warn_mcporter_missing_once(backend: str) -> None:
    """Log one actionable warning per backend per process."""
    if backend in _MCPORTER_MISSING_WARNED:
        return
    _MCPORTER_MISSING_WARNED.add(backend)
    logger.warning(
        "inspiration backend '%s' skipped: mcporter CLI not found on PATH; "
        "install mcporter or remove it from `discovery.inspiration_search_backends`",
        backend,
    )


def _backend_cooldown_remaining(backend: object) -> float:
    getter = getattr(backend, "cooldown_remaining", None)
    if not callable(getter):
        getter = getattr(backend, "search_cooldown_remaining", None)
    if not callable(getter):
        return 0.0
    try:
        return max(0.0, float(getter()))
    except Exception:
        logger.debug("platform inspiration cooldown lookup failed", exc_info=True)
        return 0.0


def _raise_for_mcporter_error(payload: object, *, backend: str) -> None:
    data: object
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return
    else:
        data = payload
    if not isinstance(data, dict):
        return
    error = str(data.get("error") or "").strip()
    if not error:
        return
    issue = data.get("issue")
    if isinstance(issue, dict):
        raw_message = str(issue.get("rawMessage") or "").strip()
        if raw_message:
            error = raw_message
    raise RuntimeError(f"mcporter {backend} search failed: {error}")


def parse_exa_search_payload(payload: object) -> list[ExaPreviewItem]:
    """Parse Exa search JSON output into preview items."""

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return _parse_exa_text_results("\n".join(_extract_mcporter_text(payload)))
    else:
        data = payload
    mcp_text = _extract_mcp_content_text(data)
    if mcp_text:
        return _parse_exa_text_results("\n".join(mcp_text))
    raw_results: object = data.get("results", []) if isinstance(data, dict) else data
    if not isinstance(raw_results, list):
        return []

    previews: list[ExaPreviewItem] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        highlights = _clean_highlights(item.get("highlights"))
        previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
    return previews


def parse_you_search_payload(payload: object) -> list[ExaPreviewItem]:
    """Parse You.com MCP search output into preview items."""

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            previews: list[ExaPreviewItem] = []
            text_blocks = _extract_mcporter_text(payload) or [payload]
            for text in text_blocks:
                previews.extend(_parse_you_text_results(text))
            return previews
    else:
        data = payload
    return _parse_you_data_results(data)


def parse_serply_search_payload(payload: object) -> list[ExaPreviewItem]:
    """Parse Serply search JSON output into preview items."""

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return []
    else:
        data = payload
    raw_results: object = data.get("results", []) if isinstance(data, dict) else data
    if not isinstance(raw_results, list):
        return []

    previews: list[ExaPreviewItem] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or "").strip()
        if not title or not url:
            continue
        description = str(item.get("description") or "").strip()
        previews.append(
            ExaPreviewItem(title=title, url=url, highlights=(description,) if description else ())
        )
    return previews


def _parse_you_text_results(text: str) -> list[ExaPreviewItem]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return []
    try:
        return _parse_you_data_results(json.loads(clean_text))
    except (TypeError, ValueError):
        pass

    first_object = min(
        [idx for idx in (clean_text.find("{"), clean_text.find("[")) if idx >= 0],
        default=-1,
    )
    last_object = max(clean_text.rfind("}"), clean_text.rfind("]"))
    if first_object >= 0 and last_object > first_object:
        try:
            return _parse_you_data_results(json.loads(clean_text[first_object : last_object + 1]))
        except (TypeError, ValueError):
            pass
    return _parse_exa_text_results(clean_text)


def _parse_you_data_results(data: object) -> list[ExaPreviewItem]:
    previews: list[ExaPreviewItem] = []
    for item in _collect_you_result_dicts(data):
        title = str(item.get("title") or item.get("name") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        if not title or not url:
            continue
        highlights = _clean_highlights(
            [
                *_coerce_highlight_items(item.get("snippets")),
                *_coerce_highlight_items(item.get("snippet")),
                *_coerce_highlight_items(item.get("description")),
                *_coerce_highlight_items(item.get("summary")),
            ]
        )
        previews.append(ExaPreviewItem(title=title, url=url, highlights=tuple(highlights)))
    return previews


def _collect_you_result_dicts(data: object) -> list[dict[str, object]]:
    if isinstance(data, list):
        results: list[dict[str, object]] = []
        for item in data:
            results.extend(_collect_you_result_dicts(item))
        return results
    if not isinstance(data, dict):
        return []

    content_text = _extract_mcp_content_text(data)
    if content_text:
        content_results: list[dict[str, object]] = []
        for text in content_text:
            for preview in _parse_you_text_results(text):
                content_results.append(
                    {
                        "title": preview.title,
                        "url": preview.url,
                        "snippets": list(preview.highlights),
                    }
                )
        return content_results

    if (data.get("title") or data.get("name")) and (data.get("url") or data.get("link")):
        return [data]

    results = []
    for key in (
        "results",
        "hits",
        "web",
        "news",
        "search_results",
        "organic",
        "items",
        "data",
    ):
        if key in data:
            results.extend(_collect_you_result_dicts(data.get(key)))
    return results


def _coerce_highlight_items(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if value:
        return [value]
    return []


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return _clean_title(text)
    return ""


def _clean_title(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _youtube_text(value: object) -> str:
    if isinstance(value, str):
        return _clean_title(value)
    if isinstance(value, dict):
        simple = value.get("simpleText")
        if simple:
            return _clean_title(simple)
        runs = value.get("runs")
        if isinstance(runs, list):
            return _clean_title("".join(str(item.get("text", "")) for item in runs))
    return ""


def _extract_mcp_content_text(data: object) -> list[str]:
    if not isinstance(data, dict):
        return []
    content = data.get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            texts.append(text)
    return texts


def _extract_mcporter_text(payload: str) -> list[str]:
    """Extract text blocks from mcporter's raw Node-inspect output."""

    chunks: list[str] = []
    in_text = False
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not in_text:
            if not line.startswith("text: "):
                continue
            in_text = True
            line = line.removeprefix("text: ").strip()
        elif line.startswith("_meta:") or line.startswith("}") or line.startswith("]"):
            break

        match = re.match(r"'((?:\\.|[^'\\])*)'(?:\s*\+)?(?:,)?$", line)
        if not match:
            if in_text and line and not line.startswith("'"):
                break
            continue
        chunks.append(_decode_js_single_quoted(match.group(1)))
    text = "".join(chunks).strip()
    return [text] if text else []


def _decode_js_single_quoted(value: str) -> str:
    try:
        decoded = ast.literal_eval("'" + value + "'")
    except (SyntaxError, ValueError):
        return value.replace("\\n", "\n")
    return str(decoded)


def _parse_exa_text_results(text: str) -> list[ExaPreviewItem]:
    previews: list[ExaPreviewItem] = []
    current_title = ""
    current_url = ""
    current_highlights: list[str] = []
    in_highlights = False

    def flush() -> None:
        nonlocal current_title, current_url, current_highlights, in_highlights
        if current_title and current_url:
            previews.append(
                ExaPreviewItem(
                    title=current_title,
                    url=current_url,
                    highlights=tuple(_clean_highlights(current_highlights)),
                )
            )
        current_title = ""
        current_url = ""
        current_highlights = []
        in_highlights = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Title:"):
            flush()
            current_title = line.removeprefix("Title:").strip()
            continue
        if not current_title:
            continue
        if line.startswith("URL:"):
            current_url = line.removeprefix("URL:").strip()
            in_highlights = False
            continue
        if line.startswith("Highlights:"):
            in_highlights = True
            continue
        if line.startswith(("Published:", "Author:")):
            in_highlights = False
            continue
        if in_highlights and line and line != "...":
            current_highlights.append(line)

    flush()
    return previews


def _clean_highlights(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        value = [value] if value else []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


async def _run_command(args: list[str], timeout_seconds: float) -> str:
    # mcporter dials the same overseas APIs as the direct providers above;
    # route the subprocess through the [network] policy exactly like reddit's
    # rdt-cli / OpenCLI backends (custom injects the proxy vars, system
    # preserves the caller env, direct strips them).
    from openbiliclaw.network import outbound_cli_environment

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=outbound_cli_environment(),
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("mcporter search timed out") from exc
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"mcporter search failed: {detail}")
    return stdout.decode("utf-8", errors="replace")
