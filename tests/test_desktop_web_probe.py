"""Desktop public-Web URL probe UI contract regressions."""

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "src/openbiliclaw/web/desktop/index.html"
APP_JS = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"
APP_CSS = ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css"


def _function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(f"function {name}")
    end = source.index(f"function {next_name}", start)
    return source[start:end]


class _FormNestingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form_depth = 0
        self.nested_form = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag != "form":
            return
        if self.form_depth:
            self.nested_form = True
        self.form_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.form_depth -= 1


def test_desktop_web_probe_uses_an_external_form_and_accessible_busy_status() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    parser = _FormNestingParser()
    parser.feed(html)

    assert parser.nested_form is False
    assert '<form id="webProbeStandaloneForm" hidden></form>' in html
    assert (
        'id="webProbeUrl" type="url" maxlength="4096" required autocomplete="url" inputmode="url"'
    ) in html
    assert 'form="webProbeStandaloneForm" data-settings-ignore-dirty' in html
    assert 'form="webProbeStandaloneForm">自动探测</button>' in html
    assert 'id="webProbePanel" class="web-probe-panel" aria-busy="false"' in html
    assert ('id="webProbeStatus" aria-live="polite" aria-atomic="true" aria-busy="false"') in html
    assert "探测只生成候选方案" in html
    assert "仍需检查并点击「添加网站」才会保存" in html


def test_desktop_web_probe_calls_the_exact_read_only_api_contract() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    run_probe = _function_body(js, "runWebProbe", "setWebProbeRecipeNumber")

    assert 'webProbe: "/sources/web/probe"' in js
    assert "requestJsonStrict(ENDPOINTS.webProbe" in run_probe
    assert 'method: "POST"' in run_probe
    assert "body: JSON.stringify({ url: requestedUrl })" in run_probe
    assert "timeoutMs: 65000" in run_probe
    assert 'getInput("webProbeUrl") !== requestedUrl' in run_probe
    assert "const responseRequestedUrl" in run_probe
    assert "const requestedUrl = String(result?.requested_url" not in run_probe
    assert 'status === "no_match"' in run_probe
    assert 'status !== "ready"' in run_probe
    assert "result?.candidates" in run_probe
    for field in ("detail", "requested_url", "final_url", "page_title"):
        assert f"result?.{field}" in run_probe


def test_desktop_web_probe_renders_bounded_safe_candidate_previews() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    samples = _function_body(js, "renderWebProbeSamples", "renderWebProbeCandidates")
    candidates = _function_body(js, "renderWebProbeCandidates", "webProbeErrorMessage")
    safe_url = _function_body(js, "webProbeSafeHttpUrl", "setWebProbeBusy")

    assert ".slice(0, 3)" in samples
    assert ".filter(webProbeCandidateIsUsable).slice(0, 6)" in candidates
    assert 'link.rel = "noopener noreferrer"' in samples
    assert "link.href = safeUrl" in samples
    assert '["http:", "https:"]' in safe_url
    assert "url.username || url.password" in safe_url
    for field in (
        "strategy",
        "confidence",
        "detail",
        "entry_url",
        "discovery_method",
        "sample_items",
        "author_name",
        "published_at",
        "content_url",
    ):
        assert field in samples + candidates
    for body in (samples, candidates):
        assert ".innerHTML" not in body
        assert "insertAdjacentHTML" not in body
        assert "outerHTML" not in body
        assert ".textContent" in body


def test_adopting_web_probe_candidate_only_fills_recipe_form() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    adopt = _function_body(js, "applyWebProbeCandidate", "renderWebRecipes")

    assert "form?.reset()" in adopt
    for field_id in (
        "webRecipeName",
        "webRecipeStrategy",
        "webRecipeUrl",
        "webRecipeOrigins",
        "webRecipeInterval",
        "webRecipeMaxItems",
        "webRecipeMaxPages",
        "webRecipeDetailLimit",
        "webRecipeShare",
        "webRecipeItemSelector",
        "webRecipeLinkSelector",
        "webRecipeTitleSelector",
        "webRecipeEmptySelector",
    ):
        assert f'"{field_id}"' in adopt
    assert "syncWebRecipeStrategyFields()" in adopt
    assert ".focus({ preventScroll: true })" in adopt
    assert "当前尚未保存" in adopt
    assert "requestJson" not in adopt
    assert 'method: "POST"' not in adopt
    assert "createWebRecipe" not in adopt


def test_desktop_web_probe_can_retry_after_validation_or_network_failure() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    busy = _function_body(js, "setWebProbeBusy", "clearWebProbeCandidates")
    errors = _function_body(js, "webProbeErrorMessage", "runWebProbe")
    init = _function_body(js, "initWebRecipeManager", "initSourceCards")

    assert "button.disabled = busy" in busy
    assert 'busy ? "探测中…"' in busy
    assert 'webProbeHasRun ? "重新探测" : "自动探测"' in busy
    assert "error?.status === 422" in errors
    assert "请求超时" not in errors
    assert "探测超时" in errors
    assert "无法连接本地后端" in errors
    assert 'addEventListener("input"' in init
    assert "clearWebProbeCandidates()" in init
    assert "URL 已更改，请重新探测" in init
    assert 'addEventListener("submit"' in init


def test_desktop_web_probe_has_responsive_candidate_styles() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    for selector in (
        ".web-probe-panel",
        ".web-probe-input-row",
        ".web-probe-candidate",
        ".web-probe-confidence",
        ".web-probe-samples",
        ".web-probe-adopt",
    ):
        assert selector in css
    assert ".web-probe-candidates[hidden]" in css
    assert ".web-probe-input-row { grid-template-columns: 1fr; }" in css
