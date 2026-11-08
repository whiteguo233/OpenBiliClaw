"""No-network safety and comparison-contract tests for the opt-in eval runner."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.llm.base import LLMResponse, LLMResponseError


@pytest.fixture
def runner(monkeypatch):
    # The script's CLI uses a sibling import; expose that same script directory.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("scripts.run_director_effect_eval")


def _args(tmp_path, **changes):
    values = {
        "real": True,
        "repeats": 1,
        "budget_seconds": 20.0,
        "output": tmp_path / "report.json",
        "config_root": tmp_path / "real-config",
        "instance": "",
        "legacy_ref": "synthetic-ref",
        "case": "practical_agent",
        "conditions": "screen_policy",
        "horizon": 1,
    }
    return argparse.Namespace(**(values | changes))


async def test_recording_adapter_preserves_current_policy_and_payload(runner):
    calls = []

    class FakeService:
        async def complete_structured_task(self, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content='{"synthetic":"result"}',
                model="fixed-model",
                provider="fixed-provider",
                instance_id="fixed-instance",
                usage={"input_tokens": 10, "output_tokens": 5},
                raw={"do_not_record": "RAW_TRANSPORT_SECRET"},
            )

    payload = {
        "profile_context": {"stable": {"interests": ["AI 工程"]}},
        "session_context": {"explicit_intent": "看点实践", "screen_policy": "nested-kept"},
        "screen_policy": {"task": "follow_intent"},
        "target_count": 6,
        "untrusted_candidates": [{"ref": "c01", "title": "合成标题"}],
    }
    kwargs = {
        "system_instruction": "FIXED NEW SYSTEM",
        "user_input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "temperature": 0.3,
        "max_tokens": 4096,
        "caller": "recommendation.director.plan",
        "inject_core_memory": False,
    }
    before = copy.deepcopy(kwargs)
    recorder = runner.RecordingLLM(FakeService())
    response = await recorder.complete_structured_task(**kwargs)
    assert kwargs == before
    assert len(calls) == 1
    forwarded = calls[0]
    assert json.loads(forwarded["user_input"]) == payload
    assert forwarded["system_instruction"] == "FIXED NEW SYSTEM"
    for key in ("temperature", "max_tokens", "caller", "inject_core_memory"):
        assert forwarded[key] == before[key]
    record = recorder.calls[0]
    assert record["input_sha256"] == hashlib.sha256(forwarded["user_input"].encode()).hexdigest()
    assert (
        record["prompt_sha256"]
        == hashlib.sha256(forwarded["system_instruction"].encode()).hexdigest()
    )
    assert record["raw_response"] == response.content
    assert record["model"] == "fixed-model"
    assert "RAW_TRANSPORT_SECRET" not in json.dumps(record)


async def test_error_recording_keeps_only_class_and_http_status(runner):
    class SyntheticHTTPError(Exception):
        status_code = 429

    class FailingService:
        async def complete_structured_task(self, **kwargs):
            try:
                raise SyntheticHTTPError("endpoint?api_key=HTTP_SECRET; response=BODY_SECRET")
            except SyntheticHTTPError as cause:
                raise RuntimeError("Authorization: Bearer OUTER_SECRET") from cause

    recorder = runner.RecordingLLM(FailingService())
    with pytest.raises(RuntimeError):
        await recorder.complete_structured_task(
            system_instruction="fixed", user_input="{}", max_tokens=4096
        )
    record = recorder.calls[0]
    assert record["error_class"] == "RuntimeError"
    assert record["error_chain"] == [
        {"class": "RuntimeError"},
        {"class": "SyntheticHTTPError", "http_status": 429},
    ]
    assert "SECRET" not in json.dumps(record)
    assert "raw_response" not in record
    assert record["elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    ("message", "safe_metadata"),
    [
        (
            "adapter returned reasoning but no final content (finish_reason=length); SECRET",
            {"response_problem": "reasoning_without_final_content", "finish_reason": "length"},
        ),
        (
            "adapter returned empty content; token=SECRET",
            {"response_problem": "empty_content"},
        ),
        (
            "adapter returned reasoning but no final content (finish_reason=SECRET)",
            {"response_problem": "reasoning_without_final_content"},
        ),
        ("unrecognized failure text with SECRET", {}),
    ],
)
async def test_response_error_metadata_is_whitelisted_without_preserving_body(
    runner, message, safe_metadata
):
    class FailingService:
        async def complete_structured_task(self, **kwargs):
            raise LLMResponseError(message)

    recorder = runner.RecordingLLM(FailingService())
    with pytest.raises(LLMResponseError):
        await recorder.complete_structured_task(
            system_instruction="fixed", user_input="{}", max_tokens=4096
        )
    assert recorder.calls[0]["error_chain"] == [{"class": "LLMResponseError", **safe_metadata}]
    assert "SECRET" not in json.dumps(recorder.calls)


@pytest.mark.parametrize("guard", ["real", "existing_output"])
async def test_external_calls_and_existing_output_are_guarded_before_config_load(
    runner, monkeypatch, tmp_path, guard
):
    args = _args(tmp_path, real=guard != "real")
    if guard == "existing_output":
        args.output.write_text("EXISTING_ARTIFACT", encoding="utf-8")

    def forbidden_load():
        pytest.fail("config must not be read before the opt-in/output guard")

    monkeypatch.setattr(runner, "load_config", forbidden_load)
    with pytest.raises(ValueError, match="--real is required|output exists"):
        await runner.run(args)
    if guard == "existing_output":
        assert args.output.read_text(encoding="utf-8") == "EXISTING_ARTIFACT"
    else:
        assert not args.output.exists()


@pytest.mark.parametrize("failure", ["exception", "invalid_json", "missing_completion"])
async def test_failed_calls_are_not_scored_as_success_and_real_config_stays_unchanged(
    runner, monkeypatch, tmp_path, failure
):
    args = _args(tmp_path)
    original = SimpleNamespace(
        llm=SimpleNamespace(
            default_chain=["primary", "backup"],
            default_provider="primary",
            fallback_provider="backup",
            instances={
                name: SimpleNamespace(
                    enabled=True,
                    model="fixed-model",
                    reasoning_effort="",
                    api_key="CONFIG_SECRET",
                )
                for name in ("primary", "backup")
            },
        ),
        director=SimpleNamespace(planning_budget_seconds=20.0),
    )
    before = copy.deepcopy(original)
    memory_paths = []
    service_kwargs = []
    registry_configs = []

    class FakeMemory:
        def __init__(self, path):
            memory_paths.append(path)

    class FakeService:
        def __init__(self, **kwargs):
            service_kwargs.append(kwargs)

        async def complete_structured_task(self, **kwargs):
            if failure == "exception":
                raise RuntimeError("CONFIG_SECRET must not leak")
            if failure == "missing_completion":
                return LLMResponse(
                    content=json.dumps(
                        {
                            "screens": [
                                {
                                    "direction": "Historical shape without completion",
                                    "pairs": [
                                        {"card_refs": ["c01", "c02"]},
                                        {"card_refs": ["c03", "c04"]},
                                        {"card_refs": ["c05", "c06"]},
                                    ],
                                }
                            ]
                        }
                    ),
                    model="fixed-model",
                )
            return LLMResponse(content="not JSON", model="fixed-model")

    def fake_registry(config):
        registry_configs.append(config)
        return object()

    monkeypatch.setattr(runner, "load_config", lambda: original)
    monkeypatch.setattr(runner, "module_overrides_from_config", lambda config: {})
    monkeypatch.setattr(runner, "build_llm_registry", fake_registry)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *args, **kwargs: "synthetic-sha")
    monkeypatch.setattr(runner, "MemoryManager", FakeMemory)
    monkeypatch.setattr(runner, "LLMService", FakeService)
    # run() mutates the process environment; restore it after this in-process test.
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    report = await runner.run(args)
    assert original == before
    pinned = registry_configs[0].llm
    assert list(pinned.instances) == ["primary"]
    assert pinned.default_chain == ["primary"]
    assert pinned.fallback_provider == ""
    assert len(memory_paths) == 1 and not memory_paths[0].is_relative_to(args.config_root)
    assert not memory_paths[0].exists()
    assert len(service_kwargs) == 1
    assert service_kwargs[0]["module_overrides"]["recommendation"].chain == ("primary",)
    results = report["results"]
    assert results[0]["condition"] == "deterministic_pair_baseline"
    assert results[0]["network_calls"] == 0
    if failure == "exception":
        assert len(results) == 2
        assert report["stopped_early"] == {
            "reason": "planner_unavailable",
            "case_id": "practical_agent",
            "condition": "screen_policy",
            "remaining_evaluations_not_run": True,
        }
    else:
        assert len(results) == 2
        assert "stopped_early" not in report
    for result in results[1:]:
        assert result["plan_valid"] is False
        assert result["reason"] in {"planner_unavailable", "invalid_planner_output"}
        assert len(result["calls"]) == (1 if failure == "exception" else 2)
        assert "first_screen_metrics" not in result
        assert "screens" not in result
    assert "CONFIG_SECRET" not in args.output.read_text(encoding="utf-8")
    assert not args.config_root.exists()


@pytest.mark.parametrize("effort", ["low", "medium"])
async def test_codex_cli_runs_real_planner_without_business_config_or_http_registry(
    runner, monkeypatch, tmp_path, effort
):
    transport = importlib.import_module("director_codex_transport")
    binary = str(tmp_path / "explicit-bundled-codex")
    args = _args(
        tmp_path,
        transport="codex-cli",
        codex_binary=binary,
        codex_reasoning_effort=effort,
        config_root=None,
    )
    services = []
    cli_invocations = []
    planner_calls = []
    fixed_output = json.dumps(
        {
            "completion": {"reason": "target_reached"},
            "screens": [
                {
                    "direction": "合成测试屏幕，不代表模型效果",
                    "pairs": [
                        {"card_refs": ["c0", "c1"], "role": "anchor", "intent": "合成主线"},
                        {"card_refs": ["c2", "c3"], "role": "complement", "intent": "合成补充"},
                        {"card_refs": ["c4", "c5"], "role": "exploration", "intent": "合成探索"},
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )

    class FakeCLIService:
        def __init__(self, *, binary, reasoning_effort="low"):
            self.binary = binary
            self.model = "gpt-6-luna"
            self.reasoning_effort = reasoning_effort
            self.records = []
            services.append(self)

        async def complete_structured_task(self, **kwargs):
            planner_calls.append(kwargs)
            self.records.append({"completed": True, "tools_allowed": False, "event_count": 4})
            return LLMResponse(
                content=fixed_output,
                model=self.model,
                provider="codex_cli",
                instance_id="temporary-codex-cli",
                usage={"input_tokens": 100, "output_tokens": 30},
            )

    def forbidden_config_or_http(*args, **kwargs):
        pytest.fail("CLI evaluation must not load business config, memory, or HTTP providers")

    def fake_check_output(command, **kwargs):
        cli_invocations.append(command)
        if command == [binary, "--version"]:
            return "codex-cli fixture-version\n"
        assert command == ["git", "rev-parse", "HEAD"]
        return "synthetic-code-sha\n"

    for name in ("load_config", "build_llm_registry", "MemoryManager", "LLMService"):
        monkeypatch.setattr(runner, name, forbidden_config_or_http)
    monkeypatch.setattr(transport, "CodexCLIService", FakeCLIService)
    monkeypatch.setattr(runner.subprocess, "check_output", fake_check_output)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", "UNCHANGED_EXISTING_ROOT")
    report = await runner.run(args)
    assert os.environ["OPENBILICLAW_PROJECT_ROOT"] == "UNCHANGED_EXISTING_ROOT"
    assert len(services) == 1 and services[0].binary == binary
    assert cli_invocations == [[binary, "--version"], ["git", "rev-parse", "HEAD"]]
    assert report["transport"] == "codex-cli"
    assert report["route"] == {
        "instance": "temporary-codex-cli",
        "model": "gpt-6-luna",
        "reasoning_effort": effort,
        "fallback_enabled": False,
        "model_verification": "explicit_cli_flag_not_server_echo",
        "cli_version": "codex-cli fixture-version",
        "cli_binary": binary,
    }
    assert report["production_planning_budget_seconds"] is None
    assert report["max_tokens_and_temperature_enforced"] is False
    assert report["cli_latency_includes_startup"] is True
    assert report["online_user_satisfaction_test"] is False
    assert report["jev_tested"] is False
    assert report["evaluation_design"] == "current_policy_single_arm"
    assert "legally approved short tail" in report["selection_metric_note"]
    assert report["policy_version"] == runner.DIRECTOR_POLICY_VERSION == "director-screen-policy.v3"
    assert "not a concurrent A/B" in report["comparison"]
    assert "frozen v1 reports separately" in report["comparison"]
    assert "legacy_ref" not in report
    assert "stopped_early" not in report
    baseline, new = report["results"]
    assert baseline["network_calls"] == 0
    assert new["condition"] == "screen_policy"
    assert len(planner_calls) == 1
    assert "screen_policy" in json.loads(planner_calls[0]["user_input"])
    for result in (new,):
        assert result["plan_valid"] is True
        assert result["first_screen_metrics"]["valid_selection"] is True
        assert result["first_screen_metrics"]["selected_count"] == 6
        assert len(result["screens"]) == 1
        assert len(result["screens"][0]["pairs"]) == 3
        assert result["cli_transport"] == {
            "completed": True,
            "tools_allowed": False,
            "event_count": 4,
        }
        assert result["cli_transport_attempts"] == [result["cli_transport"]]
        assert result["calls"][0]["raw_response"] == fixed_output
        assert result["calls"][0]["provider"] == "codex_cli"
        assert result["planning_completion"]["reason"] == "target_reached"
        assert result["planning_completion"]["initial_screen_counts"] == [6]
        assert result["planning_completion"]["semantic_quality"] == "not_certified"
    assert json.loads(args.output.read_text(encoding="utf-8")) == json.loads(json.dumps(report))


async def test_codex_cli_rejects_business_instance_selector_before_external_calls(
    runner, monkeypatch, tmp_path
):
    args = _args(tmp_path, transport="codex-cli", instance="configured-primary")

    def forbidden_call(*args, **kwargs):
        pytest.fail("invalid CLI configuration must not load config or invoke a command")

    monkeypatch.setattr(runner, "load_config", forbidden_call)
    monkeypatch.setattr(runner.subprocess, "check_output", forbidden_call)
    with pytest.raises(ValueError, match="--instance is only used with configured transport"):
        await runner.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("transport,effort", [("configured", "medium"), ("codex-cli", "invalid")])
async def test_cli_effort_guard_runs_before_external_calls(
    runner, monkeypatch, tmp_path, transport, effort
):
    def forbidden_call(*args, **kwargs):
        pytest.fail("invalid effort must fail before configuration or external calls")

    monkeypatch.setattr(runner, "load_config", forbidden_call)
    monkeypatch.setattr(runner.subprocess, "check_output", forbidden_call)
    args = _args(tmp_path, transport=transport, codex_reasoning_effort=effort)
    with pytest.raises(ValueError, match="reasoning effort|reasoning-effort"):
        await runner.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("transport", ["configured", "codex-cli"])
@pytest.mark.parametrize("conditions", ["legacy_prompt", "legacy_prompt,screen_policy"])
async def test_legacy_condition_rejected_before_configuration_or_external_calls(
    runner, monkeypatch, tmp_path, transport, conditions
):
    args = _args(tmp_path, transport=transport, conditions=conditions)

    def forbidden_call(*args, **kwargs):
        pytest.fail("incompatible legacy condition must be rejected before external setup")

    monkeypatch.setattr(runner, "load_config", forbidden_call)
    monkeypatch.setattr(runner, "build_llm_registry", forbidden_call)
    monkeypatch.setattr(runner.subprocess, "check_output", forbidden_call)
    cli_transport = importlib.import_module("director_codex_transport")
    monkeypatch.setattr(cli_transport, "CodexCLIService", forbidden_call)
    with pytest.raises(ValueError, match="frozen prior report or a historical checkout"):
        await runner.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("conditions", ["", "unknown", "screen_policy,screen_policy"])
async def test_only_one_current_policy_arm_is_accepted_before_config_load(
    runner, monkeypatch, tmp_path, conditions
):
    def forbidden_load():
        pytest.fail("condition validation must happen before config load")

    monkeypatch.setattr(runner, "load_config", forbidden_load)
    with pytest.raises(ValueError, match="single-arm evaluation only accepts"):
        await runner.run(_args(tmp_path, conditions=conditions))
