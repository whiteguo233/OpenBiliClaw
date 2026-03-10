"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    BehaviorEventBatchIn,
    ChatIn,
    ChatResponse,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
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
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RuntimeStatusResponse,
)


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
    dialogue: Any | None = None,
    runtime_controller: Any | None = None,
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
        from openbiliclaw.discovery.engine import ContentDiscoveryEngine
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
        from openbiliclaw.runtime.refresh import ContinuousRefreshController
        from openbiliclaw.soul.dialogue import SocraticDialogue
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.storage.database import Database

        config = load_config()
        llm_registry = build_llm_registry(config)
        if memory_manager is None:
            memory_manager = MemoryManager(config.data_path)
            memory_manager.initialize()
        if database is None:
            database = Database(config.data_path / "openbiliclaw.db")
            database.initialize()
        if soul_engine is None:
            soul_engine = SoulEngine(
                llm=llm_registry,  # type: ignore[arg-type]
                memory=memory_manager,
            )
        llm_service = LLMService(registry=llm_registry, memory=memory_manager)
        if runtime_controller is None:
            bilibili_client = BilibiliAPIClient(
                cookie=resolve_runtime_cookie(
                    data_dir=config.data_path,
                    configured_cookie=config.bilibili.cookie,
                )
            )
            discovery_engine = ContentDiscoveryEngine(
                llm_service=llm_service,
                database=database,
            )
            search_strategy = SearchStrategy(
                llm_service=llm_service,
                bilibili_client=bilibili_client,
            )
            trending_strategy = TrendingStrategy(
                bilibili_client=bilibili_client,
                llm_service=llm_service,
            )
            related_strategy = RelatedChainStrategy(
                bilibili_client=bilibili_client,
                llm_service=llm_service,
                memory_manager=cast("Any", memory_manager),
                search_strategy=search_strategy,
                trending_strategy=trending_strategy,
            )
            explore_strategy = ExploreStrategy(
                llm_service=llm_service,
                bilibili_client=bilibili_client,
            )
            discovery_engine.register_strategy(search_strategy)
            discovery_engine.register_strategy(trending_strategy)
            discovery_engine.register_strategy(related_strategy)
            discovery_engine.register_strategy(explore_strategy)
            recommendation_engine = RecommendationEngine(llm=llm_service, database=database)
            runtime_controller = ContinuousRefreshController(
                memory_manager=memory_manager,
                database=database,
                soul_engine=soul_engine,
                discovery_engine=discovery_engine,
                recommendation_engine=recommendation_engine,
            )
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

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="openbiliclaw-api")

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        run_forever = getattr(runtime_controller, "run_forever", None)
        if runtime_controller is None or not callable(run_forever):
            return
        app.state.refresh_task = asyncio.create_task(run_forever())

    @app.on_event("shutdown")
    async def shutdown_refresh_loop() -> None:
        refresh_task = getattr(app.state, "refresh_task", None)
        if refresh_task is None:
            return
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary() -> ProfileSummaryResponse:
        try:
            profile = await soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        top_interests = [item.name for item in profile.preferences.interests[:5] if item.name]
        cognition_updates = []
        load_cognition_updates = getattr(memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            raw_updates = [
                item
                for item in load_cognition_updates()
                if isinstance(item, dict) and str(item.get("summary", "")).strip()
            ]
            raw_updates.sort(key=lambda item: bool(item.get("notified", False)))
            cognition_updates = [
                str(item.get("summary", "")).strip()
                for item in raw_updates
            ][:3]
        return ProfileSummaryResponse(
            initialized=True,
            personality_portrait=profile.personality_portrait,
            core_traits=profile.core_traits[:5],
            deep_needs=profile.deep_needs[:5],
            top_interests=top_interests,
            recent_cognition_updates=cognition_updates,
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
                    expression=str(row.get("expression", "")),
                    topic_label=str(row.get("topic", "")),
                    presented=bool(row.get("presented", 0)),
                )
                for row in rows
            ]
        )

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
        return RuntimeStatusResponse(**get_runtime_status())

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
        with suppress(Exception):
            await soul_engine.process_feedback_batch_if_needed()
        refresh_after_feedback = getattr(runtime_controller, "refresh_after_feedback", None)
        if callable(refresh_after_feedback):
            with suppress(Exception):
                await refresh_after_feedback()
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    return app
