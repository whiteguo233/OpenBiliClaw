# Avoidance Probes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add proactive “不喜欢领域探针” that confirms user avoidance boundaries across backend, mobile Web, PC Web, plugin, and OpenClaw.

**Architecture:** Add an `AvoidanceSpeculator` that runs beside the existing positive `InterestSpeculator`, with its own active/cooldown state, runtime history, API endpoints, and UI event type. Reuse existing probe selection, axis, chat, and feedback patterns where safe, but keep the new lifecycle isolated to avoid regressions in current interest probes.

**Tech Stack:** Python dataclasses, FastAPI, SQLite-backed memory/profile layers, pytest, vanilla JS mobile Web, vanilla JS desktop Web, Chrome extension popup JS, OpenClaw adapter/CLI skills.

---

## Preconditions

- Work in a clean feature branch or worktree.
- Do not include local files such as `config.toml.bak`, `.planning/`, or generated runtime data.
- Use `uv run pytest ...`; direct `pytest` may not exist in this repo shell.
- Keep commits scoped per task.

Reference design: `docs/plans/2026-05-24-avoidance-probes-design.md`.

---

### Task 1: Runtime State And API Models

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py:319-377`
- Modify: `src/openbiliclaw/api/models.py:296-413`
- Test: `tests/test_memory_manager.py`

**Step 1: Write failing runtime-state tests**

Add coverage next to existing discovery runtime state tests:

```python
def test_discovery_runtime_state_round_trips_avoidance_probe_history(tmp_path: Path) -> None:
    memory = MemoryManager(data_dir=tmp_path)
    memory.save_discovery_runtime_state(
        {
            "probed_avoidance_domains": {"浅层热点复读": "2026-05-24T10:00:00"},
            "probed_avoidance_axes": {"knowledge|light": "2026-05-24T10:00:00"},
            "avoidance_probe_feedback_history": [
                {
                    "domain": "浅层热点复读",
                    "response": "confirm",
                    "source_mode": "negative_signal",
                    "specifics": ["标题党热点解读"],
                    "created_at": "2026-05-24T10:01:00",
                }
            ],
            "last_probe_kind": "avoidance",
        }
    )

    state = memory.load_discovery_runtime_state()

    assert state["probed_avoidance_domains"] == {"浅层热点复读": "2026-05-24T10:00:00"}
    assert state["probed_avoidance_axes"] == {"knowledge|light": "2026-05-24T10:00:00"}
    assert state["avoidance_probe_feedback_history"][0]["domain"] == "浅层热点复读"
    assert state["last_probe_kind"] == "avoidance"
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_memory_manager.py -k avoidance_probe_history -v
```

Expected: FAIL because new runtime-state keys are not persisted.

**Step 3: Implement runtime-state persistence**

In `MemoryManager.load_discovery_runtime_state()`, add defaults:

```python
"probed_avoidance_domains": {},
"probed_avoidance_axes": {},
"avoidance_probe_feedback_history": [],
"last_probe_kind": "",
```

In returned payload, preserve those fields and cap history to 100 dict records using `_as_dict_list()`.

In `save_discovery_runtime_state()`, write the same fields.

**Step 4: Add API response models**

In `src/openbiliclaw/api/models.py`, add:

```python
class SpeculativeAvoidanceOut(BaseModel):
    domain: str = ""
    category: str = ""
    reason: str = ""
    source_mode: str = ""
    confidence: float = 0.0
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    specifics: list[SpeculativeSpecificOut] = Field(default_factory=list)
```

Add to `ProfileSummaryResponse`:

```python
speculative_avoidances: list[SpeculativeAvoidanceOut] = Field(default_factory=list)
```

**Step 5: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_memory_manager.py -k "discovery_runtime_state or avoidance_probe_history" -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/memory/manager.py src/openbiliclaw/api/models.py tests/test_memory_manager.py
git commit -m "feat: add avoidance probe runtime state"
```

---

### Task 2: Avoidance Speculator Core Lifecycle

**Files:**
- Create: `src/openbiliclaw/soul/avoidance_speculator.py`
- Create: `tests/test_avoidance_speculator.py`
- Reuse: `src/openbiliclaw/soul/speculator.py`

**Step 1: Write failing lifecycle tests**

Create tests for load/save, confirm, reject, active cap, and novelty:

```python
def test_avoidance_confirm_marks_ready_for_promotion(tmp_path: Path) -> None:
    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="浅层热点复读",
                category="内容风格",
                reason="用户最近对快餐式热点内容有负反馈。",
                source_mode="negative_signal",
                created_at=datetime.now().isoformat(),
                confirmation_threshold=3,
            )
        ]
    )
    save_avoidance_state(tmp_path, state)
    speculator = AvoidanceSpeculator(llm_service=None, data_dir=tmp_path)

    ok = speculator.user_confirm_avoidance("浅层热点复读")

    loaded = load_avoidance_state(tmp_path)
    assert ok is True
    assert loaded.active[0].status == "confirmed"
    assert loaded.active[0].confirmation_count == 3
```

```python
def test_avoidance_reject_moves_to_cooldown(tmp_path: Path) -> None:
    # active item rejected by user should leave active and enter cooldown
```

```python
def test_avoidance_ingest_seeds_respects_active_cap(tmp_path: Path) -> None:
    # max_active=5 should add at most 5 active avoidances
```

```python
def test_avoidance_novelty_guard_blocks_existing_dislikes() -> None:
    # profile.interest.dislikes and preferences.disliked_topics are duplicate coverage
```

```python
def test_avoidance_observe_counts_only_explicit_negative_events(tmp_path: Path) -> None:
    # feedback_type=dislike matching the avoidance increments confirmation_count
    # quick_exit / passive inferred_satisfaction=negative does not increment
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_avoidance_speculator.py -v
```

Expected: FAIL because module/classes do not exist.

**Step 3: Implement minimal lifecycle**

Create `avoidance_speculator.py` with:

- `SpeculativeAvoidanceSpecific`
- `SpeculativeAvoidance`
- `AvoidanceCooldownEntry`
- `AvoidanceState`
- `AvoidanceTickResult`
- `load_avoidance_state(data_dir)`
- `save_avoidance_state(data_dir, state)`
- `promote_ready_avoidances(state)`
- `expire_stale_avoidances(state, now, cooldown_days)`
- `AvoidanceSpeculator`

Reuse or import these pure helpers from `speculator.py` where appropriate:

- `_normalize_probe_term`
- `_has_probe_term_overlap`
- `_chinese_bigrams`
- `build_probe_axis`
- `_normalize_experience_mode`
- `_normalize_entry_load`

Keep file-local history helpers:

```python
AVOIDANCE_FEEDBACK_HISTORY_LIMIT = 100
DENYING_AVOIDANCE_RESPONSES = {"reject", "chat_negative"}
CONFIRMING_AVOIDANCE_RESPONSES = {"confirm", "chat_positive"}
```

For avoidance probes, name responses by what they do to the avoidance hypothesis.
`DENYING_AVOIDANCE_RESPONSES` suppress future duplicates; `CONFIRMING_AVOIDANCE_RESPONSES`
move the candidate toward writeback.

Add `AvoidanceSpeculator.observe(events)` with explicit-negative-only semantics:

- Count only events with `metadata.feedback_type == "dislike"`, `metadata.reaction == "thumbs_down"`, `event_type == "dislike"`, or probe chat records explicitly marked negative toward the content.
- Do not count passive `quick_exit` or generic `inferred_satisfaction=negative`; those are too noisy for an avoidance confirmation.
- This is intentionally stricter than the preference-layer dislike extraction prompt, which can use weaker negative signals. Avoidance auto-confirmation writes durable filtering preferences, so it requires high-confidence evidence.
- When an explicit negative event text matches an active avoidance `domain` or `specifics`, increment `confirmation_count` and store a short event title.
- User `confirm` still force-sets the threshold and marks `status="confirmed"`; threshold is for repeated explicit negative evidence and state-machine compatibility, not required after a direct confirmation click.

**Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_avoidance_speculator.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/avoidance_speculator.py tests/test_avoidance_speculator.py
git commit -m "feat: add avoidance speculator lifecycle"
```

---

### Task 3: Avoidance Generation Prompt And Selector

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py:1547-1731`
- Modify: `src/openbiliclaw/soul/avoidance_speculator.py`
- Test: `tests/test_llm_prompts.py`
- Test: `tests/test_avoidance_speculator.py`

**Step 1: Write failing prompt tests**

Add tests:

```python
def test_avoidance_generation_prompt_requires_source_modes() -> None:
    messages = build_avoidance_generation_prompt(
        profile_summary="## 兴趣\n- AI",
        existing_avoidances=[],
        cooldown_domains=[],
        confirmed_dislikes=["低质标题党"],
        confirmed_likes=["AI"],
        count=5,
    )
    text = "\n".join(message["content"] for message in messages)

    assert "negative_signal" in text
    assert "positive_boundary" in text
    assert "style_boundary" in text
    assert "不能直接把正向兴趣本身当成讨厌对象" in text
```

Also update `tests/test_llm_prompts.py::_builder_test_inputs()` so
`test_prompt_builder_system_messages_are_call_invariant` covers
`build_avoidance_generation_prompt()`.

**Step 2: Write failing selector tests**

In `tests/test_avoidance_speculator.py`:

```python
def test_choose_next_avoidance_probe_skips_denied_feedback_domain() -> None:
    chosen = choose_next_avoidance_candidate(
        [
            SimpleNamespace(domain="浅层热点复读", confirmation_count=0, weight=0.9),
            SimpleNamespace(domain="营销号带货", confirmation_count=0, weight=0.2),
        ],
        feedback_history=[{"domain": "浅层热点复读", "response": "reject"}],
    )

    assert chosen.domain == "营销号带货"
```

```python
def test_avoidance_novelty_guard_blocks_positive_like_domain() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[InterestDomain(domain="AI", weight=0.9)]
        )
    )
    guard = AvoidanceNoveltyGuard.from_profile_and_state(profile, AvoidanceState())

    assert guard.is_duplicate_domain("AI") is True
    assert guard.is_duplicate_domain("AI大模型") is True
```

**Step 3: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_llm_prompts.py tests/test_avoidance_speculator.py -k "avoidance_generation_prompt or choose_next_avoidance" -v
```

Expected: FAIL because prompt and selector are missing.

**Step 4: Implement prompt and selector**

Add `build_avoidance_generation_prompt()` to `prompts.py`.

Prompt-cache requirements:

- Define `_AVOIDANCE_GENERATION_SYSTEM_PROMPT` as a module-level constant.
- Return that constant as the system message without f-strings or per-call concatenation.
- Put all variables (`profile_summary`, `existing_avoidances`, `cooldown_domains`, `confirmed_dislikes`, `confirmed_likes`, `count`) in the user message.
- Use `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)` for structured user payloads.
- Add the builder to `_builder_test_inputs()` so the cache-invariant test protects it.

Schema:

```json
{
  "avoidances": [
    {
      "domain": "浅层热点复读",
      "category": "内容风格",
      "source_mode": "negative_signal|positive_boundary|style_boundary",
      "reason": "为什么怀疑用户想避开这个方向",
      "experience_mode": "knowledge|aesthetic|hands_on|people_story|wander_observe",
      "entry_load": "light|heavy",
      "confidence": 0.45,
      "specifics": ["标题党热点解读", "无信息增量复读"]
    }
  ]
}
```

In `avoidance_speculator.py`, implement:

- `_parse_avoidance_response()`
- `AvoidanceNoveltyGuard`
- `_select_diverse_avoidances()`
- `choose_next_avoidance_candidate()`

`AvoidanceNoveltyGuard.from_profile_and_state()` must include:

- Existing `disliked_topics` and `interest.dislikes` as duplicate coverage.
- Active/cooldown avoidances.
- Recently probed avoidance domains.
- Denied avoidance feedback (`reject` / `chat_negative`).
- High-weight positive `interest.likes` domains and specifics as blocked terms. A candidate cannot directly target something the user already likes; only lower-quality boundary forms should pass.

Selection rules:

- Prefer lowest `confirmation_count`.
- Skip recently probed domains.
- Skip domains denied by `reject` / `chat_negative`.
- Prefer fresh axis among same-pressure candidates.
- Tie-break by weight/confidence.

**Step 5: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_llm_prompts.py tests/test_avoidance_speculator.py -k "avoidance or choose_next_avoidance" -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py src/openbiliclaw/soul/avoidance_speculator.py tests/test_llm_prompts.py tests/test_avoidance_speculator.py
git commit -m "feat: generate avoidance probes"
```

---

### Task 3.5: Extract Shared Dislike Helpers

**Files:**
- Add: `src/openbiliclaw/soul/dislike_writeback.py`
- Modify: `src/openbiliclaw/soul/layer_updaters.py:184-275`
- Test: `tests/test_pipeline_advanced.py` or existing layer-updater tests

This is a pure refactor before avoidance runtime wiring. Do it before Task 4 so
there is no commit where an avoidance promote can update `avoidance_state.json`
without writing `disliked_topics` or purging the candidate pool.

**Step 1: Write failing helper tests**

Add tests for:

```python
async def test_apply_new_dislikes_persists_preference_and_soul_profile(...) -> None:
    # add one new disliked topic
    # assert preference_layer.data["disliked_topics"] includes it
    # assert soul layer and profile files include it
```

```python
async def test_purge_pool_for_new_dislikes_invokes_existing_pool_purge_paths(...) -> None:
    # fake database exposes purge_pool_by_disliked_topics
    # fake embedding/LLM services are optional
    # assert fast purge runs and returned change messages include purge count
```

```python
async def test_layer_updater_uses_purge_helper_without_rewriting_preference(...) -> None:
    # analyzer path already wrote updated_preference and populated the in-memory profile
    # assert it calls purge_pool_for_new_dislikes(newly_added, all_dislikes)
    # assert it does not call apply_new_dislikes(), avoiding duplicate flat->onion writes
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_pipeline_advanced.py -k "dislike_writeback or purge_pool_for_new_dislikes" -v
```

Expected: FAIL because helpers do not exist.

**Step 3: Extract helpers with a clear seam**

Create `src/openbiliclaw/soul/dislike_writeback.py` with:

```python
def merge_new_dislikes_into_preference(
    *,
    memory: MemoryManager,
    topics: Sequence[str],
) -> tuple[list[str], list[str]]:
    ...

async def purge_pool_for_new_dislikes(
    *,
    database: object | None,
    embedding_service: object | None,
    llm_service: object | None,
    newly_added: Sequence[str],
    all_dislikes: Sequence[str],
) -> list[str]:
    ...

async def apply_new_dislikes(
    *,
    memory: MemoryManager,
    database: object | None,
    embedding_service: object | None,
    llm_service: object | None,
    topics: Sequence[str],
) -> list[str]:
    ...

def topics_for_confirmed_avoidance(avoidance: SpeculativeAvoidance) -> list[str]:
    ...
```

Responsibilities:

- `merge_new_dislikes_into_preference()` performs stable, idempotent set-add into
  `memory.get_layer("preference").data["disliked_topics"]`, saves the preference
  layer, and returns `(actually_added, all_dislikes)`.
- `purge_pool_for_new_dislikes()` owns the existing pool purge sequence that is
  currently inline in `soul/layer_updaters.py`: fast
  `database.purge_pool_by_disliked_topics`, embedding recall + LLM judge when
  available, and embedding-only fallback when LLM is unavailable.
- `apply_new_dislikes()` is for avoidance confirm / auto-promote paths. It calls
  `merge_new_dislikes_into_preference()`, loads the persisted `soul` layer as
  `OnionProfile`, calls `populate_from_flat_preference(preference_layer.data)`,
  updates the soul layer data, saves it, then calls
  `memory.sync_profile_files(profile)`. Leave a code comment near this sequence:
  first persist the layer, then sync derived files. Finally it calls
  `purge_pool_for_new_dislikes()` for `actually_added`.
- `apply_new_dislikes()` should no-op the purge when `actually_added` is empty.
- `topics_for_confirmed_avoidance()` returns valid specific names first and only
  falls back to the avoidance domain when the specifics list is empty. Keep it in
  this module so pipeline auto-promote and API confirm import the same helper.

Then modify `soul/layer_updaters.py`:

- Keep the existing analyzer path as owner of the full `updated_preference`
  write and in-memory `profile.populate_from_flat_preference(updated_preference)`.
- For `newly_added_dislikes`, call only `purge_pool_for_new_dislikes(...)`.
  Do not call `apply_new_dislikes(...)` from the analyzer path, because the flat
  preference and onion profile have already been updated there.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_pipeline_advanced.py -k "dislike_writeback or purge_pool_for_new_dislikes" -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/dislike_writeback.py src/openbiliclaw/soul/layer_updaters.py tests/test_pipeline_advanced.py
git commit -m "refactor: share dislike purge helpers"
```

---

### Task 4: Wire SoulEngine, Pipeline, Config, And Profile Summary

**Files:**
- Modify: `src/openbiliclaw/config.py:175-205`
- Modify: `config.example.toml:296-303`
- Modify: `src/openbiliclaw/soul/engine.py:76-152`
- Modify: `src/openbiliclaw/soul/pipeline.py:552-686`
- Modify: `src/openbiliclaw/api/runtime_context.py:343-373`
- Modify: `src/openbiliclaw/cli.py:400-420`
- Modify: `src/openbiliclaw/api/app.py:1395-1598`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:55-75`
- Modify: `src/openbiliclaw/soul/dislike_writeback.py`
- Test: `tests/test_config.py`
- Test: `tests/test_soul_engine.py`
- Test: `tests/test_pipeline_advanced.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing config tests**

In `tests/test_config.py`, add save/load assertions for:

- `avoidance_speculation_interval_minutes`
- `avoidance_speculation_ttl_days`
- `avoidance_speculation_cooldown_days`
- `avoidance_speculation_confirmation_threshold`
- `avoidance_speculation_max_active`

Run:

```bash
uv run pytest tests/test_config.py -k avoidance_speculation -v
```

Expected: FAIL because fields do not exist or are not serialized.

**Step 2: Implement config fields**

Add fields to `SchedulerConfig` and config serialization.

Keep the avoidance fields as an explicit scheduler block adjacent to the existing `speculation_*` fields; do not hide them inside unrelated refresh tuning fields.

Add sample config comments:

```toml
# --- 不喜欢领域探针（Avoidance Probe）调度 ---
# avoidance_speculation_interval_minutes = 10
# avoidance_speculation_ttl_days = 3
# avoidance_speculation_cooldown_days = 7
# avoidance_speculation_confirmation_threshold = 3
# avoidance_speculation_max_active = 5
```

**Step 3: Write failing profile summary test**

In `tests/test_api_app.py`, add:

```python
def test_profile_summary_includes_speculative_avoidances(...) -> None:
    # prepare avoidance_state.json with one active SpeculativeAvoidance
    response = client.get("/api/profile-summary")
    assert response.json()["speculative_avoidances"][0]["domain"] == "浅层热点复读"
```

Expected: FAIL because profile summary does not load avoidance state.

In `tests/test_pipeline_advanced.py`, add:

```python
async def test_pipeline_auto_promoted_avoidance_uses_apply_new_dislikes(...) -> None:
    # stub avoidance_speculator.tick() to return one promoted avoidance
    # monkeypatch soul.pipeline.apply_new_dislikes or imported helper
    # assert the helper receives the promoted topics and the fake pool purge path is reachable
```

**Step 4: Wire engine and profile summary**

In `SoulEngine.__init__`, instantiate:

```python
self._avoidance_speculator = AvoidanceSpeculator(
    llm_service=self._llm_service,
    data_dir=data_dir,
    generation_interval_minutes=avoidance_speculation_interval_minutes,
    default_ttl_days=avoidance_speculation_ttl_days,
    cooldown_days=avoidance_speculation_cooldown_days,
    confirmation_threshold=avoidance_speculation_confirmation_threshold,
    max_active=avoidance_speculation_max_active,
)
```

Pass to `ProfileUpdatePipeline`.

In `SoulEngine.get_profile()`, do not attach active avoidances to the profile object. Unconfirmed avoidance probes must not reach discovery, curator, delight, or recommendation contexts.

In `ProfileUpdatePipeline`, accept an `avoidance_speculator` constructor
argument and store it separately from the existing positive `_speculator`.

Call `avoidance_speculator.observe(raw_events)` with the explicit-negative-only
behavior from Task 2. On the same idle/layer-update cadence as the positive
speculator, call a new sibling method `_run_avoidance_speculator_tick(result)`.
Do not make `_run_speculator_tick()` polarity-aware; keep the existing positive
path focused on `profile.interest.likes`.

`_run_avoidance_speculator_tick(result)` should:

- Load the current profile.
- Pass `avoidance_probe_feedback_history` from runtime state into
  `avoidance_speculator.tick(...)`.
- Inspect `avoidance_tick_result.promoted`.
- Convert each promoted avoidance into writeback topics using
  `topics_for_confirmed_avoidance(avoidance)` from
  `soul.dislike_writeback`, so pipeline auto-promote and API confirm cannot
  diverge.
- Call `apply_new_dislikes(...)` from Task 3.5 with
  `database=getattr(self._memory, "_database", None)`,
  `embedding_service=self._embedding_service`, and
  `llm_service=getattr(self._preference_analyzer, "registry", None)`.
- Append a `LayerUpdateResult` and changelog entry when any topics were added.

The speculator must remain IO-free: `promote_ready_avoidances(state)` only moves
state and returns promoted items. Task 4's commit must include this writeback
hook; do not leave an intermediate commit where observe/tick can promote an
avoidance without updating `disliked_topics`.

In `/api/profile-summary`, load `avoidance_state.json` and return `speculative_avoidances`.

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_api_app.py tests/test_pipeline_advanced.py -k "avoidance_speculation or speculative_avoidances or auto_promoted_avoidance" -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/config.py config.example.toml src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/api/app.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/soul/dislike_writeback.py tests/test_config.py tests/test_soul_engine.py tests/test_pipeline_advanced.py tests/test_api_app.py
git commit -m "feat: wire avoidance probes into soul runtime"
```

---

### Task 5: Avoidance Probe API

**Files:**
- Modify: `src/openbiliclaw/api/app.py:2232-2809`
- Test: `tests/test_api_app.py`

**Step 1: Write failing API tests**

Add cases:

```python
def test_avoidance_probe_pending_returns_active_items(...) -> None:
    response = client.get("/api/avoidance-probes/pending")
    assert response.status_code == 200
    assert response.json()["items"][0]["domain"] == "浅层热点复读"
```

```python
def test_avoidance_probe_confirm_adds_disliked_topic(...) -> None:
    response = client.post(
        "/api/avoidance-probes/respond",
        json={"domain": "浅层热点复读", "response": "confirm"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "confirmed"
    assert "浅层热点复读" in memory.get_layer("preference").data["disliked_topics"]

    profile_response = client.get("/api/profile-summary")
    assert any(
        item["domain"] == "浅层热点复读"
        for item in profile_response.json()["dislikes"]
    )
```

```python
def test_avoidance_probe_reject_does_not_add_disliked_topic(...) -> None:
    response = client.post(
        "/api/avoidance-probes/respond",
        json={"domain": "浅层热点复读", "response": "reject"},
    )
    assert response.status_code == 200
    assert "浅层热点复读" not in memory.get_layer("preference").data.get("disliked_topics", [])
```

```python
def test_avoidance_probe_confirm_records_feedback_history(...) -> None:
    assert memory.runtime_state["avoidance_probe_feedback_history"][0]["response"] == "confirm"
```

```python
def test_avoidance_probe_confirm_uses_apply_new_dislikes(...) -> None:
    # monkeypatch API module's apply_new_dislikes import
    # assert confirm calls the shared helper instead of mutating preference inline
    # assert embedding_service comes from ctx.soul_engine._embedding_service
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_api_app.py -k "avoidance_probe" -v
```

Expected: FAIL because endpoints do not exist.

**Step 3: Implement endpoints**

Generalize the existing probe helpers instead of copying a full parallel set.
Keep small avoidance wrappers only where the data source differs.

Suggested helper shape:

- `_probe_metadata_from_active_item(get_active, domain, *, include_source_mode=False)`
- `_record_probe_feedback_history(state_key, append_fn, domain, response, *, get_active, message="")`
- `_record_probe_cognition(summary, domain, action, *, source="interest_probe", detail="")`
- `_publish_probe_event(event_type, message, domain)`
- `_apply_confirmed_avoidance(active_avoidance)` as a thin API wrapper around
  `soul.dislike_writeback.apply_new_dislikes(...)`

Add endpoints:

- `POST /api/avoidance-probes/trigger`
- `GET /api/avoidance-probes/pending`
- `POST /api/avoidance-probes/respond`

For confirmed avoidance, call the shared helper from Task 3.5. The API endpoint
should not own preference mutation or pool purge logic.

```python
changes = await apply_new_dislikes(
    memory=ctx.memory_manager,
    database=ctx.database or getattr(ctx.memory_manager, "_database", None),
    embedding_service=getattr(ctx.soul_engine, "_embedding_service", None),
    llm_service=ctx.llm_service,
    topics=topics_for_confirmed_avoidance(active_avoidance),
)
```

This helper writes the flat preference source of truth, syncs the persisted soul
layer, and runs the pool purge sequence. Do not only mutate the object returned
by `await ctx.soul_engine.get_profile()`; that object is a snapshot and direct
append can be lost on the next rebuild.

`topics_for_confirmed_avoidance()` should be conservative: if the confirmed
avoidance has valid specifics, write those specifics; write the domain only when
the specifics list is empty.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_api_app.py -k "avoidance_probe" -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: add avoidance probe API"
```

---

### Task 6: Runtime Push Loop

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py:935-982`
- Modify: `src/openbiliclaw/runtime/refresh.py:1436-1524`
- Test: `tests/test_refresh_runtime.py`

**Step 1: Write failing runtime tests**

Add tests mirroring interest probe push tests:

```python
async def test_publish_avoidance_probe_skips_recent_axis_repeat() -> None:
    # recent probed_avoidance_axes has knowledge|light
    # two active avoidances available
    # expect fresh axis selected
```

```python
async def test_publish_avoidance_probe_does_not_record_without_stream_subscriber() -> None:
    # RuntimeEventHub with no subscribers
    # expect probed_avoidance_domains and axes unchanged
```

```python
async def test_proactive_probe_push_publishes_only_one_probe_per_tick() -> None:
    # both interest and avoidance candidates are available
    # one call to the proactive probe arbiter should publish exactly one probe event
    # last_probe_kind should update so the next call prefers the other kind
```

```python
async def test_proactive_probe_push_does_not_record_kind_when_publish_fails() -> None:
    # selected interest probe has no stream subscribers, so _publish_event returns False
    # last_probe_kind must remain unchanged and history/cooldown must not be recorded
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_refresh_runtime.py -k "avoidance_probe" -v
```

Expected: FAIL because controller has no avoidance publisher.

**Step 3: Implement publisher**

Refactor publishing into a single selection/publish flow:

- `_select_probe_payload(kind)` returns `None` or a tuple containing the event
  type, event payload, and an `on_delivered()` callback.
- For `kind == "interest"`, move the existing candidate-selection/history logic
  from `_publish_interest_probe_if_available()` into this selector.
- For `kind == "avoidance"`, select from
  `self.soul_engine._avoidance_speculator.get_active_avoidances()`, purge stale
  `probed_avoidance_domains` / `probed_avoidance_axes`, use
  `choose_next_avoidance_candidate()`, and return an `avoidance.probe` payload.
- The `on_delivered()` callback records only the history/cooldown for the probe
  that was actually delivered.

Do not call both positive and negative publishers unconditionally in one loop.
Instead add an arbiter `_publish_probe_if_available()` that selects at most one
payload, publishes it, and invokes the callback only after `_publish_event()`
returns truthy.

```python
async def _publish_probe_if_available(self) -> None:
    state = self.memory_manager.load_discovery_runtime_state()
    last_kind = str(state.get("last_probe_kind", ""))
    order = ("avoidance", "interest") if last_kind == "interest" else ("interest", "avoidance")
    for kind in order:
        selected = self._select_probe_payload(kind)
        if selected is None:
            continue
        event_type, payload, on_delivered = selected
        delivered = await self._publish_event(event_type, payload)
        if delivered:
            on_delivered()
            state = self.memory_manager.load_discovery_runtime_state()
            state["last_probe_kind"] = kind
            self.memory_manager.save_discovery_runtime_state(state)
            return
```

If publish fails because no stream subscriber is available, do not record
`last_probe_kind`, probed-domain cooldown, or feedback history. The next loop
must be free to try the same kind again.

Call only the arbiter from `_loop_proactive_push()`.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_refresh_runtime.py -k "avoidance_probe or interest_probe" -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "feat: push avoidance probes at runtime"
```

---

### Task 7: OpenClaw Adapter, CLI, And Skills

**Files:**
- Modify: `src/openbiliclaw/integrations/openclaw/schemas.py:138-162`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py:296-411`
- Modify: `src/openbiliclaw/integrations/openclaw/cli.py:28-178`
- Modify: `src/openbiliclaw/integrations/openclaw/skill.py:111-194`
- Test: `tests/test_openclaw_adapter.py`
- Test: `tests/test_openclaw_cli.py`
- Test: `tests/test_openclaw_skill.py`

**Step 1: Write failing adapter tests**

Add:

```python
async def test_get_next_avoidance_probe_returns_top_candidate() -> None:
    result = await adapter.get_next_avoidance_probe()
    assert result.probe is not None
    assert result.probe.domain == "浅层热点复读"
    assert "避开" in result.probe.question or "不喜欢" in result.probe.question
```

```python
async def test_get_next_avoidance_probe_records_history_and_avoids_repeat() -> None:
    first = await adapter.get_next_avoidance_probe()
    second = await adapter.get_next_avoidance_probe()
    assert first.probe.domain != second.probe.domain
    assert first.probe.domain in memory_manager.runtime_state["probed_avoidance_domains"]
```

```python
async def test_respond_avoidance_probe_confirm_delegates_to_speculator() -> None:
    result = await adapter.respond_avoidance_probe(AvoidanceProbeFeedbackRequest(...))
    assert result.ok is True
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_openclaw_adapter.py -k "avoidance_probe" -v
```

Expected: FAIL because schemas and adapter methods do not exist.

**Step 3: Implement schemas and operations**

Add dataclasses:

- `AvoidanceProbeItem`
- `AvoidanceProbeResponse`
- `AvoidanceProbeFeedbackRequest`
- `AvoidanceProbeFeedbackResponse`

Add `OpenClawAdapter.get_next_avoidance_probe()` and `respond_avoidance_probe()`.

Question template:

```python
return (
    f"我猜【{domain}】{specific_hint}可能是你想避开的方向"
    f"——{reason} 这个判断准吗？"
)
```

**Step 4: Add CLI commands**

Parser:

- `next-avoidance-probe`
- `respond-avoidance-probe --domain ... --response confirm|reject|chat --message ...`

Update `_LISTEN_EVENT_TYPES`:

```python
frozenset({"delight.candidate", "interest.probe", "avoidance.probe"})
```

**Step 5: Add skill descriptors**

Add:

- `openbiliclaw_next_avoidance_probe`
- `openbiliclaw_respond_avoidance_probe`

**Step 6: Run OpenClaw tests**

Run:

```bash
uv run pytest tests/test_openclaw_adapter.py tests/test_openclaw_cli.py tests/test_openclaw_skill.py -k "avoidance_probe or listen_event_types or doctor" -v
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/integrations/openclaw/schemas.py src/openbiliclaw/integrations/openclaw/operations.py src/openbiliclaw/integrations/openclaw/cli.py src/openbiliclaw/integrations/openclaw/skill.py tests/test_openclaw_adapter.py tests/test_openclaw_cli.py tests/test_openclaw_skill.py
git commit -m "feat: expose avoidance probes to OpenClaw"
```

---

### Task 8: Mobile Web Support

**Files:**
- Modify: `src/openbiliclaw/web/js/api.js:194-204`
- Modify: `src/openbiliclaw/web/js/view-models.js`
- Modify: `src/openbiliclaw/web/js/views/chat.js:313-355`
- Modify: `src/openbiliclaw/web/js/views/profile.js:144-290`
- Modify: `src/openbiliclaw/web/js/stream.js` if event routing is centralized there
- Test: `tests/test_mobile_web_view_models.py`

**Step 1: Write failing view-model tests**

Before editing, inspect the existing mobile web test conventions:

```bash
rg -n "mobile web|view-models|profile" tests/test_mobile_web_view_models.py tests/test_mobile_web_delight_layout.py
```

Use the existing helper style rather than creating a disconnected test harness.

Add:

```python
def test_mobile_profile_normalizes_speculative_avoidances():
    summary = {
        "initialized": True,
        "speculative_avoidances": [
            {
                "domain": "浅层热点复读",
                "reason": "用户最近避开这类内容。",
                "source_mode": "negative_signal",
                "specifics": [{"name": "标题党热点解读", "confirmation_count": 0}],
            }
        ],
    }

    normalized = normalize_profile_summary(summary)

    assert normalized["speculative_avoidances"][0]["domain"] == "浅层热点复读"
```

Use existing JS-test pattern in this repo. If the current mobile tests read JS through helper snapshots rather than executing JS directly, follow that pattern.

**Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_mobile_web_view_models.py -k avoidance -v
```

Expected: FAIL because normalization/rendering lacks `speculative_avoidances`.

**Step 3: Implement mobile API helpers**

Add:

```js
export async function fetchPendingAvoidanceProbes() {
  const data = await requestJson("/avoidance-probes/pending");
  return Array.isArray(data?.items) ? data.items : [];
}

export async function respondToAvoidanceProbe(domain, responseType, message = "") {
  return requestJson("/avoidance-probes/respond", {
    ...json({ domain, response: responseType, message }),
    timeoutMs: 35_000,
  });
}
```

**Step 4: Render mobile messages**

In `chat.js`, detect:

```js
const isAvoidance = (n.type || "") === "avoidance.probe";
```

Use labels:

- Title type: `避雷确认`
- `confirm`: `确实不喜欢`
- `reject`: `不是`
- `chat`: `多聊聊`

For chat action:

```js
startContextualChat({ scope: "avoidance_probe", subjectId: domain, subjectTitle: domain });
```

**Step 5: Render mobile profile**

In `profile.js`, add a section after `speculative_interests`:

```js
if (p.speculative_avoidances.length) {
  html += section("待确认避雷方向", renderSpecAvoidances(p.speculative_avoidances));
}
```

**Step 6: Run tests**

Run:

```bash
uv run pytest tests/test_mobile_web_view_models.py tests/test_mobile_web_delight_layout.py -k "avoidance or mobile" -v
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/web/js/api.js src/openbiliclaw/web/js/view-models.js src/openbiliclaw/web/js/views/chat.js src/openbiliclaw/web/js/views/profile.js tests/test_mobile_web_view_models.py
git commit -m "feat(web): show avoidance probes on mobile"
```

---

### Task 9: PC Web Support

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:20-1200`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css` only if existing styles cannot cover labels
- Test: add or extend existing desktop web test if available; otherwise use targeted static regression in `tests/test_mobile_web_view_models.py` only for shared contracts

**Step 1: Write failing static regression test**

If no desktop JS execution harness exists, add a small source-contract test:

```python
def test_desktop_web_knows_avoidance_probe_endpoint():
    source = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()
    assert "avoidanceProbeRespond" in source
    assert "avoidance.probe" in source
    assert "确实不喜欢" in source
```

Place it in the nearest existing web test module.

**Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests -k "desktop_web_knows_avoidance" -v
```

Expected: FAIL.

**Step 3: Implement desktop support**

Add route constants:

```js
avoidanceProbeRespond: "/avoidance-probes/respond",
avoidanceProbePending: "/avoidance-probes/pending",
```

Update message normalization:

- `messageType()` preserves `avoidance.probe`.
- `messageKey()` keys avoidance separately from interest by `type + domain`.
- `hydrateInboxFromSpeculations()` becomes two functions or one generalized helper:
  - interest from `profile.speculative_interests`
  - avoidance from `profile.speculative_avoidances`

Update card rendering:

- `interest.probe`: existing text and buttons.
- `avoidance.probe`: `避雷确认`, `确实不喜欢`, `不是`, `多聊聊`.

Update `respondProbe()` to dispatch endpoint based on `messageType(msg)`.

Update runtime stream:

```js
if (event.type === "avoidance.probe" && event.domain) {
  mergeMessages([{ type: "avoidance.probe", ...event }]);
}
```

**Step 4: Run test**

Run:

```bash
uv run pytest tests -k "desktop_web_knows_avoidance" -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/assets/css/app.css tests
git commit -m "feat(web): show avoidance probes on desktop"
```

---

### Task 10: Plugin Popup Support

**Files:**
- Modify: `extension/popup/popup-api.js:324-337`
- Modify: `extension/popup/popup-helpers.js:362-405`
- Modify: `extension/popup/popup.js:660-690`
- Modify: `extension/popup/popup.js:1000-1180`
- Modify: `extension/popup/popup.js:1336-1420`
- Modify: `extension/popup/popup.js:2450`
- Modify: `extension/popup/popup.js:2676-2785`
- Modify: `extension/popup/popup.js:3749-3785`
- Modify: `extension/src/background/service-worker.ts:217-223`
- Test: extension tests if available for popup helpers; otherwise add source-contract tests under `tests/` or extension test suite

**Step 1: Write failing popup helper test**

If extension JS tests are configured, add:

```js
test("normalizeProfileSummary keeps speculative avoidances", () => {
  const summary = normalizeProfileSummary({
    initialized: true,
    speculative_avoidances: [{ domain: "浅层热点复读", source_mode: "negative_signal" }],
  });

  expect(summary.speculative_avoidances[0].domain).toBe("浅层热点复读");
});
```

If not, add a Python source-contract test:

```python
def test_plugin_popup_knows_avoidance_probe_contract():
    source = Path("extension/popup/popup.js").read_text()
    assert "avoidance.probe" in source
    assert "确实不喜欢" in source
```

**Step 2: Run test to verify failure**

Run the relevant command. If using Python source-contract:

```bash
uv run pytest tests -k "plugin_popup_knows_avoidance" -v
```

Expected: FAIL.

**Step 3: Implement popup API**

Add:

```js
export async function respondToAvoidanceProbe(domain, responseType, message = "") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  try {
    return await requestJson("/avoidance-probes/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, response: responseType, message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}
```

**Step 4: Implement popup UI**

Update runtime event handling:

```js
if (event.type === "avoidance.probe" && event.domain) {
  state.pendingAvoidanceProbe = event;
  state.messages.push({ ...event, type: "avoidance.probe" });
}
```

Generalize message card rendering:

- `interest.probe`: existing labels.
- `avoidance.probe`: `避雷确认`, `确实不喜欢`, `不是`, `多聊聊`.

Chat scope:

```js
const scope = type === "avoidance.probe" ? "avoidance_probe" : "probe";
```

Hydration:

- Continue hydrating interest messages from `summary.speculative_interests`.
- Add hydrating avoidance messages from `summary.speculative_avoidances`.
- Drop stale avoidance messages no longer active.

**Step 5: Update service worker**

Ensure service worker ignores OS-level toast for both:

```ts
if (eventType === "interest.probe" || eventType === "avoidance.probe") {
  return;
}
```

**Step 6: Run tests**

Run:

```bash
uv run pytest tests -k "plugin_popup_knows_avoidance" -v
```

If extension test scripts exist, also run the targeted extension test command used by the repo.

Expected: PASS.

**Step 7: Commit**

```bash
git add extension/popup/popup-api.js extension/popup/popup-helpers.js extension/popup/popup.js extension/src/background/service-worker.ts tests
git commit -m "feat(extension): show avoidance probes"
```

---

### Task 11: Durable Chat Scope Support

**Files:**
- Modify: `src/openbiliclaw/api/app.py:800-815`
- Modify: `src/openbiliclaw/api/app.py:2440-2525`
- Modify: `extension/popup/popup.js:1694-1735`
- Modify: `extension/popup/popup.js:2760-2785`
- Modify: `src/openbiliclaw/web/js/views/chat.js:505-540`
- Test: `tests/test_api_app.py`

**Step 1: Write failing chat scope test**

Add:

```python
def test_chat_turn_accepts_avoidance_probe_scope(client) -> None:
    response = client.post(
        "/api/chat/turns",
        json={
            "session": "popup",
            "scope": "avoidance_probe",
            "subject_id": "浅层热点复读",
            "message": "对，这类我不喜欢",
        },
    )

    assert response.status_code == 200
```

**Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/test_api_app.py -k "avoidance_probe_scope" -v
```

Expected: FAIL because scope validation only allows `chat|delight|probe`.

**Step 3: Implement scope support**

Allow `avoidance_probe` wherever chat turn scope is normalized.

In durable chat completion:

- For `scope == "avoidance_probe"`, inject avoidance context.
- Use avoidance sentiment classification.
- Publish `avoidance.chat`.
- Record avoidance feedback history.

Keep existing `probe` behavior unchanged.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_api_app.py -k "avoidance_probe_scope or probe_chat" -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py extension/popup/popup.js src/openbiliclaw/web/js/views/chat.js tests/test_api_app.py
git commit -m "feat: support avoidance probe chat scope"
```

---

### Task 12: Documentation And Final Verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/integrations.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Step 1: Update module docs**

Document:

- `AvoidanceSpeculator`
- active cap 5 separate from positive probes
- API endpoints
- runtime events
- runtime state fields
- OpenClaw commands and skills
- config fields
- confirmation semantics
- “unconfirmed avoidance probes do not filter recommendations”

Also update the cross-module architecture diagrams and top-level README diagrams.
This feature adds new API endpoints, runtime events, persistent state, and four
client surfaces, so architecture docs are mandatory rather than conditional.

**Step 2: Update changelog**

Add a current-version bullet near the top:

```markdown
- 新增不喜欢领域探针设计与实现：系统会主动确认可能的避雷方向，确认后写入 `disliked_topics` 并触发候选池清理；未确认前不参与推荐过滤。
```

**Step 3: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_avoidance_speculator.py \
  tests/test_memory_manager.py \
  tests/test_api_app.py \
  tests/test_refresh_runtime.py \
  tests/test_openclaw_adapter.py \
  tests/test_openclaw_cli.py \
  tests/test_openclaw_skill.py \
  tests/test_mobile_web_view_models.py \
  -k "avoidance or probe or discovery_runtime_state" \
  -v
```

Expected: PASS.

**Step 4: Run lint/type checks for touched Python**

Run:

```bash
uv run ruff check src/openbiliclaw tests
uv run mypy src/
```

Expected: PASS.

**Step 5: Run full pytest if time allows**

Run:

```bash
uv run pytest
```

Expected: PASS.

**Step 6: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only intended code, tests, and docs changed.

**Step 7: Commit docs and any final fixes**

```bash
git add docs src tests extension config.example.toml
git commit -m "docs: describe avoidance probes"
```

If docs were committed with the implementation tasks, skip this final docs commit and only report verification.

---

## Execution Notes

- Do not let `avoidance.probe` enter recommendation filtering until the user confirms it.
- Avoidance `confirm` means “confirmed dislike,” not “confirmed interest.”
- Avoidance `reject` means “I do not dislike this,” and should be used as future duplicate-suppression evidence.
- Preserve existing `interest.probe` behavior and tests. Any shared helper changes must run interest-probe tests as regression coverage.
- Keep `scope="probe"` for current positive probes and use only `scope="avoidance_probe"` for negative probes. Do not add a parallel `polarity="negative"` fallback route.
