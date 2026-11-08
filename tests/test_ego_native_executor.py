"""Native Ego policy/lifecycle contracts; these are not real-browser acceptance."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources.browser_workflow import WorkflowParameters, compile_browser_workflow
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


@pytest.mark.parametrize("repair_kind", ["field", "item", "syntax"])
async def test_failed_collection_retains_fixed_reader_field_evidence(plan, tmp_path, repair_kind):
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock
    )
    evidence = (
        {"href_selector_repairs": ["link_selector", "title_selector"]}
        if repair_kind == "field"
        else (
            {"item_href_selector_repair": {"origin": "https://books.example.com"}}
            if repair_kind == "item"
            else {"collection_diagnostics": {"error": "invalid_css_selector"}}
        )
    )

    async def command(operation, **arguments):
        if operation == "snapshot":
            return {"title": "Observed public list", "login_wall": False}
        assert operation == "collect"
        return {
            "items": [{"title": "", "url": ""}] if repair_kind == "field" else [],
            "empty": False,
            "next": "",
            **evidence,
        }

    session.command = command
    result = await session.execute(WorkflowParameters())
    assert result.status == "failed" and result.error_code == "extraction_unverified"
    assert result.items == ()
    assert result.observation == {
        "title": "Observed public list",
        "login_wall": False,
        **evidence,
    }
    lock.release()


async def test_capture_binds_only_valid_unique_budgeted_items_to_their_actual_rows(plan, tmp_path):
    raw_plan = plan.to_dict()
    raw_plan["budgets"]["max_items"] = 2
    bounded = compile_browser_workflow(raw_plan)
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), bounded, interactive=False, lock=lock
    )
    session.capture_observation = True

    def item(url, row_id):
        return {
            "title": row_id,
            "url": url,
            "author_name": "",
            "published_at": "",
            "description": "",
            "evidence": {"row": {"tag": "article", "id": row_id}, "link": {"tag": "a"}},
        }

    async def command(operation, **arguments):
        if operation == "snapshot":
            return {"login_wall": False}
        assert operation == "collect" and arguments["capture_evidence"] is True
        return {
            "items": [
                item("https://other.example/item/0", "foreign"),
                item("https://books.example.com/book/1", "first"),
                item("https://books.example.com/book/1", "duplicate"),
                item("https://books.example.com/book/2", "second"),
                item("https://books.example.com/book/3", "over-budget"),
            ],
            "empty": False,
            "next": "",
            "collection_context": {"item_match_count": 5},
        }

    session.command = command
    result = await session.execute(WorkflowParameters())
    rows = result.observation["collection_evidence"]["samples"]
    assert [row["row"]["id"] for row in rows] == ["first", "second"]
    assert [row["url"] for row in rows] == [item.url for item in result.items]
    lock.release()


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
    monkeypatch.setattr("openbiliclaw.sources.ego_executor.sys.platform", "darwin")
    binary = tmp_path / "ego-browser"
    binary.touch()
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", str(binary))
    monkeypatch.setattr("openbiliclaw.sources.ego_executor._host_pid", lambda _: None)
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(BrowserWorkflowExecutionError, match="Open Ego Lite"):
        await EgoLiteWorkflowExecutor(tmp_path).open_session(plan, session_id="owned")
    spawn.assert_not_awaited()


@pytest.mark.parametrize("ready_after", [40, 60])
async def test_native_initialization_uses_but_does_not_exceed_workflow_budget(
    plan, tmp_path, monkeypatch, ready_after
):
    from openbiliclaw.sources import ego_executor as module

    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(executor, "schedule_idle_stop", lambda **kwargs: None)
    monkeypatch.setattr(module, "_host_pid", lambda _: None)
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    process = SimpleNamespace(returncode=None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    reader = asyncio.StreamReader()
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(asyncio, "open_unix_connection", AsyncMock(return_value=(reader, writer)))
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(executor, plan, interactive=False, lock=lock)
    monkeypatch.setattr(session, "_open_entry", AsyncMock())
    monkeypatch.setattr(session, "command", AsyncMock(return_value=True))
    state = {
        "task_id": None,
        "task_name": "OpenBiliClaw-slow-start",
        "profile": "Default",
        "host": executor.host_key,
    }

    async def progress(_delay):
        clock[0] += 10
        if clock[0] >= ready_after:
            assert session._temporary is not None
            (Path(session._temporary.name) / "ready.json").write_text(
                json.dumps({"task_id": 7, "version": "test"})
            )

    monkeypatch.setattr(asyncio, "sleep", progress)
    try:
        if ready_after < plan.budgets.timeout_seconds:
            await session.start(tmp_path / "owned.json", state)
            assert session._started and state["task_id"] == 7
            session._open_entry.assert_awaited_once()
        else:
            with pytest.raises(BrowserWorkflowExecutionError) as failure:
                await session.start(tmp_path / "owned.json", state)
            assert failure.value.code == "ego_connection_timeout"
            session._open_entry.assert_not_awaited()
    finally:
        process.returncode = 0
        await session.close()


async def test_existing_session_keeps_first_profile_and_host_identity(plan, tmp_path, monkeypatch):
    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(
        executor,
        "availability",
        lambda: {
            "installed": True,
            "available": True,
            "platform_supported": True,
            "connection_state": "ready",
        },
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


@pytest.mark.parametrize("capture", [False, True])
async def test_candidate_observation_is_captured_before_owned_session_closes(
    plan, tmp_path, monkeypatch, capture
):
    from types import SimpleNamespace

    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    events = []

    async def snapshot():
        events.append("snapshot")
        return {"title": "Current candidate page"}

    async def close():
        events.append("close")

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=BrowserWorkflowResult(
                "ok", (BrowserWorkflowItem("A real result", "https://books.example.com/book/1"),)
            )
        ),
        snapshot=snapshot,
        close=close,
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(executor, "open_session", AsyncMock(return_value=session))
    result = await executor.run(plan, session_id="owned", capture_observation=capture)
    assert result.observation == ({"title": "Current candidate page"} if capture else {})
    assert events == (["snapshot", "close"] if capture else ["close"])


async def test_candidate_snapshot_timeout_does_not_reuse_success_and_always_closes(
    plan, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=BrowserWorkflowResult(
                "ok", (BrowserWorkflowItem("Result", "https://books.example.com/book/1"),)
            )
        ),
        snapshot=AsyncMock(side_effect=TimeoutError),
        close=AsyncMock(),
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(executor, "open_session", AsyncMock(return_value=session))
    result = await executor.run(plan, session_id="owned", capture_observation=True)
    assert result.status == "failed" and result.error_code == "timeout" and result.items == ()
    session.close.assert_awaited_once()


def test_fixed_host_resources_are_packaged_and_have_no_model_code_operation():
    host = (
        Path(__import__("openbiliclaw.sources.ego_executor", fromlist=["x"]).__file__)
        .with_name("ego_host.mjs")
        .read_text()
    )
    assert "requestStage:'Request'" in host and "requestStage:'Response'" in host
    assert "globalThis.ego" in host and "sendCDPMessage" in host
    assert "if(!config.interactive)" in host and "windowState:'minimized'" in host
    assert 'from "playwright' not in host and "from 'playwright" not in host
    assert "x.name===config.task_name && x.ownership==='agent'" in host
    assert "claimTaskSpace" not in host and "completeTaskSpace" not in host


@pytest.mark.parametrize(
    "mode",
    [
        "background",
        "interactive",
        "interactive_stuck",
        "close_pending",
        "parsed_document",
        "loading_document",
    ],
)
def test_native_request_response_redirect_guard_protocol(mode):
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
            [node, str(fixture), str(host), directory, mode],
            capture_output=True,
            text=True,
            timeout=20 if mode in {"parsed_document", "loading_document"} else 10,
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "paused_requests": 9,
        "redirect_blocked": True,
        "closed_owned_tab": True,
    }


def test_native_host_restart_only_recreates_a_missing_owned_space():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the native binding protocol fixture")
    host = Path(__import__("openbiliclaw.sources.ego_executor", fromlist=["x"]).__file__).with_name(
        "ego_host.mjs"
    )
    fixture = Path(__file__).parent / "fixtures" / "ego_space_recovery.mjs"
    result = subprocess.run(
        [node, str(fixture), str(host)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "restart recovery passed" in result.stdout


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


@pytest.mark.parametrize("document_status", [403, 503])
async def test_verification_page_pauses_scheduled_ego_without_collecting(
    plan, tmp_path, document_status
):
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=asyncio.Lock()
    )
    session.snapshot = AsyncMock(
        return_value={
            "title": "Just a moment...",
            "login_wall": False,
            "elements": [{"tag": "h2", "text": "Verify you are human"}],
            "resource_failures": [{"resource_type": "Document", "status": document_status}],
        }
    )
    session.command = AsyncMock(side_effect=AssertionError("must not collect verification text"))
    result = await session.execute(WorkflowParameters())
    assert result.status == "login_required" and not result.scope_complete and not result.items
    assert result.error_code == "verification_required"
    session.command.assert_not_called()


@pytest.mark.parametrize("change", ["title", "status", "body", "resource"])
def test_article_or_generic_denial_is_not_a_verification_wall(change):
    from openbiliclaw.sources.browser_workflow import browser_verification_required

    observation = {
        "title": "请稍候…",
        "elements": [{"tag": "h2", "text": "正在进行安全验证"}],
        "resource_failures": [{"resource_type": "Document", "status": 403}],
    }
    if change == "title":
        observation["title"] = "How security verification works"
    if change == "status":
        observation["resource_failures"][0]["status"] = 200
    if change == "body":
        observation["elements"] = [{"tag": "h2", "text": "Access denied"}]
    if change == "resource":
        observation["resource_failures"][0]["resource_type"] = "Image"
    assert not browser_verification_required(observation)
