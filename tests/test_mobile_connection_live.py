"""Opt-in real HTTP acceptance; start an actual backend before running this file."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_ORIGIN = os.environ.get("OPENBILICLAW_LIVE_BASE_URL", "").rstrip("/")


@pytest.mark.integration
@pytest.mark.skipif(not _ORIGIN, reason="Set OPENBILICLAW_LIVE_BASE_URL to a real backend")
def test_real_discovery_to_shared_qr_to_mobile_entry_and_business_api() -> None:
    """Use real metadata and JS encoders, then request the generated destinations."""
    node = shutil.which("node")
    assert node, "Node is required for the actual desktop/shared QR modules"
    assert urlsplit(_ORIGIN).scheme in {"http", "https"}
    with httpx.Client(trust_env=False, timeout=25) as client:
        response = client.get(f"{_ORIGIN}/api/qr-info")
        response.raise_for_status()
        assert response.headers["cache-control"] == "no-store"
        info = response.json()
        script = """
          const fs = require('node:fs');
          require(process.argv[1]);
          globalThis.window = {};
          require(process.argv[2]);
          const info = JSON.parse(fs.readFileSync(0, 'utf8'));
          const {choices} = OBCMobileConnection.connectionChoices({
            endpoint: {host: '127.0.0.1', port: info.api_port}, info,
          });
          process.stdout.write(JSON.stringify(choices.filter(c => c.url).map(c => ({
            id: c.id, url: c.url, svg: window.OBCMobileQr.createQrSvgMarkup(c.url),
          }))));
        """
        result = subprocess.run(
            [
                node,
                "-e",
                script,
                str(_ROOT / "src/openbiliclaw/web/shared/mobile-connection.js"),
                str(_ROOT / "src/openbiliclaw/web/desktop/assets/js/mobile-qr.js"),
            ],
            input=json.dumps(info),
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        choices = json.loads(result.stdout)
        assert any(choice["id"] == "lan" for choice in choices), "Real LAN discovery required"
        # The system IPv6 self-route is a separate, explicitly reported check.
        # This acceptance covers LAN plus available IPv4 tailnet destinations.
        targets = [choice for choice in choices if ":" not in urlsplit(choice["url"]).hostname]
        assert targets
        for choice in targets:
            endpoint = urlsplit(choice["url"])
            assert endpoint.hostname
            host = ipaddress.ip_address(endpoint.hostname)
            assert not host.is_loopback and not host.is_unspecified and not host.is_link_local
            assert "<svg" in choice["svg"] and "<path" in choice["svg"]
            page = client.get(choice["url"])
            page.raise_for_status()
            assert "OpenBiliClaw" in page.text and 'id="app"' in page.text
            assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', page.text)
            assert assets
            for asset in assets:
                resource = client.get(urljoin(choice["url"], asset))
                resource.raise_for_status()
                assert resource.content
                assert "text/html" not in resource.headers.get("content-type", "")
            backend = choice["url"].split("/m/", 1)[0]
            for path in ["/api/ping", "/api/health"]:
                health = client.get(backend + path)
                health.raise_for_status()
                assert health.json()["status"] == "ok"
            recommendations = client.get(backend + "/api/recommendations")
            recommendations.raise_for_status()
            assert recommendations.json()["items"], "Real recommendation inventory required"
