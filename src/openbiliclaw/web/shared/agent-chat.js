/**
 * Agent chat loop shared helpers (「聊一聊」 M9).
 *
 * Classic script: installs ``globalThis.OpenBiliClawAgentChat`` so the mobile
 * web page (``/shared/agent-chat.js``), the desktop page and the extension
 * popup (copied to ``extension/popup/shared/`` by scripts/build.mjs) all share
 * one implementation of:
 *
 * - incremental SSE parsing for ``POST /api/chat/agent/stream``
 *   (``event: <type>\ndata: <json>\n\n`` frames, event name = AgentEvent type).
 *   The backend also emits ``delta`` frames (``{type, step, text}``) with
 *   incremental reply fragments; they are intentionally NOT reduced into
 *   the run model (history replay rebuilds text from ``thinking`` /
 *   ``final``) — each frontend renders them live on its own. Unknown event
 *   names are ignored by ``applyAgentEvent``, so old frontends tolerate
 *   the new frames;
 * - reducing the event stream (or a persisted ``payload.agent_events`` array)
 *   into a "process flow" run model (steps / tool calls / approvals /
 *   skill-switch suggestions / background-task proposals / final / error);
 * - markup rendering for the process flow, approval cards, skill-switch cards,
 *   background-task confirm cards, task-center rows and task-summary cards.
 *
 * Rendering is markup-only; interactive wiring happens in each frontend via
 * click delegation on the documented ``data-*`` hooks:
 *
 * - ``[data-agent-approval-action="approve|reject|reject-cancel|reject-submit"]``
 *   inside ``[data-approval-id]``; reject reason is read from
 *   ``input.agent-approval-reason`` inside the same card.
 * - ``[data-agent-skill-switch]`` (value = skill name) / ``[data-agent-skill-dismiss]``.
 * - ``[data-agent-task-confirm]`` inside ``[data-agent-task-proposal]``
 *   (prompt/title/skill carried as ``data-task-prompt`` etc.).
 * - ``[data-agent-task-open]`` / ``[data-agent-task-cancel]`` (``data-task-id``).
 * - ``[data-agent-suggestion-use]`` (summary carried as ``data-summary``).
 */
(function installAgentChat(global) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function plainTextMarkup(value) {
    return escapeHtml(value).replace(/\n/g, "<br>");
  }

  // ── SSE parsing ──────────────────────────────────────────────
  /**
   * Incremental parser for ``text/event-stream`` bodies. Feed decoded text
   * chunks to ``push()``; each complete ``event:``/``data:`` frame with a JSON
   * object payload is delivered to ``onEvent(eventName, data)``. Malformed
   * frames are dropped so one bad line never kills the stream.
   */
  function createAgentSseParser(onEvent) {
    const emit = typeof onEvent === "function" ? onEvent : () => {};
    let buffer = "";
    let eventName = "";
    let dataLines = [];

    function dispatchFrame() {
      const name = eventName;
      const lines = dataLines;
      eventName = "";
      dataLines = [];
      if (!name || lines.length === 0) return;
      let data = null;
      try {
        data = JSON.parse(lines.join("\n"));
      } catch {
        data = null;
      }
      if (data && typeof data === "object") emit(name, data);
    }

    function processLine(rawLine) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line === "") {
        dispatchFrame();
        return;
      }
      if (line.startsWith(":")) return; // comment / heartbeat
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
        return;
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    }

    return {
      push(chunk) {
        buffer += String(chunk ?? "");
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) processLine(line);
      },
      end() {
        if (buffer) {
          const rest = buffer;
          buffer = "";
          processLine(rest);
        }
        dispatchFrame();
      },
    };
  }

  // ── SSE read watchdog ────────────────────────────────────────
  const SSE_READ_WATCHDOG_MS = 60_000;

  /**
   * Read watchdog for SSE fetch streams. Pass ``signal`` to fetch() and call
   * ``reset()`` around every ``reader.read()``; when no byte arrives within
   * ``timeoutMs`` the request is aborted, so a silently dropped connection
   * (proxy idle timeout, NAT drop, iOS backgrounding) rejects instead of
   * hanging forever. Server heartbeat comment lines (``: ping``) count as
   * bytes and keep the watchdog fed. ``cancel()`` disarms it for good.
   */
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

  // ── Run model (过程流状态) ────────────────────────────────────
  function createAgentRun() {
    return {
      steps: [],
      approvals: [],
      skillSuggestion: null,
      taskProposal: null,
      stepLimitReached: false,
      stepLimitText: "",
      finalText: "",
      done: null,
      error: "",
      settled: false,
    };
  }

  function stepEntry(run, stepNumber) {
    const step = Math.max(1, Math.floor(Number(stepNumber) || 1));
    let entry = run.steps.find((item) => item.step === step);
    if (!entry) {
      entry = { step, thinking: "", items: [] };
      run.steps.push(entry);
      run.steps.sort((a, b) => a.step - b.step);
    }
    return entry;
  }

  function applyAgentEvent(run, type, data = {}) {
    if (!run || !type) return run;
    if (type === "thinking") {
      const entry = stepEntry(run, data.step);
      // One thinking event per intermediate hop; replace rather than append
      // so replaying the same log stays idempotent.
      entry.thinking = String(data.text || "");
      return run;
    }
    if (type === "tool_call") {
      const entry = stepEntry(run, data.step);
      const args = data.arguments && typeof data.arguments === "object" ? data.arguments : {};
      const item = {
        kind: "tool",
        name: String(data.tool_name || "tool"),
        arguments: args,
        summary: String(data.summary || ""),
        status: "running",
        result: "",
        truncated: false,
      };
      entry.items.push(item);
      if (item.name === "suggest_skill") {
        run.skillSuggestion = {
          skill: String(args.skill || ""),
          reason: String(args.reason || ""),
        };
      } else if (item.name === "start_background_task") {
        run.taskProposal = {
          prompt: String(args.prompt || ""),
          title: String(args.title || ""),
          skill: String(args.skill || ""),
        };
      }
      return run;
    }
    if (type === "tool_result") {
      const entry = stepEntry(run, data.step);
      const name = String(data.tool_name || "");
      const item = [...entry.items]
        .reverse()
        .find((candidate) => candidate.kind === "tool" && candidate.name === name
          && candidate.status === "running");
      if (item) {
        item.result = String(data.text || "");
        item.truncated = data.truncated === true;
        item.status = data.ok === false ? "failed" : "ok";
      }
      return run;
    }
    if (type === "approval_request") {
      const approval = {
        approval_id: String(data.approval_id || ""),
        tool_name: String(data.tool_name || ""),
        arguments: data.arguments && typeof data.arguments === "object" ? data.arguments : {},
        summary: String(data.summary || ""),
        reason: String(data.reason || ""),
        impact: String(data.impact || ""),
        status: "pending",
        resultText: "",
      };
      run.approvals.push(approval);
      stepEntry(run, data.step).items.push({ kind: "approval", approval });
      return run;
    }
    if (type === "approval_result") {
      const approval = run.approvals.find(
        (item) => item.approval_id === String(data.approval_id || ""),
      );
      if (approval) {
        const decision = String(data.decision || "");
        if (decision === "approved") {
          approval.status = data.ok === false ? "failed" : "executed";
        } else if (decision) {
          approval.status = "rejected";
        }
        approval.resultText = String(data.text || "");
      }
      return run;
    }
    if (type === "step_limit_reached") {
      run.stepLimitReached = true;
      run.stepLimitText = String(data.text || "");
      return run;
    }
    if (type === "final") {
      // Exactly one per run; history replays persist up to ``final`` (the
      // endpoint-level ``done`` is not part of ``payload.agent_events``), so
      // this is also what marks a replayed run as settled.
      run.finalText = String(data.text || "");
      run.settled = true;
      return run;
    }
    if (type === "done") {
      run.done = {
        reply: String(data.reply || ""),
        turn_id: String(data.turn_id || ""),
        session_id: String(data.session_id || ""),
        skill: String(data.skill || ""),
      };
      if (!run.finalText && run.done.reply) run.finalText = run.done.reply;
      run.settled = true;
      return run;
    }
    if (type === "error") {
      run.error = String(data.error || "对话失败了，请稍后重试。");
      run.settled = true;
      return run;
    }
    return run;
  }

  /** Reduce a persisted ``payload.agent_events`` array into the same run. */
  function agentRunFromEvents(events) {
    const run = createAgentRun();
    for (const event of Array.isArray(events) ? events : []) {
      if (!event || typeof event !== "object") continue;
      applyAgentEvent(run, String(event.type || ""), event);
    }
    return run;
  }

  function agentEventsFromTurn(turn) {
    const payload = turn && typeof turn === "object" ? turn.payload : null;
    const events = payload && typeof payload === "object" ? payload.agent_events : null;
    return Array.isArray(events) ? events : [];
  }

  function agentRunStepCount(run) {
    return Array.isArray(run?.steps) ? run.steps.length : 0;
  }

  /** Keep an open rejection editor (including draft/focus) across polling renders. */
  function captureApprovalDrafts(root) {
    const drafts = [];
    for (const card of root?.querySelectorAll("[data-approval-id], [data-agent-approval-id]") || []) {
      const editor = card.querySelector(".agent-approval-reject");
      const input = editor?.querySelector("input.agent-approval-reason");
      if (!editor || editor.hidden || !input) continue;
      drafts.push({
        id: card.dataset.approvalId || card.dataset.agentApprovalId,
        editor,
        input,
        actionsHidden: card.querySelector(".agent-approval-actions")?.hidden === true,
        focused: input.ownerDocument?.activeElement === input,
      });
    }
    return drafts;
  }

  function restoreApprovalDrafts(root, drafts = []) {
    const cards = [...root?.querySelectorAll("[data-approval-id], [data-agent-approval-id]") || []];
    for (const draft of drafts) {
      const card = cards.find((item) => (item.dataset.approvalId || item.dataset.agentApprovalId) === draft.id);
      if (!card || !card.querySelector('[data-agent-approval-action="approve"], [data-approval-action="approve"]')) continue;
      const actions = card.querySelector(".agent-approval-actions");
      const editor = card.querySelector(".agent-approval-reject");
      if (editor) editor.replaceWith(draft.editor);
      else actions?.after(draft.editor);
      if (actions) actions.hidden = draft.actionsHidden;
      // A live stream may have folded its surrounding process since the user
      // opened this editor. Keep the active decision visible until it settles.
      for (let parent = card.parentElement; parent && parent !== root; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      if (draft.focused) draft.input.focus({ preventScroll: true });
      cards.splice(cards.indexOf(card), 1);
    }
  }

  // ── Skills ───────────────────────────────────────────────────
  function normalizeChatSkill(raw) {
    if (!raw || typeof raw !== "object") return null;
    const name = String(raw.name || "").trim();
    if (!name) return null;
    return {
      name,
      title: String(raw.title || "").trim() || name,
      description: String(raw.description || "").trim(),
      tools: Array.isArray(raw.tools) ? raw.tools.map(String) : [],
      source: raw.source === "custom" ? "custom" : "builtin",
      isDefault: raw.default === true,
    };
  }

  function normalizeChatSkillList(payload) {
    const items = Array.isArray(payload?.skills) ? payload.skills : [];
    return items.map(normalizeChatSkill).filter(Boolean);
  }

  function skillDisplayTitle(name, skills) {
    const found = (Array.isArray(skills) ? skills : []).find((skill) => skill.name === name);
    return found ? found.title : String(name || "") || "口味伙伴";
  }

  // ── Approvals ────────────────────────────────────────────────
  // 状态机 pending → approved → executing → executed / failed（approve 端点
  // 异步执行：响应只表示已入队，终态靠轮询 GET /api/chat/approvals 恢复）。
  const APPROVAL_STATUS_LABELS = {
    pending: "待批准",
    approved: "已批准",
    executing: "执行中…",
    executed: "已批准并执行",
    rejected: "已拒绝",
    expired: "已过期",
    failed: "执行失败",
  };
  const APPROVAL_TERMINAL_STATUSES = new Set(["executed", "failed", "rejected", "expired"]);

  function approvalStatusLabel(status) {
    return APPROVAL_STATUS_LABELS[String(status || "")] || String(status || "未知");
  }

  function isApprovalTerminalStatus(status) {
    return APPROVAL_TERMINAL_STATUSES.has(String(status || ""));
  }

  /** Normalize a GET /api/chat/approvals record into the run approval shape. */
  function normalizeApprovalRecord(record) {
    if (!record || typeof record !== "object") return null;
    const approvalId = String(record.approval_id || "").trim();
    if (!approvalId) return null;
    let status = String(record.status || "pending");
    if (status === "approved") status = "approved";
    if (record.error && (status === "executed" || status === "approved")) status = "failed";
    return {
      approval_id: approvalId,
      tool_name: String(record.tool_name || ""),
      arguments: record.arguments && typeof record.arguments === "object" ? record.arguments : {},
      summary: String(record.summary || ""),
      reason: String(record.reason || ""),
      impact: String(record.impact || ""),
      status,
      resultText: String(record.result || record.error || ""),
      session_id: String(record.session_id || ""),
      created_at: String(record.created_at || ""),
    };
  }

  function normalizeApprovalList(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    return items.map(normalizeApprovalRecord).filter(Boolean);
  }

  /**
   * Classify a ``POST /api/chat/approvals/{id}/approve`` response.
   *
   * New protocol (async execution): the response carries ``queued`` and the
   * record sits in ``executing`` until a background task settles it; the
   * frontend polls ``GET /api/chat/approvals`` for the terminal state.
   * ``already_executed`` responses are the idempotent terminal reply.
   * Legacy backends executed synchronously and returned ``ok``/``result``
   * without a ``queued`` field — treat those as settled right away.
   */
  function normalizeApproveResponse(payload) {
    const record = payload && typeof payload === "object" ? payload : {};
    const approval = normalizeApprovalRecord(record.approval);
    if (!("queued" in record)) {
      return {
        kind: "settled",
        ok: record.ok !== false,
        resultText: String(record.result || approval?.resultText || ""),
        approval,
      };
    }
    if (record.already_executed === true) {
      return {
        kind: "settled",
        ok: record.ok !== false,
        resultText: String(record.result || approval?.resultText || ""),
        approval,
      };
    }
    return { kind: "queued", alreadyQueued: record.already_queued === true, approval };
  }

  /**
   * Merge a ``GET /api/chat/approvals`` record into a run's approval entry
   * (poll tracking + replay recovery of the intermediate ``executing`` state).
   * A terminal replay (``approval_result`` event) is authoritative and never
   * downgraded by a stale list snapshot.
   */
  function applyApprovalRecordToRun(run, record) {
    const normalized = normalizeApprovalRecord(record);
    if (!normalized || !run || !Array.isArray(run.approvals)) return null;
    const approval = run.approvals.find((item) => item.approval_id === normalized.approval_id);
    if (!approval) return null;
    if (isApprovalTerminalStatus(approval.status)) return approval;
    approval.status = normalized.status;
    if (normalized.resultText) approval.resultText = normalized.resultText;
    return approval;
  }

  // ── Tasks ────────────────────────────────────────────────────
  const AGENT_TASK_STATUS_LABELS = {
    pending: "排队中",
    running: "进行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  };
  const AGENT_TASK_ACTIVE_STATUSES = new Set(["pending", "running"]);

  function agentTaskStatusLabel(status) {
    return AGENT_TASK_STATUS_LABELS[String(status || "")] || String(status || "未知");
  }

  function isAgentTaskActive(status) {
    return AGENT_TASK_ACTIVE_STATUSES.has(String(status || ""));
  }

  function normalizeAgentTask(raw) {
    if (!raw || typeof raw !== "object") return null;
    const taskId = String(raw.task_id || "").trim();
    if (!taskId) return null;
    return {
      ...raw,
      task_id: taskId,
      session_id: String(raw.session_id || ""),
      title: String(raw.title || "").trim() || String(raw.prompt || "").slice(0, 40),
      prompt: String(raw.prompt || ""),
      status: String(raw.status || "pending"),
      skill: String(raw.skill || ""),
      progress: String(raw.progress || ""),
      report: String(raw.report || ""),
      suggestions: Array.isArray(raw.suggestions) ? raw.suggestions : [],
      steps: Array.isArray(raw.steps) ? raw.steps : [],
      error: String(raw.error || ""),
    };
  }

  function isAgentTaskSummaryTurn(turn) {
    const payload = turn && typeof turn === "object" ? turn.payload : null;
    return Boolean(payload && typeof payload === "object" && payload.type === "agent_task_summary");
  }

  // ── Markup rendering ─────────────────────────────────────────
  function renderJsonBlock(value) {
    let text = "";
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value ?? "");
    }
    return `<pre class="agent-detail-pre">${escapeHtml(text)}</pre>`;
  }

  function renderApprovalCardMarkup(approval, { compact = false } = {}) {
    if (!approval?.approval_id) return "";
    const status = String(approval.status || "pending");
    const statusLabel = approvalStatusLabel(status);
    const parts = [];
    parts.push(
      `<div class="agent-approval-card${compact ? " is-compact" : ""}" data-approval-id="${escapeHtml(approval.approval_id)}" data-status="${escapeHtml(status)}">`,
    );
    parts.push(
      `<div class="agent-approval-head"><span class="agent-approval-title">需要批准：${escapeHtml(approval.tool_name || "操作")}</span>`,
      `<span class="agent-approval-status" data-tone="${escapeHtml(status)}">${escapeHtml(statusLabel)}</span></div>`,
    );
    if (approval.summary) {
      parts.push(`<p class="agent-approval-summary">${plainTextMarkup(approval.summary)}</p>`);
    }
    if (approval.reason) {
      parts.push(`<p class="agent-approval-reason">理由：${plainTextMarkup(approval.reason)}</p>`);
    }
    if (approval.impact) {
      parts.push(`<p class="agent-approval-impact">影响：${plainTextMarkup(approval.impact)}</p>`);
    }
    if (approval.arguments && Object.keys(approval.arguments).length > 0) {
      parts.push(
        `<details class="agent-approval-args"><summary>参数</summary>${renderJsonBlock(approval.arguments)}</details>`,
      );
    }
    if (approval.resultText && status !== "pending") {
      parts.push(`<p class="agent-approval-result">${plainTextMarkup(approval.resultText)}</p>`);
    }
    if (status === "pending") {
      parts.push('<div class="agent-approval-actions">');
      parts.push(
        `<button type="button" class="agent-btn agent-btn-primary" data-agent-approval-action="approve">批准执行</button>`,
        `<button type="button" class="agent-btn agent-btn-secondary" data-agent-approval-action="reject">拒绝</button>`,
      );
      parts.push("</div>");
      parts.push(
        '<div class="agent-approval-reject" hidden>',
        '<input type="text" class="agent-approval-reason" placeholder="拒绝原因（可选）" maxlength="200">',
        '<div class="agent-approval-actions">',
        '<button type="button" class="agent-btn agent-btn-secondary" data-agent-approval-action="reject-submit">确认拒绝</button>',
        '<button type="button" class="agent-btn agent-btn-ghost" data-agent-approval-action="reject-cancel">取消</button>',
        "</div></div>",
      );
    }
    parts.push("</div>");
    return parts.join("");
  }

  function renderSkillSuggestionMarkup(suggestion) {
    if (!suggestion?.skill) return "";
    return [
      `<div class="agent-skill-card" data-skill="${escapeHtml(suggestion.skill)}">`,
      `<p class="agent-skill-card-text">阿B 建议切换到「${escapeHtml(suggestion.skill)}」`,
      suggestion.reason ? `：${plainTextMarkup(suggestion.reason)}` : "",
      "</p>",
      '<div class="agent-approval-actions">',
      `<button type="button" class="agent-btn agent-btn-primary" data-agent-skill-switch="${escapeHtml(suggestion.skill)}">切换</button>`,
      '<button type="button" class="agent-btn agent-btn-ghost" data-agent-skill-dismiss>先不换</button>',
      "</div></div>",
    ].join("");
  }

  function renderTaskProposalMarkup(proposal) {
    if (!proposal?.prompt) return "";
    return [
      `<div class="agent-task-proposal" data-agent-task-proposal`,
      ` data-task-prompt="${escapeHtml(proposal.prompt)}"`,
      ` data-task-title="${escapeHtml(proposal.title || "")}"`,
      ` data-task-skill="${escapeHtml(proposal.skill || "")}">`,
      `<p class="agent-task-proposal-text">建议转成后台任务${proposal.title ? `「${escapeHtml(proposal.title)}」` : ""}，完成后会把结果和建议带回这里。</p>`,
      `<p class="agent-task-proposal-prompt">${plainTextMarkup(proposal.prompt)}</p>`,
      '<div class="agent-approval-actions">',
      '<button type="button" class="agent-btn agent-btn-primary" data-agent-task-confirm>确认发起</button>',
      '<button type="button" class="agent-btn agent-btn-ghost" data-agent-task-dismiss>先不了</button>',
      "</div></div>",
    ].join("");
  }

  function renderToolItemMarkup(item, { compact = false } = {}) {
    const statusIcon = item.status === "ok" ? "✓" : item.status === "failed" ? "✗" : "…";
    const summary = item.summary || `${item.name}()`;
    const detail = [
      '<div class="agent-tool-detail">',
      renderJsonBlock(item.arguments),
      item.result
        ? `<pre class="agent-detail-pre agent-tool-result${item.status === "failed" ? " is-error" : ""}">${escapeHtml(item.result)}${item.truncated ? "\n…（结果已截断）" : ""}</pre>`
        : "",
      "</div>",
    ].join("");
    return [
      `<details class="agent-tool${compact ? " is-compact" : ""}" data-tool="${escapeHtml(item.name)}" data-status="${escapeHtml(item.status)}"${item.status === "running" ? " open" : ""}>`,
      `<summary><span class="agent-tool-name">${escapeHtml(summary)}</span><span class="agent-tool-state">${statusIcon}</span></summary>`,
      detail,
      "</details>",
    ].join("");
  }

  /**
   * Render the whole process flow. ``collapsed`` wraps the flow in a
   * ``<details>`` so completed turns collapse into a one-line summary;
   * live runs render expanded. Approval cards keep their action buttons
   * only while pending.
   */
  function renderAgentRunMarkup(run, { compact = false, collapsed = false } = {}) {
    if (!run || (run.steps.length === 0 && !run.error && !run.skillSuggestion && !run.taskProposal)) {
      return "";
    }
    const parts = [];
    for (const step of run.steps) {
      const stepParts = [];
      if (step.thinking) {
        stepParts.push(`<p class="agent-thinking">${plainTextMarkup(step.thinking)}</p>`);
      }
      for (const item of step.items) {
        if (item.kind === "approval") {
          stepParts.push(renderApprovalCardMarkup(item.approval, { compact }));
        } else {
          stepParts.push(renderToolItemMarkup(item, { compact }));
        }
      }
      if (stepParts.length > 0) {
        parts.push(`<div class="agent-step" data-step="${step.step}">${stepParts.join("")}</div>`);
      }
    }
    if (run.stepLimitReached) {
      parts.push(
        `<p class="agent-step-limit">已达到思考步数上限，先汇报了当前进展${run.stepLimitText ? `：${plainTextMarkup(run.stepLimitText)}` : "。"}</p>`,
      );
    }
    if (run.skillSuggestion) parts.push(renderSkillSuggestionMarkup(run.skillSuggestion));
    if (run.taskProposal) parts.push(renderTaskProposalMarkup(run.taskProposal));
    if (run.error) {
      parts.push(`<p class="agent-run-error" role="alert">${plainTextMarkup(run.error)}</p>`);
    }
    const body = parts.join("");
    if (!collapsed) {
      return `<div class="agent-run${compact ? " is-compact" : ""}" data-agent-run>${body}</div>`;
    }
    const steps = agentRunStepCount(run);
    return [
      `<details class="agent-run is-collapsed${compact ? " is-compact" : ""}" data-agent-run>`,
      `<summary class="agent-run-toggle">执行过程（${steps} 步${run.settled ? "，已完成" : ""}）</summary>`,
      body,
      "</details>",
    ].join("");
  }

  /** Summary card for a ``payload.type === "agent_task_summary"`` turn. */
  function renderAgentTaskSummaryMarkup(payload, { markdown = null } = {}) {
    if (!payload || typeof payload !== "object") return "";
    const taskId = String(payload.task_id || "");
    const status = String(payload.task_status || "completed");
    const report = String(payload.report || payload.reply || "");
    const suggestions = Array.isArray(payload.suggestions) ? payload.suggestions : [];
    const parts = [
      `<div class="agent-task-summary" data-task-id="${escapeHtml(taskId)}">`,
      `<div class="agent-task-summary-head"><span>后台任务${agentTaskStatusLabel(status)}</span>`,
      taskId
        ? `<button type="button" class="agent-btn agent-btn-ghost" data-agent-task-open="${escapeHtml(taskId)}">查看任务</button>`
        : "",
      "</div>",
    ];
    if (report) {
      const rendered = typeof markdown === "function" ? markdown(report) : plainTextMarkup(report);
      parts.push(`<div class="agent-task-summary-report chat-markdown">${rendered}</div>`);
    }
    if (suggestions.length > 0) {
      parts.push('<ul class="agent-task-suggestions">');
      for (const suggestion of suggestions.slice(0, 20)) {
        const summaryText = String(suggestion?.summary || "");
        if (!summaryText) continue;
        parts.push(
          `<li><span class="agent-task-suggestion-text">${plainTextMarkup(summaryText)}</span>`,
          `<button type="button" class="agent-btn agent-btn-secondary" data-agent-suggestion-use data-summary="${escapeHtml(summaryText)}">带入对话</button></li>`,
        );
      }
      parts.push("</ul>");
    }
    parts.push("</div>");
    return parts.join("");
  }

  // ── Task center markup ───────────────────────────────────────
  function renderAgentTaskRowMarkup(task, { compact = false } = {}) {
    if (!task?.task_id) return "";
    const active = isAgentTaskActive(task.status);
    return [
      `<div class="agent-task-row${compact ? " is-compact" : ""}" data-task-id="${escapeHtml(task.task_id)}" data-status="${escapeHtml(task.status)}">`,
      `<button type="button" class="agent-task-row-main" data-agent-task-open="${escapeHtml(task.task_id)}">`,
      `<span class="agent-task-row-title">${escapeHtml(task.title)}</span>`,
      task.progress ? `<span class="agent-task-row-progress">${plainTextMarkup(task.progress)}</span>` : "",
      "</button>",
      `<span class="agent-task-row-status" data-tone="${escapeHtml(task.status)}">${escapeHtml(agentTaskStatusLabel(task.status))}</span>`,
      active
        ? `<button type="button" class="agent-btn agent-btn-ghost" data-agent-task-cancel="${escapeHtml(task.task_id)}">取消</button>`
        : "",
      "</div>",
    ].join("");
  }

  /** Full task detail: process flow from the step log + report + suggestions. */
  function renderAgentTaskDetailMarkup(task, { compact = false, markdown = null } = {}) {
    if (!task?.task_id) return "";
    const parts = [
      `<div class="agent-task-detail" data-task-id="${escapeHtml(task.task_id)}">`,
      `<div class="agent-task-detail-head"><strong>${escapeHtml(task.title)}</strong>`,
      `<span class="agent-task-row-status" data-tone="${escapeHtml(task.status)}">${escapeHtml(agentTaskStatusLabel(task.status))}</span></div>`,
      `<p class="agent-task-detail-prompt">${plainTextMarkup(task.prompt)}</p>`,
    ];
    const run = agentRunFromEvents(task.steps);
    const runMarkup = renderAgentRunMarkup(run, { compact, collapsed: !isAgentTaskActive(task.status) });
    if (runMarkup) parts.push(runMarkup);
    if (task.error) {
      parts.push(`<p class="agent-run-error" role="alert">${plainTextMarkup(task.error)}</p>`);
    }
    if (task.report) {
      const rendered = typeof markdown === "function" ? markdown(task.report) : plainTextMarkup(task.report);
      parts.push(`<div class="agent-task-detail-report chat-markdown">${rendered}</div>`);
    }
    if (task.suggestions.length > 0) {
      parts.push('<ul class="agent-task-suggestions">');
      for (const suggestion of task.suggestions.slice(0, 20)) {
        const summaryText = String(suggestion?.summary || "");
        if (!summaryText) continue;
        parts.push(
          `<li><span class="agent-task-suggestion-text">${plainTextMarkup(summaryText)}</span>`,
          `<button type="button" class="agent-btn agent-btn-secondary" data-agent-suggestion-use data-summary="${escapeHtml(summaryText)}">带入对话</button></li>`,
        );
      }
      parts.push("</ul>");
    }
    if (isAgentTaskActive(task.status)) {
      parts.push(
        `<button type="button" class="agent-btn agent-btn-secondary" data-agent-task-cancel="${escapeHtml(task.task_id)}">取消任务</button>`,
      );
    }
    parts.push("</div>");
    return parts.join("");
  }

  global.OpenBiliClawAgentChat = {
    escapeHtml,
    createAgentSseParser,
    createSseReadWatchdog,
    SSE_READ_WATCHDOG_MS,
    createAgentRun,
    applyAgentEvent,
    agentRunFromEvents,
    agentEventsFromTurn,
    agentRunStepCount,
    captureApprovalDrafts,
    restoreApprovalDrafts,
    normalizeChatSkill,
    normalizeChatSkillList,
    skillDisplayTitle,
    approvalStatusLabel,
    isApprovalTerminalStatus,
    normalizeApprovalRecord,
    normalizeApprovalList,
    normalizeApproveResponse,
    applyApprovalRecordToRun,
    agentTaskStatusLabel,
    isAgentTaskActive,
    normalizeAgentTask,
    isAgentTaskSummaryTurn,
    renderApprovalCardMarkup,
    renderSkillSuggestionMarkup,
    renderTaskProposalMarkup,
    renderAgentRunMarkup,
    renderAgentTaskSummaryMarkup,
    renderAgentTaskRowMarkup,
    renderAgentTaskDetailMarkup,
  };
})(typeof globalThis !== "undefined" ? globalThis : window);
