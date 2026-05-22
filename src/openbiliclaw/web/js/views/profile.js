/**
 * Profile view — full onion model with uninit state, all layers,
 * expandable cognition cards, speculative interest actions.
 */

import { fetchProfileSummary, respondToProbe } from "../api.js";
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

let $root = null;
let cognitionHistory = null; // { items, hasMore, nextCursor, loadingMore }
let expandedCognitionIdx = null;
let loading = false;

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

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

  let html = "";

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
  if (p.speculative_interests.length) {
    html += section("\u63A8\u6D4B\u6027\u5174\u8DA3", renderSpecInterests(p.speculative_interests));
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
  $root.querySelector("#load-more-cognition")?.addEventListener("click", loadMoreCognition);
  bindSpecInterestActions();
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
  for (const btn of $root.querySelectorAll(".spec-btn")) {
    btn.addEventListener("click", async (e) => {
      const row = e.target.closest(".spec-interest");
      const domain = row?.dataset.domain;
      const action = e.target.dataset.action;
      if (!domain || !action) return;
      btn.disabled = true;
      try {
        await respondToProbe(domain, action);
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
    loadData();
  }
}
