"""Per-host direct routes keep every other managed browser request on system routing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api import custom_sources
from openbiliclaw.sources.ego_setup import EgoSetupError, EgoSetupManager

SYSTEM_PROXY = """<dictionary> {
  ExceptionsList : <array> {
    0 : localhost
    1 : *.local
    2 : 192.168.0.0/16
    3 : <local>
  }
  HTTPEnable : 1
  HTTPProxy : 127.0.0.1
  HTTPPort : 7897
  HTTPSEnable : 1
  HTTPSProxy : 127.0.0.1
  HTTPSPort : 7897
  SOCKSEnable : 1
  SOCKSProxy : 127.0.0.1
  SOCKSPort : 7897
  ProxyAutoConfigEnable : 0
  ProxyAutoDiscoveryEnable : 0
}
"""


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.sys.platform", "darwin")
    for key in ("BROWSER", "SERVER_NAME", "PROFILE"):
        monkeypatch.delenv("OPENBILICLAW_EGO_" + key, raising=False)
    return EgoSetupManager(tmp_path)


async def test_explicit_hosts_survive_reload_and_keep_system_proxy_for_other_hosts(
    manager, monkeypatch
):
    command = AsyncMock(return_value=SYSTEM_PROXY)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    await manager.configure_direct_hosts(["www.bilinovel.com", "bilinovel.com"])
    loaded = EgoSetupManager(manager.root.parent)
    assert loaded.network_settings() == {"direct_hosts": ["bilinovel.com", "www.bilinovel.com"]}
    args = await loaded._proxy_arguments()
    assert args == [
        "--proxy-server=http=http://127.0.0.1:7897;https=http://127.0.0.1:7897;socks=socks5://127.0.0.1:7897",
        "--proxy-bypass-list=localhost;*.local;192.168.0.0/16;<local>;bilinovel.com;www.bilinovel.com",
    ]
    assert not any("no-proxy-server" in arg or "direct://" in arg for arg in args)


async def test_unconfigured_routes_leave_browser_system_proxy_discovery_untouched(
    manager, monkeypatch
):
    command = AsyncMock()
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", command)
    assert await manager._proxy_arguments() == []
    command.assert_not_awaited()


async def test_route_update_never_interrupts_a_live_session(manager):
    lease = manager.acquire_session_lease()
    try:
        with pytest.raises(EgoSetupError, match="ego_setup_sessions_busy"):
            await manager.configure_direct_hosts(["www.bilinovel.com"])
        assert manager.network_settings() == {"direct_hosts": []}
    finally:
        manager.release_session_lease(lease)


@pytest.mark.parametrize(
    "hosts",
    [
        ["*"],
        ["*.bilinovel.com"],
        [".bilinovel.com"],
        ["localhost"],
        ["127.0.0.1"],
        ["https://bilinovel.com"],
        ["bilinovel.com:443"],
        ["bilinovel.com;*"],
        [True],
        ["foo..com"],
    ],
)
async def test_routes_reject_wildcards_local_hosts_and_proxy_rule_injection(manager, hosts):
    with pytest.raises(ValueError, match="ego_setup_invalid_direct_hosts"):
        await manager.configure_direct_hosts(hosts)
    assert manager.network_settings() == {"direct_hosts": []}


async def test_idle_host_is_stopped_before_route_change_without_starting_another(manager):
    manager._pid = Mock(return_value=101)
    manager._stop = AsyncMock()
    await manager.configure_direct_hosts(["bilinovel.com"])
    manager._stop.assert_awaited_once()
    assert manager.network_settings() == {"direct_hosts": ["bilinovel.com"]}


@pytest.mark.parametrize(
    "field,value",
    [("ProxyAutoConfigEnable", "1"), ("ProxyAutoDiscoveryEnable", "1"), ("HTTPSPort", "0")],
)
async def test_unsupported_proxy_configuration_cannot_silently_become_direct(
    manager, monkeypatch, field, value
):
    await manager.configure_direct_hosts(["bilinovel.com"])
    import re

    proxy = re.sub(r"(" + field + r" : )\d+", r"\g<1>" + value, SYSTEM_PROXY)
    monkeypatch.setattr("openbiliclaw.sources.ego_setup._command", AsyncMock(return_value=proxy))
    with pytest.raises(EgoSetupError, match="ego_setup_proxy_config_unsupported"):
        await manager._proxy_arguments()


def test_routing_api_is_read_only_on_get_and_persists_explicit_choice(manager, monkeypatch):
    monkeypatch.setattr(custom_sources, "_ego_setup_manager", lambda _: manager)
    app = FastAPI()
    app.include_router(custom_sources.create_custom_sources_router(lambda: SimpleNamespace()))
    path = "/api/custom-sources/executors/ego/network"
    with TestClient(app) as client:
        assert client.get(path).json() == {"direct_hosts": []}
        response = client.put(path, json={"direct_hosts": ["www.bilinovel.com", "bilinovel.com"]})
        assert response.status_code == 200
        assert client.get(path).json() == response.json()
        assert client.put(path, json={"direct_hosts": ["*"]}).status_code == 400
        assert client.get(path).json() == response.json()
    assert manager._pid() is None


async def test_real_launch_uses_saved_routes_and_current_system_proxy(manager, monkeypatch):
    await manager.configure_direct_hosts(["bilinovel.com", "www.bilinovel.com"])
    monkeypatch.setattr(
        "openbiliclaw.sources.ego_setup._command", AsyncMock(return_value=SYSTEM_PROXY)
    )
    spawn = AsyncMock(return_value=SimpleNamespace(pid=101, returncode=None))
    monkeypatch.setattr("openbiliclaw.sources.ego_setup.asyncio.create_subprocess_exec", spawn)
    manager._pid = Mock(side_effect=[None, 101])
    assert await manager._launch(debugging=False) == 0
    args = spawn.call_args.args
    assert any(arg.startswith("--proxy-server=http=http://127.0.0.1:7897;") for arg in args)
    assert any(arg.endswith(";bilinovel.com;www.bilinovel.com") for arg in args)
    assert "--no-proxy-server" not in args


async def test_failed_idle_stop_keeps_previous_routes(manager):
    await manager.configure_direct_hosts(["bilinovel.com"])
    manager._pid = Mock(return_value=101)
    manager._stop = AsyncMock(side_effect=EgoSetupError("ego_setup_host_did_not_exit"))
    with pytest.raises(EgoSetupError, match="ego_setup_host_did_not_exit"):
        await manager.configure_direct_hosts(["www.bilinovel.com"])
    assert manager.network_settings() == {"direct_hosts": ["bilinovel.com"]}


@pytest.mark.parametrize("kind", ["external", "unsupported"])
async def test_route_writes_leave_external_or_unsupported_hosts_untouched(
    manager, monkeypatch, kind
):
    if kind == "external":
        monkeypatch.setenv("OPENBILICLAW_EGO_SERVER_NAME", "another-host")
    else:
        monkeypatch.setattr("openbiliclaw.sources.ego_setup.sys.platform", "win32")
    with pytest.raises(EgoSetupError, match="ego_setup_network_routes_unmanaged"):
        await manager.configure_direct_hosts(["bilinovel.com"])
    assert not manager.root.exists()


def test_corrupt_routing_file_does_not_silently_reset_the_users_route(manager):
    manager.root.mkdir()
    (manager.root / "network.json").write_text('{"version":1,"direct_hosts":["*"]}')
    with pytest.raises(EgoSetupError, match="ego_setup_invalid_network_routes"):
        manager.network_settings()


def test_routing_api_returns_conflict_during_live_takeover(manager, monkeypatch):
    monkeypatch.setattr(custom_sources, "_ego_setup_manager", lambda _: manager)
    app = FastAPI()
    app.include_router(custom_sources.create_custom_sources_router(lambda: SimpleNamespace()))
    lease = manager.acquire_session_lease()
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/custom-sources/executors/ego/network",
                json={"direct_hosts": ["bilinovel.com"]},
            )
        assert response.status_code == 409 and "ego_setup_sessions_busy" in response.text
        assert manager.network_settings() == {"direct_hosts": []}
    finally:
        manager.release_session_lease(lease)
