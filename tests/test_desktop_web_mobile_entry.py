"""Static contract tests for the desktop web mobile-page entry.

The desktop web must keep a *labelled* 手机版 entry in the top bar (users
missed icon-only entries) that opens a QR drawer pointing at the /m/ mobile
web. A reachable page origin is preserved; only loopback pages are replaced
with the backend-reported LAN IP so a phone can reach them.
"""

from __future__ import annotations

from pathlib import Path

_DESKTOP_DIR = Path(__file__).resolve().parent.parent / "src" / "openbiliclaw" / "web" / "desktop"
_INDEX = (_DESKTOP_DIR / "index.html").read_text(encoding="utf-8")
_APP_JS = (_DESKTOP_DIR / "assets" / "js" / "app.js").read_text(encoding="utf-8")
_QR_JS = (_DESKTOP_DIR / "assets" / "js" / "mobile-qr.js").read_text(encoding="utf-8")
_CSS = (_DESKTOP_DIR / "assets" / "css" / "app.css").read_text(encoding="utf-8")


def test_topbar_has_labelled_mobile_entry() -> None:
    assert 'id="mobileQrBtn"' in _INDEX
    button = _INDEX.split('id="mobileQrBtn"', 1)[1].split("</button>", 1)[0]
    assert "手机版" in button, "the entry must carry a visible text label, not just an icon"
    assert ".top-mobile-btn" in _CSS
    top_btn_block = _CSS.split(".top-mobile-btn {", 1)[1].split("}", 1)[0]
    assert "background: var(--fg)" in top_btn_block, (
        "the entry pill must use the house solid-dark treatment so it stands out in the top bar"
    )


def test_mobile_qr_drawer_contract() -> None:
    for marker in (
        'id="mobileQrDrawer"',
        'id="mobileQrCanvas"',
        'id="mobileQrUrl"',
        'id="mobileQrCopyBtn"',
        'id="mobileQrHint"',
        'data-close="mobileQrDrawer"',
    ):
        assert marker in _INDEX, f"missing {marker}"
    assert "/web/assets/js/mobile-qr.js" in _INDEX, "QR generator script must be loaded"


def test_app_js_keeps_loopback_lan_ip_fallback() -> None:
    wiring = _APP_JS.split("async function openMobileQrDrawer", 1)
    assert len(wiring) == 2, "app.js must define openMobileQrDrawer"
    body = wiring[1].split('safeBind("#profileBtn"', 1)[0]
    assert "ENDPOINTS.qrInfo" in body, "must ask the lightweight QR endpoint for its LAN IP"
    assert "ENDPOINTS.health" not in body, "QR drawer must not trigger readiness/embedding probes"
    assert "lan_ip" in body
    assert "isLoopbackMobileHost" in body, "must warn when only a loopback address is available"
    assert 'safeBind("#mobileQrBtn"' in _APP_JS
    assert 'safeBind("#mobileQrCopyBtn"' in _APP_JS


def test_mobile_qr_preserves_https_public_origin() -> None:
    assert 'scheme === "https" ? "https" : "http"' in _QR_JS
    assert "`${safeScheme}://${urlHost}:${safePort}/m/`" in _QR_JS
    assert 'value === "[::1]"' in _QR_JS

    body = _APP_JS.split("async function openMobileQrDrawer", 1)[1].split(
        'safeBind("#profileBtn"', 1
    )[0]
    assert "pageHostIsReachable" in body
    assert 'window.location.protocol === "https:" ? "https" : "http"' in body
    assert "pageHostIsReachable ? def.host" in body
    assert "typedPort || def.port" in body


def test_mobile_qr_manual_backend_override_wins_over_lan_ip_and_origin() -> None:
    """A manually configured backend address must win for QR generation.

    On campus networks the auto-detected LAN IP may be on a different
    VLAN/subnet than the phone (or AP isolation may block peer-to-peer
    traffic), so the QR code must use the explicit address the user saved in
    desktop settings whenever one is present.
    """
    body = _APP_JS.split("async function openMobileQrDrawer", 1)[1].split(
        'safeBind("#profileBtn"', 1
    )[0]
    host_line = "const host = typedHost || (pageHostIsReachable ? def.host : (lanIp || def.host));"
    assert host_line in body
    assert "const port = typedPort || def.port;" in body


def test_mobile_qr_drawer_requeries_lan_ip_on_every_open() -> None:
    """The LAN IP moves with the network; a sticky cache must not win over it.

    A page-load prefetch value survives Wi-Fi switches for the whole session,
    so the drawer has to ask the backend again each time it opens and treat the
    cached address only as a fallback when that request fails.
    """
    body = _APP_JS.split("async function openMobileQrDrawer", 1)[1].split(
        'safeBind("#profileBtn"', 1
    )[0]
    assert "_cachedLanIp || String(" not in body, (
        "the cache must not short-circuit the fetch; it is a fallback, not the source"
    )
    fetch_at = body.index("requestJson(ENDPOINTS.qrInfo)")
    assert body.index("_cachedLanIp") > fetch_at, (
        "the cached address may only be consulted after the fresh request resolves"
    )


def test_first_visit_discovery_affordance() -> None:
    """New visitors must get an unmissable pointer at the mobile entry."""
    for marker in (
        'id="mobileQrDot"',
        'id="mobileQrCallout"',
        'id="mobileQrCalloutOpen"',
        'id="mobileQrCalloutClose"',
    ):
        assert marker in _INDEX, f"missing {marker}"
    assert "mobileQrSeen" in _APP_JS, "seen-state must persist so the callout shows only once"
    assert "initMobileQrDiscovery" in _APP_JS
    assert 'safeBind("#mobileQrCalloutOpen"' in _APP_JS, "callout body must open the QR drawer"


def test_qr_generator_is_self_contained_global() -> None:
    assert "window.OBCMobileQr" in _QR_JS
    for symbol in ("buildMobileWebUrl", "isLoopbackMobileHost", "createQrSvgMarkup"):
        assert symbol in _QR_JS
    assert "import " not in _QR_JS, "desktop web has no module build; keep it dependency-free"
    assert "`[${safeHost}]`" in _QR_JS, "IPv6 URL literals must be enclosed in brackets"


def test_desktop_settings_selects_survive_browser_page_translation() -> None:
    """Every <option> must carry an explicit value attribute and code-like
    selects must opt out of translation. Chrome/Edge page translation
    rewrites option TEXT nodes; without a value attribute select.value falls
    back to the translated text and garbage like '奥拉玛' lands in
    config.toml (field log 2026-07-05)."""
    import re
    from pathlib import Path

    html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    valueless = re.findall(r"<option(?:\s+selected=\"\")?>[^<]*</option>", html)
    assert valueless == [], f"value-less <option> elements: {valueless}"
    for select_id in (
        "llmInstanceProviderType",
        "llmDefaultChainPicker",
        "embeddingProvider",
        "embeddingFallbackProvider",
        "moduleSoulPicker",
        "moduleDiscoveryPicker",
        "moduleRecommendationPicker",
        "moduleEvaluationPicker",
        "logLevel",
        "logFileLevel",
    ):
        m = re.search(rf'<select id="{select_id}"[^>]*>', html)
        assert m, select_id
        assert 'translate="no"' in m.group(0), f"{select_id} missing translate=no"


def test_desktop_settings_routes_by_instance_not_provider_type() -> None:
    """Two endpoints may share one adapter type; only the stable instance ID
    must be unique within an ordered chain."""
    assert 'id="llmFallbackSameWarning"' not in _INDEX
    assert 'id="llmInstanceId"' in _INDEX
    assert "LLM_INSTANCE_ID_PATTERN" in _APP_JS
    assert "if (!chain.includes(instanceId))" in _APP_JS
