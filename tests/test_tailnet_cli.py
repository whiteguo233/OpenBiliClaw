from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import typer

from openbiliclaw import cli as cli_module
from openbiliclaw import config as config_module
from openbiliclaw.config import Config

if TYPE_CHECKING:
    from pathlib import Path


def test_tailnet_enable_normalizes_hostname_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    saved: list[Config] = []
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(config_module, "save_config", lambda value: saved.append(value))

    cli_module.tailnet_enable("  OpenBiliClaw-Home-01  ")

    assert config.tailnet.enabled is True
    assert config.tailnet.hostname == "openbiliclaw-home-01"
    assert saved == [config]


def test_tailnet_enable_rejects_invalid_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config()
    monkeypatch.setattr(config_module, "load_config", lambda: config)

    with pytest.raises(typer.Exit):
        cli_module.tailnet_enable("not.a.dns-label")

    assert config.tailnet.enabled is False


def test_tailnet_disable_preserves_identity_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.tailnet.enabled = True
    config.tailnet.hostname = "my-home-host"
    saved: list[Config] = []
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(config_module, "save_config", lambda value: saved.append(value))

    cli_module.tailnet_disable()

    assert config.tailnet.enabled is False
    assert config.tailnet.hostname == "my-home-host"
    assert saved == [config]


def test_tailnet_cli_refuses_shadowed_config_local_switch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "config.local.toml").write_text(
        "[tailnet]\nenabled = true\n",
        encoding="utf-8",
    )

    with pytest.raises(typer.Exit):
        cli_module.tailnet_disable()

    assert not (tmp_path / "config.toml").exists()


def test_tailnet_cli_refuses_environment_managed_switch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_TAILNET_ENABLED", "false")

    with pytest.raises(typer.Exit):
        cli_module.tailnet_enable(None)

    assert not (tmp_path / "config.toml").exists()


def test_tailnet_event_callback_opens_each_login_url_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import webbrowser

    opened: list[str] = []

    class _InlineThread:
        def __init__(
            self,
            *,
            target: Any,
            args: tuple[object, ...] = (),
            name: str,
            daemon: bool,
        ) -> None:
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(cli_module.threading, "Thread", _InlineThread)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    callback = cli_module._build_tailnet_event_callback()
    event = {
        "protocol": 1,
        "event": "needs_login",
        "auth_url": "https://login.tailscale.com/a/one-time",
    }

    callback(event)
    callback(event)

    assert opened == ["https://login.tailscale.com/a/one-time"]


def test_tailnet_start_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import tailnet_supervisor

    config = Config()
    config.tailnet.enabled = True

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise tailnet_supervisor.TailnetHelperNotFoundError("missing helper")

    monkeypatch.setattr(tailnet_supervisor, "start_tailnet_if_enabled", _fail)

    assert (
        cli_module._start_tailnet_runtime_best_effort(
            config,
            8420,
            api_host="127.0.0.1",
        )
        is None
    )


def test_tailnet_rejects_api_host_without_loopback_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import tailnet_supervisor

    config = Config()
    config.tailnet.enabled = True

    def _unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("helper must not start for an incompatible API bind")

    monkeypatch.setattr(tailnet_supervisor, "start_tailnet_if_enabled", _unexpected)

    assert (
        cli_module._start_tailnet_runtime_best_effort(
            config,
            8420,
            api_host="192.0.2.10",
        )
        is None
    )


def test_tailnet_status_uses_recent_runtime_port_for_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime import tailnet_supervisor

    config = Config(data_dir=str(tmp_path / "data"))
    config.api.port = 8420
    status_path = config.data_path / "tailnet" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        '{"event":"ready","dns_name":"remote-host.ts.net","port":18420}\n',
        encoding="utf-8",
    )
    captured: list[tuple[str, list[tuple[str, str]]]] = []
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(tailnet_supervisor, "find_tailnet_helper", lambda _cfg: tmp_path / "helper")
    monkeypatch.setattr(
        cli_module,
        "_print_key_value_table",
        lambda title, rows: captured.append((title, rows)),
    )

    cli_module.tailnet_status()

    assert captured[0][0] == "应用内 Tailnet"
    assert ("配置端口", "8420") in captured[0][1]
    assert ("最近监听端口", "18420") in captured[0][1]
    assert ("MagicDNS 地址", "http://remote-host.ts.net:18420") in captured[0][1]


def test_run_api_server_stops_tailnet_helper_after_uvicorn_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    import openbiliclaw.api.app as api_app_module
    from openbiliclaw.runtime import api_server

    stopped: list[bool] = []
    supervisor = SimpleNamespace(stop=lambda: stopped.append(True))
    app = SimpleNamespace(state=SimpleNamespace(degraded=False))
    # Keep this unit test free of the default four-process worker children.
    monkeypatch.setenv("OPENBILICLAW_WORKER", "0")

    monkeypatch.setattr(api_app_module, "create_app", lambda: app)
    monkeypatch.setattr(config_module, "load_config", Config)
    monkeypatch.setattr(
        cli_module,
        "_start_tailnet_runtime_best_effort",
        lambda *_args, **_kwargs: supervisor,
    )
    monkeypatch.setattr(api_server, "create_wildcard_listener_sockets", lambda *_args: None)
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)

    cli_module._run_api_server(host="127.0.0.1", port=8420)

    assert stopped == [True]
