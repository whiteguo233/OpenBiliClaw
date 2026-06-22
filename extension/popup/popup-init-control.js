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
  already_initialized: "已经初始化过了；如需重建，请到设置页。",
  local_only: "只能在本机发起初始化。",
  no_sources_selected: "至少勾选一个数据来源。",
  internal_error: "初始化过程中出错了，请稍后重试。",
  none: "",
};

// Human text for a backend reason / error code. Unknown codes return "".
export function describeInitReason(reason) {
  if (!reason) {
    return "";
  }
  return REASON_TEXT[reason] || "";
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
      label: "向量模型可用（推荐，非必须）",
      ok: Boolean(prereq.embedding_ready),
      hard: false,
      hint: prereq.embedding_ready
        ? ""
        : "本地 Ollama + bge-m3 没就绪也能初始化，但语义检索会弱一些。",
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
export const INIT_SOURCE_OPTIONS = [
  { key: "bilibili", label: "B 站", defaultChecked: true },
  { key: "xiaohongshu", label: "小红书" },
  { key: "douyin", label: "抖音" },
  { key: "youtube", label: "YouTube" },
  { key: "twitter", label: "X" },
];

// Reminder under the source checkboxes: each selected platform is pulled THROUGH
// this browser, so the user must be logged into it here.
export const INIT_SOURCE_LOGIN_HINT =
  "勾选要纳入初始化的平台。使用某个平台前，请先在当前浏览器登录该平台账号——否则这个来源拿不到你的数据。勾选会同时开启该来源。";

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
    return { enabled: false, label: "已初始化", reason: "如需重建画像，请到设置页。" };
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
    describeInitReason(status.reason) ||
    (hardPrereqsSatisfied(status, selected) ? "暂时无法开始,请稍后重试。" : "请先满足上面的必需条件。");
  return { enabled: false, label: "开始初始化", reason };
}

function stageList(status) {
  return status && Array.isArray(status.stages) ? status.stages : [];
}

// Progress view for the in-flight init: percentage, current stage label, and
// terminal flags. ``pct`` counts completed stages plus a half-step for the
// stage currently running, so the bar advances mid-stage instead of jumping.
export function initProgressView(status) {
  const total = (status && status.total_stages) || STAGE_TOTAL_FALLBACK;
  const stages = stageList(status);
  const doneCount = stages.filter((s) => s.status === "ok").length;
  const running = Boolean(status && status.running);
  const failedStage = stages.find((s) => s.status === "failed" || s.status === "cancelled");
  const current = (status && status.current_stage) || 0;
  const currentStage = stages.find((s) => s.n === current);
  const stageLabel = currentStage
    ? `${currentStage.n}/${total} ${currentStage.label}`
    : "";
  const inFlight = stages.some((s) => s.status === "running") ? 0.5 : 0;
  const rawPct = ((doneCount + (running ? inFlight : 0)) / total) * 100;
  const pct = Math.max(0, Math.min(100, Math.round(rawPct)));
  return {
    active: running,
    total,
    doneCount,
    current,
    stageLabel,
    pct: running ? Math.max(pct, 1) : pct,
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

// Map an error thrown by startInit() (requestJson attaches .status/.details)
// onto human text. 409 carries a machine reason in details.error.
export function describeInitStartError(error) {
  const details = error && error.details;
  const code = details && (details.error || details.reason);
  return (
    describeInitReason(code) ||
    (error && error.message) ||
    "初始化没能启动，请稍后重试。"
  );
}
