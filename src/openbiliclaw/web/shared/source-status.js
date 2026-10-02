/**
 * Shared rendering for the platform-source auth contract.
 *
 * One enum, one table, three frontends. Before this module the desktop page,
 * the extension side panel and the setup wizard each hand-maintained their own
 * view of `/api/sources/*`, and they had already drifted in ways users could
 * see (spec D6):
 *
 *   - the side panel painted `no_auth` and `unverified` the same grey, so
 *     "this source needs no login" and "we have no idea about this source"
 *     were literally the same pixel;
 *   - an unrecognised state rendered as "状态未知" on the desktop and as an
 *     *empty string* in the side panel — a grey dot with no explanation at all.
 *
 * ## What lives here, and what does not
 *
 * This module owns exactly one thing: the **presentation of the source-auth
 * contract** — the chip label and tone derived from `item.auth`, how strong the
 * evidence behind that verdict is and how old, the verify outcome tones, and
 * the credential-row shape driven by the backend's `form` descriptor. The
 * legacy `state` table is kept beside it as the fallback for backends older
 * than the contract.
 *
 * The evidence half is the reason the contract was worth building: `verified`
 * is a verdict, `verify_method` is what it rests on, and a user who cannot see
 * the second one is being told that a probe of B站's nav endpoint and the mere
 * existence of a Reddit credential file mean the same thing. See
 * `VERIFY_EVIDENCE` for how that difference is encoded without leaning on
 * colour.
 *
 * It deliberately does *not* own the wording of anything the backend already
 * says. `item.detail`, `verify.message` and `credential.summary` are rendered
 * verbatim; a platform-specific sentence written here would be the
 * per-platform display branch invariant I4 forbids, just relocated.
 *
 * ## Why the table is in JS and not in the contract
 *
 * Shipping `access_label` / `access_tone` from the backend would look more
 * "contract-driven", but it would leave a Python table *and* a JS fallback
 * table — two copies of one piece of knowledge, in two languages, where the
 * metric script only scans one of them. That is the syntax-proxy trap spec I7
 * describes. So the label/tone table exists once, here, and the backend owns
 * the substance (`detail`) instead. A side panel installed from the store must
 * keep rendering correctly against an older self-hosted backend, which a
 * backend-only table could not guarantee.
 *
 * ## Boundary with the saved-sync enum
 *
 * `saved-sync-core.js` has its own status presentation and shares two key
 * *spellings* with this one (`login_required`, `rate_limited`). They are not
 * merged and must not be: this enum answers "can this source be reached at
 * all", saved-sync's answers "did this one item sync". The rule is ownership,
 * not vocabulary — a mapping belongs here only if its keys are values of a
 * field emitted by `/api/sources/*`. Everything below is written as small pure
 * helpers over a table so a future merge, if the enums ever do converge, is a
 * table merge rather than a rewrite.
 *
 * Loaded as a classic script by all three surfaces (and copied into the
 * extension package at build time), following `saved-sync-core.js`.
 */
(function installSourceStatus(global) {
  "use strict";

  /** Platform slugs the contract covers, in settings-page display order. */
  const SOURCE_KEYS = Object.freeze([
    "bilibili", "xiaohongshu", "douyin", "weibo", "youtube", "tiktok", "twitter", "github", "zhihu", "reddit",
    "bangumi", "linuxdo", "v2ex",
  ]);

  const SOURCE_CAPABILITIES = Object.freeze({
    bilibili: Object.freeze({ guidedInit: true }),
    xiaohongshu: Object.freeze({ guidedInit: true }),
    douyin: Object.freeze({ guidedInit: true }),
    weibo: Object.freeze({ guidedInit: true }),
    youtube: Object.freeze({ guidedInit: true }),
    // TikTok is a yt-dlp public source with no account bootstrap, so it has
    // no guided-init checkbox; it is toggled from config / settings only.
    tiktok: Object.freeze({ guidedInit: false }),
    twitter: Object.freeze({ guidedInit: true }),
    github: Object.freeze({ guidedInit: true }),
    zhihu: Object.freeze({ guidedInit: true }),
    reddit: Object.freeze({ guidedInit: true }),
    bangumi: Object.freeze({ guidedInit: true }),
    linuxdo: Object.freeze({ guidedInit: true }),
    v2ex: Object.freeze({ guidedInit: true }),
  });
  const INIT_SOURCE_KEYS = Object.freeze(
    SOURCE_KEYS.filter((key) => SOURCE_CAPABILITIES[key]?.guidedInit === true),
  );

  /**
   * Display names for the source *settings* surfaces.
   *
   * Not to be confused with the recommendation card's platform labels, which
   * spell X as "X (Twitter)" for a reader who needs the old name. Two audiences,
   * two vocabularies; merging them would make one of the two read wrong.
   */
  const SOURCE_LABELS = Object.freeze({
    bilibili: "B 站",
    xiaohongshu: "小红书",
    douyin: "抖音",
    weibo: "微博",
    youtube: "YouTube",
    tiktok: "TikTok",
    twitter: "X",
    github: "GitHub",
    zhihu: "知乎",
    reddit: "Reddit",
    bangumi: "Bangumi",
    linuxdo: "Linux.do",
    v2ex: "V2EX",
  });

  function sourceLabel(key) {
    return SOURCE_LABELS[key] || String(key || "");
  }

  /**
   * Tone -> dot colour, for surfaces that cannot use CSS custom properties.
   *
   * The desktop page styles tones through `[data-tone]` rules; the side panel
   * sets `dot.style.color` inline. Both read their tone from the same table
   * below, so the two surfaces agree on *meaning* even where they differ on
   * mechanism. Values track the desktop's CSS variables: `pending` is
   * `--source-pending`, which is what makes `unverified` (pending, blue) stop
   * colliding with `no_auth` (public, grey) in the side panel.
   */
  const TONE_COLORS = Object.freeze({
    ready: "#2ecc71",
    public: "#9aa0a6",
    pending: "#3898ec",
    warning: "#e0a800",
    danger: "#e74c3c",
    muted: "#9aa0a6",
  });

  /**
   * The single state -> presentation table.
   *
   * This is the **union** of the two tables it replaces, so no surface loses a
   * key it used to handle. Four of them (`syncing`, `expired`, `login_required`,
   * `error`) are not reachable from today's backend — spec D6's证伪 pass found
   * the backend emits only the other twelve. They stay because a table that
   * silently drops a key regresses the moment some future provider emits it,
   * and "unreachable today" is cheaper to carry than to re-derive later.
   */
  const SOURCE_ACCESS_STATE = Object.freeze({
    ok: { tone: "ready", label: "接入可用" },
    ready: { tone: "ready", label: "凭据已就绪" },
    no_auth: { tone: "public", label: "无需登录" },
    unverified: { tone: "pending", label: "状态待验证" },
    rate_limited: { tone: "pending", label: "频率受限" },
    missing: { tone: "warning", label: "需要登录" },
    login_required: { tone: "warning", label: "需要登录" },
    missing_cookie: { tone: "warning", label: "缺少 Cookie" },
    partial: { tone: "warning", label: "部分可用" },
    stale: { tone: "warning", label: "需要刷新" },
    syncing: { tone: "pending", label: "接入中" },
    error: { tone: "danger", label: "检查失败" },
    expired: { tone: "danger", label: "凭据失效" },
    expired_cookie: { tone: "danger", label: "Cookie 失效" },
    blocked: { tone: "danger", label: "接入受阻" },
    // Only reachable from Bangumi, the one source still on this legacy path.
    // It is exactly the conflation the contract removes — a scheduling switch
    // occupying the field that should describe the credential — so it stays a
    // legacy-table entry and gets no equivalent on the `auth` side, where
    // `enabled` lives on SourceStatusItem instead.
    disabled: { tone: "muted", label: "来源未启用" },
  });

  /**
   * Shown for a state no table entry matches.
   *
   * Not the empty string. The side panel used to render nothing at all here,
   * leaving a coloured dot with no text beside it, which reads as "everything
   * is fine" rather than "we do not recognise this".
   */
  const UNKNOWN_ACCESS = Object.freeze({ tone: "muted", label: "状态未知" });

  /**
   * A personal token the platform itself rejected.
   *
   * Outranks every other verdict because it is the one the user can act on: a
   * revoked Bangumi token leaves the last discovery run's `ready` sitting in
   * `state`, so without this the row stays green while private collections
   * silently stop loading. Keyed on the `token_state` field, never on a
   * platform name (invariant I4) — the desktop page and the side panel each
   * carried their own copy of this rule before, which is precisely the
   * duplication that let the two status tables drift apart (spec D6).
   */
  const ACCESS_TOKEN_REJECTED = Object.freeze({ tone: "danger", label: "令牌已失效" });

  /** Orthogonal discovery-health states that require user-visible attention. */
  const DISCOVERY_HEALTH_ISSUES = Object.freeze({
    partial: { tone: "warning", label: "发现部分异常" },
    error: { tone: "danger", label: "发现失败" },
    rate_limited: { tone: "warning", label: "发现已暂停" },
  });

  // ── the orthogonal contract's presentation ─────────────────────────────
  //
  // Everything above this line reads `item.state`, the single string that packs
  // four independent questions together and is the reason four platforms once
  // showed the same「凭据已就绪」while one of them had merely counted cookie
  // field names offline (spec D1/D2). The tables below read `item.auth`
  // instead, and are what the frontends actually render when the backend
  // speaks the contract. `SOURCE_ACCESS_STATE` stays as the fallback for a side
  // panel installed from the store talking to an older self-hosted backend.

  /**
   * The verdict, keyed on `auth.verification`.
   *
   * `rate_limited` / `blocked` are deliberately not red-for-credential: the
   * platform is refusing *us*, which says nothing about whether the stored
   * credential is good, and sending a user off to re-paste a working cookie is
   * the overclaim this contract exists to remove.
   */
  const SOURCE_ACCESS_VERIFICATION = Object.freeze({
    verified: { tone: "ready", label: "已验证" },
    failed: { tone: "danger", label: "登录已失效" },
    stale: { tone: "warning", label: "验证已过期" },
    unverified: { tone: "pending", label: "待验证" },
    rate_limited: { tone: "pending", label: "频率受限" },
    blocked: { tone: "danger", label: "接入受阻" },
  });

  /**
   * Credential presence, which gates the verdict above.
   *
   * Checked *before* `verification` on purpose. The two dimensions are
   * orthogonal, so nothing stops a backend from pairing "no credential" with a
   * stale `verified`; when they do disagree we under-claim rather than paint a
   * green light we cannot back. That is the same call `hasCredential` makes
   * about `invalid`, and the direction this whole contract leans.
   */
  const SOURCE_ACCESS_CREDENTIAL = Object.freeze({
    none: { tone: "warning", label: "需要登录" },
    invalid: { tone: "warning", label: "凭据不完整" },
  });

  /**
   * `auth_required: false` is its own tier, not a shade of verified.
   *
   * YouTube has not "passed" a check and is not "waiting" for one — it needs no
   * credential at all. Folding it into either of those was what made the side
   * panel paint it the same grey as "we have no idea" (spec D6).
   */
  const ACCESS_NO_AUTH = Object.freeze({ tone: "public", label: "无需登录" });

  /** Public discovery works, while an optional browser identity adds signals. */
  const ACCESS_OPTIONAL_AUTH = Object.freeze({ tone: "public", label: "公开发现可用" });
  const CAPABILITY_AUTH_MODES = Object.freeze({
    anonymous: "匿名可用",
    "optional-credential": "匿名可用，可选凭据增强",
    "login-required": "需要浏览器登录",
  });

  const CAPABILITY_READINESS = Object.freeze({
    ready: { tone: "ready", label: "已就绪" },
    login_required: { tone: "warning", label: "需要登录" },
    identity_required: { tone: "warning", label: "等待识别账号" },
    identity_mismatch: { tone: "danger", label: "账号冲突" },
    identity_switch_required: { tone: "warning", label: "需要重新初始化" },
    stale: { tone: "warning", label: "登录态已过期" },
    unavailable: { tone: "muted", label: "当前不可用" },
  });

  /**
   * A source needing no login can still carry a verifiable credential.
   *
   * YouTube and Bangumi both report `auth_required: false`, but they are not the
   * same case. YouTube has nothing to verify (`verify_method: "none"`); Bangumi
   * is anonymously readable yet, once a personal token is supplied, that token
   * *can* be checked against /v0/me (`verify_method: "live_probe"`). Treating
   * `auth_required === false` as a blanket "no evidence to show" hid a real,
   * network-confirmed verdict on Bangumi. The honest test is not "is login
   * required" but "is there a verification method at all".
   */
  function hasVerifiableCredential(auth) {
    return !!auth && text(auth.verify_method) !== "" && text(auth.verify_method) !== "none";
  }

  /**
   * Evidence strength, keyed on `auth.verify_method`. **The point of the whole
   * refactor.**
   *
   * Before this, B站 (three cookie field names counted offline) and Reddit (a
   * local file that exists and is under a week old) rendered the same green
   * 「凭据已就绪」. Both are now allowed to say 已验证 — the honest verdict, since
   * B站 gained a live probe — but they must not *look* the same, because the
   * evidence behind them is not the same strength.
   *
   * So `rank` is encoded three ways at once, and colour is not one of them:
   *
   *   - a glyph: ◆ filled for evidence from the network, ◇ hollow for evidence
   *     read locally or inferred, — for no capability at all;
   *   - a noun naming the method in words, which is what a screen reader and a
   *     colour-blind reader actually get;
   *   - a border treatment the surfaces apply from `data-rank` (solid / dashed).
   *
   * The noun names the *capability*, not what happened — bilibili reports
   * `live_probe` even before the first probe runs. Whether it actually ran is
   * carried by the freshness suffix, which reads 尚未验证 when `verified_at` is
   * empty. Keeping those two facts separate is what stops a capability from
   * reading as a result.
   */
  const VERIFY_EVIDENCE = Object.freeze({
    live_probe: { rank: "direct", symbol: "◆", label: "联网验证" },
    passive_health: { rank: "direct", symbol: "◆", label: "请求反馈" },
    browser_heartbeat: { rank: "indirect", symbol: "◇", label: "插件心跳" },
    local_file: { rank: "indirect", symbol: "◇", label: "本地文件" },
    task_history: { rank: "indirect", symbol: "◇", label: "历史任务" },
    none: { rank: "unable", symbol: "—", label: "无验证能力" },
  });

  /**
   * A method this build does not know about.
   *
   * Rendered hollow rather than filled: we cannot vouch for the strength of an
   * unrecognised method, and guessing "strong" is the failure mode this table
   * exists to prevent.
   */
  const UNKNOWN_EVIDENCE = Object.freeze({
    rank: "unknown", symbol: "◇", label: "验证方式未知",
  });

  /**
   * One sentence per rank, for surfaces that can afford a tooltip.
   *
   * Lives here rather than in each page so the hint cannot drift from the
   * glyph it explains — the exact failure that left `no_auth` and `unverified`
   * the same grey in one surface and different in another (spec D6).
   */
  const EVIDENCE_HINTS = Object.freeze({
    direct: "结论来自对平台发起的真实网络请求。",
    indirect: "结论来自本地凭据或间接信号，未联网确认。",
    unable: "该来源目前没有可用的验证手段。",
    unknown: "后端使用了本页面不认识的验证方式，强度无法判断。",
    none: "",
  });

  /** No evidence line at all — no contract, or none is needed. */
  const EVIDENCE_ABSENT = Object.freeze({
    rank: "none", symbol: "", label: "", freshness: "", text: "", hint: "",
  });

  /** Said when a method exists but has never produced a verdict. */
  const EVIDENCE_NEVER_RUN = "尚未验证";

  /** Shown when the whole payload is missing (backend unreachable). */
  const OFFLINE_DETAIL = "暂时无法读取来源接入状态，请确认后端服务可用。";

  function text(value) {
    return String(value === undefined || value === null ? "" : value).trim();
  }

  function toneColor(tone) {
    return TONE_COLORS[tone] || TONE_COLORS.muted;
  }

  /**
   * A naked `YYYY-MM-DD HH:MM:SS`, which is UTC but does not say so.
   *
   * The contract's `verified_at` arrives in two shapes: most providers send
   * `datetime.now(UTC).isoformat()` (an explicit `+00:00`), but the three that
   * read a timestamp back out of SQLite send `CURRENT_TIMESTAMP`, which is UTC
   * with no marker on it. `Date.parse` reads *that* as local time, so a
   * verification X made a minute ago would render as「8 小时前」for a UTC+8
   * user — the wrong direction, too: it makes fresh evidence look stale.
   * Normalising here rather than at the call sites keeps the fix in one place.
   */
  const NAKED_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/;

  function normalizeTimestamp(value) {
    const raw = text(value);
    if (!raw) return "";
    // Only when the string really carries no zone: anything with a `Z` or a
    // `±HH:MM` offset fails this test and is passed through untouched.
    return NAKED_TIMESTAMP.test(raw) ? `${raw.replace(" ", "T")}Z` : raw;
  }

  const MINUTE_MS = 60 * 1000;
  const HOUR_MS = 60 * MINUTE_MS;
  const DAY_MS = 24 * HOUR_MS;
  const WEEK_MS = 7 * DAY_MS;

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  /**
   * Render `auth.verified_at` as the age of the verdict.
   *
   * Without this the TTL half of the contract is invisible: 「已验证」 alone
   * cannot tell a probe from a minute ago apart from a heartbeat that has been
   * coasting for two days. Empty string for "never", so callers can branch on
   * falsiness rather than on a sentinel.
   */
  function describeVerifiedAt(value, now) {
    const normalized = normalizeTimestamp(value);
    if (!normalized) return "";
    const stamp = Date.parse(normalized);
    if (!Number.isFinite(stamp)) return "";
    const current = Number.isFinite(now) ? Number(now) : Date.now();
    const age = current - stamp;
    // A clock skew that puts the verdict in the future must not render as a
    // negative age; it was, at worst, just now.
    if (age < MINUTE_MS) return "刚刚";
    if (age < HOUR_MS) return `${Math.floor(age / MINUTE_MS)} 分钟前`;
    if (age < DAY_MS) return `${Math.floor(age / HOUR_MS)} 小时前`;
    if (age < WEEK_MS) return `${Math.floor(age / DAY_MS)} 天前`;
    const when = new Date(stamp);
    return `${pad2(when.getMonth() + 1)}-${pad2(when.getDate())} ${pad2(when.getHours())}:${pad2(when.getMinutes())}`;
  }

  /**
   * The `auth` object, but only when this build can actually read it.
   *
   * Returns null for a backend older than the contract (no `auth` key at all)
   * and for one whose `verification` this build does not recognise. Both fall
   * back to the legacy `state` table rather than rendering a blank chip.
   */
  function authContract(item) {
    const auth = item && typeof item.auth === "object" && item.auth ? item.auth : null;
    if (!auth) return null;
    if (auth.auth_required === false) return auth;
    return SOURCE_ACCESS_VERIFICATION[text(auth.verification)] ? auth : null;
  }

  /** Verdict from the contract: no-auth tier, then credential, then verification. */
  function describeAuthVerdict(auth) {
    if (!auth) return null;
    // No-auth AND nothing to verify → the public tier. A no-auth source that
    // carries a verifiable token (Bangumi) falls through to its verification,
    // so a confirmed token reads as 已验证 rather than a flat 无需登录.
    if (auth.auth_required === false && !hasVerifiableCredential(auth)) return ACCESS_NO_AUTH;
    // Some public sources also expose optional account signals. A missing
    // browser identity must not turn the whole source into「需要登录」: discovery
    // remains available, while the backend-owned detail explains what signing
    // in would add. Keep this contract-driven rather than naming a platform.
    if (auth.auth_required === false && text(auth.credential) === "none") {
      return ACCESS_OPTIONAL_AUTH;
    }
    return SOURCE_ACCESS_CREDENTIAL[text(auth.credential)]
      || SOURCE_ACCESS_VERIFICATION[text(auth.verification)]
      || UNKNOWN_ACCESS;
  }

  /**
   * How strong the evidence behind the verdict is, and how old.
   *
   * @returns {{rank: string, symbol: string, label: string, freshness: string, text: string}}
   */
  function describeEvidence(auth, now) {
    // A no-auth source shows an evidence badge only when it actually has a
    // verifiable credential (Bangumi with a token); YouTube, with nothing to
    // verify, shows none — the same distinction describeAuthVerdict draws.
    if (!auth || (auth.auth_required === false && !hasVerifiableCredential(auth))) {
      return EVIDENCE_ABSENT;
    }
    const method = text(auth.verify_method);
    if (!method) return EVIDENCE_ABSENT;
    const spec = VERIFY_EVIDENCE[method] || UNKNOWN_EVIDENCE;
    // "No capability" has no age to report; every other method does, even if
    // the answer is that it has never run.
    const freshness = spec.rank === "unable"
      ? ""
      : describeVerifiedAt(auth.verified_at, now) || EVIDENCE_NEVER_RUN;
    const head = `${spec.symbol} ${spec.label}`.trim();
    return {
      rank: spec.rank,
      symbol: spec.symbol,
      label: spec.label,
      freshness,
      text: freshness ? `${head} · ${freshness}` : head,
      hint: EVIDENCE_HINTS[spec.rank] || "",
    };
  }

  /**
   * Describe one `/api/sources/status` entry for rendering.
   *
   * Pass `null`/`undefined` for "no data" and the descriptor comes back in its
   * offline shape, so callers never need a second branch for that case.
   *
   * Reads the orthogonal `auth` contract when the backend sends one, and falls
   * back to the legacy `state` when it does not — an older self-hosted backend,
   * or a cached payload written before the contract shipped, still renders a
   * real chip rather than a blank one.
   *
   * `options.now` is a millisecond epoch used to age `verified_at`; tests pass
   * it to stay deterministic.
   *
   * @returns {{
   *   known: boolean, present: boolean, enabled: boolean, contract: boolean,
   *   state: string, tone: string, label: string, color: string,
   *   authRequired: boolean, credential: string, verification: string,
   *   verifyMethod: string, verifiedAt: string,
   *   evidence: {rank: string, symbol: string, label: string, freshness: string, text: string},
   *   detail: string, text: string, line: string
   * }}
   */
  function describeAccess(item, options) {
    if (!item || typeof item !== "object") {
      return {
        known: false,
        present: false,
        enabled: false,
        contract: false,
        state: "",
        tone: UNKNOWN_ACCESS.tone,
        label: "后端未连接",
        color: toneColor(UNKNOWN_ACCESS.tone),
        authRequired: true,
        credential: "",
        verification: "",
        verifyMethod: "",
        verifiedAt: "",
        evidence: EVIDENCE_ABSENT,
        detail: OFFLINE_DETAIL,
        text: OFFLINE_DETAIL,
        line: OFFLINE_DETAIL,
      };
    }
    const state = text(item.state);
    const auth = authContract(item);
    const verdict = describeAuthVerdict(auth);
    const legacy = SOURCE_ACCESS_STATE[state];
    const tokenRejected = text(item.token_state) === "rejected";
    const entry = tokenRejected ? ACCESS_TOKEN_REJECTED : verdict || legacy || UNKNOWN_ACCESS;
    const known = tokenRejected || Boolean(verdict || legacy);
    const enabled = Boolean(item.enabled);
    const evidence = describeEvidence(auth, options && options.now);
    // The backend's own words for this platform, never rewritten here.
    const detail = text(item.detail);
    const combined = detail ? `${entry.label}：${detail}` : entry.label;
    // Single-line surfaces have nowhere to put a second badge, so the evidence
    // rides along in parentheses. The desktop page renders `evidence` on its
    // own and keeps `text` free of it.
    const headline = evidence.text ? `${entry.label}（${evidence.text}）` : entry.label;
    return {
      known,
      present: true,
      enabled,
      contract: Boolean(auth),
      state,
      tone: entry.tone,
      label: entry.label,
      color: toneColor(entry.tone),
      authRequired: auth ? auth.auth_required !== false : true,
      credential: auth ? text(auth.credential) : "",
      verification: auth ? text(auth.verification) : "",
      verifyMethod: auth ? text(auth.verify_method) : "",
      verifiedAt: auth ? text(auth.verified_at) : "",
      evidence,
      detail,
      text: combined,
      line: (enabled ? "" : "(未启用) ") + (detail ? `${headline}：${detail}` : headline),
    };
  }

  /**
   * Fail-closed descriptor for one entry in ``auth.capabilities``.
   *
   * A capability is ready only when the backend sends both ``ready=true`` and
   * ``state=ready``.  This deliberately refuses to infer private readiness from
   * a source-wide anonymous verdict.
   */
  function describeCapabilityReadiness(value) {
    if (!value || typeof value !== "object") {
      return {
        known: false,
        ready: false,
        required: true,
        mode: "",
        state: "unavailable",
        tone: "muted",
        label: "后端未提供能力状态",
        detail: "当前后端无法判断这项能力是否可用。",
      };
    }
    const mode = text(value.mode);
    const state = text(value.state);
    const spec = CAPABILITY_READINESS[state] || CAPABILITY_READINESS.unavailable;
    const known = Boolean(CAPABILITY_AUTH_MODES[mode] && CAPABILITY_READINESS[state]);
    const ready = known && value.ready === true && state === "ready";
    return {
      known,
      ready,
      required: value.required !== false,
      mode,
      state,
      tone: ready ? "ready" : spec.tone,
      label: ready ? CAPABILITY_READINESS.ready.label : spec.label,
      detail: text(value.detail),
    };
  }

  function describeSourceCapability(item, capability) {
    const auth = authContract(item);
    const capabilities = auth && typeof auth.capabilities === "object"
      ? auth.capabilities
      : null;
    return describeCapabilityReadiness(capabilities && capabilities[text(capability)]);
  }

  /**
   * Return an actionable problem for an enabled source, or ``null``.
   *
   * This is deliberately narrower than ``describeAccess``: ``unverified`` and
   * ``syncing`` are normal lifecycle states, while a missing credential, a
   * stale/failed verdict, a blocked request or platform throttling needs the
   * user's attention. Keeping that distinction beside the shared access table
   * makes the dashboard, desktop settings and extension agree on what counts
   * as a problem without teaching any frontend platform-specific wording.
   * The actual explanation remains the backend-owned ``item.detail``.
   *
   * @returns {{tone: string, label: string, detail: string}|null}
   */
  function describeSourceIssue(item, options) {
    if (!item || typeof item !== "object" || item.enabled === false) return null;
    const access = describeAccess(item, options);
    const verification = text(item.auth && item.auth.verification);
    const discoveryState = text(item.discovery_state);
    const discoveryIssue = DISCOVERY_HEALTH_ISSUES[discoveryState] || null;
    const pausedFallback = item.feed_paused === true && !discoveryIssue
      ? { tone: "warning", label: "发现已暂停" }
      : null;
    const healthIssue = discoveryIssue || pausedFallback;
    const actionablePending = verification === "rate_limited";
    const actionableTone = access.tone === "warning" || access.tone === "danger";
    const unknownEnabledState = !access.known;
    if (!healthIssue && !actionablePending && !actionableTone && !unknownEnabledState) return null;
    return {
      tone: access.tone === "danger" || healthIssue?.tone === "danger" ? "danger" : "warning",
      label: healthIssue?.label || access.label,
      detail: access.detail || healthIssue?.label || access.label,
    };
  }

  /**
   * Whether a usable credential is stored for this source.
   *
   * Reads the orthogonal contract rather than any one platform's storage. The
   * setup wizard used to answer this by string-testing `config.bilibili.cookie`,
   * which is blind to the two other places a B站 cookie can live (the data file
   * and the env var) — so a working install could be told it was not logged in.
   * `invalid` deliberately does not count: a jar missing its login fields is
   * present but cannot authenticate, and calling that "connected" is the
   * misleading green light this contract exists to remove.
   */
  function hasCredential(item) {
    const credential = text(item && item.auth && item.auth.credential);
    if (credential) return credential === "present";
    // Backend older than the orthogonal contract: fall back to the coarse state.
    const state = text(item && item.state);
    return state === "ok" || state === "ready";
  }

  /**
   * Three outcomes, three tones. The third one is the whole point: a dead
   * proxy, a closed browser, a throttled platform or YouTube (which needs no
   * login at all) all mean "could not tell", and showing that in red would
   * send a user off to delete a credential that works. The backend picks the
   * outcome — this is a rendering detail, not a second opinion derived from
   * `auth.verification`.
   */
  const VERIFY_TONES = Object.freeze({
    verified: "success",
    failed: "error",
    indeterminate: "neutral",
  });

  /** Fallback when a request never reached a verdict. */
  const VERIFY_NO_RESULT = "本次没有得到验证结论。";

  /**
   * Describe a `POST /api/sources/{slug}/verify` response for rendering.
   *
   * @returns {{tone: string, message: string, replayed: boolean, text: string}}
   */
  function describeVerifyResult(result) {
    const outcome = text(result && result.outcome) || "indeterminate";
    // Every word is the backend's.
    const message = text(result && result.message) || VERIFY_NO_RESULT;
    const replayed = Boolean(result && result.replayed);
    return {
      tone: VERIFY_TONES[outcome] || "neutral",
      message,
      replayed,
      text: message + (replayed ? "（沿用刚才的结果，本次未重新探测）" : ""),
    };
  }

  /**
   * Failing to reach our own backend says nothing about the platform
   * credential, so it is indeterminate too — the same refusal to overclaim
   * that the backend applies to its own probes.
   */
  function describeVerifyError(error) {
    const reason = text(error && error.message) || "请求失败";
    return describeVerifyResult({
      outcome: "indeterminate",
      message: `无法完成验证请求（${reason}），本次未能判定。`,
    });
  }

  /**
   * Run the visible cooldown on a "测试连接" button.
   *
   * The backend debounces each platform, and a replayed answer is otherwise
   * byte-identical to a fresh one — click twice and the button looks broken
   * while quietly serving a stale verdict. So the wait is made visible, and
   * its length comes from the response rather than a per-surface constant.
   */
  function startVerifyCooldown(button, seconds, options) {
    if (!button) return;
    const clock = (options && options.clock) || global;
    clock.clearInterval(Number(button.dataset.cooldownTimer) || 0);
    delete button.dataset.cooldownTimer;
    const label = button.dataset.idleLabel || button.textContent || "测试连接";
    button.dataset.idleLabel = label;
    let left = Math.ceil(Number(seconds) || 0);
    if (!(left > 0)) {
      button.textContent = label;
      button.disabled = false;
      return;
    }
    button.disabled = true;
    button.textContent = `${label}（${left}s）`;
    const timer = clock.setInterval(() => {
      left -= 1;
      if (left > 0) {
        button.textContent = `${label}（${left}s）`;
        return;
      }
      clock.clearInterval(timer);
      delete button.dataset.cooldownTimer;
      button.textContent = label;
      button.disabled = false;
    }, 1000);
    button.dataset.cooldownTimer = String(timer);
  }

  // ── credential rows ────────────────────────────────────────────────────
  // Driven by the `form` descriptor from GET /api/sources/credentials. Kinds
  // that accept a pasted value are the only ones a surface may render an input
  // for: the backend stores no 小红书/知乎 cookie, so a text box on those
  // platforms would accept input that goes nowhere.
  const WRITABLE_FORM_KINDS = Object.freeze(["cookie_textarea", "token_input"]);

  const CREDENTIAL_UNAVAILABLE = "当前没有可展示 Cookie";
  const CREDENTIAL_OFFLINE_SUMMARY = "状态暂不可用";
  const CREDENTIAL_OFFLINE_VALUE = "暂时无法读取当前 Cookie / 登录凭据。";
  const CREDENTIAL_EMPTY_VALUE = "当前没有可展示 Cookie / 登录凭据。";

  function normalizeForm(form) {
    const raw = form && typeof form === "object" ? form : {};
    const kind = text(raw.kind) || "none";
    return {
      kind,
      label: text(raw.label),
      placeholder: text(raw.placeholder),
      envVar: text(raw.env_var) || "",
      requiredKeys: Array.isArray(raw.required_keys) ? raw.required_keys.map(text) : [],
      requiredKeysMode: text(raw.required_keys_mode) || "all",
      actions: Array.isArray(raw.actions) ? raw.actions : [],
      helpText: text(raw.help_text),
      writable: WRITABLE_FORM_KINDS.indexOf(kind) >= 0,
    };
  }

  function hasAction(form, action) {
    return normalizeForm(form).actions.some((entry) => text(entry && entry.action) === action);
  }

  /**
   * Describe one `/api/sources/credentials` entry for rendering.
   *
   * `summary` comes from the backend when it sends one. The local fallback
   * exists only for a side panel talking to a backend older than this contract
   * — it reproduces the generic branch, not any platform's special case.
   */
  function describeCredential(item) {
    if (!item || typeof item !== "object") {
      return {
        present: false,
        available: false,
        summary: CREDENTIAL_OFFLINE_SUMMARY,
        value: CREDENTIAL_OFFLINE_VALUE,
        canCopy: false,
        form: normalizeForm(null),
      };
    }
    const form = normalizeForm(item.form);
    const available = Boolean(item.available);
    const detail = text(item.detail);
    const label = text(item.label) || "Cookie";
    const summary = text(item.summary)
      || (available ? `${label} 已保存（原值不回传）` : detail || CREDENTIAL_UNAVAILABLE);
    return {
      present: true,
      available,
      summary,
      value: text(item.value) || detail || CREDENTIAL_EMPTY_VALUE,
      canCopy: available && hasAction(item.form, "copy"),
      form,
    };
  }

  const api = {
    ACCESS_NO_AUTH,
    ACCESS_OPTIONAL_AUTH,
    CAPABILITY_AUTH_MODES,
    CAPABILITY_READINESS,
    DISCOVERY_HEALTH_ISSUES,
    EVIDENCE_ABSENT,
    EVIDENCE_HINTS,
    OFFLINE_DETAIL,
    SOURCE_ACCESS_CREDENTIAL,
    SOURCE_ACCESS_STATE,
    SOURCE_ACCESS_VERIFICATION,
    SOURCE_CAPABILITIES,
    SOURCE_KEYS,
    INIT_SOURCE_KEYS,
    SOURCE_LABELS,
    TONE_COLORS,
    UNKNOWN_ACCESS,
    UNKNOWN_EVIDENCE,
    VERIFY_EVIDENCE,
    VERIFY_TONES,
    WRITABLE_FORM_KINDS,
    describeAccess,
    describeCapabilityReadiness,
    describeCredential,
    describeSourceIssue,
    describeSourceCapability,
    describeVerifiedAt,
    describeVerifyError,
    describeVerifyResult,
    hasAction,
    hasCredential,
    normalizeForm,
    sourceLabel,
    startVerifyCooldown,
    toneColor,
  };

  global.OpenBiliClawSourceStatus = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
