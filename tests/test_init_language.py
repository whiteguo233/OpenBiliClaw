"""Initialization selects a persistent locale before runtime prerequisites."""

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from openbiliclaw import cli, cli_i18n, config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_initialize_logging", lambda **_: None)
    monkeypatch.setattr(cli_i18n, "_cached_locale", "zh-Hans")
    for name in cli_i18n.LANGUAGE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("args", "interactive", "answer", "expected"),
    [
        ([], True, "1\n", "zh-Hans"),
        ([], True, "2\n", "zh-Hant"),
        ([], True, "3\n", "en"),
        ([], True, "invalid\nzh-TW\n", "zh-Hant"),
        (["--language", "en-US"], False, "", "en"),
        (["--language", "zh-TW"], True, "", "zh-Hant"),
    ],
)
def test_init_selects_language_before_runtime_setup(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    interactive: bool,
    answer: str,
    expected: str,
) -> None:
    seen = []

    def prepare() -> None:
        seen.append(config.load_config().language)
        seen.append(cli_i18n.resolve_cli_locale())
        raise typer.Exit(0)

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: interactive)
    monkeypatch.setattr(cli, "_prepare_init_runtime", prepare)
    result = CliRunner().invoke(cli.app, ["init", *args], input=answer)
    assert result.exit_code == 0, result.output
    assert seen == [expected, expected]
    if not args:
        assert "Language" in result.output
    else:
        assert "Select (1/2/3)" not in result.output


def test_invalid_explicit_language_does_not_write_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["init", "--language", "fr"])
    assert result.exit_code == 2
    assert not (tmp_path / "config.toml").exists()


def test_noninteractive_init_keeps_config_without_prompting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: False)
    cli._select_init_language()
    assert not (tmp_path / "config.toml").exists()


def test_init_language_preserves_other_configuration() -> None:
    cfg = config.load_config()
    cfg.language = "en"
    cfg.llm.openai.model = "test-preserved-model"
    config.save_config(cfg)
    cli._select_init_language("zh-Hant")
    saved = config.load_config()
    assert saved.language == "zh-Hant"
    assert saved.llm.openai.model == "test-preserved-model"
