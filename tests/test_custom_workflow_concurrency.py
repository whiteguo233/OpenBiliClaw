"""Interactive source authoring must remain usable while inventory is empty."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.concurrency import LLMConcurrencyGate, LLMTrafficClass
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.sources.workflow_service import WorkflowService
from tests.test_custom_feed_workflow import FEED_URL, _feed, _service
from tests.test_custom_feed_workflow import database as database
from tests.test_custom_source_contract import _activate


class _GatedLLM:
    def __init__(self, content):
        self.gate = LLMConcurrencyGate(1)
        self.gate.update_inventory(available=0, target=20)
        self.callers = []
        self.content = content

    async def complete_structured_task(self, *, caller, **_kwargs):
        self.callers.append(caller)
        async with self.gate.slot(caller=caller):
            return LLMResponse(content=self.content)


async def test_empty_pool_does_not_park_interactive_feed_prepare(database, monkeypatch):
    body, media_type = _feed("rss")
    llm = _GatedLLM('{"candidate_index": 0}')
    service = _service(
        database,
        monkeypatch,
        lambda _request: httpx.Response(200, content=body, headers={"Content-Type": media_type}),
        llm_service=llm,
    )

    async with asyncio.timeout(1):
        draft = await service.prepare(url=FEED_URL, intent="Follow this subscription")

    assert draft["status"] == "ready"
    assert llm.callers == ["sources.web.workflow.interactive"]
    assert llm.gate.status_payload()["llm_maintenance_waiting"] == 0


def _browser_service(database):
    llm = _GatedLLM('{"needs_details": "Which article list?"}')
    service = WorkflowService(database, llm_service=llm)
    session = SimpleNamespace(close=AsyncMock())
    service.observe_session = AsyncMock(
        return_value=(session, {"elements": [{"tag": "a", "text": "Article"}]})
    )
    return service, llm


async def test_empty_pool_does_not_park_interactive_browser_trial(database):
    service, llm = _browser_service(database)
    draft = service.store.save_draft(
        {
            "entry_url": "https://example.com/articles",
            "origin": "https://example.com",
            "intent": "Follow articles",
            "executor": "playwright",
            "workflow": None,
        }
    )
    async with asyncio.timeout(1):
        result = await service.trial(draft["draft_id"])

    assert result["status"] == "needs_details"
    assert llm.gate.classify(llm.callers[0]) is LLMTrafficClass.INTERACTIVE


async def test_automatic_repair_keeps_background_admission(database):
    service, llm = _browser_service(database)
    source = _activate(service)
    workflow = service.store.list_workflows(source["source_id"])[0]
    producer = CustomWorkflowProducer(
        database, source["source_id"], object(), service=service, llm_service=llm
    )
    task = asyncio.create_task(producer._repair(service, workflow))
    try:
        for _ in range(100):
            if llm.gate.status_payload()["llm_maintenance_waiting"] == 1:
                break
            await asyncio.sleep(0.001)
        assert llm.callers == ["sources.web.workflow"]
        assert llm.gate.status_payload()["llm_maintenance_waiting"] == 1
        assert not task.done()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert llm.gate.status_payload()["llm_maintenance_waiting"] == 0


@pytest.mark.parametrize("background", [False, True])
async def test_author_to_trial_preserves_admission_origin(database, background):
    service, _llm = _browser_service(database)
    source = _activate(service)
    workflow = service.store.list_workflows(source["source_id"])[0]
    draft = service.store.save_draft(dict(workflow, status="draft"))
    service._json_task = AsyncMock(return_value={"plan": workflow["workflow"]})
    service.trial = AsyncMock(return_value={"status": "ready"})

    await service.author_browser(draft, background=background)

    assert service._json_task.await_args.kwargs["background"] is background
    service.trial.assert_awaited_once_with(draft["draft_id"], background=background)
