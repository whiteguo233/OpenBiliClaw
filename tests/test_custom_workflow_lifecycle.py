"""Behavioural coverage for trial, approval, durable runs and independent sources."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.sources.web_recipe import build_web_recipe
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "custom.db")
    db.initialize()
    manager = WorkflowService(db)
    yield manager
    db.close()


def draft(service, **overrides):
    value = {
        "entry_url": "https://example.com/feed",
        "origin": "https://example.com",
        "name": "Example articles",
        "intent": "Follow the latest articles",
        "source_id": "",
        "session_id": "session-a",
        "executor": "http",
        "query_mode": "none",
        "query": "",
        "status": "ready",
        "trial_success": True,
        "workflow": build_web_recipe(
            {
                "name": "Example",
                "strategy": "feed",
                "config": {"entry_url": "https://example.com/feed"},
            }
        ),
    }
    value.update(overrides)
    return service.store.save_draft(value)


def item():
    return DiscoveredContent(
        title="A discovered article",
        content_id="one",
        content_url="https://example.com/articles/one",
        source_platform="web",
        source_strategy="web-feed",
    )


@pytest.mark.asyncio
async def test_browser_fetch_preserves_web_article_metadata(service):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "article", "link_selector": "h2 a"}],
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            return_value=BrowserWorkflowResult(
                "ok", (BrowserWorkflowItem("Forum topic", "https://example.com/read?tid=7"),)
            )
        )
    )
    service._executor_factory = lambda name: executor
    workflow = draft(service, executor="ego", workflow=plan)

    result = await service.fetch(workflow)

    assert result.status == "ok" and len(result.items) == 1
    assert result.items[0].content_type == "article"
    assert result.items[0].source_platform == "web"
    assert result.items[0].content_url == "https://example.com/read?tid=7"


@pytest.mark.asyncio
async def test_blank_login_resource_failure_does_not_ask_for_more_user_details(service):
    pending = draft(service, executor="playwright", status="draft", workflow=None)
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(
        return_value=(
            session,
            {
                "elements": [{"tag": "a", "text": ""}],
                "resource_failures": [
                    {"origin": "https://cdn.example.com", "resource_type": "Script", "status": 418}
                ],
            },
        )
    )
    service._json_task = AsyncMock()
    result = await service.author_browser(pending)
    assert result["status"] == "failed"
    assert "HTTP 418" in result["detail"]
    service._json_task.assert_not_called()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "login_evidence",
    [
        {"login_wall": True},
        {"blocked_navigation_origins": ["https://accounts.example.com"]},
    ],
)
async def test_login_evidence_takes_priority_over_blank_page_resource_failure(
    service, login_evidence
):
    pending = draft(
        service, executor="playwright", status="draft", workflow=None, trial_success=False
    )
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(
        return_value=(
            session,
            {
                "elements": [],
                "resource_failures": [
                    {"origin": "https://cdn.example.com", "resource_type": "Script", "status": 404}
                ],
                **login_evidence,
            },
        )
    )
    service._json_task = AsyncMock()
    result = await service.author_browser(pending)
    assert result["status"] == "needs_login"
    assert result["requires_login"] is True
    assert result["trial_success"] is False
    assert result["takeover_url"] == f"/web/source-login.html?draft_id={pending['draft_id']}"
    assert service.store.get_draft(pending["draft_id"])["status"] == "needs_login"
    service._json_task.assert_not_called()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_reauthor_receives_failed_trial_evidence_without_changing_user_scope(service):
    pending = draft(
        service,
        executor="playwright",
        status="failed",
        detail="Collection selectors need verification",
        sample_items=[{"title": "A real result"}],
    )
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(
        return_value=(session, {"elements": [{"tag": "h2", "text": "A real result"}]})
    )
    service._json_task = AsyncMock(return_value={"needs_details": "Which content list?"})
    await service.author_browser(pending)
    author_input = service._json_task.call_args.args[1]
    assert author_input["previous_trial"] == {
        "status": "failed",
        "detail": "Collection selectors need verification",
        "sample_count": 1,
    }
    assert author_input["entry_url"] == pending["entry_url"]
    assert author_input["intent"] == pending["intent"]


@pytest.mark.asyncio
async def test_ego_trial_repairs_only_proven_relative_href_fields_and_retries_once(service):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    before = 'td.content > a.topic[href^="https://example.com/read.php"]'
    after = 'td.content > a.topic[href^="/read.php"]'
    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics?board=7",
        "allowed_origins": ["https://example.com"],
        "budgets": {"max_items": 3, "max_pages": 1, "timeout_seconds": 45},
        "steps": [
            {
                "action": "collect",
                "item_selector": 'tr.topic:has(a[href^="/read.php"])',
                "link_selector": before,
                "title_selector": before,
            }
        ],
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                BrowserWorkflowResult(
                    "failed",
                    error_code="extraction_unverified",
                    scope_complete=False,
                    observation={"href_selector_repairs": ["link_selector", "title_selector"]},
                ),
                BrowserWorkflowResult(
                    "ok", (BrowserWorkflowItem("A topic", "https://example.com/read.php?tid=1"),)
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor
    pending = draft(
        service,
        executor="ego",
        workflow=plan,
        status="draft",
        trial_success=False,
        entry_url=plan["entry_url"],
    )

    result = await service.trial(pending["draft_id"])

    assert result["status"] == "ready" and result["trial_success"] is True
    assert executor.run.await_count == 2
    original = executor.run.call_args_list[0].args[0].to_dict()
    corrected = executor.run.call_args_list[1].args[0].to_dict()
    expected = dict(
        original, steps=[dict(original["steps"][0], link_selector=after, title_selector=after)]
    )
    assert corrected == expected == result["workflow"]
    assert result["sample_items"][0]["url"] == "https://example.com/read.php?tid=1"
    assert service.sources.list_sources() == []
    assert service.store.get_draft(pending["draft_id"])["workflow"] == corrected


@pytest.mark.asyncio
async def test_ego_trial_repairs_zero_row_item_href_predicate_preserving_content_filter(service):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    selector = '#topics > tbody > tr.topic:has(a.topic[href^="/read.php"])'
    field = 'td.content > a.topic[href^="https://example.com/read.php"]'
    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics?board=7",
        "allowed_origins": ["https://example.com"],
        "budgets": {"max_items": 3, "max_pages": 1, "timeout_seconds": 45},
        "steps": [
            {
                "action": "collect",
                "item_selector": selector,
                "link_selector": field,
                "title_selector": field,
            }
        ],
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                BrowserWorkflowResult(
                    "failed",
                    error_code="extraction_unverified",
                    observation={"item_href_selector_repair": {"origin": "https://example.com"}},
                ),
                BrowserWorkflowResult(
                    "ok", (BrowserWorkflowItem("A topic", "https://example.com/read.php?tid=1"),)
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor
    pending = draft(
        service,
        executor="ego",
        workflow=plan,
        status="failed",
        trial_success=False,
        entry_url=plan["entry_url"],
    )
    result = await service.trial(pending["draft_id"])
    assert result["status"] == "ready" and result["trial_success"] is True
    assert executor.run.await_count == 2
    original = executor.run.call_args_list[0].args[0].to_dict()
    corrected = executor.run.call_args_list[1].args[0].to_dict()
    expected = dict(
        original,
        steps=[
            dict(
                original["steps"][0],
                item_selector=selector.replace('"/read.php"', '"https://example.com/read.php"'),
            )
        ],
    )
    assert corrected == expected == result["workflow"]
    assert service.store.get_draft(pending["draft_id"])["workflow"] == corrected
    assert result["sample_items"][0]["url"] == "https://example.com/read.php?tid=1"
    assert service.sources.list_sources() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retry_outcome", ["failed", "exception", "empty", "degraded", "foreign_only"]
)
@pytest.mark.parametrize("repair_kind", ["field", "item"])
async def test_unsuccessful_ego_field_repair_preserves_original_plan(
    service, retry_outcome, repair_kind
):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult

    selector = 'a.topic[href^="https://example.com/read.php"]'
    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics?board=7",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "tr.topic", "link_selector": selector}],
    }
    evidence = {"href_selector_repairs": ["link_selector"]}
    if repair_kind == "item":
        plan["steps"][0]["item_selector"] = 'tr.topic:has(a.topic[href^="/read.php"])'
        evidence = {"item_href_selector_repair": {"origin": "https://example.com"}}
    outcomes = {
        "failed": BrowserWorkflowResult(
            "failed", detail="Still invalid", error_code="extraction_unverified"
        ),
        "exception": RuntimeError("Native host disconnected"),
        "empty": BrowserWorkflowResult("empty"),
        "degraded": BrowserWorkflowResult(
            "degraded",
            (BrowserWorkflowItem("One", "https://example.com/read.php?tid=1"),),
            error_code="extraction_unverified",
        ),
        "foreign_only": BrowserWorkflowResult(
            "ok", (BrowserWorkflowItem("Other", "https://other.example/read.php?tid=1"),)
        ),
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                BrowserWorkflowResult(
                    "failed",
                    error_code="extraction_unverified",
                    scope_complete=False,
                    observation=evidence,
                ),
                outcomes[retry_outcome],
            ]
        )
    )
    service._executor_factory = lambda name: executor
    pending = draft(
        service,
        executor="ego",
        workflow=plan,
        status="failed",
        trial_success=False,
        entry_url=plan["entry_url"],
    )
    result = await service.trial(pending["draft_id"])
    assert result["status"] == "failed" and result["trial_success"] is False
    assert executor.run.await_count == 2
    assert result["workflow"] == plan
    assert service.store.get_draft(pending["draft_id"])["workflow"] == plan
    assert service.sources.list_sources() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,fields",
    [
        ('a[href^="https://other.example/read.php"]', ["link_selector"]),
        ('a[href^="https://example.com/read.php?topic=safe"]', ["link_selector"]),
        ('a[href^="https://example.com/read.php#safe"]', ["link_selector"]),
        ('a.to\\pic[href^="https://example.com/read.php"]', ["link_selector"]),
        ('a[href^="https://example.com/read.php" i]', ["link_selector"]),
        ('a[href^="https://example.com/read.php"],a.other', ["link_selector"]),
        ('a[href^="https://example.com/read.php"]', ["item_selector"]),
        ('a[href^="https://example.com/read.php"]', [{"field": "link_selector"}]),
    ],
)
async def test_ego_trial_rejects_unsupported_field_evidence(service, selector, fields):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowResult

    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "tr.topic", "link_selector": selector}],
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            return_value=BrowserWorkflowResult(
                "failed",
                error_code="extraction_unverified",
                scope_complete=False,
                observation={"href_selector_repairs": fields},
            )
        )
    )
    service._executor_factory = lambda name: executor
    pending = draft(service, executor="ego", workflow=plan, status="failed", trial_success=False)
    result = await service.trial(pending["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == plan
    executor.run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,evidence",
    [
        ('tr.topic:has(a[href^="/read.php"])', {"origin": "https://other.example"}),
        ('tr.topic:has(a[href^="/read.php?scope=safe"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="/read.php#safe"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="//other.example/read.php"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="/../read.php"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="/read.php" i])', {"origin": "https://example.com"}),
        ('tr.topic:has(a.to\\pic[href^="/read.php"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="/read.php"]),tr.other', {"origin": "https://example.com"}),
        ('tr.topic:not(.ad):has(a[href^="/read.php"])', {"origin": "https://example.com"}),
        ('tr.topic:has(a[href^="/read.php"])', {"origin": "https://example.com/"}),
        ('tr.topic:has(a[href^="/read.php"])', {"origin": ["https://example.com"]}),
        ('tr.topic:has(a[href^="/read.php"])', ["https://example.com"]),
    ],
)
async def test_ego_trial_rejects_unsupported_item_evidence(service, selector, evidence):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowResult

    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": selector, "link_selector": "a"}],
    }
    executor = SimpleNamespace(
        run=AsyncMock(
            return_value=BrowserWorkflowResult(
                "failed",
                error_code="extraction_unverified",
                observation={"item_href_selector_repair": evidence},
            )
        )
    )
    service._executor_factory = lambda name: executor
    pending = draft(service, executor="ego", workflow=plan, status="failed", trial_success=False)
    result = await service.trial(pending["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == plan
    executor.run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_kind", ["field", "item"])
async def test_published_fetch_does_not_repair_selectors(service, repair_kind):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowResult

    plan = {
        "version": 1,
        "entry_url": "https://example.com/topics",
        "allowed_origins": ["https://example.com"],
        "steps": [
            {
                "action": "collect",
                "item_selector": "tr.topic",
                "link_selector": 'a[href^="https://example.com/read.php"]',
            }
        ],
    }
    evidence = {"href_selector_repairs": ["link_selector"]}
    if repair_kind == "item":
        plan["steps"][0]["item_selector"] = 'tr.topic:has(a.topic[href^="/read.php"])'
        evidence = {"item_href_selector_repair": {"origin": "https://example.com"}}
    executor = SimpleNamespace(
        run=AsyncMock(
            return_value=BrowserWorkflowResult(
                "failed",
                error_code="extraction_unverified",
                scope_complete=False,
                observation=evidence,
            )
        )
    )
    service._executor_factory = lambda name: executor
    workflow = draft(service, executor="ego", workflow=plan)
    result = await service.fetch(workflow)
    assert result.status == "failed" and result.items == []
    assert workflow["workflow"] == plan
    executor.run.assert_awaited_once()


def test_draft_never_reserves_capacity_and_activation_is_idempotent(service):
    pending = draft(service, status="needs_login", trial_success=False)
    assert service.sources.list_sources() == []
    with pytest.raises(ValueError, match="trial"):
        service.activate(pending["draft_id"])
    ready = draft(service)
    first = service.activate(ready["draft_id"], target_share=3)
    second = service.activate(ready["draft_id"], target_share=9)
    assert first["source_id"] == second["source_id"]
    assert second["target_share"] == 3
    assert len(service.store.list_workflows(first["source_id"])) == 1


def test_second_workflow_preserves_source_policy_and_disabled_state(service):
    source = service.activate(draft(service)["draft_id"], target_share=4)
    service.sources.update_source(source["source_id"], enabled=False)
    another = draft(service, source_id=source["source_id"], intent="Latest release notes")
    result = service.activate(another["draft_id"], target_share=1)
    assert result["enabled"] is False and result["target_share"] == 4
    assert len(service.store.list_workflows(source["source_id"])) == 2


def test_activation_rolls_back_source_if_publishing_fails(service, monkeypatch):
    prepared = draft(service)

    def reject(*args):
        raise RuntimeError("version storage unavailable")

    monkeypatch.setattr(service.store, "publish", reject)
    with pytest.raises(RuntimeError, match="storage"):
        service.activate(prepared["draft_id"])
    assert service.sources.list_sources() == []


def test_concurrent_activation_allocates_one_source(service):
    from concurrent.futures import ThreadPoolExecutor

    prepared = draft(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(service.activate, prepared["draft_id"]) for _ in range(2)]
        results = [call.result() for call in calls]
    assert results[0]["source_id"] == results[1]["source_id"]
    assert len(service.sources.list_sources()) == 1


def test_version_and_disable_fence_block_late_worker(service):
    source = service.activate(draft(service)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {"query": "today"})
    assert run
    service.sources.update_source(source["source_id"], enabled=False)
    calls = []
    result = service.store.complete(
        run,
        status="ok",
        items=[item()],
        enqueue=lambda items: calls.append(items) or 1,
        interval_minutes=30,
    )
    assert result["status"] == "stale" and calls == []
    service.sources.update_source(source["source_id"], enabled=True)
    replacement = draft(
        service, source_id=source["source_id"], workflow_id=workflow["workflow_id"], base_version=1
    )
    service.store.publish(replacement["draft_id"], source["source_id"])
    result = service.store.complete(
        run,
        status="ok",
        items=[item()],
        enqueue=lambda items: calls.append(items) or 1,
        interval_minutes=30,
    )
    assert result["status"] == "stale" and calls == []


@pytest.mark.asyncio
async def test_periodic_handoff_dedupes_and_preserves_canonical_identity(service):
    source = service.activate(draft(service)["draft_id"])
    service.fetch = AsyncMock(return_value=SourceFetchResult(items=[item()]))
    accepted = []
    pipeline = SimpleNamespace(
        enqueue_candidates=lambda items, **_: accepted.extend(items) or len(items)
    )
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], pipeline, service=service
    )
    first = await producer.produce_if_due(force=True)
    second = await producer.produce_if_due(force=True)
    assert first["enqueued"] == 1 and second["enqueued"] == 0
    assert len(accepted) == 1
    assert accepted[0].source_platform == "web"
    assert accepted[0].content_id == "one"
    assert accepted[0].source_id == source["source_id"]
    assert len(service.store.runs(source["source_id"])) == 2


@pytest.mark.asyncio
async def test_failed_handoff_does_not_mark_seen_or_advance_cursor(service, monkeypatch):
    source = service.activate(draft(service)["draft_id"])
    service.fetch = AsyncMock(return_value=SourceFetchResult(items=[item()], cursor={"page": 2}))

    def reject(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    pipeline = SimpleNamespace(enqueue_candidates=reject)
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], pipeline, service=service
    )
    result = await producer.produce_if_due(force=True)
    assert result["reason"] == "failed"
    workflow = service.store.list_workflows(source["source_id"])[0]
    assert workflow["cursor"] == {}
    pipeline.enqueue_candidates = lambda items, **_: len(items)
    assert (await producer.produce_if_due(force=True))["reason"] == "backoff"
    monkeypatch.setattr(
        "openbiliclaw.sources.workflow_store.time",
        SimpleNamespace(time=lambda: workflow["next_run_at"] + 1),
    )
    assert (await producer.produce_if_due(force=True))["enqueued"] == 1


@pytest.mark.asyncio
async def test_expired_login_pauses_until_manual_resume(service):
    source = service.activate(draft(service)["draft_id"])
    service.fetch = AsyncMock(
        return_value=SourceFetchResult(status="login_required", scope_complete=False)
    )
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], object(), service=service
    )
    await producer.produce_if_due(force=True)
    assert service.list_sources()[0]["health"]["status"] == "login_required"
    assert (await producer.produce_if_due(force=True))["ran"] is False
    assert service.fetch.await_count == 1


def test_api_projects_public_state_and_rejects_scope_mutation(service):
    service.executor = lambda name: SimpleNamespace(availability=lambda: {"available": False})
    source = service.activate(draft(service)["draft_id"])
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    client = TestClient(app)
    items = client.get("/api/custom-sources").json()["items"]
    assert items[0]["source_id"] == source["source_id"]
    assert items[0]["workflows"][0]["version"] == 1
    assert (
        client.patch(
            f"/api/custom-sources/{source['source_id']}",
            json={"entry_url": "https://other.example"},
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/api/custom-sources/{source['source_id']}", json={"target_share": 8}).json()[
            "source"
        ]["target_share"]
        == 8
    )
    assert client.delete(f"/api/custom-sources/{source['source_id']}").status_code == 200
    assert service.sources.get_source(source["source_id"])["enabled"] is False


@pytest.mark.asyncio
async def test_queue_pressure_leaves_unaccepted_items_retryable(service, monkeypatch):
    source = service.activate(draft(service)["draft_id"])
    service.fetch = AsyncMock(return_value=SourceFetchResult(items=[item()], cursor={"next": 2}))
    pipeline = SimpleNamespace(enqueue_candidates=lambda items, **_: 0)
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], pipeline, service=service
    )
    assert (await producer.produce_if_due(force=True))["reason"] == "degraded"
    workflow = service.store.list_workflows(source["source_id"])[0]
    assert workflow["cursor"] == {}
    pipeline.enqueue_candidates = lambda items, **_: len(items)
    assert (await producer.produce_if_due(force=True))["reason"] == "backoff"
    monkeypatch.setattr(
        "openbiliclaw.sources.workflow_store.time",
        SimpleNamespace(time=lambda: workflow["next_run_at"] + 1),
    )
    assert (await producer.produce_if_due(force=True))["enqueued"] == 1


def test_chat_summary_preserves_resume_identity_with_long_intent():
    import json

    from openbiliclaw.agent.tools.custom_source_tools import _bounded_summary

    draft_id = "08c1f0c7-7823-428e-af46-49a2ab568a74"
    payload = {
        "draft_id": draft_id,
        "status": "needs_login",
        "intent": "x" * 4000,
        "detail": "a" * 2000,
        "sample_items": [{"title": "y" * 3000}],
        "takeover_url": f"/web/source-login.html?draft_id={draft_id}",
    }
    encoded = _bounded_summary(
        payload, SimpleNamespace(agent=SimpleNamespace(tool_result_max_chars=200))
    )
    assert len(encoded) <= 200
    assert json.loads(encoded)["draft_id"] == draft_id
    assert json.loads(encoded)["takeover_url"] == payload["takeover_url"]


def test_bounded_success_stays_healthy_without_committing_frontier(service):
    source = service.activate(draft(service)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(
        run,
        status="ok",
        items=[item()],
        enqueue=lambda items: len(items),
        interval_minutes=30,
        cursor={"etag": "new"},
        scope_complete=False,
    )
    current = service.store.get_workflow(workflow["workflow_id"])
    assert current["consecutive_failures"] == 0
    assert current["last_success_at"] > 0
    assert current["cursor"] == {}


def test_partial_recovery_moves_retry_cursor_without_committing_validators(service):
    source = service.activate(draft(service)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(
        run,
        status="degraded",
        items=[item()],
        enqueue=lambda items: len(items),
        interval_minutes=30,
        scope_complete=False,
        cursor={"etag": "unsafe", "cursor": {"page": 9}, "retry_cursor": {"page": 2}},
    )
    current = service.store.get_workflow(workflow["workflow_id"])
    assert current["cursor"] == {"retry_cursor": {"page": 2}}


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "failed", "needs_details"])
async def test_author_observes_resources_but_never_accepts_model_scope_expansion(
    service, previous_status
):
    from openbiliclaw.sources.browser_workflow import BrowserWorkflowResult

    snapshots = [
        {"blocked_resource_origins": ["https://static.example.com"], "elements": []},
        {"elements": [{"tag": "article", "selector": "article"}]},
    ]
    plans, sessions = [], []

    async def open_session(plan, **kwargs):
        plans.append(plan)
        assert kwargs["observation_only"] is True
        session = SimpleNamespace(
            snapshot=AsyncMock(return_value=snapshots.pop(0)), close=AsyncMock()
        )
        sessions.append(session)
        return session

    executor = SimpleNamespace(
        open_session=open_session, run=AsyncMock(return_value=BrowserWorkflowResult(status="empty"))
    )
    service._executor_factory = lambda name: executor
    authored = {
        "plan": {
            "version": 1,
            "entry_url": "https://example.com/feed",
            "allowed_origins": ["https://example.com"],
            "resource_origins": ["https://invented.example.com"],
            "auth": {"login_origins": ["https://invented.example.com"]},
            "steps": [
                {
                    "action": "collect",
                    "item_selector": "article",
                    "link_selector": "a",
                    "empty_selector": ".empty",
                }
            ],
        },
        "query_mode": "none",
        "query": "",
    }
    service._json_task = AsyncMock(return_value=authored)
    prepared = draft(
        service, executor="playwright", workflow=None, status=previous_status, trial_success=False
    )
    result = await service.author_browser(prepared)
    assert result["status"] == "ready"
    assert plans[0].resource_origins == ()
    assert plans[1].resource_origins == ("https://static.example.com",)
    assert result["workflow"]["resource_origins"] == ["https://static.example.com"]
    assert result["workflow"]["auth"]["login_origins"] == []
    assert all(session.close.await_count == 1 for session in sessions)


def test_manual_takeover_requires_observed_login_origin(service):
    prepared = draft(
        service,
        executor="playwright",
        workflow=None,
        status="needs_login",
        suggested_login_origins=["https://login.example.com"],
    )
    observed = []
    session = SimpleNamespace(close=AsyncMock())

    async def observe(value, *, interactive):
        assert interactive
        observed.append(value)
        return session, {"elements": [], "blocked_navigation_origins": []}

    service.observe_session = observe
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        path = f"/api/custom-sources/drafts/{prepared['draft_id']}/takeover"
        assert (
            client.post(path, json={"login_origin": "https://unknown.example.com"}).status_code
            == 409
        )
        assert observed == []
        assert (
            client.post(path, json={"login_origin": "https://login.example.com"}).status_code == 200
        )
        assert observed[0]["login_origins"] == ["https://login.example.com"]
        assert observed[0]["origin"] == "https://example.com"
    session.close.assert_awaited_once()


def test_cli_activation_and_policy_use_shared_service(service, monkeypatch):
    from typer.testing import CliRunner

    from openbiliclaw import cli_custom_sources
    from openbiliclaw.cli import app

    prepared = draft(service)
    monkeypatch.setattr(cli_custom_sources, "_service", lambda **kwargs: service)
    runner = CliRunner()
    result = runner.invoke(
        app, ["sources", "activate", prepared["draft_id"], "--share", "4", "--interval", "60"]
    )
    assert result.exit_code == 0, result.output
    source = service.sources.list_sources()[0]
    assert source["target_share"] == 4 and source["interval_minutes"] == 60
    result = runner.invoke(app, ["sources", "set", source["source_id"], "--disabled"])
    assert result.exit_code == 0, result.output
    assert service.sources.get_source(source["source_id"])["enabled"] is False
    assert (
        runner.invoke(app, ["sources", "set", source["source_id"], "--share", "0"]).exit_code != 0
    )


def test_browser_install_api_shares_one_background_job(service, monkeypatch):
    import asyncio

    from openbiliclaw.sources import browser_install

    started = []

    async def install():
        started.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(browser_install, "install_browser_runtime", install)
    service.executor = lambda name: SimpleNamespace(availability=lambda: {"available": False})
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/api/custom-sources/executors/playwright/install")
            assert response.status_code == 202
            assert response.json()["status"] == "installing"
        assert len(started) == 1
        status = client.get("/api/custom-sources").json()["executors"]
        assert next(row for row in status if row["id"] == "playwright")["status"] == "installing"


def test_same_site_prepared_drafts_merge_unless_explicitly_separated(service):
    first, second, separate = draft(service), draft(service), draft(service, separate_source=True)
    source = service.activate(first["draft_id"], target_share=4)
    service.sources.update_source(source["source_id"], enabled=False)
    merged = service.activate(second["draft_id"], target_share=8)
    assert merged["source_id"] == source["source_id"]
    assert merged["target_share"] == 4 and not merged["enabled"]
    split = service.activate(separate["draft_id"], target_share=2)
    assert split["source_id"] != source["source_id"]
    assert len(service.store.list_workflows(source["source_id"])) == 2
    assert len(service.sources.list_sources()) == 2


@pytest.mark.asyncio
async def test_stale_failed_run_cannot_repair_a_replacement_version(service):
    plan = {
        "version": 1,
        "entry_url": "https://example.com/feed",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
    }
    source = service.activate(draft(service, executor="playwright", workflow=plan)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    replacement = draft(
        service,
        source_id=source["source_id"],
        workflow_id=workflow["workflow_id"],
        base_version=1,
        executor="playwright",
        workflow=plan,
    )

    async def late_result(*args, **kwargs):
        service.store.publish(replacement["draft_id"], source["source_id"])
        return SourceFetchResult(
            status="failed", scope_complete=False, error_code="extraction_unverified"
        )

    service.fetch = late_result
    service.author_browser = AsyncMock()
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], object(), service=service, llm_service=object()
    )
    result = await producer.produce_if_due(force=True)
    assert result["reason"] == "stale"
    service.author_browser.assert_not_awaited()
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2


@pytest.mark.asyncio
async def test_author_accepts_json_fences_and_rejects_unrelated_model_text(service):
    service.llm_service = SimpleNamespace(
        complete_structured_task=AsyncMock(
            return_value=SimpleNamespace(content='```json\n{"needs_details":"Which section?"}\n```')
        )
    )
    assert await service._json_task("compile", {}) == {"needs_details": "Which section?"}
    service.llm_service.complete_structured_task.return_value.content = (
        "I think this site looks nice."
    )
    with pytest.raises(ValueError, match="invalid object"):
        await service._json_task("compile", {})


@pytest.mark.asyncio
async def test_repair_trial_cannot_publish_after_source_is_disabled(service):
    plan = {
        "version": 1,
        "entry_url": "https://example.com/feed",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
    }
    source = service.activate(draft(service, executor="playwright", workflow=plan)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    service.fetch = AsyncMock(
        return_value=SourceFetchResult(
            status="failed", scope_complete=False, error_code="extraction_unverified"
        )
    )

    async def complete_trial(prepared, *, background):
        assert background is True
        service.sources.update_source(source["source_id"], enabled=False)
        prepared.update(status="ready", trial_success=True)
        return service.store.save_draft(prepared, draft_id=prepared["draft_id"])

    service.author_browser = AsyncMock(side_effect=complete_trial)
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], object(), service=service, llm_service=object()
    )
    await producer.produce_if_due(force=True)
    service.author_browser.assert_awaited_once()
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 1


async def test_background_repair_keeps_original_user_limits_when_intent_omitted_them(service):
    plan = {
        "version": 1,
        "entry_url": "https://example.com/feed",
        "allowed_origins": ["https://example.com"],
        "budgets": {"max_items": 2, "max_pages": 1},
        "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
    }
    source = service.activate(
        draft(
            service,
            executor="ego",
            workflow=plan,
            scope_limits={"max_items": 2, "max_pages": 1},
        )["draft_id"]
    )
    workflow = service.store.list_workflows(source["source_id"])[0]
    service.fetch = AsyncMock(
        return_value=SourceFetchResult(
            status="failed", scope_complete=False, error_code="extraction_unverified"
        )
    )

    async def complete_trial(prepared, *, background):
        assert background is True
        assert prepared["scope_limits"] == {"max_items": 2, "max_pages": 1}
        prepared.update(status="ready", trial_success=True)
        return service.store.save_draft(prepared, draft_id=prepared["draft_id"])

    service.author_browser = AsyncMock(side_effect=complete_trial)
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], object(), service=service, llm_service=object()
    )
    await producer.produce_if_due(force=True)
    service.author_browser.assert_awaited_once()
    repaired = service.store.get_workflow(workflow["workflow_id"])
    assert repaired["version"] == 2
    assert repaired["scope_limits"] == {"max_items": 2, "max_pages": 1}


def test_login_reuses_live_session_and_close_releases_without_publication(service):
    source = service.activate(draft(service, executor="ego")["draft_id"])
    other = service.activate(
        draft(service, executor="ego", name="Other", separate_source=True)["draft_id"]
    )
    session = SimpleNamespace(close=AsyncMock(), snapshot=AsyncMock(return_value={"elements": []}))
    service.observe_session = AsyncMock(return_value=(session, {"elements": []}))
    service.trial = AsyncMock()
    before = service.store.list_workflows(source["source_id"])
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        login_path = f"/api/custom-sources/{source['source_id']}/login"
        pending = client.post(login_path).json()
        path = f"/api/custom-sources/drafts/{pending['draft_id']}"
        assert client.post(f"{path}/takeover").status_code == 200
        assert client.post(login_path).json()["draft_id"] == pending["draft_id"]
        assert client.post(f"{path}/takeover").status_code == 200
        service.observe_session.assert_awaited_once()
        assert (
            client.post(f"/api/custom-sources/{other['source_id']}/login").json()["draft_id"]
            != pending["draft_id"]
        )
        assert client.post(f"{path}/close").json() == {"ok": True}
        assert client.post(f"{path}/close").status_code == 200
        session.close.assert_awaited_once()
        assert client.get(f"{path}/screenshot").status_code == 409
        assert client.post(login_path).json()["draft_id"] != pending["draft_id"]
        assert client.post("/api/custom-sources/drafts/unknown/close").status_code == 200
    service.trial.assert_not_awaited()
    assert service.store.list_workflows(source["source_id"]) == before


def test_login_screenshot_error_is_recoverable_without_reopening_session(service):
    from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

    pending = draft(service, executor="ego")
    session = SimpleNamespace(
        close=AsyncMock(),
        screenshot=AsyncMock(
            side_effect=[
                BrowserWorkflowExecutionError(
                    "ego_window_unavailable", "Login window is not ready"
                ),
                b"png image",
            ]
        ),
    )
    service.observe_session = AsyncMock(return_value=(session, {"elements": []}))
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    path = f"/api/custom-sources/drafts/{pending['draft_id']}"
    with TestClient(app) as client:
        assert client.post(f"{path}/takeover").status_code == 200
        failed = client.get(f"{path}/screenshot")
        assert failed.status_code == 409
        assert failed.json()["detail"] == "Login window is not ready"
        assert failed.headers["cache-control"] == "no-store"
        recovered = client.get(f"{path}/screenshot")
        assert recovered.status_code == 200 and recovered.content == b"png image"
        service.observe_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_author_timeout_has_actionable_detail_and_preserves_draft(service):
    pending = draft(service, executor="playwright", status="draft", workflow=None)
    service.observe_session = AsyncMock(side_effect=TimeoutError())
    result = await service.author_browser(pending)
    assert result["status"] == "failed" and not result["trial_success"]
    assert "超时" in result["detail"]
    assert result["draft_id"] == pending["draft_id"]
    assert service.store.get_draft(pending["draft_id"])["detail"] == result["detail"]


@pytest.mark.asyncio
async def test_source_status_probe_does_not_block_other_api_requests(service, monkeypatch):
    import asyncio
    import threading

    import httpx

    from openbiliclaw.api import custom_sources

    entered, release = threading.Event(), threading.Event()

    def executor(name):
        def availability():
            if name == "playwright":
                entered.set()
                release.wait(timeout=2)
            return {"available": False, "installed": False}

        return SimpleNamespace(availability=availability)

    service._executor_factory = executor
    monkeypatch.setattr(
        custom_sources, "_ego_setup_manager", lambda manager: SimpleNamespace(status=lambda: {})
    )
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))

    @app.get("/responsive")
    async def responsive():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        status = asyncio.create_task(client.get("/api/custom-sources"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            response = await client.get("/responsive")
            assert response.json() == {"ok": True}
            assert not status.done(), "the slow status probe must not stall the API event loop"
        finally:
            release.set()
            result = await status
        assert result.status_code == 200 and result.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("executor, expected", [("ego", 120), ("playwright", 45)])
async def test_author_receives_executor_specific_runtime_default(service, executor, expected):
    pending = draft(service, executor=executor, status="draft", workflow=None)
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(return_value=(session, {"elements": []}))
    service._json_task = AsyncMock(return_value={"needs_details": "No content rows observed"})
    result = await service.author_browser(pending, background=True)
    payload = service._json_task.await_args.args[1]
    assert payload["default_timeout_seconds"] == expected
    assert payload["intent"] == pending["intent"]
    assert result["status"] == "needs_details"
    session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("separate, explicit", [(True, False), (False, False), (True, True)])
async def test_independent_source_does_not_inherit_another_sources_task_space(
    service, separate, explicit
):
    existing = service.activate(draft(service)["draft_id"], target_share=1)
    service.author_browser = AsyncMock(side_effect=lambda value: value)
    prepared = await service.prepare(
        url=existing["entry_url"],
        intent="Observe this same public website",
        executor="ego",
        separate_source=separate,
        session_id=existing["session_id"] if explicit else "",
    )
    if separate and not explicit:
        assert prepared["session_id"] != existing["session_id"]
        assert not prepared["source_id"]
    else:
        assert prepared["session_id"] == existing["session_id"]
    assert service.sources.get_source(existing["source_id"])["session_id"] == existing["session_id"]
    assert len(service.sources.list_sources()) == 1


@pytest.mark.asyncio
async def test_provider_failure_preserves_failed_authoring_draft(service):
    from openbiliclaw.llm.service import LLMProviderExecutionError

    pending = draft(service, executor="ego", status="draft", workflow=None)
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(return_value=(session, {"elements": []}))
    service.llm_service = SimpleNamespace(
        complete_structured_task=AsyncMock(
            side_effect=LLMProviderExecutionError("Provider HTTP 502")
        )
    )
    result = await service.author_browser(pending, background=True)
    assert result["status"] == "failed" and not result["trial_success"]
    assert "模型" in result["detail"] and "重试" in result["detail"]
    assert service.store.get_draft(pending["draft_id"])["status"] == "failed"
    session.close.assert_awaited_once()
