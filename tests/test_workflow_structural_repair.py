"""A failed draft gets one scoped candidate trial and a strict review of actual samples."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "repair.db")
    db.initialize()
    service = WorkflowService(
        db,
        llm_service=SimpleNamespace(
            complete_structured_task=AsyncMock(
                return_value=SimpleNamespace(content='{"scope_preserved":true}')
            )
        ),
    )
    yield service
    db.close()


def pending(service, *, selector=None):
    link = 'td.content > a.topic[href^="https://forum.example/read.php"]'
    item = selector or "#topics > tbody > tr.topic:has(" + link + ':has([href^="/read.php"]))'
    return service.store.save_draft(
        {
            "entry_url": "https://forum.example/topics?board=7",
            "origin": "https://forum.example",
            "name": "Forum topics",
            "intent": "First page, only read.php?tid= posts; no navigation. Up to 3 items.",
            "scope_limits": {"max_items": 3, "max_pages": 1},
            "source_id": "",
            "session_id": "session-a",
            "executor": "ego",
            "status": "failed",
            "trial_success": False,
            "query_mode": "none",
            "query": "",
            "workflow": {
                "version": 1,
                "entry_url": "https://forum.example/topics?board=7",
                "allowed_origins": ["https://forum.example"],
                "budgets": {"max_items": 3, "max_pages": 1, "timeout_seconds": 45},
                "steps": [
                    {
                        "action": "collect",
                        "item_selector": item,
                        "link_selector": link,
                        "title_selector": "td.content > a.topic",
                        "next_selector": "",
                    }
                ],
            },
        }
    )


def failed():
    return BrowserWorkflowResult(
        "failed",
        error_code="extraction_unverified",
        observation={
            "collection_diagnostics": {"error": "invalid_css_selector"},
            "elements": [
                {
                    "tag": "a",
                    "selector": "a.topic",
                    "href_attribute_prefix": "https://forum.example/read.php",
                    "text": "Real topic",
                }
            ],
        },
    )


def candidate_observation(urls=None):
    urls = urls or ["https://forum.example/read.php?tid=1"]
    link = 'td.content > a.topic[href^="https://forum.example/read.php"]'
    return {
        "title": "Requested board",
        "entry_url_comparison": {
            "origin_matches": True,
            "path_matches": True,
            "requested_query_matches": True,
        },
        "elements": [{"selector": "#topics", "text": "Real topic"}],
        "collection_evidence": {
            "item_selector": "#topics > tbody > tr.topic:has(" + link + ")",
            "link_selector": link,
            "samples": [{"url": url, "row": {"tag": "tr"}, "link": {"tag": "a"}} for url in urls],
        },
    }


@pytest.mark.asyncio
async def test_trial_recovers_invalid_nested_has_only_after_actual_sample_scope_review(service):
    draft = pending(service)
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                failed(),
                BrowserWorkflowResult(
                    "ok",
                    (BrowserWorkflowItem("Real topic", "https://forum.example/read.php?tid=1"),),
                    observation=candidate_observation(),
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor

    result = await service.trial(draft["draft_id"])

    assert result["status"] == "ready" and result["trial_success"] is True
    assert executor.run.await_count == 2
    original = executor.run.call_args_list[0].args[0].to_dict()
    corrected = executor.run.call_args_list[1].args[0].to_dict()
    expected = dict(
        original,
        steps=[
            dict(
                original["steps"][0],
                item_selector='#topics > tbody > tr.topic:has(td.content > a.topic[href^="https://forum.example/read.php"])',
            )
        ],
    )
    assert corrected == expected == result["workflow"]
    audit = service.llm_service.complete_structured_task.call_args.kwargs
    data = json.loads(audit["user_input"])
    assert data["intent"] == draft["intent"] and data["previous_plan"] == original
    assert data["candidate_plan"] == corrected
    assert data["sample_items"][0]["url"] == "https://forum.example/read.php?tid=1"
    assert data["observation"] == failed().observation
    assert data["candidate_observation"] == candidate_observation()
    assert executor.run.call_args_list[1].kwargs["capture_observation"] is True
    assert audit["inject_core_memory"] is False
    assert service.sources.list_sources() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [False, "true", 1, None, "exception"])
async def test_scope_rejection_or_unavailable_review_preserves_failed_draft(service, response):
    draft = pending(service)
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                failed(),
                BrowserWorkflowResult(
                    "ok",
                    (BrowserWorkflowItem("Navigation", "https://forum.example/read.php?tid=1"),),
                    observation=candidate_observation(),
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor
    model = service.llm_service.complete_structured_task
    if response == "exception":
        model.side_effect = TimeoutError("Model review unavailable")
    else:
        model.return_value = SimpleNamespace(content=json.dumps({"scope_preserved": response}))
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["trial_success"] is False
    assert result["workflow"] == draft["workflow"]
    assert service.store.get_draft(draft["draft_id"])["workflow"] == draft["workflow"]
    assert executor.run.await_count == 2
    model.assert_awaited_once()
    assert service.sources.list_sources() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["failed", "degraded", "empty", "foreign", "exception", "no_observation"]
)
async def test_unverified_candidate_never_reaches_scope_review_or_saves_plan(service, outcome):
    draft = pending(service)
    next_result = {
        "failed": failed(),
        "degraded": BrowserWorkflowResult("degraded", error_code="extraction_unverified"),
        "empty": BrowserWorkflowResult("empty"),
        "foreign": BrowserWorkflowResult(
            "ok",
            (
                BrowserWorkflowItem("Valid", "https://forum.example/read.php?tid=1"),
                BrowserWorkflowItem("Foreign", "https://other.example/read.php?tid=2"),
            ),
            observation=candidate_observation(),
        ),
        "no_observation": BrowserWorkflowResult(
            "ok", (BrowserWorkflowItem("Valid", "https://forum.example/read.php?tid=1"),)
        ),
        "exception": RuntimeError("Native host disconnected"),
    }[outcome]
    executor = SimpleNamespace(run=AsyncMock(side_effect=[failed(), next_result]))
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    assert executor.run.await_count == 2
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "wildcard",
        "row_attribute",
        "row_negation",
        "suffix",
        "list",
        "extra_class",
        "extra_relation",
        "compound_vs_descendant",
        "group_in_compound",
        "query",
        "operator",
        "fragment",
        "empty_query",
        "other_attribute",
        "foreign",
        "already_valid",
    ],
)
async def test_ambiguous_or_additional_constraints_are_not_discarded(service, variant):
    link = 'td.content > a.topic[href^="https://forum.example/read.php"]'
    selector = "#topics > tbody > tr.topic:has(" + link + ':has([href^="/read.php"]))'
    if variant == "wildcard":
        selector = selector.replace("tr.topic", "*")
    if variant == "row_attribute":
        selector = selector.replace("tr.topic", 'tr.topic[data-type="safe"]')
    if variant == "row_negation":
        selector = selector.replace("tr.topic", "tr.topic:not(.pinned)")
    if variant == "suffix":
        selector += ":not(.pinned)"
    if variant == "list":
        selector += ",tr.other:has(a)"
    if variant == "extra_class":
        selector = selector.replace("a.topic", "a.topic.safe")
    if variant == "extra_relation":
        selector = selector.replace(link, link + " a.topic")
    if variant == "compound_vs_descendant":
        selector = selector.replace("a.topic", "a .topic")
    if variant == "group_in_compound":
        selector = selector.replace("a.topic", "a(.topic)")
    if variant == "query":
        selector = selector.replace('"/read.php"', '"/read.php?tid=7"')
    if variant == "fragment":
        selector = selector.replace('"/read.php"', '"/read.php#safe"')
    if variant == "empty_query":
        selector = selector.replace('"/read.php"', '"/read.php?"')
    if variant == "operator":
        selector = selector.replace('href^="/read.php"', 'href="/read.php"')
    if variant == "other_attribute":
        selector = selector.replace("a.topic", 'a.topic[data-type="safe"]')
    if variant == "foreign":
        selector = selector.replace('"/read.php"', '"https://other.example/read.php"')
    if variant == "already_valid":
        selector = "#topics > tbody > tr.topic:has(" + link + ")"
    draft = pending(service, selector=selector)
    executor = SimpleNamespace(run=AsyncMock(return_value=failed()))
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    executor.run.assert_awaited_once()
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code", ["ego_dom_error", "timeout", "login_required", "ego_disconnected"]
)
async def test_runtime_failures_do_not_trigger_selector_repair(service, error_code):
    draft = pending(service)
    failure = failed()
    failure = BrowserWorkflowResult(
        "failed", error_code=error_code, observation=failure.observation
    )
    executor = SimpleNamespace(run=AsyncMock(return_value=failure))
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    executor.run.assert_awaited_once()
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_collection_never_rewrites_or_asks_model_to_repair(service):
    draft = pending(service)
    executor = SimpleNamespace(run=AsyncMock(return_value=failed()))
    service._executor_factory = lambda name: executor
    result = await service.fetch(draft)
    assert result.status == "failed"
    executor.run.assert_awaited_once()
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_checks_every_collected_item_not_just_five_preview_cards(service):
    draft = pending(service)
    draft["workflow"]["budgets"]["max_items"] = 6
    service.store.save_draft(draft, draft_id=draft["draft_id"])
    items = tuple(
        BrowserWorkflowItem(f"Topic {i}", f"https://forum.example/read.php?tid={i}")
        for i in range(6)
    )
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                failed(),
                BrowserWorkflowResult(
                    "ok", items, observation=candidate_observation([i.url for i in items])
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "ready" and len(result["sample_items"]) == 5
    data = json.loads(service.llm_service.complete_structured_task.call_args.kwargs["user_input"])
    assert len(data["sample_items"]) == 6


@pytest.mark.asyncio
async def test_structural_repair_cannot_add_third_attempt_after_href_retrial_fails(service):
    draft = pending(service, selector='tr.topic:has(a[href^="/read.php"])')
    first_failure = BrowserWorkflowResult(
        "failed",
        error_code="extraction_unverified",
        observation={**failed().observation, "href_selector_repairs": ["link_selector"]},
    )
    executor = SimpleNamespace(run=AsyncMock(side_effect=[first_failure, failed()]))
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    assert executor.run.await_count == 2
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_page_structural_recovery_does_not_claim_single_page_provenance(service):
    draft = pending(service)
    draft["workflow"]["budgets"]["max_pages"] = 2
    service.store.save_draft(draft, draft_id=draft["draft_id"])
    executor = SimpleNamespace(run=AsyncMock(return_value=failed()))
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    executor.run.assert_awaited_once()
    service.llm_service.complete_structured_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["missing", "duplicate", "other_url", "other_selector", "empty_row", "non_anchor"]
)
async def test_scope_review_requires_one_exact_row_binding_for_every_real_item(service, variant):
    draft = pending(service)
    observation = candidate_observation()
    evidence = observation["collection_evidence"]
    if variant == "missing":
        evidence["samples"] = []
    if variant == "duplicate":
        evidence["samples"] *= 2
    if variant == "other_url":
        evidence["samples"][0]["url"] = "https://other.example/read.php?tid=1"
    if variant == "other_selector":
        evidence["item_selector"] = "article"
    if variant == "empty_row":
        evidence["samples"][0]["row"] = {}
    if variant == "non_anchor":
        evidence["samples"][0]["link"]["tag"] = "button"
    executor = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                failed(),
                BrowserWorkflowResult(
                    "ok",
                    (BrowserWorkflowItem("Real topic", "https://forum.example/read.php?tid=1"),),
                    observation=observation,
                ),
            ]
        )
    )
    service._executor_factory = lambda name: executor
    result = await service.trial(draft["draft_id"])
    assert result["status"] == "failed" and result["workflow"] == draft["workflow"]
    assert executor.run.await_count == 2
    service.llm_service.complete_structured_task.assert_not_awaited()
