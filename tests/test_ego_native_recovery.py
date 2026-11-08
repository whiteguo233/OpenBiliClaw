"""Retry a wedged native host only while no other session is using it."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import EgoLiteWorkflowExecutor
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("openbiliclaw.sources.ego_executor.sys.platform", "darwin")
    executor = EgoLiteWorkflowExecutor(tmp_path)
    executor.managed = True
    executor._setup.stop = AsyncMock()
    executor._setup.release_session_lease = Mock()
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://books.example/",
            "allowed_origins": ["https://books.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    return executor, plan


@pytest.mark.parametrize("code", ["ego_connection_timeout", "ego_protocol_timeout"])
async def test_native_open_retries_with_fresh_host_and_original_identity(setup, code):
    executor, plan = setup
    lease = object()
    executor._setup.try_exclusive_session_lease = Mock(return_value=lease)
    expected = object()
    calls = []

    async def open_once(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1 or executor._setup.stop.await_count == 0:
            raise BrowserWorkflowExecutionError(code, "Native host is not answering")
        return expected

    executor._open_session_once = open_once
    assert (
        await executor.open_session(plan, session_id="original-session", interactive=True)
        is expected
    )
    assert len(calls) == 2 and calls[0] == calls[1]
    executor._setup.stop.assert_awaited_once()
    executor._setup.release_session_lease.assert_called_once_with(lease)


async def test_another_live_session_prevents_host_restart(setup):
    executor, plan = setup
    executor._setup.try_exclusive_session_lease = Mock(return_value=None)
    executor._open_session_once = AsyncMock(
        side_effect=BrowserWorkflowExecutionError("ego_protocol_timeout", "Not answering")
    )
    with pytest.raises(BrowserWorkflowExecutionError):
        await executor.open_session(plan, session_id="original-session")
    assert executor._open_session_once.await_count == 2
    executor._setup.stop.assert_not_awaited()


async def test_cancellation_during_recovery_releases_lease_without_retry(setup):
    executor, plan = setup
    lease = object()
    executor._setup.try_exclusive_session_lease = Mock(return_value=lease)
    executor._setup.stop.side_effect = asyncio.CancelledError()
    executor._open_session_once = AsyncMock(
        side_effect=BrowserWorkflowExecutionError("ego_protocol_timeout", "Not answering")
    )
    with pytest.raises(asyncio.CancelledError):
        await executor.open_session(plan, session_id="original-session")
    assert executor._open_session_once.await_count == 1
    executor._setup.release_session_lease.assert_called_once_with(lease)
