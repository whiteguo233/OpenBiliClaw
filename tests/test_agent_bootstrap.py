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
