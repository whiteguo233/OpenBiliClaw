"""Screen-aware recommendation routes; legacy/off requests keep their old path."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import HTTPException, Query

from openbiliclaw.api.models import (
    DirectorGenerateIn,
    DirectorPresentIn,
    DirectorScreenInfo,
    RecommendationAppendResponse,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationPoolStatus,
)
from openbiliclaw.recommendation.director.store import DirectorStore, StoreConflict

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.recommendation.director.runtime import DirectorRuntime
    from openbiliclaw.recommendation.director.store import BatchInstance


def _stream(base: str, source: str = "") -> str:
    return base if not source else f"{base}:{source}"


def screen_items(batch: BatchInstance | None) -> list[RecommendationOut]:
    """Serialize the exact immutable sequence, never requery a mixed history window."""
    if batch is None:
        return []
    result = []
    for row in batch.items:
        item = dict(row)
        metadata = item.get("source_metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                metadata = {}
        item.update(
            {
                "id": item.get("recommendation_id", item.get("id", 0)),
                "batch_ref": batch.batch_ref,
                "stream_ref": batch.stream_ref,
                "topic_label": item.get("topic", ""),
                "source_metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
        # SQLite cache rows legitimately contain NULL; API defaults apply only
        # when absent, so omit NULL rather than treating it as a string/int.
        item = {key: value for key, value in item.items() if value is not None}
        result.append(RecommendationOut.model_validate(item))
    return result


def screen_info(
    runtime: DirectorRuntime, stream: str, batch: BatchInstance | None
) -> DirectorScreenInfo:
    return DirectorScreenInfo(
        mode=runtime.config.director.mode,
        strategy=runtime.config.director.strategy,
        stream_ref=stream,
        batch_ref=batch.batch_ref if batch else "",
        requires_generation=batch is None,
    )


async def restore_screen(ctx: RuntimeContext, stream: str) -> RecommendationListResponse:
    runtime = ctx.director_runtime
    try:
        batch = await runtime.restore(
            stream,
            latest_disliked_topics=getattr(ctx.soul_engine, "get_effective_disliked_topics", None),
        )
    except StoreConflict as exc:
        raise HTTPException(status_code=409, detail=exc.reason_code) from None
    return RecommendationListResponse(
        items=screen_items(batch), director=screen_info(runtime, stream, batch)
    )


async def generate_screen(
    ctx: RuntimeContext, payload: DirectorGenerateIn
) -> RecommendationAppendResponse:
    runtime = ctx.director_runtime
    if runtime is None or not runtime.enabled:
        raise HTTPException(status_code=409, detail="director_disabled")
    if ctx.soul_engine is None:
        raise HTTPException(status_code=409, detail="profile_unavailable")
    stream = _stream(payload.stream_ref, payload.source_platform)
    try:
        profile = await ctx.soul_engine.get_profile()
        batch = await runtime.generate(
            profile,
            stream_ref=stream,
            request_id=payload.request_id,
            intent=payload.intent,
            limit=payload.limit,
            source_platform=payload.source_platform,
            excluded_keys=frozenset(payload.excluded_bvids),
            explicit_intent=payload.explicit_intent,
            latest_disliked_topics=getattr(ctx.soul_engine, "get_effective_disliked_topics", None),
        )
    except StoreConflict as exc:
        raise HTTPException(status_code=409, detail=exc.reason_code) from None
    pool_status = None
    inventory_reader = getattr(ctx.database, "load_pool_platform_availability_async", None)
    if callable(inventory_reader):
        version = time.time_ns() // 1_000_000
        try:
            inventory = await inventory_reader(
                xhs_self_nickname=runtime.engine._xhs_self_nickname()
            )
            pool_status = RecommendationPoolStatus(
                pool_available_count=inventory.total_available,
                platform_available_counts=dict(inventory.by_platform),
                pool_status_version=version,
            )
        except Exception:
            logger.warning("Director post-commit inventory read unavailable")
    if batch is not None:
        callback = getattr(ctx, "pool_inventory_commit_callback", None)
        if callable(callback):
            try:
                await callback()
            except Exception:
                logger.warning("Director post-commit inventory notification unavailable")
    available = (
        pool_status.platform_available_counts.get(payload.source_platform, 0)
        if pool_status is not None and payload.source_platform
        else pool_status.pool_available_count
        if pool_status is not None
        else None
    )
    replenish = getattr(ctx, "director_replenish_callback", None)
    if callable(replenish):
        try:
            await replenish(
                force=batch is None or available == 0 or len(batch.items) < payload.limit,
                available_count=pool_status.pool_available_count if pool_status else None,
            )
        except Exception:
            logger.warning("Director background replenishment wake unavailable")
    return RecommendationAppendResponse(
        items=screen_items(batch),
        director=screen_info(runtime, stream, batch),
        pool_status=pool_status,
        has_more=available > 0 if available is not None else batch is not None,
    )


def project_feedback(
    database: Any,
    recommendation_id: int,
    event_ref: str,
    kind: str,
    note: str = "",
    *,
    batch_ref: str = "",
    existing_event_id: int | None = None,
) -> None:
    """Project durable feedback by exact row identity, including while mode is off."""
    from openbiliclaw.storage.database import Database

    if not isinstance(database, Database):
        return
    conn = database.open_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='director_batch_items'"
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        if batch_ref:
            raise StoreConflict("batch_not_found")
        return
    store = DirectorStore(database)
    binding = store.lookup_recommendation(recommendation_id)
    if binding is None:
        if batch_ref:
            raise StoreConflict("batch_item_mismatch")
        return
    actual_batch, item_key = binding
    if batch_ref and batch_ref != actual_batch:
        raise StoreConflict("batch_item_mismatch")
    normalized_kind = {"comment": "explicit_request", "dismiss": "skip"}.get(kind, kind)
    from openbiliclaw.recommendation.director.models import digest

    store.feedback(
        actual_batch,
        "projection_" + digest(event_ref),
        item_key,
        cast("Literal['click', 'like', 'dislike', 'skip', 'explicit_request']", normalized_kind),
        note=note,
        existing_event_id=existing_event_id,
    )


def validate_feedback_binding(database: Any, recommendation_id: int, batch_ref: str) -> None:
    """Reject wrong batch identity before the canonical event ingress can write."""
    if not batch_ref:
        return
    binding = DirectorStore(database).lookup_recommendation(recommendation_id)
    if binding is None or binding[0] != batch_ref:
        raise StoreConflict("batch_item_mismatch")


def register_director_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    """Register under recommendation prefix so the existing process/auth proxy applies."""

    @app.get("/api/recommendations/director/status")
    async def status() -> dict[str, Any]:
        runtime = ctx.director_runtime
        return {
            "enabled": bool(runtime and runtime.enabled),
            "mode": runtime.config.director.mode if runtime else "off",
            "strategy": runtime.config.director.strategy if runtime else "agent_jev",
        }

    @app.post("/api/recommendations/director/generate", response_model=RecommendationAppendResponse)
    async def generate(payload: DirectorGenerateIn) -> RecommendationAppendResponse:
        return await generate_screen(ctx, payload)

    @app.get("/api/recommendations/director/restore", response_model=RecommendationListResponse)
    async def restore(
        stream_ref: str = Query("default", min_length=1, max_length=160),
    ) -> RecommendationListResponse:
        if ctx.director_runtime is None or not ctx.director_runtime.enabled:
            raise HTTPException(status_code=409, detail="director_disabled")
        return await restore_screen(ctx, stream_ref)

    @app.post("/api/recommendations/director/present")
    async def present(payload: DirectorPresentIn) -> dict[str, Any]:
        try:
            receipt = await asyncio.to_thread(
                DirectorStore(ctx.database).present,
                payload.batch_ref,
                payload.ack_id,
                payload.item_keys,
            )
        except StoreConflict as exc:
            raise HTTPException(status_code=409, detail=exc.reason_code) from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_presentation_ack") from None
        return receipt.model_dump(mode="json")
