/* 桌面 Web「聊一聊」agent loop 前端核心（M8）。
 * 纯逻辑层：SSE 解析、agent 事件 → 过程视图模型、全部卡片/列表 markup 生成。
 * 不碰 DOM、不发请求，方便 node:test 直接断言；DOM 胶水在 app.js。 */
(function (global) {
  "use strict";

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function text(value) {
    return typeof value === "string" ? value : value == null ? "" : String(value);
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function prettyJson(value) {
    if (typeof value === "string") {
      try {
        return JSON.stringify(JSON.parse(value), null, 2);
      } catch {
        return value;
      }
    }
    try {
      return JSON.stringify(value ?? {}, null, 2);
    } catch {
      return text(value);
    }
  }

  // ── SSE 增量解析 ─────────────────────────────────────────────
  // POST /api/chat/agent/stream 的 event 名 = 事件 type，data 是单行 JSON。
  // 解析器容忍 CRLF、多行 data、注释行与粘包/拆包。

  function createSseParser(onEvent) {
    let buffer = "";
    let eventName = "";
    let dataLines = [];

    const dispatch = () => {
      if (!eventName && !dataLines.length) return;
      const raw = dataLines.join("\n");
      const name = eventName || "message";
      eventName = "";
      dataLines = [];
      if (!raw) return;
      let parsed = raw;
      try {
        parsed = JSON.parse(raw);
      } catch {
        // 保留原始字符串，调用方自行容错。
      }
      onEvent(name, parsed);
    };

    const handleLine = (line) => {
      if (line.endsWith("\r")) line = line.slice(0, -1);
      if (line === "") {
        dispatch();
        return;
      }
      if (line.startsWith(":")) return;
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    };

    return {
      feed(chunk) {
        buffer += text(chunk);
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) handleLine(line);
      },
      end() {
        if (buffer) {
          handleLine(buffer);
          buffer = "";
        }
        dispatch();
      },
    };
  }

  // ── SSE 读看门狗 ─────────────────────────────────────────────
  // fetch 流被代理空闲回收 / NAT 断流 / 系统挂起静默杀掉时 reader.read()
  // 既不 resolve 也不 reject，看门狗在 timeoutMs 无字节后 abort 请求，
  // 让 Promise 按既有错误路径落定。服务端心跳注释行（``: ping``）也算字节，
  // 会喂活看门狗。

  const SSE_READ_WATCHDOG_MS = 60_000;

  function createSseReadWatchdog({ timeoutMs = SSE_READ_WATCHDOG_MS } = {}) {
    const controller = new AbortController();
    let timer = null;
    const reset = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (!(timeoutMs > 0) || controller.signal.aborted) return;
      timer = setTimeout(() => {
        timer = null;
        if (!controller.signal.aborted) {
          controller.abort(new Error("SSE 读取超时：连接可能已中断"));
        }
      }, timeoutMs);
    };
    return {
      signal: controller.signal,
      reset,
      cancel() {
        if (timer !== null) clearTimeout(timer);
        timer = null;
      },
    };
  }

  // ── agent 事件 → 过程视图模型 ────────────────────────────────
  // 模型形状：
  // {
  //   steps: [{ step, thinking, toolCalls: [{
  //     toolName, summary, argumentsText, special,
  //     skill, reason, prompt, taskTitle, taskSkill,
  //     result: null | { text, ok, truncated },
  //     approval: null | { approvalId, summary, impact, status, decision, ok, resultText },
  //   }] }],
  //   stepLimitText, finalText, errorText,
  // }

  const SPECIAL_TOOL_SUGGEST_SKILL = "suggest_skill";
  const SPECIAL_TOOL_START_TASK = "start_background_task";

  function createAgentProcess() {
    return { steps: [], stepLimitText: "", finalText: "", errorText: "" };
  }

  function processStepCount(model) {
    return (model?.steps || []).reduce(
      (count, step) => count + Math.max(step.toolCalls.length, step.thinking ? 1 : 0, 1),
      0,
    );
  }

  function findStep(model, stepNumber) {
    const step = Math.max(1, Number(stepNumber) || 1);
    let bucket = model.steps.find((item) => item.step === step);
    if (!bucket) {
      bucket = { step, thinking: "", toolCalls: [] };
      model.steps.push(bucket);
      model.steps.sort((left, right) => left.step - right.step);
    }
    return bucket;
  }

  function normalizeToolArguments(value) {
    if (isRecord(value)) return value;
    if (typeof value === "string" && value.trim()) {
      try {
        const parsed = JSON.parse(value);
        if (isRecord(parsed)) return parsed;
      } catch {
        // fall through
      }
    }
    return {};
  }

  function applyAgentEvent(model, event) {
    if (!isRecord(model) || !isRecord(event)) return model;
    const type = text(event.type);
    if (type === "thinking") {
      const bucket = findStep(model, event.step);
      const chunk = text(event.text);
      if (chunk) bucket.thinking = bucket.thinking ? `${bucket.thinking}${chunk}` : chunk;
    } else if (type === "tool_call") {
      const bucket = findStep(model, event.step);
      const args = normalizeToolArguments(event.arguments);
      const toolName = text(event.tool_name) || "tool";
      bucket.toolCalls.push({
        toolName,
        summary: text(event.summary) || `${toolName}()`,
        argumentsText: prettyJson(args),
        special:
          toolName === SPECIAL_TOOL_SUGGEST_SKILL
            ? "suggest_skill"
            : toolName === SPECIAL_TOOL_START_TASK
              ? "start_background_task"
              : "",
        skill: text(args.skill),
        reason: text(args.reason),
        prompt: text(args.prompt),
        taskTitle: text(args.title),
        taskSkill: text(args.skill),
        result: null,
        approval: null,
      });
    } else if (type === "tool_result") {
      const bucket = findStep(model, event.step);
      const toolName = text(event.tool_name);
      const call =
        [...bucket.toolCalls].reverse().find(
          (item) => item.toolName === toolName && item.result === null,
        ) || [...bucket.toolCalls].reverse().find((item) => item.result === null);
      const result = {
        text: text(event.text),
        ok: event.ok !== false,
        truncated: event.truncated === true,
      };
      if (call) call.result = result;
      else {
        bucket.toolCalls.push({
          toolName: toolName || "tool",
          summary: `${toolName || "tool"}()`,
          argumentsText: "",
          special: "",
          skill: "",
          reason: "",
          prompt: "",
          taskTitle: "",
          taskSkill: "",
          result,
          approval: null,
        });
      }
    } else if (type === "approval_request") {
      const bucket = findStep(model, event.step);
      const toolName = text(event.tool_name);
      const call =
        [...bucket.toolCalls].reverse().find(
          (item) => item.toolName === toolName && !item.approval,
        ) || null;
      const approval = {
        approvalId: text(event.approval_id),
        summary: text(event.summary),
        impact: text(event.impact),
        status: "pending",
        decision: "",
        ok: null,
        resultText: "",
      };
      if (call) call.approval = approval;
      else {
        bucket.toolCalls.push({
          toolName: toolName || "tool",
          summary: text(event.summary) || `${toolName || "tool"}()`,
          argumentsText: prettyJson(normalizeToolArguments(event.arguments)),
          special: "",
          skill: "",
          reason: "",
          prompt: "",
          taskTitle: "",
          taskSkill: "",
          result: null,
          approval,
        });
      }
    } else if (type === "approval_result") {
      // 回放事件：审批决策后由服务端追加进 turn 的 agent_events，无 step 字段，
      // 按 approval_id 找回原审批步骤。
      const approvalId = text(event.approval_id);
      for (const step of model.steps) {
        for (const call of step.toolCalls) {
          if (call.approval && call.approval.approvalId === approvalId) {
            call.approval.decision = text(event.decision);
            call.approval.status =
              call.approval.decision === "approved"
                ? event.ok === false
                  ? "failed"
                  : "executed"
                : "rejected";
            call.approval.ok = event.ok !== false;
            call.approval.resultText = text(event.text);
          }
        }
      }
    } else if (type === "step_limit_reached") {
      model.stepLimitText = text(event.text);
    } else if (type === "final") {
      model.finalText = text(event.text);
    } else if (type === "error") {
      model.errorText = text(event.error) || "这轮对话出错了，请稍后重试。";
    }
    return model;
  }

  function buildAgentProcess(events) {
    const model = createAgentProcess();
    for (const event of Array.isArray(events) ? events : []) applyAgentEvent(model, event);
    return model;
  }

  function turnAgentEvents(turn) {
    const payload = isRecord(turn?.payload) ? turn.payload : {};
    return Array.isArray(payload.agent_events) ? payload.agent_events : [];
  }

  function isAgentTaskSummaryTurn(turn) {
    return isRecord(turn?.payload) && turn.payload.type === "agent_task_summary";
  }

  // ── 过程视图 markup ──────────────────────────────────────────

  function toolCallSummaryMarkup(call) {
    const dotClass = call.result
      ? call.result.ok
        ? "is-ok"
        : "is-failed"
      : call.approval
        ? "is-pending"
        : "is-running";
    return `<span class="agent-tool-dot ${dotClass}" aria-hidden="true"></span><span class="agent-tool-summary">${escapeHtml(call.summary)}</span>`;
  }

  function approvalCardMarkup(approval, options = {}) {
    const status = text(approval.status) || "pending";
    const decided = status !== "pending";
    const statusLabel =
      {
        executed: approval.ok === false ? "已批准，但执行失败" : "已批准并执行",
        failed: "已批准，但执行失败",
        approved: "已批准，执行中…",
        executing: "执行中…",
        rejected: "已拒绝",
        expired: "已过期",
      }[status] || status;
    const statusClass =
      status === "rejected" ? "is-rejected" : status === "executed" && approval.ok !== false ? "is-ok" : status === "pending" || status === "approved" || status === "executing" ? "" : "is-failed";
    const actions = decided
      ? `<p class="agent-approval-status ${statusClass}" role="status">${escapeHtml(statusLabel)}</p>${
          approval.resultText
            ? `<details class="agent-tool-detail"><summary>执行结果</summary><pre>${escapeHtml(approval.resultText)}</pre></details>`
            : ""
        }`
      : `<div class="agent-approval-actions"><button type="button" class="pill-btn primary" data-approval-action="approve">批准并执行</button><button type="button" class="pill-btn" data-approval-action="reject">拒绝</button></div>${
          options.rejecting
            ? `<div class="agent-approval-reject"><input type="text" class="agent-approval-reason" placeholder="拒绝原因（可选）" aria-label="拒绝原因"><button type="button" class="pill-btn" data-approval-action="confirm-reject">确认拒绝</button></div>`
            : ""
        }`;
    return `<div class="agent-approval-card" data-agent-approval-id="${escapeHtml(approval.approvalId)}"><p class="agent-card-kicker">需要你的批准</p><h4 class="agent-card-title">${escapeHtml(approval.summary || "一项修改操作")}</h4>${
      approval.impact ? `<p class="agent-approval-impact">影响：${escapeHtml(approval.impact)}</p>` : ""
    }${options.argumentsText ? `<details class="agent-tool-detail"><summary>参数</summary><pre>${escapeHtml(options.argumentsText)}</pre></details>` : ""}${actions}</div>`;
  }

  function skillSuggestCardMarkup(call) {
    return `<div class="agent-skill-card" data-suggest-skill="${escapeHtml(call.skill)}"><p class="agent-card-kicker">阿B 建议切换角色</p><h4 class="agent-card-title">切换到「${escapeHtml(call.skillTitle || call.skill)}」</h4>${
      call.reason ? `<p class="agent-card-note">${escapeHtml(call.reason)}</p>` : ""
    }<div class="agent-card-actions"><button type="button" class="pill-btn primary" data-skill-action="accept">切换</button><button type="button" class="pill-btn" data-skill-action="dismiss">忽略</button></div></div>`;
  }

  function backgroundTaskCardMarkup(call, options = {}) {
    const payload = escapeHtml(
      JSON.stringify({ prompt: call.prompt, title: call.taskTitle, skill: call.taskSkill }),
    );
    const started = options.started === true;
    return `<div class="agent-task-card" data-bg-task="${payload}"><p class="agent-card-kicker">阿B 提议在后台执行</p><h4 class="agent-card-title">${escapeHtml(call.taskTitle || call.prompt.slice(0, 40) || "后台任务")}</h4><p class="agent-card-note">${escapeHtml(call.prompt)}</p>${
      started
        ? `<p class="agent-card-note" role="status">已发起，可在任务中心查看进度。</p>`
        : `<div class="agent-card-actions"><button type="button" class="pill-btn primary" data-bg-task-action="accept">确认开始</button><button type="button" class="pill-btn" data-bg-task-action="dismiss">忽略</button></div>`
    }</div>`;
  }

  function toolCallMarkup(call) {
    if (call.special === "suggest_skill") return skillSuggestCardMarkup(call);
    if (call.special === "start_background_task") return backgroundTaskCardMarkup(call);
    const detailRows = [];
    if (call.approval) {
      return approvalCardMarkup(call.approval, { argumentsText: call.argumentsText });
    }
    if (call.argumentsText && call.argumentsText !== "{}") {
      detailRows.push(`<p class="agent-tool-label">参数</p><pre>${escapeHtml(call.argumentsText)}</pre>`);
    }
    if (call.result) {
      detailRows.push(
        `<p class="agent-tool-label">结果${call.result.truncated ? "（已截断）" : ""}</p><pre>${escapeHtml(call.result.text || (call.result.ok ? "（无输出）" : "（失败）"))}</pre>`,
      );
    }
    const detail = detailRows.length
      ? `<div class="agent-tool-detail">${detailRows.join("")}</div>`
      : "";
    return `<details class="agent-tool"><summary>${toolCallSummaryMarkup(call)}</summary>${detail}</details>`;
  }

  // 过程折叠组件：进行中（live=true）固定展开；完成后默认折叠成「过程（N 步）」。
  function agentProcessMarkup(model, options = {}) {
    if (!isRecord(model)) return "";
    const steps = Array.isArray(model.steps) ? model.steps : [];
    const count = processStepCount(model);
    const live = options.live === true;
    if (!steps.length && !model.stepLimitText && !model.errorText) return "";
    const rows = steps
      .map((step) => {
        const parts = [];
        if (step.thinking) {
          parts.push(`<p class="agent-thinking">${escapeHtml(step.thinking)}</p>`);
        }
        for (const call of step.toolCalls) parts.push(toolCallMarkup(call));
        return parts.length ? `<li class="agent-step">${parts.join("")}</li>` : "";
      })
      .filter(Boolean)
      .join("");
    const limitNote = model.stepLimitText
      ? `<p class="agent-step-limit" role="status">${escapeHtml(model.stepLimitText)}</p>`
      : "";
    const errorNote = model.errorText
      ? `<p class="agent-process-error" role="alert">${escapeHtml(model.errorText)}</p>`
      : "";
    const summaryLabel = live
      ? `进行中…（${count} 步）`
      : `过程（${count} 步）`;
    const tag = live ? "div" : "details";
    const openAttr = live || options.expanded ? " open" : "";
    return `<${tag} class="agent-process${live ? " is-live" : ""}"${tag === "details" ? openAttr : ""}><summary class="agent-process-summary"><span class="agent-process-icon" aria-hidden="true">${live ? "⏳" : "⚙️"}</span>${escapeHtml(summaryLabel)}</summary><ol class="agent-process-steps">${rows}</ol>${limitNote}${errorNote}</${tag}>`;
  }

  // ── 任务汇总卡（agent_task_summary turn） ────────────────────

  const SUGGESTION_SOFT_WRITE = new Set(["write_memory", "submit_feedback", "save_item"]);

  function isSoftWriteSuggestion(action) {
    return SUGGESTION_SOFT_WRITE.has(text(action));
  }

  function suggestionConfirmLabel(action) {
    return isSoftWriteSuggestion(action) ? "确认执行" : "去对话确认";
  }

  function suggestionListMarkup(suggestions, options = {}) {
    const list = Array.isArray(suggestions) ? suggestions : [];
    if (!list.length) return "";
    const source = text(options.source || "");
    return `<ul class="agent-suggestion-list">${list
      .map((item, index) => {
        if (!isRecord(item)) return "";
        const handled = options.handled instanceof Set && options.handled.has(`${source}:${index}`);
        return `<li class="agent-suggestion" data-suggestion-index="${index}" data-suggestion-source="${escapeHtml(source)}"><span class="agent-suggestion-action">${escapeHtml(text(item.action))}</span><span class="agent-suggestion-summary">${escapeHtml(text(item.summary))}</span>${
          handled
            ? `<span class="agent-suggestion-done">已提交</span>`
            : `<button type="button" class="pill-btn" data-suggestion-action="confirm">${escapeHtml(suggestionConfirmLabel(item.action))}</button>`
        }</li>`;
      })
      .join("")}</ul>`;
  }

  function taskSummaryCardMarkup(turn, options = {}) {
    const payload = isRecord(turn?.payload) ? turn.payload : {};
    const failed = text(payload.task_status) === "failed";
    const title = text(turn.message).replace(/^\[后台任务(完成|失败)\]\s*/, "") || "后台任务";
    const renderMarkdown = typeof options.renderMarkdown === "function" ? options.renderMarkdown : null;
    const report = text(turn.reply);
    const reportHtml = renderMarkdown
      ? `<div class="chat-markdown agent-task-report">${renderMarkdown(report)}</div>`
      : `<p class="agent-task-report">${escapeHtml(report)}</p>`;
    return `<article class="agent-task-summary${failed ? " is-failed" : ""}" data-dialogue-turn-id="${escapeHtml(turn.turn_id)}" data-task-id="${escapeHtml(text(payload.task_id))}"><p class="agent-card-kicker">后台任务${failed ? "失败" : "完成"}</p><h4 class="agent-card-title">${escapeHtml(title)}</h4>${reportHtml}${suggestionListMarkup(payload.suggestions, { source: text(payload.task_id), handled: options.handledSuggestions })}<div class="agent-card-actions"><button type="button" class="pill-btn" data-task-open="${escapeHtml(text(payload.task_id))}">查看执行记录</button></div></article>`;
  }

  // ── 会话 / 任务中心 / 审批列表 / skill 选择 markup ──────────

  function sessionListMarkup(sessions, currentId, options = {}) {
    const list = Array.isArray(sessions) ? sessions : [];
    if (!list.length) {
      return '<p class="chat-session-empty">还没有会话，点「新会话」开始。</p>';
    }
    return list
      .map((session) => {
        if (!isRecord(session)) return "";
        const id = text(session.session_id);
        const isDefault = id === "default";
        const active = id === currentId;
        const title = text(session.title) || (isDefault ? "默认会话" : "未命名会话");
        const preview = text(session.last_message_preview);
        const activeTurns = Math.max(0, Number(session.active_turns) || 0);
        const archived = session.archived === true;
        return `<div class="chat-session-item${active ? " is-active" : ""}${archived ? " is-archived" : ""}" role="listitem" data-session-id="${escapeHtml(id)}"><button type="button" class="chat-session-main" data-session-action="switch"><span class="chat-session-title-row"><strong>${escapeHtml(title)}</strong>${isDefault ? '<span class="chat-session-default">默认</span>' : ""}${activeTurns > 0 ? '<span class="chat-session-live" title="有回复正在进行"></span>' : ""}</span>${preview ? `<span class="chat-session-preview">${escapeHtml(preview)}</span>` : ""}</button><span class="chat-session-actions"><button type="button" data-session-action="rename" title="重命名">改名</button>${isDefault ? "" : `<button type="button" data-session-action="archive" title="${archived ? "取消归档" : "归档"}">${archived ? "恢复" : "归档"}</button>`}</span></div>`;
      })
      .join("");
  }

  const TASK_STATUS_LABELS = {
    pending: "排队中",
    running: "进行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  };

  function taskStatusLabel(status) {
    return TASK_STATUS_LABELS[text(status)] || text(status) || "未知";
  }

  function taskIsActive(task) {
    return ["pending", "running"].includes(text(task?.status));
  }

  function taskListMarkup(tasks) {
    const list = Array.isArray(tasks) ? tasks : [];
    if (!list.length) return '<p class="chat-tasks-empty">还没有后台任务。</p>';
    return list
      .map((task) => {
        if (!isRecord(task)) return "";
        const id = text(task.task_id);
        const title = text(task.title) || text(task.prompt).slice(0, 40) || "后台任务";
        const status = text(task.status);
        const active = taskIsActive(task);
        return `<div class="chat-task-item" data-task-id="${escapeHtml(id)}"><button type="button" class="chat-task-main" data-task-open="${escapeHtml(id)}"><span class="chat-task-title-row"><strong>${escapeHtml(title)}</strong><span class="chat-task-status is-${escapeHtml(status)}">${escapeHtml(taskStatusLabel(status))}</span></span>${active && text(task.progress) ? `<span class="chat-task-progress">${escapeHtml(text(task.progress))}</span>` : ""}</button>${active ? `<button type="button" class="pill-btn chat-task-cancel" data-task-cancel="${escapeHtml(id)}">取消</button>` : ""}</div>`;
      })
      .join("");
  }

  function taskDetailMarkup(task, options = {}) {
    if (!isRecord(task)) return "";
    const steps = Array.isArray(task.steps) ? task.steps : [];
    const process = buildAgentProcess(steps);
    const active = taskIsActive(task);
    const suggestions = Array.isArray(task.suggestions) ? task.suggestions : [];
    const renderMarkdown = typeof options.renderMarkdown === "function" ? options.renderMarkdown : null;
    return `<div class="chat-task-detail" data-task-id="${escapeHtml(text(task.task_id))}"><div class="chat-task-detail-head"><button type="button" class="pill-btn" data-task-back>← 返回列表</button><span class="chat-task-status is-${escapeHtml(text(task.status))}">${escapeHtml(taskStatusLabel(task.status))}</span></div><h4 class="agent-card-title">${escapeHtml(text(task.title) || text(task.prompt).slice(0, 40))}</h4><p class="agent-card-note">${escapeHtml(text(task.prompt))}</p>${active ? `<button type="button" class="pill-btn" data-task-cancel="${escapeHtml(text(task.task_id))}">取消任务</button>` : ""}${agentProcessMarkup(process, { live: active })}${
      text(task.report)
        ? renderMarkdown
          ? `<div class="chat-markdown agent-task-report">${renderMarkdown(text(task.report))}</div>`
          : `<p class="agent-task-report">${escapeHtml(text(task.report))}</p>`
        : ""
    }${text(task.error) ? `<p class="agent-process-error">${escapeHtml(text(task.error))}</p>` : ""}${suggestionListMarkup(suggestions, { source: text(task.task_id), handled: options.handledSuggestions })}</div>`;
  }

  const APPROVAL_TERMINAL_STATUSES = new Set(["executed", "failed", "rejected", "expired"]);

  function isApprovalTerminalStatus(status) {
    return APPROVAL_TERMINAL_STATUSES.has(text(status));
  }

  /** GET /api/chat/approvals 记录 → 审批卡模型。 */
  function approvalCardModelFromRecord(item) {
    if (!isRecord(item)) return null;
    const status = text(item.status) || "pending";
    return {
      approvalId: text(item.approval_id),
      summary: text(item.summary),
      impact: text(item.impact),
      status,
      decision: "",
      ok: status === "failed" ? false : status === "executed" ? true : null,
      resultText: text(item.result || item.error),
    };
  }

  /**
   * POST /api/chat/approvals/{id}/approve 响应归类。
   * 新协议（异步执行）：响应带 queued 字段，只表示已入队（executing），
   * 终态靠轮询 GET /api/chat/approvals；already_executed 为幂等终态应答。
   * 旧协议（同步执行）：响应直接带 ok/result、没有 queued 字段。
   */
  function normalizeApproveResponse(payload) {
    const record = isRecord(payload) ? payload : {};
    const approval = approvalCardModelFromRecord(record.approval);
    if (!("queued" in record) || record.already_executed === true) {
      return {
        kind: "settled",
        ok: record.ok !== false,
        resultText: text(record.result || approval?.resultText),
        approval,
      };
    }
    return { kind: "queued", alreadyQueued: record.already_queued === true, approval };
  }

  /**
   * 把一条 approvals 列表记录合并进过程模型（轮询跟踪 + 回放恢复 executing
   * 中间态）。已终态的回放（approval_result 事件）不被过期的列表快照降级。
   */
  function applyApprovalRecordToProcess(model, item) {
    const record = approvalCardModelFromRecord(item);
    if (!record || !record.approvalId || !isRecord(model)) return null;
    for (const step of model.steps || []) {
      for (const call of step.toolCalls || []) {
        if (call.approval && call.approval.approvalId === record.approvalId) {
          if (!isApprovalTerminalStatus(call.approval.status)) {
            call.approval.status = record.status;
            if (record.resultText) call.approval.resultText = record.resultText;
            if (record.ok !== null) call.approval.ok = record.ok;
          }
          return call.approval;
        }
      }
    }
    return null;
  }

  function approvalsPanelMarkup(approvals) {
    const list = Array.isArray(approvals) ? approvals : [];
    if (!list.length) return '<p class="chat-approvals-empty">没有待批准的改动。</p>';
    return list
      .map((item) => {
        const model = approvalCardModelFromRecord(item);
        if (!model) return "";
        return approvalCardMarkup(model, { argumentsText: prettyJson(item.arguments) });
      })
      .join("");
  }

  function skillPickerMarkup(skills, currentName) {
    const list = Array.isArray(skills) ? skills : [];
    if (!list.length) return '<p class="chat-skill-empty">没有可用的角色。</p>';
    return list
      .map((skill) => {
        if (!isRecord(skill)) return "";
        const name = text(skill.name);
        const active = name === currentName || (!currentName && skill.default === true);
        return `<button type="button" class="chat-skill-option${active ? " is-active" : ""}" data-skill-pick="${escapeHtml(name)}"><span class="chat-skill-option-head"><strong>${escapeHtml(text(skill.title) || name)}</strong>${skill.default === true ? '<span class="chat-session-default">默认</span>' : ""}${skill.builtin === false ? '<span class="chat-skill-custom">自定义</span>' : ""}</span><span class="chat-skill-option-desc">${escapeHtml(text(skill.description))}</span></button>`;
      })
      .join("");
  }

  const SKILL_ICONS = {
    "taste-companion": "🧭",
    "taste-explorer": "🔍",
    "bangumi-advisor": "📺",
    "system-steward": "🛠️",
  };

  function skillIcon(name) {
    return SKILL_ICONS[text(name)] || "💬";
  }

  const api = {
    SPECIAL_TOOL_START_TASK,
    SPECIAL_TOOL_SUGGEST_SKILL,
    agentProcessMarkup,
    applyAgentEvent,
    applyApprovalRecordToProcess,
    approvalCardMarkup,
    approvalCardModelFromRecord,
    approvalsPanelMarkup,
    backgroundTaskCardMarkup,
    buildAgentProcess,
    createAgentProcess,
    createSseParser,
    createSseReadWatchdog,
    SSE_READ_WATCHDOG_MS,
    escapeHtml,
    isAgentTaskSummaryTurn,
    isApprovalTerminalStatus,
    isSoftWriteSuggestion,
    normalizeApproveResponse,
    prettyJson,
    processStepCount,
    sessionListMarkup,
    skillIcon,
    skillPickerMarkup,
    skillSuggestCardMarkup,
    suggestionConfirmLabel,
    suggestionListMarkup,
    taskDetailMarkup,
    taskIsActive,
    taskListMarkup,
    taskStatusLabel,
    taskSummaryCardMarkup,
    turnAgentEvents,
  };
  global.OpenBiliClawChatAgentCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
