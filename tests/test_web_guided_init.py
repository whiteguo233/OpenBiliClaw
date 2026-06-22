import re
from pathlib import Path


def test_setup_wizard_static_contract_uses_guided_init_endpoint() -> None:
    """Static guard: setup must reference guided init and not the legacy poke."""
    html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'data-panel="3"' in html
    assert "GET /api/init-status" in html or 'fetch("/api/init-status"' in html
    assert 'fetch("/api/init"' in html
    assert "init_progress" in html
    assert "/api/init-completed" not in html


def test_desktop_web_static_contract_exposes_guided_init_cta() -> None:
    """Static guard for the desktop guided-init CTA wiring."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'initStatus: "/init-status"' in app_js
    assert 'startInit: "/init"' in app_js
    assert "renderInitOnboarding" in app_js
    assert "buildInitChecklist" in app_js
    assert "INIT_SOURCE_OPTIONS" in app_js
    assert "init_progress" in app_js
    assert "openbiliclaw init" not in app_js
    assert ".init-onboarding" in app_css
    assert ".init-progress-fill" in app_css


def test_web_guided_init_polling_is_single_flight() -> None:
    """Runtime-stream events and timer fallback must not compound status polls."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "initPollInFlight" in setup_html
    assert "initPollPending" in setup_html
    assert "scheduleInitPoll(" in setup_html
    assert "initRefreshInFlight" in app_js
    assert "initRefreshPending" in app_js
    assert "scheduleInitStatusRefresh(" in app_js


def test_unknown_init_reasons_remain_diagnosable() -> None:
    """Frontend fallback should surface unknown backend reason codes."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "未知初始化状态" in setup_html
    assert "未知初始化状态" in app_js
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", setup_html)
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", app_js)


def test_setup_llm_model_is_visible_and_save_suppresses_background_llm_work() -> None:
    """Setup step 1 saves config only; model name is a normal required field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "高级（可选：自定义模型名）" not in setup_html
    assert '<label for="model">模型名</label>' in setup_html
    assert "suppress_background_llm_work: true" in setup_html


def test_setup_init_sources_are_explicit_opt_in_without_settings_enable_block() -> None:
    """Checked setup sources are this-run opt-ins, not a filter over settings toggles."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "勾选会同时开启该来源" in setup_html
    assert "selectedSourcesNeedingEnable" not in setup_html
    assert "还没在设置里开启" not in setup_html


def test_guided_init_web_docs_belong_to_v03110_release_block() -> None:
    """Do not retroactively claim already-released v0.3.109 shipped web Phase 2."""
    version_py = Path("src/openbiliclaw/__init__.py").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    gui_spec = Path("docs/specs/gui-init.md").read_text(encoding="utf-8")

    # Web Phase 2 shipped in v0.3.111 — the project version must never sit
    # below that (an exact pin here would break on every release bump).
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', version_py)
    assert match is not None
    assert tuple(int(part) for part in match.groups()) >= (0, 3, 111)
    top_block = changelog.split("## v0.3.109", 1)[0]
    assert "/setup/" in top_block
    assert "/web" in top_block
    assert "已落地 v0.3.111" in gui_spec
    assert "已落地 v0.3.109" not in gui_spec
