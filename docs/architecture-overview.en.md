# 🏛️ Architecture Overview

> The architecture overview ASCII diagrams are split out of the README to keep it compact.
> For full architecture detail, see [Architecture](architecture.md) and the [visual architecture diagrams](index.md#可视化架构图).


```text
native Android / iOS app (embedded tsnet) → user's tailnet → desktop Go tsnet helper (default off)
                                                           → fixed 127.0.0.1:effective-port → same FastAPI
No system Tailscale is required on the computer; no Funnel/Serve; add the app password beyond ACLs
Web, Linux, macOS, and Windows Flutter clients are outside this embedded-tsnet support scope

interactive (dialogue / config probe) ──────────────┐
                                                    ├─ runtime total gate (default 4) ─ ordered instance chain ─ adapter
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  ├─ low-stock supply includes explore queries / source extraction
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0

guided init: signals → preferences → full profile commit → discover → evaluate → copy → canonical ready
             Instagram: read-only liked/saved/following → init-owned topic/creator → small-batch eval/copy
                                                              └→ optional probes after terminal state

Agent hosts (OpenClaw / Hermes / WorkBuddy)
        → capabilities(agent-bridge/v2) + JSON CLI / skill descriptors
        → integrations.agent alias / integrations.openclaw compatibility adapter
        → runtime / soul / recommendation / saved_sync owners

config recovery draft (normal or degraded; business APIs remain gated)
             ├→ /api/config/probe-service → temporary registry → total gate
             └→ /api/config/discover-models → exact instance GET /models (no write)
                                           → editable model list + local effort advisory
Douyin supply: daemon presence gate (explicit manual calls bypass it) → one shared plugin-cycle budget
              → terminal dy_task → pending_eval; absent means zero enqueue, failures back off
local migration: export → config minus api.auth + online SQLite snapshot + portable files → plaintext .obcbackup
                 import(request_id) → processing(upload/validate) → private stage ↔ status/cancel
                                    ↘ General-open force reconcile; applied prefs once/browser/migration_id
                                    → restart + project/canonical data-dir locks → replace | rollback
durable reply: reply_to_turn_id + fixed time/payload → POST-time frozen binding → pending SQLite → rowid-serial reply worker → visible completion CAS (app-stable dialogue lease)
post-reply learning/object settlement: independent 11-kind typed queue → actual worker + guard
confirmation entry (pending list/cards) → one anchor(kind+ref+generation) → frozen admission / relation matrix
                          ├→ pending≤3 · user no cooldown / system 12h+object 72h · confirmation-first attachment
                          ├→ busy worker: dialogue_busy + Retry-After → waiting UI auto-retry
                          ├→ active confusion: current holder only; hidden once this session has its turn
                          ├→ frozen kind/ref/generation → worker-only apply → event/object/derived/marker → applied
                          │                                                └→ publication-only retry → projection / exact release
                          ├→ one context digest → prompt/history/event/learn/settlement provenance
                          ├→ action local≤1s: completed 200 / blocked 202 → popup/mobile/desktop poll 1/2/5s, ≤30s
                          └→ confusion FIFO≤5 / head fencing / 12h recovery
config save: persist → HTTP 202 queued/apply_revision → latest-wins background apply queue → apply-status / final receipt; data_dir is persisted only and switches after a full restart
config hot reload: accepting drain old worker → atomic pause/revoke → new worker; 25m safety window
realtime: runtime-stream 20s idle heartbeat → transient close shows reconnecting and retries
images: proxy foreground + refresh prefetch → app-stable lane (total 4 / bg 3, fg priority)
                                            → cache-key singleflight → public DNS (proxied: fixed DoH)
                                            → pinned IP / original Host and TLS → atomic cache
Instagram manual replenish → source quota/task locks → discover/eval/copy → usable supply or explicit failure
```

```
┌────────────────────────────────────────────────┐
│ Browser Extension (Chrome / Firefox / Safari)  │
│  Behavior capture · MAIN-world taps (comment/  │
│  danmaku, xhs strong signal) · Cookie · Tasks  │
└──────────────────────┬─────────────────────────┘
                       │ HTTP default: IPv4 0.0.0.0 + IPv6 [::] → REST / WebSocket
                       │ Optional HTTPS: public Caddy :443 / LAN TLS Proxy :8443 → loopback HTTP → same API
                       │ Optional tailnet: Android/iOS App tsnet → helper → effective-port loopback → same API
                       │ + Desktop Web (/web) · Mobile Web (/m) · QR LAN-IP
                       │ + ping preflight → /web · /setup · /m → config + in-process recovery
┌──────────────────────▼─────────────────────────┐
│               Agent Orchestration               │
│ Skills · Dialogue · Runtime · 10s undo barrier   │
├─────────┬──────────┬───────────┬───────────────┤
│  Soul   │  Memory  │ Discovery │ Recommendation │
│ Engine  │  System  │Discovery +│     Engine     │
│         │          │ Admission │                │
├─────────┴──────────┴───────────┴───────────────┤
│ Events/recommendation clicks → generic durable cursor ─┐ │
│ Content feedback → content_feedback durable cursor ────┴→ atomic buffer+cursor checkpoint │
│ 30-day history: click events + recommendations + saved_item_removals → paged/lazy UI │
│ dislike: exact card hides synchronously; durable topic → final history/serve/push recheck │
│ discovery may keep broad search; async semantic purge optimizes inventory, not correctness │
│ cold start fence+task admission → listener; background recovery → tick_if_buffered │
│ hot reload pause/drain/recover then rebind; periodic maintenance alone calls tick │
│ Dialogue → typed settlement worker → learning       │
│ Legacy batch only when rollback flag=false     │
│ Init barrier: profile commit → discover/evaluate/copy → ready │
│ Bilibili supply: relevance search + budgeted 1×5 pubdate recent lane → shared evaluation │
│ Evaluation: time-neutral relevance + grounded temporal evidence → eligible / review hold / expired + publication bonus │
│ Temporal shadow: bonus vs no-bonus Top10/50/100 aggregates → class/source/age audit (no serving change) │
│ Images: proxy fg + refresh prefetch → app-stable 4/3 lane → singleflight/atomic cache │
│ Soul cognition: dual pending cooldown · one anchor · worker-only settlement · winner receipt · confusion FIFO · ledger · deep gate │
│   LLM adapters · Source adapters (SourceAdapter) │
│ Module route → LLM instance chain → adapter · SourceAdapter │
│ Optional visual prewarm: covers / profile centroids / keyframes + danmaku │
│ provenance (provider/model/dim/sampling) → empty-success / retryable fail │
│ Config recovery draft (normal/degraded) → temp probe / exact /models (no write) │
│ Local migration: checksummed .obcbackup → request-id pending ↔ status/cancel → restart replace/rollback │
│ Source-family registry: alias · strategy · URL host │
│             → pool accounting · durable seen_items ledger │
│ Bangumi public API → search/ranked/date producer → shared eval │
│ GitHub REST API → repository search/ranked/latest → shared eval │
│ GitHub public starred repositories → favorite events → guided init (no background polling) │
│ V2EX public API/Feed → bounded Topic/Reply enrichment → five modes → shared eval │
│ V2EX identity ladder: verified PAT > observed browser > accepted user; mismatch pauses only account projection │
│ Temporal lifecycle: verbatim evidence + code-owned review clock → serve / temporal_review_hold / expired │
│ Evaluator prefilter stays shadow → privacy-safe decision/raw-score join → read-only gate (no auto-enforce) │
│ Named cognition views → task gate: compact only for awareness_confusions; others legacy │
│ Token diet: per-offset preference packing; weighted recent/judged/relevant/important insight≤40 → full merge │
│ Keyword planner → safe 24h cross-digest pending reconcile → deficit/generate/claim (0=hard expiry) │
│ Admitted backlog → copy watermark ∪ visible topic-slot gap → eligible-first copy (0=legacy drain-all) │
│ API projected=available+eligible copy-pending+evaluated → 3×30 workers → serial admit → UI │
│ API raw-empty → wake under-share sources now → real progress resets / duplicate-only waves back off │
│ Delight gate: formal copy/topic ready + seen_items guard → score/snapshot → UI × writes seen ledger │
│ Inventory API/OpenClaw startup hook → recover/maintain → expose LLM │
│ Reshuffle: current-card exclusion → hold/stale retirement + PoolServeSnapshot → final temporal recheck + atomic write │
│ Platform scope (PC Web tabs only): source_platform → scoped candidates, no cross-platform floor → same rank/copy/persist │
│ Platform inventory: platform-availability → same canonical servable set → total == Σ by_platform │
│ Background maintenance: isolated worker → ≤50 rows/batch; unchanged skip / 10m sweep │
│ /api/saved/* · router · Bilibili native save      │
│ Six adapters → ExtensionNativeSaveBroker → extension_native_save_jobs │
│ eight-source task multiplex: xhs / dy / yt / zhihu / reddit / linuxdo / v2ex / weibo │
│ Extension-online periodic re-pull (off by default; explicit opt-in): Runtime → seven bootstrap sources (global serial; excludes weibo / GitHub) → installed extension │
│ task-result → staged durable ingress → atomic bounded seen keys (5,000/source) → terminal │
│ V2EX complete favorite snapshots → two confirmed misses → durable retraction/restore outbox → account-scoped Node affinity │
│ XHS auto tasks: source/scheduler gate → SQLite pacing/breaker → no new tab while off/limited │
│ XHS search: inactive tab → MAIN response normalization → isolated replay / DOM fallback │
│ Linux.do: isolated task tab → same-origin GET → five discovery / three bootstrap paths │
│ extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension │
│ real targets (YouTube `OpenBiliClaw` / `YouTube Watch Later`; Zhihu global favorite) → safe task-result │
│ runner-owned task tab (collector off on first load/reload) → abort+await old write → fresh document READY → read-only verify │
│ exact terminal callback replay → canonical ACK; changed terminal remains 409 │
│ trusted-local E2E exact auth → one saved-sync item → six-field callback │
│ unsupported_adapter_missing retryable · unsupported_content_type local-only │
│ Canonical ID · Local-first sync · Task poll · SQLite (events · seen ledger · pool · recs · saved/tasks · removal snapshots)│
│ Six adapters → broker → shared MV3 recovery barrier → Reddit/X/YT/XHS/DY/Zhihu executors (6/6 fixture + real-account)│
└────────────────────────────────────────────────┘

Web/API durable → rowid reply worker → app-stable dialogue lease(max active 1) → SocraticDialogue(queued) → visible CAS
delight/legacy/interest-probe/avoidance chat ───────────────────────────────┘ (reply + required effects share the lease)
post-reply 11-kind learning/settlement → independent typed settlement worker (not reply backlog)
CLI/OpenClaw → SocraticDialogue(legacy_direct) → user+agent history → direct learning outside queue/guard
learning → bypass background admission; keep total gate ── new dislike: shared purge → content_cache
transient/provider/timeout/cancel → rollback provisional history → durable pending + head retry; explicit invalid/empty → failed CAS
durable turn → fixed time/payload → confirmation entry (pending list/cards) → frozen anchor admission → relation matrix
                                                  └→ card/anchor/chat/probe/confusion/replay/legacy all worker-only
card action → synchronous 200 fast path | 202 processing → popup/mobile/desktop poll; CLI has no action

Desktop startup: recommendation hydration │ runtime hydration │ secondary health/profile/activity/config hydration (independent)
Desktop background resume (cards already loaded): skip the pool-filling recommendation GET │ sync runtime / inventory status only

Overseas traffic: `[network].mode` → system proxy (default) / direct / custom proxy → LLM, YouTube, X/Reddit CLIs, Bangumi, GitHub source/API stats, and updater; CN clients including V2EX remain isolated and direct
Manual Douyin discovery: CLI discover → daemon-equivalent producer → per-keyword outcomes → extension search/hot/feed → pending-eval pool
```
