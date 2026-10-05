// Guided-init control logic for the recommend tab (gui-init F1).
//
// Pure, DOM-agnostic helpers driven by GET /api/init-status (shape:
// initialized / running / current_stage / total_stages / stages[] /
// partial_success / can_start / can_manage / prerequisites / reason).
// popup.js renders these; tests exercise them directly (init-control.test.ts).

const STAGE_TOTAL_FALLBACK = 4;

const REASON_TEXT = {
  unsupported_runtime: "当前运行环境（例如 Docker）不支持图形化初始化，请用命令行 openbiliclaw init。",
  already_running: "初始化正在进行中。",
  bilibili_not_logged_in: "还没检测到 B 站登录。",
  llm_not_ready: "AI 服务还没配好或当前不可用。",
  // v0.3.137+ 配置了 embedding provider 时向量模型是服务端硬前置；此前
  // popup 独缺这条映射（desktop / setup 都有），用户只能看到与清单矛盾的
  // 通用「以下条件未满足」（field report 2026-07-05, machine B）。
  embedding_not_ready: "向量模型还没就绪，请等待 bge-m3 下载完成或修复 Ollama 后重试。",
  already_initialized: "已经初始化过了；如需重建，请到设置页。",
  local_only: "只能在本机发起初始化。",
  no_sources_selected: "至少勾选一个数据来源。",
  no_profile_signal_sources:
    "所选来源暂时无法提供画像信号，请检查对应账号、凭据及扩展连接后重试。",
  invalid_bangumi_access_token: "Bangumi 个人令牌被拒绝（缺失、错误或已过期）。请到 next.bgm.tv/demo/access-token 重新生成后重试。",
  bangumi_token_check_failed: "校验 Bangumi 令牌时无法连接 Bangumi，请稍后重试。",
  analyze_failed: "偏好分析未完成。",
  profile_failed: "画像生成未完成。",
  discovery_timeout: "画像已生成，但首轮内容池整理超时。",
  discovery_partial: "画像已生成，但首轮内容池本次未完成。",
  douyin_degraded: "抖音已采数据已用于画像，但至少一个账号范围的分页未完整完成。",
  internal_error: "初始化过程中出错了，请稍后重试。",
  interrupted: "上次初始化被打断（后端重启），可重试。",
  cancelled: "初始化已取消。",
  collection_timeout: "数据采集达到总等待上限，已停止继续等待平台或扩展。",
  none: "",
};

// Human text for a backend reason / error code. Unknown codes return "".
export function describeInitReason(reason) {
  if (!reason) {
    return "";
  }
  return REASON_TEXT[reason] || "";
}

// Authoritative status explanation for pre-init and partial-success states.
// Typed backend details carry the concrete cause + recovery action and should
// win over a short reason-code label. account-sync keeps llm_not_ready while
// its live probe is red, so recognise its prefixed analysis detail as well.
export function describeInitStatusReason(status) {
  const reason = String((status && status.reason) || "");
  const detail = String((status && status.detail) || "").trim();
  const detailFirst = [
    "analyze_failed",
    "profile_failed",
    "discovery_timeout",
    "discovery_partial",
    "douyin_degraded",
  ];
  if (detail && (detailFirst.includes(reason) || detail.startsWith("画像分析失败："))) {
    return detail;
  }
  return describeInitReason(reason) || detail;
}

// Human text for a failed/cancelled run. ``status.detail`` carries the
// backend's stored failure specifics (exception summary / GuidedInitError
// message, v0.3.156+) — append it so an internal_error is diagnosable from
// the UI instead of only the generic "请稍后重试" (field report 2026-07-05).
export function describeInitFailure(status, progress = null) {
  const base = describeInitReason(status && status.reason) || "";
  const detail = String((status && status.detail) || "").trim();
  const reason = String((status && status.reason) || "");
  if (
    detail &&
    ([
      "analyze_failed",
      "profile_failed",
      "discovery_timeout",
      "discovery_partial",
      "douyin_degraded",
    ].includes(reason) || detail.startsWith("画像分析失败："))
  ) {
    return detail;
  }
  if (base && detail) {
    return `${base}（${detail}）`;
  }
  return base || detail || (progress && progress.failedReason) || "请稍后重试";
}

// Classified embedding-not-ready causes (init-status prerequisites
// ``embedding_check``, v0.3.155+). The backend's ``embedding_detail``
// wins when present; these are fallbacks for older backends.
const EMBEDDING_CHECK_TEXT = {
  repairing: "正在下载向量模型，完成后自动就绪。",
  not_running: "Ollama 没有在运行。启动 Ollama（或运行 `ollama serve`）后再试。",
  model_missing:
    "Ollama 已在运行，但缺 bge-m3 模型——推荐页横幅可一键拉取，或手动 `ollama pull bge-m3`。",
  model_broken:
    "bge-m3 已安装但调用持续失败——建议重新拉取（`ollama pull bge-m3`）或重启 Ollama。",
  model_path_encoding:
    "bge-m3 已安装，但模型路径含非 ASCII 字符。请设置 OLLAMA_MODELS 为纯英文路径后重启 Ollama 并重新拉取。",
  misconfigured: "embedding 配置无效，请到设置页重新选择 provider 并保存。",
  provider_error: "embedding 服务探测失败，请检查 provider 的 Key / 地址 / 网络。",
};

// Actionable hint for the embedding checklist row / banner. "" when ready.
export function describeEmbeddingHint(prereq) {
  if (!prereq || prereq.embedding_ready) {
    return "";
  }
  const detail =
    typeof prereq.embedding_detail === "string" ? prereq.embedding_detail.trim() : "";
  if (detail) {
    return detail;
  }
  const byCode = EMBEDDING_CHECK_TEXT[prereq.embedding_check];
  if (byCode) {
    return byCode;
  }
  // Required (provider configured) blocks init server-side — the legacy
  // "也能初始化" copy would contradict the blocked start button.
  return prereq.embedding_required
    ? "本地 Ollama + bge-m3 需要完成一次真实向量请求；模型仍在下载或服务异常时请稍后重试。"
    : "本地 Ollama + bge-m3 没就绪也能初始化，但语义检索会弱一些。";
}

// Live bge-m3 pull progress from init-status prerequisites. Returns
// {active, pct, label}; pct remains between 1 and 99 while a repair is active.
export function embeddingPullProgressView(prereq) {
  const p = prereq || {};
  const active =
    Boolean(p.embedding_repair_running) || p.embedding_check === "repairing";
  const completed = Number(p.embedding_repair_completed || 0);
  const total = Number(p.embedding_repair_total || 0);
  const pct =
    total > 0
      ? Math.max(1, Math.min(99, Math.round((completed * 100) / total)))
      : active
        ? 1
        : 0;
  const phase = p.ollama_phase === "starting" ? "Ollama 启动中…" : "";
  const status = String(p.embedding_pull_status || "").trim();
  const label =
    [phase, status].filter(Boolean).join(" ") ||
    (active ? "正在下载向量模型…" : "");
  return { active, pct, label };
}

// Select the repair action exposed beside an unavailable embedding model.
export function embeddingRepairAction(prereq) {
  const p = prereq || {};
  if (p.embedding_ready) {
    return { repairable: false, label: "" };
  }
  const check = String(p.embedding_check || "");
  if (check === "model_path_encoding") {
    return { repairable: true, label: "迁移模型目录并修复" };
  }
  if (check === "model_missing" || check === "model_broken") {
    return { repairable: true, label: "自动下载向量模型" };
  }
  if (["disk_full", "network", "model_oom", "provider_error"].includes(check)) {
    return { repairable: true, label: "重新检测" };
  }
  return { repairable: false, label: "" };
}

// Only successful starts (or an already-running single-flight repair) should
// enter the long init-status polling loop.
export function embeddingRepairStartAccepted(result) {
  const status = Number((result && result.status) || 0);
  return (
    status === 200 ||
    status === 202 ||
    (status === 409 && result && result.error === "already_running")
  );
}

// Pre-init checklist rows. ``hard`` rows must be satisfied before init can
// start; soft rows (embedding) only warn. Each row carries a fix-it hint.
// ``selected`` is the current source-checkbox selection: B 站登录 is a hard
// prerequisite only while bilibili is among the checked sources (v0.3.118+);
// null (legacy callers) keeps it hard.
export function buildInitChecklist(status, selected = null) {
  const prereq = (status && status.prerequisites) || {};
  const enabled = getEnabledPlatforms(status);
  const selectedSources = Array.isArray(selected) ? selected : null;
  const biliSelected = selectedSources ? selectedSources.includes("bilibili") : true;
  return [
    {
      key: "bilibili",
      label: biliSelected ? "B 站已登录" : "B 站已登录（未勾选 B 站，可跳过）",
      ok: Boolean(prereq.bilibili_logged_in),
      hard: biliSelected,
      hint: prereq.bilibili_logged_in
        ? ""
        : "在浏览器里登录 bilibili.com，扩展会自动把 Cookie 同步给后端；不想接 B 站也可以直接取消勾选。",
    },
    {
      key: "llm",
      label: "AI 服务可用",
      ok: Boolean(prereq.llm_ready),
      hard: true,
      hint: prereq.llm_ready
        ? ""
        : "AI 服务没通过实时请求测试 —— 到设置页填好 LLM provider 的 API Key,或确认服务可达。",
    },
    {
      key: "embedding",
      // v0.3.137+ a configured embedding provider is a HARD server-side init
      // gate (can_start waits for embedding_ready) — mirroring setup / desktop
      // web. A fixed "非必须" label here contradicted the blocked start.
      label: prereq.embedding_required ? "向量模型可用" : "向量模型可用（推荐，非必须）",
      ok: Boolean(prereq.embedding_ready),
      hard: Boolean(prereq.embedding_required),
      hint: describeEmbeddingHint(prereq),
      pull: embeddingPullProgressView(prereq),
      repair: embeddingRepairAction(prereq),
    },
    {
      key: "platforms",
      label: selectedSources?.length
        ? `本次初始化来源：${initSourceLabels(selectedSources).join("、")}`
        : enabled.length
          ? `数据来源：${initSourceLabels(enabled).join("、")}`
          : "数据来源：仅 B 站（可在设置里开启更多平台）",
      ok: Boolean(selectedSources?.length || enabled.length),
      hard: false,
      hint:
        selectedSources?.length || enabled.length > 0
          ? ""
          : "默认只接入 B 站；想纳入小红书 / 抖音 / YouTube，先到设置页开启对应平台。",
    },
  ];
}

export function getEnabledPlatforms(status) {
  const prereq = (status && status.prerequisites) || {};
  return Array.isArray(prereq.enabled_platforms) ? prereq.enabled_platforms.slice() : [];
}

// Platform sources the user can include in a guided-init run. Bilibili is
// selectable like every other source (v0.3.118+): default checked
// (recommended) but no longer forced — at least one source must stay checked.
//
// WHICH sources can seed a profile comes from the shared capability roster
// (shared/source-status.js,
// published on globalThis before this module evaluates in the side panel), the
// same projection the desktop page and the setup wizard build their pickers from —
// a hardcoded copy here is what let the three surfaces drift. Labels come from
// the shared module too, with a local fallback map so an unrecognised key still
// renders; defaultChecked stays local first-run policy.
const INIT_SOURCE_LABEL_FALLBACK = {
  bilibili: "B 站",
  xiaohongshu: "小红书",
  douyin: "抖音",
  youtube: "YouTube",
  twitter: "X",
  zhihu: "知乎",
  reddit: "Reddit",
  bangumi: "Bangumi",
  v2ex: "V2EX",
  instagram: "Instagram",
};
const INIT_SOURCE_DEFAULT_CHECKED = new Set(["bilibili"]);
const _initSourceStatus = globalThis.OpenBiliClawSourceStatus || null;
const INIT_SOURCE_KEYS = (_initSourceStatus?.INIT_SOURCE_KEYS || _initSourceStatus?.SOURCE_KEYS)
  ? [...(_initSourceStatus.INIT_SOURCE_KEYS || _initSourceStatus.SOURCE_KEYS)]
  : Object.keys(INIT_SOURCE_LABEL_FALLBACK);
export const INIT_SOURCE_OPTIONS = INIT_SOURCE_KEYS.map((key) => ({
  key,
  label: _initSourceStatus?.sourceLabel?.(key) || INIT_SOURCE_LABEL_FALLBACK[key] || key,
  ...(INIT_SOURCE_DEFAULT_CHECKED.has(key) ? { defaultChecked: true } : {}),
}));

// Reminder under the source checkboxes: each selected platform is pulled THROUGH
// this browser, so the user must be logged into it here.
export const INIT_SOURCE_LOGIN_HINT =
  "勾选要纳入初始化的平台。需要登录的平台请先在当前浏览器登录；Bangumi 使用公开 API，无需登录。勾选会同时开启该来源。";

// Human labels for a list of platform keys (unknown keys pass through).
export function initSourceLabels(keys) {
  const byKey = new Map(INIT_SOURCE_OPTIONS.map((o) => [o.key, o.label]));
  return (Array.isArray(keys) ? keys : []).map((k) => byKey.get(k) || k);
}

// Compatibility helper for older callers/tests. A checked source is now an
// explicit guided-init opt-in, so the UI no longer blocks on prior settings.
export function initSelectedSourcesNeedingEnable(selected, status) {
  return [];
}

// True only when every HARD prerequisite is satisfied (B 站登录 counts only
// while bilibili is selected — see buildInitChecklist).
export function hardPrereqsSatisfied(status, selected = null) {
  return buildInitChecklist(status, selected)
    .filter((row) => row.hard)
    .every((row) => row.ok);
}

// Display state for the "开始初始化" button. Mirrors the backend's can_start
// (trusted-local + supported + hard prereqs + not running) but degrades
// gracefully when the status hasn't loaded yet. ``selected`` adds the
// client-side gates the server can't know at status time: at least one
// source checked, and B 站登录 when bilibili is among them.
export function initStartButtonState(status, selected = null) {
  if (!status) {
    return { enabled: false, label: "开始初始化", reason: "正在检查前置条件…" };
  }
  if (status.running) {
    return { enabled: false, label: "初始化进行中…", reason: "" };
  }
  if (status.initialized) {
    return {
      enabled: false,
      label: status.partial_success ? "初始化部分完成" : "已初始化",
      reason: status.partial_success
        ? describeInitStatusReason(status)
        : "如需重建画像，请到设置页。",
    };
  }
  if (Array.isArray(selected) && selected.length === 0) {
    return { enabled: false, label: "开始初始化", reason: REASON_TEXT.no_sources_selected };
  }
  if (status.can_start) {
    if (!hardPrereqsSatisfied(status, selected)) {
      return { enabled: false, label: "开始初始化", reason: REASON_TEXT.bilibili_not_logged_in };
    }
    return { enabled: true, label: "开始初始化", reason: "" };
  }
  const reason =
    describeInitStatusReason(status) ||
    (hardPrereqsSatisfied(status, selected) ? "暂时无法开始,请稍后重试。" : "请先满足上面的必需条件。");
  return { enabled: false, label: "开始初始化", reason };
}

function stageList(status) {
  return status && Array.isArray(status.stages) ? status.stages : [];
}

// ── Intra-stage progress + liveness (init-progress-visibility Phase 2) ──────
//
// REFERENCE IMPLEMENTATION for the three GUI surfaces: desktop web
// (web/desktop/assets/js/app.js) and the setup wizard (web/setup/index.html)
// mirror these pure functions. The three surfaces share no module system, so
// this duplication is the sanctioned exception to the four-surface contract —
// keep the formulas in lock-step when editing any copy.

// A running stage's fraction never claims completion: real sub-progress and
// the elapsed/eta pseudo-progress both cap here so the bar can't hit the next
// stage tick before the backend confirms it.
const STAGE_FRACTION_CAP = 0.95;
// Legacy half-step for statuses without progress/eta fields (old backends):
// preserves the historic 13/38/63/88 ticks exactly.
// A stage with no real done/total contributes NOTHING to the bar. It used to
// contribute a flat half-step (0.5), which implied progress the stage had not
// made; such stages now render indeterminate instead.
const STAGE_FRACTION_UNKNOWN = 0;
// Stall threshold. Calibration: backend heartbeat period 30s × 3 missed beats
// (api/app.py _INIT_HEARTBEAT_INTERVAL_SECONDS) — change them in lock-step.
// This governs the CONNECTION check only.
export const INIT_STALL_THRESHOLD_SECONDS = 90;
// Work-unit stall floor. A heartbeat period says nothing about how long ONE
// unit of work legitimately takes: an analysis batch on a slow/remote chat
// model routinely runs minutes (field report 2026-07-20: 280s for 2 batches,
// i.e. ~140s each — healthy, yet the old shared 90s threshold cried "stalled"
// the whole way and read as a failure). Floor generously, then adapt below to
// the cadence THIS run actually demonstrates.
export const INIT_PROGRESS_STALL_FLOOR_SECONDS = 300;
// Slack over the slowest work unit observed so far in this run: a stage is
// only "stuck" once it exceeds its own demonstrated pace by half again.
const PROGRESS_STALL_SLACK = 1.5;

// How long without a completed work unit counts as stalled for THIS run.
// max(floor, 1.5 × slowest unit seen), then capped by the backend's own hard
// ceiling for the stage so the warning still precedes a real timeout.
function _progressStallThreshold(st, stage) {
  const observed = Math.round((st.slowestProgressIntervalSeconds || 0) * PROGRESS_STALL_SLACK);
  const threshold = Math.max(INIT_PROGRESS_STALL_FLOOR_SECONDS, observed);
  const maxSeconds = Number((stage && stage.progress && stage.progress.max_seconds) || 0);
  return maxSeconds > 0 ? Math.min(threshold, maxSeconds) : threshold;
}
// Expectation copy shared by the idle checklist and the start-button area.
export const INIT_EXPECTATION_HINT =
  "完整画像和首轮可用推荐会严格按顺序生成。总耗时差别很大——取决于你勾了几个平台、拉到多少历史，也取决于 AI 服务的快慢，所以这里不预估时间；运行时会实时显示每一步的已用时和已完成的量。期间可离开此页面，进度会保留。";

// Said ONCE while the user waits, not repeated on every stage row.
export const INIT_RUNNING_HINT =
  "只要还在出结果就不会被打断，慢一些是正常的。期间可离开此页面，进度会保留。";

// Per-run client view state: elapsed-based pseudo-progress anchors (first
// client observation of each running stage) + the monotonic pct clamp + the
// staleness change marker. Keyed by run_id; bounded so long sessions can't
// accumulate stale runs.
const _runViewState = new Map();

function _viewState(runId) {
  let st = _runViewState.get(runId);
  if (!st) {
    st = {
      maxPct: 0,
      lastHeartbeatMark: null,
      lastHeartbeatChangeMs: 0,
      lastProgressMark: null,
      lastProgressChangeMs: 0,
      // Slowest gap between two completed work units seen in this run; drives
      // the adaptive progress-stall threshold.
      slowestProgressIntervalSeconds: 0,
    };
    _runViewState.set(runId, st);
    if (_runViewState.size > 8) {
      const oldest = _runViewState.keys().next().value;
      if (oldest !== runId) {
        _runViewState.delete(oldest);
      }
    }
  }
  return st;
}

// Test hook / logout reset: forget all per-run view state.
export function resetInitProgressViewState() {
  _runViewState.clear();
}

// Fraction of a RUNNING stage: ONLY real sub-progress (done/total) moves the
// bar. The old elapsed/eta pseudo-progress (1 - e^-t/eta) is gone along with
// the forecasts that fed it — faking a moving bar from a made-up duration is
// the same lie in another shape. A stage without done/total contributes
// nothing and is rendered indeterminate by ``initProgressView``.
function _runningStageFraction(stage) {
  const total = Number((stage && stage.progress && stage.progress.total) || 0);
  if (total > 0) {
    const done = Math.max(0, Math.min(Number(stage.progress.done || 0), total));
    return Math.min(STAGE_FRACTION_CAP, done / total);
  }
  return STAGE_FRACTION_UNKNOWN;
}

// A waiting user needs EVIDENCE OF PROGRESS, not a forecast. A predicted
// duration we cannot honour is worse than none: every wrong estimate reads as
// "it broke" (field report 2026-07-20 — stage 2 announced 3 minutes and stage 4
// announced 5, both legitimately ran far longer). The running row therefore
// reports only observed facts the backend already publishes: how long this
// stage has been running, and real sub-progress counts when they exist. No
// estimate, no ceiling, no extrapolation.
function formatElapsedText(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  return s < 60 ? "已用时不到 1 分钟" : `已用时 ${Math.floor(s / 60)} 分钟`;
}

export function stageDetailText(stage) {
  const progress = stage && stage.progress;
  if (!progress) {
    return "";
  }
  const parts = [];
  const elapsed = Number(progress.elapsed_seconds || 0);
  if (elapsed > 0) {
    parts.push(formatElapsedText(elapsed));
  }
  const total = Number(progress.total || 0);
  if (total > 0) {
    const done = Math.max(0, Math.min(Number(progress.done || 0), total));
    parts.push(`已完成 ${done}/${total}`);
  }
  return parts.join(" · ");
}

// Track backend heartbeat and substantive progress separately. The heartbeat
// proves the worker is connected; progress_sequence only advances at real
// milestones, so a live-but-stalled provider call is no longer hidden.
export function stalenessView(status, nowMs = Date.now()) {
  if (!status || !status.running) {
    return { fresh: true, staleSeconds: 0, text: "" };
  }
  const runId = status.run_id ? String(status.run_id) : "";
  const heartbeatAt = status.last_heartbeat_at || status.last_activity;
  const progressAt = status.last_progress_at || status.last_activity;
  if (!runId || !heartbeatAt) {
    return { fresh: true, staleSeconds: 0, text: "● 后端已接单 · 正在建立进度" };
  }
  const st = _viewState(runId);
  const heartbeatMark = `${status.sequence ?? ""}|${heartbeatAt}`;
  if (st.lastHeartbeatMark !== heartbeatMark) {
    st.lastHeartbeatMark = heartbeatMark;
    st.lastHeartbeatChangeMs = nowMs;
  }
  const progressMark = `${status.progress_sequence ?? status.sequence ?? ""}|${progressAt || ""}`;
  if (st.lastProgressMark !== progressMark) {
    // Learn this run's pace from every completed unit (skip the first mark,
    // which measures "since we started watching", not a unit's duration).
    if (st.lastProgressMark !== null && st.lastProgressChangeMs) {
      const interval = Math.max(0, Math.round((nowMs - st.lastProgressChangeMs) / 1000));
      st.slowestProgressIntervalSeconds = Math.max(
        st.slowestProgressIntervalSeconds || 0,
        interval,
      );
    }
    st.lastProgressMark = progressMark;
    st.lastProgressChangeMs = nowMs;
  }
  const heartbeatStale = Math.max(
    0,
    Math.round((nowMs - st.lastHeartbeatChangeMs) / 1000),
  );
  const progressStale = Math.max(
    0,
    Math.round((nowMs - st.lastProgressChangeMs) / 1000),
  );
  if (heartbeatStale > INIT_STALL_THRESHOLD_SECONDS) {
    const minutes = Math.max(1, Math.round(heartbeatStale / 60));
    return {
      fresh: false,
      staleSeconds: heartbeatStale,
      text: `后端已 ${minutes} 分钟没有心跳，连接可能中断。系统会继续重试；也可以取消后重试。`,
    };
  }
  const runningStage = Array.isArray(status.stages)
    ? status.stages.find((stage) => stage && stage.status === "running")
    : null;
  if (progressStale > _progressStallThreshold(st, runningStage)) {
    const minutes = Math.max(1, Math.round(progressStale / 60));
    return {
      fresh: false,
      staleSeconds: progressStale,
      text: `● 后端在线 · 这一步已等待 ${minutes} 分钟，比本轮此前的节奏慢；AI 或平台可能正卡在一次较慢的请求上，可继续等待或取消。`,
    };
  }
  return {
    fresh: true,
    staleSeconds: progressStale,
    text: "● 后端在线 · 正在处理",
  };
}

// Progress view for the in-flight init: percentage, current stage label, and
// terminal flags. ``pct`` counts completed stages plus the running stage's
// fraction — real sub-progress when available, elapsed/eta pseudo-progress
// otherwise, legacy half-step for old backends. Rendered pct is monotonic per
// run_id (stale/regressed polls can't move the bar backwards).
export function initProgressView(status, nowMs = Date.now()) {
  const total = (status && status.total_stages) || STAGE_TOTAL_FALLBACK;
  const stages = stageList(status);
  const doneCount = stages.filter((s) => s.status === "ok").length;
  const running = Boolean(status && status.running);
  const runId = status && status.run_id ? String(status.run_id) : "";
  const st = runId ? _viewState(runId) : null;
  const failedStage = stages.find((s) => s.status === "failed" || s.status === "cancelled");
  const current = (status && status.current_stage) || 0;
  const currentStage = stages.find((s) => s.n === current);
  // Indeterminate covers both the backend's explicit flag and any running
  // stage with no real done/total — with the eta gone there is nothing honest
  // left to fill such a bar with.
  const indeterminate = Boolean(
    running &&
      currentStage &&
      (currentStage.progress?.mode === "indeterminate" ||
        !(Number(currentStage.progress?.total || 0) > 0)),
  );
  let stageLabel = currentStage
    ? `${currentStage.n}/${total} ${currentStage.label}`
    : "";
  const note = currentStage && currentStage.progress && currentStage.progress.note;
  if (stageLabel && note) {
    stageLabel += ` · ${note}`;
  }
  const runningStages = stages.filter((s) => s.status === "running");
  const inFlight = runningStages.length
    ? runningStages.reduce((sum, s) => sum + _runningStageFraction(s), 0) /
      runningStages.length
    : 0;
  const rawPct = ((doneCount + (running ? inFlight : 0)) / total) * 100;
  let pct = Math.max(0, Math.min(100, Math.round(rawPct)));
  if (running) {
    pct = Math.max(pct, 1);
  }
  if (st) {
    st.maxPct = Math.max(st.maxPct, pct);
    pct = st.maxPct;
  }
  return {
    active: running,
    total,
    doneCount,
    current,
    stageLabel,
    stageDetailText: running ? stageDetailText(currentStage) : "",
    pct,
    indeterminate,
    failed: Boolean(failedStage),
    failedReason: failedStage ? failedStage.reason || "" : "",
    partial: Boolean(status && status.partial_success),
  };
}

// Whether a run has reached a terminal state (so the UI can stop polling /
// streaming and reload recommendations). Idle (never started) is not terminal.
export function isInitTerminal(status) {
  if (!status || status.running) {
    return false;
  }
  return Boolean(status.initialized) || initProgressView(status).failed;
}

// Whether a freshly-loaded popup should re-attach the progress poll instead of
// painting the idle panel. True only when a run is live (started elsewhere, or
// the page was reopened / refreshed mid-init, so no click or SSE frame kicked
// the poll on this instance). Tolerant of missing / legacy status objects.
export function shouldAttachRunningInitProgress(status) {
  return Boolean(status && status.running);
}

// A packaged desktop can pull bge-m3 before the user has started guided init.
// Keep that separate from the stage-progress view: the init panel is still
// idle, but its embedding checklist owns the live download bar.
export function shouldAttachEmbeddingPullProgress(status) {
  return Boolean(
    status &&
      !status.running &&
      !status.initialized &&
      embeddingPullProgressView(status.prerequisites).active,
  );
}

// Map an error thrown by startInit() (requestJson attaches .status/.details)
// onto human text. 409 carries a machine reason in details.error.
export function describeInitStartError(error) {
  const details = error && error.details;
  const code = details && (details.error || details.reason);
  // The same admission code serves several platforms; the backend owns the
  // source-specific recovery hint. Callers render this as plain text.
  const detail = typeof details?.detail === "string" ? details.detail.trim().slice(0, 2000) : "";
  return (
    detail ||
    describeInitReason(code) ||
    (error && error.message) ||
    "初始化没能启动，请稍后重试。"
  );
}
