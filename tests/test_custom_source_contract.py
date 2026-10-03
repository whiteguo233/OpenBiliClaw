"""Executable capability boundaries for backend-owned dynamic Web discovery.

These tests exercise production entrypoints; no assertion reads the source
contract whose applicability declarations use this file as evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.runtime.init_prereqs import _PLATFORM_SOURCE_FIELDS
from openbiliclaw.runtime.source_incremental_sync import (
    _TASK_SPECS,
    SOURCE_ORDER,
    SourceIncrementalSync,
)
from openbiliclaw.saved_sync.adapters.bilibili import BilibiliNativeSaveAdapter
from openbiliclaw.saved_sync.adapters.extension import build_extension_native_save_adapters
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.sources.bootstrap_state import default_source_bootstrap_state
from openbiliclaw.sources.browser_workflow import (
    BrowserWorkflowItem,
    BrowserWorkflowPlan,
    BrowserWorkflowResult,
    compile_browser_workflow,
)
from openbiliclaw.sources.source_bootstrap import _BOOTSTRAP_TASK_TABLES
from openbiliclaw.sources.web_public import web_content_id
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://custom-source.example.test"
ENTRY_URL = f"{ORIGIN}/search"


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "custom-source-contract.db")
    db.initialize()
    yield db
    db.close()


def _plan() -> BrowserWorkflowPlan:
    return compile_browser_workflow(
        {
            "version": 1,
            "entry_url": ENTRY_URL,
            "allowed_origins": [ORIGIN],
            "steps": [
                {"action": "goto", "url": f"{ENTRY_URL}?q={{query}}"},
                {"action": "collect", "item_selector": "article", "link_selector": "h2 a"},
            ],
        }
    )


def _activate(service: WorkflowService, **overrides: Any) -> dict[str, Any]:
    value = {
        "entry_url": ENTRY_URL,
        "origin": ORIGIN,
        "name": "Contract articles",
        "intent": "Follow articles matching my current interests",
        "source_id": "",
        "session_id": "isolated-contract-session",
        "executor": "playwright",
        "query_mode": "none",
        "query": "",
        "status": "ready",
        "trial_success": True,
        "workflow": _plan().to_dict(),
    }
    value.update(overrides)
    draft = service.store.save_draft(value)
    return service.activate(draft["draft_id"])


def _browser_result(slug: str = "article") -> BrowserWorkflowResult:
    return BrowserWorkflowResult(
        status="ok",
        items=(
            BrowserWorkflowItem(
                title="A public article",
                url=f"{ORIGIN}/articles/{slug}#heading",
                author_name="Example author",
                published_at="2026-09-01T08:00:00Z",
                description="Metadata extracted from the approved page",
            ),
        ),
    )


def _service(database: Database, executor: Any) -> WorkflowService:
    def factory(name: str) -> Any:
        assert name == "playwright"
        return executor

    return WorkflowService(database, executor_factory=factory)


def _account_rows(database: Database) -> dict[str, list[tuple[Any, ...]]]:
    """Snapshot actual profile/event and built-in account-task storage only."""
    names = {
        row[0] for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    account_tables = {table for _, table, _ in _BOOTSTRAP_TASK_TABLES}
    names = {
        name
        for name in names
        if name in account_tables or any(word in name for word in ("event", "profile", "memory"))
    }
    return {
        name: [tuple(row) for row in database.conn.execute(f'SELECT * FROM "{name}"')]
        for name in sorted(names)
    }


async def test_custom_web_discovery_does_not_bootstrap_or_ingest_profile(database):
    """Web profile signals, incremental sync and refresh-mode have no account route."""
    executor = SimpleNamespace(run=AsyncMock(return_value=_browser_result()))
    service = _service(database, executor)
    source = _activate(service)
    source_id = source["source_id"]
    bootstrap_sources = {source for source, _, _ in _BOOTSTRAP_TASK_TABLES}
    for source_key in ("web", source_id):
        assert source_key not in bootstrap_sources
        assert source_key not in _PLATFORM_SOURCE_FIELDS
        assert source_key not in SOURCE_ORDER
        assert source_key not in _TASK_SPECS

    before = _account_rows(database)
    assert "events" in before and "profile_update_ledger" in before
    sink = Mock(return_value=1)
    profile = SimpleNamespace(get_profile=AsyncMock())
    producer = CustomWorkflowProducer(
        database,
        source_id,
        SimpleNamespace(enqueue_candidates=sink),
        service=service,
        soul_engine=profile,
        candidate_evaluation_owned_by_coordinator=True,
    )
    assert (await producer.produce_if_due(force=True))["enqueued"] == 1
    assert _account_rows(database) == before
    profile.get_profile.assert_not_awaited()

    # Even with global periodic sync enabled and a ready profile/extension,
    # enabling this source cannot schedule an upstream account bootstrap.
    memory = SimpleNamespace(
        load_source_bootstrap_state=Mock(return_value=default_source_bootstrap_state()),
        update_source_bootstrap_state=Mock(),
    )
    kick = AsyncMock()
    sync = SourceIncrementalSync(
        database=database,
        memory_manager=memory,
        presence=SimpleNamespace(is_present=lambda _: True),
        source_enabled={"web": True, source_id: True},
        scheduler_config=SchedulerConfig(source_incremental_enabled=True),
        profile_ready=lambda: True,
        init_active=lambda: False,
        kick=kick,
    )
    outcome = await sync.tick()
    assert outcome.reason == "not_due" and not outcome.created
    kick.assert_not_awaited()
    memory.update_source_bootstrap_state.assert_not_called()
    assert _account_rows(database) == before


async def test_custom_web_browser_normalization_omits_unsupported_metadata(database):
    """No media image or engagement view/like/favorite/comment/share/danmaku is fabricated."""
    executor = SimpleNamespace(run=AsyncMock(return_value=_browser_result()))
    service = _service(database, executor)
    source = _activate(service)
    workflow = service.store.list_workflows(source["source_id"])[0]
    result = await service.fetch(workflow, parameters={"query": "photography"})

    assert result.status == "ok" and len(result.items) == 1
    args, kwargs = executor.run.call_args
    assert isinstance(args[0], BrowserWorkflowPlan)
    assert args[0] == _plan()
    assert kwargs["session_id"] == source["session_id"]
    assert kwargs["parameters"].query == "photography"
    item = result.items[0]
    assert item.source_platform == "web"
    assert item.content_url == f"{ORIGIN}/articles/article"
    assert item.content_id == web_content_id(item.content_url)
    assert item.item_key == f"web:{item.content_id}"
    assert item.author_name == "Example author"
    assert item.published_at == "2026-09-01T08:00:00Z"
    assert item.cover_url == "" and item.engagement_available == []
    for metric in (
        "view_count",
        "like_count",
        "favorite_count",
        "collect_count",
        "comment_count",
        "share_count",
        "danmaku_count",
        "reply_count",
        "retweet_count",
        "bookmark_count",
    ):
        assert getattr(item, metric) == 0


async def test_custom_source_ids_do_not_change_canonical_web_identity(database):
    executor = SimpleNamespace(run=AsyncMock(return_value=_browser_result()))
    service = _service(database, executor)
    sources = [
        _activate(service, name=name, separate_source=True)
        for name in ("Articles", "Selected articles")
    ]
    assert sources[0]["source_id"] != sources[1]["source_id"]
    captured = []

    def enqueue(items, **kwargs):
        assert kwargs == {"source_context": "web-workflow"}
        captured.extend(items)
        return len(items)

    for source in sources:
        producer = CustomWorkflowProducer(
            database,
            source["source_id"],
            SimpleNamespace(enqueue_candidates=enqueue),
            service=service,
        )
        assert (await producer.produce_if_due(force=True))["enqueued"] == 1
    assert [item.source_id for item in captured] == [source["source_id"] for source in sources]
    assert [item.source_name for item in captured] == [source["name"] for source in sources]
    assert {item.source_platform for item in captured} == {"web"}
    assert len({item.item_key for item in captured}) == 1


@pytest.mark.parametrize("list_kind", ["favorite", "watch_later"])
async def test_custom_web_native_save_is_unsupported(database, list_kind):
    """Media native-save cannot mutate an upstream Web account; local saves still work."""
    broker, bili_client = Mock(), Mock()
    router = NativeSaveRouter(
        [BilibiliNativeSaveAdapter(bili_client), *build_extension_native_save_adapters(broker)]
    )
    service = SavedSyncService(database, router)
    item = SavedItemInput(
        source_platform="web",
        content_id=web_content_id(ENTRY_URL),
        content_url=ENTRY_URL,
        content_type="article",
        title="Local saved article",
    )
    assert service.save_local(list_kind, item).saved
    task = service.create_sync_task(list_kind, [item.item_key], "manual")
    result = await service.run_sync_task(task.task_id)
    assert len(result.items) == 1
    assert result.items[0].status == "unsupported"
    assert result.items[0].error_code == "unsupported_adapter_missing"
    assert database.get_saved_membership(list_kind, item.item_key) is not None
    assert not broker.mock_calls and not bili_client.mock_calls


def assert_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute production browser helpers")
    result = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_custom_web_extension_tasks_and_cookie_sync_are_absent():
    """No extension task/marker/background/early-response or cookie-sync route owns Web sites.

    Content and WAR matches cannot inject a task marker or content executor;
    the service worker registers only built-in dispatchers, and the actual
    cookie listener ignores this source's origin and unknown runtime requests.
    Backend browser execution is exercised separately through WorkflowService.
    """
    manifest = json.loads((ROOT / "extension/manifest.json").read_text())
    registrations = manifest["content_scripts"] + manifest["web_accessible_resources"]
    matches = [pattern for entry in registrations for pattern in entry["matches"]]
    assert any(fnmatchcase("https://www.bilibili.com/video/BV123", p) for p in matches)
    assert all(p != "<all_urls>" and not fnmatchcase(ENTRY_URL, p) for p in matches)
    worker = (ROOT / "extension/src/background/service-worker.ts").read_text()
    dispatchers = set(re.findall(r'from "\./([^"/]+)-task-dispatcher\.(?:js|ts)"', worker))
    assert dispatchers == {
        "bili",
        "xhs",
        "dy",
        "yt",
        "x",
        "zhihu",
        "reddit",
        "linuxdo",
        "v2ex",
        "weibo",
    }
    assert_node(
        """
        import assert from 'node:assert/strict';
        import {startCookieSync, handleCookieSyncRuntimeEvent}
          from './extension/src/background/cookie-sync.ts';
        const listeners = [];
        const schedules = [];
        globalThis.chrome = {
          cookies: {getAll: async () => [], onChanged: {addListener: f => listeners.push(f)}},
          alarms: {create() {}, clear() {}},
        };
        globalThis.fetch = async () => new Response('{}', {status: 200});
        startCookieSync();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(listeners.length, 1);
        globalThis.setTimeout = (...args) => { schedules.push(args); return 1; };
        listeners[0]({
          cookie: {domain: '.custom-source.example.test', name: 'session'}, removed: false
        });
        assert.equal(schedules.length, 0);
        assert.equal(handleCookieSyncRuntimeEvent({type: 'web_cookie_sync_requested'}), false);
        assert.equal(handleCookieSyncRuntimeEvent({
          type: 'custom_source_cookie_sync_requested'
        }), false);
        listeners[0]({cookie: {domain: '.bilibili.com', name: 'SESSDATA'}, removed: false});
        assert.equal(schedules.length, 1);
        """
    )


def test_custom_web_link_has_no_native_scheme():
    """Media deep-link for a dynamic Web origin uses the canonical browser fallback."""
    assert_node(
        """
        import assert from 'node:assert/strict';
        import {buildAppDeepLink} from './src/openbiliclaw/web/js/app-launch.js';
        assert.equal(buildAppDeepLink('https://custom-source.example.test/articles/one'), '');
        assert.equal(buildAppDeepLink('https://custom-source.example.test/video/BV123'), '');
        assert.match(buildAppDeepLink('https://www.bilibili.com/video/BV123'), /^bilibili:/);
        """
    )


def _coordinator():
    keywords = {
        "regular": SimpleNamespace(id=101, keyword="photography"),
        "explore": SimpleNamespace(id=202, keyword="architecture"),
    }
    return SimpleNamespace(
        keywords=keywords,
        claim=Mock(side_effect=lambda source_id, *, n, keyword_kind: [keywords[keyword_kind]]),
        rollback=Mock(),
        requeue_transient=Mock(),
        mark_used=Mock(),
    )


async def test_custom_producer_keyword_provenance(database):
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=lambda plan, *, session_id, parameters: _browser_result(parameters.query)
        )
    )
    service = _service(database, executor)
    source = _activate(service, query_mode="interest")
    coordinator = _coordinator()
    captured = []

    def enqueue(items, **kwargs):
        assert kwargs == {"source_context": "web-workflow"}
        captured.extend(items)
        return len(items)

    producer = CustomWorkflowProducer(
        database,
        source["source_id"],
        SimpleNamespace(enqueue_candidates=enqueue),
        service=service,
        keyword_fetch_coordinator=coordinator,
    )
    for kind in ("regular", "explore"):
        assert (await producer.produce_if_due(force=True))["enqueued"] == 1
        keyword = coordinator.keywords[kind]
        coordinator.claim.assert_called_with(source["source_id"], n=1, keyword_kind=kind)
        coordinator.mark_used.assert_called_with([keyword])
        item = captured[-1]
        assert item.source_keyword_id == keyword.id
        assert item.source_metadata["keyword_id"] == keyword.id
        assert item.source_metadata["keyword"] == keyword.keyword
        assert item.source_id == source["source_id"] and item.source_platform == "web"
        parameters = executor.run.call_args.kwargs["parameters"]
        assert parameters.query == keyword.keyword
        run = service.store.runs(source["source_id"])[0]
        persisted = database.conn.execute(
            "SELECT parameters FROM custom_workflow_runs WHERE run_id=?", (run["run_id"],)
        ).fetchone()[0]
        assert json.loads(persisted) == {
            "query": keyword.keyword,
            "date_from": parameters.date_from,
            "date_to": parameters.date_to,
        }
        assert item.source_metadata["run_id"] == run["run_id"]
    assert coordinator.mark_used.call_count == 2
    coordinator.rollback.assert_not_called()
    coordinator.requeue_transient.assert_not_called()
    # Rotation is based on durable workflow history, not the 100-row API page.
    for index in range(99):
        database.conn.execute(
            "INSERT INTO custom_workflow_runs "
            "(run_id,workflow_id,version,lease_token,executor,parameters,status,started_at) "
            "SELECT ?,workflow_id,version,'',executor,parameters,'ok',started_at "
            "FROM custom_workflow_runs LIMIT 1",
            (f"historical-{index}",),
        )
    database.conn.commit()
    await producer.produce_if_due(force=True)
    coordinator.claim.assert_called_with(source["source_id"], n=1, keyword_kind="explore")


@pytest.mark.parametrize("failure", ["exhausted", "lease_busy", "login_required", "queue_full"])
async def test_custom_producer_keyword_failures_preserve_retry(database, failure):
    browser_result = (
        BrowserWorkflowResult(status="login_required", scope_complete=False)
        if failure == "login_required"
        else _browser_result()
    )
    executor = SimpleNamespace(run=AsyncMock(return_value=browser_result))
    service = _service(database, executor)
    source = _activate(service, query_mode="interest")
    workflow = service.store.list_workflows(source["source_id"])[0]
    coordinator = _coordinator()
    sink = Mock(return_value=0 if failure == "queue_full" else 1)
    if failure == "exhausted":
        coordinator.claim.side_effect = lambda *args, **kwargs: []
    if failure == "lease_busy":
        assert service.store.claim(workflow["workflow_id"], {"query": "in flight"}, force=True)
    producer = CustomWorkflowProducer(
        database,
        source["source_id"],
        SimpleNamespace(enqueue_candidates=sink),
        service=service,
        keyword_fetch_coordinator=coordinator,
    )
    result = await producer.produce_if_due(force=True)
    coordinator.mark_used.assert_not_called()
    assert result["enqueued"] == 0
    assert service.store.get_workflow(workflow["workflow_id"])["cursor"] == {}
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 0
    if failure == "exhausted":
        assert result["reason"] == "keyword_unavailable"
        assert [c.kwargs["keyword_kind"] for c in coordinator.claim.call_args_list] == [
            "regular",
            "explore",
        ]
        executor.run.assert_not_awaited()
        coordinator.rollback.assert_not_called()
        coordinator.requeue_transient.assert_not_called()
    elif failure == "lease_busy":
        executor.run.assert_not_awaited()
        coordinator.rollback.assert_called_once_with(coordinator.keywords["explore"])
        coordinator.requeue_transient.assert_not_called()
    else:
        assert result["reason"] == ("degraded" if failure == "queue_full" else "login_required")
        coordinator.requeue_transient.assert_called_once_with(coordinator.keywords["regular"])
        coordinator.rollback.assert_not_called()
        if failure == "queue_full":
            sink.return_value = 1
            assert (await producer.produce_if_due(force=True))["enqueued"] == 1
            assert (
                database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0]
                == 1
            )
        else:
            assert not (await producer.produce_if_due(force=True))["ran"]
            executor.run.assert_awaited_once()
