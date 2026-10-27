"""Static regressions for PCWeb model service probe controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_exposes_and_wires_model_probe_controls() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'id="probeLlmChain"' in html
    assert 'id="probeEmbedding"' in html
    assert 'id="probeLlmChainStatus"' in html
    assert 'id="probeEmbeddingStatus"' in html
    assert 'aria-live="polite"' in html

    assert 'configProbe: "/config/probe-service"' in js
    assert 'function probeConfigService(kind, config, instanceId = "")' in js
    assert 'probeConfigService("llm_chain", buildConfigUpdate())' in js
    assert 'probeConfigService("llm_instance", buildConfigUpdate(), instanceId)' in js
    assert 'probeConfigService("embedding", buildConfigUpdate())' in js
    assert "timeoutMs: 125000" in js
    assert "function renderProbeResult" in js

    assert ".settings-probe-row" in css
    assert ".settings-probe-status" in css


def test_desktop_web_settings_exposes_and_wires_ordered_chain_probe() -> None:
    """The ordered global route is tested as a chain, while every card can
    probe one exact endpoint without triggering fallback."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="probeLlmChain"' in html
    assert 'id="probeLlmChainStatus"' in html
    assert 'data-instance-action="probe"' in js
    assert "function runLlmChainProbe()" in js
    assert 'probeConfigService("llm_chain", buildConfigUpdate())' in js
    assert 'safeBind("#probeLlmChain"' in js


def test_desktop_web_settings_exposes_and_wires_network_proxy() -> None:
    """The general tab must expose the [network].proxy field with a
    connectivity probe, and the copy must state CN requests stay direct
    (invariant: overseas-only proxy, never a global proxy)."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="networkProxyMode"' in html
    assert 'id="networkProxy"' in html
    assert 'id="probeNetworkProxy"' in html
    assert 'id="probeNetworkProxyStatus"' in html
    assert "海外" in html
    assert "国内请求始终直连" in html

    # The fallback literal must track the backend [network].mode default
    # (system since v0.3.175), else an omitted field renders the wrong mode.
    assert 'setSelect("networkProxyMode", config.network?.mode || "system")' in js
    assert 'setInput("networkProxy", config.network?.proxy || "")' in js
    assert 'network: { mode: getInput("networkProxyMode"), proxy: getInput("networkProxy") }' in js
    assert "function runNetworkProxyConfigProbe()" in js
    assert 'probeConfigService("network_proxy",' in js
    assert 'safeBind("#probeNetworkProxy"' in js


def test_desktop_web_instance_editor_persists_deepseek_reasoning_effort() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert (
        '["openai", "claude", "gemini", "deepseek", "openrouter", "orcarouter", '
        '"openai_compatible"].includes(providerType)'
    ) in js
    assert "reasoning_effort:" in js
    assert 'getInput("llmInstanceReasoning")' in js


def test_orcarouter_provider_exposed_across_web_surfaces() -> None:
    """OrcaRouter must be selectable in the desktop settings and the first-run
    setup wizard, with matching key-link and default-model wiring."""
    desktop_html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(
        encoding="utf-8"
    )
    setup_html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert '<option value="orcarouter">OrcaRouter</option>' in desktop_html
    assert '<option value="orcarouter">OrcaRouter</option>' in setup_html
    assert 'orcarouter: "OrcaRouter"' in desktop_js
    assert (
        'orcarouter: { model: "openai/gpt-4o", base_url: "https://api.orcarouter.ai/v1" }'
    ) in desktop_js
    assert 'orcarouter: ["https://www.orcarouter.ai/keys", "OrcaRouter"]' in setup_html
    assert 'orcarouter: "openai/gpt-4o"' in setup_html
    assert '"orcarouter"' in setup_html


def test_requesty_provider_exposed_across_web_surfaces() -> None:
    """Requesty must be selectable in the desktop settings and the first-run
    setup wizard, with matching key-link and default-model wiring."""
    desktop_html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(
        encoding="utf-8"
    )
    setup_html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert '<option value="requesty">Requesty</option>' in desktop_html
    assert '<option value="requesty">Requesty</option>' in setup_html
    assert 'requesty: "Requesty"' in desktop_js
    assert (
        'requesty: { model: "openai/gpt-4o-mini", base_url: "https://router.requesty.ai/v1" }'
    ) in desktop_js
    assert 'requesty: ["https://app.requesty.ai/api-keys", "Requesty"]' in setup_html
    assert 'requesty: "openai/gpt-4o-mini"' in setup_html


def test_api_route_provider_exposed_across_web_surfaces() -> None:
    desktop_html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(
        encoding="utf-8"
    )
    setup_html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    popup_html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
    popup_js = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")

    for html in (desktop_html, setup_html, popup_html):
        assert '<option value="api_route">API Route</option>' in html
    for js in (desktop_js, popup_js):
        assert 'api_route: "API Route"' in js
        assert 'api_route: { model: "gpt-5.5", base_url: "https://global.api-route.com/v1" }' in js
    assert 'api_route: ["https://global.api-route.com/", "API Route"]' in setup_html
    assert 'api_route: "gpt-5.5"' in setup_html


def test_cheaperinference_provider_exposed_across_web_surfaces() -> None:
    desktop_html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(
        encoding="utf-8"
    )
    setup_html = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    popup_html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
    popup_js = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")

    for html in (desktop_html, setup_html, popup_html):
        assert '<option value="cheaperinference">Cheaper Inference</option>' in html
    for js in (desktop_js, popup_js):
        assert 'cheaperinference: "Cheaper Inference"' in js
        assert (
            'cheaperinference: { model: "gpt-5.4-mini", '
            'base_url: "https://api.cheaperinference.com/v1" }'
        ) in js
    assert 'cheaperinference: ["https://cheaperinference.com/signup", "Cheaper Inference"]' in (
        setup_html
    )
    assert 'cheaperinference: "gpt-5.4-mini"' in setup_html
    assert 'id="cfgCheaperinferenceKey"' in popup_html
    assert 'setVal("cfgCheaperinferenceKey", cfg.llm?.cheaperinference?.api_key)' in popup_js
