"""Desktop bootstrap assets are delivered once, in their dependency order."""

from __future__ import annotations

import gzip
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.desktop_assets import DESKTOP_SCRIPTS, DesktopAssets


@pytest.fixture()
def assets(tmp_path: Path) -> DesktopAssets:
    styles = (
        "/web/assets/css/app.css",
        "/web/assets/css/classic.css",
        "/shared/jev-settings.css",
    )
    for index, url in enumerate((*DESKTOP_SCRIPTS, *styles)):
        relative = url.replace("/web/", "desktop/", 1).removeprefix("/")
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"/* fixture {index} */\n", encoding="utf-8")
    html = "<head>" + "".join(f'<link rel="stylesheet" href="{url}">' for url in styles)
    html += "".join(f'<script src="{url}" defer></script>' for url in DESKTOP_SCRIPTS)
    html += "</head><body>desktop</body>"
    (tmp_path / "desktop/index.html").write_text(html, encoding="utf-8")
    return DesktopAssets(tmp_path)


def test_desktop_serves_one_ordered_bootstrap_script() -> None:
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)

    response = client.get("/web/")
    scripts = re.findall(r'<script src="([^"]+)" defer></script>', response.text)

    assert response.status_code == 200
    assert len(scripts) == 1
    assert scripts[0].startswith("/web/assets/desktop-bundle.js?v=")
    bundle = client.get(scripts[0])
    assert bundle.status_code == 200
    assert "window.OBCMobileQr" in bundle.text
    assert "createPendingActionCoordinator" in bundle.text
    assert "startDesktopBackendSession" in bundle.text
    assert client.get("/web/index.html").text == response.text
    assert client.get("/web").text == response.text
    assert client.get("/shared/i18n.js").status_code == 200
    assert client.get("/web/assets/js/app.js").status_code == 200


def test_bundle_keeps_dependency_order_and_only_compresses_each_build_once(
    assets: DesktopAssets, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_compress = gzip.compress
    calls = 0

    def compress(data: bytes, *, compresslevel: int, mtime: int) -> bytes:
        nonlocal calls
        calls += 1
        return original_compress(data, compresslevel=compresslevel, mtime=mtime)

    monkeypatch.setattr(gzip, "compress", compress)
    with ThreadPoolExecutor(max_workers=4) as pool:
        builds = list(pool.map(lambda _: assets.current(), range(8)))
    build = builds[0]
    assert all(other is build for other in builds)
    assert calls == 1
    assert gzip.decompress(build.gzip_javascript) == build.javascript
    offsets = [build.javascript.index(url.encode()) for url in DESKTOP_SCRIPTS]
    assert offsets == sorted(offsets)
    assert build.javascript.count(b"\n;\n") == len(DESKTOP_SCRIPTS) - 1


@pytest.mark.parametrize(
    "url",
    [
        "/web/assets/js/mobile-qr.js",
        "/web/assets/js/pending-actions.js",
        "/web/assets/js/saved-sync-core.js",
        "/web/assets/js/fish-settings.js",
        "/shared/jev-settings.js",
        "/shared/jev-settings.css",
    ],
)
def test_every_previously_unversioned_input_invalidates_the_build(
    assets: DesktopAssets, tmp_path: Path, url: str
) -> None:
    before = assets.current()
    path = tmp_path / url.replace("/web/", "desktop/", 1).removeprefix("/")
    path.write_text(path.read_text() + "/* changed */\n")
    after = assets.current()
    assert after.version != before.version
    assert f"?v={after.version}" in after.html


def test_retains_exact_previous_version_and_rejects_evicted_or_unknown_versions(
    assets: DesktopAssets, tmp_path: Path
) -> None:
    first = assets.current()
    path = tmp_path / "desktop/assets/js/app.js"
    path.write_text("/* second */")
    second = assets.current()
    response = assets.response(first.version, accept_encoding="identity")
    assert response.body == first.javascript
    assert "immutable" in response.headers["cache-control"]
    path.write_text("/* third version */")
    assets.current()
    assert assets.response(second.version, accept_encoding="identity").body == second.javascript
    for unknown in (first.version, "not-a-build", "../../config.toml"):
        missing = assets.response(unknown, accept_encoding="identity")
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("accept_encoding", "expected"),
    [
        ("", "identity"),
        ("identity", "identity"),
        ("gzip, deflate, br", "gzip"),
        ("gzip;q=0", "identity"),
        ("gzip;q=0, *;q=1", "identity"),
        ("gzip ; q=0, *;q=1", "identity"),
        ("gzip;q=0.5, identity;q=0.1", "gzip"),
        ("gzip;q=0.1", "identity"),
        ("gzip;q=invalid", "identity"),
        ("gzip;q=NaN", "identity"),
        ("identity;q=0, gzip", "gzip"),
        ("*", "gzip"),
        ("*;q=0", None),
        ("identity;q=0, gzip;q=0", None),
    ],
)
def test_encoding_quality_and_representation_etags(
    assets: DesktopAssets, accept_encoding: str, expected: str | None
) -> None:
    build = assets.current()
    response = assets.response(build.version, accept_encoding=accept_encoding)
    assert response.headers["vary"] == "Accept-Encoding"
    if expected is None:
        assert response.status_code == 406
        return
    assert response.status_code == 200
    assert response.headers["content-encoding"] == expected
    assert response.headers["x-content-type-options"] == "nosniff"
    decoded = gzip.decompress(response.body) if expected == "gzip" else response.body
    assert decoded == build.javascript
    not_modified = assets.response(
        build.version,
        accept_encoding=accept_encoding,
        if_none_match=f'"other", W/{response.headers["etag"]}',
    )
    assert not_modified.status_code == 304
    assert not_modified.body == b""
    assert not_modified.headers["etag"] == response.headers["etag"]


def test_api_does_not_recompress_gzip_or_override_gzip_quality_zero() -> None:
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)
    page = client.get("/web/")
    url = re.findall(r'<script src="([^"]+)" defer></script>', page.text)[0]
    compressed = client.get(url, headers={"Accept-Encoding": "gzip"})
    identity = client.get(url, headers={"Accept-Encoding": "gzip;q=0"})
    assert compressed.content == identity.content  # HTTPX decodes exactly one gzip layer.
    assert compressed.headers["content-encoding"] == "gzip"
    assert identity.headers["content-encoding"] == "identity"
    assert compressed.headers["etag"] != identity.headers["etag"]
    unchanged = client.get(
        url, headers={"Accept-Encoding": "gzip", "If-None-Match": compressed.headers["etag"]}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""


def test_bundle_is_public_but_recommendations_keep_the_password_gate() -> None:
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    app.state.auth_gate.auth.enabled = True
    app.state.auth_gate.auth.trust_loopback = False
    client = TestClient(app, client=("192.168.1.50", 5000))
    page = client.get("/web/")
    url = re.findall(r'<script src="([^"]+)" defer></script>', page.text)[0]
    assert client.get(url).status_code == 200
    assert client.get("/api/recommendations").status_code == 401


def test_manifest_mismatch_cannot_silently_reorder_scripts(
    assets: DesktopAssets, tmp_path: Path
) -> None:
    path = tmp_path / "desktop/index.html"
    path.write_text(path.read_text().replace(DESKTOP_SCRIPTS[0], "/unexpected-script.js"))
    with pytest.raises(ValueError, match="manifest"):
        assets.current()


def test_actual_script_bundle_is_valid_javascript(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is only needed for this JavaScript syntax check")
    root = Path(__file__).resolve().parents[1] / "src/openbiliclaw/web"
    path = tmp_path / "desktop-bundle.js"
    path.write_bytes(DesktopAssets(root).current().javascript)
    subprocess.run([node, "--check", str(path)], check=True, capture_output=True, text=True)
