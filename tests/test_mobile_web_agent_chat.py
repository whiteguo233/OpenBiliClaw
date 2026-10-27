"""Static regressions for the mobile agent-loop chat surface (「聊一聊」 M9)."""

from __future__ import annotations

from pathlib import Path

INDEX_HTML = Path("src/openbiliclaw/web/index.html")
APP_CSS = Path("src/openbiliclaw/web/css/app.css")
API_JS = Path("src/openbiliclaw/web/js/api.js")
CHAT_JS = Path("src/openbiliclaw/web/js/views/chat.js")
SHARED_AGENT_CHAT = Path("src/openbiliclaw/web/shared/agent-chat.js")


def test_mobile_loads_shared_agent_chat_helper_before_the_module_app() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert SHARED_AGENT_CHAT.exists()
    shared = html.index('<script src="/shared/agent-chat.js"></script>')
    module = html.index('<script type="module" src="js/app.js"></script>')
    assert shared < module


def test_mobile_api_exposes_agent_stream_sessions_skills_approvals_tasks() -> None:
    api = API_JS.read_text(encoding="utf-8")

    assert "export async function streamAgentChatTurn" in api
    assert '"/chat/agent/stream"' in api
    assert "export async function streamChatTurnLegacy" in api
    assert '"/chat/stream"' in api
    assert "export async function fetchChatSkills" in api
    assert "export async function fetchChatSessions" in api
    assert "export async function createChatSession" in api
    assert "export async function updateChatSession" in api
    assert "export async function fetchChatSessionDetail" in api
    assert "export async function fetchChatApprovals" in api
    assert "export async function approveChatApproval" in api
    assert "export async function rejectChatApproval" in api
    assert "export async function createAgentTask" in api
    assert "export async function fetchAgentTasks" in api
    assert "export async function fetchAgentTask" in api
    assert "export async function cancelAgentTask" in api
    # The durable turn POST carries the multi-session and skill bindings.
    assert "if (sessionId) payload.session_id = sessionId;" in api
    assert "if (skill) payload.skill = skill;" in api


def test_mobile_chat_drives_agent_stream_with_legacy_fallback() -> None:
    chat = CHAT_JS.read_text(encoding="utf-8")

    assert "globalThis.OpenBiliClawAgentChat" in chat
    assert "async function driveAgentStream" in chat
    assert "async function driveLegacyStream" in chat
    # loop_enabled=false (503) permanently falls back for the page load.
    assert "error?.status) === 503" in chat
    assert "agentLoopAvailable = false;" in chat
    # Live runs update a targeted slot, never the whole shell.
    assert "function updateAgentRunDom(turnId)" in chat
    assert "agent-run-live-slot" in chat
    # Completed turns replay the persisted event log collapsed.
    assert "agentEventsFromTurn(turn)" in chat
    assert "renderAgentRunMarkup(agentRun, { collapsed: agentRun.settled })" in chat


def test_mobile_chat_wires_sessions_skills_approvals_and_tasks() -> None:
    chat = CHAT_JS.read_text(encoding="utf-8")

    # Session drawer: create / switch / rename / archive.
    assert "async function switchSession(sessionId)" in chat
    assert "async function handleCreateSession()" in chat
    assert "async function handleRenameSession(sessionId, title)" in chat
    assert "async function handleArchiveSession(sessionId)" in chat
    assert "fetchChatSessionDetail(sessionId, { limit: 100 })" in chat
    # Skill chip + sheet + suggest_skill one-tap switch card.
    assert "function currentSkillName()" in chat
    assert "function handleSkillSwitchCard(button)" in chat
    assert "data-skill-pick" in chat
    # Approval cards reuse the pending-confirmation panel habits.
    assert "async function handleApprovalAction(button)" in chat
    assert "reject-submit" in chat
    # Task center overlay with detail, cancel and suggestion carry-over.
    assert "async function refreshAgentTasks()" in chat
    assert "async function openTaskDetail(taskId" in chat
    assert "renderAgentTaskDetailMarkup" in chat
    assert "data-agent-suggestion-use" in chat
    # Background-task completion turns render the summary card.
    assert "isAgentTaskSummaryTurn(turn)" in chat
    assert "renderAgentTaskSummaryMarkup" in chat


def test_mobile_agent_styles_keep_long_lists_bounded() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".chat-agent-topbar {" in css
    assert ".agent-run {" in css
    assert ".agent-run.is-collapsed > .agent-run-toggle {" in css
    assert ".agent-approval-card {" in css
    assert ".agent-drawer-overlay {" in css
    assert ".agent-drawer-list {" in css
    assert "overscroll-behavior: contain;" in css
    assert ".agent-task-row {" in css


def test_mobile_chat_shows_loading_indicator_until_first_history_settles() -> None:
    chat = CHAT_JS.read_text(encoding="utf-8")

    # First paint goes through the shared view-state helper: loading until the
    # first history fetch settles, empty copy only after total=0.
    assert "getChatHistoryViewState" in chat
    assert 'historyViewState === "loading"' in chat
    assert 'historyViewState === "empty"' in chat
    assert "chat-history-loading" in chat
    assert "let historyLoaded = false;" in chat
    # Entering the view paints immediately (spinner) before the fetch returns.
    assert "render();\n  loadHistory();" in chat
    # Switching sessions re-arms the loading state instead of flashing the
    # empty copy while the new session's history is in flight.
    assert "turns = [];\n  historyLoaded = false;" in chat


def test_mobile_chat_history_loading_styles_exist() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".chat-history-loading {" in css
    assert ".chat-history-loading-text {" in css


def test_mobile_approval_cards_follow_the_async_execute_protocol() -> None:
    """approve 只入队（queued → executing → 终态轮询），旧协议同步结果兜底。"""

    chat = CHAT_JS.read_text(encoding="utf-8")

    # approve 响应按 queued 字段归类。
    assert "normalizeApproveResponse(await approveChatApproval(approvalId))" in chat
    assert 'response.kind === "queued"' in chat
    # 入队后卡片进入「执行中…」并登记跟踪，防止重复点击。
    assert "markApprovalCardExecuting(card)" in chat
    assert "executingApprovals.set(approvalId" in chat
    # 终态由 refreshApprovals 的 2.5s 轮询恢复（executing 列表 + 全量快照）。
    assert 'fetchChatApprovals({ status: "executing" })' in chat
    assert "settleTrackedApproval(approvalId, turnId, record)" in chat
    # 刷新/回放用 executing 覆盖恢复中间态。
    assert "applyApprovalRecordToRun(run, record)" in chat

    shared = SHARED_AGENT_CHAT.read_text(encoding="utf-8")

    assert 'executing: "执行中…"' in shared
    assert "function normalizeApproveResponse(payload)" in shared
    assert "function applyApprovalRecordToRun(run, record)" in shared

    css = APP_CSS.read_text(encoding="utf-8")

    assert '.agent-approval-status[data-tone="executing"]' in css
