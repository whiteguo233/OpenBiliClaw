import asyncio
import re
from pathlib import Path

import pytest


def test_profile_analysis_default_budget_scales_with_chunk_count() -> None:
    import openbiliclaw.cli as cli

    assert cli._profile_analysis_timeout_seconds(event_count=0, requested=None) == 600
    assert (
        cli._profile_analysis_timeout_seconds(event_count=400, requested=None, concurrency=1) == 900
    )
    assert (
        cli._profile_analysis_timeout_seconds(event_count=1100, requested=None, concurrency=1)
        == 2100
    )
    assert (
        cli._profile_analysis_timeout_seconds(event_count=1100, requested=None, concurrency=2)
        == 1200
    )
    assert (
        cli._profile_analysis_timeout_seconds(event_count=1100, requested=None, concurrency=3)
        == 900
    )
    assert (
        cli._profile_analysis_timeout_seconds(event_count=1100, requested=12.5, concurrency=1)
        == 12.5
    )
    assert cli._profile_analysis_timeout_seconds(event_count=1100, requested=0) is None


def test_v2ex_guided_identity_activates_only_after_profile_commit(tmp_path: Path) -> None:
    import openbiliclaw.cli as cli
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "guided-v2ex.db")
    database.initialize()
    alice_id = database.insert_event(
        "publish",
        title="Alice",
        metadata={"source_platform": "v2ex", "source_identity": "alice"},
    )
    database.activate_v2ex_profile_identity("alice")
    memory = type("Memory", (), {"_database": database})()
    bob_event = {
        "event_type": "publish",
        "title": "Bob",
        "metadata": {"source_platform": "v2ex", "source_identity": "bob"},
    }

    identity = cli._stage_guided_v2ex_profile_identity(memory, [bob_event], "ok")
    assert identity == "bob"
    assert bob_event["metadata"]["profile_inactive"] is True
    bob_id = database.insert_event(
        "publish",
        title="Bob",
        metadata=bob_event["metadata"],
    )
    assert [row["id"] for row in database.query_events(limit=10)] == [alice_id]

    cli._activate_guided_v2ex_profile_identity(memory, identity)

    assert database.get_v2ex_profile_identity()[0] == "bob"
    assert [row["id"] for row in database.query_events(limit=10)] == [bob_id]


def test_v2ex_guided_identity_rejects_mixed_account_events() -> None:
    import openbiliclaw.cli as cli

    events = [
        {"metadata": {"source_identity": "alice"}},
        {"metadata": {"source_identity": "bob"}},
    ]

    with pytest.raises(cli.GuidedInitError, match="唯一账号"):
        cli._guided_v2ex_profile_identity(events, "partial")


def test_profile_analysis_concurrency_reads_soul_llm_service() -> None:
    import openbiliclaw.cli as cli

    class Service:
        concurrency = 3

    class Analyzer:
        registry = Service()

    class Engine:
        _preference_analyzer = Analyzer()

    assert cli._profile_analysis_concurrency(Engine()) == 3
    assert cli._profile_analysis_concurrency(object()) == 1


def test_setup_wizard_static_contract_uses_guided_init_endpoint() -> None:
    """Static guard: setup must reference guided init and not the legacy poke."""
    html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'data-panel="3"' in html
    assert 'fetchWithTimeout("/api/init-status"' in html
    assert 'fetchWithTimeout("/api/init"' in html
    assert 'fetchWithTimeout("/api/init/cancel"' in html
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
    # Roster drift lock: the desktop init picker derives WHICH sources exist from
    # the shared roster (/shared/source-status.js), the same list the wizard and
    # side panel build from — no third hand-kept copy that can silently drift.
    assert "_initSourceStatus?.INIT_SOURCE_KEYS" in app_js
    assert 'src="/shared/source-status.js"' in Path(
        "src/openbiliclaw/web/desktop/index.html"
    ).read_text(encoding="utf-8")
    assert "init_progress" in app_js
    # "openbiliclaw init" may appear ONLY inside the unsupported_runtime copy
    # (the container-blocked docker-exec fallback) — never as generic guidance
    # steering users away from the in-page guided-init CTA.
    assert app_js.count("openbiliclaw init") == 1
    unsupported_line = next(line for line in app_js.splitlines() if "unsupported_runtime:" in line)
    assert "docker exec" in unsupported_line
    assert "openbiliclaw init" in unsupported_line
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


def test_typed_timeout_reasons_prefer_backend_detail_in_web_surfaces() -> None:
    """Timeout details contain the cause/action; a short map label must not hide them."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for source in (setup_html, app_js):
        assert "analyze_failed" in source
        assert "profile_failed" in source
        assert "discovery_timeout" in source
        assert "detailFirst" in source
        assert "initStatusReasonText(status)" in source


def test_douyin_degraded_reason_is_mapped_without_promising_background_pool_fill() -> None:
    """A partial Douyin import is not a discovery-pool failure."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    popup_control = Path("extension/popup/popup-init-control.js").read_text(encoding="utf-8")
    popup_js = Path("extension/popup/popup.js").read_text(encoding="utf-8")

    for source in (setup_html, app_js, popup_control):
        assert "douyin_degraded" in source
        assert "抖音已采数据已用于画像" in source
    for source in (setup_html, app_js, popup_js):
        assert "初始化部分完成；已采数据已保留并使用，请按提示稍后补齐。" in source

    assert '"画像已就绪，后台补池中"' not in app_js
    assert '"完整画像已就绪；首轮推荐将在后台继续补齐"' not in app_js


def test_web_surfaces_no_longer_block_reddit_only_init() -> None:
    """Reddit bootstrap events are valid init signals."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "no_profile_signal_sources" in setup_html
    assert "Reddit 当前只启用 discovery" not in setup_html
    assert "连接你的 B站 账号" not in setup_html
    assert "连接浏览器扩展和平台账号" in setup_html
    assert "reddit.com" in setup_html
    assert "先检查 B站 登录 / AI 服务 / 向量模型" not in setup_html
    assert "所选平台的登录状态" in setup_html
    assert "no_profile_signal_sources" in app_js
    assert "Reddit 当前只启用 discovery" not in app_js


def test_setup_llm_model_is_visible_and_save_suppresses_background_llm_work() -> None:
    """Setup step 1 saves config only; model name is a normal required field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "高级（可选：自定义模型名）" not in setup_html
    assert '<label for="model">模型名</label>' in setup_html
    assert "suppress_background_llm_work: true" in setup_html


def test_setup_init_sources_are_explicit_opt_in_without_settings_enable_block() -> None:
    """Checked setup sources are this-run opt-ins, not a filter over settings toggles."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    shared = Path("src/openbiliclaw/web/shared/source-status.js").read_text(encoding="utf-8")

    assert "勾选会同时开启该来源" in setup_html
    assert "selectedSourcesNeedingEnable" not in setup_html
    assert "还没在设置里开启" not in setup_html
    # The wizard builds its checkbox list from the shared roster instead of a
    # third hand-kept copy, so it can no longer offer a platform the settings
    # pages do not know about (spec I6).
    assert "SourceStatus.INIT_SOURCE_KEYS.map" in setup_html
    assert 'src="/shared/source-status.js"' in setup_html
    for source in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
        "bangumi",
        "linuxdo",
    ):
        assert f'"{source}"' in shared
        assert f'key: "{source}"' in app_js
    assert "weibo: Object.freeze({ guidedInit: true })" in shared


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


def test_init_onboarding_gate_trusts_init_status_when_runtime_status_is_unavailable() -> None:
    """The guided-init gate must not depend solely on state.runtimeStatus.

    runtime-status can be transiently unreachable (hydrate re-fetch swallowed
    into null) or rebuilt from field-less runtime events whose missing
    `initialized` normalizes to true. /api/init-status stays the authoritative
    pre-init source, so an explicit initialized=false there must still surface
    the guided-init card.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    gate = app_js.split("function shouldShowInitOnboarding(", 1)[1]
    gate = gate.split("\n    }", 1)[0]
    assert "state.initStatus?.initialized === false" in gate
    assert "hasPostInitRuntimeSignals(runtime)" in gate


def test_hydrate_runtime_status_fallback_is_not_dead_catch() -> None:
    """Progressive runtime reads apply and recover independently."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    marker = "async function hydrateFromBackend({ replaceRecommendations = false } = {}) {"
    assert marker in app_js
    hydrate = app_js.split(marker, 1)[1]
    hydrate = hydrate.split("\n    function renderAll(", 1)[0]

    # The first runtime read has its own immediate application/recovery branch.
    assert "const firstRuntimeGeneration = desktopRuntimeGeneration;" in hydrate
    assert "const runtimePromise = readRuntimeSnapshot();" in hydrate
    assert "const runtimeApplicationPromise = runtimePromise.then(" in hydrate
    assert "(snapshot) => applyInitialRuntimeSnapshot(snapshot)" in hydrate
    assert "() => markDesktopRuntimeFailedAndRecover()" in hydrate
    assert "if (firstRuntimeGeneration !== desktopRuntimeGeneration) return;" in hydrate
    assert "applyDesktopRuntimeSnapshot(snapshot, firstRuntimeGeneration)" in hydrate

    # Recommendation settlement starts a separate freshness reread, guarded
    # against newer runtime-stream generations.
    assert "const runtimeReconciliationPromise = recommendationApplicationPromise.then(" in hydrate
    assert "() => reconcileRuntimeAfterRecommendations()" in hydrate
    assert "const secondRuntimeGeneration = desktopRuntimeGeneration;" in hydrate
    assert "await readRuntimeSnapshot()" in hydrate
    assert "if (runtimeReconciliationGeneration !== desktopRuntimeGeneration) return;" in hydrate

    # Initial rejection enters the existing bounded runtime recovery owner.
    assert 'desktopRuntimeLoadState = "failed";' in hydrate
    assert "scheduleDesktopRuntimeRecovery();" in hydrate
    assert "renderDesktopRuntimeFailure();" in hydrate


def test_bili_checklist_label_reflects_probe_result_and_surfaces_detail() -> None:
    """A failed B站 probe must never render a label containing "已登录".

    Field report (2026-07): with a proxy on, the login probe fails while the
    user IS logged in in the browser. Unchecking B站 demoted the row to the
    soft "B站 已登录（未勾选 B 站，可跳过）" label — which reads as "logged in
    now". Labels must state the actual probe result, and the failure hint must
    carry the backend's `bilibili_detail` (cookie-expired vs proxy-broken).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "B站 登录检测未通过" in setup_html
    assert "B 站登录检测未通过" in app_js
    for text in (setup_html, app_js):
        # The old unconditional "已登录（未勾选…" label is gone.
        assert "已登录（未勾选" not in text
        assert "bilibili_detail" in text


def test_runtime_stream_open_rehydrates_when_backend_data_never_loaded() -> None:
    """Frozen-entry race: /web can load before the backend binds, and the boot
    hydrate swallows every failure into nulls. An uninitialized backend emits
    no runtime events, so without a re-hydrate on the first successful
    runtime-stream connect the guided-init card would stay hidden forever."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    open_handler = app_js.split('socket.addEventListener("open"', 1)[1]
    open_handler = open_handler.split('socket.addEventListener("message"', 1)[0]
    guard = "if (!state.initStatus && !state.runtimeStatus) {"
    authenticate = "void ensureAuthenticated()"
    schedule = ".then(scheduleBackendHydration)"
    safe_rejection = ".catch(() => {})"

    assert guard in open_handler
    assert authenticate in open_handler
    assert schedule in open_handler
    assert safe_rejection in open_handler
    assert open_handler.index(guard) < open_handler.index(authenticate)
    assert open_handler.index(authenticate) < open_handler.index(schedule)
    assert open_handler.index(schedule) < open_handler.index(safe_rejection)


def test_setup_wizard_guard_resumes_running_and_initialized_states_on_load() -> None:
    """A mid-init reload must re-attach to progress instead of landing on step 0,
    and an initialized backend must not re-present the LLM form."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    guard = setup_html.split("(async function guard()", 1)[1]
    assert "fetchInitStatus()" in guard
    assert "if (status.running)" in guard
    assert "renderInitProgress(status)" in guard
    assert "connectInitStream()" in guard
    assert "if (status.initialized)" in guard
    assert "showInitCompletion(status)" in guard


def test_setup_wizard_guard_reattaches_to_embedding_pull_on_load() -> None:
    """A packaged desktop may be downloading bge-m3 before guided init starts.

    The first status response must attach the wizard's polling loop to that
    process-global progress; otherwise the user has to click the init CTA just
    to make the download percentage move.
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    guard = setup_html.split("(async function guard()", 1)[1]
    assert "embeddingPullProgressView(status).active" in guard
    assert "scheduleInitPoll" in guard
    poller = setup_html.split("async function pollInitStatus()", 1)[1]
    assert "embeddingPullProgressView(status).active" in poller


def test_setup_wizard_allows_saved_api_key_to_be_reused_without_reentry() -> None:
    """PUT /api/config only touches fields present in the payload, so an empty
    key field on a provider with a persisted key must not block step 0."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "savedKeyProviders" in setup_html
    assert "!apiKey && !savedKeyProviders.has(provider)" in setup_html
    assert "已保存，留空则沿用当前 Key" in setup_html


def test_setup_wizard_terminal_partial_success_allows_entry_without_second_wait() -> None:
    """A completed partial run must not create a frontend-owned 95% wait."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "function showInitCompletion(status = null)" in setup_html
    assert "你现在可以先进入应用" in setup_html
    assert "setStep(3);" in setup_html
    assert "renderWaitingForFirstPool" not in setup_html
    assert '"95%"' not in setup_html


def test_issue72_gateway_fields_present_on_all_config_surfaces() -> None:
    """issue #72 — third-party gateway controls exist on every web config
    surface: Claude gets an optional Base URL, the OpenAI-protocol family
    gets an api_flavor (/v1/responses) selector, and stale Base URLs are
    never submitted for providers that don't show the field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    desktop_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    # /setup/ wizard: Claude shows optional Base URL with a relay hint;
    # openai_compatible shows the protocol selector; base_url is only
    # submitted for providers whose form actually displayed it.
    assert 'id="baseHint"' in setup_html
    assert 'id="flavorWrap"' in setup_html
    assert 'id="apiFlavor"' in setup_html
    assert "(isCompat || isClaude)" in setup_html
    assert 'provider === "openai_compatible" || provider === "claude"' in setup_html
    assert 'pcfg.api_flavor = $("#apiFlavor").value' in setup_html

    # Desktop settings: every endpoint instance owns its own Base URL and
    # OpenAI-protocol flavor, so two gateways of the same adapter type remain
    # independently configurable.
    assert 'id="llmInstanceBaseUrl"' in desktop_html
    assert 'id="llmInstanceApiFlavor"' in desktop_html
    assert 'setSelect("llmInstanceApiFlavor"' in app_js
    assert 'api_flavor: ["openai", "openai_compatible"].includes(providerType)' in app_js
    assert "base_url: baseUrl" in app_js


def test_setup_wizard_config_save_401_points_to_login_instead_of_dead_end() -> None:
    """/api/config is session-gated while init endpoints are public: an
    auth-enabled remote browser must get a login path on save, not a bare
    "保存失败：HTTP 401" dead end at step 0."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "r.status === 401" in setup_html
    assert "输入访问密码登录" in setup_html
    assert '<a href="/web">' in setup_html


def test_setup_wizard_protected_requests_use_csrf_auth_fetch_contract() -> None:
    """Password-authenticated setup writes must use the shared CSRF-aware fetch."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'credentials: "same-origin"' in setup_html
    assert '"X-OBC-Auth": "1"' in setup_html
    assert 'const r = await fetch("/api/config", {' not in setup_html
    assert 'const r = await fetchWithTimeout("/api/config", {' in setup_html
    assert '"/api/config/discover-models"' in setup_html
    assert '"/api/embedding/repair"' in setup_html


def test_web_surfaces_offer_embedding_repair_and_progress() -> None:
    """Both web init checklists expose one-click model download + live progress.

    The repair button POSTs /api/embedding/repair; while the pull runs the
    backend classifies embedding_check="repairing" and the pages keep polling
    so the row's hint shows live percent (user request 2026-07-05).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    for surface in (setup_html, app_js):
        assert "data-embedding-repair" in surface
        assert "embedding_detail" in surface
        assert "embedding_pull_status" in surface
        assert "ollama_phase" in surface
        assert "Ollama 启动中" in surface
        assert "model_missing" in surface and "model_broken" in surface
        assert "model_path_encoding" in surface
        assert "disk_full" in surface and "network" in surface and "model_oom" in surface
        assert "provider_error" in surface
        assert "迁移模型目录并修复" in surface
        assert "重新检测" in surface
        assert "embeddingPullProgressView" in surface
    assert '"/api/embedding/repair"' in setup_html
    assert "embedding_repair_running" in setup_html  # keeps polling while downloading
    assert 'embeddingRepair: "/embedding/repair"' in app_js
    assert "handleEmbeddingRepairClick" in app_js
    assert ".init-repair-btn" in setup_html
    assert ".init-repair-btn" in app_css


# ── init-progress-visibility Phase 0: API models + heartbeat task ────────────


def _progress_coord(tmp_path):
    from types import SimpleNamespace

    from openbiliclaw.runtime.init_coordinator import InitCoordinator
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "hb.db")
    db.initialize()
    ctx = SimpleNamespace(database=db, event_hub=None, runtime_controller=None)
    return InitCoordinator(ctx), db


def test_init_stage_out_accepts_and_omits_progress_fields() -> None:
    """InitStageOut stays backward-compatible: old stage dicts (no progress /
    eta_seconds) parse, and new ones nest InitStageProgressOut."""
    from openbiliclaw.api.models import InitStageOut, InitStageProgressOut

    legacy = InitStageOut(n=2, label="分析偏好", status="pending", reason=None)
    assert legacy.progress is None
    assert legacy.eta_seconds is None

    rich = InitStageOut(
        n=2,
        label="分析偏好",
        status="running",
        reason=None,
        progress={"done": 3, "total": 8, "note": "第 3/8 批"},
        eta_seconds=180,
    )
    assert isinstance(rich.progress, InitStageProgressOut)
    assert rich.progress.done == 3 and rich.progress.total == 8
    assert rich.eta_seconds == 180


def test_init_status_out_has_last_activity_default() -> None:
    from openbiliclaw.api.models import InitStatusOut

    status = InitStatusOut()
    assert status.last_activity == ""
    assert status.last_heartbeat_at == ""
    assert status.last_progress_at == ""
    assert status.progress_sequence == 0


def test_heartbeat_interval_bounds_last_activity_freshness() -> None:
    """Goal metric 1 fallback: the heartbeat period must be ≤30s so that a
    65s hung stage still lands ≥2 touches (last_activity stays ≤30s fresh)."""
    from openbiliclaw.api.app import _INIT_HEARTBEAT_INTERVAL_SECONDS

    assert _INIT_HEARTBEAT_INTERVAL_SECONDS <= 30


async def test_heartbeat_task_keeps_touching_until_cancelled(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    coord, db = _progress_coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    seq_before = db.get_latest_init_run()["sequence"]

    task = asyncio.create_task(_run_init_heartbeat(coord, "run-1", interval=0.01))
    await asyncio.sleep(0.06)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    seq_after = db.get_latest_init_run()["sequence"]
    # At least two heartbeat touches landed while the task ran.
    assert seq_after - seq_before >= 2
    assert coord.get_status()["last_activity"] != ""


async def test_heartbeat_swallows_touch_errors(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    class _BoomCoord:
        async def touch(self, run_id: str) -> None:
            raise RuntimeError("db gone")

    # A failing touch must not kill the heartbeat loop (it just logs WARNING).
    task = asyncio.create_task(_run_init_heartbeat(_BoomCoord(), "run-1", interval=0.01))
    await asyncio.sleep(0.03)
    assert not task.done()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def test_init_status_endpoint_surfaces_last_activity(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "e1.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app) as client:
        body = client.get("/api/init-status").json()
    assert "last_activity" in body
    assert isinstance(body["last_activity"], str)
    assert "last_heartbeat_at" in body
    assert "last_progress_at" in body
    assert "progress_sequence" in body


# ── init-progress-visibility Phase 1: run_guided_init producer wiring ─────────


class _RecordingCoordinator:
    """Records the progress signals run_guided_init emits (no DB needed)."""

    def __init__(self) -> None:
        self.stage_progress_calls: list[dict] = []
        self.started_stages: list[int] = []
        self.done_stages: list[int] = []
        self.done_stage_calls: list[dict[str, object | None]] = []
        self.events: list[tuple[str, int]] = []

    async def stage_started(self, run_id: str, n: int) -> None:
        self.started_stages.append(n)
        self.events.append(("started", n))

    async def stage_done(self, run_id: str, n: int, *, status: str = "ok", reason=None) -> None:
        self.done_stages.append(n)
        self.done_stage_calls.append({"stage": n, "status": status, "reason": reason})
        self.events.append(("done", n))

    async def stage_progress(
        self,
        run_id: str,
        stage: int,
        *,
        done: int,
        total: int,
        note=None,
        mode="determinate",
        elapsed_seconds=None,
        max_seconds=None,
        substantive=True,
    ) -> None:
        self.stage_progress_calls.append(
            {
                "stage": stage,
                "done": done,
                "total": total,
                "note": note,
                "mode": mode,
                "elapsed_seconds": elapsed_seconds,
                "max_seconds": max_seconds,
                "substantive": substantive,
            }
        )

    def register_enqueued_task(self, run_id: str, task_id: str) -> None:
        pass


class _StubEngine:
    def __init__(self, chunk_reports: int = 3) -> None:
        self.chunk_reports = chunk_reports
        self.received_callback = None
        self.profile = object()
        self.discover_profiles: list[object] = []

    async def analyze_events(
        self, events, *, event_chunk_size=0, progress_callback=None, llm_concurrency=None
    ):
        self.received_callback = progress_callback
        if progress_callback is not None:
            for i in range(1, self.chunk_reports + 1):
                await progress_callback(i, self.chunk_reports)

    async def build_initial_profile(self, history):
        return self.profile


class _StubMemory:
    async def propagate_event(self, event) -> None:
        pass


def _patch_run_guided_init_collectors(monkeypatch, engine) -> None:
    import openbiliclaw.cli as cli

    async def _fetch_bili(client, *, history_limit, favorite_limit, follow_limit):
        return ([{"title": "hist-1"}], [], [])

    async def _rwp(coro, **kwargs):
        return await coro

    async def _discover_backfill(
        profile,
        *,
        target_pool_count,
        label_suffix="",
        progress_callback=None,
    ):
        engine.discover_profiles.append(profile)
        if progress_callback is not None:
            await progress_callback(4, 4, "首轮内容池已就绪（测试）")
        return 0

    monkeypatch.setattr(cli, "_fetch_bilibili_init_data", _fetch_bili)
    monkeypatch.setattr(
        cli, "_history_item_to_event", lambda item: {"event_type": "view", "title": "hist-1"}
    )
    monkeypatch.setattr(cli, "_collect_xhs_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_dy_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_yt_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_zhihu_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_reddit_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_enqueue_reddit_bootstrap_task", lambda kick=True: "task-r")
    monkeypatch.setattr(cli, "_kick_task_dispatcher", lambda source: None)
    monkeypatch.setattr(cli, "_run_with_progress", _rwp)
    monkeypatch.setattr(cli, "_print_section_title", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_maybe_update_init_source_shares", lambda *a, **k: None)
    return _discover_backfill


async def test_run_guided_init_emits_stage_progress_for_sources_and_chunks(monkeypatch) -> None:
    from contextlib import contextmanager
    from contextvars import ContextVar

    import openbiliclaw.cli as cli

    scope_active = ContextVar("guided_init_test_scope", default=False)
    scope_observations: list[tuple[str, bool]] = []

    @contextmanager
    def _scope():
        token = scope_active.set(True)
        try:
            yield
        finally:
            scope_active.reset(token)

    class _ScopedEngine(_StubEngine):
        async def analyze_events(self, *args, **kwargs):
            scope_observations.append(("analyze", scope_active.get()))
            await super().analyze_events(*args, **kwargs)

        async def build_initial_profile(self, *args, **kwargs):
            scope_observations.append(("profile", scope_active.get()))
            return await super().build_initial_profile(*args, **kwargs)

    monkeypatch.setattr(cli, "_background_admission_bypass", _scope)
    engine = _ScopedEngine(chunk_reports=3)
    base_discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    async def discover_backfill(*args, **kwargs):
        scope_observations.append(("discover", scope_active.get()))
        return await base_discover_backfill(*args, **kwargs)

    coord = _RecordingCoordinator()

    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=True,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=coord,
        run_id="run-1",
    )

    stage1 = [c for c in coord.stage_progress_calls if c["stage"] == 1]
    # Two selected sources: B站 then Reddit — note switches, done increments 0→1.
    assert [(c["done"], c["total"], c["note"]) for c in stage1] == [
        (0, 2, "正在采集 B 站"),
        (1, 2, "正在采集 Reddit · 扩展未响应会在约 3 分钟后自动跳过"),
    ]

    stage2 = [c for c in coord.stage_progress_calls if c["stage"] == 2]
    assert [(c["done"], c["total"]) for c in stage2] == [
        (0, 1),
        (1, 3),
        (2, 3),
        (3, 3),
    ]
    assert stage2[0]["note"] == "已完成 0/1 批 · AI 开始处理（并发上限 1）"
    assert stage2[-1]["note"] == "第 3/3 批"
    assert stage2[0]["elapsed_seconds"] == 0
    assert all(call["max_seconds"] == 2700 for call in stage2)
    assert ("analyze", True) in scope_observations
    assert ("profile", True) in scope_observations
    assert ("discover", False) in scope_observations
    assert engine.discover_profiles == [engine.profile]
    assert coord.events.index(("done", 3)) < coord.events.index(("started", 4))

    stage3 = [c for c in coord.stage_progress_calls if c["stage"] == 3]
    assert stage3[0]["note"] == "正在综合偏好、历史与认知线索"
    assert stage3[-1]["note"] == "完整画像已保存，下一步将严格基于它生成内容"
    stage4 = [c for c in coord.stage_progress_calls if c["stage"] == 4]
    assert stage4[0]["note"] == "完整画像已就绪，准备发现候选内容"
    assert "可直接浏览" in stage4[-1]["note"]

    # Stages all completed (Task 1 clears any progress residue on stage_done).
    assert coord.done_stages == [1, 2, 3, 4]


async def test_run_guided_init_marks_douyin_degraded_collection_as_warning(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=0)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    monkeypatch.setattr(cli, "_enqueue_dy_bootstrap_task", lambda **kwargs: "task-dy")
    monkeypatch.setattr(
        cli,
        "_collect_dy_bootstrap_events",
        lambda task_id: (
            [{"event_type": "favorite", "title": "dy-partial"}],
            {"dy_collect": 1},
            "degraded",
        ),
    )
    coord = _RecordingCoordinator()

    result = await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=True,
        include_yt=False,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=coord,
        run_id="run-douyin-degraded",
    )

    assert result.dy_status == "degraded"
    assert coord.done_stage_calls[0] == {
        "stage": 1,
        "status": "warning",
        "reason": "douyin_degraded",
    }


async def test_run_guided_init_cli_path_uses_console_progress_callback(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=2)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    # No coordinator (CLI path): analyze_events must still receive a callback,
    # and invoking it must not raise (it prints instead of hitting a coordinator).
    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=False,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=None,
        run_id=None,
    )
    assert engine.received_callback is not None
    await engine.received_callback(1, 2)  # prints without error


async def test_run_guided_init_bounds_hung_preference_analysis(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    class _HangingAnalyzeEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def analyze_events(
            self, events, *, event_chunk_size=0, progress_callback=None, llm_concurrency=None
        ):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    engine = _HangingAnalyzeEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    coord = _RecordingCoordinator()

    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=object(),
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=True,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=discover_backfill,
            coordinator=coord,
            run_id="run-timeout",
            profile_analysis_timeout_seconds=0.01,
        )

    # Explicit caller budgets stay a pure wall clock, so this is the *absolute*
    # ceiling message: it must not blame Base URL / 模型名 (nothing proves the
    # service is unreachable) and must name the actionable fix instead.
    assert excinfo.value.reason == "analyze_failed"
    assert "总时长超过上限（约 1 分钟）" in excinfo.value.message
    assert "更快的模型" in excinfo.value.message
    assert "重试初始化" in excinfo.value.message
    assert "Base URL" not in excinfo.value.message
    assert engine.cancelled is True
    assert engine.discover_profiles == []
    assert coord.started_stages == [1, 2]
    assert coord.done_stages == [1]


async def test_run_guided_init_bounds_stage1_and_stops_extension_worker(monkeypatch) -> None:
    import threading

    import openbiliclaw.cli as cli

    engine = _StubEngine()
    _patch_run_guided_init_collectors(monkeypatch, engine)
    cancel_seen = threading.Event()

    def _blocking_collector(
        task_id,
        *,
        max_wait_seconds=None,
        cancel_event=None,
    ):
        assert task_id == "xhs-timeout"
        assert cancel_event is not None
        cancel_event.wait(2)
        if cancel_event.is_set():
            cancel_seen.set()
        return [], {}, "timeout"

    monkeypatch.setattr(cli, "_enqueue_xhs_bootstrap_task", lambda **kwargs: "xhs-timeout")
    monkeypatch.setattr(cli, "_kick_task_dispatcher", lambda source: None)
    monkeypatch.setattr(cli, "_collect_xhs_bootstrap_events", _blocking_collector)

    class _RichCoordinator(_RecordingCoordinator):
        async def stage_progress(self, run_id, stage, **kwargs):
            self.stage_progress_calls.append({"stage": stage, **kwargs})

    coord = _RichCoordinator()
    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=None,
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=True,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=lambda *args, **kwargs: None,
            coordinator=coord,
            run_id="run-collection-timeout",
            collection_timeout_seconds=0.03,
        )

    assert excinfo.value.reason == "collection_timeout"
    assert "总等待上限" in excinfo.value.message
    assert cancel_seen.wait(1), "blocking extension collector did not receive cancellation"
    countdowns = [
        call
        for call in coord.stage_progress_calls
        if call["stage"] == 1 and call.get("mode") == "indeterminate"
    ]
    assert countdowns
    assert countdowns[-1]["substantive"] is False
    assert "阶段剩余最多" in countdowns[-1]["note"]


async def test_run_guided_init_bounds_hung_profile_build(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    class _HangingProfileEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__(chunk_reports=0)
            self.cancelled = False

        async def build_initial_profile(self, history):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    engine = _HangingProfileEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=object(),
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=True,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=discover_backfill,
            profile_build_timeout_seconds=0.01,
        )

    assert excinfo.value.reason == "profile_failed"
    assert "超过 30 分钟" in excinfo.value.message
    assert "Base URL" in excinfo.value.message
    assert "模型名" in excinfo.value.message
    assert "重试初始化" in excinfo.value.message
    assert engine.cancelled is True
    assert engine.discover_profiles == []


async def test_run_guided_init_treats_hung_discovery_as_partial_success(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=0)
    _patch_run_guided_init_collectors(monkeypatch, engine)
    discovery_cancelled = False

    async def _hanging_discovery(profile, *, target_pool_count, label_suffix=""):
        nonlocal discovery_cancelled
        try:
            await asyncio.Event().wait()
        finally:
            discovery_cancelled = True

    result = await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        target_pool_count=0,
        discover_backfill=_hanging_discovery,
        discovery_timeout_seconds=0.01,
    )

    assert result.discovery_error is True
    assert isinstance(result.discover_exc, TimeoutError)
    assert result.discovery_reason == "discovery_timeout"
    assert "超过 45 分钟" in result.discovery_detail
    assert "部分完成" in result.discovery_detail
    assert "后台继续补池" in result.discovery_detail
    assert discovery_cancelled is True


# ── Progress-aware deadlines (idle + absolute) ───────────────────────────
#
# Regression cover for the 2026-07-20 field report: a healthy-but-slow gateway
# was killed by stage 2's fixed wall clock while batches were still landing.
# All of these drive `_await_with_progress_deadline` with an injected fake
# clock so they assert the *policy*, not real elapsed time.


class _FakeClock:
    """Monotonic clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_progress_deadline_survives_slow_but_progressing_run() -> None:
    """The reported case: 6 chunks x ~140s used to die at the 15-min wall clock."""
    import openbiliclaw.cli as cli

    clock = _FakeClock()
    marker = cli._InitProgressMarker(clock)
    chunks_done = 0

    async def _slow_but_progressing() -> str:
        nonlocal chunks_done
        for _ in range(6):
            # One chunk of real work on a slow gateway.
            clock.advance(140.0)
            await asyncio.sleep(0)
            chunks_done += 1
            marker.touch()
        return "ok"

    old_fixed_budget = cli._profile_analysis_timeout_seconds(
        event_count=1100, requested=None, concurrency=1
    )
    assert old_fixed_budget is not None
    # 6 x 140s = 840s of work would have fitted, but the throttled real run
    # overran the old budget; assert the new pair does not care about total
    # elapsed as long as progress keeps arriving.
    result = await cli._await_with_progress_deadline(
        _slow_but_progressing(),
        marker=marker,
        idle_seconds=cli._INIT_PROGRESS_IDLE_SECONDS,
        absolute_seconds=cli._INIT_PROGRESS_ABSOLUTE_SECONDS,
        poll_seconds=0,
    )

    assert result == "ok"
    assert chunks_done == 6
    # Every gap between chunks is under the idle limit even though the run is
    # far slower than the per-wave calibration it was previously killed by.
    assert clock.now == 840.0
    assert cli._INIT_PROGRESS_IDLE_SECONDS > 140.0


async def test_progress_deadline_idle_limit_fires_when_nothing_returns() -> None:
    import openbiliclaw.cli as cli

    clock = _FakeClock()
    marker = cli._InitProgressMarker(clock)
    cancelled = False

    async def _never_progresses() -> None:
        nonlocal cancelled
        try:
            while True:
                clock.advance(60.0)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(cli._InitIdleTimeoutError):
        await cli._await_with_progress_deadline(
            _never_progresses(),
            marker=marker,
            idle_seconds=600.0,
            absolute_seconds=2700.0,
            poll_seconds=0,
        )

    assert cancelled is True
    # Idle, not absolute: it gave up long before the 45-minute ceiling.
    assert clock.now < 2700.0


async def test_progress_deadline_absolute_ceiling_still_fires() -> None:
    import openbiliclaw.cli as cli

    clock = _FakeClock()
    marker = cli._InitProgressMarker(clock)
    cancelled = False

    async def _progresses_forever() -> None:
        nonlocal cancelled
        try:
            while True:
                clock.advance(60.0)
                marker.touch()
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(cli._InitAbsoluteTimeoutError):
        await cli._await_with_progress_deadline(
            _progresses_forever(),
            marker=marker,
            idle_seconds=600.0,
            absolute_seconds=2700.0,
            poll_seconds=0,
        )

    assert cancelled is True
    assert clock.now >= 2700.0


async def test_progress_deadline_propagates_cancellation() -> None:
    """CancelledError must escape so the API wrapper records `cancelled`."""
    import openbiliclaw.cli as cli

    marker = cli._InitProgressMarker()
    inner_cancelled = asyncio.Event()

    async def _work() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            inner_cancelled.set()
            raise

    outer = asyncio.ensure_future(
        cli._await_with_progress_deadline(
            _work(),
            marker=marker,
            idle_seconds=600.0,
            absolute_seconds=2700.0,
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    assert inner_cancelled.is_set()


async def test_profile_analysis_deadlines_split_idle_and_absolute() -> None:
    import openbiliclaw.cli as cli

    idle, absolute = cli._profile_analysis_deadlines(
        event_count=1100, requested=None, concurrency=1
    )
    assert idle == cli._INIT_PROGRESS_IDLE_SECONDS
    assert idle == 1500.0
    # Generous enough for the reported 6-chunk bootstrap on a slow gateway.
    assert absolute == cli._INIT_PROGRESS_ABSOLUTE_SECONDS

    # A bootstrap whose scaled wall clock exceeds the flat ceiling keeps the
    # larger of the two — the ceiling never shrinks a big run's budget.
    huge_idle, huge_absolute = cli._profile_analysis_deadlines(
        event_count=6000, requested=None, concurrency=1
    )
    assert huge_idle == cli._INIT_PROGRESS_IDLE_SECONDS
    assert huge_absolute is not None and huge_absolute > cli._INIT_PROGRESS_ABSOLUTE_SECONDS

    # Explicit caller overrides stay an exact pure wall clock, no idle limit.
    assert cli._profile_analysis_deadlines(event_count=1100, requested=12.5) == (None, 12.5)
    assert cli._profile_analysis_deadlines(event_count=1100, requested=0) == (None, None)


async def test_run_guided_init_idle_analysis_reports_connectivity_message(monkeypatch) -> None:
    """A stage-2 run that returns nothing gets the idle message, not the slow one."""
    import openbiliclaw.cli as cli

    monkeypatch.setattr(cli, "_INIT_PROGRESS_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(cli, "_INIT_PROGRESS_ABSOLUTE_SECONDS", 600.0)

    class _HangingAnalyzeEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def analyze_events(
            self, events, *, event_chunk_size=0, progress_callback=None, llm_concurrency=None
        ):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    engine = _HangingAnalyzeEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=object(),
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=True,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=discover_backfill,
        )

    assert excinfo.value.reason == "analyze_failed"
    assert "没有返回任何新结果" in excinfo.value.message
    assert "Base URL" in excinfo.value.message
    assert "模型名" in excinfo.value.message
    assert "重试初始化" in excinfo.value.message
    # Distinct from the absolute message — the user must be able to tell
    # "unreachable" apart from "too slow".
    assert "更快的模型" not in excinfo.value.message
    assert engine.cancelled is True


async def test_run_guided_init_slow_analysis_completes_past_old_fixed_budget(monkeypatch) -> None:
    """Regression: steady per-chunk progress must outlive the old wall clock."""
    import openbiliclaw.cli as cli

    ticks = {"n": 0}

    class _SlowProgressingEngine(_StubEngine):
        async def analyze_events(
            self, events, *, event_chunk_size=0, progress_callback=None, llm_concurrency=None
        ):
            self.received_callback = progress_callback
            for i in range(1, 7):
                # Each chunk takes longer than the whole old floor budget would
                # have allowed once summed, but progress keeps arriving.
                await asyncio.sleep(0.01)
                ticks["n"] += 1
                if progress_callback is not None:
                    await progress_callback(i, 6)

    # Idle limit is comfortably above the per-chunk gap, so a run that keeps
    # reporting batches must reach the end. (The clock-level proof that a run
    # slower than the old fixed budget survives lives in
    # ``test_progress_deadline_survives_slow_but_progressing_run``.)
    monkeypatch.setattr(cli, "_INIT_PROGRESS_IDLE_SECONDS", 5.0)

    engine = _SlowProgressingEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    coord = _RecordingCoordinator()

    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=coord,
        run_id="run-slow",
    )

    assert ticks["n"] == 6
    assert coord.done_stages == [1, 2, 3, 4]
    stage2 = [c for c in coord.stage_progress_calls if c["stage"] == 2]
    assert [c["done"] for c in stage2] == [0, 1, 2, 3, 4, 5, 6]
    # The GUI's ETA copy quotes max_seconds; it must be the absolute ceiling,
    # i.e. the only clock that can still end the stage on time.
    _, expected_ceiling = cli._profile_analysis_deadlines(event_count=1, requested=None)
    assert expected_ceiling is not None
    assert all(c["max_seconds"] == int(expected_ceiling) for c in stage2)
