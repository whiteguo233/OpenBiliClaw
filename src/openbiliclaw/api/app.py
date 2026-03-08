"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    BehaviorEventBatchIn,
    EventIngestResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    RecommendationListResponse,
    RecommendationOut,
)


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
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
        from openbiliclaw.config import load_config
        from openbiliclaw.llm import build_llm_registry
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.storage.database import Database

        config = load_config()
        if memory_manager is None:
            memory_manager = MemoryManager(config.data_path)
            memory_manager.initialize()
        if database is None:
            database = Database(config.data_path / "openbiliclaw.db")
            database.initialize()
        if soul_engine is None:
            soul_engine = SoulEngine(
                llm=build_llm_registry(config),  # type: ignore[arg-type]
                memory=memory_manager,
            )

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="openbiliclaw-api")

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
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    return app
