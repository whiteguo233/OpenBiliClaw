import test from "node:test";
import assert from "node:assert/strict";

import core from "../../src/openbiliclaw/web/desktop/assets/js/chat-agent-core.js";

const {
  agentProcessMarkup,
  applyAgentEvent,
  applyApprovalRecordToProcess,
  approvalCardMarkup,
  approvalCardModelFromRecord,
  approvalsPanelMarkup,
  buildAgentProcess,
  createAgentProcess,
  createSseParser,
  createSseReadWatchdog,
  SSE_READ_WATCHDOG_MS,
  isAgentTaskSummaryTurn,
  isApprovalTerminalStatus,
  isSoftWriteSuggestion,
  normalizeApproveResponse,
  processStepCount,
  sessionListMarkup,
  skillPickerMarkup,
  suggestionConfirmLabel,
  taskDetailMarkup,
  taskListMarkup,
  taskSummaryCardMarkup,
  turnAgentEvents,
} = core;

// ── SSE 解析 ──────────────────────────────────────────────────

test("SSE parser dispatches event name + parsed JSON data", () => {
  const seen = [];
  const parser = createSseParser((event, data) => seen.push([event, data]));
  parser.feed('event: thinking\ndata: {"type":"thinking","step":1,"text":"先看看"}\n\n');
  assert.deepEqual(seen, [["thinking", { type: "thinking", step: 1, text: "先看看" }]]);
});

test("SSE parser handles split chunks and CRLF", () => {
  const seen = [];
  const parser = createSseParser((event, data) => seen.push([event, data]));
  parser.feed('event: tool_call\r\nda');
  parser.feed('ta: {"tool_name":"get_profile"}\r\n\r\nevent: done\r\n');
  parser.feed('data: {"reply":"好"}\r\n\r\n');
  assert.equal(seen.length, 2);
  assert.equal(seen[0][0], "tool_call");
  assert.deepEqual(seen[0][1], { tool_name: "get_profile" });
  assert.deepEqual(seen[1], ["done", { reply: "好" }]);
});

test("SSE parser concatenates multi-line data and ignores comments", () => {
  const seen = [];
  const parser = createSseParser((event, data) => seen.push([event, data]));
  parser.feed(': keep-alive\n\n');
  parser.feed('event: content\ndata: {"a":\ndata: 1}\n\n');
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], "content");
  assert.deepEqual(seen[0][1], { a: 1 }); // 多行 data 拼接后仍是合法 JSON
});

test("SSE parser flushes a trailing event without blank line on end()", () => {
  const seen = [];
  const parser = createSseParser((event, data) => seen.push([event, data]));
  parser.feed('event: error\ndata: {"error":"boom"}');
  parser.end();
  assert.deepEqual(seen, [["error", { error: "boom" }]]);
});

// ── SSE 读看门狗 ──────────────────────────────────────────────

test("SSE read watchdog aborts after a silent window", async () => {
  const watchdog = createSseReadWatchdog({ timeoutMs: 20 });
  const aborted = new Promise((resolve) => watchdog.signal.addEventListener("abort", resolve));
  watchdog.reset();
  await aborted;
  assert.equal(watchdog.signal.aborted, true);
});

test("SSE read watchdog reset postpones and cancel disarms", async () => {
  const watchdog = createSseReadWatchdog({ timeoutMs: 40 });
  watchdog.reset();
  await new Promise((resolve) => setTimeout(resolve, 25));
  watchdog.reset(); // 心跳/字节到达 → 重新计时
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.equal(watchdog.signal.aborted, false);
  watchdog.cancel();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(watchdog.signal.aborted, false);
});

test("SSE read watchdog default window is 60s", () => {
  assert.equal(SSE_READ_WATCHDOG_MS, 60_000);
});

// ── 过程模型 ──────────────────────────────────────────────────

test("buildAgentProcess assembles thinking/tool steps in order", () => {
  const model = buildAgentProcess([
    { type: "thinking", step: 1, text: "先看看你的画像。" },
    { type: "tool_call", step: 1, tool_name: "get_profile", arguments: {}, summary: "🔍 查看了你的画像" },
    { type: "tool_result", step: 1, tool_name: "get_profile", text: "画像…", ok: true, truncated: false },
    { type: "thinking", step: 2, text: "再看看历史。" },
    { type: "tool_call", step: 2, tool_name: "get_watch_history", arguments: { limit: 5 }, summary: "🔍 查看了你的观看历史" },
    { type: "tool_result", step: 2, tool_name: "get_watch_history", text: "历史…", ok: true, truncated: true },
    { type: "final", step: 3, text: "你最近在看…" },
  ]);
  assert.equal(model.steps.length, 2);
  assert.equal(model.steps[0].thinking, "先看看你的画像。");
  assert.equal(model.steps[0].toolCalls[0].result.ok, true);
  assert.equal(model.steps[1].toolCalls[0].result.truncated, true);
  assert.equal(model.finalText, "你最近在看…");
  assert.equal(processStepCount(model), 2);
});

test("applyAgentEvent accepts JSON-string arguments", () => {
  const model = createAgentProcess();
  applyAgentEvent(model, {
    type: "tool_call",
    step: 1,
    tool_name: "write_memory",
    arguments: '{"layer":"event","text":"x"}',
    summary: "写入记忆",
  });
  assert.equal(model.steps[0].toolCalls[0].argumentsText, '{\n  "layer": "event",\n  "text": "x"\n}');
});

test("tool_result attaches to the matching unresolved call in its step", () => {
  const model = createAgentProcess();
  applyAgentEvent(model, { type: "tool_call", step: 1, tool_name: "a", arguments: {}, summary: "a()" });
  applyAgentEvent(model, { type: "tool_call", step: 1, tool_name: "b", arguments: {}, summary: "b()" });
  applyAgentEvent(model, { type: "tool_result", step: 1, tool_name: "a", text: "A", ok: true });
  applyAgentEvent(model, { type: "tool_result", step: 1, tool_name: "b", text: "B", ok: false });
  assert.equal(model.steps[0].toolCalls[0].result.text, "A");
  assert.equal(model.steps[0].toolCalls[1].result.ok, false);
});

test("orphan tool_result still renders as a step", () => {
  const model = buildAgentProcess([
    { type: "tool_result", step: 2, tool_name: "x", text: "...", ok: false },
  ]);
  assert.equal(model.steps[0].step, 2);
  assert.equal(model.steps[0].toolCalls[0].result.ok, false);
});

test("approval_request then approval_result resolves the same card", () => {
  const model = buildAgentProcess([
    { type: "tool_call", step: 1, tool_name: "update_config", arguments: { key: "a" }, summary: "修改配置" },
    {
      type: "approval_request",
      step: 1,
      approval_id: "ap_1",
      tool_name: "update_config",
      arguments: { key: "a" },
      summary: "修改配置 a",
      impact: "立即生效",
    },
    { type: "tool_result", step: 1, tool_name: "update_config", text: "已提交审批", ok: true },
    { type: "final", step: 2, text: "等你批准" },
    { type: "approval_result", approval_id: "ap_1", tool_name: "update_config", decision: "approved", ok: true, text: "已写入" },
  ]);
  const approval = model.steps[0].toolCalls[0].approval;
  assert.equal(approval.status, "executed");
  assert.equal(approval.decision, "approved");
  assert.equal(approval.resultText, "已写入");
});

test("approval_result rejection is reflected", () => {
  const model = buildAgentProcess([
    { type: "approval_request", step: 1, approval_id: "ap_2", tool_name: "create_source", arguments: {}, summary: "新建源", impact: "" },
    { type: "approval_result", approval_id: "ap_2", tool_name: "create_source", decision: "rejected", ok: true, text: "已拒绝" },
  ]);
  assert.equal(model.steps[0].toolCalls[0].approval.status, "rejected");
});

test("step_limit_reached and error populate model notes", () => {
  const model = buildAgentProcess([
    { type: "step_limit_reached", step: 64, text: "达到步数上限" },
    { type: "final", step: 65, text: "目前进展…" },
  ]);
  assert.equal(model.stepLimitText, "达到步数上限");
  const errored = buildAgentProcess([{ type: "error", error: "LLM 超时" }]);
  assert.equal(errored.errorText, "LLM 超时");
});

// ── 过程折叠组件 markup ───────────────────────────────────────

test("agentProcessMarkup renders collapsed details bar after completion", () => {
  const model = buildAgentProcess([
    { type: "tool_call", step: 1, tool_name: "get_profile", arguments: {}, summary: "🔍 查看了你的画像" },
    { type: "tool_result", step: 1, tool_name: "get_profile", text: "ok", ok: true },
    { type: "final", step: 2, text: "答复" },
  ]);
  const html = agentProcessMarkup(model);
  assert.match(html, /<details class="agent-process">/);
  assert.match(html, /过程（1 步）/);
  assert.doesNotMatch(html, /<details class="agent-process" open>/);
  assert.match(html, /🔍 查看了你的画像/);
});

test("agentProcessMarkup live mode stays expanded and shows progress", () => {
  const model = buildAgentProcess([
    { type: "thinking", step: 1, text: "想想" },
    { type: "tool_call", step: 1, tool_name: "get_profile", arguments: {}, summary: "查看画像" },
  ]);
  const html = agentProcessMarkup(model, { live: true });
  assert.match(html, /class="agent-process is-live"/);
  assert.match(html, /进行中…（1 步）/);
  assert.match(html, /agent-thinking/);
  // 无结果的工具调用是可展开的 details
  assert.match(html, /<details class="agent-tool">/);
});

test("agentProcessMarkup escapes HTML in tool output", () => {
  const model = buildAgentProcess([
    { type: "tool_result", step: 1, tool_name: "x", text: "<script>alert(1)</script>", ok: true },
  ]);
  const html = agentProcessMarkup(model);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("agentProcessMarkup returns empty string for empty model", () => {
  assert.equal(agentProcessMarkup(createAgentProcess()), "");
});

test("suggest_skill and start_background_task render as action cards", () => {
  const model = buildAgentProcess([
    { type: "tool_call", step: 1, tool_name: "suggest_skill", arguments: { skill: "taste-explorer", reason: "更适合深挖" }, summary: "建议切换" },
    { type: "tool_call", step: 2, tool_name: "start_background_task", arguments: { prompt: "盘点收藏", title: "收藏盘点" }, summary: "提议后台任务" },
  ]);
  const html = agentProcessMarkup(model, { live: true });
  assert.match(html, /data-suggest-skill="taste-explorer"/);
  assert.match(html, /更适合深挖/);
  assert.match(html, /data-bg-task=/);
  assert.match(html, /收藏盘点/);
});

// ── 汇总卡 / 列表 markup ──────────────────────────────────────

test("taskSummaryCardMarkup renders report and suggestion buttons", () => {
  const turn = {
    turn_id: "t1",
    message: "[后台任务完成] 收藏盘点",
    reply: "盘完了。",
    payload: {
      type: "agent_task_summary",
      task_id: "task-1",
      task_status: "completed",
      suggestions: [
        { action: "write_memory", summary: "记下偏好", payload: { text: "x" } },
        { action: "toggle_source", summary: "关掉某源", payload: {} },
      ],
    },
  };
  assert.equal(isAgentTaskSummaryTurn(turn), true);
  const html = taskSummaryCardMarkup(turn);
  assert.match(html, /收藏盘点/);
  assert.match(html, /确认执行/); // soft_write
  assert.match(html, /去对话确认/); // hard_write
  assert.match(html, /data-task-open="task-1"/);
});

test("suggestion classification matches soft/hard write split", () => {
  assert.equal(isSoftWriteSuggestion("write_memory"), true);
  assert.equal(isSoftWriteSuggestion("submit_feedback"), true);
  assert.equal(isSoftWriteSuggestion("save_item"), true);
  assert.equal(isSoftWriteSuggestion("update_config"), false);
  assert.equal(suggestionConfirmLabel("save_item"), "确认执行");
  assert.equal(suggestionConfirmLabel("create_source"), "去对话确认");
});

test("sessionListMarkup flags default, active and live sessions", () => {
  const html = sessionListMarkup(
    [
      { session_id: "default", title: "", last_message_preview: "hi", active_turns: 0, archived: false },
      { session_id: "chat-1", title: "追番计划", last_message_preview: "...", active_turns: 2, archived: false },
      { session_id: "chat-2", title: "旧话题", archived: true },
    ],
    "chat-1",
  );
  assert.match(html, /默认会话/);
  assert.match(html, /chat-session-item is-active/);
  assert.match(html, /chat-session-live/);
  assert.match(html, /is-archived/);
  // 默认会话不提供归档按钮
  const defaultRow = html.split("chat-session-item")[1];
  assert.doesNotMatch(defaultRow, /data-session-action="archive"/);
});

test("taskListMarkup shows status, progress and cancel for active tasks", () => {
  const html = taskListMarkup([
    { task_id: "t1", title: "盘点", status: "running", progress: "第 3 步" },
    { task_id: "t2", title: "旧任务", status: "completed" },
  ]);
  assert.match(html, /进行中/);
  assert.match(html, /第 3 步/);
  assert.match(html, /data-task-cancel="t1"/);
  assert.doesNotMatch(html, /data-task-cancel="t2"/);
  assert.match(html, /已完成/);
});

test("taskDetailMarkup reuses the collapsed process view for steps", () => {
  const html = taskDetailMarkup({
    task_id: "t1",
    title: "盘点",
    prompt: "帮我盘点收藏",
    status: "completed",
    report: "结论",
    suggestions: [{ action: "save_item", summary: "收藏某视频", payload: {} }],
    steps: [
      { type: "tool_call", step: 1, tool_name: "get_watch_history", arguments: {}, summary: "看历史" },
      { type: "tool_result", step: 1, tool_name: "get_watch_history", text: "…", ok: true },
      { type: "final", step: 2, text: "结论" },
    ],
  });
  assert.match(html, /过程（1 步）/);
  assert.match(html, /结论/);
  assert.match(html, /确认执行/);
});

test("approvalsPanelMarkup renders pending approval cards", () => {
  const html = approvalsPanelMarkup([
    { approval_id: "ap_1", tool_name: "update_config", summary: "改并发数", impact: "立即生效", arguments: { key: "x" }, status: "pending" },
  ]);
  assert.match(html, /需要你的批准/);
  assert.match(html, /data-approval-action="approve"/);
  assert.match(html, /影响：立即生效/);
});

// ── 异步审批执行（approve 只入队） ─────────────────────────────

test("approvalCardMarkup renders executing state without action buttons", () => {
  const html = approvalCardMarkup({
    approvalId: "ap_1",
    summary: "修改配置",
    impact: "热重载",
    status: "executing",
    decision: "",
    ok: null,
    resultText: "",
  });
  assert.match(html, /执行中…/);
  assert.doesNotMatch(html, /data-approval-action=/);
  // 进行中不是失败态，不刷失败色。
  assert.doesNotMatch(html, /agent-approval-status is-failed/);
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
  assert.deepEqual(first, {
    kind: "queued",
    alreadyQueued: false,
    approval: {
      approvalId: "ap_1",
      summary: "",
      impact: "",
      status: "executing",
      decision: "",
      ok: null,
      resultText: "",
    },
  });

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

test("applyApprovalRecordToProcess recovers executing and settles terminal states", () => {
  const model = buildAgentProcess([
    { type: "approval_request", step: 1, approval_id: "ap_1", tool_name: "update_config", arguments: {}, summary: "改配置", impact: "" },
    { type: "final", step: 2, text: "等你批准" },
  ]);
  const approval = model.steps[0].toolCalls[0].approval;
  assert.equal(approval.status, "pending");

  // 刷新恢复：后端 executing 列表把回放里的 pending 卡标成执行中。
  applyApprovalRecordToProcess(model, { approval_id: "ap_1", status: "executing" });
  assert.equal(approval.status, "executing");

  // 轮询到终态：写入结果摘要。
  applyApprovalRecordToProcess(model, { approval_id: "ap_1", status: "executed", result: "已写入" });
  assert.equal(approval.status, "executed");
  assert.equal(approval.ok, true);
  assert.equal(approval.resultText, "已写入");

  // 已终态的回放不被过期快照降级。
  applyApprovalRecordToProcess(model, { approval_id: "ap_1", status: "executing" });
  assert.equal(approval.status, "executed");

  // 未命中 id / 非记录输入安全返回 null。
  assert.equal(applyApprovalRecordToProcess(model, { approval_id: "ap_x", status: "executing" }), null);
  assert.equal(applyApprovalRecordToProcess(model, null), null);
  assert.equal(isApprovalTerminalStatus("failed"), true);
  assert.equal(isApprovalTerminalStatus("executing"), false);
});

test("approvalsPanelMarkup renders executing records without action buttons", () => {
  const html = approvalsPanelMarkup([
    { approval_id: "ap_9", tool_name: "update_config", summary: "改并发数", arguments: {}, status: "executing" },
  ]);
  assert.match(html, /执行中…/);
  assert.doesNotMatch(html, /data-approval-action=/);
});

test("skillPickerMarkup marks current and default skills", () => {
  const html = skillPickerMarkup(
    [
      { name: "taste-companion", title: "口味伙伴", description: "默认", default: true, builtin: true },
      { name: "custom-one", title: "自定义", description: "d", default: false, builtin: false },
    ],
    "custom-one",
  );
  assert.match(html, /data-skill-pick="custom-one"/);
  assert.match(html, /is-active/);
  assert.match(html, /chat-skill-custom/);
});

test("turnAgentEvents reads payload.agent_events defensively", () => {
  assert.deepEqual(turnAgentEvents({ payload: { agent_events: [{ type: "final" }] } }), [{ type: "final" }]);
  assert.deepEqual(turnAgentEvents({ payload: null }), []);
  assert.deepEqual(turnAgentEvents(null), []);
});
