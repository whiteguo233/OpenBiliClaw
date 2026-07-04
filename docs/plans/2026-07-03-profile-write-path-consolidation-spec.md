# Profile Write Path Consolidation Spec

**Created:** 2026-07-03
**Scope:** memory layer persistence, soul-layer write serialization, structured profile history,
feedback/dialogue learning path unification, state-file hygiene, runtime loop scheduler (optional)

## Goal

Keep the onion-layer incremental profile architecture exactly as it is — signal classification,
per-layer thresholds, LLM delta updates with diff protection, the significance gate, read-time user
overrides. Those are the right design for a local single-user, LLM-cost-sensitive system and are
explicitly **not** being redesigned.

What this spec fixes is that profile *mutation* is currently a convention, not a mechanism. Nine
independent code paths perform unserialized load → mutate → save cycles against the same
`soul.json`, the file itself is written non-atomically, and there is no structured record of who
changed what. The target behavior:

- `soul.json`, `preference.json`, and every other memory-layer / state file survive a crash or
  power loss mid-write (atomic replace, never truncation);
- concurrent profile writers can never silently revert each other's changes (no lost updates
  across `await` windows);
- every profile mutation is recorded in a structured, queryable history with trigger provenance,
  and the profile can be restored to a recent snapshot without relying on consolidation runs;
- feedback and dialogue learning become pipeline paths instead of a parallel engine with its own
  scheduler, lock, and cursor;
- all JSON state files converge on one atomic write helper;
- (optional) the 15 hand-rolled runtime loops converge on a periodic-job abstraction.

No phase in this spec changes prompt contracts, layer thresholds, override semantics, or the
LLM prompt-cache convention.

## Priority Classification

Three tiers. **MUST（必须做）** items fix live correctness/durability defects — the spec is not
considered done without them. **RECOMMENDED（强烈建议）** items are follow-ons whose cost drops
sharply once the MUST items land; defer them if needed, but don't drop them. **OPTIONAL（可做可不做）**
items are hygiene/ergonomics with no correctness impact; do them opportunistically or never.

| Phase | Content | Tier | Why this tier |
| --- | --- | --- | --- |
| 1 | Atomic persistence for all layers/state files | **MUST** | `soul.json` can be truncated by a crash mid-write today; data-loss bug, trivial fix |
| 1 | D7 doc/comment cleanup (`propagate_event`, soul.md §2) | **MUST** | Misleads readers and subagents today; near-zero cost, bundled with Phase 1 |
| 2 | `ProfileStore` serialized write path | **MUST** | Live lost-update race across 9 writers; silent profile corruption in normal operation |
| 3 | Structured history + snapshots + restore CLI | RECOMMENDED | Debuggability/revert; nearly free once Phase 2 exists, but nothing breaks without it |
| 4 | Feedback/dialogue unification into the pipeline | RECOMMENDED | Removes duplicated machinery; current dual path is correct once Phase 2 serializes it, just costly to maintain |
| 5 | Cursor migration to SQLite `runtime_state` | OPTIONAL | Atomicity already solved by Phase 1; only transactionality with event rows remains |
| 6 | `PeriodicJob` abstraction for runtime loops | OPTIONAL | Pure ergonomics/observability; zero behavior change by design |

Dependency note: Phase 4 requires Phase 2. Phase 3 requires Phase 2. Phases 5–6 are independent.

## Current Diagnosis

### D1. Layer persistence is not atomic

`MemoryLayer.save()` (`memory/manager.py:100-101`) writes with a plain
`open(path, "w") + json.dump`. A crash, OOM kill, or power loss mid-write truncates the layer
file. For `soul.json` this destroys the profile; the existing "bad LLM output never overwrites
soul.json" invariant does not protect against this because the corruption happens below the
application layer.

An atomic writer already exists: `memory/json_state.py:66` `_atomic_write_json` (tempfile +
`os.replace`, plus process/file locking in `update_json_state`). It is used by the speculators'
state files — but **not** by `MemoryLayer.save()`, nor by the hand-rolled `json.dump` call sites
in `manager.py` (`save_feedback_state:264`, `save_account_sync_state:322`,
`save_source_bootstrap_state:344`, `save_insight_candidates:532`, `save_cognition_updates:548`,
`save_profile_overrides:581`). The most important files in the system have the weakest write path.

### D2. Lost-update race on the soul layer

`ProfileUpdatePipeline._update_layer` (`soul/pipeline.py:836-869`) follows this pattern:

```text
profile = self._load_profile()          # snapshot at T0
update_result = await update_layer(...) # LLM call: seconds to tens of seconds
self._save_profile(profile)             # clear() + update() + save() at T1  — whole-document overwrite
```

Everything runs on one asyncio event loop, so races only occur at `await` points — but the awaits
here are LLM calls, the longest possible windows. Any writer that commits between T0 and T1 is
silently reverted when `_save_profile` writes the stale T0 snapshot back.

Complete writer inventory (verified 2026-07-03):

| # | Writer | Location | Trigger |
| --- | --- | --- | --- |
| 1 | Pipeline layer updates + portrait regen | `soul/pipeline.py:880` `_save_profile` | per-layer threshold / strong signal |
| 2 | Initial full build | `soul/engine.py:341` | `init` / `rebuild-profile` |
| 3 | Insight feedback snapshot sync | `soul/engine.py:814` | `POST /api/insights/feedback` |
| 4 | Dialogue significant rebuild | `soul/engine.py:924` | chat turns |
| 5 | Feedback batch significant rebuild | `soul/engine.py:1026` | `FeedbackBatchScheduler` |
| 6 | Consolidation apply | `soul/consolidator.py:120` | 12h consolidation |
| 7 | Cognition cycle sync | `soul/cognition_cycle.py:388` `_sync_to_profile` | 12h cognition |
| 8 | Dislike writeback | `soul/dislike_writeback.py:129` | dislike events |
| 9 | Probe promotion writeback | `api/app.py:4618` (`merge_confirmed_interest`) | interest probe confirmed |

The only lock in this set is `_feedback_batch_lock` (`engine.py:160`), which prevents writer #5
from re-entering **itself**. Nothing serializes writers against each other.

Concrete failure scenarios:

- CognitionCycle syncs `recent_awareness` / `active_insights` while an INTEREST update awaits its
  LLM call → the sync is reverted; the fresh awareness disappears from profile-summary until the
  next 12h run.
- A probe promotion (`app.py:4618`) lands while portrait regeneration awaits its LLM call → the
  confirmed interest is lost. The probe machinery believes it succeeded; the interest never
  reappears without a second confirmation cycle.
- A dislike writeback lands during a ROLE delta update → the dislike is reverted; the user keeps
  seeing content they explicitly rejected.

Two aggravating details:

- `MemoryLayer.data` triggers `_reload_if_stale` (`manager.py:87-89`), so in-flight `OnionProfile`
  copies and the layer's `_data` can diverge mid-flight in surprising ways.
- The `preference` layer has the same unserialized whole-layer-replace pattern (pipeline INTEREST
  updater vs. feedback batch vs. consolidation).

User overrides (`profile_overrides.json`) are **not** exposed to this race — they live in a
separate file and are applied at read time. That design is correct and unchanged.

### D3. Two parallel learning engines

The pipeline (`soul/pipeline.py` + `soul/layer_updaters.py`) and the `SoulEngine` batch methods
(`process_feedback_batch_if_needed`, `learn_from_dialogue`) are independent mechanisms that both
end in `PreferenceAnalyzer.analyze_events` and both can rewrite the preference layer and rebuild
the profile. The feedback path carries its own scheduler (`runtime/feedback_scheduler.py`, 5s
debounce), its own lock, its own cursor file (`feedback_state.json`), and its own
changelog/notify handling. Two code paths must be understood, tested, and kept consistent for
what is conceptually one operation: "learn from new evidence, rebuild deep layers only if the
change is significant."

### D4. No structured profile history

Mutation records today: a human-readable `soul_changelog.md` (append-only markdown, no structure)
and per-run snapshots only for consolidation (`consolidation_runs/<run_id>.json`, revertible via
`profile-consolidate --revert`). There is no way to answer "which path changed
`interest_domains` at 14:32 and what did it look like before" for the other eight writers, and no
way to restore yesterday's profile unless the damage happened to come from consolidation.

### D5. State-file sprawl

`data/memory/` holds ~10 hand-rolled JSON state files (`pipeline_state`, `feedback_state`,
`cognition_cycle_state`, `consolidation_state`, `speculative_state`, `avoidance_state`,
`insight_candidates`, `cognition_updates`, `profile_overrides`, `account_sync_state`,
`source_bootstrap_state`, …), each with its own load/save code and its own partial-write failure
mode. Some use `json_state.py`, most do not.

### D6. Runtime loop proliferation (observation)

`RuntimeController.run_forever` (`runtime/refresh.py:1062-1124`) spawns 15 independent loops,
each hand-rolling its interval, gating (`_llm_work_allowed`), and error handling. Cross-loop
coupling has already caused one production incident (search and explore sharing a process-level
escalation cooldown). This is not urgent, but each new feature currently adds another loop.

### D7. Stale documentation and comments

- `MemoryManager.propagate_event` docstring (`manager.py:826`) claims it "may trigger updates in
  higher layers"; the three `# TODO` comments (`manager.py:856-858`) imply the same. In reality it
  only inserts the event row — all upward propagation is explicit from the API layer. A reader
  (or subagent) following the docstring looks for propagation in the wrong place.
- `docs/modules/soul.md` §2 frames the post-init behavior-event path around `analyze_events()`;
  the actual incremental path is the pipeline. The two descriptions coexist and are easy to
  conflate.

## Design

### 1. Atomic persistence everywhere (Phase 1 — MUST)

Promote `json_state._atomic_write_json` to a public helper (`write_json_atomic(path, payload)`)
and route every JSON write in the memory package through it:

- `MemoryLayer.save()` — keep UTF-8 / `ensure_ascii=False` / `indent=2`; keep the
  `_loaded_mtime` bookkeeping (stat **after** `os.replace`).
- All `json.dump` call sites in `manager.py` listed in D1.
- `soul/profile_renderer.sync_profile_files` outputs (`soul_profile.json`, `soul_profile.md`) —
  the markdown mirror should use the same tempfile + replace pattern via a text variant
  (`write_text_atomic`).

Failed writes must clean up their temp file. Keep the existing helper's fsync-before-replace
behavior as-is (`json_state.py:74`); document the durability contract (old-or-new, never
truncated) in the helper's docstring.

### 2. ProfileStore: one serialized mutation path (Phase 2 — MUST)

New module `soul/profile_store.py`:

```python
class ProfileStore:
    """Sole owner of soul-layer (and preference-layer) mutations."""

    async def mutate(
        self,
        source: str,                                   # e.g. "pipeline.interest", "cognition.sync"
        fn: Callable[[OnionProfile], MutationResult],  # synchronous, no awaits
        *,
        allow_empty_overwrite: bool = False,
    ) -> MutationOutcome: ...

    def snapshot(self) -> OnionProfile: ...            # fresh defensive copy, read-only use
```

Critical-section contract (the core of the design):

1. Acquire the store's `asyncio.Lock`.
2. Load a **fresh** profile from the soul layer (not a caller-provided snapshot).
3. Apply `fn` — a pure, synchronous closure. The store asserts `fn` is not a coroutine
   function; holding the lock across an LLM call is forbidden by construction.
4. Validate: refuse to replace a non-empty profile with an empty one unless
   `allow_empty_overwrite=True` (this absorbs the existing "bad JSON never overwrites soul.json"
   invariant into the mechanism).
5. If `fn` reports a change: save atomically, append a history record (§3), render the changelog
   entry, fire `_notify_profile_changed` — one emit point for all of it.
6. Release the lock. Hold time is microseconds-to-milliseconds; warn if lock wait exceeds 5s.

**LLM work moves outside the lock.** The calling pattern for every delta-based writer becomes:

```python
base = store.snapshot()
delta = await compute_role_delta(base, signals, llm)     # slow, unlocked
await store.mutate("pipeline.role", lambda p: apply_role_delta(p, delta))  # fast, locked
```

Because the closure re-applies the delta to a *fresh* profile, a writer that committed during the
LLM call is preserved instead of reverted. Deltas from different writers touch disjoint concerns
(interest tree vs. awareness snapshot vs. dislikes vs. role fields), so field-level
last-writer-wins on a fresh base is the correct merge semantics; the failure mode this spec
eliminates is *whole-document* reverts.

Migration table — every writer from D2 gets a `source` tag and is converted:

| Writer | New source tag |
| --- | --- |
| Pipeline SURFACE/INTEREST/ROLE/VALUES/CORE | `pipeline.<layer>` |
| Portrait regeneration | `pipeline.portrait` |
| Initial/full build | `engine.init_build`, `engine.rebuild` |
| Insight feedback sync | `engine.insight_feedback` |
| Dialogue significant rebuild | `engine.dialogue` |
| Feedback batch rebuild | `engine.feedback_batch` (retired in Phase 4 → `pipeline.feedback`) |
| Consolidation apply | `consolidation.apply` |
| Cognition cycle sync | `cognition.sync` |
| Dislike writeback | `dislike.writeback` |
| Probe promotion | `probe.promotion` |
| Manual revert (§3) | `manual.revert` |

Preference-layer replacement (INTEREST updater, feedback batch, consolidation) goes through the
same store (`mutate_preference` or a `layers=` parameter — implementation's choice, same locking
and history semantics).

**Enforcement (mirrors the prompt-cache convention test):** the store sets a
`contextvars.ContextVar` while inside its critical section; `MemoryLayer.save()` for the `soul`
and `preference` layers logs a warning (test mode: raises) when called outside it. Plus a static
test that greps `src/` for `get_layer("soul")` followed by `.save()` outside
`soul/profile_store.py`. This keeps writer #10 from ever being added off-path.

Rejected alternative: one big lock held across LLM calls. Simple, but serializes 30-second LLM
calls behind each other, stalls the pipeline tick, and couples unrelated layers' latency. The
compute-outside / apply-inside pattern gets the same safety with no added latency.

### 3. Structured profile history (Phase 3 — RECOMMENDED)

Because Phase 2 gives a single write point, history is nearly free:

- **`data/memory/profile_history.jsonl`** — one record per committed mutation:

```json
{
  "seq": 1041,
  "ts": "2026-07-03T14:32:07+08:00",
  "source": "pipeline.interest",
  "summary": "interest update: +2 specifics under 科技, 1 dislike added",
  "changed_fields": ["interest_domains", "disliked_topics"],
  "digest_before": "sha256:…",
  "digest_after": "sha256:…"
}
```

  Rotation: keep the newest ~2000 records / 5 MB; rotate to `.1`. Append failure must never block
  the profile save (log and continue).

- **`data/memory/profile_snapshots/YYYY-MM-DD.json`** — first committed mutation of each day also
  writes a full profile snapshot (atomic); keep 14 days. Snapshots are the revert substrate —
  simpler and more robust than maintaining inverse diffs.

- **CLI**:
  - `openbiliclaw profile-history [--limit N] [--source TAG]` — human-readable table of recent
    mutations (answers "what changed my profile and when").
  - `openbiliclaw profile-restore --date YYYY-MM-DD [--dry-run]` — restores a snapshot **through
    the store** (`source=manual.revert`, `allow_empty_overwrite` stays false), so the restore
    itself is atomic, serialized, and recorded in history. `--dry-run` prints a field-level diff.

- `soul_changelog.md` remains the human-readable render, now emitted only by the store.

Existing consolidation run snapshots / revert stay as-is; they cover a different granularity
(semantic like/dislike merges with op-level revert).

### 4. Feedback and dialogue path unification (Phase 4 — RECOMMENDED)

Fold the `SoulEngine` batch learning paths into the pipeline so there is exactly one learning
mechanism:

- **Feedback**: `POST /api/feedback` keeps recording the event and the immediate cognition card,
  then ingests a `FEEDBACK` signal into the pipeline (it is already a strong-signal type →
  INTEREST updates immediately). The "significant change → full rebuild" logic becomes a pipeline
  policy stage: after an INTEREST update whose drained signals include feedback,
  evaluate `_preference_changed_significantly`; if significant, run the full
  `ProfileBuilder.build` (LLM outside lock, apply via `store.mutate("pipeline.rebuild", …)`).
- **Retire**: `FeedbackBatchScheduler`, `_feedback_batch_lock`, `feedback_state.json` (the
  pipeline buffer + backfill cursor already provide at-least-once coverage; verify the CLI
  `feedback` command routes through pipeline ingest before deleting the cursor).
- **Dialogue**: `learn_from_dialogue`'s preference re-analysis folds into the INTEREST updater;
  dialogue insights already enter as `DIALOGUE_INSIGHT` signals routed to deep layers.
- **Behavior preserved, explicitly**: feedback still updates INTEREST with min_signals=1
  (strong-signal bypass); burst coalescing moves from the 5s debounce to the INTEREST buffer's
  `min_interval` semantics (strong signals still drain promptly — confirm the resulting LLM call
  rate is not worse than today's debounced batching before removing the scheduler; if it is, give
  FEEDBACK a small dedicated debounce inside the pipeline rather than keeping the parallel
  engine).
- During transition the engine methods become thin wrappers delegating to pipeline ingestion,
  with deprecation comments; remove them once the extension and CLI paths are verified.

This phase depends on Phase 2 (both paths must already write through the store) and should not
start before it.

### 5. State-file hygiene (Phase 5 — OPTIONAL)

- Everything already writes atomically after Phase 1; this phase is consolidation only.
- Migrate pure cursors/watermarks (`cognition_cycle_state` cursors, profile-pipeline backfill
  cursor, anything left of `feedback_state`) into a SQLite `runtime_state(key TEXT PRIMARY KEY,
  value TEXT, updated_at TIMESTAMP)` table for transactionality with the event rows they
  describe. Keep human-inspectable JSON for everything that a user might want to read or edit
  (`profile_overrides`, speculative state). Do not migrate for its own sake — only cursors whose
  desync from SQLite events has bitten or plausibly could.

### 6. Runtime periodic-job abstraction (Phase 6 — OPTIONAL)

Introduce a `PeriodicJob` dataclass (name, interval, gate predicate, jitter, failure backoff,
last-run stats) and have `RuntimeController` create its loops uniformly from a job list instead
of 15 bespoke `_loop_*` methods. Mechanical migration, zero behavior change, one place to add
observability (`/api/runtime-status` lists each job's last run / next due / consecutive
failures). Explicit non-goal: no shared cooldowns or cross-job coupling — the pool-replenishment
incident showed shared state between loops is the hazard, not the loops themselves.

### 7. Documentation and comment cleanup (with Phase 1 — MUST)

- Fix `propagate_event` docstring and remove/replace the three stale TODOs (`manager.py:826,
  856-858`) with a statement of the actual contract: "persists the event only; upward propagation
  is explicit via the profile pipeline."
- `docs/modules/soul.md` §2: state explicitly that `analyze_events` is init/rebuild only and the
  post-init incremental path is `ProfileUpdatePipeline`.

## Data Model

- `profile_history.jsonl` — schema in §3; append-only, rotated.
- `data/memory/profile_snapshots/YYYY-MM-DD.json` — full profile snapshots, 14-day retention.
- (Phase 5) `runtime_state` SQLite table for cursors.
- No changes to `soul.json` / `preference.json` schemas, `OnionProfile.version`, or the SQLite
  event schema.

## Error Handling

- `store.mutate`: if `fn` raises, nothing is written; the error is logged with `source`; callers
  keep their existing recovery (the pipeline already re-buffers drained signals on failure —
  `pipeline.py:854-855` — and that behavior is preserved).
- Empty-overwrite rejection: log at warning, skip the save, record a `rejected` history entry so
  the attempt is visible in `profile-history`.
- History/snapshot append failures never block or roll back a profile save.
- Atomic writer removes its temp file on failure; a leftover `.tmp` file is never read.
- `profile-restore` refuses to run when the daemon holds the store lock busy (i.e. waits with a
  timeout and clear message) and always writes a history record for the restore itself.
- Lock diagnostics: warn when any `mutate` waits >5s (should never happen given no awaits inside
  the critical section; if it fires, something is holding the lock wrongly).

## Rollout Plan

Each phase is an independently shippable PR with its own doc updates
(`docs/modules/soul.md`, `docs/modules/memory.md` where touched, `docs/changelog.md` entry;
Phase 3 also updates `docs/modules/cli.md` for the new commands).

1. **Phase 1 (MUST)** — atomic write convergence + D7 doc/comment cleanup.
   Tests: interrupted-write simulation (patch `os.replace` / kill between tempfile write and
   replace → original file intact); an inventory test asserting no bare
   `json.dump(open(...))` writes remain under `src/openbiliclaw/memory/`.
2. **Phase 2 (MUST)** — `ProfileStore`; migrate all nine writers; changelog + WS notify emitted
   only by the store; context-var enforcement guard.
   Tests: a regression test reproducing the lost update (writer A loads, awaits; writer B
   commits; writer A commits — B's field must survive); enforcement test that off-path
   `soul` layer saves raise in test mode; behavior parity for each migrated writer.
3. **Phase 3 (RECOMMENDED)** — history JSONL + daily snapshots + `profile-history` /
   `profile-restore` CLI.
   Tests: rotation, restore round-trip through the store, restore-refuses-empty.
4. **Phase 4 (RECOMMENDED)** — feedback/dialogue unification; retire `FeedbackBatchScheduler` and
   `feedback_state.json`.
   Tests: feedback burst coalescing (LLM call count under a 10-feedback burst ≤ today's),
   significance gate still triggers full rebuild on high-weight interest shifts, CLI `feedback`
   path parity.
5. **Phase 5 (OPTIONAL)** — cursor migration to SQLite `runtime_state`.
6. **Phase 6 (OPTIONAL)** — `PeriodicJob` abstraction in `RuntimeController`.

Phases 1–2 are the point of this spec and it is not done without them; 3–4 are strongly
recommended follow-ons; 5–6 are opportunistic and may be dropped without weakening the earlier
phases.

## Non-Goals

- No multi-user abstraction; the single-user assumptions (single `soul.json`, socratic prompt
  exception) are deliberate and stay.
- No migration of `soul.json` / `preference.json` into SQLite; whole-file JSON is the right size
  and keeps human readability and the prompt-cache convention simple.
- No changes to onion-layer semantics: signal classification, thresholds, strong-signal bypass,
  diff protection, `_PORTRAIT_TRIGGER_LAYERS`, or the significance gate's thresholds.
- No changes to prompt builders or the LLM prompt-cache convention.
- No change to override semantics — overrides remain a read-time overlay in their own file and
  are intentionally outside the store's write path.
- No change to consolidation's op-validation / revert machinery.
- No cross-job shared state in Phase 6 — uniform scheduling only.

## Acceptance Criteria

- [ ] Killing the process at any point during a profile save leaves `soul.json` readable as
      either the old or the new version, never truncated (verified by an interrupted-write test).
- [ ] Every JSON write under `src/openbiliclaw/memory/` and `soul/profile_renderer.py` goes
      through the shared atomic helper; the inventory test enforces it.
- [ ] All nine writers from D2 mutate the soul layer only via `ProfileStore.mutate` with a
      `source` tag; the enforcement guard raises in tests on any off-path save.
- [ ] The lost-update regression test passes: a mutation committed during another writer's LLM
      await survives the second writer's commit.
- [ ] No `await` can occur inside the store's critical section (asserted by construction).
- [ ] A non-empty profile can never be replaced by an empty one through the store without
      `allow_empty_overwrite`.
- [ ] `openbiliclaw profile-history` shows source-tagged records for pipeline, cognition,
      consolidation, dislike-writeback, and probe-promotion mutations.
- [ ] `openbiliclaw profile-restore --date <d>` restores a snapshot atomically, through the
      store, and records the restore in history.
- [ ] After Phase 4 there is exactly one learning path: `FeedbackBatchScheduler` and
      `feedback_state.json` are gone, feedback still updates INTEREST immediately, and the
      significance gate still controls full rebuilds; LLM call volume under feedback bursts does
      not regress.
- [ ] `propagate_event` docstring/TODOs and `soul.md` §2 match actual behavior.
- [ ] `docs/changelog.md` and touched module docs updated per phase (pre-merge checklist in
      CLAUDE.md applies to each PR).
