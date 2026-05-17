"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    BehaviorEventBatchIn,
    BilibiliConfigOut,
    BilibiliCookieIn,
    BilibiliCookieResponse,
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

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}

_SOURCE_SHARE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube")


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
    from fastapi.responses import JSONResponse

    from openbiliclaw.api.runtime_context import RuntimeContext, build_runtime_context
    from openbiliclaw.config import load_config

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
        ctx = build_runtime_context(
            config,
            memory_manager=memory_manager,
            database=database,
            event_hub=runtime_event_hub,
        )
    app.state.runtime_context = ctx

    async def _run_post_feedback_tasks() -> None:
        with suppress(Exception):
            await ctx.soul_engine.process_feedback_batch_if_needed()
        refresh_after_feedback = getattr(ctx.runtime_controller, "refresh_after_feedback", None)
        if callable(refresh_after_feedback):
            with suppress(Exception):
                await refresh_after_feedback()

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

    chat_turn_lock = asyncio.Lock()
    fallback_chat_turns: dict[str, dict[str, Any]] = {}
    running_chat_turn_tasks: set[str] = set()

    def _normalize_chat_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if normalized in {"chat", "delight", "probe"}:
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

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="openbiliclaw-api")

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
                content_id=str(getattr(item.content, "content_id", "") or item.content.bvid),
                content_url=str(getattr(item.content, "content_url", "") or ""),
                source_platform=str(getattr(item.content, "source_platform", "") or "bilibili"),
            )
            for item in items
        ]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        await websocket.accept()
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
            SpeculativeInterestOut,
            StylePreferenceOut,
        )
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
        try:
            spec_state = load_speculative_state(ctx.config.data_path)
            from openbiliclaw.api.models import SpeculativeSpecificOut

            # Filter status="active" only — confirmed/rejected items are
            # technically still in spec_state.active until force_tick rotates
            # them out, but the popup should not surface them: a user who
            # clicked 喜欢 has already given their answer and expects the
            # row to disappear, not to re-render with a "已确认" tag.
            active_specs = [item for item in spec_state.active if item.status == "active"]
            spec_items = [
                SpeculativeInterestOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
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
                for item in active_specs[:6]
            ]
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

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
            await asyncio.sleep(3)
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
        # Pause discovery LLM calls and wait for RPM window to clear
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
            await asyncio.sleep(3)  # Let RPM window drain
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
                    "source": "interest_probe",
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

    def _probe_metadata_from_active_speculation(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active probe metadata before confirm/reject mutates state."""
        from openbiliclaw.soul.speculator import build_probe_axis

        get_active = getattr(speculator, "get_active_speculations", None)
        if not callable(get_active):
            return {"domain": domain}
        try:
            active_specs = list(get_active())
        except Exception:
            logger.debug("Failed to read active probe metadata", exc_info=True)
            return {"domain": domain}

        for spec in active_specs:
            spec_domain = str(getattr(spec, "domain", "")).strip()
            if spec_domain.lower() != domain.lower():
                continue
            specifics = [
                str(getattr(item, "name", "")).strip()
                for item in getattr(spec, "specifics", [])
                if str(getattr(item, "name", "")).strip()
            ]
            axis = build_probe_axis(
                experience_mode=getattr(spec, "experience_mode", ""),
                entry_load=getattr(spec, "entry_load", ""),
            )
            metadata: dict[str, object] = {
                "domain": spec_domain or domain,
                "category": str(getattr(spec, "category", "")).strip(),
                "reason": str(getattr(spec, "reason", "")).strip(),
            }
            if axis:
                metadata["axis"] = axis
            if specifics:
                metadata["specifics"] = specifics
            return metadata
        return {"domain": domain}

    def _record_probe_feedback_history(
        domain: str,
        response: str,
        *,
        speculator: Any,
        message: str = "",
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
            entry = _probe_metadata_from_active_speculation(speculator, domain)
            entry["response"] = response
            if message:
                entry["message"] = message
            state["probe_feedback_history"] = append_probe_feedback_history(
                state.get("probe_feedback_history", []),
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
        """Judge whether the user's probe chat is positive, negative, or neutral.

        Uses LLM first; falls back to keyword detection on failure.
        Returns: "positive", "negative", or "neutral".
        """
        # Try LLM judgment
        llm_result = await _llm_judge_sentiment(user_message, ai_reply, domain)
        if llm_result in ("positive", "negative"):
            return llm_result
        # Fallback: keyword detection
        return _keyword_judge_sentiment(user_message)

    def _keyword_judge_sentiment(user_message: str) -> str:
        """Fallback keyword-based sentiment detection."""
        msg = user_message.lower()
        neg = {
            "不喜欢",
            "太硬",
            "太艰涩",
            "没兴趣",
            "不感兴趣",
            "不想看",
            "太深",
            "太学术",
            "无聊",
            "不行",
            "算了",
            "不要",
            "讨厌",
        }
        pos = {
            "有意思",
            "感兴趣",
            "想看看",
            "挺好",
            "可以",
            "继续",
            "不错",
            "有点意思",
            "想了解",
            "喜欢",
        }
        if any(kw in msg for kw in neg):
            return "negative"
        if any(kw in msg for kw in pos):
            return "positive"
        return "neutral"

    async def _llm_judge_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """LLM-based sentiment judgment. Returns positive/negative/neutral."""
        if ctx.recommendation_engine is None:
            return "neutral"
        llm = getattr(ctx.recommendation_engine, "_llm", None)
        if llm is None:
            return "neutral"
        try:
            response = await asyncio.wait_for(
                llm.complete_structured_task(
                    system_instruction=(
                        "任务：判断用户对一个兴趣方向的态度。\n\n"
                        "规则：\n"
                        "1. 只输出一个英文单词：positive 或 negative 或 neutral\n"
                        "2. 不要输出任何其他内容\n\n"
                        "判断标准：\n"
                        "- positive = 用户表达了兴趣、想了解、觉得有意思\n"
                        "- negative = 用户表达了不喜欢、不感兴趣、太难、太无聊\n"
                        "- neutral = 态度不明确\n"
                    ),
                    user_input=f"方向：{domain}\n用户：{user_message}",
                    max_tokens=8,
                    temperature=0.0,
                    caller="api.sentiment",
                ),
                timeout=15,
            )
            raw = str(getattr(response, "content", "")).strip().lower()
            # Extract the first recognizable word
            for word in raw.split():
                cleaned = word.strip("\"'.,:;!?")
                if cleaned in ("negative", "positive", "neutral"):
                    logger.info("Sentiment LLM for '%s': %s (raw=%r)", domain, cleaned, raw)
                    return cleaned
            logger.info(
                "Sentiment LLM for '%s': unrecognized (raw=%r), trying keywords", domain, raw
            )
            return "neutral"
        except Exception:
            logger.info("Sentiment LLM for '%s' failed, trying keywords", domain)
            return "neutral"

    def _contextual_chat_message(turn: ChatTurnOut) -> str:
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id or "这条惊喜推荐"
            return f"[关于惊喜推荐「{label}」的反馈] {turn.message}"
        if turn.scope == "probe":
            label = turn.subject_title or turn.subject_id or "这个方向"
            return f"[关于猜测兴趣「{label}」的反馈] {turn.message}"
        return turn.message

    async def _generate_durable_chat_reply(turn: ChatTurnOut) -> str:
        if ctx.dialogue is None:
            return "对话引擎暂不可用。"

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
            await asyncio.sleep(3)
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
            sentiment = await _judge_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_speculator", None)
            if sentiment == "negative":
                if speculator is not None:
                    with suppress(Exception):
                        speculator.user_reject_speculation(domain, cooldown_days=14)
                summary = f"你对「{domain}」的反馈偏负面（{turn.message}），已暂时搁置 14 天。"
            elif sentiment == "positive":
                if speculator is not None:
                    with suppress(Exception):
                        speculator.observe(
                            [
                                {
                                    "event_type": "dialogue",
                                    "title": domain,
                                    "metadata": {
                                        "user_message": turn.message,
                                        "source": "probe_chat",
                                    },
                                }
                            ]
                        )
                summary = f"你对「{domain}」表示了兴趣，确认度 +1。"
            else:
                summary = f"关于「{domain}」你说：{turn.message}"
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("interest.chat", summary, domain)

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
            _record_probe_feedback_history(
                domain,
                "confirm",
                speculator=speculator,
            )
            ok = speculator.user_confirm_speculation(domain)
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
        # Pause discovery LLM calls and wait for RPM window to clear
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
            await asyncio.sleep(3)
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            # Judge sentiment while discovery is still paused
            sentiment = await _judge_probe_sentiment(raw_message, reply, domain)
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

        chat_response = (
            f"chat_{sentiment}" if sentiment in {"positive", "negative"} else "chat_neutral"
        )
        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
        )

        if sentiment == "negative":
            speculator.user_reject_speculation(domain, cooldown_days=14)
            summary = f"你对「{domain}」的反馈偏负面（{raw_message}），已暂时搁置 14 天。"
        elif sentiment == "positive":
            speculator.observe(
                [
                    {
                        "event_type": "dialogue",
                        "title": domain,
                        "metadata": {"user_message": raw_message, "source": "probe_chat"},
                    }
                ]
            )
            summary = f"你对「{domain}」表示了兴趣，确认度 +1。"
        else:
            summary = f"关于「{domain}」你说：{raw_message}"

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

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment"}:
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
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "")).strip()
            title = title or str(recommendation.get("title", "")).strip()
            topic_label = topic_label or str(recommendation.get("topic_label", "")).strip()
            up_name = up_name or str(recommendation.get("up_name", "")).strip()

        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")

        # Persist the click as an event so history/query paths can see it.
        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            build_event,
        )

        click_extra_parts: list[str] = []
        if topic_label:
            click_extra_parts.append(f"主题:{topic_label}")
        if up_name:
            click_extra_parts.append(f"UP:{up_name}")
        click_context = f"在 B 站点开了《{title}》"
        if click_extra_parts:
            click_context = f"{click_context}({','.join(click_extra_parts)})"
        click_metadata: dict[str, object] = {
            "recommendation_id": payload.recommendation_id,
            "bvid": bvid,
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
                    source_platform=SOURCE_BILIBILI,
                    title=title,
                    url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    author=up_name,
                    context=click_context,
                    metadata=click_metadata,
                )
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
                # Filter self-authored notes from event propagation —
                # otherwise the user's own posts get treated as their
                # own "favorite/like" signals and warp the soul profile.
                propagated = 0
                skipped_self = 0
                profile_events: list[dict[str, Any]] = []
                for note in added_notes:
                    if _is_self_authored_note(note, self_info_now):
                        skipped_self += 1
                        continue
                    for event in xhs_bootstrap_notes_to_events([note]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        propagated += 1
                await _ingest_profile_update_events(profile_events)
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
                profile_events: list[dict[str, Any]] = []
                for video in added_videos:
                    for event in dy_bootstrap_videos_to_events([video]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                await _ingest_profile_update_events(profile_events)
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
                profile_events: list[dict[str, Any]] = []
                for event in yt_bootstrap_items_to_events(added_items):
                    await ctx.memory_manager.propagate_event(event)
                    profile_events.append(event)
                await _ingest_profile_update_events(profile_events)
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
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
                reasoning_effort=getattr(p, "reasoning_effort", ""),
            )

        issue_list = [ConfigIssueOut(field=i.field, message=i.message) for i in (issues or [])]

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            llm=LLMConfigOut(
                default_provider=cfg.llm.default_provider,
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
                speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
                speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
                speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
                speculation_confirmation_threshold=(
                    cfg.scheduler.speculation_confirmation_threshold
                ),
                speculation_max_active=cfg.scheduler.speculation_max_active,
                speculation_max_primary_interests=(
                    cfg.scheduler.speculation_max_primary_interests
                ),
                speculation_max_secondary_interests=(
                    cfg.scheduler.speculation_max_secondary_interests
                ),
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
            ),
            storage=StorageConfigOut(db_path=cfg.storage.db_path),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
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
        issues = _collect_config_issues(cfg)
        return _config_to_response(cfg, issues, mask_keys=not reveal_keys)

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(payload: ConfigUpdateIn) -> ConfigUpdateResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _collect_config_issues,
            _normalize_extension_disconnect_grace,
            _normalize_pool_source_shares,
            load_config,
            save_config,
        )

        cfg = load_config()
        update = payload.model_dump(exclude_none=True)

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
                    for field_name in (
                        "api_key",
                        "model",
                        "base_url",
                        "http_referer",
                        "x_title",
                        "reasoning_effort",
                    ):
                        if field_name in pdata:
                            setattr(provider_cfg, field_name, str(pdata[field_name]))
            if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
                emb = llm_data["embedding"]
                if "provider" in emb:
                    cfg.llm.embedding.provider = str(emb["provider"])
                if "model" in emb:
                    cfg.llm.embedding.model = str(emb["model"])
                # v0.3.32+ — embedding owns api_key/base_url. Skip the
                # api_key write when the payload echoes back the masked
                # value (e.g. ``sk-d****a826``) so we don't overwrite the
                # real key with asterisks. A genuine API key never
                # contains ``*``.
                if "api_key" in emb:
                    new_key = str(emb["api_key"])
                    if "*" not in new_key:
                        cfg.llm.embedding.api_key = new_key
                if "base_url" in emb:
                    cfg.llm.embedding.base_url = str(emb["base_url"])
                if "similarity_threshold" in emb:
                    cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
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
                if isinstance(yt_data, dict) and "enabled" in yt_data:
                    cfg.sources.youtube.enabled = _as_bool(yt_data["enabled"])

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            for key in (
                "enabled",
                "pause_on_extension_disconnect",
                "extension_disconnect_grace_seconds",
                "discovery_cron",
                "pool_target_count",
                "account_sync_interval_hours",
                "speculation_interval_minutes",
                "speculation_ttl_days",
                "speculation_cooldown_days",
                "speculation_confirmation_threshold",
                "speculation_max_active",
                "speculation_max_primary_interests",
                "speculation_max_secondary_interests",
                "auto_update_enabled",
                "auto_update_check_interval_hours",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if key == "extension_disconnect_grace_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_extension_disconnect_grace(sdata[key]),
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

        # Save to disk
        saved_path = save_config(cfg)
        issues = _collect_config_issues(cfg)
        logger.info("Configuration saved to %s", saved_path)

        # ── Hot-reload: rebuild runtime components ──────────────────
        reloaded = False
        reload_message = f"配置已保存到 {saved_path}。"
        try:
            await ctx.rebuild_from_config(cfg)
            await ctx.restart_background_tasks(app)
            reloaded = True
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
        except Exception as exc:
            logger.exception("Config hot-reload failed — old components remain active")
            reload_message += f" 热重载失败（{exc}），旧组件仍在运行，重启后端可完全生效。"

        return ConfigUpdateResponse(
            ok=True,
            config=_config_to_response(cfg, issues, mask_keys=True),
            message=reload_message,
            reloaded=reloaded,
        )

    def _normalize_enabled_sources_override(
        raw_enabled: dict[str, bool] | None,
        fallback: dict[str, bool],
    ) -> dict[str, bool]:
        if raw_enabled is None:
            return fallback
        enabled = {"bilibili": True}
        for source in _SOURCE_SHARE_ORDER:
            if source == "bilibili":
                continue
            enabled[source] = bool(raw_enabled.get(source, False))
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

    return app
