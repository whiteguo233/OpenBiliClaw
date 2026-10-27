"""Authenticated custom-source API and explicit human browser takeover."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.sources.browser_workflow import workflow_url
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

logger = logging.getLogger(__name__)

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
    workflow_id: str = ""
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


def _ego_setup_manager(service: Any) -> Any:
    from openbiliclaw.sources.ego_setup import EgoSetupManager

    return EgoSetupManager(Path(service.database._db_path).resolve().parent)


def create_custom_sources_router(
    service_factory: Callable[[], WorkflowService],
    *,
    prepare_ego_on_startup: bool = False,
) -> APIRouter:
    """Share live runtime dependencies; takeover handles stay inside this API worker."""
    router = APIRouter(prefix="/api/custom-sources")
    sessions: dict[str, Any] = {}
    expiry: dict[str, asyncio.Task[None]] = {}
    admission = asyncio.Lock()
    installation: dict[str, str] = {}
    install_task: asyncio.Task[None] | None = None
    ego_prepare_task: asyncio.Task[None] | None = None
    ego_prepare_error: dict[str, Any] = {}

    def ego_setup_status() -> dict[str, Any]:
        status: dict[str, Any] = _ego_setup_manager(service()).status()
        if ego_prepare_task is not None and not ego_prepare_task.done():
            if status.get("setup_state") != "preparing":
                status.update(setup_state="preparing", detail="正在准备 Ego Lite…")
        elif ego_prepare_error:
            status.update(ego_prepare_error)
        return status

    async def prepare_ego_runtime(manager: Any) -> None:
        try:
            result = await manager.prepare()
            if result.get("available") is True:
                logger.info("Managed Ego Lite preparation completed")
            elif result.get("setup_state") != "preparing":
                logger.warning("Managed Ego Lite preparation did not complete; see source status")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not expose command arguments, local paths or browser state.
            ego_prepare_error.update(
                setup_state="failed", detail=f"Ego Lite 准备失败：{type(exc).__name__}"
            )
            logger.warning("Managed Ego Lite preparation failed (%s)", type(exc).__name__)

    def schedule_ego_preparation(manager: Any) -> None:
        nonlocal ego_prepare_task
        if ego_prepare_task is None or ego_prepare_task.done():
            ego_prepare_error.clear()
            ego_prepare_task = asyncio.create_task(
                prepare_ego_runtime(manager), name="ego_deployment_prepare"
            )

    @router.on_event("startup")
    async def prepare_deployed_ego() -> None:
        # Only actual desktop/CLI server entry points opt in. Constructing an
        # app for inspection, an embedded consumer or a worker never installs.
        # Keep setup in an owned task so the UI can report progress immediately.
        if not prepare_ego_on_startup:
            return
        try:
            manager = _ego_setup_manager(service())
            status = manager.status()
            if status.get("managed") is not True or status.get("platform_supported") is not True:
                return
            if status.get("setup_state") == "preparing":
                return
            if status.get("setup_state") == "ready" and status.get("installed") is True:
                # Prepared/offline is sufficient at deployment startup. Actual
                # collection starts that host when needed; don't launch it here.
                if status.get("available") is True:
                    manager.schedule_idle_stop()
                return
            schedule_ego_preparation(manager)
        except Exception as exc:
            ego_prepare_error.update(
                setup_state="failed", detail=f"Ego Lite 准备失败：{type(exc).__name__}"
            )
            logger.warning("Managed Ego Lite preparation could not start (%s)", type(exc).__name__)

    @router.post("/executors/ego/prepare", status_code=202)
    async def prepare_ego() -> dict[str, Any]:
        manager = _ego_setup_manager(service())
        status = manager.status()
        if status.get("managed") is not True:
            raise HTTPException(409, "此 Ego Lite 连接由外部配置管理。")
        if status.get("platform_supported") is False or status.get("setup_state") == "unsupported":
            raise HTTPException(409, str(status.get("detail", "当前环境不支持准备 Ego Lite。")))
        if status.get("setup_state") == "ready" and status.get("available") is True:
            manager.schedule_idle_stop()
            return dict(status)
        schedule_ego_preparation(manager)
        return ego_setup_status()

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
    def list_sources() -> dict[str, Any]:
        # Runtime inspection can spawn a bounded legacy-driver process or ps.
        # Let FastAPI run this read-only handler in its worker pool, so polling
        # cannot starve the native Ego authorization and command readers.
        manager = service()
        executors = [{"id": "http", "available": True, "detail": "公开 Feed / Sitemap / 静态列表"}]
        for name in ("playwright", "ego"):
            status = manager.executor(name).availability()
            if name == "playwright" and installation:
                status.update(installation)
            if name == "ego":
                setup = ego_setup_status()
                status.update(
                    {key: setup[key] for key in ("setup_state", "phase", "managed") if key in setup}
                )
                if setup.get("managed") is True and setup.get("setup_state") in {
                    "preparing",
                    "failed",
                    "unsupported",
                }:
                    status["detail"] = str(setup.get("detail", ""))
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
        async with admission:
            manager = service()
            # Reopening after a disconnected app must not contend with its own lock.
            for draft_id in sessions:
                draft = manager.store.get_draft(draft_id)
                if (
                    draft.get("source_id") == source_id
                    and draft.get("workflow_id")
                    and not draft.get("requires_activation")
                ):
                    return public_draft(draft)
            try:
                return public_draft(manager.login_draft(source_id))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.post("/drafts/{draft_id}/close")
    async def close_login(draft_id: str) -> dict[str, bool]:
        """Release interactive control without trial, publication or logout."""
        async with admission:
            await close(draft_id)
        return {"ok": True}

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
        try:
            image = await session.screenshot()
        except BrowserWorkflowExecutionError as exc:
            raise HTTPException(409, exc.detail, headers={"Cache-Control": "no-store"}) from exc
        return Response(
            image,
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
            if (
                draft.get("workflow_id")
                and draft["status"] == "ready"
                and not draft.get("requires_activation")
            ):
                # Re-login resumes the already approved workflow and source policy.
                manager.store.publish(draft_id, draft["source_id"])
            return public_draft(draft)

    @router.on_event("shutdown")
    async def shutdown() -> None:
        if ego_prepare_task is not None and not ego_prepare_task.done():
            ego_prepare_task.cancel()
            with suppress(asyncio.CancelledError):
                await ego_prepare_task
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
        "workflow_id",
        "base_version",
        "requires_activation",
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
    from openbiliclaw.sources.workflow_service import workflow_presentation

    return {key: value for key, value in draft.items() if key in fields} | workflow_presentation(
        draft
    )
