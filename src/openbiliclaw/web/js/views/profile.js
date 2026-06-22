/**
 * Profile view — full onion model with uninit state, all layers,
 * expandable cognition cards, speculative interest/avoidance actions.
 */

import {
  fetchProfileSummary,
  respondToProbe,
  respondToAvoidanceProbe,
  fetchEditState,
  submitProfileEdit,
} from "../api.js";
import {
  normalizeProfileSummary,
  normalizeMbtiDimensions,
  normalizeCognitionUpdateCard,
  buildNextCognitionHistoryState,
  getContextPatternRows,
  getMbtiDisplayState,
  getProfileStyleDisplay,
  formatRelativeTimestamp,
} from "../view-models.js";
import { state, patchState } from "../state.js";
import {
  filterVisibleProbes,
  forgetHandledProbe,
  rememberHandledProbe,
} from "./probe-notification-helpers.js";

let $root = null;
let cognitionHistory = null; // { items, hasMore, nextCursor, loadingMore }
let expandedCognitionIdx = null;
let loading = false;
const PROFILE_REFRESH_DEBOUNCE_MS = 1000;
let profileRefreshTimer = null;
let profileRefreshInFlight = false;
let profileRefreshPending = false;
let editing = false;
let editState = null;

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

function escAttr(s) {
  return esc(String(s)).replace(/"/g, "&quot;");
}

// Editable fields (Phase 3): onion paths + interest polarities, in display order.
const EDIT_FIELD_LABELS = {
  personality_portrait: "人格素描",
  "core.core_traits": "核心特质",
  "core.deep_needs": "深层需求",
  "values_layer.values": "价值观",
  "values_layer.motivational_drivers": "内在驱动力",
  likes: "感兴趣的方向",
  dislikes: "明显会避开",
  "interest.favorite_up_users": "关注的 UP",
  "role.life_stage": "人生阶段",
  "role.current_phase": "当前阶段",
  "surface.cognitive_style": "认知风格",
  "surface.exploration_openness": "探索开放度",
  "surface.style.quality_sensitivity": "质量敏感度",
  "surface.style.humor_preference": "幽默偏好",
  "surface.style.depth_preference": "深度偏好",
};
const EDIT_FIELD_ORDER = [
  "personality_portrait",
  "core.core_traits",
  "core.deep_needs",
  "values_layer.values",
  "values_layer.motivational_drivers",
  "likes",
  "dislikes",
  "interest.favorite_up_users",
  "role.life_stage",
  "role.current_phase",
  "surface.cognitive_style",
  "surface.exploration_openness",
  "surface.style.quality_sensitivity",
  "surface.style.humor_preference",
  "surface.style.depth_preference",
];

function chipList(items, cls = "") {
  if (!items?.length) return "";
  return `<div class="chip-list">${items.map((t) => `<span class="chip ${cls}">${esc(String(t))}</span>`).join("")}</div>`;
}

function section(title, html) {
  return `<div class="profile-section"><div class="profile-section-title">${esc(title)}</div>${html}</div>`;
}

// ── Render ────────────────────────────────────────────────────
function render() {
  if (!$root) return;
  const p = state.profile;

  if (loading && !p) {
    $root.innerHTML = `<div style="padding:40px"><div class="spinner"></div></div>`;
    return;
  }

  if (!p) {
    $root.innerHTML = `<div class="profile-uninit"><div class="profile-uninit-icon">\u{1F9E0}</div><div class="profile-uninit-text">\u8FD8\u6CA1\u6709\u753B\u50CF\u6570\u636E</div></div>`;
    return;
  }

  if (!p.initialized) {
    $root.innerHTML = `<div class="profile-uninit"><div class="profile-uninit-icon">\u{1F9E0}</div><div class="profile-uninit-text">\u8FD8\u6CA1\u5B8C\u6210\u521D\u59CB\u5316\uFF0C\u5148\u8FD0\u884C <code>openbiliclaw init</code></div></div>`;
    return;
  }

  if (editing) {
    $root.innerHTML = renderEditPanelHtml();
    bindEditActions();
    return;
  }

  let html = `<div class="profile-edit-bar"><button class="profile-edit-toggle" data-edit-toggle="enter">✏️ 编辑画像</button></div>`;

  // Portrait
  html += section("\u4EBA\u683C\u7D20\u63CF", `<div class="profile-portrait">${esc(p.personality_portrait)}</div>`);

  // Core
  const coreHtml = [];
  if (p.core_traits.length) coreHtml.push(`<div style="margin-bottom:8px">${chipList(p.core_traits, "brand")}</div>`);
  if (p.deep_needs.length) coreHtml.push(`<div style="margin-bottom:8px"><span style="font-size:11px;color:var(--text-muted)">\u6DF1\u5C42\u9700\u6C42</span>${chipList(p.deep_needs)}</div>`);
  if (p.mbti?.type) coreHtml.push(renderMBTI(p.mbti));
  if (coreHtml.length) html += section("CORE", coreHtml.join(""));

  // Values & Drivers
  const valHtml = [];
  if (p.values.length) valHtml.push(chipList(p.values, "success"));
  if (p.motivational_drivers.length) {
    valHtml.push(`<div style="margin-top:8px"><span style="font-size:11px;color:var(--text-muted)">\u5185\u5728\u9A71\u52A8\u529B</span>${chipList(p.motivational_drivers)}</div>`);
  }
  if (valHtml.length) html += section("\u4EF7\u503C\u89C2", valHtml.join(""));

  // Interests
  const intHtml = [];
  if (p.likes.length) {
    intHtml.push(`<div style="margin-bottom:8px;font-size:12px;font-weight:600;color:var(--sky)">\u559C\u6B22</div>`);
    intHtml.push(renderInterestTree(p.likes, false));
  }
  if (p.dislikes.length) {
    intHtml.push(`<div style="margin-bottom:8px;margin-top:12px;font-size:12px;font-weight:600;color:var(--danger)">\u4E0D\u559C\u6B22</div>`);
    intHtml.push(renderInterestTree(p.dislikes, true));
  }
  if (p.favorite_up_users.length) {
    intHtml.push(`<div style="margin-top:12px"><span style="font-size:11px;color:var(--text-muted)">\u5173\u6CE8\u7684 UP</span>${chipList(p.favorite_up_users, "brand")}</div>`);
  }
  if (intHtml.length) html += section("\u5174\u8DA3\u9886\u57DF", intHtml.join(""));

  // Role
  if (p.life_stage || p.current_phase) {
    const roleHtml = [p.life_stage, p.current_phase].filter(Boolean).map((s) => `<div style="font-size:13px;margin-bottom:4px">${esc(s)}</div>`).join("");
    html += section("\u89D2\u8272", roleHtml);
  }

  // Surface
  const surfHtml = [];
  if (p.cognitive_style.length) surfHtml.push(chipList(p.cognitive_style));
  if (p.style) {
    const styleDisplay = getProfileStyleDisplay(p.style);
    const prefs = [
      ["preferred_duration", "\u559C\u6B22\u65F6\u957F"],
      ["preferred_pace", "\u559C\u6B22\u8282\u594F"],
    ];
    for (const [key, label] of prefs) {
      if (styleDisplay?.[key]) surfHtml.push(`<div style="font-size:12px;margin-top:4px">${esc(label)}: <strong>${esc(styleDisplay[key])}</strong></div>`);
    }
    const bars = [
      ["quality_sensitivity", "\u8D28\u91CF\u654F\u611F\u5EA6"],
      ["humor_preference", "\u5E7D\u9ED8\u504F\u597D"],
      ["depth_preference", "\u6DF1\u5EA6\u504F\u597D"],
    ];
    for (const [key, label] of bars) {
      if (typeof styleDisplay?.[key] === "number") {
        const pct = Math.round(styleDisplay[key] * 100);
        surfHtml.push(`<div style="font-size:12px;margin-top:6px">${esc(label)} <span style="color:var(--text-muted)">${pct}%</span>
          <div class="interest-bar"><div class="interest-bar-fill" style="width:${pct}%"></div></div></div>`);
      }
    }
  }
  if (p.context) {
    const patterns = getContextPatternRows(p.context);
    if (patterns.length) {
      surfHtml.push(`<div class="context-patterns" style="margin-top:8px">${patterns.map((row) =>
        `<div><div class="context-pattern-label">${esc(row.label)}</div><div style="font-size:12px">${esc(row.value)}</div></div>`
      ).join("")}</div>`);
    }
  }
  if (typeof p.exploration_openness === "number") {
    surfHtml.push(renderExplorationBar(p.exploration_openness));
  }
  if (surfHtml.length) html += section("\u8BA4\u77E5\u98CE\u683C", surfHtml.join(""));

  // Speculative interests
  const visibleSpeculativeInterests = filterVisibleProbes(
    p.speculative_interests,
    "interest.probe",
  );
  if (visibleSpeculativeInterests.length) {
    html += section("\u63A8\u6D4B\u6027\u5174\u8DA3", renderSpecInterests(visibleSpeculativeInterests));
  }

  // Speculative avoidances
  const visibleSpeculativeAvoidances = filterVisibleProbes(
    p.speculative_avoidances,
    "avoidance.probe",
  );
  if (visibleSpeculativeAvoidances.length) {
    html += section("\u5F85\u786E\u8BA4\u907F\u96F7\u65B9\u5411", renderSpecAvoidances(visibleSpeculativeAvoidances));
  }

  // Cognition history
  const cogItems = cognitionHistory?.items || p.recent_cognition_updates || [];
  if (cogItems.length) {
    let cogHtml = cogItems.map((c, i) => renderCognitionCard(c, i)).join("");
    const hasMore = cognitionHistory?.hasMore ?? p.has_more_cognition_updates;
    if (hasMore) {
      cogHtml += `<button class="load-more-btn" id="load-more-cognition">${cognitionHistory?.loadingMore ? "\u52A0\u8F7D\u4E2D\u2026" : "\u52A0\u8F7D\u66F4\u591A"}</button>`;
    }
    html += section("\u8BA4\u77E5\u66F4\u65B0\u5386\u53F2", cogHtml);
  }

  // Active insights
  if (p.active_insights.length) {
    const insHtml = p.active_insights.map((i) => {
      let extra = "";
      if (i.evidence.length) {
        extra += `<div class="insight-evidence">${i.evidence.map((e) => `<div>\u2022 ${esc(e)}</div>`).join("")}</div>`;
      }
      const confPct = Math.round(i.confidence * 100);
      return `
        <div class="insight-item">
          <div class="insight-label">\u{1F4A1} Insight
            <span class="insight-confidence">${confPct}%</span>
            ${i.validated ? `<span class="insight-validated">\u2713 \u5DF2\u9A8C\u8BC1</span>` : ""}
          </div>
          <div>${esc(i.hypothesis)}</div>
          ${extra}
          ${i.created_at ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(formatRelativeTimestamp(i.created_at))}</div>` : ""}
        </div>`;
    }).join("");
    html += section("\u6D3B\u8DC3\u6D1E\u5BDF", insHtml);
  }

  // Recent awareness
  if (p.recent_awareness.length) {
    const awHtml = p.recent_awareness.map((a) => `
      <div class="awareness-item">
        <div class="awareness-label">\u{1F331} ${esc(a.date || "Awareness")}</div>
        <div>${esc(a.observation)}</div>
        ${a.trend ? `<div class="awareness-trend">\u8D8B\u52BF: ${esc(a.trend)}</div>` : ""}
        ${a.emotion_guess ? `<div class="awareness-emotion">\u60C5\u7EEA: ${esc(a.emotion_guess)}</div>` : ""}
      </div>`).join("");
    html += section("\u8FD1\u671F\u611F\u77E5", awHtml);
  }

  $root.innerHTML = html;

  // Bind events
  $root.querySelector('[data-edit-toggle="enter"]')?.addEventListener("click", () => void enterEdit());
  $root.querySelector("#load-more-cognition")?.addEventListener("click", loadMoreCognition);
  bindSpecInterestActions();
  bindSpecAvoidanceActions();
  bindCognitionExpand();
}

// ── MBTI ─────────────────────────────────────────────────────
function renderMBTI(mbti) {
  const display = getMbtiDisplayState(mbti);
  if (!display.type) return "";
  const dims = display.dimensions.length ? display.dimensions : normalizeMbtiDimensions(mbti);
  let dimsHtml = "";
  for (const d of dims) {
    const pct = Math.round((d.score ?? 0.5) * 100);
    dimsHtml += `
      <div class="mbti-dim">
        <span style="width:28px;font-size:11px;text-align:right">${esc(d.left)}</span>
        <div class="mbti-bar"><div class="mbti-bar-fill" style="width:${pct}%"></div></div>
        <span style="width:28px;font-size:11px">${esc(d.right)}</span>
      </div>`;
  }
  return `<div class="mbti-type">${esc(display.type)}${display.confidence_label ? `<span style="font-size:11px;font-weight:500;color:var(--text-muted);margin-left:8px">${esc(display.confidence_label)}</span>` : ""}</div>${dimsHtml ? `<div class="mbti-dims">${dimsHtml}</div>` : ""}`;
}

// ── Interest Tree ────────────────────────────────────────────
function renderInterestTree(domains, isDislike) {
  return domains.map((d) => {
    const weight = Math.round((d.weight ?? 0.5) * 100);
    const specifics = (d.specifics || []).map((s) => s.name).join(", ");
    return `
      <div class="interest-domain">
        <div class="interest-domain-name">${esc(d.domain)}</div>
        <div class="interest-bar"><div class="interest-bar-fill${isDislike ? " dislike" : ""}" style="width:${weight}%"></div></div>
        ${specifics ? `<div class="interest-topics">${esc(specifics)}</div>` : ""}
      </div>`;
  }).join("");
}

// ── Exploration Bar ──────────────────────────────────────────
function renderExplorationBar(value) {
  const pct = Math.round(value * 100);
  const labels = ["\u4FDD\u5B88", "\u9002\u4E2D", "\u5F00\u653E", "\u975E\u5E38\u5F00\u653E"];
  const label = labels[Math.min(Math.floor(value * labels.length), labels.length - 1)];
  return `
    <div class="exploration-bar" style="margin-top:8px">
      <span class="exploration-label">\u63A2\u7D22\u5F00\u653E\u5EA6</span>
      <div class="exploration-track"><div class="exploration-fill" style="width:${pct}%"></div></div>
      <span class="exploration-label">${esc(label)} ${pct}%</span>
    </div>`;
}

// ── Speculative Interests ────────────────────────────────────
function renderSpecInterests(interests) {
  return interests.map((si) => {
    const canAct = si.status === "active" || si.status === "pending";
    const progressPct = si.confirmation_threshold > 0
      ? Math.round((si.confirmation_count / si.confirmation_threshold) * 100)
      : 0;
    return `
      <div class="spec-interest" data-domain="${esc(si.domain)}">
        <div class="spec-interest-info">
          <div class="spec-interest-name">${esc(si.domain)}</div>
          <div class="spec-interest-status">${esc(si.status)}${si.confidence ? ` \u00B7 ${Math.round(si.confidence * 100)}%` : ""}</div>
          ${si.reason ? `<div style="font-size:11px;color:var(--text-muted)">${esc(si.reason)}</div>` : ""}
          <div class="spec-interest-progress"><div class="spec-interest-progress-fill" style="width:${progressPct}%"></div></div>
          ${si.specifics?.length ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${si.specifics.map((s) => esc(s.name)).join(", ")}</div>` : ""}
        </div>
        ${canAct ? `
        <div class="spec-interest-actions">
          <button class="spec-btn confirm" data-action="confirm">\u2713</button>
          <button class="spec-btn reject" data-action="reject">\u2717</button>
        </div>` : ""}
      </div>`;
  }).join("");
}

function bindSpecInterestActions() {
  for (const btn of $root.querySelectorAll(".spec-interest .spec-btn")) {
    btn.addEventListener("click", async (e) => {
      const row = e.target.closest(".spec-interest");
      const domain = row?.dataset.domain;
      const action = e.target.dataset.action;
      if (!domain || !action) return;
      btn.disabled = true;
      rememberHandledProbe(domain, "interest.probe");
      try {
        await respondToProbe(domain, action, { surface: "profile" });
        const p = state.profile;
        if (p?.speculative_interests) {
          patchState({
            profile: {
              ...p,
              speculative_interests: p.speculative_interests.filter((si) => si.domain !== domain),
            },
          });
        }
        render();
      } catch {
        forgetHandledProbe(domain, "interest.probe");
        btn.disabled = false;
      }
    });
  }
}

// ── Speculative Avoidances ──────────────────────────────────
function renderSpecAvoidances(avoidances) {
  return avoidances.map((item) => {
    const canAct = item.status === "active" || item.status === "pending";
    const progressPct = item.confirmation_threshold > 0
      ? Math.round((item.confirmation_count / item.confirmation_threshold) * 100)
      : 0;
    const source = item.source_mode ? ` \u00B7 ${esc(item.source_mode)}` : "";
    return `
      <div class="spec-interest spec-avoidance" data-domain="${esc(item.domain)}">
        <div class="spec-interest-info">
          <div class="spec-interest-name">${esc(item.domain)}</div>
          <div class="spec-interest-status">${esc(item.status)}${item.confidence ? ` \u00B7 ${Math.round(item.confidence * 100)}%` : ""}${source}</div>
          ${item.reason ? `<div style="font-size:11px;color:var(--text-muted)">${esc(item.reason)}</div>` : ""}
          <div class="spec-interest-progress"><div class="spec-interest-progress-fill" style="width:${progressPct}%"></div></div>
          ${item.specifics?.length ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${item.specifics.map((s) => esc(s.name)).join(", ")}</div>` : ""}
        </div>
        ${canAct ? `
        <div class="spec-interest-actions">
          <button class="spec-btn spec-avoidance-btn confirm" data-action="confirm">\u2713</button>
          <button class="spec-btn spec-avoidance-btn reject" data-action="reject">\u2717</button>
        </div>` : ""}
      </div>`;
  }).join("");
}

function bindSpecAvoidanceActions() {
  for (const btn of $root.querySelectorAll(".spec-avoidance .spec-avoidance-btn")) {
    btn.addEventListener("click", async (e) => {
      const row = e.target.closest(".spec-avoidance");
      const domain = row?.dataset.domain;
      const action = e.target.dataset.action;
      if (!domain || !action) return;
      btn.disabled = true;
      rememberHandledProbe(domain, "avoidance.probe");
      try {
        await respondToAvoidanceProbe(domain, action);
        const p = state.profile;
        if (p?.speculative_avoidances) {
          patchState({
            profile: {
              ...p,
              speculative_avoidances: p.speculative_avoidances.filter((si) => si.domain !== domain),
            },
          });
        }
        render();
      } catch {
        forgetHandledProbe(domain, "avoidance.probe");
        btn.disabled = false;
      }
    });
  }
}

// ── Cognition Cards ──────────────────────────────────────────
function renderCognitionCard(raw, idx) {
  const c = normalizeCognitionUpdateCard(raw);
  const isExpanded = expandedCognitionIdx === idx;
  return `
    <div class="cognition-card ${c.expandable ? "expandable" : ""} ${isExpanded ? "expanded" : ""}" data-cog-idx="${idx}">
      <div class="cognition-date">${esc(formatRelativeTimestamp(c.created_at) || c.created_at)}</div>
      <div class="cognition-summary">${esc(c.summary)}</div>
      <div style="font-size:11px;color:var(--text-muted)">${esc(c.contextLine)}</div>
      ${c.sourceLabel ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(c.sourceLabel)}</div>` : ""}
      ${c.expandable ? `<div class="cognition-expand-hint">${isExpanded ? "\u6536\u8D77" : esc(c.expandLabel)}</div>` : ""}
      ${c.expandable ? `<div class="cognition-detail">
        ${c.impact ? `<div><strong>\u5F71\u54CD:</strong> ${esc(c.impact)}</div>` : ""}
        ${c.reasoning ? `<div><strong>\u63A8\u7406:</strong> ${esc(c.reasoning)}</div>` : ""}
        ${c.evidence ? `<div><strong>\u8BC1\u636E:</strong> ${esc(c.evidence)}</div>` : ""}
      </div>` : ""}
    </div>`;
}

function bindCognitionExpand() {
  for (const card of $root.querySelectorAll(".cognition-card.expandable")) {
    card.addEventListener("click", () => {
      const idx = Number(card.dataset.cogIdx);
      expandedCognitionIdx = expandedCognitionIdx === idx ? null : idx;
      render();
    });
  }
}

// ── Editable profile (Phase 3) ───────────────────────────────
// Edit mode swaps the display for an edit panel rendered from
// GET /api/profile/edit-state (un-truncated). Each control posts one
// deterministic op to /api/profile/edit and re-renders from edit_state.
// Edits survive profile rebuilds (server-side overrides overlay).

async function enterEdit() {
  editing = true;
  editState = null;
  render();
  try {
    editState = await fetchEditState();
  } catch {
    // Distinguish a failed request (404/500/network) from a genuinely
    // uninitialized profile — the latter is what the server reports as
    // {initialized:false}. Conflating them showed "先跑一遍 init" even
    // when the profile exists (view mode renders it fine) and only the
    // edit-state request failed.
    editState = { loadError: true };
  }
  render();
}

function exitEdit() {
  editing = false;
  editState = null;
  loadData();
}

async function applyEdit(payload) {
  try {
    const res = await submitProfileEdit(payload);
    editState = res?.edit_state?.initialized ? res.edit_state : await fetchEditState();
  } catch {
    try {
      editState = await fetchEditState();
    } catch {
      /* keep current editState */
    }
  }
  render();
}

function renderTextEditField(path, label, field) {
  const pinned = Boolean(field.pinned);
  const rows = path === "personality_portrait" ? 4 : 2;
  return `
    <div class="edit-field">
      <div class="edit-field-head"><span class="edit-field-label">${esc(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
      <textarea class="edit-text-input" data-edit-text="${escAttr(path)}" rows="${rows}">${esc(field.value || "")}</textarea>
      ${field.ai_suggestion ? `<p class="edit-drift-hint">AI 当前想更新为：${esc(field.ai_suggestion)}</p>` : ""}
      <div class="edit-field-actions">
        <button class="edit-save-btn" data-edit-save="${escAttr(path)}">保存</button>
        ${pinned ? `<button class="edit-reset-btn" data-edit-reset="${escAttr(path)}">恢复 AI 建议</button>` : ""}
      </div>
    </div>`;
}

// Scalar (0..1) fields render as a percent slider. Like text fields they
// commit on an explicit 保存 tap (not per-drag) to avoid a POST per pixel;
// the live label updates on input so the value is visible while dragging.
function renderScalarEditField(path, label, field) {
  const pinned = Boolean(field.pinned);
  const pct = Math.round((Number(field.value) || 0) * 100);
  const aiPct = typeof field.ai_suggestion === "number" ? Math.round(field.ai_suggestion * 100) : null;
  return `
    <div class="edit-field">
      <div class="edit-field-head"><span class="edit-field-label">${esc(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
      <div class="edit-scalar-row">
        <input class="edit-scalar-input" type="range" min="0" max="100" step="1" value="${pct}" data-edit-scalar="${escAttr(path)}" />
        <span class="edit-scalar-value" data-edit-scalar-value="${escAttr(path)}">${pct}%</span>
      </div>
      ${aiPct !== null ? `<p class="edit-drift-hint">AI 当前想更新为：${aiPct}%</p>` : ""}
      <div class="edit-field-actions">
        <button class="edit-save-btn" data-edit-save-scalar="${escAttr(path)}">保存</button>
        ${pinned ? `<button class="edit-reset-btn" data-edit-reset="${escAttr(path)}">恢复 AI 建议</button>` : ""}
      </div>
    </div>`;
}

function renderListEditField(path, label, field) {
  const items = Array.isArray(field.items) ? field.items : [];
  const edited = (field.added?.length || 0) > 0 || (field.removed?.length || 0) > 0;
  const chips = items.length
    ? items
        .map(
          (it) =>
            `<span class="edit-chip">${esc(it)}<button class="edit-chip-remove" data-edit-remove="${escAttr(path)}" data-edit-value="${escAttr(it)}">✕</button></span>`,
        )
        .join("")
    : `<p class="edit-empty">还没有，添加一个吧</p>`;
  return `
    <div class="edit-field">
      <div class="edit-field-head"><span class="edit-field-label">${esc(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
      <div class="edit-chip-list">${chips}</div>
      <div class="edit-add-row">
        <input class="edit-add-input" data-edit-add-input="${escAttr(path)}" placeholder="添加一项" />
        <button class="edit-add-btn" data-edit-add="${escAttr(path)}">添加</button>
      </div>
      ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" data-edit-reset="${escAttr(path)}">恢复 AI 建议</button></div>` : ""}
    </div>`;
}

function renderInterestEditField(path, label, field) {
  const domains = Array.isArray(field.domains) ? field.domains : [];
  const edited = (field.removed_domains?.length || 0) > 0 || domains.some((d) => d?.user_added);
  const chips = domains.length
    ? domains
        .map(
          (d) =>
            `<span class="edit-chip">${esc(d.domain)}${d.user_added ? " ＋" : ""}<button class="edit-chip-remove" data-edit-remove="${escAttr(path)}" data-edit-value="${escAttr(d.domain)}">✕</button></span>`,
        )
        .join("")
    : `<p class="edit-empty">还没有，添加一个吧</p>`;
  const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
  return `
    <div class="edit-field">
      <div class="edit-field-head"><span class="edit-field-label">${esc(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
      <div class="edit-chip-list">${chips}</div>
      <div class="edit-add-row">
        <input class="edit-add-input" data-edit-add-input="${escAttr(path)}" placeholder="${esc(placeholder)}" />
        <button class="edit-add-btn" data-edit-add="${escAttr(path)}">添加</button>
      </div>
      ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" data-edit-reset="${escAttr(path)}">恢复 AI 建议</button></div>` : ""}
    </div>`;
}

function renderEditPanelHtml() {
  let html = `<div class="profile-edit-bar"><button class="profile-edit-toggle" data-edit-toggle="exit">✓ 完成</button></div>`;
  if (!editState) {
    html += `<div style="padding:24px"><div class="spinner"></div></div>`;
    return html;
  }
  if (editState.loadError) {
    html += `<div class="profile-edit-note">编辑数据加载失败，请检查后端连接后重试。</div><button class="load-more-btn" data-edit-retry="1">重试</button>`;
    return html;
  }
  if (!editState.initialized || !editState.fields) {
    html += `<div class="profile-edit-note">画像还没攒起来，先跑一遍 <code>openbiliclaw init</code> 再回来编辑。</div>`;
    return html;
  }
  html += `<div class="profile-edit-note">标签 / 兴趣类增删即时生效；文本与滑杆类改完点「保存」才生效。改动都不会被后续自动重建覆盖，删错了点「恢复 AI 建议」即可。</div>`;
  for (const path of EDIT_FIELD_ORDER) {
    const field = editState.fields[path];
    if (!field || typeof field !== "object") continue;
    const label = EDIT_FIELD_LABELS[path] || path;
    if (field.type === "text") html += renderTextEditField(path, label, field);
    else if (field.type === "scalar") html += renderScalarEditField(path, label, field);
    else if (field.type === "list") html += renderListEditField(path, label, field);
    else if (field.type === "interest") html += renderInterestEditField(path, label, field);
  }
  return html;
}

function bindEditActions() {
  $root.querySelector('[data-edit-toggle="exit"]')?.addEventListener("click", exitEdit);
  $root.querySelector("[data-edit-retry]")?.addEventListener("click", () => void enterEdit());

  for (const btn of $root.querySelectorAll("[data-edit-remove]")) {
    btn.addEventListener("click", () =>
      void applyEdit({ target: btn.dataset.editRemove, op: "remove", value: btn.dataset.editValue }),
    );
  }
  for (const btn of $root.querySelectorAll("[data-edit-reset]")) {
    btn.addEventListener("click", () => void applyEdit({ target: btn.dataset.editReset, op: "reset" }));
  }
  for (const btn of $root.querySelectorAll("[data-edit-add]")) {
    btn.addEventListener("click", () => {
      const path = btn.dataset.editAdd;
      const input = $root.querySelector(`[data-edit-add-input="${path}"]`);
      const value = input?.value.trim();
      if (!value) return;
      void applyEdit({ target: path, op: "add", value });
    });
  }
  for (const input of $root.querySelectorAll("[data-edit-add-input]")) {
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      void applyEdit({ target: input.dataset.editAddInput, op: "add", value });
    });
  }
  for (const btn of $root.querySelectorAll("[data-edit-save]")) {
    btn.addEventListener("click", () => {
      const path = btn.dataset.editSave;
      const ta = $root.querySelector(`[data-edit-text="${path}"]`);
      const value = ta?.value.trim();
      if (!value) return;
      void applyEdit({ target: path, op: "set", value });
    });
  }
  for (const input of $root.querySelectorAll("[data-edit-scalar]")) {
    input.addEventListener("input", () => {
      const out = $root.querySelector(`[data-edit-scalar-value="${input.dataset.editScalar}"]`);
      if (out) out.textContent = `${input.value}%`;
    });
  }
  for (const btn of $root.querySelectorAll("[data-edit-save-scalar]")) {
    btn.addEventListener("click", () => {
      const path = btn.dataset.editSaveScalar;
      const input = $root.querySelector(`[data-edit-scalar="${path}"]`);
      if (!input) return;
      void applyEdit({ target: path, op: "set", value: Number(input.value) / 100 });
    });
  }
}

// ── Load ─────────────────────────────────────────────────────
async function loadData() {
  loading = true;
  render();
  try {
    const data = await fetchProfileSummary({ limit: 5 });
    const profile = normalizeProfileSummary(data);
    patchState({ profile });
    cognitionHistory = {
      items: profile.recent_cognition_updates,
      hasMore: profile.has_more_cognition_updates,
      nextCursor: profile.next_cognition_cursor,
      loadingMore: false,
      loadMoreError: "",
    };
  } catch { /* ignore */ }
  loading = false;
  render();
}

function scheduleProfileRefresh({ delayMs = PROFILE_REFRESH_DEBOUNCE_MS } = {}) {
  if (profileRefreshTimer !== null) {
    clearTimeout(profileRefreshTimer);
  }
  profileRefreshTimer = setTimeout(() => {
    profileRefreshTimer = null;
    void runScheduledProfileRefresh();
  }, Math.max(0, delayMs));
}

async function runScheduledProfileRefresh() {
  if (profileRefreshInFlight) {
    profileRefreshPending = true;
    return;
  }
  profileRefreshInFlight = true;
  try {
    await loadData();
  } finally {
    profileRefreshInFlight = false;
    if (profileRefreshPending) {
      profileRefreshPending = false;
      scheduleProfileRefresh();
    }
  }
}

async function loadMoreCognition() {
  if (!cognitionHistory?.nextCursor || cognitionHistory.loadingMore) return;
  cognitionHistory.loadingMore = true;
  render();
  try {
    const data = await fetchProfileSummary({ limit: 5, cursor: cognitionHistory.nextCursor });
    cognitionHistory = buildNextCognitionHistoryState(cognitionHistory, normalizeProfileSummary(data));
    render();
  } catch {
    cognitionHistory.loadingMore = false;
    cognitionHistory.loadMoreError = "加载失败";
    render();
  }
}

// ── Public API ───────────────────────────────────────────────
export function initProfileView(root) {
  $root = root;
  loadData();
}

export function onStreamEvent(payload) {
  const type = payload?.type || payload?.event;
  if (type === "profile_updated") {
    scheduleProfileRefresh();
  }
}
