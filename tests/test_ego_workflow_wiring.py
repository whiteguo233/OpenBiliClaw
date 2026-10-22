"""Executor selection and identity through the public custom-source lifecycle."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.sources.browser_workflow import BrowserWorkflowItem, BrowserWorkflowResult
from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.sources.web_adapter import WebSourceAdapter
from openbiliclaw.sources.web_recipe import build_web_recipe
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database

URL = "https://books.example.com/"
PLAN = {
    "version": 1,
    "entry_url": URL,
    "allowed_origins": ["https://books.example.com"],
    "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
}


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "ego.db")
    database.initialize()
    names = []
    session = SimpleNamespace(
        snapshot=AsyncMock(return_value={"elements": [{"tag": "article", "text": "A book"}]}),
        close=AsyncMock(),
    )
    executor = SimpleNamespace(
        open_session=AsyncMock(return_value=session),
        run=AsyncMock(
            return_value=BrowserWorkflowResult(
                "ok",
                (BrowserWorkflowItem("A book", URL + "books/one"),),
            )
        ),
    )

    def factory(name):
        names.append(name)
        return executor

    probe = AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"candidates": []}))
    llm = SimpleNamespace(
        complete_structured_task=AsyncMock(
            return_value=LLMResponse(
                content=json.dumps({"plan": PLAN, "query_mode": "none", "query": ""})
            )
        )
    )
    service = WorkflowService(database, executor_factory=factory, probe=probe, llm_service=llm)
    yield service, executor, names, probe
    database.close()


async def test_auto_website_keeps_ego_identity_through_trial_activation_and_producer(setup):
    service, executor, names, _ = setup
    draft = await service.prepare(url=URL, intent="Discover new books")
    assert draft["executor"] == "ego"
    assert draft["status"] == "ready"
    assert service.sources.list_sources() == []
    source = service.activate(draft["draft_id"], interval_minutes=5)
    workflow = service.store.list_workflows(source["source_id"])[0]
    assert workflow["executor"] == "ego"
    pipeline = DiscoveryCandidatePipeline(
        database=service.database, discovery_engine=SimpleNamespace()
    )
    producer = CustomWorkflowProducer(
        service.database,
        source["source_id"],
        pipeline,
        service=service,
        candidate_evaluation_owned_by_coordinator=True,
    )
    result = await producer.produce_if_due()
    assert result["discovered"] == result["enqueued"] == 1
    assert service.store.runs(source["source_id"])[0]["executor"] == "ego"
    assert names and set(names) == {"ego"}
    assert executor.run.await_count == 2


@pytest.mark.parametrize("strategy", ["listing", "sitemap"])
async def test_auto_html_uses_ego_even_when_http_probe_finds_a_recipe(setup, monkeypatch, strategy):
    service, _, names, probe = setup
    recipe = build_web_recipe(
        {
            "name": "Book index",
            "strategy": strategy,
            "config": {
                "entry_url": URL,
                "listing": {"item_selector": "article", "link_selector": "a"},
            },
        }
    )
    probe.return_value = SimpleNamespace(to_dict=lambda: {"candidates": [{"recipe": recipe}]})

    async def model(**request):
        value = (
            {"candidate_index": 0}
            if request["system_instruction"].startswith("Choose")
            else {
                "plan": PLAN,
                "query_mode": "none",
                "query": "",
            }
        )
        return LLMResponse(content=json.dumps(value))

    service.llm_service.complete_structured_task.side_effect = model
    fetch = AsyncMock(return_value=SourceFetchResult(status="empty"))
    monkeypatch.setattr(WebSourceAdapter, "fetch_result", fetch)
    draft = await service.prepare(url=URL, intent="Discover new books")
    assert draft["executor"] == "ego" and draft["status"] == "ready"
    assert set(names) == {"ego"}
    fetch.assert_not_awaited()


@pytest.mark.parametrize("previous_executor", ["ego", "playwright", "http"])
async def test_auto_update_keeps_the_original_executor(setup, monkeypatch, previous_executor):
    from tests.test_custom_workflow_lifecycle import draft as saved_draft

    service, _, names, probe = setup
    ready = saved_draft(
        service,
        entry_url=URL,
        origin="https://books.example.com",
        executor=previous_executor,
        workflow=PLAN
        if previous_executor != "http"
        else build_web_recipe(
            {"name": "Book feed", "strategy": "feed", "config": {"entry_url": URL}}
        ),
    )
    source = service.activate(ready["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    if previous_executor == "http":
        probe.return_value = SimpleNamespace(
            to_dict=lambda: {"candidates": [{"recipe": ready["workflow"]}]}
        )
        monkeypatch.setattr(
            WebSourceAdapter,
            "fetch_result",
            AsyncMock(return_value=SourceFetchResult(status="empty")),
        )
    update = await service.prepare(
        url=URL,
        intent="Follow new book descriptions",
        source_id=source["source_id"],
        workflow_id=workflow["workflow_id"],
    )
    assert update["status"] == "ready" and update["executor"] == previous_executor
    assert update["base_version"] == 1
    assert service.store.get_workflow(workflow["workflow_id"])["executor"] == previous_executor
    if previous_executor != "http":
        probe.assert_not_awaited()
        assert set(names) == {previous_executor}
    service.activate(update["draft_id"])
    replaced = service.store.get_workflow(workflow["workflow_id"])
    assert replaced["executor"] == previous_executor and replaced["version"] == 2


@pytest.mark.parametrize("strategy", ["listing", "sitemap"])
@pytest.mark.parametrize("append_to_source", [False, True])
async def test_new_explicit_http_workflow_requires_a_feed(
    setup, monkeypatch, strategy, append_to_source
):
    from tests.test_custom_workflow_lifecycle import draft as saved_draft

    service, executor, names, probe = setup
    source_id = ""
    if append_to_source:
        ready = saved_draft(service, entry_url=URL, origin="https://books.example.com")
        source_id = service.activate(ready["draft_id"])["source_id"]
    recipe = build_web_recipe(
        {
            "name": "Book index",
            "strategy": strategy,
            "config": {
                "entry_url": URL,
                "listing": {"item_selector": "article", "link_selector": "a"},
            },
        }
    )
    probe.return_value = SimpleNamespace(to_dict=lambda: {"candidates": [{"recipe": recipe}]})
    fetch = AsyncMock(return_value=SourceFetchResult(status="empty"))
    monkeypatch.setattr(WebSourceAdapter, "fetch_result", fetch)
    draft = await service.prepare(
        url=URL, intent="Discover new books", executor="http", source_id=source_id
    )
    assert draft["status"] == "needs_details" and not draft["trial_success"]
    assert "Feed" in draft["detail"] and "Ego" in draft["detail"]
    assert draft["workflow"] is None
    fetch.assert_not_awaited()
    executor.open_session.assert_not_awaited()
    assert names == []
    with pytest.raises(ValueError, match="successful trial"):
        service.activate(draft["draft_id"])
    assert len(service.sources.list_sources()) == int(append_to_source)
    if source_id:
        assert len(service.store.list_workflows(source_id)) == 1


@pytest.mark.parametrize("strategy", ["listing", "sitemap"])
@pytest.mark.parametrize("selection", ["auto", "http"])
async def test_existing_http_html_workflow_can_still_be_updated(
    setup, monkeypatch, strategy, selection
):
    from tests.test_custom_workflow_lifecycle import draft as saved_draft

    service, executor, names, probe = setup
    recipe = build_web_recipe(
        {
            "name": "Legacy index",
            "strategy": strategy,
            "config": {
                "entry_url": URL,
                "listing": {"item_selector": "article", "link_selector": "a"},
            },
        }
    )
    ready = saved_draft(service, entry_url=URL, origin="https://books.example.com", workflow=recipe)
    source = service.activate(ready["draft_id"])
    original = service.store.list_workflows(source["source_id"])[0]
    probe.return_value = SimpleNamespace(to_dict=lambda: {"candidates": [{"recipe": recipe}]})
    fetch = AsyncMock(return_value=SourceFetchResult(status="empty"))
    monkeypatch.setattr(WebSourceAdapter, "fetch_result", fetch)
    update = await service.prepare(
        url=URL,
        intent="Keep following the legacy index",
        executor=selection,
        source_id=source["source_id"],
        workflow_id=original["workflow_id"],
    )
    assert update["status"] == "ready" and update["executor"] == "http"
    assert update["workflow"]["strategy"] == strategy
    assert update["base_version"] == 1
    service.activate(update["draft_id"])
    replacement = service.store.get_workflow(original["workflow_id"])
    assert replacement["executor"] == "http" and replacement["version"] == 2
    assert replacement["workflow"]["strategy"] == strategy
    fetch.assert_awaited_once()
    executor.open_session.assert_not_awaited()
    assert names == []


async def test_new_playwright_workflow_is_rejected_before_probe_or_browser(setup):
    service, executor, _, probe = setup
    with pytest.raises(ValueError, match="existing"):
        await service.prepare(url=URL, intent="Discover new books", executor="playwright")
    probe.assert_not_awaited()
    executor.open_session.assert_not_awaited()
    assert service.sources.list_sources() == []


@pytest.mark.parametrize("selection", ["ego", "auto"])
async def test_ego_unavailable_fails_without_http_or_playwright_fallback(setup, selection):
    from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

    service, executor, names, probe = setup
    executor.open_session.side_effect = BrowserWorkflowExecutionError(
        "executor_unavailable", "Ego Lite host is not connected"
    )
    draft = await service.prepare(url=URL, intent="Discover new books", executor=selection)
    assert draft["executor"] == "ego" and draft["status"] == "failed"
    assert not draft["trial_success"]
    assert "Ego Lite" in draft["detail"]
    assert names == ["ego"]
    executor.run.assert_not_awaited()
    if selection == "ego":
        probe.assert_not_awaited()
    with pytest.raises(ValueError, match="successful trial"):
        service.activate(draft["draft_id"])
    assert service.sources.list_sources() == []


async def test_login_takeover_and_resume_keep_ego_executor_and_session(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openbiliclaw.api.custom_sources import create_custom_sources_router

    service, executor, names, _ = setup
    draft = await service.prepare(url=URL, intent="Discover books", executor="ego")
    source = service.activate(draft["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    login = service.login_draft(source["source_id"])
    session = executor.open_session.return_value
    session.screenshot = AsyncMock(return_value=b"fixture-png")
    session.act = AsyncMock(return_value={"elements": [], "login_wall": False})
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        base = f"/api/custom-sources/drafts/{login['draft_id']}"
        assert client.get(base).json()["executor"] == "ego"
        assert client.post(base + "/takeover", json={}).status_code == 200
        assert executor.open_session.call_args.kwargs["interactive"] is True
        assert executor.open_session.call_args.kwargs["session_id"] == workflow["session_id"]
        assert client.get(base + "/screenshot").content == b"fixture-png"
        assert (
            client.post(
                base + "/action",
                json={
                    "action": "fill",
                    "selector": "#username",
                    "value": "manual-fixture-value",
                },
            ).status_code
            == 200
        )
        resumed = client.post(base + "/resume").json()
        assert resumed["executor"] == "ego" and resumed["status"] == "ready"
    replaced = service.store.get_workflow(workflow["workflow_id"])
    assert replaced["executor"] == "ego" and replaced["version"] == 2
    assert replaced["session_id"] == workflow["session_id"]
    assert "manual-fixture-value" not in json.dumps(service.store.get_draft(login["draft_id"]))
    assert set(names) == {"ego"}


def test_new_chat_and_cli_expose_ego_or_feed_without_playwright(setup, monkeypatch):
    from typer.testing import CliRunner

    from openbiliclaw import cli_custom_sources
    from openbiliclaw.agent.tools.custom_source_tools import build_custom_source_tools
    from openbiliclaw.config import Config

    service, _, _, _ = setup
    tools = build_custom_source_tools(
        SimpleNamespace(database=service.database, config=Config(), llm_service=None)
    )
    prepare = next(tool for tool in tools if tool.name == "prepare_discovery_source")
    assert prepare.parameters["properties"]["executor"]["enum"] == ["auto", "http", "ego"]
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return service

    monkeypatch.setattr(cli_custom_sources, "_service", factory)
    runner = CliRunner()
    rejected = runner.invoke(
        cli_custom_sources.sources_app,
        ["prepare", URL, "--intent", "Discover books", "--executor", "playwright"],
    )
    assert rejected.exit_code == 2
    assert calls == []
    accepted = runner.invoke(
        cli_custom_sources.sources_app,
        ["prepare", URL, "--intent", "Discover books", "--executor", "ego"],
    )
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.output)["executor"] == "ego"


def test_ego_host_state_is_scoped_beside_the_active_database(setup, tmp_path, monkeypatch):
    from openbiliclaw.sources import workflow_executors

    service, _, _, _ = setup
    roots = []
    sentinel = object()

    def constructor(root):
        roots.append(root)
        return sentinel

    monkeypatch.setattr(workflow_executors, "EgoLiteWorkflowExecutor", constructor)
    assert WorkflowService(service.database).executor("ego") is sentinel
    assert roots == [tmp_path / "browser-sessions"]


@pytest.mark.parametrize(
    "description", ["真实作品简介。", "真实作品简介。" * 100], ids=["short", "bounded"]
)
async def test_trial_exposes_bounded_real_description_to_api_and_chat(setup, description):
    from openbiliclaw.agent.tools.custom_source_tools import _bounded_summary
    from openbiliclaw.api.custom_sources import public_draft
    from openbiliclaw.config import Config

    service, executor, _, _ = setup
    executor.run.return_value = BrowserWorkflowResult(
        "ok", (BrowserWorkflowItem("A book", URL + "books/one", description=description),)
    )
    draft = await service.prepare(url=URL, intent="Discover book descriptions", executor="ego")
    assert draft["status"] == "ready"
    sample = public_draft(draft)["sample_items"][0]
    assert sample["description"] == description[:500]
    assert sample["author_name"] == ""
    summary = json.loads(_bounded_summary(public_draft(draft), Config()))
    assert summary["sample_items"][0]["description"] == description[:500]
    assert service.store.get_draft(draft["draft_id"])["sample_items"][0] == sample
    fetched = await service.fetch(draft)
    assert fetched.items[0].description == description
