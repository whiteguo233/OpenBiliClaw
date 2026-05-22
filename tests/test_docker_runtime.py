"""Tests for optional Docker proxy bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.docker_runtime import (
    bootstrap_runtime_environment,
    bootstrap_runtime_root,
    is_running_in_container,
    resolve_optional_proxy_env,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_optional_proxy_env_skips_when_proxy_already_configured() -> None:
    env = {
        "HTTP_PROXY": "http://custom-proxy:8080",
        "NO_PROXY": "example.com",
    }

    updates = resolve_optional_proxy_env(
        env,
        can_connect=lambda host, port, timeout: True,
    )

    assert updates == {}


def test_resolve_optional_proxy_env_adds_proxy_when_host_proxy_is_reachable() -> None:
    env = {
        "NO_PROXY": "example.com",
    }

    updates = resolve_optional_proxy_env(
        env,
        can_connect=lambda host, port, timeout: host == "host.docker.internal" and port == 7897,
    )

    expected_proxy = "http://host.docker.internal:7897"
    assert updates["HTTP_PROXY"] == expected_proxy
    assert updates["HTTPS_PROXY"] == expected_proxy
    assert updates["ALL_PROXY"] == expected_proxy
    assert updates["http_proxy"] == expected_proxy
    assert updates["https_proxy"] == expected_proxy
    assert updates["all_proxy"] == expected_proxy
    assert updates["NO_PROXY"] == "example.com,127.0.0.1,localhost,host.docker.internal"
    assert updates["no_proxy"] == "example.com,127.0.0.1,localhost,host.docker.internal"


def test_resolve_optional_proxy_env_returns_empty_when_host_proxy_is_unreachable() -> None:
    updates = resolve_optional_proxy_env(
        {},
        can_connect=lambda host, port, timeout: False,
    )

    assert updates == {}


def test_bootstrap_runtime_root_creates_default_config_and_directories(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    template = tmp_path / "config.example.toml"
    template.write_text("[general]\nlanguage = \"zh\"\n", encoding="utf-8")

    bootstrap_runtime_root(runtime_root=runtime_root, template_path=template)

    assert (runtime_root / "config.toml").read_text(encoding="utf-8") == template.read_text(
        encoding="utf-8"
    )
    assert (runtime_root / "data").is_dir()
    assert (runtime_root / "logs").is_dir()


def test_bootstrap_runtime_root_keeps_existing_config(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    existing = runtime_root / "config.toml"
    existing.write_text("[general]\nlanguage = \"en\"\n", encoding="utf-8")
    template = tmp_path / "config.example.toml"
    template.write_text("[general]\nlanguage = \"zh\"\n", encoding="utf-8")

    bootstrap_runtime_root(runtime_root=runtime_root, template_path=template)

    assert existing.read_text(encoding="utf-8") == "[general]\nlanguage = \"en\"\n"


def test_bootstrap_runtime_root_seeds_embedding_base_url_for_ollama_sidecar(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    template = tmp_path / "config.example.toml"
    template.write_text(
        "\n".join(
            [
                "[llm.ollama]",
                'base_url = ""',
                "",
                "[llm.embedding]",
                'provider = ""',
                'model = ""',
                'base_url = ""',
            ]
        ),
        encoding="utf-8",
    )

    bootstrap_runtime_root(
        runtime_root=runtime_root,
        template_path=template,
        env={
            "OPENBILICLAW_SEED_OLLAMA_DEFAULTS": "1",
            "OPENBILICLAW_OLLAMA_BASE_URL": "http://ollama:11434/v1",
            "OPENBILICLAW_EMBEDDING_MODEL": "bge-m3",
        },
    )

    text = (runtime_root / "config.toml").read_text(encoding="utf-8")
    assert '[llm.embedding]' in text
    assert 'provider = "ollama"' in text
    assert 'model = "bge-m3"' in text
    assert 'base_url = "http://ollama:11434/v1"' in text


def test_bootstrap_runtime_environment_prepares_runtime_root_and_proxy(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    template = tmp_path / "config.example.toml"
    template.write_text("[general]\nlanguage = \"zh\"\n", encoding="utf-8")
    env = {
        "OPENBILICLAW_PROJECT_ROOT": str(runtime_root),
        "OPENBILICLAW_CONFIG_TEMPLATE": str(template),
    }

    bootstrap_runtime_environment(
        env,
        can_connect=lambda host, port, timeout: host == "host.docker.internal" and port == 7897,
        in_container=lambda _env: True,
    )

    expected_proxy = "http://host.docker.internal:7897"
    assert env["OPENBILICLAW_PROJECT_ROOT"] == str(runtime_root)
    assert env["HTTP_PROXY"] == expected_proxy
    assert env["HTTPS_PROXY"] == expected_proxy
    assert env["ALL_PROXY"] == expected_proxy
    assert (runtime_root / "config.toml").exists()
    assert (runtime_root / "data").is_dir()
    assert (runtime_root / "logs").is_dir()


def test_bootstrap_runtime_environment_skips_proxy_outside_container(tmp_path: Path) -> None:
    """On a native host the proxy bootstrap must not touch HTTP(S)_PROXY.

    Even when ``host.docker.internal`` is reachable (Docker Desktop always
    resolves it on macOS), we only want to route traffic through the
    host's Clash when we're actually running inside a container.
    """
    runtime_root = tmp_path / "runtime"
    template = tmp_path / "config.example.toml"
    template.write_text("[general]\nlanguage = \"zh\"\n", encoding="utf-8")
    env = {
        "OPENBILICLAW_PROJECT_ROOT": str(runtime_root),
        "OPENBILICLAW_CONFIG_TEMPLATE": str(template),
    }

    bootstrap_runtime_environment(
        env,
        can_connect=lambda host, port, timeout: True,
        in_container=lambda _env: False,
    )

    assert env["OPENBILICLAW_PROJECT_ROOT"] == str(runtime_root)
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "ALL_PROXY" not in env
    # Runtime root still gets set up — only the proxy step is gated.
    assert (runtime_root / "config.toml").exists()
    assert (runtime_root / "data").is_dir()
    assert (runtime_root / "logs").is_dir()


def test_is_running_in_container_respects_explicit_env() -> None:
    assert is_running_in_container({"OPENBILICLAW_IN_CONTAINER": "1"}) is True
    assert is_running_in_container({"OPENBILICLAW_IN_CONTAINER": "yes"}) is True


def test_is_running_in_container_ignores_blank_env(monkeypatch) -> None:
    """Blank value must NOT count as a container marker.

    On a developer machine without a Docker/Podman marker file present,
    the function should return False even if the env var exists but is
    whitespace-only.
    """
    from openbiliclaw import docker_runtime as module

    monkeypatch.setattr(
        module.Path,
        "exists",
        lambda self: False,
    )
    assert is_running_in_container({"OPENBILICLAW_IN_CONTAINER": "   "}) is False
    assert is_running_in_container({}) is False
