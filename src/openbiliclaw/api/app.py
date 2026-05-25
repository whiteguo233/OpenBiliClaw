"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, BinaryIO, cast
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    BehaviorEventBatchIn,
    BilibiliConfigOut,
    BilibiliCookieIn,
    BilibiliCookieResponse,
    BilibiliSourceConfigOut,
    ChatIn,
    ChatTurnIn,
    ChatTurnListResponse,
    ChatTurnOut,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    CognitionUpdateSummary,
    ConfigIssueOut,
    ConfigResponse,
    ConfigUpdateIn,
    ConfigUpdateResponse,
    DelightAckIn,
    DelightAckResponse,
    DouyinCookieIn,
    DouyinCookieResponse,
    DouyinSourceConfigOut,
    EmbeddingConfigOut,
    EventIngestResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    LLMConfigOut,
    LLMProviderConfigOut,
    LoggingConfigOut,
    ModuleLLMConfigOut,
    NotificationAckIn,
    NotificationAckResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingDelightOut,
    PendingDelightResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    ProfileSummaryResponse,
    RecommendationAppendIn,
    RecommendationClickIn,
    RecommendationClickResponse,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RecommendationReshuffleResponse,
    RuntimeStatusResponse,
    SchedulerConfigOut,
    SourcesBrowserConfigOut,
    SourcesConfigOut,
    SourceShareSuggestionIn,
    SourceShareSuggestionResponse,
    StorageConfigOut,
    XiaohongshuSourceConfigOut,
    YoutubeSourceConfigOut,
)
from openbiliclaw.soul.dislike_writeback import (
    apply_new_dislikes,
    topics_for_confirmed_avoidance,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)
_CONFIG_SAVE_LOCK = asyncio.Lock()

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}

_SOURCE_SHARE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube")
_PROBE_MODES = {"near", "lateral", "bridge", "wildcard"}
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}

_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_IMAGE_PROXY_ALLOWED_SUFFIXES = (
    "hdslb.com",
    "xhscdn.com",
    "pstatp.com",
    "douyinpic.com",
    "douyinvod.com",
    "ytimg.com",
    "ggpht.com",
)
_IMAGE_PROXY_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_PROXY_SPOOL_MEMORY_BYTES = 1024 * 1024
_IMAGE_PROXY_TIMEOUT_SECONDS = 10.0
_IMAGE_PROXY_MAX_REDIRECTS = 3
_IMAGE_CACHE_MAX_AGE_DAYS = 30
_IMAGE_PROXY_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_IMAGE_PROXY_UPSTREAM_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    ),
}


def _default_route_ip() -> str | None:
    """Return the IPv4 address selected for outbound traffic, if usable."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            return str(ip) if ip else None
    except Exception:
        return None


def _interface_ipv4_candidates() -> list[str]:
    """Best-effort local IPv4 enumeration without extra dependencies."""
    commands: list[list[str]]
    if os.name == "nt":
        commands = [["ipconfig"]]
    else:
        commands = [["ifconfig"], ["ip", "-4", "addr", "show", "scope", "global"]]

    candidates: list[str] = []
    seen: set[str] = set()
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for ip in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", proc.stdout):
            if ip not in seen:
                candidates.append(ip)
                seen.add(ip)
        if candidates:
            break
    return candidates


def _is_rfc1918_ipv4(addr: ipaddress.IPv4Address) -> bool:
    return any(addr in network for network in _RFC1918_NETWORKS)


def _usable_lan_candidate(ip: str) -> tuple[bool, bool]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (False, False)
    if not isinstance(addr, ipaddress.IPv4Address):
        return (False, False)
    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr in _BENCHMARK_NETWORK
    ):
        return (False, False)
    return (True, _is_rfc1918_ipv4(addr))


def _detect_lan_ip() -> str | None:
    """Return a likely phone-reachable LAN IPv4 address.

    UDP default-route detection can return VPN/TUN addresses such as
    198.18.0.1 on macOS. Prefer RFC1918 interface addresses and only use
    the default-route result when it is not a benchmark / loopback address.
    """
    candidates = _interface_ipv4_candidates()
    route_ip = _default_route_ip()
    if route_ip:
        candidates.append(route_ip)

    fallback: str | None = None
    for candidate in candidates:
        usable, rfc1918 = _usable_lan_candidate(candidate)
        if not usable:
            continue
        if rfc1918:
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


_RESETTABLE_CONFIG_FIELDS = {
    "llm.openai.api_key": ("llm", "openai", "api_key"),
    "llm.claude.api_key": ("llm", "claude", "api_key"),
    "llm.gemini.api_key": ("llm", "gemini", "api_key"),
    "llm.deepseek.api_key": ("llm", "deepseek", "api_key"),
    "llm.openrouter.api_key": ("llm", "openrouter", "api_key"),
    "llm.openai_compatible.api_key": ("llm", "openai_compatible", "api_key"),
    "llm.embedding.api_key": ("llm", "embedding", "api_key"),
}


def _config_backup_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.name}.bak")


def _snapshot_config_file(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    backup_path = _config_backup_path(config_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup_path)
    return backup_path


def _restore_config_snapshot(backup_path: Path, config_path: Path) -> None:
    shutil.copy2(backup_path, config_path)


def _validate_llm_buildable(cfg: Any, base_issues: list[Any]) -> list[Any]:
    from openbiliclaw.config import ConfigIssue
    from openbiliclaw.llm.registry import RegistryBuildError, build_llm_registry

    issues = list(base_issues)
    try:
        build_llm_registry(cfg)
    except RegistryBuildError as exc:
        issues.append(
            ConfigIssue(
                field="llm",
                message=f"LLM registry would fail to build: {exc}",
                severity="blocking",
            )
        )
    return issues


def _count_events_by_source_platform(database: Any) -> dict[str, int]:
    """Count stored behavior events by normalized source platform."""

    counter = {source: 0 for source in _SOURCE_SHARE_ORDER}
    if hasattr(database, "count_events_by_source_platform"):
        raw_counts = database.count_events_by_source_platform()
        if isinstance(raw_counts, dict):
            for source, count in raw_counts.items():
                source_key = _normalize_source_platform(source)
                counter[source_key] = counter.get(source_key, 0) + int(count)
            return {source: counter.get(source, 0) for source in _SOURCE_SHARE_ORDER}

    rows: list[dict[str, Any]] = []
    if hasattr(database, "conn"):
        try:
            cursor = database.conn.execute("SELECT metadata FROM events")
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            rows = []
    elif hasattr(database, "get_recent_events"):
        try:
            rows = list(database.get_recent_events(limit=10000))
        except Exception:
            rows = []

    for row in rows:
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json as _json

                metadata = _json.loads(metadata) if metadata else {}
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        source = metadata.get("source_platform", row.get("source_platform", "bilibili"))
        source_key = _normalize_source_platform(source)
        counter[source_key] = counter.get(source_key, 0) + 1
    return {source: counter.get(source, 0) for source in _SOURCE_SHARE_ORDER}


def _normalize_source_platform(source: object) -> str:
    source_key = str(source or "").strip().lower()
    if source_key in {"xhs", "rednote"}:
        return "xiaohongshu"
    if source_key in {"yt", "youtube"}:
        return "youtube"
    if source_key in {"douyin", "tiktok"}:
        return "douyin"
    if source_key in {"bilibili", "bili", ""}:
        return "bilibili"
    return source_key


def _infer_source_platform_from_url(url: object) -> str:
    text = str(url or "").strip().lower()
    if "youtube.com" in text or "youtu.be" in text:
        return "youtube"
    if "xiaohongshu.com" in text or "xhslink.com" in text:
        return "xiaohongshu"
    if "douyin.com" in text:
        return "douyin"
    if "bilibili.com" in text or "b23.tv" in text:
        return "bilibili"
    return ""


def _fallback_recommendation_click_url(
    *,
    source_platform: str,
    content_id: str,
    bvid: str,
) -> str:
    """Build a canonical click URL when the recommendation row lacks one."""
    item_id = (content_id or bvid).strip()
    if not item_id:
        return ""
    if source_platform == "youtube":
        return f"https://www.youtube.com/watch?v={quote(item_id, safe='')}"
    if source_platform == "douyin":
        return f"https://www.douyin.com/video/{quote(item_id, safe='')}"
    if source_platform == "bilibili":
        return f"https://www.bilibili.com/video/{quote(bvid or item_id, safe='')}"
    return ""


def _normalize_probe_mode_for_payload(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _PROBE_MODES else "near"


def _probe_metadata_for_payload(item: object) -> tuple[str, bool]:
    probe_mode = _normalize_probe_mode_for_payload(getattr(item, "probe_mode", ""))
    challenge = probe_mode in _PROBE_CHALLENGE_MODES
    with suppress(Exception):
        challenge = challenge or bool(getattr(item, "challenge", False))
    return probe_mode, challenge


def _cap_by_franchise(
    rows: list[dict[str, Any]],
    *,
    max_per_franchise: int = 2,
) -> list[dict[str, Any]]:
    """Drop later duplicates of the same ``franchise_key`` from a list.

    ``franchise_key`` is the LLM-tagged IP / series column (set during
    content evaluation, see ``llm/prompts.py`` and
    ``discovery/engine.py``). Empty franchise = general-interest content
    (科普 / 美食 / 通用资讯…) and passes through with no constraint —
    only matched IPs are subject to the cap.

    Why not in SQL: the recommendation pipeline orders by
    ``created_at DESC`` and we want a stable preserve-newest-N filter
    that's clearly testable. SQL window functions could do it, but the
    in-Python pass is cheap (≤ 40 rows) and easy to audit.
    """
    if max_per_franchise <= 0:
        return list(rows)
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        franchise = str(row.get("franchise_key", "") or "").strip()
        if not franchise:
            out.append(row)
            continue
        if seen.get(franchise, 0) >= max_per_franchise:
            continue
        seen[franchise] = seen.get(franchise, 0) + 1
        out.append(row)
    return out


def _normalize_cognition_update(item: dict[str, object]) -> CognitionUpdateSummary:
    impact = str(item.get("impact", "")).strip()
    reasoning = str(item.get("reasoning", "")).strip()
    evidence = str(item.get("evidence", "")).strip()
    source = str(item.get("source", "")).strip()
    source_label = str(item.get("source_label", "")).strip() or SOURCE_LABELS.get(source, "")
    expand_hint = str(item.get("expand_hint", "")).strip()
    if expand_hint not in {"expandable", "summary_only"}:
        expand_hint = "expandable" if any((impact, reasoning, evidence)) else "summary_only"
    return CognitionUpdateSummary(
        summary=str(item.get("summary", "")).strip(),
        context_line=str(item.get("context_line", "")).strip() or "基于最近几条相关内容",
        impact=impact,
        reasoning=reasoning,
        evidence=evidence,
        source=source,
        source_label=source_label,
        expand_hint=expand_hint,
        created_at=str(item.get("created_at", "")).strip(),
    )


def _is_image_proxy_host_allowed(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return any(
        host == suffix or host.endswith(f".{suffix}") for suffix in _IMAGE_PROXY_ALLOWED_SUFFIXES
    )


def _parse_image_proxy_url(raw_url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(raw_url)
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=400, detail="Invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if parsed.userinfo:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if not _is_image_proxy_host_allowed(parsed.host):
        raise HTTPException(status_code=403, detail="Domain not in whitelist")
    return parsed


def _validate_image_proxy_content_headers(headers: httpx.Headers) -> str:
    content_type = str(headers.get("content-type", "")).strip()
    if not content_type.lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="Not an image")
    content_length = headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Invalid upstream content length") from exc
        if size > _IMAGE_PROXY_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Image too large")
    return content_type


def _iter_spooled_file(file_obj: BinaryIO) -> Iterator[bytes]:
    try:
        while True:
            chunk = file_obj.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        file_obj.close()


def _image_cache_dir() -> Path:
    from pathlib import Path

    d = Path("data/image-cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_cache_url(url: str) -> str:
    """Normalize URLs with expiring tokens so the same image always maps to the same cache key.

    XHS CDN URLs: ``https://sns-webpic-qc.xhscdn.com/{timestamp}/{token}/{path}``
    — the ``{timestamp}/{token}`` changes on every regeneration but ``{path}`` is stable.
    """
    import re

    m = re.match(r"(https?://[^/]*xhscdn\.com)/\d{12}/[0-9a-f]+/(.*)", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return url


def _image_cache_key(url: str) -> str:
    return hashlib.sha256(_normalize_cache_url(url).encode()).hexdigest()


def _image_cache_lookup(url: str) -> tuple[Path, str] | None:
    """Return (path, content_type) if a cached copy exists."""
    key = _image_cache_key(url)
    cache_dir = _image_cache_dir()
    for candidate in cache_dir.glob(f"{key}.*"):
        ext = candidate.suffix.lstrip(".")
        content_type = f"image/{ext}" if ext else "image/jpeg"
        if candidate.stat().st_size > 0:
            return candidate, content_type
    return None


def _image_cache_save(url: str, data: bytes, content_type: str) -> None:
    """Persist image bytes to disk cache."""
    key = _image_cache_key(url)
    ext = content_type.split("/")[-1].split(";")[0].strip()
    if ext not in {"jpeg", "jpg", "png", "webp", "avif", "gif"}:
        ext = "jpg"
    path = _image_cache_dir() / f"{key}.{ext}"
    with suppress(Exception):
        path.write_bytes(data)


def _image_cache_response(url: str) -> FileResponse | None:
    cached = _image_cache_lookup(url)
    if not cached:
        return None
    cache_path, cache_ct = cached
    return FileResponse(
        cache_path,
        media_type=cache_ct,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "X-Image-Cache": "hit",
        },
    )


def _image_cache_cleanup() -> int:
    """Remove cached images older than _IMAGE_CACHE_MAX_AGE_DAYS. Returns count removed."""
    import time

    cache_dir = _image_cache_dir()
    cutoff = time.time() - _IMAGE_CACHE_MAX_AGE_DAYS * 86400
    removed = 0
    with suppress(Exception):
        for f in cache_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                removed += 1
    return removed


async def _send_image_proxy_request(client: httpx.AsyncClient, url: httpx.URL) -> httpx.Response:
    current = url
    seen: set[str] = set()
    for _ in range(_IMAGE_PROXY_MAX_REDIRECTS + 1):
        current = _parse_image_proxy_url(str(current))
        current_key = str(current)
        if current_key in seen:
            raise HTTPException(status_code=502, detail="Redirect loop")
        seen.add(current_key)
        request = client.build_request("GET", current_key, headers=_IMAGE_PROXY_UPSTREAM_HEADERS)
        response = await client.send(request, stream=True)
        if response.status_code in _IMAGE_PROXY_REDIRECT_STATUSES:
            location = response.headers.get("location", "").strip()
            await response.aclose()
            if not location:
                raise HTTPException(status_code=502, detail="Invalid redirect")
            current = current.join(location)
            continue
        return response
    raise HTTPException(status_code=502, detail="Too many redirects")


async def _read_image_proxy_body(response: httpx.Response) -> BinaryIO:
    spool = tempfile.SpooledTemporaryFile(  # noqa: SIM115 - returned after validation.
        max_size=_IMAGE_PROXY_SPOOL_MEMORY_BYTES,
        mode="w+b",
    )
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _IMAGE_PROXY_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Image too large")
            spool.write(chunk)
        spool.seek(0)
        return cast("BinaryIO", spool)
    except Exception:
        spool.close()
        raise


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
    dialogue: Any | None = None,
    runtime_controller: Any | None = None,
    recommendation_engine: Any | None = None,
    runtime_event_hub: Any | None = None,
    account_sync_service: Any | None = None,
    auto_update_service: Any | None = None,
) -> FastAPI:
    """Create the local backend API app."""
    from openbiliclaw.api.runtime_context import (
        RuntimeContext,
        build_degraded_runtime_context,
        build_runtime_context,
    )
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import RegistryBuildError

    app = FastAPI(title="OpenBiliClaw API", default_response_class=JSONResponse)

    # GZip middleware: only compress responses ≥ 500 bytes.
    # ``minimum_size=0`` was previously used as a sledgehammer workaround
    # for an h11 Content-Length mismatch on CJK text in older starlette
    # versions, but the side-effect was that 204/empty responses were
    # also force-compressed (gzip header alone is ~20 bytes > original
    # body), tripping h11's strict size check on every poll. Modern
    # starlette already encodes JSON bodies as UTF-8 bytes for
    # Content-Length, so the original workaround is no longer needed.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Build RuntimeContext ────────────────────────────────────────
    config = load_config()

    if soul_engine is not None:
        # Injection path: caller provides swappable components.
        # Auto-create stable components (database, memory_manager) if missing.
        from openbiliclaw.runtime.events import RuntimeEventHub as _RuntimeEventHub

        _db = database
        _created_db = False
        if _db is None:
            from openbiliclaw.storage.database import Database

            _db = Database(config.data_path / "openbiliclaw.db")
            _db.initialize()
            _created_db = True
        _mm = memory_manager
        if _mm is None:
            from openbiliclaw.memory.manager import MemoryManager

            _mm = MemoryManager(config.data_path, database=_db if _created_db else None)
            _mm.initialize()

        ctx = RuntimeContext(
            database=_db,
            memory_manager=_mm,
            event_hub=runtime_event_hub
            or getattr(runtime_controller, "event_hub", None)
            or _RuntimeEventHub(),
            # config intentionally left None in injection path — matches
            # old behaviour where closures couldn't see config when all
            # core components were provided by the caller.
            soul_engine=soul_engine,
            dialogue=dialogue,
            runtime_controller=runtime_controller,
            recommendation_engine=recommendation_engine,
            account_sync_service=account_sync_service,
            auto_update_service=auto_update_service,
        )
        if ctx.dialogue is None:
            from openbiliclaw.soul.dialogue import SocraticDialogue

            ctx.dialogue = SocraticDialogue(llm=None, soul_engine=soul_engine, session="popup")
        if ctx.auto_update_service is None:
            from openbiliclaw.runtime.updater import AutoUpdateService

            ctx.auto_update_service = AutoUpdateService(enabled=True)
    else:
        # Production path: build everything from config.
        try:
            ctx = build_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
            )
        except RegistryBuildError as exc:
            ctx = build_degraded_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
                exc=exc,
            )
            logger.warning(
                "FastAPI started in degraded mode (%s): %s",
                ctx.degraded_reason,
                "; ".join(str(getattr(issue, "message", issue)) for issue in ctx.degraded_issues),
            )
    app.state.runtime_context = ctx
    app.state.degraded = bool(getattr(ctx, "degraded", False))
    app.state.degraded_reason = str(getattr(ctx, "degraded_reason", ""))
    app.state.degraded_issues = list(getattr(ctx, "degraded_issues", []))

    def _degraded_issues_payload() -> list[dict[str, str]]:
        return [
            {
                "field": str(getattr(issue, "field", "")),
                "message": str(getattr(issue, "message", issue)),
                "severity": str(getattr(issue, "severity", "warning")),
            }
            for issue in getattr(ctx, "degraded_issues", [])
        ]

    def _degraded_body() -> dict[str, object]:
        return {
            "status": "degraded",
            "reason": str(getattr(ctx, "degraded_reason", "")),
            "issues": _degraded_issues_payload(),
        }

    @app.middleware("http")
    async def _degraded_mode_guard(request: Request, call_next: Any) -> Any:
        if not bool(getattr(ctx, "degraded", False)):
            return await call_next(request)
        path = request.url.path
        method = request.method.upper()
        allowed = (
            method == "OPTIONS"
            or path == "/api/health"
            or path == "/api/runtime-status"
            or (path == "/api/config" and method in {"GET", "PUT"})
            or path.startswith("/m")
        )
        if allowed:
            return await call_next(request)
        return JSONResponse(status_code=503, content=_degraded_body())

    async def _run_post_feedback_tasks() -> None:
        with suppress(Exception):
            await ctx.soul_engine.process_feedback_batch_if_needed()

    async def _ingest_profile_update_events(events: list[dict[str, Any]]) -> None:
        """Feed source task events into the profile-update pipeline when ready.

        Init handles first-run analysis explicitly via ``analyze_events`` +
        ``build_initial_profile``. After a profile exists, extension task
        results should also affect the incremental update buffers instead of
        only being persisted to event memory.
        """
        if not events or ctx.soul_engine is None:
            return
        is_ready = getattr(ctx.soul_engine, "is_profile_ready", None)
        if callable(is_ready):
            with suppress(Exception):
                if not bool(is_ready()):
                    return

        pipeline = getattr(ctx.soul_engine, "pipeline", None)
        if pipeline is None:
            return

        from openbiliclaw.soul.pipeline import signals_from_events

        signals = signals_from_events(events)
        if not signals:
            return
        try:
            ingest_batch = getattr(pipeline, "ingest_batch", None)
            if callable(ingest_batch):
                await ingest_batch(signals)
                return
            ingest = getattr(pipeline, "ingest", None)
            if callable(ingest):
                for signal in signals:
                    await ingest(signal)
        except Exception:
            logger.exception("Failed to ingest source task events into profile pipeline")

    def _load_source_bootstrap_state() -> dict[str, object]:
        from openbiliclaw.sources.bootstrap_state import (
            default_source_bootstrap_state,
            normalize_source_bootstrap_state,
        )

        load_state = getattr(ctx.memory_manager, "load_source_bootstrap_state", None)
        if not callable(load_state):
            return default_source_bootstrap_state()
        with suppress(Exception):
            return normalize_source_bootstrap_state(load_state())
        return default_source_bootstrap_state()

    def _save_source_bootstrap_state(state: dict[str, object]) -> None:
        from openbiliclaw.sources.bootstrap_state import normalize_source_bootstrap_state

        save_state = getattr(ctx.memory_manager, "save_source_bootstrap_state", None)
        if not callable(save_state):
            return
        with suppress(Exception):
            save_state(normalize_source_bootstrap_state(state))

    def _filter_new_source_bootstrap_items(
        source: str,
        items: list[dict[str, Any]],
        key_func: Callable[[dict[str, Any]], str],
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """Filter bootstrap items that already propagated from an older task."""
        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        seen = set(as_string_list(state.get(state_key, [])))
        batch_seen: set[str] = set()
        fresh: list[dict[str, Any]] = []
        fresh_keys_by_index: dict[int, str] = {}
        for item in items:
            key = key_func(item)
            if not key or key in seen or key in batch_seen:
                continue
            batch_seen.add(key)
            fresh_keys_by_index[len(fresh)] = key
            fresh.append(item)
        return fresh, fresh_keys_by_index

    def _mark_source_bootstrap_keys(source: str, keys: list[str]) -> None:
        """Persist bootstrap keys that already entered the source event path."""
        if not keys:
            return
        from datetime import UTC, datetime

        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        merged = as_string_list(state.get(state_key, []))
        seen = set(merged)
        for key in keys:
            normalized = str(key).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        state[state_key] = merged
        state["last_source_bootstrap_sync_at"] = datetime.now(UTC).isoformat()
        _save_source_bootstrap_state(state)

    chat_turn_lock = asyncio.Lock()
    fallback_chat_turns: dict[str, dict[str, Any]] = {}
    running_chat_turn_tasks: set[str] = set()

    def _normalize_chat_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if normalized in {"chat", "delight", "probe", "avoidance_probe"}:
            return normalized
        return "chat"

    def _normalize_chat_turn(row: dict[str, Any]) -> ChatTurnOut:
        return ChatTurnOut(
            turn_id=str(row.get("turn_id", "")),
            session=str(row.get("session", "popup") or "popup"),
            scope=_normalize_chat_scope(str(row.get("scope", "chat"))),
            subject_id=str(row.get("subject_id", "") or ""),
            subject_title=str(row.get("subject_title", "") or ""),
            message=str(row.get("message", "") or ""),
            reply=str(row.get("reply", "") or ""),
            status=str(row.get("status", "pending") or "pending"),
            error=str(row.get("error", "") or ""),
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
        )

    def _chat_db_method(name: str) -> Any | None:
        method = getattr(ctx.database, name, None)
        return method if callable(method) else None

    def _get_chat_turn_row(turn_id: str) -> dict[str, Any] | None:
        get_chat_turn = _chat_db_method("get_chat_turn")
        if get_chat_turn is not None:
            return cast("dict[str, Any] | None", get_chat_turn(turn_id))
        row = fallback_chat_turns.get(turn_id)
        return dict(row) if row else None

    def _list_chat_turn_rows(
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        list_chat_turns = _chat_db_method("list_chat_turns")
        if list_chat_turns is not None:
            return cast(
                "list[dict[str, Any]]",
                list_chat_turns(session=session, scope=scope, limit=limit),
            )
        rows = [
            dict(row)
            for row in fallback_chat_turns.values()
            if row.get("session") == session and (not scope or row.get("scope") == scope)
        ]
        rows.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("turn_id", ""))))
        return rows[-max(1, int(limit)) :]

    def _create_chat_turn_row(payload: ChatTurnIn, *, turn_id: str) -> dict[str, Any]:
        create_chat_turn = _chat_db_method("create_chat_turn")
        if create_chat_turn is not None:
            return cast(
                "dict[str, Any]",
                create_chat_turn(
                    turn_id=turn_id,
                    session=payload.session.strip() or "popup",
                    scope=_normalize_chat_scope(payload.scope),
                    subject_id=payload.subject_id.strip(),
                    subject_title=payload.subject_title.strip(),
                    message=payload.message.strip(),
                ),
            )

        from datetime import datetime

        now = datetime.now().isoformat(sep=" ")
        fallback_chat_turns.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "session": payload.session.strip() or "popup",
                "scope": _normalize_chat_scope(payload.scope),
                "subject_id": payload.subject_id.strip(),
                "subject_title": payload.subject_title.strip(),
                "message": payload.message.strip(),
                "status": "pending",
                "reply": "",
                "error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        return dict(fallback_chat_turns[turn_id])

    def _complete_chat_turn_row(turn_id: str, *, reply: str) -> None:
        complete_chat_turn = _chat_db_method("complete_chat_turn")
        if complete_chat_turn is not None:
            complete_chat_turn(turn_id, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "completed",
                    "reply": reply,
                    "error": "",
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

    def _fail_chat_turn_row(turn_id: str, *, error: str, reply: str = "") -> None:
        fail_chat_turn = _chat_db_method("fail_chat_turn")
        if fail_chat_turn is not None:
            fail_chat_turn(turn_id, error=error, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "failed",
                    "reply": reply,
                    "error": error,
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

    def _health_profile_ready() -> bool | None:
        soul_engine = getattr(ctx, "soul_engine", None)
        if soul_engine is None:
            return None
        is_ready_candidate = getattr(soul_engine, "is_profile_ready", None)
        if not callable(is_ready_candidate):
            return None
        is_ready_fn = cast("Callable[[], bool]", is_ready_candidate)
        try:
            return bool(is_ready_fn())
        except Exception:
            logger.debug("Health profile readiness check failed", exc_info=True)
            return None

    @app.get("/api/health", response_model=HealthResponse, response_model_exclude_none=True)
    def health() -> HealthResponse | JSONResponse:
        profile_ready = _health_profile_ready()
        lan_ip = _detect_lan_ip()
        if bool(getattr(ctx, "degraded", False)):
            body: dict[str, object] = {
                "status": "degraded",
                "service": "openbiliclaw-api",
                "reason": str(getattr(ctx, "degraded_reason", "")),
                "issues": _degraded_issues_payload(),
            }
            if profile_ready is not None:
                body["profile_ready"] = profile_ready
            if lan_ip is not None:
                body["lan_ip"] = lan_ip
            return JSONResponse(status_code=200, content=body)
        return HealthResponse(
            status="ok",
            service="openbiliclaw-api",
            profile_ready=profile_ready,
            lan_ip=lan_ip,
        )

    @app.get("/api/image-proxy", response_model=None)
    async def image_proxy(
        url: str = Query(..., description="URL-encoded image URL to proxy"),
    ) -> StreamingResponse | FileResponse:
        """Proxy whitelisted remote cover images through the local backend.

        Successfully fetched images are cached to ``data/image-cache/``.
        When the upstream fails (e.g. expired XHS CDN tokens), the cached
        copy is served instead.
        """

        parsed = _parse_image_proxy_url(url)
        try:
            async with httpx.AsyncClient(
                timeout=_IMAGE_PROXY_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = await _send_image_proxy_request(client, parsed)
                try:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise HTTPException(status_code=502, detail="Upstream request failed")
                    content_type = _validate_image_proxy_content_headers(response.headers)
                    spool = await _read_image_proxy_body(response)
                finally:
                    await response.aclose()
        except httpx.TimeoutException as exc:
            # Network-level upstream failures may use a cached copy.
            if cached := _image_cache_response(url):
                return cached
            raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
        except httpx.HTTPError as exc:
            if cached := _image_cache_response(url):
                return cached
            raise HTTPException(status_code=502, detail="Upstream request failed") from exc
        except HTTPException as exc:
            if exc.status_code >= 500 and (cached := _image_cache_response(url)):
                return cached
            raise

        # Cache the successfully fetched image.
        spool.seek(0)
        image_bytes = spool.read()
        spool.seek(0)
        _image_cache_save(url, image_bytes, content_type)

        return StreamingResponse(
            _iter_spooled_file(spool),
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/bilibili/cookie", response_model=BilibiliCookieResponse)
    async def sync_bilibili_cookie(payload: BilibiliCookieIn) -> BilibiliCookieResponse:
        """Receive a Bilibili cookie from the browser extension and persist
        it server-side so the backend can call B 站 API as the user.

        Replaces the manual "F12 → Network → copy cookie → paste into
        wizard" flow. The extension already runs on bilibili.com and has
        the ``cookies`` Chrome permission, so it's the natural place to
        get a fresh, valid cookie. We auto-sync on first install and
        whenever ``chrome.cookies.onChanged`` fires.

        Persistence: writes to ``data/bilibili_cookie.json`` (the runtime
        cookie source) AND ``config.toml [bilibili].cookie`` (kept in sync
        as a mirror for ``config-show``). Then rebuilds the runtime
        BilibiliAPIClient via the same ``rebuild_from_config`` path that
        the config-update endpoint uses, so any in-flight handlers see
        the new cookie on their next call.

        Security: the backend is bound to 127.0.0.1 by default, so this
        endpoint is only reachable from the user's own machine. CORS
        already accepts ``*`` (set when the app is built); no auth token
        is needed for an API that lives behind a localhost-only listener.
        Users who flip ``--host 0.0.0.0`` should put their own auth
        layer in front of the backend.
        """
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import (
            load_config_with_diagnostics,
            save_config,
        )

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return BilibiliCookieResponse(
                ok=False,
                authenticated=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        config, diagnostics = load_config_with_diagnostics()
        # 1) Validate the cookie if requested. We use the same auth
        # manager the CLI's interactive wizard uses, for consistency.
        auth_manager = AuthManager(data_dir=config.data_path)
        if payload.validate_with_bilibili:
            status = await auth_manager.validate_cookie(cookie_value)
            if not status.authenticated:
                # Distinguish "network couldn't reach api.bilibili.com"
                # (transient — extension should retry quickly) from
                # "Bilibili rejected this cookie" (cookie expired —
                # extension should back off until next login). The
                # heuristic relies on AuthManager.validate_cookie's
                # ``message`` field — connection / timeout / DNS errors
                # surface as the underlying httpx exception text, while
                # an actual logged-out cookie surfaces as the literal
                # "当前 Cookie 未登录或已失效。" message we set in
                # validate_cookie.
                msg = (status.message or "").lower()
                network_markers = (
                    "timeout",
                    "connect",
                    "dns",
                    "ssl",
                    "proxy",
                    "name or service",
                    "connection",
                    "网络",
                    "代理",
                )
                is_network_error = any(m in msg for m in network_markers)
                error_code = "validation_network" if is_network_error else "cookie_invalid"
                return BilibiliCookieResponse(
                    ok=False,
                    authenticated=False,
                    message=status.message or "Cookie validation failed; not saved.",
                    error_code=error_code,
                )
            authenticated = True
            username = status.username or ""
            user_id = int(status.user_id or 0)
        else:
            authenticated = False
            username = ""
            user_id = 0

        # 2) Persist to both stores, but keep repeated extension syncs
        # idempotent. Chrome may POST the same Cookie several times around
        # startup; rebuilding for an unchanged effective cookie cancels and
        # restarts producer loops for no behavioral gain.
        stored_cookie = ""
        with suppress(Exception):
            stored_cookie = auth_manager.load_cookie().strip()
        configured_cookie = config.bilibili.cookie.strip()
        effective_cookie_before = configured_cookie or stored_cookie
        cookie_file_changed = stored_cookie != cookie_value
        config_changed = configured_cookie != cookie_value
        runtime_cookie_changed = effective_cookie_before != cookie_value

        if cookie_file_changed:
            auth_manager.set_cookie(cookie_value)  # → data/bilibili_cookie.json
        if config_changed:
            config.bilibili.cookie = cookie_value
            save_config(config, diagnostics.config_path)

        # 3) Reload runtime so existing in-flight components pick up
        # the new client. ``rebuild_from_config`` is atomic — if it
        # fails partway, the old runtime stays intact.
        runtime_refreshed = False
        if runtime_cookie_changed or config_changed:
            with suppress(Exception):
                await ctx.rebuild_from_config(config)
                await ctx.restart_background_tasks(app)
                runtime_refreshed = True

        # 4) Tell the extension UI the cookie just got refreshed —
        # this is how the popup knows it can stop nagging the user
        # to log in.
        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "bilibili_cookie_synced",
                    "username": username,
                    "user_id": user_id,
                    "source": payload.source,
                }
            )

        return BilibiliCookieResponse(
            ok=True,
            authenticated=authenticated,
            username=username,
            user_id=user_id,
            message=(
                "Cookie synced and runtime refreshed."
                if runtime_refreshed
                else "Cookie already synced; runtime unchanged."
            ),
        )

    @app.post("/api/sources/dy/cookie", response_model=DouyinCookieResponse)
    async def sync_douyin_cookie(payload: DouyinCookieIn) -> DouyinCookieResponse:
        """Receive a Douyin cookie from the browser extension.

        Unlike Bilibili, Douyin direct-cookie discovery currently has no
        stable nav endpoint that cleanly distinguishes "logged out" from
        "soft anti-bot returned HTTP 200 with empty data". We therefore
        persist the browser-provided Cookie header as-is and let discovery
        smoke surface whether search / hot / feed calls return content.
        """
        from openbiliclaw.sources.douyin_auth import DouyinCookieManager
        from openbiliclaw.sources.douyin_direct import parse_cookie_header

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return DouyinCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        runtime_config = getattr(ctx, "config", None) or config
        manager = DouyinCookieManager(runtime_config.data_path)
        manager.set_cookie(cookie_value, source=payload.source)
        cookie_names = sorted(parse_cookie_header(cookie_value).keys())

        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "douyin_cookie_synced",
                    "source": payload.source,
                    "cookie_names": cookie_names,
                }
            )

        return DouyinCookieResponse(
            ok=True,
            has_cookie=True,
            cookie_names=cookie_names,
            message="Douyin Cookie synced.",
        )

    @app.post("/api/init-completed")
    async def init_completed() -> dict[str, object]:
        """Notify the running server that ``openbiliclaw init`` has finished.

        Called by the CLI at the end of a successful init.  The handler
        broadcasts an ``init_completed`` event via WebSocket so the
        browser extension can immediately re-fetch profile, recommendations
        and activity data.  It also kicks the continuous-refresh controller
        so the discovery pool is picked up without waiting for the next
        60-second tick.
        """
        # Broadcast to extension
        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "init_completed",
                    "message": "初始化完成，画像与发现池已就绪。",
                }
            )
        # Kick refresh controller immediately. v0.3.63+: route through
        # the registry so a hot-reload mid-init can cancel this task.
        trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if callable(trigger):
            with suppress(Exception):
                registry = getattr(ctx, "task_registry", None)
                if registry is not None:
                    registry.track("init_completed_trigger", trigger())
                else:
                    asyncio.create_task(trigger())
        return {"ok": True}

    def _serialize_recommendation_items(items: list[Any]) -> list[RecommendationOut]:
        return [
            RecommendationOut(
                id=int(item.recommendation_id),
                bvid=str(item.content.bvid),
                title=str(item.content.title),
                up_name=str(item.content.up_name),
                cover_url=str(item.content.cover_url),
                expression=str(item.expression),
                topic_label=str(item.topic_label),
                presented=bool(item.presented),
                feedback_type=str(getattr(item, "feedback_type", "") or ""),
                content_id=str(getattr(item.content, "content_id", "") or item.content.bvid),
                content_url=str(getattr(item.content, "content_url", "") or ""),
                source_platform=str(getattr(item.content, "source_platform", "") or "bilibili"),
            )
            for item in items
        ]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        if bool(getattr(ctx, "degraded", False)):
            connected = False
            try:
                ctx.presence.on_connect()
                connected = True
                await websocket.send_json(
                    {
                        "type": "degraded",
                        "reason": str(getattr(ctx, "degraded_reason", "")),
                        "issues": _degraded_issues_payload(),
                    }
                )
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        raise WebSocketDisconnect
            except WebSocketDisconnect:
                pass
            finally:
                if connected:
                    ctx.presence.on_disconnect()
            return

        subscribe = getattr(ctx.event_hub, "subscribe", None)
        unsubscribe = getattr(ctx.event_hub, "unsubscribe", None)
        if not callable(subscribe) or not callable(unsubscribe):
            await websocket.close()
            return
        queue = await subscribe()
        connected = False

        async def _send_runtime_events() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event)

        async def _receive_until_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect

        try:
            ctx.presence.on_connect()
            connected = True
            client_name = str(websocket.query_params.get("client", "") or "").strip().lower()
            if client_name in {"background", "extension", "service-worker"}:
                from openbiliclaw.bilibili.auth import resolve_runtime_cookie
                from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

                runtime_config = getattr(ctx, "config", None) or config
                with suppress(Exception):
                    cookie = resolve_runtime_cookie(
                        data_dir=runtime_config.data_path,
                        configured_cookie=runtime_config.bilibili.cookie,
                    )
                    if not str(cookie or "").strip():
                        await websocket.send_json(
                            {
                                "type": "bilibili_cookie_sync_requested",
                                "reason": "missing_cookie",
                                "source": "runtime-stream",
                            }
                        )
                with suppress(Exception):
                    dy_cfg = getattr(runtime_config.sources, "douyin", None)
                    if dy_cfg is not None and bool(getattr(dy_cfg, "enabled", False)):
                        dy_cookie = resolve_douyin_cookie(
                            data_dir=runtime_config.data_path,
                            cookie_env=str(
                                getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")
                            ),
                        )
                        if not str(dy_cookie or "").strip():
                            await websocket.send_json(
                                {
                                    "type": "douyin_cookie_sync_requested",
                                    "reason": "missing_cookie",
                                    "source": "runtime-stream",
                                }
                            )

            writer = asyncio.create_task(_send_runtime_events())
            reader = asyncio.create_task(_receive_until_disconnect())
            done, pending = await asyncio.wait(
                {writer, reader},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                with suppress(WebSocketDisconnect):
                    task.result()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("runtime-stream closed after handler exception", exc_info=True)
        finally:
            if connected:
                ctx.presence.on_disconnect()
            await unsubscribe(queue)

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        # Clean up expired image cache on startup.
        try:
            removed = _image_cache_cleanup()
            if removed:
                logger.info("Image cache cleanup: removed %d expired files", removed)
        except Exception:
            logger.debug("Image cache cleanup failed", exc_info=True)

        if bool(getattr(ctx, "degraded", False)):
            return
        await ctx.restart_background_tasks(app)

    @app.on_event("shutdown")
    async def shutdown_refresh_loop() -> None:
        refresh_task = getattr(app.state, "refresh_task", None)
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        account_sync_task = getattr(app.state, "account_sync_task", None)
        if account_sync_task is not None:
            account_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await account_sync_task
        auto_update_task = getattr(app.state, "auto_update_task", None)
        if auto_update_task is not None:
            auto_update_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_update_task

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary(
        limit: int = Query(default=3, ge=1, le=20),
        cursor: str = "",
    ) -> ProfileSummaryResponse:
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        from openbiliclaw.api.models import (
            AwarenessNoteOut,
            ContextModeOut,
            InsightHypothesisOut,
            InterestDomainOut,
            InterestSpecificOut,
            MBTIDimensionOut,
            MBTIOut,
            SpeculativeAvoidanceOut,
            SpeculativeInterestOut,
            SpeculativeSpecificOut,
            StylePreferenceOut,
        )
        from openbiliclaw.soul.avoidance_speculator import load_avoidance_state
        from openbiliclaw.soul.speculator import load_speculative_state

        prefs = profile.preferences

        # ── Core layer ──
        mbti_obj = getattr(getattr(profile, "core", None), "mbti", None)
        mbti_out = MBTIOut()
        mbti_type = str(getattr(mbti_obj, "type", "") or "") if mbti_obj is not None else ""
        if mbti_type:
            mbti_out = MBTIOut(
                type=mbti_type,
                dimensions={
                    k: MBTIDimensionOut(pole=str(v.pole), strength=float(v.strength))
                    for k, v in getattr(mbti_obj, "dimensions", {}).items()
                },
                confidence=float(getattr(mbti_obj, "confidence", 0.0)),
            )

        # ── Interest layer (tree structure) ──
        interest_layer = getattr(profile, "interest", None)

        def _domain_list(raw_domains: object) -> list[InterestDomainOut]:
            if not isinstance(raw_domains, list):
                return []
            return [
                InterestDomainOut(
                    domain=str(getattr(d, "domain", "")),
                    weight=float(getattr(d, "weight", 0.5)),
                    specifics=[
                        InterestSpecificOut(
                            name=str(getattr(s, "name", "")),
                            weight=float(getattr(s, "weight", 0.5)),
                        )
                        for s in getattr(d, "specifics", [])
                        if str(getattr(s, "name", "")).strip()
                    ],
                )
                for d in raw_domains
                if str(getattr(d, "domain", "")).strip()
            ]

        likes_out = _domain_list(getattr(interest_layer, "likes", []))[:12]
        dislikes_out = _domain_list(getattr(interest_layer, "dislikes", []))[:8]

        favorite_ups = [
            str(item).strip()
            for item in getattr(prefs, "favorite_up_users", [])[:8]
            if str(item).strip()
        ]

        # ── Surface layer ──
        style_raw = getattr(prefs, "style", None)
        style_out = StylePreferenceOut()
        if style_raw is not None:
            style_out = StylePreferenceOut(
                preferred_duration=str(getattr(style_raw, "preferred_duration", "")),
                preferred_pace=str(getattr(style_raw, "preferred_pace", "")),
                quality_sensitivity=float(getattr(style_raw, "quality_sensitivity", 0.5)),
                humor_preference=float(getattr(style_raw, "humor_preference", 0.5)),
                depth_preference=float(getattr(style_raw, "depth_preference", 0.5)),
            )
        ctx_raw = getattr(prefs, "context", None)
        ctx_out = ContextModeOut()
        if ctx_raw is not None:
            ctx_out = ContextModeOut(
                weekday_patterns=str(getattr(ctx_raw, "weekday_patterns", "")),
                weekend_patterns=str(getattr(ctx_raw, "weekend_patterns", "")),
                time_of_day_patterns=str(getattr(ctx_raw, "time_of_day_patterns", "")),
                session_type=str(getattr(ctx_raw, "session_type", "")),
            )

        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        # ── Cognition updates ──
        cognition_updates = []
        has_more_cognition_updates = False
        next_cognition_cursor = ""
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            raw_updates = [
                item
                for item in load_cognition_updates()
                if isinstance(item, dict) and str(item.get("summary", "")).strip()
            ]
            raw_updates.sort(key=lambda item: str(item.get("created_at", "")).strip(), reverse=True)
            raw_updates.sort(key=lambda item: bool(item.get("notified", False)))
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
            end = start + limit
            sliced_updates = raw_updates[start:end]
            has_more_cognition_updates = end < len(raw_updates)
            next_cognition_cursor = str(end) if has_more_cognition_updates else ""
            cognition_updates = [_normalize_cognition_update(item) for item in sliced_updates]

        # ── Speculative interests ──
        spec_items: list[SpeculativeInterestOut] = []
        avoidance_items: list[SpeculativeAvoidanceOut] = []
        runtime_config = getattr(ctx, "config", None) or config
        try:
            spec_state = load_speculative_state(runtime_config.data_path)

            # Filter status="active" only — confirmed/rejected items are
            # technically still in spec_state.active until force_tick rotates
            # them out, but the popup should not surface them: a user who
            # clicked 喜欢 has already given their answer and expects the
            # row to disappear, not to re-render with a "已确认" tag.
            active_specs = [item for item in spec_state.active if item.status == "active"]
            for item in active_specs[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                spec_items.append(
                    SpeculativeInterestOut(
                        domain=item.domain,
                        reason=item.reason,
                        confidence=item.confidence,
                        probe_mode=probe_mode,
                        challenge=challenge,
                        confirmation_count=item.confirmation_count,
                        confirmation_threshold=item.confirmation_threshold,
                        status=item.status,
                        specifics=[
                            SpeculativeSpecificOut(
                                name=s.name,
                                confirmation_count=s.confirmation_count,
                            )
                            for s in item.specifics
                            if s.name.strip()
                        ],
                    )
                )
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

        # ── Speculative avoidances ──
        try:
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active_avoidances = [
                item for item in avoidance_state.active if item.status == "active"
            ]
            avoidance_items = [
                SpeculativeAvoidanceOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
                    source_mode=item.source_mode,
                    source_signal=item.source_signal,
                    confirmation_count=item.confirmation_count,
                    confirmation_threshold=item.confirmation_threshold,
                    status=item.status,
                    specifics=[
                        SpeculativeSpecificOut(
                            name=s.name,
                            confirmation_count=s.confirmation_count,
                        )
                        for s in item.specifics
                        if s.name.strip()
                    ],
                )
                for item in active_avoidances[:6]
            ]
        except Exception:
            logger.debug("Failed to load avoidance state for profile summary")

        active_insights_out = [
            InsightHypothesisOut(
                hypothesis=str(getattr(ins, "hypothesis", "")),
                evidence=[str(e) for e in getattr(ins, "evidence", [])],
                confidence=float(getattr(ins, "confidence", 0.5)),
                validated=bool(getattr(ins, "validated", False)),
                created_at=str(getattr(ins, "created_at", "")),
            )
            for ins in getattr(profile, "active_insights", [])[:6]
            if str(getattr(ins, "hypothesis", "")).strip()
        ]

        recent_awareness_out = [
            AwarenessNoteOut(
                date=str(getattr(note, "date", "")),
                observation=str(getattr(note, "observation", "")),
                trend=str(getattr(note, "trend", "")),
                emotion_guess=str(getattr(note, "emotion_guess", "")),
            )
            for note in getattr(profile, "recent_awareness", [])[:8]
            if str(getattr(note, "observation", "")).strip()
        ]

        return ProfileSummaryResponse(
            initialized=True,
            personality_portrait=profile.personality_portrait,
            # Core
            core_traits=profile.core_traits[:6],
            deep_needs=profile.deep_needs[:5],
            mbti=mbti_out,
            # Values
            values=list(getattr(profile, "values", [])[:5]),
            motivational_drivers=list(getattr(profile, "motivational_drivers", [])[:4]),
            # Interest
            likes=likes_out,
            dislikes=dislikes_out,
            favorite_up_users=favorite_ups,
            # Role
            life_stage=str(getattr(profile, "life_stage", "")),
            current_phase=str(getattr(profile, "current_phase", "")),
            # Surface
            cognitive_style=list(getattr(profile, "cognitive_style", [])[:5]),
            style=style_out,
            context=ctx_out,
            exploration_openness=exploration_openness,
            # Cross-cutting
            speculative_interests=spec_items,
            speculative_avoidances=avoidance_items,
            recent_cognition_updates=cognition_updates,
            has_more_cognition_updates=has_more_cognition_updates,
            next_cognition_cursor=next_cognition_cursor,
            active_insights=active_insights_out,
            recent_awareness=recent_awareness_out,
        )

    @app.post("/api/events", response_model=EventIngestResponse)
    async def ingest_events(payload: BehaviorEventBatchIn) -> EventIngestResponse:
        from openbiliclaw.sources.event_format import build_event

        accepted = 0
        for item in payload.events:
            source_platform = (item.source_platform or "bilibili").strip() or "bilibili"
            # Coerce context to a string for downstream LLM consumers.
            # Pre-v0.3.22 this passed item.context through verbatim — when
            # the extension sent a dict (e.g. structured click context),
            # database serialization stored it as a JSON blob and prompt
            # builders surfaced "[object Object]"-like noise. build_event
            # fills in a natural-language fallback when context is empty
            # / non-string.
            raw_context = item.context
            if isinstance(raw_context, str):
                context_str = raw_context.strip()
            elif raw_context is None:
                context_str = ""
            else:
                # Dict / list / other — fold into metadata so it's
                # preserved without polluting the LLM-facing context.
                context_str = ""
            metadata = {
                **item.metadata,
                "timestamp": item.timestamp,
            }
            if not isinstance(raw_context, str) and raw_context:
                metadata.setdefault("raw_context", raw_context)
            # v0.3.x event-satisfaction: fold top-level dwell into
            # metadata so the storage classifier sees them in one place.
            # `setdefault` preserves an explicit metadata.watch_seconds
            # the extension might already have set inside metadata.
            if item.watch_seconds is not None:
                metadata.setdefault("watch_seconds", item.watch_seconds)
            if item.video_duration_seconds is not None:
                metadata.setdefault("video_duration_seconds", item.video_duration_seconds)
            event = build_event(
                event_type=item.type,
                source_platform=source_platform,
                title=item.title or "",
                url=item.url or "",
                author=str(metadata.get("author", "") or metadata.get("up_name", "") or ""),
                context=context_str,
                metadata=metadata,
            )
            await ctx.memory_manager.propagate_event(event)
            accepted += 1
        refresh_after_event_ingest = getattr(
            ctx.runtime_controller, "refresh_after_event_ingest", None
        )
        if callable(refresh_after_event_ingest):
            with suppress(Exception):
                await refresh_after_event_ingest()
        # Notify popup that the activity feed has new entries so it can
        # refresh its UI without polling. Throttled naturally to once per
        # ingest call (extension batches 10+ events into a single POST).
        if accepted > 0:
            event_hub = getattr(ctx.runtime_controller, "event_hub", None)
            publish = getattr(event_hub, "publish", None)
            if callable(publish):
                with suppress(Exception):
                    await publish(
                        {
                            "type": "activity.added",
                            "count": accepted,
                        }
                    )
        return EventIngestResponse(accepted=accepted)

    @app.get("/api/recommendations", response_model=RecommendationListResponse)
    async def recommendations() -> RecommendationListResponse:
        # Pull a 2x window so the per-franchise cap below still has 20
        # survivors to return after dropping over-represented IPs.
        # Without the wider pool, capping 原神 at 2 in a 20-row request
        # would leave gaps that other items further back in time would
        # have filled.
        rows = ctx.database.get_recommendations(limit=40)

        # Fresh-install bootstrap: ``recommendations`` table is the
        # write-only history of items we've ever served. On first popup
        # load nobody has called ``reshuffle`` / ``append`` / CLI
        # ``recommend`` yet, so the table is empty even if the discovery
        # pool already has 100+ scored candidates. Surface those by
        # bootstrapping a single ``serve()`` call right here — it writes
        # 10 fresh entries to the history table that the next ``rows =
        # get_recommendations`` re-read will pick up. Failure is fully
        # silent: any error returns the original empty list, leaving
        # the popup's "正在补货" state intact and giving the regular
        # refresh tick another chance.
        if not rows and ctx.recommendation_engine is not None and ctx.soul_engine is not None:
            with suppress(Exception):
                pool_count_fn = getattr(ctx.database, "count_pool_candidates", None)
                pool_count = int(pool_count_fn()) if callable(pool_count_fn) else 0
                if pool_count > 0:
                    profile = await ctx.soul_engine.get_profile()
                    await ctx.recommendation_engine.serve(profile, limit=10)
                    rows = ctx.database.get_recommendations(limit=40)
                    logger.info(
                        "GET /api/recommendations bootstrap: served from "
                        "empty history (pool_count=%d → wrote %d to history)",
                        pool_count,
                        len(rows),
                    )

        rows = _cap_by_franchise(rows, max_per_franchise=2)[:20]
        return RecommendationListResponse(
            items=[
                RecommendationOut(
                    id=int(row["id"]),
                    bvid=str(row.get("bvid", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    expression=str(row.get("expression", "")),
                    topic_label=str(row.get("topic", "")),
                    presented=bool(row.get("presented", 0)),
                    feedback_type=str(row.get("feedback_type", "") or ""),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    content_url=str(row.get("content_url", "") or ""),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                )
                for row in rows
            ]
        )

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed(
        limit: int = 10,
        before: str = "",
    ) -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        runtime_status: dict[str, object] = {}
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            runtime_status = dict(get_runtime_status())
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            runtime_status.update(get_account_sync_status())

        cognition_updates: list[dict[str, object]] = []
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            cognition_updates = [
                item for item in load_cognition_updates() if isinstance(item, dict)
            ]

        builder = ActivityFeedBuilder(database=ctx.database)
        payload = builder.build(
            runtime_status=runtime_status,
            cognition_updates=cognition_updates,
            limit=limit,
            before=before,
        )
        payload_items = payload.get("items", [])
        item_dicts = payload_items if isinstance(payload_items, list) else []
        return ActivityFeedResponse(
            live_summary=str(payload.get("live_summary", "")),
            headline=str(payload.get("headline", "")),
            items=[
                ActivityFeedItemOut(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    created_at=str(item.get("created_at", "")),
                    tone=str(item.get("tone", "info")),
                )
                for item in item_dicts
                if isinstance(item, dict)
            ],
            has_more=bool(payload.get("has_more", False)),
            next_cursor=str(payload.get("next_cursor", "")),
        )

    async def _classify_new_pool_items() -> None:
        """Run LLM classification on pool items that lack content features.

        Called after XHS (or any non-bilibili) content is ingested.  This
        ensures every item gets ``style_key``, ``topic_group``, and
        ``relevance_score`` before it can be recommended — same treatment
        bilibili content receives during discovery.

        Silent skip when soul profile hasn't been built yet (init's first
        ~7 minutes). Otherwise events ingested before profile-ready would
        log ERROR-level traces for every batch — the legitimate retry is
        the next-tick + the profile-ready hook in ``SoulEngine``.
        """
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return
        if not ctx.soul_engine.is_profile_ready():
            logger.debug("Background pool classification skipped: soul profile not ready")
            return
        try:
            profile = await ctx.soul_engine.get_profile()
            await ctx.recommendation_engine.classify_pool_backlog(
                profile=profile,
                limit=30,
            )
        except Exception:
            logger.exception("Background pool classification failed")

    async def _trigger_replenishment_if_needed() -> None:
        """Fire a background Discovery refresh when the pool runs low."""
        curator = getattr(ctx.recommendation_engine, "_curator", None)
        if curator is None or not hasattr(curator, "needs_replenishment"):
            return
        if not curator.needs_replenishment():
            return
        trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if callable(trigger):
            logger.info("Pool low — triggering automatic replenishment")
            asyncio.create_task(trigger())

    @app.post("/api/recommendations/reshuffle", response_model=RecommendationReshuffleResponse)
    async def reshuffle_recommendations() -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.reshuffle_recommendations(profile=profile, limit=10)
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/append", response_model=RecommendationReshuffleResponse)
    async def append_recommendations(
        payload: RecommendationAppendIn,
    ) -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.append_recommendations(
            profile=profile,
            excluded_bvids=payload.excluded_bvids,
            limit=10,
        )
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/refresh", response_model=RecommendationRefreshResponse)
    async def refresh_recommendations() -> RecommendationRefreshResponse:
        trigger_manual_refresh = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
        if not callable(trigger_manual_refresh):
            return RecommendationRefreshResponse(
                ok=True,
                accepted=False,
                state="idle",
                reason="runtime_unavailable",
            )

        result = await trigger_manual_refresh()
        return RecommendationRefreshResponse(
            ok=True,
            accepted=bool(result.get("accepted", False)),
            state=str(result.get("state", "idle")),
            reason=str(result.get("reason", "")),
        )

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if not callable(get_runtime_status):
            return RuntimeStatusResponse(
                initialized=False,
                recommendation_count=0,
                pending_signal_events=0,
                unread_count=0,
            )
        payload = dict(get_runtime_status())
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        get_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_update_status):
            payload.update(get_update_status())
        return RuntimeStatusResponse(**payload)

    @app.get("/api/notifications/pending", response_model=PendingNotificationResponse)
    async def pending_notification() -> PendingNotificationResponse:
        get_pending_notification = getattr(ctx.runtime_controller, "get_pending_notification", None)
        item = get_pending_notification() if callable(get_pending_notification) else None
        if item is None:
            get_notification_candidate = getattr(ctx.database, "get_notification_candidate", None)
            if callable(get_notification_candidate):
                candidate = get_notification_candidate(min_confidence=0.82)
                if candidate is not None:
                    item = {
                        "recommendation_id": int(candidate["id"]),
                        "bvid": str(candidate.get("bvid", "")),
                        "title": str(candidate.get("title", "")),
                        "reason": str(candidate.get("expression", "")),
                    }
        if item is None:
            return PendingNotificationResponse(item=None)
        return PendingNotificationResponse(item=PendingNotificationOut(**item))

    @app.get(
        "/api/cognition-updates/pending",
        response_model=PendingCognitionUpdateResponse,
    )
    async def pending_cognition_update() -> PendingCognitionUpdateResponse:
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if not callable(load_cognition_updates):
            return PendingCognitionUpdateResponse(item=None)
        updates = [
            item
            for item in load_cognition_updates()
            if isinstance(item, dict) and not bool(item.get("notified", False))
        ]
        if not updates:
            return PendingCognitionUpdateResponse(item=None)
        latest = updates[-1]
        return PendingCognitionUpdateResponse(
            item=PendingCognitionUpdateOut(
                id=str(latest.get("id", "")),
                kind=str(latest.get("kind", "")),
                summary=str(latest.get("summary", "")),
            )
        )

    @app.post(
        "/api/cognition-updates/seen",
        response_model=CognitionUpdateSeenResponse,
    )
    async def cognition_update_seen(
        payload: CognitionUpdateSeenIn,
    ) -> CognitionUpdateSeenResponse:
        update_id = payload.id.strip()
        if not update_id:
            raise HTTPException(status_code=422, detail="Cognition update id is required.")
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        save_cognition_updates = getattr(ctx.memory_manager, "save_cognition_updates", None)
        if not callable(load_cognition_updates) or not callable(save_cognition_updates):
            raise HTTPException(status_code=500, detail="Cognition update storage unavailable.")
        updates = load_cognition_updates()
        found = False
        for item in updates:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() != update_id:
                continue
            item["notified"] = True
            found = True
            break
        if not found:
            raise HTTPException(status_code=404, detail="Cognition update not found.")
        save_cognition_updates(updates)
        return CognitionUpdateSeenResponse(ok=True, id=update_id)

    @app.post("/api/delight/trigger")
    async def trigger_delight(payload: dict[str, Any] | None = None) -> Any:
        """Manually push N distinct delight candidates via WebSocket.

        Body: ``{"count": 3}``. For testing the queue UI: pulls the top N
        un-notified candidates from the pool and publishes a
        ``delight.candidate`` event for each one in succession, **without**
        marking any as notified. That way you can re-trigger the same
        batch repeatedly while iterating on the popup-side queue, and
        the popup's own ``/api/delight/pending`` calls still see them
        afterwards.

        Cooldown is cleared at the end so the proactive-push loop
        isn't gated.
        """
        count = 1
        if isinstance(payload, dict):
            try:
                count = max(1, min(20, int(payload.get("count", 1))))
            except (ValueError, TypeError):
                count = 1

        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        candidates = ctx.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=count,
        )
        pushed: list[str] = []
        for row in candidates:
            payload_event = {
                "type": "delight.candidate",
                "phase": "ready",
                "message": "发现了一条你可能会意外喜欢的内容",
                "bvid": str(row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
            }
            with suppress(Exception):
                await ctx.event_hub.publish(payload_event)
            pushed.append(str(payload_event["bvid"]))

        # Clear cooldown so the regular push loop isn't gated after manual
        # trigger.
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is not None:
            state = memory_manager.load_discovery_runtime_state()
            state.pop("last_delight_notification_at", None)
            memory_manager.save_discovery_runtime_state(state)
        return {"ok": True, "pushed_count": len(pushed), "bvids": pushed}

    @app.get("/api/delight/pending", response_model=PendingDelightResponse)
    async def pending_delight() -> PendingDelightResponse:
        get_pending_delight = getattr(ctx.runtime_controller, "get_pending_delight", None)
        item = get_pending_delight() if callable(get_pending_delight) else None
        if item is None:
            return PendingDelightResponse(item=None)
        return PendingDelightResponse(item=PendingDelightOut(**item))

    @app.get("/api/delight/pending-batch")
    async def pending_delight_batch(limit: int = 20) -> dict[str, Any]:
        """Return up to ``limit`` un-notified delight candidates.

        Unlike ``/api/delight/pending`` this ignores the 4-hour
        notification cooldown — it's intended for the popup to
        re-hydrate the full queue on init, not for active push gating.
        Honors ``disliked_topics`` substring filter same as the singular
        endpoint.
        """
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        rows = ctx.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=max(1, min(50, int(limit))),
        )
        # Reuse the same disliked-topic filter as get_pending_delight by
        # going through the runtime controller's loader if possible.
        controller = ctx.runtime_controller
        load_phrases = getattr(controller, "_load_disliked_topic_phrases", None)
        disliked_phrases = load_phrases() if callable(load_phrases) else []

        def passes_filter(row: dict[str, Any]) -> bool:
            haystack = f"{str(row.get('title', '')).lower()} {str(row.get('tags', '')).lower()}"
            return not any(p and p in haystack for p in disliked_phrases)

        items = [
            {
                "bvid": str(row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
            }
            for row in rows
            if passes_filter(row)
        ]
        return {"items": items}

    @app.post("/api/delight/sent", response_model=DelightAckResponse)
    async def mark_delight_sent(payload: DelightAckIn) -> DelightAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Delight bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_delight_notified(bvid)
        return DelightAckResponse(ok=True, bvid=bvid)

    @app.post("/api/delight/respond")
    async def respond_to_delight(payload: dict[str, Any]) -> Any:
        """User responds to a delight (surprise) recommendation.

        Body:
        ``{ "bvid": "...", "title": "...", "response": "view"|"dislike"|"chat",
        "message": "..." }``
        """
        from fastapi.responses import JSONResponse

        bvid = str(payload.get("bvid", "")).strip()
        title = str(payload.get("title", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        if response_type not in {"view", "like", "dislike", "chat"}:
            raise HTTPException(
                status_code=422,
                detail="response must be view, like, dislike, or chat",
            )

        if response_type == "view":
            return JSONResponse(content={"ok": True, "action": "viewed", "bvid": bvid})

        if response_type == "like":
            # User marks this delight as liked WITHOUT having opened the
            # video. Treat as a strong positive feedback signal: boost
            # the row's relevance score and record a cognition update so
            # downstream scoring + UI both reflect the preference.
            try:
                ctx.database._execute_write(
                    "UPDATE content_cache SET feedback_type='like', "
                    "feedback_at=CURRENT_TIMESTAMP, "
                    "relevance_score=MIN(1.0, COALESCE(relevance_score, 0.5) + 0.15) "
                    "WHERE bvid = ?",
                    (bvid,),
                )
            except Exception:
                logger.debug("Failed to record delight like for %s", bvid)
            label = title or bvid
            _record_probe_cognition(
                f"你喜欢惊喜推荐「{label}」，会多挖类似的。",
                bvid,
                "delight_like",
            )
            await _publish_probe_event(
                "delight.liked",
                f"好，「{label}」这类多来点。",
                bvid,
            )
            _record_exploration_buffer_event(
                domain=label,
                source_event="card_more_like",
                evidence_id=bvid,
            )
            return JSONResponse(content={"ok": True, "action": "liked", "bvid": bvid})

        if response_type == "dislike":
            try:
                ctx.database._execute_write(
                    "UPDATE content_cache SET pool_status = 'purged_by_dislike' "
                    "WHERE bvid = ? AND COALESCE(pool_status, 'fresh') = 'fresh'",
                    (bvid,),
                )
            except Exception:
                logger.debug("Failed to purge delight bvid %s", bvid)
            label = title or bvid
            _record_probe_cognition(
                f"你对惊喜推荐「{label}」不感兴趣。",
                bvid,
                "delight_dislike",
            )
            await _publish_probe_event(
                "delight.disliked",
                f"好，「{label}」这类先不推了。",
                bvid,
            )
            _record_exploration_buffer_event(
                domain=label,
                source_event="negative",
                evidence_id=bvid,
            )
            return JSONResponse(content={"ok": True, "action": "disliked", "bvid": bvid})

        # Chat
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"聊聊你为什么觉得「{title or bvid}」我会喜欢"
        contextual_message = f"[关于惊喜推荐「{title or bvid}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return JSONResponse(
                content={"ok": False, "action": "chat", "bvid": bvid, "reply": "对话引擎暂不可用。"}
            )
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(ctx.dialogue.respond(contextual_message), timeout=30)
        except TimeoutError:
            return JSONResponse(
                content={
                    "ok": False,
                    "action": "chat",
                    "bvid": bvid,
                    "reply": "后台正忙，等一下再聊。",
                }
            )
        except Exception:
            logger.exception("Dialogue failed for delight chat: %s", bvid)
            return JSONResponse(
                content={
                    "ok": False,
                    "action": "chat",
                    "bvid": bvid,
                    "reply": "聊天出了点问题，稍后再试。",
                }
            )
        finally:
            if concurrency is not None:
                concurrency.chat_active = False
        label = title or bvid
        _record_probe_cognition(
            f"关于惊喜推荐「{label}」你说：{raw_message}",
            bvid,
            "delight_chat",
            detail=f"你的反馈：{raw_message}\n阿b的回复：{reply}",
        )
        await _publish_probe_event("delight.chat", f"关于「{label}」你说：{raw_message}", bvid)
        return JSONResponse(content={"ok": True, "action": "chat", "bvid": bvid, "reply": reply})

    @app.post("/api/notifications/sent", response_model=NotificationAckResponse)
    async def mark_notification_sent(payload: NotificationAckIn) -> NotificationAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Notification bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_notification_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_notification_sent(bvid)
        return NotificationAckResponse(ok=True, bvid=bvid)

    @app.post("/api/chat")
    async def chat(payload: ChatIn) -> Any:
        from fastapi.responses import JSONResponse

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            # Bumped from 30s to 120s — deepseek with reasoning_effort=max
            # routinely takes 60-90s for one dialogue turn, so a 30s budget
            # truncated essentially every reply. Extension's AbortController
            # is sized to be generous enough to cover this end-to-end.
            reply = await asyncio.wait_for(ctx.dialogue.respond(message), timeout=120)
        except TimeoutError:
            reply = "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Chat dialogue failed")
            reply = "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False
        return JSONResponse(content={"reply": reply})

    def _record_probe_cognition(
        summary: str,
        domain: str,
        action: str,
        *,
        source: str = "interest_probe",
        detail: str = "",
    ) -> None:
        """Write a cognition update so probe feedback shows in '阿b最近记住了什么'."""
        from datetime import datetime

        try:
            updates = ctx.memory_manager.load_cognition_updates()
            updates.append(
                {
                    "summary": summary,
                    "detail": detail or f"兴趣探针反馈：{action} — {domain}",
                    "created_at": datetime.now().isoformat(),
                    "source": source,
                    "tone": "success" if action == "confirmed" else "info",
                }
            )
            ctx.memory_manager.save_cognition_updates(updates)
        except Exception:
            logger.exception("Failed to record probe cognition update")

    async def _publish_probe_event(event_type: str, message: str, domain: str) -> None:
        """Push a probe result event via WebSocket."""
        event_hub = getattr(ctx.runtime_controller, "event_hub", None)
        publish = getattr(event_hub, "publish", None)
        if callable(publish):
            await publish(
                {
                    "type": event_type,
                    "phase": "ready",
                    "message": message,
                    "domain": domain,
                }
            )

    def _probe_metadata_from_active_item(
        get_active: Any,
        domain: str,
        *,
        include_category: bool = False,
        include_source_mode: bool = False,
    ) -> dict[str, object]:
        """Read active probe metadata before confirm/reject mutates state."""
        from openbiliclaw.soul.speculator import build_probe_axis

        if not callable(get_active):
            return {"domain": domain}
        try:
            active_items = list(get_active())
        except Exception:
            logger.debug("Failed to read active probe metadata", exc_info=True)
            return {"domain": domain}

        for item in active_items:
            spec_domain = str(getattr(item, "domain", "")).strip()
            if spec_domain.lower() != domain.lower():
                continue
            specifics = [
                str(getattr(specific, "name", "")).strip()
                for specific in getattr(item, "specifics", [])
                if str(getattr(specific, "name", "")).strip()
            ]
            axis = build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
            metadata: dict[str, object] = {
                "domain": spec_domain or domain,
                "reason": str(getattr(item, "reason", "")).strip(),
            }
            if include_category:
                metadata["category"] = str(getattr(item, "category", "")).strip()
            if include_source_mode:
                source_mode = str(getattr(item, "source_mode", "")).strip()
                source_signal = str(getattr(item, "source_signal", "")).strip()
                if source_mode:
                    metadata["source_mode"] = source_mode
                if source_signal:
                    metadata["source_signal"] = source_signal
            if axis:
                metadata["axis"] = axis
            if specifics:
                metadata["specifics"] = specifics
            return metadata
        return {"domain": domain}

    def _probe_metadata_from_active_speculation(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active interest probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_speculations", None),
            domain,
            include_category=True,
        )

    def _probe_metadata_from_active_avoidance(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active avoidance probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_avoidances", None),
            domain,
            include_source_mode=True,
        )

    def _record_probe_feedback_history(
        domain: str,
        response: str,
        *,
        speculator: Any,
        message: str = "",
        classification: str = "",
        classifier: str = "",
        resulting_action: str = "",
        state_key: str = "probe_feedback_history",
        metadata_fn: Any | None = None,
    ) -> None:
        """Persist explicit user feedback for future probe novelty checks."""
        from openbiliclaw.soul.speculator import append_probe_feedback_history

        memory_manager = getattr(ctx, "memory_manager", None)
        if memory_manager is None:
            memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        if not callable(load_state) or not callable(save_state):
            return
        try:
            state = load_state()
            if metadata_fn is not None:
                entry = metadata_fn(domain)
            else:
                entry = _probe_metadata_from_active_speculation(speculator, domain)
            entry["response"] = response
            if message:
                entry["message"] = message
                entry["raw_text_excerpt"] = message[:240]
            if classification:
                entry["classification"] = classification
            if classifier:
                entry["classifier"] = classifier
            if resulting_action:
                entry["resulting_action"] = resulting_action
            state[state_key] = append_probe_feedback_history(
                state.get(state_key, []),
                entry,
            )
            save_state(state)
        except Exception:
            logger.exception("Failed to record probe feedback history")

    async def _judge_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """Judge the user's probe chat as a 4-way confirmation signal."""
        sentiment, _classifier = await _classify_probe_sentiment(
            user_message,
            ai_reply,
            domain,
        )
        return sentiment

    async def _classify_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> tuple[str, str]:
        """Return ``(classification, classifier)`` for probe chat feedback."""
        llm_result = await _llm_judge_sentiment(user_message, ai_reply, domain)
        if llm_result in {"strong_positive", "weak_positive", "negative"}:
            return llm_result, "llm"
        keyword_result = _keyword_judge_sentiment(user_message)
        if keyword_result != "neutral":
            return keyword_result, "keyword"
        return "neutral", "neutral_default"

    def _keyword_judge_sentiment(user_message: str) -> str:
        """Fallback keyword-based sentiment detection."""
        msg = user_message.lower()
        negative_terms = {
            "不喜欢",
            "不感兴趣",
            "不是这个意思",
            "别推",
            "没兴趣",
            "不想看",
        }
        strong_positive_terms = {
            "以后多推",
            "这就是我想看的",
            "我就喜欢",
            "加入我的画像",
        }
        weak_positive_terms = {
            "有点意思",
            "可以看看",
            "偶尔看看",
            "还行",
            "先试试",
        }
        if any(kw in msg for kw in negative_terms):
            return "negative"
        if any(kw in msg for kw in strong_positive_terms):
            return "strong_positive"
        if any(kw in msg for kw in weak_positive_terms):
            return "weak_positive"
        return "neutral"

    async def _llm_judge_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """LLM-based sentiment judgment for probe chat."""
        if ctx.recommendation_engine is None:
            return "neutral"
        llm = getattr(ctx.recommendation_engine, "_llm", None)
        if llm is None:
            return "neutral"
        try:
            response = await asyncio.wait_for(
                llm.complete_with_core_memory(
                    system_instruction=(
                        "任务：判断用户对一个兴趣方向的态度。\n\n"
                        "规则：\n"
                        "1. 只输出一个英文标签："
                        "strong_positive、weak_positive、neutral 或 negative\n"
                        "2. 不要输出任何其他内容\n\n"
                        "判断标准：\n"
                        "- strong_positive = 用户明确要加入画像、以后多推、这就是想看的\n"
                        "- weak_positive = 用户表达轻微兴趣、可以看看、偶尔看看，但未直接确认\n"
                        "- negative = 用户表达了不喜欢、不感兴趣、太难、太无聊\n"
                        "- neutral = 态度不明确\n"
                    ),
                    user_input=f"方向：{domain}\n用户：{user_message}",
                    max_tokens=8,
                    temperature=0.0,
                    json_mode=False,
                    caller="api.sentiment",
                    bypass_semaphore=True,
                ),
                timeout=15,
            )
            raw = str(getattr(response, "content", "")).strip().lower()
            # Extract the first recognizable word
            for word in raw.split():
                cleaned = word.strip("\"'.,:;!?")
                if cleaned in (
                    "strong_positive",
                    "weak_positive",
                    "negative",
                    "neutral",
                ):
                    logger.info("Sentiment LLM for '%s': %s (raw=%r)", domain, cleaned, raw)
                    return cleaned
            logger.info(
                "Sentiment LLM for '%s': unrecognized (raw=%r), trying keywords", domain, raw
            )
            return "neutral"
        except Exception:
            logger.info("Sentiment LLM for '%s' failed, trying keywords", domain)
            return "neutral"

    def _confirm_speculation_with_source(
        speculator: Any,
        domain: str,
        *,
        confirmation_source: str,
    ) -> bool:
        confirm = getattr(speculator, "user_confirm_speculation", None)
        if not callable(confirm):
            return False
        try:
            return bool(confirm(domain, confirmation_source=confirmation_source))
        except TypeError:
            return bool(confirm(domain))

    def _promote_exploration_buffer_entries(
        promoted: list[dict[str, object]],
    ) -> None:
        if not promoted:
            return
        from openbiliclaw.soul.interest_writeback import merge_confirmed_interest
        from openbiliclaw.soul.profile import OnionProfile

        memory_manager = getattr(ctx, "memory_manager", None)
        get_layer = getattr(memory_manager, "get_layer", None)
        if not callable(get_layer):
            return
        try:
            soul_layer = get_layer("soul")
            raw_profile = getattr(soul_layer, "data", {})
            profile = (
                OnionProfile.from_dict(raw_profile)
                if isinstance(raw_profile, dict) and raw_profile
                else OnionProfile()
            )
            changed = False
            for entry in promoted:
                raw_specifics = entry.get("specifics", [])
                specifics = (
                    [str(item) for item in raw_specifics if str(item).strip()]
                    if isinstance(raw_specifics, list)
                    else []
                )
                changed = (
                    merge_confirmed_interest(
                        profile,
                        domain=str(entry.get("domain", "")),
                        specifics=specifics,
                        source=str(entry.get("confirmation_source", "buffer_promoted")),
                        first_seen=str(entry.get("first_seen", "")),
                        last_seen=str(entry.get("last_seen", "")),
                    )
                    or changed
                )
            if not changed:
                return
            if isinstance(raw_profile, dict):
                raw_profile.clear()
                raw_profile.update(profile.to_dict())
            save = getattr(soul_layer, "save", None)
            if callable(save):
                save()
            sync_profile_files = getattr(memory_manager, "sync_profile_files", None)
            if callable(sync_profile_files):
                sync_profile_files(profile)
        except Exception:
            logger.exception("Failed to promote exploration buffer entries")

    def _record_exploration_buffer_event(
        *,
        domain: str,
        source_event: str,
        specifics: list[str] | None = None,
        evidence_id: str = "",
    ) -> None:
        from datetime import UTC, datetime

        from openbiliclaw.soul.exploration_buffer import (
            pop_promotable_buffer_entries,
            record_buffer_event,
        )

        clean_domain = domain.strip()
        if not clean_domain:
            return
        memory_manager = getattr(ctx, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        if not callable(load_state) or not callable(save_state):
            return
        try:
            state = load_state()
            if not isinstance(state, dict):
                state = {}
            now = datetime.now(UTC)
            buffer_state = record_buffer_event(
                state.get("short_term_exploration_buffer", {}),
                domain=clean_domain,
                source_event=source_event,
                specifics=specifics or [],
                evidence_id=evidence_id,
                now=now,
            )
            promoted, buffer_state = pop_promotable_buffer_entries(buffer_state, now=now)
            state["short_term_exploration_buffer"] = buffer_state
            save_state(state)
            _promote_exploration_buffer_entries(promoted)
        except Exception:
            logger.exception("Failed to record exploration buffer event")

    def _recommendation_buffer_domain(row: dict[str, object]) -> tuple[str, list[str]]:
        title = str(row.get("title", "")).strip()
        domain = (
            str(row.get("topic_group", "")).strip()
            or str(row.get("topic_label", "")).strip()
            or str(row.get("topic", "")).strip()
            or str(row.get("topic_key", "")).strip()
            or title
        )
        specifics = [title] if title and title != domain else []
        return domain, specifics

    def _contextual_chat_message(turn: ChatTurnOut) -> str:
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id or "这条惊喜推荐"
            return f"[关于惊喜推荐「{label}」的反馈] {turn.message}"
        if turn.scope == "probe":
            label = turn.subject_title or turn.subject_id or "这个方向"
            return f"[关于猜测兴趣「{label}」的反馈] {turn.message}"
        if turn.scope == "avoidance_probe":
            label = turn.subject_title or turn.subject_id or "这个避雷方向"
            return f"[关于避雷方向「{label}」的反馈] {turn.message}"
        return turn.message

    async def _generate_durable_chat_reply(turn: ChatTurnOut) -> str:
        if ctx.dialogue is None:
            return "对话引擎暂不可用。"

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            async with chat_turn_lock:
                reply = await asyncio.wait_for(
                    ctx.dialogue.respond(_contextual_chat_message(turn)),
                    timeout=120,
                )
                reply = str(reply)
        except TimeoutError:
            return "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Durable chat turn failed: %s", turn.turn_id)
            return "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id
            _record_probe_cognition(
                f"关于惊喜推荐「{label}」你说：{turn.message}",
                turn.subject_id or label,
                "delight_chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event(
                "delight.chat",
                f"关于「{label}」你说：{turn.message}",
                turn.subject_id or label,
            )
        elif turn.scope == "probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_speculator", None)
            chat_response = "chat_neutral"
            resulting_action = "none"
            if sentiment == "negative":
                chat_response = "chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.user_reject_speculation(domain, cooldown_days=14)
                summary = f"你对「{domain}」的反馈偏负面（{turn.message}），已暂时搁置 14 天。"
            elif sentiment == "strong_positive":
                chat_response = "chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        _confirm_speculation_with_source(
                            speculator,
                            domain,
                            confirmation_source="chat_confirmed",
                        )
                summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
            elif sentiment == "weak_positive":
                chat_response = "weak_positive"
                resulting_action = "weak_positive_deferred"
                _record_exploration_buffer_event(
                    domain=domain,
                    source_event="weak_positive_chat",
                )
                summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
            else:
                summary = f"关于「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("interest.chat", summary, domain)
        elif turn.scope == "avoidance_probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
            if sentiment == "negative":
                chat_response = "avoidance_chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.observe(
                            [
                                {
                                    "event_type": "dislike",
                                    "title": domain,
                                    "metadata": {
                                        "feedback_type": "dislike",
                                        "user_message": turn.message,
                                        "source": "avoidance_probe_chat",
                                    },
                                }
                            ]
                        )
                summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
            elif sentiment in {"strong_positive", "weak_positive"}:
                chat_response = "avoidance_chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    reject_fn = getattr(speculator, "user_reject_avoidance", None)
                    if callable(reject_fn):
                        with suppress(Exception):
                            reject_fn(domain, cooldown_days=14)
                summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
            else:
                chat_response = "avoidance_chat_neutral"
                resulting_action = "none"
                summary = f"关于避雷方向「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                    state_key="avoidance_probe_feedback_history",
                    metadata_fn=lambda item_domain: _probe_metadata_from_active_avoidance(
                        speculator,
                        item_domain,
                    ),
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="avoidance_probe",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("avoidance.chat", summary, domain)

        return reply

    async def _complete_durable_chat_turn(turn_id: str) -> None:
        if turn_id in running_chat_turn_tasks:
            return
        running_chat_turn_tasks.add(turn_id)
        try:
            row = _get_chat_turn_row(turn_id)
            if row is None:
                return
            turn = _normalize_chat_turn(row)
            if turn.status != "pending":
                return
            reply = await _generate_durable_chat_reply(turn)
            _complete_chat_turn_row(turn_id, reply=reply)
        except Exception as exc:
            logger.exception("Failed to complete durable chat turn %s", turn_id)
            _fail_chat_turn_row(turn_id, error=str(exc), reply="聊天出了点问题，稍后再试。")
        finally:
            running_chat_turn_tasks.discard(turn_id)

    @app.post("/api/chat/turns", response_model=ChatTurnOut)
    async def start_chat_turn(payload: ChatTurnIn) -> ChatTurnOut:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        raw_turn_id = payload.turn_id.strip()
        turn_id = raw_turn_id or f"turn-{uuid.uuid4().hex}"
        existing = _get_chat_turn_row(turn_id)
        if existing is not None:
            turn = _normalize_chat_turn(existing)
            if turn.status == "pending":
                asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
            return turn
        row = _create_chat_turn_row(payload, turn_id=turn_id)
        asyncio.create_task(_complete_durable_chat_turn(turn_id))
        return _normalize_chat_turn(row)

    @app.get("/api/chat/turns", response_model=ChatTurnListResponse)
    async def list_chat_turns(
        session: str = "popup",
        scope: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ChatTurnListResponse:
        normalized_scope = _normalize_chat_scope(scope) if scope else ""
        rows = _list_chat_turn_rows(
            session=session.strip() or "popup",
            scope=normalized_scope,
            limit=limit,
        )
        return ChatTurnListResponse(items=[_normalize_chat_turn(row) for row in rows])

    @app.get("/api/chat/turns/{turn_id}", response_model=ChatTurnOut)
    async def get_chat_turn(turn_id: str) -> ChatTurnOut:
        row = _get_chat_turn_row(turn_id.strip())
        if row is None:
            raise HTTPException(status_code=404, detail="Chat turn not found.")
        turn = _normalize_chat_turn(row)
        if turn.status == "pending":
            asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
        return turn

    @app.post("/api/interest-probes/trigger")
    async def trigger_interest_probe() -> dict[str, Any]:
        """Manually trigger an interest probe push via WebSocket.

        Useful when ``run_forever`` is blocked by a long refresh cycle
        and the probe wouldn't fire on its own for several minutes.
        """
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_interest_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Probe publisher not available")
        await publish()
        return {"ok": True, "action": "probe_triggered"}

    @app.get("/api/interest-probes/pending")
    async def pending_interest_probes() -> dict[str, Any]:
        """Return active speculative interests that the user hasn't responded to.

        The mobile web UI polls this on page load / bell-click so probes
        survive page refreshes (unlike WebSocket-only delivery).
        """
        try:
            from openbiliclaw.soul.speculator import load_speculative_state

            spec_state = load_speculative_state(ctx.config.data_path)
            active = [item for item in spec_state.active if item.status == "active"]
            items = []
            for item in active[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                items.append(
                    {
                        "domain": item.domain,
                        "reason": item.reason,
                        "confidence": item.confidence,
                        "status": item.status,
                        "probe_mode": probe_mode,
                        "challenge": challenge,
                    }
                )
            return {"items": items}
        except Exception:
            return {"items": []}

    @app.post("/api/interest-probes/respond")
    async def respond_to_interest_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated interest probe.

        Body: { "domain": "...", "response": "confirm" | "reject" | "chat", "message": "..." }

        - confirm: Force-promote the speculation
        - reject: Move to cooldown (30 days)
        - chat: Forward to dialogue engine with probe context, return reply
        """
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Speculator not available")

        if response_type == "confirm":
            requested_source = str(payload.get("confirmation_source", "")).strip()
            surface = str(payload.get("surface", "")).strip().lower()
            confirmation_source = (
                requested_source
                or ("profile_confirmed" if surface == "profile" else "probe_confirmed")
            )
            _record_probe_feedback_history(
                domain,
                "confirm",
                speculator=speculator,
                resulting_action="confirmed",
            )
            ok = _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source=confirmation_source,
            )
            if ok:
                # Force_tick generates 5 new probes via LLM (~30-60s).
                # Running it inline blocks the response past the
                # browser fetch timeout (35s) — the user gives up,
                # AbortError fires, and the next click hits a stale UI.
                # Schedule it as a background task so the API returns
                # immediately; the new probes will be visible on the
                # next profile-summary refresh.
                tick_fn = getattr(speculator, "force_tick", None)
                if callable(tick_fn):

                    async def _bg_force_tick() -> None:
                        try:
                            profile = await ctx.soul_engine.get_profile()
                            feedback_history: object = []
                            load_runtime_state = getattr(
                                ctx.memory_manager,
                                "load_discovery_runtime_state",
                                None,
                            )
                            if callable(load_runtime_state):
                                runtime_state = load_runtime_state()
                                if isinstance(runtime_state, dict):
                                    feedback_history = runtime_state.get(
                                        "probe_feedback_history",
                                        [],
                                    )
                            if asyncio.iscoroutinefunction(tick_fn):
                                try:
                                    await tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                    )
                                except TypeError:
                                    await tick_fn(profile)
                            else:
                                try:
                                    tick_fn(profile, feedback_history=feedback_history)
                                except TypeError:
                                    tick_fn(profile)
                        except Exception:
                            logger.exception("Background force_tick after confirm failed")

                    asyncio.create_task(_bg_force_tick())
                # Record cognition update so it shows in "阿b最近记住了什么"
                _record_probe_cognition(
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                    "confirmed",
                )
                # Notify frontend via WebSocket
                await _publish_probe_event(
                    "interest.confirmed",
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                )
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            _record_probe_feedback_history(
                domain,
                "reject",
                speculator=speculator,
            )
            ok = speculator.user_reject_speculation(domain)
            if ok:
                _record_probe_cognition(
                    f"你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                    "rejected",
                )
                await _publish_probe_event(
                    "interest.rejected",
                    f"已记录：你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        # Chat: forward to dialogue with domain context injected
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能感兴趣的「{domain}」这个方向"
        # Inject domain context so dialogue engine + learn_from_dialogue
        # understand this is feedback on a specific speculated interest
        contextual_message = f"[关于猜测兴趣「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            # Judge sentiment while discovery is still paused
            sentiment, classifier = await _classify_probe_sentiment(raw_message, reply, domain)
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        chat_response = "chat_neutral"
        resulting_action = "none"
        if sentiment == "negative":
            chat_response = "chat_rejected"
            resulting_action = "rejected"
            speculator.user_reject_speculation(domain, cooldown_days=14)
            summary = f"你对「{domain}」的反馈偏负面（{raw_message}），已暂时搁置 14 天。"
        elif sentiment == "strong_positive":
            chat_response = "chat_confirmed"
            resulting_action = "confirmed"
            _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source="chat_confirmed",
            )
            summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
        elif sentiment == "weak_positive":
            chat_response = "weak_positive"
            resulting_action = "weak_positive_deferred"
            _record_exploration_buffer_event(
                domain=domain,
                source_event="weak_positive_chat",
            )
            summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
        else:
            summary = f"关于「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
        )

        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(summary, domain, "chat", detail=detail)
        await _publish_probe_event(
            "interest.chat",
            summary,
            domain,
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    @app.post("/api/avoidance-probes/trigger")
    async def trigger_avoidance_probe() -> dict[str, Any]:
        """Manually trigger an avoidance probe push via WebSocket."""
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_avoidance_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Avoidance probe publisher not available")
        await publish()
        return {"ok": True, "action": "avoidance_probe_triggered"}

    @app.get("/api/avoidance-probes/pending")
    async def pending_avoidance_probes() -> dict[str, Any]:
        """Return active speculative avoidances awaiting user response."""
        try:
            from openbiliclaw.soul.avoidance_speculator import load_avoidance_state

            runtime_config = getattr(ctx, "config", None) or config
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active = [item for item in avoidance_state.active if item.status == "active"]
            items = [
                {
                    "domain": item.domain,
                    "reason": item.reason,
                    "confidence": item.confidence,
                    "source_mode": item.source_mode,
                    "source_signal": item.source_signal,
                    "status": item.status,
                    "specifics": [
                        {"name": specific.name, "confirmation_count": specific.confirmation_count}
                        for specific in item.specifics
                        if specific.name.strip()
                    ],
                }
                for item in active[:6]
            ]
            return {"items": items}
        except Exception:
            logger.debug("Failed to load pending avoidance probes", exc_info=True)
            return {"items": []}

    @app.post("/api/avoidance-probes/respond")
    async def respond_to_avoidance_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated avoidance probe."""
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Avoidance speculator not available")

        def metadata_fn(item_domain: str) -> dict[str, object]:
            return _probe_metadata_from_active_avoidance(
                speculator,
                item_domain,
            )

        if response_type == "confirm":
            _record_probe_feedback_history(
                domain,
                "confirm",
                speculator=speculator,
                state_key="avoidance_probe_feedback_history",
                metadata_fn=metadata_fn,
            )
            confirm_fn = getattr(speculator, "user_confirm_avoidance", None)
            active_avoidance = confirm_fn(domain) if callable(confirm_fn) else None
            ok = active_avoidance is not None
            if ok:
                topics = topics_for_confirmed_avoidance(active_avoidance)
                changes = await apply_new_dislikes(
                    memory=ctx.memory_manager,
                    database=getattr(ctx, "database", None)
                    or getattr(ctx.memory_manager, "_database", None),
                    embedding_service=getattr(ctx.soul_engine, "_embedding_service", None),
                    llm_service=getattr(ctx, "llm_service", None),
                    topics=topics,
                )
                _record_probe_cognition(
                    f"你确认了避开「{domain}」，已更新不喜欢方向。",
                    domain,
                    "confirmed",
                    source="avoidance_probe",
                    detail="\n".join(changes) if changes else "",
                )
                await _publish_probe_event(
                    "avoidance.confirmed",
                    f"你确认了避开「{domain}」，已更新不喜欢方向。",
                    domain,
                )
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            _record_probe_feedback_history(
                domain,
                "reject",
                speculator=speculator,
                state_key="avoidance_probe_feedback_history",
                metadata_fn=metadata_fn,
            )
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            ok = bool(reject_fn(domain) if callable(reject_fn) else False)
            if ok:
                _record_probe_cognition(
                    f"你表示并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                    "rejected",
                    source="avoidance_probe",
                )
                await _publish_probe_event(
                    "avoidance.rejected",
                    f"已记录：你并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能想避开的「{domain}」这个方向"
        contextual_message = f"[关于避雷方向「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            sentiment, classifier = await _classify_probe_sentiment(
                raw_message,
                reply,
                domain,
            )
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for avoidance probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if sentiment == "negative":
            chat_response = "avoidance_chat_confirmed"
            resulting_action = "confirmed"
            speculator.observe(
                [
                    {
                        "event_type": "dislike",
                        "title": domain,
                        "metadata": {
                            "feedback_type": "dislike",
                            "user_message": raw_message,
                            "source": "avoidance_probe_chat",
                        },
                    }
                ]
            )
            summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
        elif sentiment in {"strong_positive", "weak_positive"}:
            chat_response = "avoidance_chat_rejected"
            resulting_action = "rejected"
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            if callable(reject_fn):
                reject_fn(domain, cooldown_days=14)
            summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
        else:
            chat_response = "avoidance_chat_neutral"
            resulting_action = "none"
            summary = f"关于避雷方向「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
            state_key="avoidance_probe_feedback_history",
            metadata_fn=metadata_fn,
        )
        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(
            summary,
            domain,
            "chat",
            source="avoidance_probe",
            detail=detail,
        )
        await _publish_probe_event("avoidance.chat", summary, domain)
        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment", "dismiss"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = ctx.database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        ctx.database.update_recommendation_feedback(
            payload.recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            build_event,
        )

        rec_title = str(recommendation.get("title", ""))
        # Tailor a natural-language context per feedback type — the
        # "feedback" verb in the generic table doesn't capture the
        # like/dislike/comment distinction the LLM cares about.
        feedback_label = {
            "like": "点赞了",
            "dislike": "踩了",
            "comment": "评论了",
            "dismiss": "忽略了",
        }.get(feedback_type, "反馈了")
        feedback_context = f"在 B 站{feedback_label}《{rec_title}》"
        if note:
            feedback_context = f"{feedback_context},备注:{note}"
        await ctx.memory_manager.propagate_event(
            build_event(
                event_type="feedback",
                source_platform=SOURCE_BILIBILI,
                title=rec_title,
                context=feedback_context,
                metadata={
                    "recommendation_id": payload.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": feedback_type,
                    "feedback_note": note,
                },
            )
        )
        buffer_domain, buffer_specifics = _recommendation_buffer_domain(recommendation)
        if feedback_type == "like":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="card_like",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        elif feedback_type == "dislike":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="negative",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        record_immediate_feedback_cognition = getattr(
            ctx.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if callable(record_immediate_feedback_cognition):
            with suppress(Exception):
                record_immediate_feedback_cognition(
                    feedback_type=feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=note,
                )
        asyncio.create_task(_run_post_feedback_tasks())
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    @app.post(
        "/api/recommendation-click",
        response_model=RecommendationClickResponse,
    )
    async def recommendation_click(
        payload: RecommendationClickIn,
    ) -> RecommendationClickResponse:
        """Ingest a recommendation click-through as a strong profile signal.

        The click is evidence that the user actively chose to watch a
        recommended video. It is treated as a strong signal that bypasses
        the pipeline's min_signals gate and updates Interest + Surface
        immediately. If the recommendation_id resolves to a stored card,
        its metadata (title, topic, up_name) is pulled from the database
        so the payload reaches the pipeline even when the extension sends
        only a bare BV id.
        """
        from openbiliclaw.soul.pipeline import signal_from_recommendation_click

        recommendation: dict[str, object] | None = None
        if payload.recommendation_id is not None:
            recommendation = ctx.database.get_recommendation_by_id(
                payload.recommendation_id,
            )

        bvid = (payload.bvid or "").strip()
        content_id = (payload.content_id or "").strip()
        content_url = (payload.content_url or "").strip()
        source_platform_raw = (payload.source_platform or "").strip()
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "") or "").strip()
            content_id = content_id or str(recommendation.get("content_id", "") or "").strip()
            content_url = content_url or str(recommendation.get("content_url", "") or "").strip()
            source_platform_raw = (
                source_platform_raw or str(recommendation.get("source_platform", "") or "").strip()
            )
            title = title or str(recommendation.get("title", "") or "").strip()
            topic_label = topic_label or str(recommendation.get("topic_label", "") or "").strip()
            up_name = up_name or str(recommendation.get("up_name", "") or "").strip()

        content_id = content_id or bvid
        bvid = bvid or content_id
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")
        if not source_platform_raw:
            source_platform_raw = _infer_source_platform_from_url(content_url)
        source_platform = _normalize_source_platform(source_platform_raw)
        if not content_url:
            content_url = _fallback_recommendation_click_url(
                source_platform=source_platform,
                content_id=content_id,
                bvid=bvid,
            )

        # Persist the click as an event so history/query paths can see it.
        from openbiliclaw.sources.event_format import (
            build_event,
            format_event_context,
        )

        click_extra_parts: list[str] = []
        if topic_label:
            click_extra_parts.append(f"主题:{topic_label}")
        click_context = format_event_context(
            event_type="click",
            source_platform=source_platform,
            title=title,
            author=up_name,
            extra=",".join(click_extra_parts),
        )
        click_metadata: dict[str, object] = {
            "recommendation_id": payload.recommendation_id,
            "bvid": bvid,
            "content_id": content_id,
            "content_url": content_url,
            "source_platform": source_platform,
            "topic_label": topic_label,
            "up_name": up_name,
            "source": "recommendation_click",
        }
        # v0.3.x event-satisfaction: forward dwell so the persisted
        # click row can be classified as meaningful_dwell vs quick_exit.
        # Absent fields stay absent; storage classifier degrades to
        # unknown / missing_dwell. Storage is the single classification
        # owner — do not classify here.
        if payload.watch_seconds is not None:
            click_metadata["watch_seconds"] = payload.watch_seconds
        if payload.video_duration_seconds is not None:
            click_metadata["video_duration_seconds"] = payload.video_duration_seconds
        with suppress(Exception):
            await ctx.memory_manager.propagate_event(
                build_event(
                    event_type="click",
                    source_platform=source_platform,
                    title=title,
                    url=content_url,
                    author=up_name,
                    context=click_context,
                    metadata=click_metadata,
                )
            )
        buffer_domain, buffer_specifics = _recommendation_buffer_domain(
            {
                "title": title,
                "topic_label": topic_label,
                "bvid": bvid,
            }
        )
        _record_exploration_buffer_event(
            domain=buffer_domain,
            specifics=buffer_specifics,
            source_event="plain_click",
            evidence_id=bvid,
        )

        # Push a strong signal into the profile update pipeline.
        layers_updated: list[str] = []
        pipeline = getattr(ctx.soul_engine, "pipeline", None) if ctx.soul_engine else None
        if pipeline is not None:
            signal = signal_from_recommendation_click(
                bvid=bvid,
                title=title,
                recommendation_id=payload.recommendation_id,
                topic_label=topic_label,
                up_name=up_name,
                content_id=content_id,
                content_url=content_url,
                source_platform=source_platform,
            )
            try:
                ingest_result = await pipeline.ingest(signal)
            except Exception:
                logger.exception("Failed to ingest recommendation_click signal")
            else:
                layers_updated = [r.layer.value for r in ingest_result.layers_updated]

        return RecommendationClickResponse(
            ok=True,
            bvid=bvid,
            layers_updated=layers_updated,
        )

    # ── Source recipe management endpoints ──────────────────────────

    @app.get("/api/sources")
    def list_sources() -> dict[str, Any]:
        """Return all source recipes."""
        recipes = ctx.database.get_all_recipes()
        return {"items": recipes}

    @app.post("/api/sources", status_code=201)
    def create_source(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new source recipe."""
        import uuid

        recipe_id = payload.get("id") or str(uuid.uuid4())
        source_type = payload.get("source_type", "")
        name = payload.get("name", "")
        strategy = payload.get("strategy", "")
        if not source_type or not name or not strategy:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail="source_type, name, and strategy are required",
            )
        recipe = {
            "id": recipe_id,
            "source_type": source_type,
            "name": name,
            "strategy": strategy,
            "config": payload.get("config", {}),
            "target_share": payload.get("target_share", 4),
            "enabled": payload.get("enabled", True),
            "created_by": payload.get("created_by", "user"),
        }
        ctx.database.save_source_recipe(recipe)
        return {"ok": True, "recipe": recipe}

    @app.put("/api/sources/{recipe_id}")
    def update_source(recipe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing source recipe."""
        updated = ctx.database.update_recipe(recipe_id, **payload)
        if not updated:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    @app.delete("/api/sources/{recipe_id}")
    def delete_source(recipe_id: str) -> dict[str, Any]:
        """Delete a source recipe (system recipes cannot be deleted)."""
        # Check if it's a system recipe
        all_recipes = ctx.database.get_all_recipes()
        target = next((r for r in all_recipes if r["id"] == recipe_id), None)
        if target and target.get("created_by") == "system":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="System recipes cannot be deleted")
        deleted = ctx.database.delete_recipe(recipe_id)
        if not deleted:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    # ── XHS observed URL ingestion endpoint ─────────────────────────

    xhs_max_urls_per_batch = 50
    xhs_url_prefix = "https://www.xiaohongshu.com/"

    def _pick_best_xhs_url(database: Any, note_id: str, incoming: str) -> str:
        """Return the most share-worthy URL for a xhs note.

        xhs search-result pages don't render ``xsec_token`` into ``<a href>``
        (React SPA keeps the token in props, not DOM), but explore-feed
        cards do. When the same note arrives both ways, prefer the URL
        that carries a token — without it, outbound links can silently
        dead-end at an xhs login wall.

        Order of preference:
        1. ``incoming`` URL if it already has ``xsec_token=``
        2. Any prior ``xhs_observed_urls`` row for this note with a token
        3. Existing ``content_cache.content_url`` if it has a token
        4. Fall back to ``incoming`` (bare URL — still works for the
           logged-in user on the xhs domain, just not guaranteed for
           share/outbound traffic)
        """
        if "xsec_token=" in incoming:
            return incoming
        try:
            row = database.conn.execute(
                "SELECT url FROM xhs_observed_urls "
                "WHERE url LIKE ? AND url LIKE '%xsec_token=%' "
                "ORDER BY observed_at DESC LIMIT 1",
                (f"%/{note_id}?%",),
            ).fetchone()
            if row and row["url"]:
                return str(row["url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM content_cache WHERE bvid=?",
                (note_id,),
            ).fetchone()
            if row and isinstance(row["content_url"], str) and "xsec_token=" in row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        return incoming

    def _backfill_xhs_tokens(database: Any, urls: list[str]) -> int:
        """Upgrade cached xhs rows whose content_url lacks xsec_token.

        The extension often observes the same note twice — once from a
        search result page (no token in ``<a href>``) and once from an
        explore-feed card (token present). When a tokenized URL arrives
        later, rewrite the previously-cached bare URL so share links
        don't dead-end at xhs's login wall.
        """
        from urllib.parse import urlparse

        updated = 0
        for url in urls:
            if "xsec_token=" not in url:
                continue
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                continue
            if not note_id:
                continue
            try:
                cursor = database.conn.execute(
                    "UPDATE content_cache SET content_url=? "
                    "WHERE bvid=? AND source_platform='xiaohongshu' "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')",
                    (url, note_id),
                )
                updated += cursor.rowcount or 0
            except Exception:
                continue
        if updated:
            with suppress(Exception):
                database.conn.commit()
        return updated

    # ── XHS self-author filter (v0.3.48+) ────────────────────────────
    #
    # XHS search / explore / saved-author paths all happily return the
    # logged-in user's own published notes. Without filtering, the
    # recommendation pool fills with content the user posted themselves
    # ("自己发的笔记被推回给自己" — observed in 2026-05-05 logs as
    # 屎屎/三花/etc. cat photos polluting the popup). The extension
    # bootstrap captures self user_id + nickname from XHS state and
    # sends it back via ``debug.xhs_bootstrap.steps[*].self_info``.
    # Backend persists in ``discovery_runtime_state["xhs_self_info"]``
    # and consults it on every ingest path.

    def _normalize_self_info(raw: Any) -> dict[str, str] | None:
        """Validate + normalize a self_info-shaped dict.

        Returns ``{"user_id": ..., "nickname": ...}`` if either field is
        non-empty, otherwise ``None``.
        """
        if not isinstance(raw, dict):
            return None
        user_id = str(raw.get("user_id", "") or "").strip()
        nickname = str(raw.get("nickname", "") or "").strip()
        if not user_id and not nickname:
            return None
        return {"user_id": user_id, "nickname": nickname}

    def _extract_self_info_from_payload(payload: Any) -> dict[str, str] | None:
        """Pull self_info from any XHS ingest payload.

        v0.3.57+: extension v0.3.10 sends self_info at the **payload top
        level** for every ingest path (passive ``observed-urls``, search /
        creator ``task-result``, bootstrap_profile ``task-result``). The
        legacy bootstrap-only nested location
        ``debug.xhs_bootstrap.steps[*].self_info`` (v0.3.48 / extension
        v0.3.9) is kept as fallback for older extensions.
        """
        if not isinstance(payload, dict):
            return None
        # 1) New top-level location.
        info = _normalize_self_info(payload.get("self_info"))
        if info is not None:
            return info
        # 2) Legacy bootstrap-debug nested location.
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            return None
        bootstrap = debug.get("xhs_bootstrap")
        if not isinstance(bootstrap, dict):
            return None
        steps = bootstrap.get("steps")
        if not isinstance(steps, list):
            return None
        for step in steps:
            if not isinstance(step, dict):
                continue
            info = _normalize_self_info(step.get("self_info"))
            if info is not None:
                return info
        return None

    def _persist_xhs_self_info(self_info: dict[str, str]) -> None:
        """Save self info into discovery_runtime_state if not already there."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            # Idempotent: only write when content changes (avoid sqlite churn).
            if isinstance(existing, dict) and existing == self_info:
                return
            state["xhs_self_info"] = self_info
            memory_manager.save_discovery_runtime_state(state)
            logger.info(
                "xhs self_info persisted: user_id=%s nickname=%r",
                self_info.get("user_id", ""),
                self_info.get("nickname", ""),
            )
        except Exception:
            logger.exception("Failed to persist xhs self_info")

    def _load_xhs_self_info() -> dict[str, str]:
        """Load self info from runtime state (returns empty dict on miss)."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return {}
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            if isinstance(existing, dict):
                return {
                    "user_id": str(existing.get("user_id", "") or ""),
                    "nickname": str(existing.get("nickname", "") or ""),
                }
        except Exception:
            logger.exception("Failed to load xhs self_info")
        return {}

    def _is_self_authored_note(note: dict[str, Any], self_info: dict[str, str]) -> bool:
        """Check whether a note's author matches the logged-in user.

        Both user_id and nickname can match — XHS sometimes only ships
        nickname in note metadata (no author user_id), other times both.
        Treat the match as case-insensitive on the trimmed values.
        """
        if not self_info:
            return False
        nickname = self_info.get("nickname", "").strip().lower()
        user_id = self_info.get("user_id", "").strip().lower()
        author = str(note.get("author", "") or "").strip().lower()
        if author and nickname and author == nickname:
            return True
        author_id = str(note.get("author_id", "") or "").strip().lower()
        return bool(author_id and user_id and author_id == user_id)

    def _purge_self_authored_pool_items(
        database: Any,
        self_info: dict[str, str],
    ) -> int:
        """Mark every pool row authored by ``self_info.nickname`` as suppressed.

        v0.3.57+: cleans up content_cache rows that entered before the
        per-path self_info filter was wired in. Idempotent — already-
        suppressed rows are not flipped further. Returns the number of
        rows actually changed in this call.

        ``up_name`` is the column populated by ``_cache_xhs_notes`` from
        the note's ``author`` field, so the comparison mirrors the
        runtime filter exactly.
        """
        if not self_info or not hasattr(database, "conn"):
            return 0
        nickname = (self_info.get("nickname") or "").strip()
        if not nickname:
            return 0
        try:
            cursor = database.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'suppressed' "
                "WHERE source_platform = 'xiaohongshu' "
                "  AND COALESCE(pool_status, 'fresh') = 'fresh' "
                "  AND LOWER(COALESCE(up_name, '')) = LOWER(?)",
                (nickname,),
            )
            database.conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            logger.exception("Failed to purge self-authored xhs pool items")
            return 0

    def _cache_xhs_notes(
        database: Any,
        notes: list[dict[str, Any]],
        page_type: str,
        self_info: dict[str, str] | None = None,
    ) -> int:
        """Store xhs note metadata from the extension directly into content_cache.

        ``self_info`` (v0.3.48+) lets the caller pass the just-extracted
        login fingerprint from the same request — avoids a round-trip
        through ``discovery_runtime_state`` and works against test
        stubs that haven't implemented the runtime-state API.  When
        ``None``, falls back to the persisted state.
        """
        from urllib.parse import urlparse

        if self_info is None:
            self_info = _load_xhs_self_info()
        cached = 0
        skipped_self = 0
        for note in notes:
            if _is_self_authored_note(note, self_info):
                skipped_self += 1
                continue
            url = note.get("url", "")
            if not isinstance(url, str) or not url.startswith(xhs_url_prefix):
                continue
            # Extract note ID from URL path
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                note_id = ""
            if not note_id:
                continue

            title = str(note.get("title", "") or "").strip()
            if not title:
                continue  # Skip notes with empty title — they produce blank recommendation cards
            author = str(note.get("author", "") or "").strip()
            cover_url = str(note.get("cover_url", "") or "").strip()
            best_url = _pick_best_xhs_url(database, note_id, url)

            # Cache as DiscoveredContent with multi-source fields.
            # NOTE: `cache_content` reads the `source` kwarg (not `source_strategy`)
            # for the content_cache.source column — passing the wrong key silently
            # dropped the label and was the cause of empty-source xhs rows.
            database.cache_content(
                bvid=note_id,
                title=title,
                up_name=author,
                cover_url=cover_url,
                source=f"xhs-extension-{page_type}",
                content_id=note_id,
                content_url=best_url,
                source_platform="xiaohongshu",
                author_name=author,
            )
            cached += 1
        if skipped_self > 0:
            logger.info(
                "xhs ingest filter: dropped %d self-authored note(s) (%s)",
                skipped_self,
                page_type,
            )
        return cached

    @app.post("/api/sources/xhs/observed-urls")
    async def ingest_xhs_observed_urls(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept xhs note URLs + optional metadata the extension collected.

        Body: ``{ "urls": [...], "notes": [{url, title, author, cover_url}], "page_type": "..." }``

        When ``notes`` is present, metadata is stored directly into content_cache
        as DiscoveredContent — no sidecar enrichment needed.  A background LLM
        classification task is spawned so the content receives the same
        ``style_key`` / ``topic_group`` / ``relevance_score`` that bilibili
        content gets during discovery.
        """
        from fastapi import HTTPException

        urls_raw: list[str] = payload.get("urls", [])
        notes_raw: list[dict[str, Any]] = payload.get("notes", [])
        page_type: str = payload.get("page_type", "other")

        if not urls_raw and not notes_raw:
            raise HTTPException(status_code=422, detail="urls or notes must be non-empty")
        if len(urls_raw) > xhs_max_urls_per_batch:
            raise HTTPException(
                status_code=422,
                detail=f"Too many URLs (max {xhs_max_urls_per_batch})",
            )

        # v0.3.57+: passive collector (extension v0.3.10) piggybacks
        # self_info on every observed-urls request. Persist on first
        # arrival so subsequent requests without self_info still filter
        # via the loaded state.
        self_info_now = _extract_self_info_from_payload(payload)
        if self_info_now:
            _persist_xhs_self_info(self_info_now)
        self_info_for_filter = self_info_now or _load_xhs_self_info()

        # Filter to valid xhs note URLs
        valid_urls = [
            u
            for u in urls_raw
            if isinstance(u, str) and u.startswith(xhs_url_prefix) and "/explore/" in u
        ]

        # Store bare URLs for tracking
        if valid_urls:
            ctx.database.save_xhs_observed_urls(valid_urls, page_type)
            _backfill_xhs_tokens(ctx.database, valid_urls)

        # Store rich notes directly into content_cache
        cached = 0
        if notes_raw:
            cached = _cache_xhs_notes(
                ctx.database,
                notes_raw,
                page_type,
                self_info=self_info_for_filter or None,
            )
            # Trigger background LLM classification so XHS content gets the
            # same style_key / topic_group / relevance_score that bilibili
            # content receives during discovery.  Without this the
            # recommendation diversity mechanism collapses (all XHS items
            # share "unknown" style and a single fallback topic token).
            if cached and ctx.recommendation_engine is not None:
                asyncio.create_task(_classify_new_pool_items())

        return {"ok": True, "accepted": max(len(valid_urls), cached)}

    @app.post("/api/sources/xhs/tokens")
    def ingest_xhs_tokens(payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest ``(note_id, xsec_token)`` pairs harvested by the MAIN-
        world fetch sniffer inside ``dist/main/xhs-token-sniffer.js``.

        We rebuild the full tokenized URL from each pair and feed it
        through ``_backfill_xhs_tokens`` so previously-cached bare URLs
        (the typical search-page-sourced ones) get upgraded in place.
        Without this, clicking an xhs recommendation trips xhs's 300031
        access-denied gating because the stored URL lacks xsec_token.
        """
        raw = payload.get("pairs", [])
        if not isinstance(raw, list) or not raw:
            return {"ok": True, "upgraded": 0}
        urls: list[str] = []
        for pair in raw:
            if not isinstance(pair, dict):
                continue
            note_id = str(pair.get("note_id", "") or "").strip()
            token = str(pair.get("xsec_token", "") or "").strip()
            # Guard against the noise the sniffer's deep-walk can surface
            # — e.g. 24-hex ids that aren't notes. The backfill UPDATE is
            # narrow (bvid match), so the worst case of a false id is a
            # no-op, but the token must at least be non-empty.
            if not note_id or not token:
                continue
            urls.append(f"{xhs_url_prefix}explore/{note_id}?xsec_token={token}")
        upgraded = _backfill_xhs_tokens(ctx.database, urls)
        return {"ok": True, "upgraded": upgraded}

    # ── XHS task queue endpoints (extension dispatcher) ──────────────

    from openbiliclaw.sources.xhs_tasks import (
        XhsCreatorStore,
        XhsTaskQueue,
        xhs_bootstrap_note_key,
        xhs_bootstrap_notes_to_events,
    )

    # Guard: only initialise when ctx.database is a real Database (has .conn).
    # Tests that pass database=object() as a stub won't trigger table creation.
    _xhs_task_queue: XhsTaskQueue | None = None
    _xhs_creator_store: XhsCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _xhs_task_queue = XhsTaskQueue(ctx.database)
        _xhs_creator_store = XhsCreatorStore(ctx.database)

    @app.get("/api/sources/xhs/next-task")
    def xhs_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable xhs task, or 204 if none."""
        from starlette.responses import Response

        # 204 No Content responses MUST NOT carry a body (RFC 7230).
        # JSONResponse(204, None) serialises None to "null" (4 bytes),
        # then GZipMiddleware (minimum_size=0) wraps it into ~20 bytes
        # of gzip stream while Content-Length stays at 4, which trips
        # h11's strict "Too much data for declared Content-Length"
        # check on every poll. Use a body-less Response instead.
        if _xhs_task_queue is None:
            return Response(status_code=204)
        task = _xhs_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/xhs/task-result")
    async def xhs_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        urls = payload.get("urls", [])
        notes = [note for note in payload.get("notes", []) if isinstance(note, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _xhs_task_queue is None:
            return {"ok": True}

        task = _xhs_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_notes = _xhs_task_queue.merge_result(
                task_id,
                urls=urls,
                notes=notes if notes else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # v0.3.48+: piggyback self_info from bootstrap debug payload.
            # v0.3.57+: also accept self_info at the payload top level for
            # search / creator / passive paths via extension v0.3.10.
            # Persist immediately so future requests can also consult it,
            # AND use the just-extracted value in this request's
            # downstream filters (skip a state round-trip that some
            # in-process test stubs don't implement).
            self_info_from_request = _extract_self_info_from_payload(payload)
            if self_info_from_request:
                _persist_xhs_self_info(self_info_from_request)
            self_info_now = self_info_from_request or _load_xhs_self_info()
            # Store discovered URLs + metadata
            valid_urls = [u for u in urls if isinstance(u, str) and u.startswith(xhs_url_prefix)]
            if valid_urls:
                ctx.database.save_xhs_observed_urls(valid_urls, "task")
                _backfill_xhs_tokens(ctx.database, valid_urls)
            if added_notes:
                _cache_xhs_notes(ctx.database, added_notes, "task", self_info_now)
            if task_type == "bootstrap_profile" and added_notes:
                fresh_notes, note_keys_by_index = _filter_new_source_bootstrap_items(
                    "xhs",
                    added_notes,
                    xhs_bootstrap_note_key,
                )
                # Filter self-authored notes from event propagation —
                # otherwise the user's own posts get treated as their
                # own "favorite/like" signals and warp the soul profile.
                propagated = 0
                skipped_self = 0
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, note in enumerate(fresh_notes):
                    if _is_self_authored_note(note, self_info_now):
                        skipped_self += 1
                        continue
                    for event in xhs_bootstrap_notes_to_events([note]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = note_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                        propagated += 1
                await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("xhs", propagated_keys)
                if skipped_self > 0:
                    logger.info(
                        "xhs bootstrap propagate: dropped %d self-authored note(s) (%d propagated)",
                        skipped_self,
                        propagated,
                    )
        else:
            _xhs_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    @app.get("/api/sources/xhs/creators")
    def xhs_list_creators() -> dict[str, Any]:
        """List all xhs creator subscriptions."""
        if _xhs_creator_store is None:
            return {"items": []}
        return {"items": _xhs_creator_store.list_all()}

    @app.post("/api/sources/xhs/creators", status_code=201)
    def xhs_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an xhs creator subscription."""
        from fastapi import HTTPException

        creator_id = payload.get("creator_id", "")
        creator_url = payload.get("creator_url", "")
        display_name = payload.get("display_name", "")

        if not creator_id or not creator_url:
            raise HTTPException(
                status_code=422,
                detail="creator_id and creator_url are required",
            )

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        _xhs_creator_store.add(creator_id, creator_url, display_name)
        return {"ok": True}

    @app.delete("/api/sources/xhs/creators/{sub_id}")
    def xhs_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an xhs creator subscription."""
        from fastapi import HTTPException

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        deleted = _xhs_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── Douyin task queue endpoints (extension dispatcher) ──────────
    # Independent from the XHS block above by design — see
    # docs/plans/2026-05-06-douyin-bootstrap-import-design.md
    # §"Module Isolation from XHS". Different table (dy_tasks),
    # different queue class, different fail isolation.

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_video_key,
        dy_bootstrap_videos_to_events,
    )

    _dy_task_queue: DyTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _dy_task_queue = DyTaskQueue(ctx.database)

    @app.get("/api/sources/dy/next-task")
    def dy_next_task(response: Any = None) -> Any:
        """Return the oldest pending dy task, or 204 if none."""
        from starlette.responses import Response

        if _dy_task_queue is None:
            return Response(status_code=204)
        task = _dy_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/dy/task-result")
    async def dy_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Douyin task result from the extension dispatcher.

        Status semantics mirror XHS (``ok`` = final, ``partial`` = keep
        pending, ``failed`` = mark failed) but the result schema uses
        ``videos`` instead of ``notes`` and propagation goes through
        ``dy_bootstrap_videos_to_events``. No self-author filtering yet
        (Douyin has its own posts in ``dy_post`` scope which we treat as
        a weak ``view`` signal — they're meant to count as input).
        """
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        videos = [v for v in payload.get("videos", []) if isinstance(v, dict)]
        # TEMP DEBUG: surface incoming partial debug field for the dy
        # bootstrap e2e probe (will be reverted before release).
        logger.info(
            "[dy-debug] task_result IN: status=%s videos=%d debug=%s",
            status,
            len(videos),
            payload.get("debug"),
        )
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _dy_task_queue is None:
            return {"ok": True}

        task = _dy_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_videos = _dy_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            if task_type == "bootstrap_profile" and added_videos:
                fresh_videos, video_keys_by_index = _filter_new_source_bootstrap_items(
                    "dy",
                    added_videos,
                    dy_bootstrap_video_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, video in enumerate(fresh_videos):
                    for event in dy_bootstrap_videos_to_events([video]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = video_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("dy", propagated_keys)
        else:
            _dy_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    # ── Wake-up kick endpoints ──────────────────────────────────────
    #
    # The extension's task dispatchers normally poll on a 60s
    # chrome.alarms timer. That's fine for the steady state but
    # introduces a 0–60s wait between CLI enqueue and extension pickup,
    # which racing init's 30s collect window is the actual reason init
    # sometimes prints "扩展未连接或任务仍在后台跑". These endpoints let
    # the CLI broadcast a wake-up event over the existing
    # /api/runtime-stream WebSocket so the dispatcher polls immediately
    # instead of waiting for the next alarm. The 60s alarm stays as
    # fallback for the WS-down case.

    # TEMP DEBUG: extension-side log relay. Lets the service-worker
    # dispatcher POST debug events here so they end up in the daemon
    # log alongside backend-side activity. Will be reverted before
    # release.
    @app.post("/api/sources/_debug/log")
    async def ext_debug_log(payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source", "?"))[:8]
        event = str(payload.get("event", "?"))[:80]
        data = payload.get("data")
        logger.warning("[ext-debug] [%s] %s data=%s", source, event, data)
        return {"ok": True}

    @app.post("/api/sources/xhs/kick")
    async def xhs_task_kick() -> dict[str, Any]:
        """Broadcast `xhs_task_available` so any subscribed extension
        service-worker triggers an immediate poll. Idempotent and best
        effort — failures here never affect task state."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "xhs_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/sources/dy/kick")
    async def dy_task_kick() -> dict[str, Any]:
        """Broadcast `dy_task_available` over runtime-stream. See
        xhs_task_kick docstring for rationale."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "dy_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── YouTube bootstrap endpoints ────────────────────────────────
    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_item_key,
        yt_bootstrap_items_to_events,
    )

    _yt_task_queue: YtTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _yt_task_queue = YtTaskQueue(ctx.database)

    @app.get("/api/sources/yt/next-task")
    def yt_next_task(response: Any = None) -> Any:
        """Return the oldest pending YouTube task, or 204 if none."""
        from starlette.responses import Response

        if _yt_task_queue is None:
            return Response(status_code=204)
        task = _yt_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/yt/task-result")
    async def yt_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a YouTube task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _yt_task_queue is None:
            return {"ok": True}

        task = _yt_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_items = _yt_task_queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            if task_type == "bootstrap_profile" and added_items:
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "yt",
                    added_items,
                    yt_bootstrap_item_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, item in enumerate(fresh_items):
                    for event in yt_bootstrap_items_to_events([item]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = item_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("yt", propagated_keys)
        else:
            _yt_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/yt/kick")
    async def yt_task_kick() -> dict[str, Any]:
        """Broadcast `yt_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "yt_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/extension/reload")
    async def extension_reload() -> dict[str, Any]:
        """Dev-only: broadcast `extension_reload` so the connected
        service-worker calls chrome.runtime.reload() — picks up the
        latest /dist bundle without the user clicking the reload icon
        in chrome://extensions.

        Best-effort — silent when no event-hub is wired."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "extension_reload", "source": "dev"})
        return {"ok": True}

    # ── Configuration management endpoints ──────────────────────────

    def _config_to_response(
        cfg: Any,
        issues: list[Any] | None = None,
        *,
        mask_keys: bool = True,
        degraded: bool = False,
        degraded_reason: str = "",
    ) -> ConfigResponse:
        """Convert a Config dataclass to a ConfigResponse, optionally masking API keys."""

        def _mask(key: str) -> str:
            if not mask_keys or not key:
                return key
            if len(key) <= 8:
                return "*" * len(key)
            return key[:4] + "*" * (len(key) - 8) + key[-4:]

        def _provider_out(p: Any) -> LLMProviderConfigOut:
            return LLMProviderConfigOut(
                api_key=_mask(p.api_key),
                model=p.model,
                base_url=p.base_url,
                auth_mode=getattr(p, "auth_mode", ""),
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
                reasoning_effort=getattr(p, "reasoning_effort", ""),
            )

        issue_list = [
            ConfigIssueOut(
                field=i.field,
                message=i.message,
                severity=getattr(i, "severity", "warning"),
            )
            for i in (issues or [])
        ]

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            degraded=degraded,
            degraded_reason=degraded_reason,
            llm=LLMConfigOut(
                default_provider=cfg.llm.default_provider,
                concurrency=int(getattr(cfg.llm, "concurrency", 3)),
                timeout=int(getattr(cfg.llm, "timeout", 300)),
                fallback_enabled=cfg.llm.fallback_enabled,
                fallback_provider=cfg.llm.fallback_provider,
                openai=_provider_out(cfg.llm.openai),
                claude=_provider_out(cfg.llm.claude),
                gemini=_provider_out(cfg.llm.gemini),
                deepseek=_provider_out(cfg.llm.deepseek),
                ollama=_provider_out(cfg.llm.ollama),
                openrouter=_provider_out(cfg.llm.openrouter),
                openai_compatible=_provider_out(cfg.llm.openai_compatible),
                embedding=EmbeddingConfigOut(
                    provider=cfg.llm.embedding.provider,
                    model=cfg.llm.embedding.model,
                    api_key=_mask(cfg.llm.embedding.api_key),
                    base_url=cfg.llm.embedding.base_url,
                    similarity_threshold=cfg.llm.embedding.similarity_threshold,
                    fallback_enabled=cfg.llm.embedding.fallback_enabled,
                    fallback_provider=cfg.llm.embedding.fallback_provider,
                ),
                soul=ModuleLLMConfigOut(
                    provider=cfg.llm.soul.provider,
                    model=cfg.llm.soul.model,
                ),
                discovery=ModuleLLMConfigOut(
                    provider=cfg.llm.discovery.provider,
                    model=cfg.llm.discovery.model,
                ),
                recommendation=ModuleLLMConfigOut(
                    provider=cfg.llm.recommendation.provider,
                    model=cfg.llm.recommendation.model,
                ),
                evaluation=ModuleLLMConfigOut(
                    provider=cfg.llm.evaluation.provider,
                    model=cfg.llm.evaluation.model,
                ),
            ),
            bilibili=BilibiliConfigOut(
                auth_method=cfg.bilibili.auth_method,
                cookie=_mask(cfg.bilibili.cookie),
                browser_executable=cfg.bilibili.browser_executable,
                browser_headed=cfg.bilibili.browser_headed,
            ),
            sources=SourcesConfigOut(
                browser=SourcesBrowserConfigOut(
                    cdp_url=cfg.sources.browser_cdp_url,
                    headed=cfg.sources.browser_headed,
                ),
                bilibili=BilibiliSourceConfigOut(
                    enabled=cfg.sources.bilibili.enabled,
                ),
                xiaohongshu=XiaohongshuSourceConfigOut(
                    enabled=cfg.sources.xiaohongshu.enabled,
                    daily_search_budget=cfg.sources.xiaohongshu.daily_search_budget,
                    daily_creator_budget=cfg.sources.xiaohongshu.daily_creator_budget,
                    task_interval_seconds=cfg.sources.xiaohongshu.task_interval_seconds,
                ),
                douyin=DouyinSourceConfigOut(
                    enabled=cfg.sources.douyin.enabled,
                    mode=cfg.sources.douyin.mode,
                    cookie_env=cfg.sources.douyin.cookie_env,
                    daily_search_budget=cfg.sources.douyin.daily_search_budget,
                    daily_hot_budget=cfg.sources.douyin.daily_hot_budget,
                    daily_feed_budget=cfg.sources.douyin.daily_feed_budget,
                    request_interval_seconds=cfg.sources.douyin.request_interval_seconds,
                ),
                youtube=YoutubeSourceConfigOut(
                    enabled=cfg.sources.youtube.enabled,
                    daily_search_budget=cfg.sources.youtube.daily_search_budget,
                    daily_trending_budget=cfg.sources.youtube.daily_trending_budget,
                    daily_channel_budget=cfg.sources.youtube.daily_channel_budget,
                    request_interval_seconds=cfg.sources.youtube.request_interval_seconds,
                    min_interval_minutes=cfg.sources.youtube.min_interval_minutes,
                ),
            ),
            scheduler=SchedulerConfigOut(
                enabled=cfg.scheduler.enabled,
                pause_on_extension_disconnect=cfg.scheduler.pause_on_extension_disconnect,
                extension_disconnect_grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
                discovery_cron=cfg.scheduler.discovery_cron,
                pool_target_count=cfg.scheduler.pool_target_count,
                pool_source_shares=dict(cfg.scheduler.pool_source_shares),
                account_sync_interval_hours=cfg.scheduler.account_sync_interval_hours,
                refresh_check_interval_seconds=cfg.scheduler.refresh_check_interval_seconds,
                signal_event_threshold=cfg.scheduler.signal_event_threshold,
                trending_refresh_hours=cfg.scheduler.trending_refresh_hours,
                explore_refresh_hours=cfg.scheduler.explore_refresh_hours,
                discovery_limit=cfg.scheduler.discovery_limit,
                proactive_push_interval_seconds=cfg.scheduler.proactive_push_interval_seconds,
                speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
                speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
                speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
                speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
                speculation_confirmation_threshold=(
                    cfg.scheduler.speculation_confirmation_threshold
                ),
                speculation_max_active=cfg.scheduler.speculation_max_active,
                speculation_max_primary_interests=(cfg.scheduler.speculation_max_primary_interests),
                speculation_max_secondary_interests=(
                    cfg.scheduler.speculation_max_secondary_interests
                ),
                avoidance_speculation_interval_minutes=(
                    cfg.scheduler.avoidance_speculation_interval_minutes
                ),
                avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
                avoidance_speculation_cooldown_days=(
                    cfg.scheduler.avoidance_speculation_cooldown_days
                ),
                avoidance_speculation_confirmation_threshold=(
                    cfg.scheduler.avoidance_speculation_confirmation_threshold
                ),
                avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
            ),
            storage=StorageConfigOut(db_path=cfg.storage.db_path),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
                file_path=str(cfg.logging.file_path),
                max_file_size_mb=cfg.logging.max_file_size_mb,
                backup_count=cfg.logging.backup_count,
                aggregate_budget_mb=cfg.logging.aggregate_budget_mb,
                unmanaged_truncate_mb=cfg.logging.unmanaged_truncate_mb,
                unmanaged_max_age_days=cfg.logging.unmanaged_max_age_days,
            ),
            issues=issue_list,
        )

    @app.get("/api/config", response_model=ConfigResponse)
    def get_config(reveal_keys: bool = False) -> ConfigResponse:
        """Return the current configuration (API keys masked by default)."""
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
        )

        cfg = load_config()
        issues = list(_collect_config_issues(cfg))
        if bool(getattr(ctx, "degraded", False)):
            issues.extend(getattr(ctx, "degraded_issues", []))
        return _config_to_response(
            cfg,
            issues,
            mask_keys=not reveal_keys,
            degraded=bool(getattr(ctx, "degraded", False)),
            degraded_reason=str(getattr(ctx, "degraded_reason", "")),
        )

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(payload: ConfigUpdateIn) -> ConfigUpdateResponse | JSONResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _DEFAULT_DISCOVERY_LIMIT,
            _DEFAULT_EXPLORE_REFRESH_HOURS,
            _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
            _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
            _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
            _DEFAULT_SIGNAL_EVENT_THRESHOLD,
            _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
            _DEFAULT_TRENDING_REFRESH_HOURS,
            _collect_config_issues,
            _default_config_path,
            _normalize_extension_disconnect_grace,
            _normalize_llm_concurrency,
            _normalize_pool_source_shares,
            _normalize_scheduler_int,
            load_config,
            save_config,
        )

        cfg = load_config()
        update = payload.model_dump(exclude_none=True)
        reset_fields = [str(field) for field in update.pop("reset_fields", [])]
        unknown_reset_fields = [
            field for field in reset_fields if field not in _RESETTABLE_CONFIG_FIELDS
        ]
        if unknown_reset_fields:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_reset_fields",
                    "fields": unknown_reset_fields,
                },
            )

        def _as_bool(value: object) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return bool(value)

        # Apply top-level scalars
        if "language" in update:
            cfg.language = str(update["language"])
        if "data_dir" in update:
            cfg.data_dir = str(update["data_dir"])

        # Apply LLM updates
        if "llm" in update:
            llm_data = update["llm"]
            if "default_provider" in llm_data:
                cfg.llm.default_provider = str(llm_data["default_provider"])
            if "concurrency" in llm_data:
                cfg.llm.concurrency = _normalize_llm_concurrency(llm_data["concurrency"])
            if "timeout" in llm_data:
                from openbiliclaw.config import _normalize_llm_timeout

                cfg.llm.timeout = _normalize_llm_timeout(llm_data["timeout"])
            if "fallback_enabled" in llm_data:
                cfg.llm.fallback_enabled = _as_bool(llm_data["fallback_enabled"])
            if "fallback_provider" in llm_data:
                cfg.llm.fallback_provider = str(llm_data["fallback_provider"]).strip()
            for provider_name in (
                "openai",
                "claude",
                "gemini",
                "deepseek",
                "ollama",
                "openrouter",
                "openai_compatible",
            ):
                if provider_name in llm_data and isinstance(llm_data[provider_name], dict):
                    provider_cfg = getattr(cfg.llm, provider_name)
                    pdata = llm_data[provider_name]
                    skipped_fields: list[str] = []
                    for field_name in (
                        "api_key",
                        "model",
                        "base_url",
                        "auth_mode",
                        "http_referer",
                        "x_title",
                        "reasoning_effort",
                    ):
                        if field_name in pdata:
                            new_value = str(pdata[field_name])
                            if field_name == "api_key" and "*" in new_value:
                                skipped_fields.append(f"{field_name}=masked")
                                continue
                            existing = getattr(provider_cfg, field_name, "")
                            if (
                                field_name != "auth_mode"
                                and not new_value.strip()
                                and isinstance(existing, str)
                                and existing.strip()
                            ):
                                skipped_fields.append(f"{field_name}=empty_skip")
                                continue
                            setattr(provider_cfg, field_name, new_value)
                    if skipped_fields:
                        logger.debug(
                            "PUT /api/config: provider %s skipped fields: %s",
                            provider_name,
                            ", ".join(skipped_fields),
                        )
            if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
                emb = llm_data["embedding"]
                if "provider" in emb:
                    cfg.llm.embedding.provider = str(emb["provider"])
                if "model" in emb:
                    new_model = str(emb["model"])
                    if new_model.strip() or not cfg.llm.embedding.model.strip():
                        cfg.llm.embedding.model = new_model
                # v0.3.32+ — embedding owns api_key/base_url. Skip the
                # api_key write when the payload echoes back the masked
                # value (e.g. ``sk-d****a826``) so we don't overwrite the
                # real key with asterisks. A genuine API key never
                # contains ``*``.
                if "api_key" in emb:
                    new_key = str(emb["api_key"])
                    if "*" not in new_key and (
                        new_key.strip() or not cfg.llm.embedding.api_key.strip()
                    ):
                        cfg.llm.embedding.api_key = new_key
                if "base_url" in emb:
                    new_base_url = str(emb["base_url"])
                    if new_base_url.strip() or not cfg.llm.embedding.base_url.strip():
                        cfg.llm.embedding.base_url = new_base_url
                if "similarity_threshold" in emb:
                    cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
                if "fallback_enabled" in emb:
                    cfg.llm.embedding.fallback_enabled = _as_bool(emb["fallback_enabled"])
                if "fallback_provider" in emb:
                    cfg.llm.embedding.fallback_provider = str(emb["fallback_provider"]).strip()
            for module_name in ("soul", "discovery", "recommendation", "evaluation"):
                if module_name in llm_data and isinstance(llm_data[module_name], dict):
                    mod_cfg = getattr(cfg.llm, module_name)
                    mdata = llm_data[module_name]
                    if "provider" in mdata:
                        mod_cfg.provider = str(mdata["provider"])
                    if "model" in mdata:
                        mod_cfg.model = str(mdata["model"])

        # Apply bilibili updates
        if "bilibili" in update:
            bdata = update["bilibili"]
            if "auth_method" in bdata:
                cfg.bilibili.auth_method = str(bdata["auth_method"])
            if "cookie" in bdata:
                cfg.bilibili.cookie = str(bdata["cookie"])
            if "browser_executable" in bdata:
                cfg.bilibili.browser_executable = str(bdata["browser_executable"])
            if "browser_headed" in bdata:
                cfg.bilibili.browser_headed = _as_bool(bdata["browser_headed"])

        # Apply source updates
        if "sources" in update:
            sources_data = update["sources"]
            if isinstance(sources_data, dict):
                browser_data = sources_data.get("browser")
                if isinstance(browser_data, dict):
                    if "cdp_url" in browser_data:
                        cfg.sources.browser_cdp_url = str(browser_data["cdp_url"])
                    if "headed" in browser_data:
                        cfg.sources.browser_headed = _as_bool(browser_data["headed"])

                bilibili_data = sources_data.get("bilibili")
                if isinstance(bilibili_data, dict) and "enabled" in bilibili_data:
                    cfg.sources.bilibili.enabled = _as_bool(bilibili_data["enabled"])

                xhs_data = sources_data.get("xiaohongshu")
                if isinstance(xhs_data, dict):
                    if "enabled" in xhs_data:
                        cfg.sources.xiaohongshu.enabled = _as_bool(xhs_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_creator_budget",
                        "task_interval_seconds",
                    ):
                        if key in xhs_data:
                            setattr(cfg.sources.xiaohongshu, key, int(xhs_data[key]))

                dy_data = sources_data.get("douyin")
                if isinstance(dy_data, dict):
                    if "enabled" in dy_data:
                        cfg.sources.douyin.enabled = _as_bool(dy_data["enabled"])
                    if "mode" in dy_data:
                        cfg.sources.douyin.mode = str(dy_data["mode"])
                    if "cookie_env" in dy_data:
                        cfg.sources.douyin.cookie_env = str(dy_data["cookie_env"])
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "request_interval_seconds",
                    ):
                        if key in dy_data:
                            setattr(cfg.sources.douyin, key, int(dy_data[key]))

                yt_data = sources_data.get("youtube")
                if isinstance(yt_data, dict):
                    if "enabled" in yt_data:
                        cfg.sources.youtube.enabled = _as_bool(yt_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_trending_budget",
                        "daily_channel_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in yt_data:
                            setattr(cfg.sources.youtube, key, int(yt_data[key]))

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            scheduler_int_limits = {
                "refresh_check_interval_seconds": (
                    _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
                    15,
                    None,
                ),
                "signal_event_threshold": (_DEFAULT_SIGNAL_EVENT_THRESHOLD, 1, None),
                "trending_refresh_hours": (_DEFAULT_TRENDING_REFRESH_HOURS, 1, None),
                "explore_refresh_hours": (_DEFAULT_EXPLORE_REFRESH_HOURS, 1, None),
                "discovery_limit": (_DEFAULT_DISCOVERY_LIMIT, 1, 60),
                "proactive_push_interval_seconds": (
                    _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
                    30,
                    None,
                ),
                "speculator_idle_interval_minutes": (
                    _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
                    5,
                    None,
                ),
                "feedback_batch_threshold": (
                    _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
                    1,
                    None,
                ),
                "avoidance_speculation_interval_minutes": (10, 1, None),
                "avoidance_speculation_ttl_days": (3, 1, None),
                "avoidance_speculation_cooldown_days": (7, 1, None),
                "avoidance_speculation_confirmation_threshold": (3, 1, None),
                "avoidance_speculation_max_active": (5, 1, None),
            }
            for key in (
                "enabled",
                "pause_on_extension_disconnect",
                "extension_disconnect_grace_seconds",
                "discovery_cron",
                "pool_target_count",
                "account_sync_interval_hours",
                "refresh_check_interval_seconds",
                "signal_event_threshold",
                "trending_refresh_hours",
                "explore_refresh_hours",
                "discovery_limit",
                "proactive_push_interval_seconds",
                "speculator_idle_interval_minutes",
                "speculation_interval_minutes",
                "speculation_ttl_days",
                "speculation_cooldown_days",
                "speculation_confirmation_threshold",
                "speculation_max_active",
                "speculation_max_primary_interests",
                "speculation_max_secondary_interests",
                "avoidance_speculation_interval_minutes",
                "avoidance_speculation_ttl_days",
                "avoidance_speculation_cooldown_days",
                "avoidance_speculation_confirmation_threshold",
                "avoidance_speculation_max_active",
                "auto_update_enabled",
                "auto_update_check_interval_hours",
                "feedback_batch_threshold",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if key == "extension_disconnect_grace_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_extension_disconnect_grace(sdata[key]),
                        )
                    elif key in scheduler_int_limits:
                        default, min_value, max_value = scheduler_int_limits[key]
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_scheduler_int(
                                sdata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )
                    elif isinstance(current_val, bool):
                        setattr(cfg.scheduler, key, _as_bool(sdata[key]))
                    elif isinstance(current_val, int):
                        setattr(cfg.scheduler, key, int(sdata[key]))
                    else:
                        setattr(cfg.scheduler, key, str(sdata[key]))
            if "pool_source_shares" in sdata:
                cfg.scheduler.pool_source_shares = _normalize_pool_source_shares(
                    sdata["pool_source_shares"]
                )

        # Apply storage updates
        if "storage" in update:
            stdata = update["storage"]
            if "db_path" in stdata:
                cfg.storage.db_path = str(stdata["db_path"])

        # Apply logging updates
        if "logging" in update:
            ldata = update["logging"]
            for key in ("level", "file_level", "directory", "filename"):
                if key in ldata:
                    setattr(cfg.logging, key, str(ldata[key]))
            for key in (
                "max_file_size_mb",
                "backup_count",
                "aggregate_budget_mb",
                "unmanaged_truncate_mb",
                "unmanaged_max_age_days",
            ):
                if key in ldata:
                    setattr(cfg.logging, key, int(ldata[key]))

        for field in reset_fields:
            target = _RESETTABLE_CONFIG_FIELDS[field]
            section = getattr(cfg, target[0])
            subsection = getattr(section, target[1])
            setattr(subsection, target[2], "")

        issues = _validate_llm_buildable(cfg, _collect_config_issues(cfg))
        if any(getattr(issue, "severity", "warning") == "blocking" for issue in issues):
            response = ConfigUpdateResponse(
                ok=False,
                config=_config_to_response(
                    cfg,
                    issues,
                    mask_keys=True,
                    degraded=bool(getattr(ctx, "degraded", False)),
                    degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                ),
                message="配置校验失败，未写入 config.toml。",
                reloaded=False,
                rollback_applied=False,
                restart_required=False,
            )
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json"),
            )

        async with _CONFIG_SAVE_LOCK:
            config_path = _default_config_path()
            try:
                backup_path = _snapshot_config_file(config_path)
            except Exception as exc:
                logger.exception("Config snapshot failed — refusing to overwrite config.toml")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "config_snapshot_failed",
                        "message": f"couldn't snapshot config, refusing to risk overwrite: {exc}",
                    },
                )

            saved_path = save_config(cfg)
            logger.info("Configuration saved to %s", saved_path)

            if bool(getattr(ctx, "degraded", False)):
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(
                        cfg,
                        issues,
                        mask_keys=True,
                        degraded=True,
                        degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                    ),
                    message=(
                        f"配置已保存到 {saved_path}。当前后端处于降级模式，"
                        "请 restart daemon 后让新配置生效。"
                    ),
                    reloaded=False,
                    rollback_applied=False,
                    restart_required=True,
                )

            # ── Hot-reload: rebuild runtime components ──────────────
            reload_message = f"配置已保存到 {saved_path}。"
            try:
                await ctx.rebuild_from_config(cfg)
                await ctx.restart_background_tasks(app)
                reload_message += " 运行时组件已热重载，新配置立即生效。"
                logger.info("Config hot-reload succeeded")
                # Notify WebSocket subscribers so the extension re-fetches data
                with suppress(Exception):
                    await ctx.event_hub.publish(
                        {
                            "type": "config_reloaded",
                            "message": "配置已热重载，运行时组件已重建。",
                        }
                    )
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(cfg, issues, mask_keys=True),
                    message=reload_message,
                    reloaded=True,
                    rollback_applied=False,
                    restart_required=False,
                )
            except Exception as exc:
                logger.exception("Config hot-reload failed — attempting config rollback")
                if backup_path is None:
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），未找到可回滚的 config.toml.bak。"
                    )
                    rollback_cfg = cfg
                    rollback_applied = False
                else:
                    try:
                        _restore_config_snapshot(backup_path, saved_path)
                    except Exception as restore_exc:
                        logger.critical(
                            "Config rollback failed after hot-reload exception",
                            exc_info=True,
                        )
                        return JSONResponse(
                            status_code=500,
                            content={
                                "error": "config_persistence_corrupted",
                                "message": (
                                    "config.toml may be in inconsistent state after hot-reload "
                                    f"failure and rollback failure: {restore_exc}"
                                ),
                                "manual_recovery": (
                                    "config.toml may be in inconsistent state; if "
                                    "config.toml.bak exists, manually copy it back."
                                ),
                            },
                        )
                    rollback_cfg = load_config(saved_path)
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），已从 config.toml.bak 回滚。"
                    )
                    rollback_applied = True

                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(rollback_cfg, _collect_config_issues(rollback_cfg)),
                    message=reload_message + rollback_message,
                    reloaded=False,
                    rollback_applied=rollback_applied,
                    restart_required=False,
                )

    def _normalize_enabled_sources_override(
        raw_enabled: dict[str, bool] | None,
        fallback: dict[str, bool],
    ) -> dict[str, bool]:
        if raw_enabled is None:
            return fallback
        enabled: dict[str, bool] = {}
        for source in _SOURCE_SHARE_ORDER:
            enabled[source] = bool(raw_enabled.get(source, fallback.get(source, False)))
        return {source: enabled.get(source, False) for source in _SOURCE_SHARE_ORDER}

    def _build_source_share_suggestion_response(
        payload: SourceShareSuggestionIn | None = None,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from observed platform event counts."""
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.source_policy import (
            source_enabled_map,
            suggest_pool_source_shares,
        )

        cfg = load_config()
        event_counts = _count_events_by_source_platform(ctx.database)
        enabled_sources = _normalize_enabled_sources_override(
            payload.enabled_sources if payload else None,
            source_enabled_map(cfg),
        )
        suggested_shares = suggest_pool_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=(
                payload.configured_shares
                if payload and payload.configured_shares is not None
                else cfg.scheduler.pool_source_shares
            ),
        )
        return SourceShareSuggestionResponse(
            event_counts=event_counts,
            enabled_sources=enabled_sources,
            suggested_shares=suggested_shares,
        )

    @app.get(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion() -> SourceShareSuggestionResponse:
        """Suggest pool source shares from saved config switches."""
        return _build_source_share_suggestion_response()

    @app.post(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion_for_form(
        payload: SourceShareSuggestionIn,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from unsaved settings form state."""
        return _build_source_share_suggestion_response(payload)

    # v0.3.57+: one-shot purge of self-authored xhs pool rows that
    # accumulated before the per-path filter was wired in. No-op on
    # fresh installs (no persisted self_info → nothing to scan against);
    # repairs the pool the first time the user upgrades after having
    # browsed XHS while logged in.
    _existing_self_info = _load_xhs_self_info()
    if _existing_self_info:
        _purged = _purge_self_authored_pool_items(ctx.database, _existing_self_info)
        if _purged:
            logger.info(
                "startup purge: suppressed %d self-authored xhs pool item(s) (nickname=%r)",
                _purged,
                _existing_self_info.get("nickname", ""),
            )

    # ── Mobile Web UI ───────────────────────────────────────────
    from pathlib import Path as _Path

    from fastapi.staticfiles import StaticFiles as _StaticFiles

    _web_dir = _Path(__file__).resolve().parent.parent / "web"
    if _web_dir.is_dir():
        _favicon_path = _web_dir / "icon-192.png"

        @app.get("/favicon.ico", include_in_schema=False)
        def _favicon() -> FileResponse:
            if not _favicon_path.is_file():
                raise HTTPException(status_code=404, detail="favicon not found")
            return FileResponse(_favicon_path, media_type="image/png")

        app.mount("/m", _StaticFiles(directory=_web_dir, html=True), name="mobile-web")

    # ── Desktop Web UI ───────────────────────────────────────────
    _desktop_dir = _Path(__file__).resolve().parent.parent / "web" / "desktop"
    if _desktop_dir.is_dir():
        app.mount("/web", _StaticFiles(directory=_desktop_dir, html=True), name="desktop-web")

        @app.get("/", include_in_schema=False)
        def _root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/web", status_code=302)

    return app
