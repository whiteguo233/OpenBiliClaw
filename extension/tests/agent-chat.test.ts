import assert from "node:assert/strict";
import test from "node:test";

await import("../../src/openbiliclaw/web/shared/agent-chat.js");

const agentChat = (globalThis as typeof globalThis & {
  OpenBiliClawAgentChat?: Record<string, any>;
}).OpenBiliClawAgentChat;

assert.ok(agentChat, "shared agent chat helper should install its browser global");

const {
  createAgentSseParser,
  createAgentRun,
  applyAgentEvent,
  agentRunFromEvents,
  agentEventsFromTurn,
  normalizeChatSkillList,
  skillDisplayTitle,
  approvalStatusLabel,
  isApprovalTerminalStatus,
  normalizeApprovalList,
  normalizeApproveResponse,
  applyApprovalRecordToRun,
  agentTaskStatusLabel,
  isAgentTaskActive,
  normalizeAgentTask,
  isAgentTaskSummaryTurn,
  renderApprovalCardMarkup,
  renderAgentRunMarkup,
  renderAgentTaskSummaryMarkup,
  renderAgentTaskRowMarkup,
  renderAgentTaskDetailMarkup,
} = agentChat;

function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("SSE parser emits typed events and tolerates chunk splits", () => {
  const seen: Array<[string, any]> = [];
  const parser = createAgentSseParser((name: string, data: unknown) => seen.push([name, data]));
  const frame = sseFrame("thinking", { type: "thinking", step: 1, text: "先看看画像" });
  // Split mid-line and mid-frame to prove incremental parsing.
  for (const char of frame) parser.push(char);
  parser.push(sseFrame("done", { reply: "你好", turn_id: "t1", session_id: "default", skill: "taste-companion" }));
  parser.end();
  assert.equal(seen.length, 2);
  assert.equal(seen[0][0], "thinking");
  assert.deepEqual(seen[0][1], { type: "thinking", step: 1, text: "先看看画像" });
  assert.equal(seen[1][0], "done");
  assert.equal(seen[1][1].reply, "你好");
});

test("SSE parser supports multi-line data and skips comments", () => {
  const seen: Array<[string, any]> = [];
  const parser = createAgentSseParser((name: string, data: unknown) => seen.push([name, data]));
  parser.push(": heartbeat\n\n");
  parser.push("event: tool_call\n");
  parser.push('data: {"type":"tool_call",\n');
  parser.push('data: "step":2,"tool_name":"get_profile","arguments":{},"summary":"get_profile()"}\n\n');
  parser.end();
  assert.equal(seen.length, 1);
  assert.equal(seen[0][1].tool_name, "get_profile");
});

test("SSE parser drops malformed frames but keeps the stream alive", () => {
  const seen: Array<[string, any]> = [];
  const parser = createAgentSseParser((name: string, data: unknown) => seen.push([name, data]));
  parser.push("event: thinking\ndata: {not json}\n\n");
  parser.push(sseFrame("final", { type: "final", step: 3, text: "结论" }));
  parser.end();
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], "final");
});

test("run reducer tracks thinking, tool call lifecycle and final", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "thinking", { step: 1, text: "先查画像" });
  applyAgentEvent(run, "tool_call", {
    step: 1,
    tool_name: "get_profile",
    arguments: {},
    summary: "get_profile()",
  });
  assert.equal(run.steps.length, 1);
  assert.equal(run.steps[0].items[0].status, "running");
  applyAgentEvent(run, "tool_result", {
    step: 1,
    tool_name: "get_profile",
    text: "# 画像",
    ok: true,
    truncated: false,
  });
  assert.equal(run.steps[0].items[0].status, "ok");
  assert.equal(run.steps[0].items[0].result, "# 画像");
  applyAgentEvent(run, "final", { step: 2, text: "你的画像是…" });
  applyAgentEvent(run, "done", { reply: "你的画像是…", turn_id: "t1", skill: "taste-companion" });
  assert.equal(run.settled, true);
  assert.equal(run.finalText, "你的画像是…");
  assert.equal(run.done.skill, "taste-companion");
});

test("run reducer marks failed tool results and step limit", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "tool_call", { step: 1, tool_name: "nope", arguments: {}, summary: "nope()" });
  applyAgentEvent(run, "tool_result", {
    step: 1,
    tool_name: "nope",
    text: "unknown_tool",
    ok: false,
    truncated: false,
  });
  applyAgentEvent(run, "step_limit_reached", { step: 64, text: "达到上限" });
  assert.equal(run.steps[0].items[0].status, "failed");
  assert.equal(run.stepLimitReached, true);
});

test("run reducer captures suggest_skill and start_background_task meta calls", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "tool_call", {
    step: 1,
    tool_name: "suggest_skill",
    arguments: { skill: "bangumi-advisor", reason: "你在聊追番" },
    summary: "suggest_skill(bangumi-advisor)",
  });
  applyAgentEvent(run, "tool_call", {
    step: 2,
    tool_name: "start_background_task",
    arguments: { prompt: "盘点我的收藏", title: "收藏盘点", skill: "" },
    summary: "start_background_task()",
  });
  assert.deepEqual(run.skillSuggestion, { skill: "bangumi-advisor", reason: "你在聊追番" });
  assert.equal(run.taskProposal.prompt, "盘点我的收藏");
  assert.equal(run.taskProposal.title, "收藏盘点");
});

test("approval_request parks a pending approval and approval_result settles it", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "approval_request", {
    step: 1,
    approval_id: "ap_1",
    tool_name: "update_config",
    arguments: { key: "scheduler.enabled", value: true },
    summary: "修改配置 scheduler.enabled",
    impact: "立即热重载",
  });
  assert.equal(run.approvals.length, 1);
  assert.equal(run.approvals[0].status, "pending");
  assert.equal(run.steps[0].items[0].kind, "approval");
  applyAgentEvent(run, "approval_result", {
    approval_id: "ap_1",
    tool_name: "update_config",
    decision: "approved",
    ok: true,
    text: "已写入",
  });
  assert.equal(run.approvals[0].status, "executed");
  assert.equal(run.approvals[0].resultText, "已写入");
});

test("agentRunFromEvents replays persisted payload.agent_events", () => {
  const turn = {
    turn_id: "t1",
    payload: {
      agent_events: [
        { type: "thinking", step: 1, text: "想一下" },
        { type: "tool_call", step: 1, tool_name: "list_sources", arguments: {}, summary: "list_sources()" },
        { type: "tool_result", step: 1, tool_name: "list_sources", text: "3 个源", ok: true },
        { type: "final", step: 2, text: "你订阅了 3 个源" },
      ],
    },
  };
  const events = agentEventsFromTurn(turn);
  assert.equal(events.length, 4);
  const run = agentRunFromEvents(events);
  // final does not open a new step entry; only hop content does.
  assert.equal(run.steps.length, 1);
  assert.equal(run.steps[0].items[0].status, "ok");
  assert.equal(run.finalText, "你订阅了 3 个源");
});

test("run markup renders collapsed summary, tool details and error", () => {
  const run = agentRunFromEvents([
    { type: "thinking", step: 1, text: "<思考一下>" },
    { type: "tool_call", step: 1, tool_name: "get_config", arguments: { a: 1 }, summary: "get_config()" },
    { type: "tool_result", step: 1, tool_name: "get_config", text: "{ok}", ok: true, truncated: true },
    { type: "final", step: 2, text: "好了" },
  ]);
  const markup = renderAgentRunMarkup(run, { collapsed: true });
  assert.match(markup, /agent-run is-collapsed/);
  assert.match(markup, /执行过程（1 步，已完成）/);
  assert.match(markup, /&lt;思考一下&gt;/);
  assert.match(markup, /data-tool="get_config"/);
  assert.match(markup, /结果已截断/);

  const failedRun = agentRunFromEvents([{ type: "error", error: "模型繁忙" }]);
  assert.match(renderAgentRunMarkup(failedRun), /agent-run-error/);
  assert.match(renderAgentRunMarkup(failedRun), /模型繁忙/);
});

test("run markup renders skill suggestion and task proposal cards with hooks", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "tool_call", {
    step: 1,
    tool_name: "suggest_skill",
    arguments: { skill: "taste-explorer", reason: "想深挖" },
    summary: "suggest_skill()",
  });
  const markup = renderAgentRunMarkup(run);
  assert.match(markup, /data-agent-skill-switch="taste-explorer"/);
  assert.match(markup, /想深挖/);

  const run2 = createAgentRun();
  applyAgentEvent(run2, "tool_call", {
    step: 1,
    tool_name: "start_background_task",
    arguments: { prompt: "长期观察", title: "观察", skill: "taste-companion" },
    summary: "start_background_task()",
  });
  const markup2 = renderAgentRunMarkup(run2);
  assert.match(markup2, /data-agent-task-confirm/);
  assert.match(markup2, /data-task-prompt="长期观察"/);
});

test("approval card markup exposes approve/reject hooks only while pending", () => {
  const pending = renderApprovalCardMarkup({
    approval_id: "ap_1",
    tool_name: "update_config",
    arguments: { key: "a", value: 1 },
    summary: "改配置",
    impact: "热重载",
    status: "pending",
  });
  assert.match(pending, /data-agent-approval-action="approve"/);
  assert.match(pending, /data-agent-approval-action="reject-submit"/);
  assert.match(pending, /agent-approval-reason/);

  const settled = renderApprovalCardMarkup({
    approval_id: "ap_1",
    tool_name: "update_config",
    arguments: {},
    summary: "改配置",
    impact: "",
    status: "rejected",
    resultText: "用户拒绝了该操作，未执行。",
  });
  assert.doesNotMatch(settled, /data-agent-approval-action="approve"/);
  assert.match(settled, /已拒绝/);
});

test("approval list normalization maps records and failure states", () => {
  const items = normalizeApprovalList({
    items: [
      { approval_id: "ap_1", tool_name: "update_config", arguments: {}, summary: "s", status: "pending" },
      { approval_id: "ap_2", tool_name: "toggle_source", arguments: {}, summary: "s", status: "executed", error: "写入失败" },
    ],
  });
  assert.equal(items.length, 2);
  assert.equal(items[0].status, "pending");
  assert.equal(items[1].status, "failed");
  assert.equal(approvalStatusLabel("expired"), "已过期");
});

// ── 异步审批执行（approve 只入队，终态靠轮询恢复） ─────────────

test("approval statuses label the executing intermediate state", () => {
  assert.equal(approvalStatusLabel("executing"), "执行中…");
  assert.equal(approvalStatusLabel("executed"), "已批准并执行");
  assert.equal(approvalStatusLabel("failed"), "执行失败");
  assert.equal(isApprovalTerminalStatus("executed"), true);
  assert.equal(isApprovalTerminalStatus("failed"), true);
  assert.equal(isApprovalTerminalStatus("executing"), false);
  assert.equal(isApprovalTerminalStatus("pending"), false);
});

test("executing approval cards show the running state without action buttons", () => {
  const markup = renderApprovalCardMarkup({
    approval_id: "ap_1",
    tool_name: "update_config",
    arguments: {},
    summary: "改配置",
    impact: "热重载",
    status: "executing",
    resultText: "",
  });
  assert.match(markup, /data-status="executing"/);
  assert.match(markup, /执行中…/);
  assert.doesNotMatch(markup, /data-agent-approval-action="approve"/);
});

test("normalizeApproveResponse classifies async queued replies", () => {
  const first = normalizeApproveResponse({
    approval: { approval_id: "ap_1", status: "executing" },
    executed: false,
    queued: true,
    already_queued: false,
    ok: null,
    result: "",
  });
  assert.equal(first.kind, "queued");
  assert.equal(first.alreadyQueued, false);
  assert.equal(first.approval.status, "executing");

  const duplicate = normalizeApproveResponse({
    approval: { approval_id: "ap_1", status: "executing" },
    executed: false,
    queued: true,
    already_queued: true,
    ok: null,
    result: "",
  });
  assert.equal(duplicate.kind, "queued");
  assert.equal(duplicate.alreadyQueued, true);
});

test("normalizeApproveResponse surfaces idempotent terminal and legacy sync replies", () => {
  const terminal = normalizeApproveResponse({
    approval: { approval_id: "ap_1", status: "failed", error: "热重载超时" },
    executed: false,
    already_executed: true,
    queued: false,
    ok: false,
    result: "",
  });
  assert.equal(terminal.kind, "settled");
  assert.equal(terminal.ok, false);
  assert.equal(terminal.resultText, "热重载超时");

  // 旧协议：同步执行，响应没有 queued 字段，直接带 ok/result。
  const legacy = normalizeApproveResponse({ ok: true, result: "已写入" });
  assert.equal(legacy.kind, "settled");
  assert.equal(legacy.ok, true);
  assert.equal(legacy.resultText, "已写入");
});

test("applyApprovalRecordToRun recovers executing and settles terminal states", () => {
  const run = createAgentRun();
  applyAgentEvent(run, "approval_request", {
    step: 1,
    approval_id: "ap_1",
    tool_name: "update_config",
    arguments: {},
    summary: "改配置",
    impact: "",
  });
  const approval = run.approvals[0];
  assert.equal(approval.status, "pending");

  // 刷新恢复：后端 executing 列表把回放里的 pending 卡标成执行中。
  applyApprovalRecordToRun(run, { approval_id: "ap_1", status: "executing" });
  assert.equal(approval.status, "executing");

  // 轮询到终态：写入结果摘要。
  applyApprovalRecordToRun(run, { approval_id: "ap_1", status: "executed", result: "已写入" });
  assert.equal(approval.status, "executed");
  assert.equal(approval.resultText, "已写入");

  // 已终态的回放不被过期快照降级。
  applyApprovalRecordToRun(run, { approval_id: "ap_1", status: "executing" });
  assert.equal(approval.status, "executed");

  // 未命中 id / 非记录输入安全返回 null。
  assert.equal(applyApprovalRecordToRun(run, { approval_id: "ap_x", status: "executing" }), null);
  assert.equal(applyApprovalRecordToRun(run, null), null);
});

test("task helpers label statuses and detect summary turns", () => {
  assert.equal(agentTaskStatusLabel("running"), "进行中");
  assert.equal(agentTaskStatusLabel("interrupted"), "已中断");
  assert.equal(isAgentTaskActive("pending"), true);
  assert.equal(isAgentTaskActive("completed"), false);
  assert.equal(
    isAgentTaskSummaryTurn({ payload: { type: "agent_task_summary", task_id: "t" } }),
    true,
  );
  assert.equal(isAgentTaskSummaryTurn({ payload: { type: "hypothesis_card" } }), false);
});

test("task row and detail markup expose open/cancel hooks and suggestions", () => {
  const task = normalizeAgentTask({
    task_id: "task-1",
    prompt: "盘点收藏",
    status: "completed",
    report: "盘完了",
    suggestions: [{ action: "write_memory", summary: "记下偏好", payload: {} }],
    steps: [{ type: "final", step: 1, text: "盘完了" }],
  });
  const row = renderAgentTaskRowMarkup(task);
  assert.match(row, /data-agent-task-open="task-1"/);
  assert.doesNotMatch(row, /data-agent-task-cancel/, "terminal tasks must not offer cancel");

  const detail = renderAgentTaskDetailMarkup(task);
  assert.match(detail, /盘完了/);
  assert.match(detail, /data-agent-suggestion-use/);
  assert.match(detail, /data-summary="记下偏好"/);

  const running = renderAgentTaskRowMarkup(normalizeAgentTask({ task_id: "task-2", prompt: "x", status: "running" }));
  assert.match(running, /data-agent-task-cancel="task-2"/);
});

test("task summary card markup renders report and suggestion hooks", () => {
  const markup = renderAgentTaskSummaryMarkup({
    task_id: "task-9",
    task_status: "completed",
    report: "报告正文",
    suggestions: [{ action: "save_item", summary: "收藏这个", payload: {} }],
  });
  assert.match(markup, /data-task-id="task-9"/);
  assert.match(markup, /报告正文/);
  assert.match(markup, /data-agent-task-open="task-9"/);
  assert.match(markup, /带入对话/);
});

test("skill list normalization keeps titles and default flag", () => {
  const skills = normalizeChatSkillList({
    skills: [
      { name: "taste-companion", title: "口味伙伴", description: "默认", tools: ["get_profile"], default: true },
      { name: "custom-one", title: "", description: "", source: "custom" },
      { name: "" },
    ],
  });
  assert.equal(skills.length, 2);
  assert.equal(skills[0].isDefault, true);
  assert.equal(skills[1].title, "custom-one");
  assert.equal(skillDisplayTitle("taste-companion", skills), "口味伙伴");
  assert.equal(skillDisplayTitle("missing", skills), "missing");
});
