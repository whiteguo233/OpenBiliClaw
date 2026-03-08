"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openbiliclaw.api.models import (
    BehaviorEventBatchIn,
    EventIngestResponse,
    HealthResponse,
    RecommendationListResponse,
    RecommendationOut,
)


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
) -> FastAPI:
    """Create the local backend API app."""
    app = FastAPI(title="OpenBiliClaw API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if memory_manager is None or database is None:
        from openbiliclaw.config import load_config
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.storage.database import Database

        config = load_config()
        if memory_manager is None:
            memory_manager = MemoryManager(config.data_path)
            memory_manager.initialize()
        if database is None:
            database = Database(config.data_path / "openbiliclaw.db")
            database.initialize()

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

    return app
