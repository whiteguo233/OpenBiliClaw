"""Model-bound context budgets, exact wire measurements, and strict persistence."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.config import (
    Config,
    ConfigError,
    JevEvaluationConfig,
    _build_config,
    _render_config_toml,
    load_config,
    save_config,
    validate_director_routes,
)
from openbiliclaw.decision.context_budget import (
    JevContextError,
    JevContextLimits,
    parse_jev_context_limits,
    resolve_jev_context,
)
from openbiliclaw.decision.models import ChoiceQuestion, DecisionRequest, NoulQuestion


def test_published_budget_is_conservative_bytes_not_a_tokenizer() -> None:
    budget = resolve_jev_context("jev-1.13.0")
    assert budget.max_request_tokens == 64_000
    assert budget.max_state_question_tokens == 32_000
    assert budget.max_request_bytes == 48_000
    assert budget.max_state_question_bytes == 24_000
    assert budget.source == "published_model_registry"
    with pytest.raises(FrozenInstanceError):
        budget.model = "another"  # type: ignore[misc]


def test_exact_model_override_does_not_leak_on_model_switch() -> None:
    overrides = {"custom-fixed": JevContextLimits(128_000, 96_000)}
    budget = resolve_jev_context("custom-fixed", overrides)
    assert (budget.max_request_bytes, budget.max_state_question_bytes) == (96_000, 72_000)
    assert budget.source == "operator_confirmed"
    assert resolve_jev_context("jev-1.13.0", overrides) == resolve_jev_context("jev-1.13.0")
    with pytest.raises(JevContextError, match="unknown"):
        resolve_jev_context("other-fixed", overrides)
    assert (
        budget.fingerprint
        != resolve_jev_context(
            "custom-fixed", {"custom-fixed": JevContextLimits(256_000, 128_000)}
        ).fingerprint
    )


@pytest.mark.parametrize(
    "limits",
    [
        {},
        {"max_request_tokens": 64_000},
        {"max_request_tokens": 64_000, "max_state_question_tokens": 32_000, "api_key": "secret"},
        {"max_request_tokens": True, "max_state_question_tokens": 32_000},
        {"max_request_tokens": float("nan"), "max_state_question_tokens": 32_000},
        {"max_request_tokens": float("inf"), "max_state_question_tokens": 32_000},
        {"max_request_tokens": "64000", "max_state_question_tokens": 32_000},
        {"max_request_tokens": 255, "max_state_question_tokens": 256},
        {"max_request_tokens": 100_000_001, "max_state_question_tokens": 32_000},
        {"max_request_tokens": 32_000, "max_state_question_tokens": 64_000},
        [],
    ],
)
def test_context_limits_reject_bad_values_without_echo(limits: object) -> None:
    with pytest.raises(JevContextError) as error:
        parse_jev_context_limits(limits)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("model", ["", " ", " fixed", "fixed ", "fixed\ninside", True])
def test_model_identifiers_are_not_normalized_or_guessed(model: object) -> None:
    with pytest.raises(JevContextError):
        resolve_jev_context(model)  # type: ignore[arg-type]


def test_measurement_exactly_matches_native_json_envelope() -> None:
    budget = resolve_jev_context("jev-1.13.0")
    request = DecisionRequest(
        request_id="local-only",
        purpose="EVALUATE",
        question_schema_version="v1",
        state={"画像": ["中文😀", 'quote"slash\\\n']},
        questions={
            "短": NoulQuestion(instructions="真？"),
            "long": ChoiceQuestion(instructions="choose", criteria={"a": "中" * 50}),
        },
    )

    def actual_wire(questions: dict[str, object]) -> int:
        return len(
            json.dumps(
                {"model": budget.model, "state": request.state, "questions": questions},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )

    questions = {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in request.questions.items()
    }
    size = budget.measure(request.state, request.questions)
    assert size.request_bytes == actual_wire(questions)
    assert size.max_state_question_bytes == max(actual_wire({k: v}) for k, v in questions.items())
    assert budget.with_byte_guards(
        max_request_bytes=size.request_bytes,
        max_state_question_bytes=size.max_state_question_bytes,
    ).fits_request(request)
    assert not budget.with_byte_guards(
        max_request_bytes=size.request_bytes - 1,
        max_state_question_bytes=size.max_state_question_bytes,
    ).fits_request(request)
    assert not budget.with_byte_guards(
        max_request_bytes=size.request_bytes,
        max_state_question_bytes=size.max_state_question_bytes - 1,
    ).fits_request(request)


def test_optional_manual_guards_only_tighten_model_budget() -> None:
    budget = resolve_jev_context("jev-1.13.0")
    assert budget.with_byte_guards() == budget
    assert budget.with_byte_guards(max_request_bytes=1000).max_state_question_bytes == 1000
    assert budget.with_byte_guards(max_request_bytes=1000).fingerprint != budget.fingerprint
    with pytest.raises(JevContextError):
        budget.with_byte_guards(max_request_bytes=48_001)
    with pytest.raises(JevContextError):
        budget.with_byte_guards(max_state_question_bytes=24_001)
    with pytest.raises(JevContextError):
        budget.with_byte_guards(max_request_bytes=1000, max_state_question_bytes=2000)


def test_config_defaults_auto_and_retains_explicit_legacy_guards(tmp_path: Path) -> None:
    assert JevEvaluationConfig().max_request_bytes == 0
    assert JevEvaluationConfig().max_state_question_bytes == 0
    config = Config()
    config.decision.providers.jev.model = "operator-fixed"
    config.decision.providers.jev.context_limits = {
        "operator-fixed": JevContextLimits(128_000, 64_000),
        "another-fixed": JevContextLimits(256_000, 128_000),
    }
    config.discovery.jev_evaluation.max_request_bytes = 48_000
    config.discovery.jev_evaluation.max_state_question_bytes = 24_000
    path = save_config(config, tmp_path / "settings.toml")
    loaded = load_config(path, consult_environment=False)
    assert loaded.decision.providers.jev == config.decision.providers.jev
    assert loaded.discovery.jev_evaluation == config.discovery.jev_evaluation
    assert '[decision.providers.jev.context_limits."operator-fixed"]' in path.read_text()


@pytest.mark.parametrize("route", ["eval", "director", "filter"])
def test_unknown_context_is_allowed_only_until_a_route_is_enabled(route: str) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    config.decision.providers.jev.model = "operator-fixed"
    validate_director_routes(config)
    if route == "eval":
        config.discovery.eval_scorer = "jev"
    elif route == "director":
        config.director.mode = "shadow"
    else:
        config.discovery.semantic_filter.mode = "shadow"
    with pytest.raises(ConfigError, match="context_limits"):
        validate_director_routes(config)
    config.decision.providers.jev.context_limits["operator-fixed"] = JevContextLimits(
        128_000, 64_000
    )
    validate_director_routes(config)


def test_larger_model_can_use_more_than_old_hardcoded_limits() -> None:
    config = Config()
    config.decision.providers.jev.model = "larger-fixed"
    config.decision.providers.jev.context_limits = {
        "larger-fixed": JevContextLimits(128_000, 64_000)
    }
    config.discovery.jev_evaluation.max_request_bytes = 80_000
    config.discovery.jev_evaluation.max_state_question_bytes = 40_000
    validate_director_routes(config)
    config.decision.providers.jev.model = "jev-1.13.0"
    with pytest.raises(ConfigError, match="max_request_bytes"):
        validate_director_routes(config)


def test_nested_context_config_unknown_fields_rejected_before_persistence() -> None:
    raw = {
        "decision": {
            "providers": {
                "jev": {
                    "context_limits": {
                        "operator-fixed": {
                            "max_request_tokens": 64_000,
                            "max_state_question_tokens": 32_000,
                            "api_key": "never-echo",
                        },
                    }
                }
            }
        }
    }
    with pytest.raises(ConfigError, match="context_limits") as error:
        _build_config(raw, consult_environment=False)
    assert "never-echo" not in str(error.value)
    config = Config()
    config.decision.providers.jev.context_limits = {" bad ": JevContextLimits(64_000, 32_000)}
    with pytest.raises(ConfigError, match="context_limits"):
        _render_config_toml(config)
