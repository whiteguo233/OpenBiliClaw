"""Native Ego policy/lifecycle contracts; these are not real-browser acceptance."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import (
    EgoLiteWorkflowExecutor,
    EgoLiteWorkflowSession,
    EgoRequestPolicy,
)
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError


@pytest.fixture
def plan():
    return compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://books.example.com/",
            "allowed_origins": ["https://books.example.com"],
            "resource_origins": ["https://static.example.com"],
            "auth": {"login_origins": ["https://login.example.com"]},
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )


@pytest.mark.parametrize(
    "url,method,document,interactive,allowed",
    [
        ("https://books.example.com/list?q=hello", "GET", True, False, True),
        ("https://static.example.com/a.js", "GET", False, False, True),
        ("https://static.example.com/new", "GET", True, False, False),
        ("https://books.example.com.evil.test/", "GET", True, False, False),
        ("https://books.example.com/", "POST", True, False, False),
        ("https://login.example.com/login", "POST", True, True, True),
        ("https://login.example.com/login", "GET", True, False, False),
        ("https://books.example.com/", "DELETE", False, True, False),
        ("http://127.0.0.1/", "GET", True, True, False),
        ("file:///etc/passwd", "GET", True, True, False),
        ("https://name:secret@books.example.com/", "GET", True, False, False),
    ],
)
async def test_native_guard_exact_scope_and_method(
    plan, monkeypatch, url, method, document, interactive, allowed
):
    resolver = AsyncMock(return_value=["93.184.216.34"])
    monkeypatch.setattr("openbiliclaw.sources.ego_executor._system_resolver", resolver)
    assert (
        await EgoRequestPolicy().authorize(
            plan, {"url": url, "method": method, "document": document}, interactive=interactive
        )
        is allowed
    )


async def test_fresh_dns_check_rejects_rebinding_and_mixed_answers(plan, monkeypatch):
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["127.0.0.1"], ["93.184.216.34", "::1"]])
    monkeypatch.setattr("openbiliclaw.sources.ego_executor._system_resolver", resolver)
    request = {"url": plan.entry_url, "method": "GET", "document": True}
    policy = EgoRequestPolicy()
    assert await policy.authorize(plan, request, interactive=False)
    assert not await policy.authorize(plan, request, interactive=False)
    assert not await policy.authorize(plan, request, interactive=False)
    assert resolver.await_count == 3


def test_availability_never_executes_cli_or_claims_installed_means_ready(tmp_path, monkeypatch):
    binary = tmp_path / "ego-browser"
    binary.touch()
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", str(binary))
    monkeypatch.setattr("openbiliclaw.sources.ego_executor.sys.platform", "darwin")
    monkeypatch.setattr("openbiliclaw.sources.ego_executor._host_pid", lambda _: 123)
    status = EgoLiteWorkflowExecutor(tmp_path).availability()
    assert status["installed"] and not status["available"]
    assert status["connection_state"] == "unverified"
    assert status["executor"] == "ego" and "fallback" not in status
    assert status["dns_pinned"] is False


async def test_offline_host_does_not_start_cli(plan, tmp_path, monkeypatch):
    binary = tmp_path / "ego-browser"
    binary.touch()
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", str(binary))
    monkeypatch.setattr("openbiliclaw.sources.ego_executor._host_pid", lambda _: None)
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(BrowserWorkflowExecutionError, match="Open Ego Lite"):
        await EgoLiteWorkflowExecutor(tmp_path).open_session(plan, session_id="owned")
    spawn.assert_not_awaited()


async def test_existing_session_keeps_first_profile_and_host_identity(plan, tmp_path, monkeypatch):
    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(
        executor,
        "availability",
        lambda: {"installed": True, "platform_supported": True, "connection_state": "unverified"},
    )
    executor.profile_root.mkdir()
    state = {
        "task_id": 5,
        "task_name": "OpenBiliClaw-unique",
        "profile": "Original",
        "host": executor.host_key,
    }
    path = executor.profile_root / "owned.json"
    path.write_text(json.dumps(state))
    start = AsyncMock()
    monkeypatch.setattr(EgoLiteWorkflowSession, "start", start)
    executor.profile = "Changed default"
    session = await executor.open_session(plan, session_id="owned")
    assert start.call_args.args[1]["profile"] == "Original"
    await session.close()
    state["host"] = "anotherhost"
    path.write_text(json.dumps(state))
    with pytest.raises(BrowserWorkflowExecutionError, match="different Ego host"):
        await executor.open_session(plan, session_id="owned")


async def test_manual_operations_require_takeover_and_do_not_return_input_errors(plan, tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock
    )
    with pytest.raises(BrowserWorkflowExecutionError, match="login takeover"):
        await session.act({"action": "fill", "selector": "input", "value": "secret"})
    await session.close()
    assert not lock.locked()


async def test_run_total_budget_includes_native_start(plan, tmp_path, monkeypatch):
    executor = EgoLiteWorkflowExecutor(tmp_path)

    async def slow(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(executor, "open_session", slow)
    result = await executor.run(plan, session_id="owned")
    assert result.status == "failed" and result.error_code == "timeout"


def test_fixed_host_resources_are_packaged_and_have_no_model_code_operation():
    host = (
        Path(__import__("openbiliclaw.sources.ego_executor", fromlist=["x"]).__file__)
        .with_name("ego_host.mjs")
        .read_text()
    )
    assert "requestStage:'Request'" in host and "requestStage:'Response'" in host
    assert "globalThis.ego" in host and "sendCDPMessage" in host
    assert 'from "playwright' not in host and "from 'playwright" not in host
    assert "x.name===config.task_name && x.ownership==='agent'" in host
    assert "claimTaskSpace" not in host and "completeTaskSpace" not in host


def test_native_request_response_redirect_guard_protocol():
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the native binding protocol fixture")
    host = Path(__import__("openbiliclaw.sources.ego_executor", fromlist=["x"]).__file__).with_name(
        "ego_host.mjs"
    )
    fixture = Path(__file__).parent / "fixtures" / "ego_native_protocol.mjs"
    with tempfile.TemporaryDirectory(prefix="ego-test-", dir="/tmp") as directory:
        result = subprocess.run(
            [node, str(fixture), str(host), directory], capture_output=True, text=True, timeout=10
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "paused_requests": 6,
        "redirect_blocked": True,
        "closed_owned_tab": True,
    }


async def test_observation_preserves_denied_login_origin_without_navigating_it(plan, tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock, observation_only=True
    )
    session.navigate = AsyncMock(
        side_effect=BrowserWorkflowExecutionError("ego_navigation_failed", "blocked")
    )
    session.command = AsyncMock(
        return_value={
            "blocked_navigation_origins": ["https://login.example.com"],
            "login_wall": True,
        }
    )
    await session._open_entry()
    observation = await session.snapshot()
    assert observation["elements"] == [] and observation["login_wall"] is True
    assert observation["blocked_navigation_origins"] == ["https://login.example.com"]
    session.navigate.assert_awaited_once_with(plan.entry_url)
    session.command.assert_awaited_once_with("diagnostics")
    await session.close()


async def test_observation_does_not_hide_same_origin_network_failure(plan, tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock, observation_only=True
    )
    session.navigate = AsyncMock(
        side_effect=BrowserWorkflowExecutionError("ego_navigation_failed", "blocked")
    )
    session.command = AsyncMock(
        return_value={"blocked_navigation_origins": ["https://books.example.com"]}
    )
    with pytest.raises(BrowserWorkflowExecutionError):
        await session._open_entry()
    await session.close()


async def test_manual_fill_missing_or_ambiguous_selector_fails(plan, tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=True, lock=lock
    )
    session.command = AsyncMock(return_value=False)
    with pytest.raises(BrowserWorkflowExecutionError, match="did not match one input"):
        await session.act({"action": "fill", "selector": "input", "value": "private-value"})
    await session.close()


async def test_pagination_stops_when_second_page_requires_login(plan, tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock
    )
    session.login_required = AsyncMock(side_effect=[False, True])
    session.navigate = AsyncMock()
    session.command = AsyncMock(
        return_value={
            "items": [
                {
                    "title": "Public book",
                    "url": "https://books.example.com/book/1",
                    "author_name": "Writer",
                    "published_at": "",
                    "description": "Synopsis",
                }
            ],
            "empty": False,
            "next": "https://books.example.com/page2",
        }
    )
    result = await session._collect(plan.steps[0])
    assert result.status == "login_required" and len(result.items) == 1
    assert not result.scope_complete
    session.command.assert_awaited_once()
    await session.close()


async def test_close_disconnects_socket_even_when_native_close_times_out(plan, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock
    )
    writer = SimpleNamespace(close=Mock())
    session._writer = writer
    session.command = AsyncMock(side_effect=TimeoutError)
    await session.close()
    writer.close.assert_called_once()
    assert not lock.locked()
