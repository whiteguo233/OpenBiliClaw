"""Network failure reasons must survive the fixed host without leaking page text."""

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources import ego_executor
from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import EgoLiteWorkflowExecutor, EgoLiteWorkflowSession
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError


def test_fixed_host_reports_only_safe_current_navigation_reason():
    node = shutil.which("node")
    assert node is not None, "Node is required to verify the native host protocol"
    with tempfile.TemporaryDirectory(prefix="ego-nav-test-", dir="/tmp") as directory:
        result = subprocess.run(
            [
                node,
                str(Path(__file__).parent / "fixtures" / "ego_navigation_error.mjs"),
                str(Path(ego_executor.__file__).with_name("ego_host.mjs")),
                directory,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr
    assert "safe navigation errors passed" in result.stdout


@pytest.mark.parametrize(
    "code,reason,retained",
    [
        ("ego_navigation_failed", "net::ERR_CONNECTION_CLOSED", True),
        ("ego_navigation_failed", "net::ERR_CERT_AUTHORITY_INVALID", True),
        ("ego_navigation_failed", "net::ERR_CONNECTION_CLOSED secret", False),
        ("ego_navigation_failed", "net::ERR_" + "X" * 65, False),
        ("ego_navigation_failed", {"url": "secret"}, False),
        ("ego_navigation_failed", None, False),
        ("login_required", "net::ERR_CONNECTION_CLOSED", False),
        ("ego_protocol_error", "net::ERR_CONNECTION_CLOSED", False),
    ],
)
async def test_socket_reader_preserves_valid_reason_only_for_navigation_failure(
    tmp_path, code, reason, retained
):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://allowed.example/",
            "allowed_origins": ["https://allowed.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=lock
    )
    future = asyncio.get_running_loop().create_future()
    session._pending[1] = future
    reader = asyncio.StreamReader()
    reader.feed_data(
        (json.dumps({"id": 1, "error": code, "navigation_error": reason}) + "\n").encode()
    )
    reader.feed_eof()
    await session._read(reader)
    try:
        with pytest.raises(BrowserWorkflowExecutionError) as caught:
            await future
        assert caught.value.code == code
        assert ("net::ERR_" in caught.value.detail) is retained
        assert "secret" not in caught.value.detail
    finally:
        await session.close()


@pytest.mark.parametrize(
    "detail,expected",
    [
        (
            "Ego could not load this page (net::ERR_CONNECTION_CLOSED)",
            "ego_navigation_failed (net::ERR_CONNECTION_CLOSED)",
        ),
        (
            "https://private.example/?token=secret (net::ERR_CONNECTION_CLOSED)",
            "ego_navigation_failed (net::ERR_CONNECTION_CLOSED)",
        ),
        ("net::ERR_CONNECTION_CLOSED secret", "ego_navigation_failed"),
    ],
)
async def test_scheduled_run_retains_reason_without_persisting_browser_text(
    tmp_path, detail, expected
):
    from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
    from openbiliclaw.sources.custom_sources import CustomSourceStore
    from openbiliclaw.sources.protocol import SourceFetchResult
    from openbiliclaw.sources.workflow_store import WorkflowStore
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "run.db")
    database.initialize()
    try:
        sources = CustomSourceStore(database)
        source = sources.upsert_source(
            name="Public books", entry_url="https://allowed.example/", intent="New books"
        )
        store = WorkflowStore(database)
        draft = store.save_draft(
            {
                "intent": "New books",
                "executor": "ego",
                "query_mode": "fixed",
                "status": "ready",
                "trial_success": True,
            }
        )
        workflow = store.publish(draft["draft_id"], source["source_id"])
        service = SimpleNamespace(
            sources=sources,
            store=store,
            parameters=lambda *_: {},
            fetch=AsyncMock(
                return_value=SourceFetchResult(
                    status="failed", error_code="ego_navigation_failed", detail=detail
                )
            ),
        )
        result = await CustomWorkflowProducer(
            database, source["source_id"], SimpleNamespace(), service=service
        ).produce_if_due(force=True)
        assert result["ran"] is True
        assert store.get_workflow(workflow["workflow_id"])["last_error"] == expected
        row = database.conn.execute("SELECT error FROM custom_workflow_runs").fetchone()
        assert row[0] == expected and "secret" not in row[0]
    finally:
        database.close()
