"""Phone connection discovery and scan payloads are provider independent."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import qrcode
from fastapi.testclient import TestClient
from qrcode.util import MODE_8BIT_BYTE, QRData

from openbiliclaw.api import app as app_module
from openbiliclaw.config import Config

_ROOT = Path(__file__).resolve().parent.parent


def test_connection_discovery_keeps_system_and_embedded_nodes_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw import config as config_module
    from openbiliclaw.runtime import tailnet_supervisor

    cfg = Config(data_dir=str(tmp_path))
    cfg.tailnet.enabled = True
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.7")
    monkeypatch.setattr(app_module, "_detect_system_tailscale_ips", lambda: ["100.100.1.2"])
    monkeypatch.setattr(
        tailnet_supervisor,
        "read_tailnet_status",
        lambda _: {
            "event": "ready",
            "port": 8421,
            "ips": ["100.100.1.3", "fd7a:115c:a1e0::1234", "127.0.0.1", "invalid"],
            "auth_key": "must-not-be-returned",
        },
    )
    app = app_module.create_app(memory_manager=object(), database=object(), soul_engine=object())
    app.state.api_listen_host = "0.0.0.0"
    app.state.api_listen_port = 19090
    response = TestClient(app).get("/api/qr-info")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "lan_ip": "192.168.1.7",
        "api_host": "0.0.0.0",
        "api_port": 19090,
        "tailscale_addresses": [
            {"host": "100.100.1.2", "port": 19090, "source": "system"},
            {"host": "100.100.1.3", "port": 8421, "source": "embedded"},
            {"host": "fd7a:115c:a1e0::1234", "port": 8421, "source": "embedded"},
        ],
    }


@pytest.mark.parametrize("enabled,event", [(False, "ready"), (True, "stopped"), (True, "error")])
def test_inactive_embedded_nodes_are_not_advertised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, event: str
) -> None:
    from openbiliclaw import config as config_module
    from openbiliclaw.runtime import tailnet_supervisor

    cfg = Config(data_dir=str(tmp_path))
    cfg.tailnet.enabled = enabled
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: None)
    monkeypatch.setattr(app_module, "_detect_system_tailscale_ips", lambda: [])
    monkeypatch.setattr(
        tailnet_supervisor,
        "read_tailnet_status",
        lambda _: {"event": event, "ips": ["100.100.1.3"], "port": 8420},
    )
    app = app_module.create_app(memory_manager=object(), database=object(), soul_engine=object())
    assert TestClient(app).get("/api/qr-info").json()["tailscale_addresses"] == []


def test_lan_detection_excludes_tailscale_ipv4_and_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_interface_ipv4_candidates", lambda: ["100.100.1.2"])
    monkeypatch.setattr(app_module, "_default_route_ip", lambda: "100.100.1.2")
    monkeypatch.setattr(
        app_module, "_interface_ipv6_candidates", lambda: ["fd7a:115c:a1e0::1234", "fd12::7"]
    )
    monkeypatch.setattr(app_module, "_default_route_ipv6", lambda: None)
    assert app_module._detect_lan_ip() == "fd12::7"
    assert app_module._detect_system_tailscale_ips() == ["100.100.1.2", "fd7a:115c:a1e0::1234"]


def test_shared_connection_links_and_original_loopback_repro() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for shared frontend tests")
    subprocess.run([node, "--test", "tests/js/mobile-network-qr.test.mjs"], cwd=_ROOT, check=True)


@pytest.mark.parametrize(
    "text",
    ["http://192.168.1.7:8420/m/", "https://" + "a" * 60 + ".example.com/" + "prefix/" * 12 + "m/"],
)
@pytest.mark.parametrize("surface", ["extension", "desktop"])
def test_local_qr_matrix_matches_python_reference(text: str, surface: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for QR encoder validation")
    code = """
      import { createQrSvgMarkup } from './extension/popup/popup-qr.js';
      import { readFileSync } from 'node:fs';
      import vm from 'node:vm';
      let render = createQrSvgMarkup;
      if (process.argv[2] === 'desktop') {
        const context = vm.createContext({window: {}, TextEncoder});
        const source = readFileSync('src/openbiliclaw/web/desktop/assets/js/mobile-qr.js', 'utf8');
        vm.runInContext(source, context);
        render = context.window.OBCMobileQr.createQrSvgMarkup;
      }
      console.log(render(JSON.parse(process.argv[1])));
    """
    svg = subprocess.check_output(
        [node, "--input-type=module", "-e", code, json.dumps(text), surface], cwd=_ROOT, text=True
    )
    size = int(re.search(r'viewBox="0 0 (\d+)', svg)[1])  # type: ignore[index]
    actual = [[False] * size for _ in range(size)]
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", svg):
        actual[int(y)][int(x)] = True
    reference = qrcode.QRCode(
        version=5 if size == 45 else 10,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        border=4,
        mask_pattern=0,
    )
    reference.add_data(QRData(text.encode(), mode=MODE_8BIT_BYTE), optimize=0)
    reference.make(fit=False)
    assert actual == reference.get_matrix()
