"""All project LLM fan-in paths use the same bounded SDK operations."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.config import Config
from openbiliclaw.llm.base import LLMRegistry, LLMResponse
from openbiliclaw.llm.service import LLMService
from openbiliclaw.llm.sponsored_errors import (
    SponsoredContractMismatchError,
    SponsoredQuotaExceededError,
)
from openbiliclaw.llm.sponsored_operations import (
    EMBEDDING_MODEL,
    OPERATIONS,
    validate_operation_input,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_router import complete_project_task
from openbiliclaw.llm.sponsored_tasks import DEFAULT_SPONSORED_REGISTRY
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
    reset_sponsored_settings,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/sponsored-sdk-operations.json"


@pytest.fixture(autouse=True)
def settings() -> Any:
    reset_sponsored_settings()
    yield
    reset_sponsored_settings()


class NoUserProvider:
    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unexpected user-provider request")


class RecordingSponsored(SponsoredProvider):
    def __init__(self) -> None:
        super().__init__(["unused"])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_task(
        self, *, task_id: str = "", user_payload: Any, **_kwargs: Any
    ) -> LLMResponse:
        contract = DEFAULT_SPONSORED_REGISTRY.get(task_id)
        assert contract is not None
        self._serialize_payload(user_payload, contract)
        self.calls.append((task_id, user_payload))
        return LLMResponse(
            content='{"reply":"project reply"}', provider="sponsored", model=contract.model
        )


_BUILDERS = runpy.run_path(str(ROOT / "tests/test_llm_prompts.py"))["_builder_test_inputs"]()


@pytest.mark.parametrize("name,args1,args2", _BUILDERS)
async def test_every_existing_prompt_builder_crosses_the_sdk_without_changing_user_data(
    name: str, args1: Any, args2: Any
) -> None:
    from openbiliclaw.llm import prompts
    from openbiliclaw.llm.prompt_contracts import ensure_json_mode_contract

    provider = RecordingSponsored()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    for args in (args1, args2):
        messages = getattr(prompts, name)(**args)
        await service.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[-1]["content"],
            inject_core_memory=False,
            caller=f"coverage.{name}",
        )
        operation, payload = provider.calls[-1]
        assert operation == "llm.structured"
        assert payload["messages"] == [
            {
                "role": "system",
                "content": ensure_json_mode_contract(messages[0]["content"]).strip(),
            },
            messages[-1],
        ]


async def test_sdk_operations_with_actual_runtime_child() -> None:
    command = os.environ.get("OBC_TEST_RUST_RUNTIME")
    provider = SponsoredProvider(
        shlex.split(command)
        if command
        else [sys.executable, "-m", "openbiliclaw.llm.sponsored_mock_runtime"]
    )
    try:
        for case in json.loads(FIXTURE.read_text())["cases"]:
            response = await provider.execute_task(
                task_id=case["task_id"], user_payload=case["input"]
            )
            assert response.provider == "sponsored"
        text = await provider.embedding("用户兴趣")
        image = await provider.embedding("data:image/png;base64,aW1hZ2U=", image=True)
        assert len(text) == len(image) == 1024
        assert text == image
    finally:
        await provider.aclose()


@pytest.mark.parametrize("operation", OPERATIONS)
def test_all_operations_reject_hidden_http_fields(operation: str) -> None:
    case = next(
        c for c in json.loads(FIXTURE.read_text())["cases"] if c["task_id"] == f"llm.{operation}"
    )
    contract = DEFAULT_SPONSORED_REGISTRY.get(case["task_id"])
    assert contract is not None
    for key in ("model", "endpoint", "headers", "api_key", "url"):
        with pytest.raises(SponsoredContractMismatchError):
            SponsoredProvider._serialize_payload({**case["input"], key: "override"}, contract)


def test_multimodal_input_rejects_remote_urls_and_extra_roles() -> None:
    data = next(
        c["input"] for c in json.loads(FIXTURE.read_text())["cases"] if c["task_id"] == "llm.vision"
    )
    data["messages"][-1]["content"][1]["image_url"]["url"] = "https://attacker.invalid/image"
    with pytest.raises(ValueError):
        validate_operation_input(data, "vision")


async def test_raw_registry_and_bare_provider_evaluation_are_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import sponsored_router

    provider = RecordingSponsored()
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: provider)
    messages = [{"role": "user", "content": "evaluate this project case"}]
    assert (await LLMRegistry().complete(messages)).provider == "sponsored"
    assert (
        await complete_project_task(NoUserProvider(), messages, json_mode=True)
    ).provider == "sponsored"
    assert [c[0] for c in provider.calls] == ["llm.text", "llm.structured"]


async def test_tools_and_vision_keep_existing_application_processing() -> None:
    provider = RecordingSponsored()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    tools = [{"name": "search", "description": "查找推荐"}]
    await service.complete_with_tools(
        system_instruction="项目对话",
        user_input="找些内容",
        tools=tools,
        caller="soul.dialogue.tools",
    )
    assert provider.calls[-1][0] == "llm.tools"
    assert "<available_tools>" in provider.calls[-1][1]["messages"][0]["content"]
    await service.complete_multimodal_structured_task(
        system_instruction="评估封面 json",
        user_input="候选内容",
        image_inputs=[{"content_id": "c1", "data_url": "data:image/png;base64,aW1hZ2U="}],
        caller="discovery.evaluate_batch",
    )
    assert provider.calls[-1][0] == "llm.vision"
    parts = provider.calls[-1][1]["messages"][-1]["content"]
    assert parts[-1]["image_url"]["url"].startswith("data:image/png;")


async def test_sponsor_only_registry_and_shared_child_with_embedding(tmp_path: Path) -> None:
    from openbiliclaw.llm.registry import build_embedding_service, build_llm_registry
    from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider

    cfg = Config()
    cfg.storage.data_dir = str(tmp_path)
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=True,
        runtime_path=shlex.join([sys.executable, "-m", "openbiliclaw.llm.sponsored_mock_runtime"]),
    )
    registry = build_llm_registry(cfg)
    assert "sponsored" in registry.available_providers
    try:
        await registry.complete([{"role": "user", "content": "project readiness"}])
        child = shared_sponsored_provider()
        assert child is not None
        process = child._process
        embedding = build_embedding_service(cfg, registry)
        assert embedding is not None
        assert await embedding.embed("兴趣")
        assert shared_sponsored_provider() is child and child._process is process
        assert EMBEDDING_MODEL in str(embedding.cache_model_namespace)  # type: ignore[attr-defined]
    finally:
        await close_shared_provider()


async def test_disabled_fallback_does_not_spend_user_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.llm import sponsored_router

    class Unavailable:
        async def complete(self, *_args: Any, **_kwargs: Any) -> Any:
            raise SponsoredQuotaExceededError("exhausted")

    install_sponsored_settings(
        SponsoredRuntimeSettings(enabled=True, fallback_to_user_provider=False)
    )
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: Unavailable())
    with pytest.raises(SponsoredQuotaExceededError):
        await complete_project_task(NoUserProvider(), [{"role": "user", "content": "test"}])


async def test_evaluation_read_tools_use_sdk_and_reject_outside_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.eval.sponsored_agents import _read_tool, sponsored_agent_text
    from openbiliclaw.llm import sponsored_router

    path = tmp_path / "src/openbiliclaw/llm/prompts.py"
    path.parent.mkdir(parents=True)
    path.write_text("project source sentinel")
    responses = iter(
        [
            '{"tool_call":{"name":"Read","arguments":{"file_path":"src/openbiliclaw/llm/prompts.py"}}}',
            '{"changes":[],"summary":"done"}',
        ]
    )
    calls = []

    class AgentProvider:
        async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
            calls.append(json.loads(json.dumps(messages)))
            return LLMResponse(content=next(responses), provider="sponsored")

    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: AgentProvider())
    result = await sponsored_agent_text(
        "review project",
        SimpleNamespace(
            system_prompt="optimizer", allowed_tools=["Read"], cwd=str(tmp_path), max_turns=2
        ),
    )
    assert result is not None and json.loads(result[1])["summary"] == "done"
    assert "project source sentinel" in calls[-1][-1]["content"]
    with pytest.raises(ValueError):
        _read_tool("Read", {"file_path": "../../config.toml"}, tmp_path)


def test_project_llm_inventory_has_no_unreviewed_new_entry_points() -> None:
    module = runpy.run_path(str(ROOT / "scripts/audit_sponsored_coverage.py"))
    actual = module["inventory"](ROOT)
    expected = json.loads((ROOT / "docs/testing/sponsored-task-inventory.json").read_text())
    assert actual == expected, "review and regenerate the project LLM coverage inventory"
    assert any(row["family"] == "vision" for row in actual)
    assert any(row["family"] == "embedding_image" for row in actual)
    assert any("Claude fallback" in row["route"] for row in actual)


async def test_shared_service_close_waits_for_an_active_request() -> None:
    import asyncio

    provider = SponsoredProvider(["unused"])
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def request() -> None:
        async with provider._request_lock:
            entered.set()
            await finish.wait()

    task = asyncio.create_task(request())
    await entered.wait()
    closing = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    await asyncio.gather(task, closing)


async def test_sponsored_is_the_ready_default_while_user_chain_remains_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import sponsored_router
    from openbiliclaw.llm.base import LLMProvider
    from openbiliclaw.llm.registry import build_llm_registry

    class UserProvider(LLMProvider):
        calls = 0

        @property
        def name(self) -> str:
            return "openai"

        async def complete(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
            self.calls += 1
            return LLMResponse(content="user fallback", provider="openai")

    class Unavailable:
        async def complete(self, *_args: Any, **_kwargs: Any) -> Any:
            raise SponsoredQuotaExceededError("exhausted")

    user = UserProvider()
    cfg = Config()
    cfg.llm.sponsored = SponsoredRuntimeSettings(enabled=True, runtime_path="unused")
    registry = build_llm_registry(cfg, provider_overrides={"openai": user})
    assert registry.default_provider == "sponsored"
    assert registry._fallback_order() == ["sponsored", "openai"]
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: Unavailable())
    response = await registry.complete([{"role": "user", "content": "project task"}])
    assert response.content == "user fallback" and user.calls == 1


async def test_multimodal_entry_accepts_a_text_only_batch_without_forcing_a_vision_model() -> None:
    provider = RecordingSponsored()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    await service.complete_multimodal_structured_task(
        system_instruction="evaluate json",
        user_input="text-only candidates",
        image_inputs=[],
    )
    operation, payload = provider.calls[-1]
    assert operation == "llm.structured"
    assert payload["messages"][-1]["content"] == [{"type": "text", "text": "text-only candidates"}]


async def test_explicit_task_helper_routes_other_project_callers_through_the_sdk() -> None:
    provider = RecordingSponsored()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    response = await service.execute_sponsored_task(
        caller="soul.preference",
        payload={"events": []},
        fallback_system_instruction="项目偏好分析 json",
        fallback_user_input='{"events":[]}',
    )
    assert response.provider == "sponsored"
    assert provider.calls[-1][0] == "llm.structured"
    assert provider.calls[-1][1]["caller"] == "soul.preference"


async def test_current_main_native_tool_interface_keeps_schema_and_multihop_results() -> None:
    class NativeSponsor(RecordingSponsored):
        async def execute_task(self, **kwargs: Any) -> LLMResponse:
            response = await super().execute_task(**kwargs)
            response.content = '{"tool_call":{"name":"lookup","arguments":{"query":"topic"}}}'
            return response

    provider = NativeSponsor()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    schema = {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "lookup sources",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
    messages = [
        {"role": "system", "content": "project dialogue"},
        {"role": "user", "content": "find something"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "old",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"query":"prior"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old", "content": "prior tool evidence"},
    ]
    response = await service.complete_with_native_tools(
        messages=messages, tools=[schema], caller="soul.dialogue.agent"
    )
    assert response.tool_calls and response.tool_calls[0]["name"] == "lookup"
    assert response.tool_calls[0]["arguments"] == {"query": "topic"}
    assert response.content == ""
    operation, payload = provider.calls[-1]
    assert operation == "llm.tools"
    assert "prior tool evidence" in str(payload["messages"])
    assert "parameters" in payload["messages"][0]["content"]


async def test_current_main_stream_entry_points_work_with_sponsored_client() -> None:
    provider = RecordingSponsored()
    service = LLMService(registry=NoUserProvider(), memory=None, sponsored_provider=provider)  # type: ignore[arg-type]
    chunks = [
        c
        async for c in service.stream_complete_with_core_memory(
            system_instruction="project", user_input="chat", caller="soul.dialogue"
        )
    ]
    assert "".join(c.delta for c in chunks) == '{"reply":"project reply"}'
    assert chunks[-1].response and chunks[-1].response.provider == "sponsored"
    chunks = [
        c
        async for c in service.stream_complete_with_native_tools(
            messages=[
                {"role": "system", "content": "project"},
                {"role": "user", "content": "chat"},
            ],
            tools=[],
            caller="soul.dialogue.agent",
        )
    ]
    assert chunks[-1].response and chunks[-1].response.provider == "sponsored"
    assert provider.calls[-1][0] == "llm.tools"


async def test_current_main_raw_registry_native_and_stream_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import sponsored_router

    provider = RecordingSponsored()
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    monkeypatch.setattr(sponsored_router, "shared_sponsored_provider", lambda: provider)
    registry = LLMRegistry()
    messages = [{"role": "user", "content": "project task"}]
    assert (await registry.complete_with_tools(messages, [])).provider == "sponsored"
    chunks = [c async for c in registry.stream_complete(messages)]
    assert chunks[-1].response and chunks[-1].response.provider == "sponsored"
    chunks = [c async for c in registry.stream_complete_with_tools(messages, [])]
    assert chunks[-1].response and chunks[-1].response.provider == "sponsored"


@pytest.mark.parametrize(
    "emits_first,allow_fallback", [(False, True), (True, True), (False, False)]
)
async def test_sponsored_stream_fallback_cannot_repeat_partial_output_or_spend_unapproved_quota(
    emits_first: bool, allow_fallback: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.llm import sponsored_router
    from openbiliclaw.llm.base import LLMStreamChunk

    class BrokenStream:
        async def stream_complete(self, *_args: Any, **_kwargs: Any) -> Any:
            if emits_first:
                yield LLMStreamChunk(delta="partial sponsor output")
            raise SponsoredQuotaExceededError("exhausted")

    fallback_calls = []
    alerts = []

    async def user_stream() -> Any:
        assert sponsored_router.sponsored_bypassed()
        fallback_calls.append(True)
        yield LLMStreamChunk(response=LLMResponse(content="fallback", provider="user"))

    install_sponsored_settings(
        SponsoredRuntimeSettings(enabled=True, fallback_to_user_provider=allow_fallback)
    )
    monkeypatch.setattr(sponsored_router, "_notify_fallback", lambda *a, **kw: alerts.append(kw))
    stream = sponsored_router.stream_project_completion(
        [{"role": "user", "content": "project task"}],
        provider=BrokenStream(),
        fallback=user_stream,
        caller="soul.dialogue",
    )
    if emits_first or not allow_fallback:
        with pytest.raises(SponsoredQuotaExceededError):
            async for _chunk in stream:
                assert not sponsored_router.sponsored_bypassed()
        assert fallback_calls == [] and alerts == []
    else:
        chunks = [chunk async for chunk in stream]
        assert chunks[-1].response and chunks[-1].response.provider == "user"
        assert fallback_calls == [True] and alerts == [{"caller": "soul.dialogue"}]
    assert not sponsored_router.sponsored_bypassed()


def test_explicit_probe_factories_keep_targets_and_active_sponsored_settings() -> None:
    from openbiliclaw.llm.registry import build_embedding_service, build_llm_registry
    from openbiliclaw.llm.sponsored_router import bypass_sponsored
    from openbiliclaw.sponsored_runtime_state import current_sponsored_settings

    active = SponsoredRuntimeSettings(enabled=True, runtime_path="active-runtime")
    install_sponsored_settings(active)
    cfg = Config()
    cfg.llm.sponsored = SponsoredRuntimeSettings(enabled=True, runtime_path="draft-runtime")
    cfg.llm.embedding.provider = ""

    class ProbeProvider(NoUserProvider):
        name = "openai"

    with bypass_sponsored():
        registry = build_llm_registry(cfg, provider_overrides={"openai": ProbeProvider()})  # type: ignore[dict-item]
        assert registry.default_provider == "openai"
        assert "sponsored" not in registry.available_providers
        assert build_embedding_service(cfg, registry) is None
    assert current_sponsored_settings() == active
