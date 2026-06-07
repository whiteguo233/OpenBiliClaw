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
export function buildInitChecklist(status) {
  const prereq = (status && status.prerequisites) || {};
  const enabled = getEnabledPlatforms(status);
  return [
    {
      key: "bilibili",
      label: "B 站已登录",
      ok: Boolean(prereq.bilibili_logged_in),
      hard: true,
      hint: prereq.bilibili_logged_in
        ? ""
        : "在浏览器里登录 bilibili.com，扩展会自动把 Cookie 同步给后端。",
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
      label: enabled.length
        ? `数据来源：${enabled.join("、")}`
        : "数据来源：仅 B 站（可在设置里开启更多平台）",
      ok: enabled.length > 0,
      hard: false,
      hint:
        enabled.length > 0
          ? ""
          : "默认只接入 B 站；想纳入小红书 / 抖音 / YouTube，先到设置页开启对应平台。",
    },
  ];
}

export function getEnabledPlatforms(status) {
  const prereq = (status && status.prerequisites) || {};
  return Array.isArray(prereq.enabled_platforms) ? prereq.enabled_platforms.slice() : [];
}

// True only when every HARD prerequisite is satisfied.
export function hardPrereqsSatisfied(status) {
  return buildInitChecklist(status)
    .filter((row) => row.hard)
    .every((row) => row.ok);
}

// Display state for the "开始初始化" button. Mirrors the backend's can_start
// (trusted-local + supported + hard prereqs + not running) but degrades
// gracefully when the status hasn't loaded yet.
export function initStartButtonState(status) {
  if (!status) {
    return { enabled: false, label: "开始初始化", reason: "正在检查前置条件…" };
  }
  if (status.running) {
    return { enabled: false, label: "初始化进行中…", reason: "" };
  }
  if (status.initialized) {
    return { enabled: false, label: "已初始化", reason: "如需重建画像，请到设置页。" };
  }
  if (status.can_start) {
    return { enabled: true, label: "开始初始化", reason: "" };
  }
  const reason =
    describeInitReason(status.reason) ||
    (hardPrereqsSatisfied(status) ? "暂时无法开始,请稍后重试。" : "请先满足上面的必需条件。");
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
