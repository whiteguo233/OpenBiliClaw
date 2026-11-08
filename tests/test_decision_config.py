"""Independent decision-provider configuration and safe CLI visibility."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openbiliclaw import config as config_module
from openbiliclaw.config import (
    Config,
    ConfigDiagnostics,
    ConfigError,
    DecisionConfig,
    DecisionProvidersConfig,
    JevDecisionConfig,
    _build_config,
    _collect_config_issues,
    _render_config_toml,
    load_config_with_diagnostics,
    save_config,
    validate_jev_decision_config,
)


def test_old_config_keeps_decision_disabled_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = _build_config({"llm": {"default_provider": "ollama"}}, consult_environment=False)

    assert isinstance(config.decision, DecisionConfig)
    assert isinstance(config.decision.providers, DecisionProvidersConfig)
    assert config.decision.providers.jev == JevDecisionConfig()
    assert config.decision.providers.jev.enabled is False
    assert not any(issue.field.startswith("decision.") for issue in _collect_config_issues(config))


def test_decision_config_parse_save_roundtrip_keeps_only_environment_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_JEV_KEY", "secret-value-must-not-appear")
    raw = {
        "enabled": True,
        "model": "jev-1.13.0",
        "api_key_env": "TEST_JEV_KEY",
        "timeout_seconds": 1.25,
        "max_attempts": 3,
    }
    config = _build_config({"decision": {"providers": {"jev": raw}}}, consult_environment=False)
    path = save_config(config, tmp_path / "config.toml", preserve_override_provenance=False)
    rendered = path.read_text(encoding="utf-8")
    reloaded, diagnostics = load_config_with_diagnostics(path, consult_environment=False)

    assert reloaded.decision == config.decision
    assert tomllib.loads(rendered)["decision"]["providers"]["jev"] == raw
    assert "secret-value-must-not-appear" not in rendered
    assert "api_key" not in tomllib.loads(rendered)["decision"]["providers"]["jev"]
    assert not any(issue.field.startswith("decision.") for issue in diagnostics.issues)


def test_enabling_provider_does_not_require_key_during_config_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    path = tmp_path / "config.toml"
    path.write_text("[decision.providers.jev]\nenabled = true\n", encoding="utf-8")

    config, diagnostics = load_config_with_diagnostics(path, consult_environment=False)

    assert config.decision.providers.jev.enabled is True
    assert not any(issue.field.startswith("decision.") for issue in diagnostics.issues)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("enabled", "false"),
        ("enabled", 0),
        ("model", ""),
        ("model", "  "),
        ("model", " jev-1.13.0 "),
        ("model", 13),
        ("api_key_env", ""),
        ("api_key_env", "sk-private-secret"),
        ("api_key_env", "1INVALID"),
        ("api_key_env", "KEY NAME"),
        ("api_key_env", 42),
        ("timeout_seconds", 0),
        ("timeout_seconds", -0.5),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", "2.0"),
        ("timeout_seconds", True),
        ("max_attempts", 0),
        ("max_attempts", -1),
        ("max_attempts", 1.5),
        ("max_attempts", "2"),
        ("max_attempts", True),
    ],
)
def test_invalid_decision_field_rejected_on_parse_and_save(
    tmp_path: Path, field_name: str, value: Any
) -> None:
    expected_field = f"decision.providers.jev.{field_name}"
    with pytest.raises(ConfigError, match=expected_field):
        _build_config({"decision": {"providers": {"jev": {field_name: value}}}})

    config = Config()
    setattr(config.decision.providers.jev, field_name, value)
    issues = _collect_config_issues(config)
    assert any(issue.field == expected_field and issue.severity == "blocking" for issue in issues)
    with pytest.raises(ConfigError, match=expected_field):
        validate_jev_decision_config(config.decision.providers.jev)
    with pytest.raises(ConfigError, match=expected_field):
        _render_config_toml(config)
    path = tmp_path / "must-not-exist.toml"
    with pytest.raises(ConfigError, match=expected_field):
        save_config(config, path)
    assert not path.exists()


@pytest.mark.parametrize(
    "raw",
    [
        {"decision": "jev"},
        {"decision": {"providers": []}},
        {"decision": {"providers": {"jev": False}}},
        {"decision": {"providers": {"jev": {"api_keey": "do-not-echo-me"}}}},
        {"decision": {"providers": {"unknown": {}}}},
    ],
)
def test_invalid_decision_table_has_clear_secret_safe_error(raw: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match="decision") as exc:
        _build_config(raw)
    assert "do-not-echo-me" not in str(exc.value)


def test_config_show_reports_jev_settings_without_resolving_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw import cli as cli_module
    from openbiliclaw.llm import RegistryBuildError

    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "do-not-show-key-value")
    monkeypatch.setattr(
        config_module, "load_config_with_diagnostics", lambda: (config, ConfigDiagnostics())
    )
    monkeypatch.setattr(cli_module, "_bootstrap_container_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_sync_outbound_proxy", lambda: None)
    monkeypatch.setitem(cli_module._APP_CONTEXT, "log_level", None)

    def no_chat_registry() -> None:
        raise RegistryBuildError("No chat providers configured in this isolated test")

    monkeypatch.setattr(cli_module, "_build_registry", no_chat_registry)

    result = CliRunner().invoke(cli_module.app, ["config-show"])

    assert result.exit_code == 0, result.output
    assert "Jev decision client" in result.output
    assert "推荐导演" in result.output
    assert "Jev 独立语义过滤" in result.output
    assert "jev-1.13.0" in result.output
    assert "TYPESAFE_API_KEY" in result.output
    assert "do-not-show-key-value" not in result.output


def test_example_toml_has_disabled_independent_jev_defaults() -> None:
    example = Path(__file__).parents[1] / "config.example.toml"
    raw = tomllib.loads(example.read_text(encoding="utf-8"))

    config = _build_config(raw, consult_environment=False)

    assert config.decision.providers.jev == JevDecisionConfig()
