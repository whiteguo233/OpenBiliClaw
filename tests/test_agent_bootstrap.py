from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


def _load_bootstrap_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_agent_bootstrap", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def test_bootstrap_extends_no_proxy_for_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.delenv("no_proxy", raising=False)

    bootstrap.ensure_local_no_proxy()

    assert os.environ["NO_PROXY"] == "example.com,localhost,127.0.0.1,::1"
    assert os.environ["no_proxy"] == "example.com,localhost,127.0.0.1,::1"


def test_bootstrap_defaults_to_lan_accessible_bind_host(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    assert args.host == "0.0.0.0"


def test_bootstrap_connects_to_loopback_when_binding_all_interfaces() -> None:
    assert bootstrap._connect_host_for_bind_host("0.0.0.0") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("::") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("127.0.0.1") == "127.0.0.1"
    assert bootstrap._connect_host_for_bind_host("192.168.1.100") == "192.168.1.100"


def test_is_user_data_only_root_accepts_packaged_data_root(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text("[api]\nport = 18420\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    assert bootstrap._is_user_data_only_root(tmp_path)


def test_is_user_data_only_root_rejects_unknown_entries(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (tmp_path / "random.txt").write_text("not ours\n", encoding="utf-8")

    assert not bootstrap._is_user_data_only_root(tmp_path)


def test_ensure_repo_checkout_clones_into_existing_user_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "OpenBiliClaw"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (project_dir / "data").mkdir()
    (project_dir / "data" / "openbiliclaw.db").write_bytes(b"existing db")

    def fake_run_streaming(cmd: list[str], **_kwargs: object) -> None:
        clone_dir = Path(cmd[-1])
        clone_dir.mkdir(parents=True, exist_ok=True)
        (clone_dir / ".git").mkdir()
        (clone_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (clone_dir / "config.example.toml").write_text("[general]\n", encoding="utf-8")
        (clone_dir / "src").mkdir()
        (clone_dir / "src" / "marker.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "which", lambda name: "git" if name == "git" else None)
    monkeypatch.setattr(bootstrap, "run_streaming", fake_run_streaming)

    result = bootstrap.ensure_repo_checkout(project_dir, "https://example/repo.git", "main")

    assert result == project_dir.resolve()
    assert (project_dir / ".git").exists()
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / "config.example.toml").exists()
    assert (project_dir / "config.toml").read_text(encoding="utf-8") == "language = 'zh'\n"
    assert (project_dir / "data" / "openbiliclaw.db").read_bytes() == b"existing db"


def _write_minimal_config(
    tmp_path: Path,
    *,
    embedding_provider: str = "",
    embedding_model: str = "",
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai"',
                "",
                "[llm.openai]",
                'api_key = "sk-test"',
                "",
                "[llm.embedding]",
                f'provider = "{embedding_provider}"',
                f'model = "{embedding_model}"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_init_decisions_required_when_source_and_embedding_were_not_explicit(
    tmp_path: Path,
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["embedding", "xhs", "douyin", "youtube"]
    assert decisions["xhs"]["policy"] == "pending"
    assert decisions["douyin"]["policy"] == "pending"
    assert decisions["youtube"]["policy"] == "pending"
    assert decisions["embedding"]["source"] == "missing"


def test_init_decisions_accept_explicit_source_and_embedding_choices(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--embedding-provider",
            "ollama",
            "--embedding-model",
            "bge-m3",
            "--no-xhs",
            "--yes-douyin",
            "--no-youtube",
        ]
    )

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=True)

    assert decisions["missing"] == []
    assert decisions["xhs"]["policy"] == "disabled"
    assert decisions["douyin"]["policy"] == "enabled"
    assert decisions["youtube"]["policy"] == "disabled"
    assert decisions["embedding"]["source"] == "flags"


def test_init_decisions_accept_existing_embedding_but_still_require_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin", "youtube"]
    assert decisions["embedding"]["source"] == "config"


def test_init_decisions_required_for_all_optional_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin", "youtube"]


def test_apply_embedding_config_writes_embedding_owned_credentials(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)

    result = bootstrap.apply_embedding_config(
        tmp_path,
        provider="openai",
        model="text-embedding-3-small",
        base_url="https://embed.example.com/v1",
        api_key="sk-embedding",
    )

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "llm.embedding.base_url" in result["written"]
    assert "llm.embedding.api_key" in result["written"]
    assert "[llm.embedding]" in text
    assert 'provider = "openai"' in text
    assert 'model = "text-embedding-3-small"' in text
    assert 'base_url = "https://embed.example.com/v1"' in text
    assert 'api_key = "sk-embedding"' in text


def test_docker_run_rewrites_default_ollama_embedding_to_compose_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_minimal_config(
        tmp_path,
        embedding_provider="",
        embedding_model="",
    )
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "docker",
            "--skip-start",
            "--embedding-provider",
            "ollama",
            "--embedding-model",
            "bge-m3",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )

    returncode = bootstrap.run(args)

    config = bootstrap.read_simple_toml(tmp_path / "config.toml")
    assert returncode == 0
    assert config["llm"]["embedding"]["provider"] == "ollama"
    assert config["llm"]["embedding"]["model"] == "bge-m3"
    assert config["llm"]["embedding"]["base_url"] == "http://ollama:11434/v1"


def test_should_auto_wire_embedding_when_unconfigured_local() -> None:
    # Flag-driven install that never passed --embedding-* and left embedding
    # empty → default to local Ollama so dedup isn't silently disabled.
    assert bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="", mode="local"
    )


def test_should_not_auto_wire_embedding_when_already_configured() -> None:
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="gemini", mode="local"
    )


def test_should_not_auto_wire_embedding_when_explicitly_disabled() -> None:
    # User passed --embedding-provider "" to deliberately turn embedding off.
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg="", effective_provider="", mode="local"
    )


def test_should_not_auto_wire_embedding_under_docker() -> None:
    # The container can't reach the host's Ollama at localhost, so wiring it
    # would just mint a broken config.
    assert not bootstrap.should_auto_wire_embedding(
        embedding_provider_arg=None, effective_provider="", mode="docker"
    )


def test_detect_missing_secrets_defaults_to_deepseek_when_provider_absent(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                "",
                "[llm.deepseek]",
                'api_key = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = bootstrap.detect_missing_secrets(tmp_path)

    assert status["provider"] == "deepseek"
    assert status["missing"] == ["llm.deepseek.api_key", "bilibili.cookie"]


def test_parser_accepts_openai_compatible_provider(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--provider", "openai_compatible"]
    )

    assert args.provider == "openai_compatible"


def test_detect_missing_secrets_flags_openai_compatible_connection_fields(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai_compatible"',
                "",
                "[llm.openai_compatible]",
                'api_key = ""',
                'base_url = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = bootstrap.detect_missing_secrets(tmp_path)

    assert status["provider"] == "openai_compatible"
    assert status["missing"] == [
        "llm.openai_compatible.api_key",
        "llm.openai_compatible.base_url",
        "bilibili.cookie",
    ]


def test_detect_missing_secrets_flags_ollama_without_chat_model(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "ollama"',
                "",
                "[llm.ollama]",
                'base_url = "http://localhost:11434/v1"',
                'model = ""',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = bootstrap.detect_missing_secrets(tmp_path)

    assert status["missing"] == ["llm.ollama.model"]


def test_v2_template_provider_writes_target_one_instance_and_default_chain(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    (tmp_path / "config.toml").write_text(
        (project_root / "config.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    bootstrap.apply_provider_override(tmp_path, "openai_compatible")
    bootstrap.apply_llm_api_key(tmp_path, "openai_compatible", "sk-relay")
    bootstrap.apply_llm_base_url(
        tmp_path,
        "openai_compatible",
        "https://relay.example/v1",
    )
    bootstrap.apply_llm_model(tmp_path, "openai_compatible", "relay-model")

    data = bootstrap.read_simple_toml(tmp_path / "config.toml")
    status = bootstrap.detect_missing_secrets(tmp_path)
    llm = data["llm"]
    instance = llm["instances"]["openai-compatible"]

    assert llm["routing_version"] == 2
    assert llm["default_chain"] == ["openai-compatible"]
    assert llm["instances"]["deepseek"]["enabled"] is False
    assert instance["provider_type"] == "openai_compatible"
    assert instance["enabled"] is True
    assert instance["api_key"] == "sk-relay"
    assert instance["base_url"] == "https://relay.example/v1"
    assert instance["model"] == "relay-model"
    assert status["provider"] == "openai_compatible"
    assert status["instance_id"] == "openai-compatible"
    assert status["missing"] == ["bilibili.cookie"]


def test_v2_provider_override_preserves_configured_deepseek_fallback(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    (tmp_path / "config.toml").write_text(
        (project_root / "config.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    bootstrap.apply_llm_api_key(tmp_path, "deepseek", "sk-deepseek")
    bootstrap.apply_provider_override(tmp_path, "openai")

    llm = bootstrap.read_simple_toml(tmp_path / "config.toml")["llm"]

    assert llm["default_chain"] == ["openai", "deepseek"]
    assert llm["instances"]["deepseek"]["enabled"] is True
    assert llm["instances"]["deepseek"]["api_key"] == "sk-deepseek"


def test_v2_bootstrap_module_override_creates_complete_derived_instance(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    (tmp_path / "config.toml").write_text(
        (project_root / "config.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bootstrap.apply_llm_api_key(tmp_path, "deepseek", "sk-deepseek")

    summary = bootstrap.apply_module_overrides(
        tmp_path,
        ["soul=deepseek:deepseek-quality"],
    )

    data = bootstrap.read_simple_toml(tmp_path / "config.toml")
    route = data["llm"]["routes"]["soul"]
    instance_id = route["chain"][0]
    instance = data["llm"]["instances"][instance_id]
    assert summary["modules"] == ["llm.soul=deepseek:deepseek-quality"]
    assert route == {"inherit": False, "chain": [instance_id]}
    assert instance["provider_type"] == "deepseek"
    assert instance["api_key"] == "sk-deepseek"
    assert instance["model"] == "deepseek-quality"
    assert instance["enabled"] is True


def test_v2_bootstrap_module_override_reuses_matching_derived_instance(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    (tmp_path / "config.toml").write_text(
        (project_root / "config.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bootstrap.apply_llm_api_key(tmp_path, "deepseek", "sk-deepseek")

    bootstrap.apply_module_overrides(tmp_path, ["soul=deepseek:deepseek-quality"])
    first = bootstrap.read_simple_toml(tmp_path / "config.toml")
    first_instance_id = first["llm"]["routes"]["soul"]["chain"][0]
    bootstrap.apply_module_overrides(tmp_path, ["soul=deepseek:deepseek-quality"])
    second = bootstrap.read_simple_toml(tmp_path / "config.toml")

    assert second["llm"]["routes"]["soul"]["chain"] == [first_instance_id]
    assert [
        instance_id
        for instance_id, instance in second["llm"]["instances"].items()
        if instance.get("provider_type") == "deepseek"
        and instance.get("model") == "deepseek-quality"
    ] == [first_instance_id]


def test_v2_reuse_copies_all_instances_and_tolerates_bad_num_ctx(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    (target / "config.toml").write_text(
        (project_root / "config.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (source / "config.toml").write_text(
        """
[llm]
routing_version = 2
default_chain = ["relay-a", "relay-b"]

[llm.instances.relay-a]
name = "Relay A"
provider_type = "openai_compatible"
enabled = true
api_key = "sk-a"
model = "model-a"
base_url = "https://a.example/v1"
num_ctx = "invalid"

[llm.instances.relay-b]
name = "Relay B"
provider_type = "openai_compatible"
enabled = true
api_key = "sk-b"
model = "model-b"
base_url = "https://b.example/v1"

[llm.routes.soul]
inherit = false
chain = ["relay-b", "relay-a"]
""".strip(),
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)
    data = bootstrap.read_simple_toml(target / "config.toml")

    assert data["llm"]["default_chain"] == ["relay-a", "relay-b"]
    assert data["llm"]["instances"]["relay-a"]["api_key"] == "sk-a"
    assert data["llm"]["instances"]["relay-a"]["num_ctx"] == 0
    assert data["llm"]["instances"]["relay-b"]["api_key"] == "sk-b"
    assert data["llm"]["routes"]["soul"] == {
        "inherit": False,
        "chain": ["relay-b", "relay-a"],
    }
    assert "llm.instances.relay-a" in summary["reused"]
    assert "llm.instances.relay-b" in summary["reused"]


def test_reuse_config_secrets_copies_openai_compatible_connection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    (target / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "deepseek"',
                "",
                "[llm.openai_compatible]",
                'api_key = ""',
                'model = ""',
                'base_url = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai_compatible"',
                "",
                "[llm.openai_compatible]",
                'api_key = "sk-relay"',
                'model = "relay-model"',
                'base_url = "https://relay.example/v1"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)
    status = bootstrap.detect_missing_secrets(target)
    target_config = bootstrap.read_simple_toml(target / "config.toml")

    assert "llm.openai_compatible.api_key" in summary["reused"]
    assert "llm.openai_compatible.model" in summary["reused"]
    assert "llm.openai_compatible.base_url" in summary["reused"]
    assert status["missing"] == []
    assert target_config["llm"]["default_provider"] == "openai_compatible"
    assert target_config["llm"]["openai_compatible"]["api_key"] == "sk-relay"
    assert target_config["llm"]["openai_compatible"]["model"] == "relay-model"
    assert target_config["llm"]["openai_compatible"]["base_url"] == "https://relay.example/v1"


def test_reuse_config_secrets_copies_remote_provider_model_and_base_url(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    target.mkdir()
    source.mkdir()
    (target / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "deepseek"',
                "",
                "[llm.openrouter]",
                'api_key = ""',
                'model = ""',
                'base_url = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openrouter"',
                "",
                "[llm.openrouter]",
                'api_key = "sk-router"',
                'model = "anthropic/claude-sonnet-4-6"',
                'base_url = "https://openrouter.ai/api/v1"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = bootstrap.reuse_config_secrets(target, source)
    target_config = bootstrap.read_simple_toml(target / "config.toml")

    assert "llm.openrouter.api_key" in summary["reused"]
    assert "llm.openrouter.model" in summary["reused"]
    assert "llm.openrouter.base_url" in summary["reused"]
    assert target_config["llm"]["default_provider"] == "openrouter"
    assert target_config["llm"]["openrouter"]["api_key"] == "sk-router"
    assert target_config["llm"]["openrouter"]["model"] == "anthropic/claude-sonnet-4-6"
    assert target_config["llm"]["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"


def test_run_reports_auto_wired_embedding_from_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "deepseek"',
                "",
                "[llm.deepseek]",
                'api_key = ""',
                "",
                "[llm.embedding]",
                'provider = ""',
                'model = ""',
                'base_url = ""',
                "",
                "[bilibili]",
                'cookie = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_ollama_ready",
        lambda _models: {"running": True, "pulled": ["bge-m3"]},
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    final = status_lines[-1]

    assert returncode == 0
    assert final["message"] == "skipped_start"
    assert final["details"]["init_decisions"]["embedding"] == {
        "source": "config",
        "provider": "ollama",
        "model": "bge-m3",
        "explicit": True,
    }


def test_build_init_command_appends_all_source_flags_for_local(tmp_path: Path) -> None:
    command = bootstrap.build_init_command(
        "local",
        tmp_path,
        "--no-xhs",
        "--no-douyin",
        "--yes-youtube",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    assert command[-8:] == [
        "init",
        "--no-xhs",
        "--no-douyin",
        "--yes-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
    ]


def test_human_install_choice_parser_accepts_numbers_and_aliases() -> None:
    assert bootstrap.resolve_human_llm_choice("") == "deepseek"
    assert bootstrap.resolve_human_llm_choice("1") == "deepseek"
    assert bootstrap.resolve_human_llm_choice("2") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("relay") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("ollama") == "ollama"
    assert bootstrap.resolve_human_llm_choice("bad") is None


def test_secret_presence_label_never_includes_secret_value() -> None:
    assert "sk-test" not in bootstrap.mask_secret_for_prompt("sk-test")
    assert bootstrap.mask_secret_for_prompt("") == "not set"


def test_collect_human_install_wizard_refuses_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_human_install_wizard()


def test_human_install_answers_reject_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        bootstrap.HumanInstallAnswers(provider="openai-compat")


def test_collect_human_llm_defaults_to_deepseek() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["", ""])
    secret_inputs = iter(["sk-deepseek"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "deepseek"
    assert answer.llm_api_key == "sk-deepseek"
    assert answer.llm_model == "deepseek-v4-flash"
    assert answer.llm_base_url is None
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_openai_compat_relay_collects_triplet() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["2", "", "https://relay.example/v1", ""])
    secret_inputs = iter(["sk-relay"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "openai_compatible"
    assert answer.provider in bootstrap.SUPPORTED_PROVIDERS
    assert answer.llm_base_url == "https://relay.example/v1"
    assert answer.llm_api_key == "sk-relay"
    assert answer.llm_model == "gpt-5-nano"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_openai_compat_numeric_preset_uses_vendor_defaults() -> None:
    plain_inputs = iter(["2", "2", "", ""])
    secret_inputs = iter(["sk-kimi"])

    answer = bootstrap.collect_human_llm_config(
        input_func=lambda _prompt: next(plain_inputs),
        secret_input_func=lambda _prompt: next(secret_inputs),
    )

    assert answer.provider == "openai_compatible"
    assert answer.llm_base_url == "https://api.moonshot.ai/v1"
    assert answer.llm_api_key == "sk-kimi"
    assert answer.llm_model == "kimi-k2.6"


def test_collect_human_llm_ollama_needs_no_api_key() -> None:
    # ollama is off the interactive menu but still accepted by name for
    # backward-compat / non-interactive installs.
    plain_inputs = iter(["ollama", "qwen2.5:7b"])
    secret_prompts: list[str] = []
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda _prompt: next(plain_inputs),
        secret_input_func=lambda prompt: secret_prompts.append(prompt) or "",
    )

    assert answer.provider == "ollama"
    assert answer.llm_api_key == ""
    assert answer.llm_model == "qwen2.5:7b"
    assert secret_prompts == []


def test_collect_human_llm_does_not_reuse_key_across_providers() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["3", ""])
    secret_inputs = iter(["", "sk-openai"])

    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
        existing_provider="deepseek",
        existing_api_key="sk-old-deepseek",
    )

    assert answer.provider == "openai"
    assert answer.llm_api_key == "sk-openai"
    assert all("press Enter to reuse" not in prompt for _kind, prompt in prompts)


def test_prompt_secret_converts_getpass_warning_to_runtime_error() -> None:
    import getpass

    def raise_getpass_warning(_prompt: str) -> str:
        raise getpass.GetPassWarning("echo cannot be disabled")

    with pytest.raises(RuntimeError, match="cannot disable terminal echo"):
        bootstrap._prompt_secret(raise_getpass_warning, "API Key")


def test_collect_human_install_wizard_default_path() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    secret_inputs = iter(["sk-deepseek"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "deepseek"
    assert answer.embedding_provider == "ollama"
    assert answer.embedding_model == "bge-m3"
    assert answer.bilibili_favorite_limit == 300
    assert answer.bilibili_follow_limit == 100
    assert answer.xhs is False
    assert answer.douyin is False
    assert answer.youtube is False
    assert answer.cookie_mode == "extension"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_install_wizard_manual_cookie() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(
        [
            "ollama",
            "qwen2.5:7b",
            "3",
            "120",
            "80",
            "n",
            "y",
            "n",
            "manual",
        ]
    )
    secret_inputs = iter(["SESSDATA=test; bili_jct=test; DedeUserID=1"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "ollama"
    assert answer.embedding_provider == ""
    assert answer.embedding_model == ""
    assert answer.douyin is True
    assert answer.cookie_mode == "manual"
    assert answer.bilibili_cookie.startswith("SESSDATA=")
    assert any(kind == "secret" and "Cookie" in prompt for kind, prompt in prompts)


def test_apply_human_install_answers_sets_all_bootstrap_args(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])
    answers = bootstrap.HumanInstallAnswers(
        provider="deepseek",
        llm_api_key="sk-test",
        llm_model="deepseek-v4-flash",
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.provider == "deepseek"
    assert args.llm_api_key == "sk-test"
    assert args.llm_model == "deepseek-v4-flash"
    assert args.embedding_provider == "ollama"
    assert args.embedding_model == "bge-m3"
    assert args.no_xhs is True
    assert args.yes_douyin is True
    assert args.no_youtube is True
    assert args.bilibili_cookie == "SESSDATA=test"
    assert args.bilibili_favorite_limit == 120
    assert args.bilibili_follow_limit == 80


def test_apply_human_install_answers_does_not_clear_existing_secret_on_empty_answer(
    tmp_path: Path,
) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--llm-api-key", "explicit"]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek", llm_api_key="")

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.llm_api_key == "explicit"


def test_apply_human_install_answers_reconciles_extension_wait_flag(
    tmp_path: Path,
) -> None:
    for cookie_mode, expected_wait in [
        ("extension", True),
        ("manual", False),
        ("existing", False),
    ]:
        args = bootstrap.build_arg_parser().parse_args(
            [
                "--project-dir",
                str(tmp_path),
                "--wait-for-extension-cookie",
            ]
        )
        answers = bootstrap.HumanInstallAnswers(
            provider="deepseek",
            cookie_mode=cookie_mode,
            bilibili_cookie="SESSDATA=test" if cookie_mode == "manual" else "",
        )

        bootstrap.apply_human_install_answers_to_args(args, answers)

        assert args.wait_for_extension_cookie is expected_wait


def test_run_interactive_confirm_collects_full_human_install_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: bootstrap.HumanInstallAnswers(
            provider="deepseek",
            llm_api_key="sk-new",
            llm_model="deepseek-v4-flash",
            embedding_provider="ollama",
            embedding_model="bge-m3",
            xhs=False,
            douyin=False,
            youtube=False,
            cookie_mode="manual",
            bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_ollama_ready",
        lambda _models: {"running": True, "pulled": ["bge-m3"]},
    )

    returncode = bootstrap.run(args)

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    output = capsys.readouterr().out

    assert returncode == 0
    assert 'default_provider = "deepseek"' in text
    assert 'api_key = "sk-new"' in text
    assert 'provider = "ollama"' in text
    assert args.wait_for_extension_cookie is False
    assert "sk-new" not in output
    assert "SESSDATA=test" not in output


def test_run_interactive_confirm_without_tty_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    assert returncode == 2
    assert "interactive confirmation requires a terminal" in output


def test_run_interactive_confirm_getpass_warning_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cannot disable terminal echo for secret prompt")
        ),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]


def test_run_interactive_confirm_apply_error_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek")

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(bootstrap, "collect_human_install_wizard", lambda **_kwargs: answers)
    monkeypatch.setattr(
        bootstrap,
        "apply_human_install_answers_to_args",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unknown provider")),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]


def test_interactive_answers_apply_source_flags() -> None:
    answers = bootstrap.InitConfirmationAnswers(
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    argv = bootstrap.confirmation_answers_to_bootstrap_args(answers)

    assert argv == [
        "--embedding-provider",
        "ollama",
        "--embedding-model",
        "bge-m3",
        "--no-xhs",
        "--yes-douyin",
        "--no-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
        "--bilibili-cookie",
        "SESSDATA=test; bili_jct=test; DedeUserID=1",
    ]


def test_collect_interactive_confirmations_collects_bilibili_limits() -> None:
    inputs = iter(["", "", "120", "80", "n", "y", "n", "manual", "SESSDATA=test"])

    answers = bootstrap.collect_interactive_confirmations(input_func=lambda _prompt: next(inputs))

    assert answers.embedding_provider == "ollama"
    assert answers.embedding_model == "bge-m3"
    assert answers.bilibili_favorite_limit == 120
    assert answers.bilibili_follow_limit == 80
    assert answers.xhs is False
    assert answers.douyin is True
    assert answers.youtube is False
    assert answers.cookie_mode == "manual"
    assert answers.bilibili_cookie == "SESSDATA=test"


def test_collect_interactive_confirmations_requires_input_func() -> None:
    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_interactive_confirmations(input_func=None)


def test_wait_for_cookie_sync_returns_when_cookie_appears(tmp_path: Path) -> None:
    calls = {"count": 0}

    def detector(_project_dir: Path) -> dict[str, object]:
        calls["count"] += 1
        missing = ["bilibili.cookie"] if calls["count"] == 1 else []
        return {"missing": missing}

    assert (
        bootstrap.wait_for_cookie_sync(
            tmp_path,
            timeout_seconds=1,
            interval_seconds=0,
            detector=detector,
        )
        is True
    )


def test_wait_for_cookie_sync_times_out(tmp_path: Path) -> None:
    assert (
        bootstrap.wait_for_cookie_sync(
            tmp_path,
            timeout_seconds=0.01,
            interval_seconds=0,
            detector=lambda _project_dir: {"missing": ["bilibili.cookie"]},
        )
        is False
    )


def _service_check_runner(payload: dict[str, object]):
    def runner(
        _cmd: list[str],
        *,
        check: bool = True,
        cwd: Path | None = None,
    ) -> bootstrap.CommandResult:
        return bootstrap.CommandResult(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    return runner


def test_pre_init_service_checks_pass_when_probe_reports_services_ready(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": True,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is True
    assert result["failed"] == []
    assert result["services"]["llm"]["provider"] == "deepseek"
    assert result["services"]["embedding"]["provider"] == "ollama"


def test_pre_init_service_checks_fail_when_llm_probe_fails(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": False, "provider": "deepseek", "error": "401 unauthorized"},
            "embedding": {
                "available": True,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is False
    assert result["failed"] == ["llm"]
    assert result["services"]["llm"]["error"] == "401 unauthorized"


def test_pre_init_service_checks_fail_when_embedding_probe_fails(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": False,
                "provider": "ollama",
                "model": "bge-m3",
                "error": "empty embedding vector",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is False
    assert result["failed"] == ["embedding"]
    assert result["services"]["embedding"]["error"] == "empty embedding vector"


def test_pre_init_service_checks_accept_disabled_embedding(tmp_path: Path) -> None:
    payload = {
        "services": {
            "llm": {"available": True, "provider": "deepseek", "error": ""},
            "embedding": {
                "available": True,
                "provider": "",
                "model": "",
                "skipped": True,
                "error": "",
            },
        }
    }

    result = bootstrap.run_pre_init_service_checks(
        tmp_path,
        "local",
        runner=_service_check_runner(payload),
    )

    assert result["available"] is True
    assert result["failed"] == []
    assert result["services"]["embedding"]["skipped"] is True


def test_run_blocks_auto_init_when_pre_init_service_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
        ]
    )
    init_calls: list[object] = []

    monkeypatch.setattr(
        bootstrap,
        "ensure_repo_checkout",
        lambda project_dir, _repo_url, _branch: project_dir,
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_config_toml",
        lambda _project_dir: tmp_path / "config.toml",
    )
    monkeypatch.setattr(bootstrap, "start_local_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        bootstrap,
        "run_pre_init_service_checks",
        lambda *_args, **_kwargs: {
            "available": False,
            "failed": ["embedding"],
            "services": {
                "llm": {"available": True, "provider": "openai", "error": ""},
                "embedding": {
                    "available": False,
                    "provider": "ollama",
                    "model": "bge-m3",
                    "error": "empty embedding vector",
                },
            },
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "run_init_streaming",
        lambda *args, **_kwargs: init_calls.append(args) or 0,
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 0
    assert init_calls == []
    assert any(
        event["status"] == "service_check_failed"
        and event["message"] == "pre_init_service_check_failed"
        for event in status_lines
    )


def test_docker_runtime_config_copy_commands(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "config.toml").write_text("[llm]\n", encoding="utf-8")
    (data_dir / "bilibili_cookie.json").write_text('{"cookie":"x"}', encoding="utf-8")

    commands = bootstrap.build_docker_runtime_sync_commands(tmp_path)

    assert [
        "docker",
        "cp",
        str(tmp_path / "config.toml"),
        "openbiliclaw-backend:/app/runtime/config.toml",
    ] in commands
    assert [
        "docker",
        "cp",
        str(data_dir / "bilibili_cookie.json"),
        "openbiliclaw-backend:/app/runtime/data/bilibili_cookie.json",
    ] in commands


def test_docker_secret_detector_command_reads_runtime_config() -> None:
    command = bootstrap.build_docker_missing_secrets_command()

    assert command[:3] == ["docker", "exec", "openbiliclaw-backend"]
    assert "/app/runtime/config.toml" in " ".join(command)
    assert "/app/runtime/data/bilibili_cookie.json" in " ".join(command)


def test_build_init_command_appends_explicit_source_flags_for_docker(tmp_path: Path) -> None:
    command = bootstrap.build_init_command(
        "docker",
        tmp_path,
        "--yes-xhs",
        "--yes-douyin",
        "--no-youtube",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    assert command == [
        "docker",
        "exec",
        "-i",
        "openbiliclaw-backend",
        "openbiliclaw",
        "init",
        "--yes-xhs",
        "--yes-douyin",
        "--no-youtube",
        "--bilibili-favorite-limit",
        "120",
        "--bilibili-follow-limit",
        "80",
    ]


def test_run_init_streaming_emits_machine_readable_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [
        sys.executable,
        "-c",
        "\n".join(
            [
                "print('1/4 拉取数据', flush=True)",
                "print('  · 分析偏好: 已用 20s / 预计还需 ~50s', flush=True)",
                "print('阶段完成: 当前池子 0/15，本轮发现 20 条', flush=True)",
            ]
        ),
    ]

    returncode = bootstrap.run_init_streaming(command, cwd=None, check=True)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    progress_events = [event for event in status_lines if event["message"] == "init_progress"]
    assert returncode == 0
    assert "1/4 拉取数据" in output
    assert any(event["details"]["phase"] == "1/4" for event in progress_events)
    assert any("分析偏好" in event["details"]["line"] for event in progress_events)
    assert any("阶段完成" in event["details"]["line"] for event in progress_events)


def test_parser_rejects_conflicting_xhs_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-xhs", "--no-xhs"])


def test_parser_rejects_conflicting_douyin_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-douyin", "--no-douyin"])


def test_parser_rejects_conflicting_youtube_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-youtube", "--no-youtube"])


# ── Reused-cookie live validation (init-progress spec Phase 3) ──────────────


def _final_status(missing: list[str] | None = None) -> dict:
    return {
        "provider": "deepseek",
        "missing": list(missing or []),
        "has_cookie_inline": True,
        "has_cookie_file": True,
    }


def _reuse_summary(reused: list[str]) -> dict:
    return {"reused": reused, "skipped": [], "source": "/old/install"}


def _init_status(bilibili_check: str) -> dict:
    return {"prerequisites": {"bilibili_check": bilibili_check}}


def test_reused_cookie_stale_downgrades_to_needs_secrets() -> None:
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie", "data/bilibili_cookie.json"]),
        init_status=_init_status("failed"),
    )
    assert validated["reused_cookie_stale"] is True
    assert bootstrap.STALE_COOKIE_MISSING_ENTRY in validated["missing"]
    assert (
        bootstrap.STALE_COOKIE_MISSING_ENTRY
        == "bilibili.cookie (stale — reused cookie failed live validation)"
    )
    label = bootstrap.backend_healthy_label(validated)
    assert label == "needs_secrets"
    assert label != "complete"


def test_reused_cookie_valid_keeps_complete() -> None:
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("ok"),
    )
    assert "reused_cookie_stale" not in validated
    assert validated["missing"] == []
    assert bootstrap.backend_healthy_label(validated) == "complete"


def test_reused_cookie_unverifiable_probe_does_not_downgrade() -> None:
    # Backend unreachable / malformed payload / still "checking" → the
    # install.sh disclaimer branch stays; never claim staleness we didn't see.
    for payload in (None, {}, {"prerequisites": {}}, _init_status("checking")):
        validated = bootstrap.apply_reused_cookie_validation(
            _final_status(),
            reuse_summary=_reuse_summary(["data/bilibili_cookie.json"]),
            init_status=payload,
        )
        assert "reused_cookie_stale" not in validated
        assert bootstrap.backend_healthy_label(validated) == "complete"


def test_cookie_not_reused_skips_live_validation_fold() -> None:
    # This run reused only LLM keys — a failed bilibili probe must not be
    # attributed to a "reused stale cookie" (it was never reused).
    validated = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["llm.deepseek.api_key"]),
        init_status=_init_status("failed"),
    )
    assert "reused_cookie_stale" not in validated
    assert bootstrap.backend_healthy_label(validated) == "complete"


def test_backend_healthy_label_still_reports_plain_missing_secrets() -> None:
    assert (
        bootstrap.backend_healthy_label(_final_status(["bilibili.cookie"]))
        == "running_with_missing_secrets"
    )


def test_stale_entry_is_not_duplicated_on_refold() -> None:
    once = bootstrap.apply_reused_cookie_validation(
        _final_status(),
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("failed"),
    )
    twice = bootstrap.apply_reused_cookie_validation(
        once,
        reuse_summary=_reuse_summary(["bilibili.cookie"]),
        init_status=_init_status("failed"),
    )
    assert twice["missing"].count(bootstrap.STALE_COOKIE_MISSING_ENTRY) == 1


def test_set_toml_raw_value_matches_quoted_instance_section() -> None:
    import tomllib

    content = '[llm.instances."openai"]\nenabled = true\napi_key = "sk-old"\n'
    updated = bootstrap.set_toml_raw_value(
        content, "llm.instances.openai", "enabled", "false"
    )
    updated = bootstrap.set_toml_string_value(
        updated, "llm.instances.openai", "api_key", "sk-new"
    )
    assert updated.count("[llm.instances") == 1
    parsed = tomllib.loads(updated)
    assert parsed["llm"]["instances"]["openai"]["enabled"] is False
    assert parsed["llm"]["instances"]["openai"]["api_key"] == "sk-new"


def test_clear_toml_string_value_matches_quoted_section() -> None:
    import tomllib

    content = '[llm.instances."openai"]\nbase_url = "https://relay.example/v1"\n'
    updated, changed = bootstrap.clear_toml_string_value(
        content, "llm.instances.openai", "base_url"
    )
    assert changed
    assert updated.count("[llm.instances") == 1
    assert tomllib.loads(updated)["llm"]["instances"]["openai"]["base_url"] == ""


def test_toml_table_path_equates_bare_and_quoted_keys() -> None:
    assert bootstrap._toml_table_path('[llm.instances.openai]') == (
        "llm",
        "instances",
        "openai",
    )
    assert bootstrap._toml_table_path('[llm.instances."openai"]') == (
        "llm",
        "instances",
        "openai",
    )
    assert bootstrap._toml_table_path('[llm.instances."openai-compatible"]') == (
        "llm",
        "instances",
        "openai-compatible",
    )

