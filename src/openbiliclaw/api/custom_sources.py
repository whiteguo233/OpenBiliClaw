"""Authenticated custom-source API and explicit human browser takeover."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.sources.browser_workflow import workflow_url

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.sources.workflow_service import WorkflowService


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    intent: str = Field(min_length=1, max_length=4000)
    name: str = Field(default="", max_length=200)
    executor: str = "auto"
    source_id: str = ""
    separate_source: bool = False
    session_id: str = ""


class ActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_share: int = Field(default=1, ge=1, le=20, strict=True)
    interval_minutes: int = Field(default=30, ge=5, le=10080, strict=True)


class SourceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_share: int | None = Field(default=None, ge=1, le=20, strict=True)
    interval_minutes: int | None = Field(default=None, ge=5, le=10080, strict=True)
    enabled: bool | None = Field(default=None, strict=True)


class TakeoverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_origin: str = ""


def create_custom_sources_router(service_factory: Callable[[], WorkflowService]) -> APIRouter:
    """Share live runtime dependencies; takeover handles stay inside this API worker."""
    router = APIRouter(prefix="/api/custom-sources")
    sessions: dict[str, Any] = {}
    expiry: dict[str, asyncio.Task[None]] = {}
    admission = asyncio.Lock()
    installation: dict[str, str] = {}
    install_task: asyncio.Task[None] | None = None

    async def install_runtime() -> None:
        from openbiliclaw.sources.browser_install import install_browser_runtime

        try:
            outcome = await install_browser_runtime()
            installation.update(
                status="ready" if outcome["installed"] else "failed",
                detail=str(outcome.get("detail", "")),
            )
        except Exception as exc:
            installation.update(status="failed", detail=f"浏览器安装失败：{type(exc).__name__}")

    @router.post("/executors/playwright/install", status_code=202)
    async def install_playwright() -> dict[str, str]:
        nonlocal install_task
        if install_task is None or install_task.done():
            installation.update(status="installing", detail="正在安装 Chromium…")
            install_task = asyncio.create_task(install_runtime())
        return dict(installation)

    async def close(draft_id: str) -> None:
        timer = expiry.pop(draft_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        session = sessions.pop(draft_id, None)
        if session is not None:
            await session.close()

    async def expire(draft_id: str) -> None:
        await asyncio.sleep(15 * 60)
        await close(draft_id)

    def service() -> WorkflowService:
        return service_factory()

    @router.get("")
    async def list_sources() -> dict[str, Any]:
        manager = service()
        executors = [{"id": "http", "available": True, "detail": "公开 Feed / Sitemap / 静态列表"}]
        for name in ("playwright", "ego"):
            status = manager.executor(name).availability()
            if name == "playwright" and installation:
                status.update(installation)
            executors.append(dict(status, id=name))
        return {"items": manager.list_sources(), "executors": executors}

    @router.post("/drafts")
    async def prepare(payload: DraftIn) -> dict[str, Any]:
        try:
            return public_draft(await service().prepare(**payload.model_dump()))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/drafts/{draft_id}")
    async def get_draft(draft_id: str) -> dict[str, Any]:
        try:
            return public_draft(service().store.get_draft(draft_id))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/drafts/{draft_id}/trial")
    async def trial(draft_id: str) -> dict[str, Any]:
        async with admission:
            await close(draft_id)
            try:
                return public_draft(await service().trial(draft_id))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.post("/drafts/{draft_id}/activate")
    async def activate(draft_id: str, payload: ActivateIn) -> dict[str, Any]:
        try:
            return {"source": service().activate(draft_id, **payload.model_dump())}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/{source_id}")
    async def update(source_id: str, payload: SourceUpdateIn) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        if any(value is None for value in values.values()):
            raise HTTPException(422, "source policy values cannot be null")
        try:
            return {"source": service().sources.update_source(source_id, **values)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/{source_id}")
    async def delete(source_id: str) -> dict[str, Any]:
        try:
            # Archive identity, historical attribution and shared login remain valid.
            return {"ok": service().sources.archive_source(source_id)}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/{source_id}/runs")
    async def runs(source_id: str) -> dict[str, Any]:
        return {"items": service().store.runs(source_id)}

    @router.post("/{source_id}/login")
    async def login(source_id: str) -> dict[str, Any]:
        try:
            return public_draft(service().login_draft(source_id))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/drafts/{draft_id}/takeover")
    async def takeover(draft_id: str, payload: TakeoverIn | None = None) -> dict[str, Any]:
        """Only the human-facing page invokes interactive control; no agent tool does."""
        async with admission:
            if draft_id in sessions and not (payload and payload.login_origin):
                return await sessions[draft_id].snapshot()  # type: ignore[no-any-return]
            if draft_id not in sessions and len(sessions) >= 2:
                raise HTTPException(409, "Two login windows are already open")
            try:
                manager = service()
                draft = manager.store.get_draft(draft_id)
                if draft["executor"] == "http":
                    raise ValueError("public HTTP sources do not use a browser login")
                if payload and payload.login_origin:
                    origin = payload.login_origin
                    workflow_url(origin)
                    if origin not in draft.get("suggested_login_origins", []):
                        raise ValueError("select a login origin observed in this website")
                    approved = set(draft.get("login_origins", []))
                    approved.add(origin)
                    if len(approved) > 8:
                        raise ValueError("too many login origins")
                    draft["login_origins"] = sorted(approved)
                    if draft.get("workflow"):
                        draft["workflow"]["auth"]["login_origins"] = sorted(approved)
                    manager.store.save_draft(draft, draft_id=draft_id)
                    await close(draft_id)
                session, observation = await manager.observe_session(draft, interactive=True)
                sessions[draft_id] = session
                expiry[draft_id] = asyncio.create_task(expire(draft_id))
                return observation
            except (ValueError, RuntimeError, OSError) as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.get("/drafts/{draft_id}/screenshot")
    async def screenshot(draft_id: str) -> Response:
        session = sessions.get(draft_id)
        if session is None:
            raise HTTPException(409, "Open the login window first")
        return Response(
            await session.screenshot(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/drafts/{draft_id}/action")
    async def action(draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = sessions.get(draft_id)
        if session is None:
            raise HTTPException(409, "Login window expired; open it again")
        try:
            # Deliberately do not log, persist, or send manual input to the LLM.
            observation: dict[str, Any] = await session.act(payload)
            manager = service()
            draft = manager.store.get_draft(draft_id)
            draft["suggested_login_origins"] = observation.get("blocked_navigation_origins", [])
            manager.store.save_draft(draft, draft_id=draft_id)
            return observation
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/drafts/{draft_id}/resume")
    async def resume(draft_id: str) -> dict[str, Any]:
        async with admission:
            await close(draft_id)
            manager = service()
            draft = await manager.trial(draft_id)
            if draft.get("workflow_id") and draft["status"] == "ready":
                # Re-login resumes the already approved workflow and source policy.
                manager.store.publish(draft_id, draft["source_id"])
            return public_draft(draft)

    @router.on_event("shutdown")
    async def shutdown() -> None:
        if install_task is not None and not install_task.done():
            install_task.cancel()
            with suppress(asyncio.CancelledError):
                await install_task
        for draft_id in list(sessions):
            await close(draft_id)

    return router


def public_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Keep session identifiers and implementation state out of chat/API summaries."""
    fields = {
        "draft_id",
        "status",
        "source_id",
        "name",
        "intent",
        "executor",
        "sample_items",
        "detail",
        "takeover_url",
        "entry_url",
        "query_mode",
        "trial_success",
        "suggested_login_origins",
    }
    return {key: value for key, value in draft.items() if key in fields}
