"""Model requests must honor the configured budget through every transport."""

from types import SimpleNamespace

import pytest

from openbiliclaw.config import Config
from openbiliclaw.llm.registry import _build_instance_provider
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
@pytest.mark.parametrize("seconds", [600, 1200])
def test_free_instance_inherits_global_timeout_despite_legacy_180(model, seconds):
    config = Config()
    config.llm.timeout = seconds
    config.llm.sponsored = SponsoredRuntimeSettings(request_timeout_seconds=180)
    provider = _build_instance_provider(config, "siliconflow_free", SimpleNamespace(model=model))
    assert provider._settings.request_timeout_seconds == seconds
    assert config.llm.sponsored.request_timeout_seconds == 180


def test_init_budget_allows_one_timeout_and_both_recovery_halves():
    from openbiliclaw.cli import _profile_analysis_deadlines

    idle, absolute = _profile_analysis_deadlines(
        event_count=800,
        requested=None,
        concurrency=3,
        batch_count=30,
        request_timeout_seconds=1200,
    )
    assert idle > 3 * 1200
    assert absolute > 10 * 3 * 1200
    assert _profile_analysis_deadlines(
        event_count=800, requested=7, request_timeout_seconds=1200
    ) == (None, 7)


@pytest.mark.parametrize(
    "kind,constructor",
    [
        ("openai", "OpenAIProvider"),
        ("claude", "ClaudeProvider"),
        ("gemini", "GeminiProvider"),
        ("deepseek", "DeepSeekProvider"),
        ("ollama", "OllamaProvider"),
        ("openrouter", "OpenRouterProvider"),
        ("orcarouter", "OrcaRouterProvider"),
        ("requesty", "RequestyProvider"),
        ("api_route", "ApiRouteProvider"),
        ("cheaperinference", "CheaperInferenceProvider"),
        ("openai_compatible", "OpenAIProvider"),
    ],
)
@pytest.mark.parametrize("seconds", [600, 1200])
def test_other_instance_factories_forward_global_budget(monkeypatch, kind, constructor, seconds):
    from openbiliclaw.llm import registry

    config = Config()
    config.llm.timeout = seconds
    instance = getattr(config.llm, kind)
    instance.api_key = "test-only-placeholder"
    instance.model = "test-model"
    instance.base_url = "http://127.0.0.1:1/v1"
    captured = {}

    def construct(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(registry, constructor, construct)
    monkeypatch.setattr(registry, "gemini_sdk_available", lambda: True)
    assert _build_instance_provider(config, kind, instance) is not None
    assert captured["timeout"] == seconds


@pytest.mark.parametrize("negotiated", [False, True])
async def test_real_ipc_negotiates_budgets_for_normal_stream_and_embedding(monkeypatch, negotiated):
    import sys

    from openbiliclaw.llm import sponsored_provider as module
    from openbiliclaw.llm.sponsored_profiles import DEFAULT_CHAT_MODEL, approved_registry

    frames = []
    original = module.write_frame_async

    async def record(writer, frame):
        frames.append(frame)
        await original(writer, frame)

    monkeypatch.setattr(module, "write_frame_async", record)
    command = [
        sys.executable,
        "-m",
        "openbiliclaw.llm.sponsored_mock_runtime",
        "--approved",
        "--stream",
    ]
    if negotiated:
        command.append("--request-timeouts")
    async with module.SponsoredProvider(
        command, registry=approved_registry(), model=DEFAULT_CHAT_MODEL, request_timeout=600
    ) as sdk:
        messages = [{"role": "user", "content": "测试"}]
        await sdk.complete(messages)
        chunks = [chunk async for chunk in sdk.stream_complete(messages)]
        assert chunks[-1].response
        assert len(await sdk.embedding("测试")) == 1024
    requests = [frame for frame in frames if frame["type"] in {"execute", "execute_stream"}]
    assert len(requests) == 3
    if negotiated:
        assert [frame["timeout_ms"] for frame in requests] == [600000, 600000, 180000]
    else:
        assert all("timeout_ms" not in frame for frame in requests)
    assert not sdk.runtime_running
