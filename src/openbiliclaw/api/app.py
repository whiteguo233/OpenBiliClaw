"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    BehaviorEventBatchIn,
    ChatIn,
    ChatResponse,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    CognitionUpdateSummary,
    EventIngestResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    NotificationAckIn,
    NotificationAckResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    ProfileSummaryResponse,
    RecommendationAppendIn,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RecommendationReshuffleResponse,
    RuntimeStatusResponse,
)

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}


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
) -> FastAPI:
    """Create the local backend API app."""
    app = FastAPI(title="OpenBiliClaw API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if memory_manager is None or database is None or soul_engine is None:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config
        from openbiliclaw.discovery.engine import (
            ContentDiscoveryEngine,
            DiscoveryConcurrencyController,
        )
        from openbiliclaw.discovery.strategies.strategies import (
            ExploreStrategy,
            RelatedChainStrategy,
            SearchStrategy,
            TrendingStrategy,
        )
        from openbiliclaw.llm import build_llm_registry
        from openbiliclaw.llm.service import LLMService
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.recommendation.engine import RecommendationEngine
        from openbiliclaw.runtime.account_sync import AccountSyncService
        from openbiliclaw.runtime.events import RuntimeEventHub
        from openbiliclaw.runtime.refresh import ContinuousRefreshController
        from openbiliclaw.soul.dialogue import SocraticDialogue
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.storage.database import Database

        config = load_config()
        llm_registry = build_llm_registry(config)
        created_runtime_database = False
        if database is None:
            database = Database(config.data_path / "openbiliclaw.db")
            database.initialize()
            created_runtime_database = True
        if memory_manager is None:
            shared_database = database if created_runtime_database else None
            memory_manager = MemoryManager(config.data_path, database=shared_database)
            memory_manager.initialize()
        if soul_engine is None:
            soul_engine = SoulEngine(
                llm=llm_registry,  # type: ignore[arg-type]
                memory=memory_manager,
            )
        llm_service = LLMService(registry=llm_registry, memory=memory_manager)
        if recommendation_engine is None:
            from openbiliclaw.recommendation.curator import PoolCurator

            curator = PoolCurator(database)
            # Build embedding service for semantic dedup (optional, reused below)
            _rec_embedding = None
            try:
                from openbiliclaw.llm.embedding import EmbeddingService
                from openbiliclaw.llm.gemini_provider import GeminiProvider
                _g = registry.get("gemini")
                if isinstance(_g, GeminiProvider):
                    _rec_embedding = EmbeddingService(_g)
            except Exception:
                pass
            recommendation_engine = RecommendationEngine(
                llm=llm_service, database=database, curator=curator,
                embedding_service=_rec_embedding,
            )
        bilibili_client = BilibiliAPIClient(
            cookie=resolve_runtime_cookie(
                data_dir=config.data_path,
                configured_cookie=config.bilibili.cookie,
            )
        )
        if runtime_controller is None:
            concurrency = DiscoveryConcurrencyController(
                bilibili_request_concurrency=2,
                llm_evaluation_concurrency=2,
            )
            # Build embedding service for semantic topic dedup (optional)
            _embedding_service = None
            try:
                from openbiliclaw.llm.embedding import EmbeddingService
                from openbiliclaw.llm.gemini_provider import GeminiProvider
                _gemini = registry.get("gemini")
                if isinstance(_gemini, GeminiProvider):
                    _embedding_service = EmbeddingService(_gemini)
            except Exception:
                pass

            discovery_engine = ContentDiscoveryEngine(
                llm_service=llm_service,
                database=database,
                concurrency=concurrency,
                embedding_service=_embedding_service,
            )
            search_strategy = SearchStrategy(
                llm_service=llm_service,
                bilibili_client=bilibili_client,
                concurrency=concurrency,
            )
            trending_strategy = TrendingStrategy(
                bilibili_client=bilibili_client,
                llm_service=llm_service,
                concurrency=concurrency,
            )
            related_strategy = RelatedChainStrategy(
                bilibili_client=bilibili_client,
                llm_service=llm_service,
                memory_manager=cast("Any", memory_manager),
                search_strategy=search_strategy,
                trending_strategy=trending_strategy,
                concurrency=concurrency,
            )
            explore_strategy = ExploreStrategy(
                llm_service=llm_service,
                bilibili_client=bilibili_client,
                concurrency=concurrency,
            )
            discovery_engine.register_strategy(search_strategy)
            discovery_engine.register_strategy(trending_strategy)
            discovery_engine.register_strategy(related_strategy)
            discovery_engine.register_strategy(explore_strategy)
            runtime_controller = ContinuousRefreshController(
                memory_manager=memory_manager,
                database=database,
                soul_engine=soul_engine,
                discovery_engine=discovery_engine,
                recommendation_engine=recommendation_engine,
                pool_target_count=config.scheduler.pool_target_count,
                event_hub=runtime_event_hub or RuntimeEventHub(),
            )
        if account_sync_service is None:
            account_sync_service = AccountSyncService(
                memory_manager=memory_manager,
                bilibili_client=bilibili_client,
                soul_engine=soul_engine,
                sync_interval_hours=config.scheduler.account_sync_interval_hours,
            )
        if runtime_event_hub is None:
            runtime_event_hub = getattr(runtime_controller, "event_hub", None)
        if dialogue is None:
            dialogue = SocraticDialogue(
                llm=None,
                soul_engine=soul_engine,
                llm_service=llm_service,
                session="popup",
            )

    if dialogue is None:
        from openbiliclaw.soul.dialogue import SocraticDialogue

        dialogue = SocraticDialogue(llm=None, soul_engine=soul_engine, session="popup")
    if runtime_event_hub is None:
        from openbiliclaw.runtime.events import RuntimeEventHub

        runtime_event_hub = RuntimeEventHub()

    async def _run_post_feedback_tasks() -> None:
        with suppress(Exception):
            await soul_engine.process_feedback_batch_if_needed()
        refresh_after_feedback = getattr(runtime_controller, "refresh_after_feedback", None)
        if callable(refresh_after_feedback):
            with suppress(Exception):
                await refresh_after_feedback()

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="openbiliclaw-api")

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
            )
            for item in items
        ]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        subscribe = getattr(runtime_event_hub, "subscribe", None)
        unsubscribe = getattr(runtime_event_hub, "unsubscribe", None)
        if not callable(subscribe) or not callable(unsubscribe):
            await websocket.close()
            return
        queue = await subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            await unsubscribe(queue)

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        run_forever = getattr(runtime_controller, "run_forever", None)
        if runtime_controller is None or not callable(run_forever):
            app.state.refresh_task = None
        else:
            app.state.refresh_task = asyncio.create_task(run_forever())
        sync_forever = getattr(account_sync_service, "run_forever", None)
        if account_sync_service is None or not callable(sync_forever):
            app.state.account_sync_task = None
            return
        app.state.account_sync_task = asyncio.create_task(sync_forever())
        # Trigger speculator on startup to ensure speculations exist
        if soul_engine is not None:
            try:
                profile = await soul_engine.get_profile()
                speculator = getattr(soul_engine, "_speculator", None)
                if speculator is not None:
                    await speculator.force_tick(profile)
            except Exception:
                pass  # Profile not initialized yet — skip silently

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

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary(
        limit: int = Query(default=3, ge=1, le=20),
        cursor: str = "",
    ) -> ProfileSummaryResponse:
        try:
            profile = await soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        from openbiliclaw.api.models import (
            ContextModeOut,
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
        if mbti_obj is not None and getattr(mbti_obj, "type", ""):
            mbti_out = MBTIOut(
                type=str(mbti_obj.type),
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
        load_cognition_updates = getattr(memory_manager, "load_cognition_updates", None)
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
            cognition_updates = [
                _normalize_cognition_update(item)
                for item in sliced_updates
            ]

        # ── Speculative interests ──
        spec_items: list[SpeculativeInterestOut] = []
        try:
            spec_state = load_speculative_state(config.data_path)
            spec_items = [
                SpeculativeInterestOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
                    confirmation_count=item.confirmation_count,
                    confirmation_threshold=item.confirmation_threshold,
                    status=item.status,
                )
                for item in spec_state.active[:6]
            ]
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

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
        )

    @app.post("/api/events", response_model=EventIngestResponse)
    async def ingest_events(payload: BehaviorEventBatchIn) -> EventIngestResponse:
        accepted = 0
        for item in payload.events:
            event = {
                "event_type": item.type,
                "url": item.url,
                "title": item.title,
                "context": item.context,
                "metadata": {
                    **item.metadata,
                    "timestamp": item.timestamp,
                },
            }
            await memory_manager.propagate_event(event)
            accepted += 1
        refresh_after_event_ingest = getattr(runtime_controller, "refresh_after_event_ingest", None)
        if callable(refresh_after_event_ingest):
            with suppress(Exception):
                await refresh_after_event_ingest()
        return EventIngestResponse(accepted=accepted)

    @app.get("/api/recommendations", response_model=RecommendationListResponse)
    async def recommendations() -> RecommendationListResponse:
        rows = database.get_recommendations(limit=20)
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
                )
                for row in rows
            ]
        )

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed() -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        runtime_status: dict[str, object] = {}
        get_runtime_status = getattr(runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            runtime_status = dict(get_runtime_status())
        get_account_sync_status = getattr(account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            runtime_status.update(get_account_sync_status())

        cognition_updates: list[dict[str, object]] = []
        load_cognition_updates = getattr(memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            cognition_updates = [
                item for item in load_cognition_updates() if isinstance(item, dict)
            ]

        builder = ActivityFeedBuilder(database=database)
        payload = builder.build(
            runtime_status=runtime_status,
            cognition_updates=cognition_updates,
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
        )

    async def _trigger_replenishment_if_needed() -> None:
        """Fire a background Discovery refresh when the pool runs low."""
        curator = getattr(recommendation_engine, "_curator", None)
        if curator is None or not hasattr(curator, "needs_replenishment"):
            return
        if not curator.needs_replenishment():
            return
        trigger = getattr(runtime_controller, "trigger_manual_refresh", None)
        if callable(trigger):
            logger.info("Pool low — triggering automatic replenishment")
            asyncio.create_task(trigger())

    @app.post("/api/recommendations/reshuffle", response_model=RecommendationReshuffleResponse)
    async def reshuffle_recommendations() -> RecommendationReshuffleResponse:
        if recommendation_engine is None or soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await recommendation_engine.reshuffle_recommendations(profile=profile, limit=10)
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/append", response_model=RecommendationReshuffleResponse)
    async def append_recommendations(
        payload: RecommendationAppendIn,
    ) -> RecommendationReshuffleResponse:
        if recommendation_engine is None or soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await recommendation_engine.append_recommendations(
            profile=profile,
            excluded_bvids=payload.excluded_bvids,
            limit=10,
        )
        await _trigger_replenishment_if_needed()
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/refresh", response_model=RecommendationRefreshResponse)
    async def refresh_recommendations() -> RecommendationRefreshResponse:
        trigger_manual_refresh = getattr(runtime_controller, "trigger_manual_refresh", None)
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
        get_runtime_status = getattr(runtime_controller, "get_runtime_status", None)
        if not callable(get_runtime_status):
            return RuntimeStatusResponse(
                initialized=False,
                recommendation_count=0,
                pending_signal_events=0,
                unread_count=0,
            )
        payload = dict(get_runtime_status())
        get_account_sync_status = getattr(account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        return RuntimeStatusResponse(**payload)

    @app.get("/api/notifications/pending", response_model=PendingNotificationResponse)
    async def pending_notification() -> PendingNotificationResponse:
        get_pending_notification = getattr(runtime_controller, "get_pending_notification", None)
        item = get_pending_notification() if callable(get_pending_notification) else None
        if item is None:
            get_notification_candidate = getattr(database, "get_notification_candidate", None)
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
        load_cognition_updates = getattr(memory_manager, "load_cognition_updates", None)
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
        load_cognition_updates = getattr(memory_manager, "load_cognition_updates", None)
        save_cognition_updates = getattr(memory_manager, "save_cognition_updates", None)
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

    @app.post("/api/notifications/sent", response_model=NotificationAckResponse)
    async def mark_notification_sent(payload: NotificationAckIn) -> NotificationAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Notification bvid is required.")
        mark_sent = getattr(runtime_controller, "mark_notification_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            database.mark_notification_sent(bvid)
        return NotificationAckResponse(ok=True, bvid=bvid)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatIn) -> ChatResponse:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        reply = await dialogue.respond(message)
        return ChatResponse(reply=reply)

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        database.update_recommendation_feedback(
            payload.recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
        await memory_manager.propagate_event(
            {
                "event_type": "feedback",
                "title": str(recommendation.get("title", "")),
                "metadata": {
                    "recommendation_id": payload.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": feedback_type,
                    "feedback_note": note,
                },
            }
        )
        record_immediate_feedback_cognition = getattr(
            soul_engine,
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

    return app
