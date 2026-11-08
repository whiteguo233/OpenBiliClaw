"""Managed browser setup is automatic at authoring, never at status/feed reads."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources.ego_executor import EgoLiteWorkflowExecutor, EgoLiteWorkflowSession
from openbiliclaw.sources.ego_setup import EgoSetupManager
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database
from tests.test_ego_workflow_wiring import PLAN, URL


@pytest.mark.usefixtures("managed_env")
@pytest.mark.parametrize("outcome", ["ready", "failed", "cancelled_waiter"])
async def test_chat_waits_for_deployment_preparation(tmp_path, monkeypatch, outcome):
    from openbiliclaw.sources.ego_setup import EgoSetupError
    from tests.test_ego_setup import fake_lifecycle

    monkeypatch.setattr("openbiliclaw.sources.ego_setup.sys.platform", "darwin")
    database = Database(tmp_path / "test.db")
    database.initialize()
    service = WorkflowService(database)
    manager = EgoSetupManager(tmp_path)
    calls = fake_lifecycle(manager, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def install():
        calls.append("install")
        manager._phase("verify")
        entered.set()
        await release.wait()
        if outcome == "failed":
            raise EgoSetupError("ego_setup_signature_mismatch")

    monkeypatch.setattr(manager, "_install", install)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.complete_onboarding", AsyncMock())
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.EgoSetupManager", lambda _: manager)
    observed = login_observation(service, monkeypatch)
    owner = asyncio.create_task(manager.prepare())
    await entered.wait()
    author = asyncio.create_task(service.prepare(url=URL, intent="Books", executor="ego"))
    try:
        await asyncio.sleep(0.01)
        assert not author.done(), "Chat must wait for the deployment owner, not fail its draft"
        observed.assert_not_awaited()
        if outcome == "cancelled_waiter":
            author.cancel()
            with pytest.raises(asyncio.CancelledError):
                await author
            assert not owner.done() and "stop" not in calls
        release.set()
        prepared = await owner
        if outcome != "cancelled_waiter":
            draft = await author
            if outcome == "ready":
                assert prepared["available"] and draft["status"] == "needs_login"
                observed.assert_awaited_once()
            else:
                assert draft["status"] == "failed"
                assert "ego_setup_signature_mismatch" in draft["detail"]
                observed.assert_not_awaited()
        assert calls.count("install") == 1
        assert service.sources.list_sources() == []
    finally:
        release.set()
        await owner
        if not author.done():
            author.cancel()
        with suppress(asyncio.CancelledError):
            await author
        database.close()


@pytest.fixture
def managed_env(monkeypatch):
    for key in ("BROWSER", "SERVER_NAME", "PROFILE"):
        monkeypatch.delenv(f"OPENBILICLAW_EGO_{key}", raising=False)


def login_observation(service, monkeypatch):
    observed = AsyncMock(return_value=(SimpleNamespace(close=AsyncMock()), {"login_wall": True}))
    monkeypatch.setattr(service, "observe_session", observed)
    return observed


@pytest.mark.usefixtures("managed_env")
async def test_first_website_prepares_browser_before_observation(tmp_path, monkeypatch):
    database = Database(tmp_path / "data" / "test.db")
    database.initialize()
    service = WorkflowService(database)
    roots = []

    async def prepare(manager, *, wait=False):
        assert wait is True
        roots.append(manager.root)
        return {"available": True}

    monkeypatch.setattr(EgoSetupManager, "prepare", prepare)
    observed = login_observation(service, monkeypatch)
    try:
        result = await service.prepare(url=URL, intent="Discover books", executor="ego")
        assert result["status"] == "needs_login"
        assert roots == [database._db_path.parent / "ego-managed"]
        observed.assert_awaited_once()
        assert service.sources.list_sources() == []
    finally:
        database.close()


@pytest.mark.usefixtures("managed_env")
async def test_setup_failure_stays_a_draft_without_browser_or_activation(tmp_path, monkeypatch):
    database = Database(tmp_path / "test.db")
    database.initialize()
    service = WorkflowService(database)
    prepare = AsyncMock(return_value={"available": False, "detail": "ego_setup_signature_mismatch"})
    monkeypatch.setattr(EgoSetupManager, "prepare", prepare)
    observed = login_observation(service, monkeypatch)
    try:
        result = await service.prepare(url=URL, intent="Discover books", executor="ego")
        assert result["status"] == "failed" and not result["trial_success"]
        assert "ego_setup_signature_mismatch" in result["detail"]
        observed.assert_not_awaited()
        assert service.sources.list_sources() == []
        # Resume exactly the saved failed draft, as a later chat will do.
        prepare.return_value = {"available": True}
        resumed = await service.trial(result["draft_id"])
        assert resumed["draft_id"] == result["draft_id"]
        assert resumed["status"] == "needs_login" and prepare.await_count == 2
        observed.assert_awaited_once()
    finally:
        database.close()


async def test_explicit_external_website_never_prepares_managed_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBILICLAW_EGO_SERVER_NAME", "external")
    database = Database(tmp_path / "test.db")
    database.initialize()
    service = WorkflowService(database)
    prepare = AsyncMock()
    monkeypatch.setattr(EgoSetupManager, "prepare", prepare)
    login_observation(service, monkeypatch)
    try:
        result = await service.prepare(url=URL, intent="Books", executor="ego")
        assert result["status"] == "needs_login"
        prepare.assert_not_awaited()
    finally:
        database.close()


@pytest.mark.usefixtures("managed_env")
def test_executor_uses_bundled_managed_connection_and_read_only_handshake_state(
    tmp_path, monkeypatch
):
    root = tmp_path / "browser-sessions"
    executor = EgoLiteWorkflowExecutor(root)
    assert executor.executable.startswith(str(tmp_path / "ego-managed"))
    assert executor.profile == "Default" and executor.managed
    assert not tmp_path.joinpath("ego-managed").exists()
    monkeypatch.setattr(EgoSetupManager, "status", lambda _: {"available": True, "installed": True})
    status = executor.availability()
    assert status["available"] and status["executor"] == "ego" and not status["dns_pinned"]
    assert not tmp_path.joinpath("ego-managed").exists()


@pytest.mark.usefixtures("managed_env")
async def test_actual_use_starts_prepared_host_before_opening_session(tmp_path, monkeypatch):
    from openbiliclaw.sources.browser_workflow import compile_browser_workflow

    executor = EgoLiteWorkflowExecutor(tmp_path / "browser-sessions")
    state = {
        "available": False,
        "installed": True,
        "platform_supported": True,
        "connection_state": "offline",
    }
    monkeypatch.setattr(EgoSetupManager, "status", lambda _: dict(state))

    async def ensure():
        state.update(available=True, connection_state="ready")
        return state

    ensure_running = AsyncMock(side_effect=ensure)
    monkeypatch.setattr(executor._setup, "ensure_running", ensure_running)
    start = AsyncMock()
    monkeypatch.setattr(EgoLiteWorkflowSession, "start", start)
    session = await executor.open_session(compile_browser_workflow(PLAN), session_id="owned")
    ensure_running.assert_awaited_once()
    start.assert_awaited_once()
    await session.close()


@pytest.mark.usefixtures("managed_env")
async def test_managed_onboarding_never_opens_a_workflow_session(tmp_path, monkeypatch):
    from openbiliclaw.sources.browser_workflow import compile_browser_workflow
    from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

    executor = EgoLiteWorkflowExecutor(tmp_path / "browser-sessions")
    status = {
        "installed": True,
        "available": False,
        "platform_supported": True,
        "connection_state": "unverified",
        "setup_state": "preparing",
        "detail": "Preparing Ego",
    }
    monkeypatch.setattr(EgoSetupManager, "status", lambda _: status)
    monkeypatch.setattr(executor._setup, "ensure_running", AsyncMock(return_value=status))
    start = AsyncMock()
    monkeypatch.setattr(EgoLiteWorkflowSession, "start", start)
    with pytest.raises(BrowserWorkflowExecutionError, match="Preparing Ego"):
        await executor.open_session(compile_browser_workflow(PLAN), session_id="owned")
    start.assert_not_awaited()


@pytest.mark.usefixtures("managed_env")
async def test_feed_authoring_does_not_install_a_browser(tmp_path, monkeypatch):
    import httpx

    from tests.test_custom_feed_workflow import FEED_URL, _feed, _service

    database = Database(tmp_path / "feed.db")
    database.initialize()
    body, media_type = _feed("rss")
    service = _service(
        database,
        monkeypatch,
        lambda _: httpx.Response(200, content=body, headers={"Content-Type": media_type}),
    )
    prepare = AsyncMock()
    monkeypatch.setattr(EgoSetupManager, "prepare", prepare)
    try:
        draft = await service.prepare(url=FEED_URL, intent="跟踪该订阅的新内容")
        assert draft["status"] == "ready" and draft["executor"] == "http"
        prepare.assert_not_awaited()
        assert not tmp_path.joinpath("ego-managed").exists()
    finally:
        database.close()
