"""Three-mode configuration and independent, calibrated filtering routes."""

from __future__ import annotations

import tomllib
from typing import Any

import pytest

from openbiliclaw.config import (
    Config,
    ConfigError,
    DirectorConfig,
    SemanticFilterConfig,
    _build_config,
    _collect_config_issues,
    _render_config_toml,
    decision_routes_enabled,
    validate_director_routes,
)


def test_old_config_has_disabled_independent_routes() -> None:
    config = _build_config({}, consult_environment=False)
    assert config.director == DirectorConfig()
    assert config.discovery.semantic_filter == SemanticFilterConfig()
    assert not decision_routes_enabled(config)


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_three_modes_roundtrip(strategy: str, mode: str) -> None:
    config = Config()
    config.director.strategy = strategy
    config.director.mode = mode
    config.decision.providers.jev.enabled = True
    rendered = _render_config_toml(config, consult_environment=False)
    result = _build_config(tomllib.loads(rendered), consult_environment=False)
    assert result.director == config.director
    assert result.discovery.semantic_filter == config.discovery.semantic_filter
    assert decision_routes_enabled(result) == (strategy != "agent_only" and mode != "off")


def test_agent_only_without_jev_can_enable_independent_filter_later() -> None:
    config = Config()
    config.director.strategy = "agent_only"
    config.director.mode = "enforce"
    validate_director_routes(config)
    config.discovery.semantic_filter.mode = "shadow"
    with pytest.raises(ConfigError, match="decision.providers.jev.enabled"):
        validate_director_routes(config)
    config.decision.providers.jev.enabled = True
    validate_director_routes(config)


def test_filter_enforce_requires_exact_pinned_model_calibration_and_exclusive_rejection() -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    config.discovery.semantic_filter.mode = "enforce"
    with pytest.raises(ConfigError, match="calibration_tag"):
        validate_director_routes(config)
    config.discovery.semantic_filter.calibration_tag = "local-domain-eval-2026-09"
    with pytest.raises(ConfigError, match="calibration_model"):
        validate_director_routes(config)
    config.discovery.semantic_filter.calibration_model = config.decision.providers.jev.model
    validate_director_routes(config)
    config.discovery.eval_prefilter_mode = "enforce"
    with pytest.raises(ConfigError, match="不能同时 enforce"):
        validate_director_routes(config)
    config.discovery.eval_prefilter_mode = "off"
    config.discovery.semantic_filter.calibration_model = "jev-latest"
    config.decision.providers.jev.model = "jev-latest"
    with pytest.raises(ConfigError, match="calibration_model"):
        validate_director_routes(config)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("director", "mode", "unknown"),
        ("director", "strategy", "rules"),
        ("director", "decision_provider", "openai"),
        ("director", "planning_horizon_screens", True),
        ("director", "planning_horizon_screens", 4),
        ("director", "planning_horizon_screens", 5),
        ("director", "planning_horizon_screens", 6),
        ("director", "plan_ttl_seconds", 0),
        ("director", "planning_budget_seconds", float("nan")),
        ("director", "decision_budget_seconds", -1),
        ("director", "max_candidates", 41),
        ("director", "max_decision_calls", 11),
        ("semantic_filter", "mode", "unknown"),
        ("semantic_filter", "provider", "openai"),
        ("semantic_filter", "refusal_threshold", float("nan")),
        ("semantic_filter", "refusal_threshold", True),
        ("semantic_filter", "refusal_threshold", 0.4),
        ("semantic_filter", "calibration_tag", " tag "),
        ("semantic_filter", "calibration_model", 1),
        ("semantic_filter", "max_candidates_per_cycle", 91),
        ("semantic_filter", "max_calls_per_cycle", 0),
        ("semantic_filter", "decision_budget_seconds", 31),
    ],
)
def test_invalid_route_fields_rejected_on_parse_render_and_diagnostics(
    section: str,
    field: str,
    value: Any,
) -> None:
    raw = {section: {field: value}}
    if section == "semantic_filter":
        raw = {"discovery": raw}
    with pytest.raises(ConfigError):
        _build_config(raw, consult_environment=False)
    config = Config()
    target = config.director if section == "director" else config.discovery.semantic_filter
    setattr(target, field, value)
    assert any(issue.field.endswith(f".{field}") for issue in _collect_config_issues(config))
    with pytest.raises(ConfigError):
        _render_config_toml(config, consult_environment=False)


@pytest.mark.parametrize(
    "raw",
    [
        {"director": []},
        {"director": {"api_key": "secret-do-not-echo"}},
        {"discovery": {"semantic_filter": []}},
        {"discovery": {"semantic_filter": {"unknown": "secret-do-not-echo"}}},
    ],
)
def test_bad_tables_do_not_echo_values(raw: dict[str, Any]) -> None:
    with pytest.raises(ConfigError) as error:
        _build_config(raw, consult_environment=False)
    assert "secret-do-not-echo" not in str(error.value)
