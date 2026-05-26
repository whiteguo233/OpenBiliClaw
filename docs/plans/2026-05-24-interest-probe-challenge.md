# Interest Probe Challenge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make interest probes an anti-bubble challenge surface by adding explicit probe distance bands, direct confirmation semantics, short-term exploration buffering, and recommendation amplification guards.

**Architecture:** Extend the existing speculative-interest lifecycle instead of creating a second probe system. Store probe distance and confirmation metadata on `SpeculativeInterest`, keep short-term exploration buffer state in `discovery_runtime_state` for v1, and enforce new-interest amplification through `PoolCurator` rolling context plus final recommendation batch caps.

**Tech Stack:** Python, FastAPI, SQLite, pytest, Ruff, existing web JS.

---

### Task 1: Add Probe Distance Schema, Prompt Contract, and Hard Selector Quotas

**Files:**
- Modify: `tests/test_speculator.py`
- Modify: `tests/test_llm_prompts.py`
- Modify: `src/openbiliclaw/soul/speculator.py`
- Modify: `src/openbiliclaw/llm/prompts.py`

**Step 1: Write failing tests**

Add tests that lock the new distance-band contract:

```python
def test_speculative_interest_round_trips_probe_mode_and_confirmation_fields():
    spec = SpeculativeInterest(
        domain="城市基础设施观察",
        category="知识观察",
        probe_mode="bridge",
        confirmation_source="probe_confirmed",
        confirmed_at="2026-05-24T12:00:00",
    )

    restored = SpeculativeInterest.from_dict(spec.to_dict())

    assert restored.probe_mode == "bridge"
    assert restored.challenge is True
    assert restored.confirmation_source == "probe_confirmed"
    assert restored.confirmed_at == "2026-05-24T12:00:00"
```

```python
def test_select_diverse_candidates_enforces_probe_mode_quota_when_possible():
    candidates = [
        SpeculativeInterest(domain="近1", probe_mode="near", confidence=0.9, weight=0.9),
        SpeculativeInterest(domain="近2", probe_mode="near", confidence=0.8, weight=0.8),
        SpeculativeInterest(domain="横向", probe_mode="lateral", confidence=0.6, weight=0.6),
        SpeculativeInterest(domain="桥接", probe_mode="bridge", confidence=0.55, weight=0.55),
    ]

    selected = speculator_module._select_diverse_candidates(candidates, limit=3)

    assert any(item.probe_mode != "near" for item in selected)
    assert sum(1 for item in selected if item.probe_mode == "near") <= 2
```

```python
def test_speculation_prompt_requests_probe_mode_distance_bands():
    messages = build_speculation_generation_prompt(
        profile_summary="likes: 机器人技术",
        existing_speculations=[],
        cooldown_domains=[],
        confirmed_domains=["机器人技术"],
        count=5,
    )

    system = messages[0]["content"]
    # Distance definitions are static and should stay in the system prompt for prompt-cache reuse.
    assert "probe_mode" in system
    for band in ("near", "lateral", "bridge", "wildcard"):
        assert band in system
```

Also add parser/defaulting coverage:

```python
def test_normalize_probe_mode_defaults_missing_or_unknown_to_near():
    assert speculator_module._normalize_probe_mode("") == "near"
    assert speculator_module._normalize_probe_mode(None) == "near"
    assert speculator_module._normalize_probe_mode("surprise") == "near"
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_speculator.py tests/test_llm_prompts.py -k "probe_mode or distance_bands or diverse_candidates" -v
```

Expected: FAIL because `SpeculativeInterest` has no `probe_mode` fields, the prompt does not request the four distance bands, and selector quota does not account for distance.

**Step 3: Implement minimal schema and prompt changes**

In `src/openbiliclaw/soul/speculator.py`:

- Add fields to `SpeculativeInterest`:

```python
probe_mode: str = "near"
confirmation_source: str = ""
confirmed_at: str = ""
```

- Add a derived property:

```python
@property
def challenge(self) -> bool:
    return self.probe_mode in {"lateral", "bridge", "wildcard"}
```

- Include the new fields in `to_dict()` and `from_dict()`.
- Add `_normalize_probe_mode(value: Any) -> str`, defaulting unknown/missing values to `"near"`.
- Parse `probe_mode` in `_generate()` and `ingest_seeds()`.

In `src/openbiliclaw/llm/prompts.py`:

- Extend `build_speculation_generation_prompt()` rules and output schema to require:

```json
"probe_mode": "near|lateral|bridge|wildcard"
```

- Explain the four distance bands in the system prompt.
- Keep `probe_mode` as guidance, not user-facing copy.

**Step 4: Implement hard local selector quotas**

In `_select_diverse_candidates()`:

- Keep existing confidence and `experience_mode` / `entry_load` diversity.
- Add a first pass that enforces distance-band constraints when viable candidates exist:
  - `near` max = `ceil(limit * 0.40)` when challenge candidates exist.
  - at least one challenge item when any viable challenge exists.
  - for `limit >= 4`, at least two distance bands when available.
- Keep graceful fallback when candidate supply is sparse.

**Step 5: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_speculator.py tests/test_llm_prompts.py -k "probe_mode or distance_bands or diverse_candidates" -v
uv run --extra dev python -m ruff check src/openbiliclaw/soul/speculator.py src/openbiliclaw/llm/prompts.py tests/test_speculator.py tests/test_llm_prompts.py
```

Expected: PASS.

**Step 6: Commit**

```bash
git add tests/test_speculator.py tests/test_llm_prompts.py src/openbiliclaw/soul/speculator.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: add challenge distance bands to interest probes"
```

---

### Task 2: Persist Probe Distance History in Runtime, OpenClaw, and Memory

**Files:**
- Modify: `tests/test_memory_manager.py`
- Modify: `tests/test_refresh_runtime.py`
- Modify: `tests/test_openclaw_adapter.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`
- Modify: `src/openbiliclaw/soul/speculator.py`

**Step 1: Write failing tests**

Add memory round-trip coverage:

```python
def test_discovery_runtime_state_round_trips_probed_distance_bands(tmp_path):
    memory = MemoryManager(data_dir=tmp_path)
    memory.save_discovery_runtime_state({
        "probed_distance_bands": {"bridge": "2026-05-24T12:00:00"},
    })

    state = memory.load_discovery_runtime_state()

    assert state["probed_distance_bands"] == {"bridge": "2026-05-24T12:00:00"}
```

Add selector coverage:

```python
def test_choose_next_probe_candidate_prefers_fresh_probe_mode_after_filters():
    near = SpeculativeInterest(domain="近", probe_mode="near", weight=0.9)
    bridge = SpeculativeInterest(domain="桥", probe_mode="bridge", weight=0.6)

    chosen = choose_next_probe_candidate(
        [near, bridge],
        probed_probe_modes={"near"},
    )

    assert chosen is bridge
```

Add runtime/OpenClaw tests that successful probe selection records `probed_distance_bands`.

**Step 2: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_memory_manager.py tests/test_speculator.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py -k "distance_bands or probe_mode" -v
```

Expected: FAIL because the runtime state and selector do not track distance bands.

**Step 3: Implement runtime state persistence**

In `MemoryManager.load_discovery_runtime_state()` and `save_discovery_runtime_state()`:

- Add default field:

```python
"probed_distance_bands": {}
```

- Preserve it during save/load.

**Step 4: Update probe selection**

In `choose_next_probe_candidate()`:

- Add parameter:

```python
probed_probe_modes: set[str] | None = None
```

- Apply Section 6 ordering:
  1. filter `probed_domains`
  2. filter negative feedback
  3. prefer fresh `experience_mode|entry_load`
  4. prefer fresh `probe_mode`
  5. tie-break by weight/confidence

If all challenge candidates are filtered out by steps 1-2, fallback to `near`.

**Step 5: Record distance history**

In `ContinuousRefreshController._publish_interest_probe_if_available()`:

- Load and prune `probed_distance_bands` with the same cooldown window as `probed_axes`.
- Pass `set(probed_distance_bands)` into `choose_next_probe_candidate()`.
- On successful publish, write:

```python
probed_distance_bands[top.probe_mode] = now.isoformat()
```

In `OpenClawAdapter.get_next_probe()` / `_record_probe_history()`:

- Load `probed_distance_bands`.
- Pass distance history into `choose_next_probe_candidate()`.
- Record selected `probe_mode`.

**Step 6: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_memory_manager.py tests/test_speculator.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py -k "distance_bands or probe_mode" -v
uv run --extra dev python -m ruff check src/openbiliclaw/memory/manager.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/integrations/openclaw/operations.py src/openbiliclaw/soul/speculator.py tests/test_memory_manager.py tests/test_speculator.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py
```

Expected: PASS.

**Step 7: Commit**

```bash
git add tests/test_memory_manager.py tests/test_speculator.py tests/test_refresh_runtime.py tests/test_openclaw_adapter.py src/openbiliclaw/memory/manager.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/integrations/openclaw/operations.py src/openbiliclaw/soul/speculator.py
git commit -m "feat: track interest probe distance history"
```

---

### Task 3: Implement Direct Confirmation Sources and Strong/Weak Probe Chat Classification

**Files:**
- Modify: `tests/test_speculator.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_openclaw_proactive_e2e.py`
- Create: `src/openbiliclaw/soul/interest_writeback.py`
- Modify: `src/openbiliclaw/soul/speculator.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/api/app.py`

**Step 1: Write failing tests**

Add direct confirmation tests:

```python
def test_user_confirm_speculation_records_source_and_confirmed_at():
    speculator.user_confirm_speculation("建筑美学", confirmation_source="profile_confirmed")
    state = speculator._load_state()
    spec = next(item for item in state.active if item.domain == "建筑美学")

    assert spec.status == "confirmed"
    assert spec.confirmation_source == "profile_confirmed"
    assert spec.confirmed_at
```

Add pipeline promotion tests:

```python
async def test_confirmed_speculation_promotes_with_source_weight():
    # Seed confirmed SpeculativeInterest(source=profile_confirmed)
    # Run pipeline tick
    # Assert InterestDomain.source == "profile_confirmed"
    # Assert InterestDomain.weight == 0.60
```

Add API tests:

```python
def test_interest_probe_confirm_from_profile_uses_profile_confirmed_source(client):
    response = client.post(
        "/api/interest-probes/respond",
        json={"domain": "建筑美学", "response": "confirm", "surface": "profile"},
    )

    assert response.status_code == 200
```

Add chat classification tests:

- strong phrase `"这就是我想看的，以后多推这种"` direct-confirms with `chat_confirmed`.
- weak phrase `"有点意思，可以看看"` records `weak_positive` but does not confirm.
- classifier failure defaults to `neutral`.
- probe feedback history stores bounded `raw_text_excerpt`, `classification`, `classifier`, and `resulting_action`.

**Step 2: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_speculator.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py -k "confirm_speculation or probe_chat or profile_confirmed" -v
```

Expected: FAIL because confirmation source, strong/weak chat states, and audit metadata are not implemented.

**Step 3: Extend confirmation source handling**

In `InterestSpeculator.user_confirm_speculation()`:

- Add optional args:

```python
def user_confirm_speculation(
    self,
    domain: str,
    *,
    confirmation_source: str = "probe_confirmed",
) -> bool:
```

- Store `confirmation_source`, `confirmed_at`, `status="confirmed"`, and `confirmation_count=threshold`.

In `ProfileUpdatePipeline` where promoted speculations become `InterestDomain`:

- Create `src/openbiliclaw/soul/interest_writeback.py` with source and weight mapping:

```python
CONFIRMED_INTEREST_WEIGHTS = {
    "probe_confirmed": 0.45,
    "profile_confirmed": 0.60,
    "chat_confirmed": 0.50,
    "buffer_promoted": 0.45,
    "speculated": 0.30,
}
```

- Add:

```python
def confirmed_interest_weight(source: str) -> float: ...
def merge_confirmed_interest(profile: OnionProfile, *, domain: str, specifics: Sequence[str] = (), source: str, first_seen: str = "", last_seen: str = "") -> bool: ...
```

- Use the helper from `ProfileUpdatePipeline` instead of appending duplicate domains.

**Step 4: Update API confirmation surfaces**

In `/api/interest-probes/respond`:

- Accept optional `surface` or `confirmation_source`.
- Map:

```python
surface == "profile" -> "profile_confirmed"
otherwise -> "probe_confirmed"
```

- Continue to default old clients to `probe_confirmed`.

**Step 5: Implement strong/weak chat classifier**

In `src/openbiliclaw/api/app.py`, change the `create_app()` inner closures
`_judge_probe_sentiment()`, `_keyword_judge_sentiment()`, and
`_llm_judge_sentiment()` to return:

```text
strong_positive | weak_positive | neutral | negative
```

`_llm_judge_sentiment()` currently asks for a 3-way scalar. Change that prompt
to request one of:

```text
strong_positive
weak_positive
neutral
negative
```

Keyword fallback should split the current positive set into strong and weak
terms:

```python
strong_positive_terms = ["以后多推", "这就是我想看的", "我就喜欢", "加入我的画像"]
weak_positive_terms = ["有点意思", "可以看看", "偶尔看看", "还行", "先试试"]
negative_terms = ["不感兴趣", "不是这个意思", "别推", "不喜欢"]
```

Before finishing this task, run a global search and migrate every 3-way branch:

```bash
rg -n 'sentiment == "positive"|sentiment == "negative"|chat_positive|chat_negative' src/openbiliclaw/api/app.py
```

At the time this plan was written, the affected branches include interest
probe chat and avoidance probe chat. Avoidance probe semantics are inverted:
negative user sentiment toward an avoidance probe means confirm the avoidance,
while positive sentiment means reject the avoidance. Preserve that inversion
when splitting strong/weak positive.

In the chat branch:

- `strong_positive`: call `user_confirm_speculation(..., confirmation_source="chat_confirmed")`.
- `weak_positive`: record history and defer to Task 4 buffer helper once available.
- `negative`: reject/cooldown.
- `neutral`: no writeback.

Persist audit fields through `_record_probe_feedback_history()` and extend `normalize_probe_feedback_history()` to preserve bounded classification fields.

**Step 6: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_speculator.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py -k "confirm_speculation or probe_chat or profile_confirmed" -v
uv run --extra dev python -m ruff check src/openbiliclaw/soul/interest_writeback.py src/openbiliclaw/soul/speculator.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/app.py tests/test_speculator.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py
uv run --extra dev python -m mypy src/openbiliclaw/soul/interest_writeback.py src/openbiliclaw/soul/speculator.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/app.py
```

Expected: PASS.

**Step 7: Commit**

```bash
git add tests/test_speculator.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py src/openbiliclaw/soul/interest_writeback.py src/openbiliclaw/soul/speculator.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/app.py
git commit -m "feat: confirm interest probes with source-aware semantics"
```

---

### Task 4: Add Short-Term Exploration Buffer and Promote Repeated Weak Evidence

**Files:**
- Create: `src/openbiliclaw/soul/exploration_buffer.py`
- Create: `tests/test_exploration_buffer.py`
- Modify: `tests/test_memory_manager.py`
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/soul/interest_writeback.py`

**Step 1: Write failing unit tests for buffer rules**

Create tests covering:

```python
def test_buffer_promotes_after_three_explicit_weak_events_with_score_threshold():
    now = datetime(2026, 5, 24, tzinfo=UTC)
    state = record_buffer_event({}, domain="城市基础设施观察", source_event="weak_positive_chat", now=now)
    state = record_buffer_event(state, domain="城市基础设施观察", source_event="card_like", now=now + timedelta(days=1))
    state = record_buffer_event(state, domain="城市基础设施观察", source_event="card_more_like", now=now + timedelta(days=2))

    promoted, state = pop_promotable_buffer_entries(state, now=now + timedelta(days=2))

    assert promoted[0]["domain"] == "城市基础设施观察"
    assert promoted[0]["confirmation_source"] == "buffer_promoted"
```

```python
def test_buffer_cooldown_ignores_positive_score_increments():
    now = datetime(2026, 5, 24, tzinfo=UTC)
    state = record_buffer_event({}, domain="城市基础设施观察", source_event="negative", now=now)
    state = record_buffer_event(state, domain="城市基础设施观察", source_event="card_like", now=now + timedelta(hours=1))

    entry = state["entries"][0]
    assert entry["score"] == -3
    assert entry["positive_event_count"] == 0
```

```python
def test_buffer_expiry_is_later_than_promotion_window():
    entry = make_buffer_entry(domain="x", first_seen=now)
    assert entry["expires_at"] == (now + timedelta(days=10)).isoformat()
```

**Step 2: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_exploration_buffer.py tests/test_memory_manager.py tests/test_api_app.py -k "buffer" -v
```

Expected: FAIL because the buffer module and runtime state fields do not exist.

**Step 3: Implement buffer module**

In `src/openbiliclaw/soul/exploration_buffer.py`:

- Implement constants:

```python
PROMOTION_SCORE = 4.0
PROMOTION_WINDOW_DAYS = 7
BUFFER_TTL_DAYS = 10
COOLDOWN_HOURS = 48
EVENT_WEIGHTS = {
    "weak_positive_chat": 1.5,
    "card_like": 1.5,
    "card_more_like": 1.5,
    "long_watch": 0.5,
    "plain_click": 0.25,
    "negative": -3.0,
}
```

- Implement:

```python
def normalize_buffer_key(domain: str, specifics: Sequence[str] = ()) -> str: ...
def normalize_buffer_state(raw: object) -> dict[str, object]: ...
def record_buffer_event(state: dict[str, object], *, domain: str, source_event: str, now: datetime, specifics: Sequence[str] = (), evidence_id: str = "") -> dict[str, object]: ...
def pop_promotable_buffer_entries(state: dict[str, object], *, now: datetime) -> tuple[list[dict[str, object]], dict[str, object]]: ...
```

- Store:

```text
domain
specifics
buffer_key
score
first_seen
expires_at
last_seen
positive_event_count
explicit_event_count
cooldown_until
recent_evidence
```

**Step 4: Persist buffer in runtime state**

In `MemoryManager` runtime state:

- Add default:

```python
"short_term_exploration_buffer": {"entries": []}
```

- Round-trip this field in save/load.

**Step 5: Wire weak signals into the buffer**

In `src/openbiliclaw/api/app.py`:

- In `/api/interest-probes/respond` chat branch:
  - `weak_positive` records `source_event="weak_positive_chat"`.
- In `/api/feedback`:
  - `feedback_type == "like"` records `source_event="card_like"`.
  - `/api/delight/respond` with `response == "like"` records `source_event="card_more_like"` because the UI copy is "这类多来点".
  - `feedback_type == "dislike"` records `source_event="negative"`.
- In `/api/recommendation-click`:
  - Record `source_event="plain_click"`.
  - Do not direct-confirm a long-term interest from click alone.

**Step 6: Promote buffer entries**

After each buffer event:

- Call `pop_promotable_buffer_entries()`.
- For each promoted entry, write a confirmed interest with:

```text
source = buffer_promoted
weight = 0.45
```

Use the same merge helper added in Task 3 so existing domains are raised/merged rather than duplicated.

**Step 7: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_exploration_buffer.py tests/test_memory_manager.py tests/test_api_app.py -k "buffer or recommendation_click or feedback" -v
uv run --extra dev python -m ruff check src/openbiliclaw/soul/exploration_buffer.py src/openbiliclaw/memory/manager.py src/openbiliclaw/api/app.py src/openbiliclaw/soul/interest_writeback.py tests/test_exploration_buffer.py tests/test_memory_manager.py tests/test_api_app.py
uv run --extra dev python -m mypy src/openbiliclaw/soul/exploration_buffer.py src/openbiliclaw/memory/manager.py src/openbiliclaw/api/app.py src/openbiliclaw/soul/interest_writeback.py
```

Expected: PASS.

**Step 8: Commit**

```bash
git add tests/test_exploration_buffer.py tests/test_memory_manager.py tests/test_api_app.py src/openbiliclaw/soul/exploration_buffer.py src/openbiliclaw/memory/manager.py src/openbiliclaw/api/app.py src/openbiliclaw/soul/interest_writeback.py
git commit -m "feat: add short-term exploration buffer"
```

---

### Task 5: Enforce New-Interest Amplification Guard in Curator and Final Selection

**Files:**
- Modify: `tests/test_pool_curator.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/recommendation/curator.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

**Step 1: Write failing tests for 24h rolling budget**

Add database/curator tests:

```python
def test_get_recommendation_signals_since_uses_presented_at_window(database):
    # Insert old and recent recommendation rows with topic_group.
    # Mark only one inside the last 24h.
    rows = database.get_recent_recommendation_signals_since(
        since=datetime.now(UTC) - timedelta(hours=24),
    )
    assert len(rows) == 1
```

```python
def test_pool_curator_marks_over_budget_amplification_key():
    context = curator.build_context(
        newly_confirmed_amplification_keys={"城市基础设施观察"},
        rolling_window_hours=24,
    )

    assert "城市基础设施观察" in context.over_budget_amplification_keys
```

**Step 2: Write failing tests for per-batch cap**

Add recommendation-engine test:

```python
def test_select_diversified_batch_caps_newly_confirmed_direction():
    items = [
        item("A1", topic_group="城市基础设施观察"),
        item("A2", topic_group="城市基础设施观察"),
        item("A3", topic_group="城市基础设施观察"),
        item("B1", topic_group="游戏推荐"),
        item("C1", topic_group="手工木工"),
    ]

    selected = RecommendationEngine._select_diversified_batch(
        items,
        limit=4,
        amplification_guard={"城市基础设施观察"},
    )

    assert sum(i.topic_group == "城市基础设施观察" for i in selected) <= 1
```

**Step 3: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_pool_curator.py tests/test_recommendation_engine.py -k "amplification or rolling_budget" -v
```

Expected: FAIL because there is no 24h query, amplification context, or final cap.

**Step 4: Add 24h recommendation query**

Before adding the query, verify the needed columns are covered by schema
creation or migrations:

```bash
rg -n "presented_at|topic_group|topic_key|_ensure_.*topic" src/openbiliclaw/storage/database.py
```

`recommendations.presented_at` and `content_cache.topic_key` are in the base
schema. `content_cache.topic_group` is added by existing column-ensure logic; if
a test fixture bypasses `Database.__init__`, adjust that fixture or add the same
ensure call before querying.

Then add to `Database`:

```python
def get_recent_recommendation_signals_since(self, *, since: datetime) -> list[dict[str, Any]]:
    self._ensure_fresh_read()
    # Use presented_at when available, otherwise created_at for legacy rows.
```

Return at least:

```text
bvid
topic_key
topic_group
source
created_at
presented_at
```

**Step 5: Add amplification key helpers**

In `src/openbiliclaw/recommendation/curator.py`:

```python
def normalize_amplification_key(value: str) -> str: ...
def candidate_amplification_keys(item: DiscoveredContent) -> set[str]: ...
```

For v1, match confirmed interests against:

```text
normalized domain
specific names
topic_group
topic_key
```

**Step 6: Extend PoolCurator context**

In `ScoringContext`, add:

```python
newly_confirmed_amplification_keys: frozenset[str] = frozenset()
over_budget_amplification_keys: frozenset[str] = frozenset()
```

In `PoolCurator.build_context()`:

- Accept `newly_confirmed_amplification_keys`.
- Use the new 24h query.
- Calculate rolling share.
- Mark keys over budget at `>= 0.25`.

Candidates matching over-budget keys should get a strong score penalty or be excluded before final selection. Keep this small; the final selector still owns the hard per-batch cap.

**Step 7: Enforce final per-batch cap**

In `RecommendationEngine._select_diversified_batch()` and MMR path:

- Add optional `amplification_guard` / `amplification_keys` parameter.
- Cap selected items matching each newly confirmed key:

```python
max_new_direction_items = max(1, math.floor(limit * 0.25))
```

- Apply the same cap in both non-embedding and MMR paths.

**Step 8: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_pool_curator.py tests/test_recommendation_engine.py -k "amplification or rolling_budget" -v
uv run --extra dev python -m ruff check src/openbiliclaw/storage/database.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/recommendation/engine.py tests/test_pool_curator.py tests/test_recommendation_engine.py
uv run --extra dev python -m mypy src/openbiliclaw/storage/database.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/recommendation/engine.py
```

Expected: PASS.

**Step 9: Commit**

```bash
git add tests/test_pool_curator.py tests/test_recommendation_engine.py src/openbiliclaw/storage/database.py src/openbiliclaw/recommendation/curator.py src/openbiliclaw/recommendation/engine.py
git commit -m "feat: cap newly confirmed recommendation directions"
```

---

### Task 6: Surface Probe Mode and Profile Confirmation in API/UI

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/web/js/api.js`
- Modify: `src/openbiliclaw/web/js/views/profile.js`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`

**Step 1: Write failing tests**

Add API tests that `/api/profile-summary`, `/api/interest-probes/pending`, and runtime probe events include:

```text
probe_mode
challenge
```

Add profile-action tests that profile-page confirm passes `surface="profile"` or `confirmation_source="profile_confirmed"`.

**Step 2: Run tests to verify failure**

Run:

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_mobile_web_view_models.py -k "probe_mode or profile_confirm" -v
```

Expected: FAIL because the API responses and JS calls do not carry probe mode/profile source consistently.

**Step 3: Update API serializers and events**

In API profile/probe serializers:

- Include:

```json
{
  "probe_mode": "bridge",
  "challenge": true
}
```

In runtime probe event payloads, include the same fields so inbox and mobile web can render them.

**Step 4: Update JS clients**

In `src/openbiliclaw/web/js/api.js`:

- Allow `respondToProbe(domain, action, options)` to pass `surface`.

In `src/openbiliclaw/web/js/views/profile.js`:

- For profile speculative-interest confirm buttons, call:

```javascript
respondToProbe(domain, "confirm", { surface: "profile" })
```

In desktop JS:

- Preserve current probe-card confirm as default probe source.
- If confirming from a profile/speculative list, pass profile surface.

**Step 5: Run tests and checks to verify pass**

Run:

```bash
uv run --extra dev python -m pytest tests/test_api_app.py tests/test_mobile_web_view_models.py -k "probe_mode or profile_confirm" -v
uv run --extra dev python -m ruff check src/openbiliclaw/api/app.py src/openbiliclaw/web/js/api.js src/openbiliclaw/web/js/views/profile.js src/openbiliclaw/web/desktop/assets/js/app.js tests/test_api_app.py tests/test_mobile_web_view_models.py
```

Expected: PASS.

Note: Python tests cover API/view-model contracts. If JS behaviour changes
beyond payload shape, add or run the relevant frontend/extension JS tests in the
same task.

**Step 6: Commit**

```bash
git add tests/test_api_app.py tests/test_mobile_web_view_models.py src/openbiliclaw/api/app.py src/openbiliclaw/web/js/api.js src/openbiliclaw/web/js/views/profile.js src/openbiliclaw/web/desktop/assets/js/app.js
git commit -m "feat: expose challenge probe metadata"
```

---

### Task 7: Update Documentation and Changelog

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/plans/2026-05-24-interest-probe-challenge-spec.md` if implementation decisions differ

**Step 1: Update soul module docs**

In `docs/modules/soul.md`, document:

- `probe_mode` distance bands.
- direct confirmation sources.
- strong/weak chat classification.
- short-term exploration buffer and promotion rules.
- `probed_distance_bands`.

**Step 2: Update recommendation docs**

In `docs/modules/recommendation.md`, document:

- `amplification_key`.
- per-batch cap formula.
- 24h rolling budget.
- PoolCurator/final-selection responsibilities.

**Step 3: Update runtime/memory docs**

In `docs/modules/runtime.md` and `docs/modules/memory.md`, document:

- new `discovery_runtime_state` fields:

```text
probed_distance_bands
short_term_exploration_buffer
```

**Step 4: Update extension docs**

In `docs/modules/extension.md`, document profile confirm vs probe-message confirm behaviour if UI payloads changed.

**Step 5: Update changelog**

Append a current-version bullet in `docs/changelog.md`:

```markdown
- Interest probes now use challenge distance bands, direct confirmation sources, weak-signal buffering, and guarded recommendation amplification to reduce short-term overfitting.
```

**Step 6: Commit**

```bash
git add docs/modules/soul.md docs/modules/recommendation.md docs/modules/runtime.md docs/modules/memory.md docs/modules/extension.md docs/changelog.md docs/plans/2026-05-24-interest-probe-challenge-spec.md
git commit -m "docs: describe challenge probe lifecycle"
```

---

### Task 8: Final Verification

**Files:**
- Test: `tests/test_speculator.py`
- Test: `tests/test_llm_prompts.py`
- Test: `tests/test_memory_manager.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_openclaw_adapter.py`
- Test: `tests/test_openclaw_proactive_e2e.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_exploration_buffer.py`
- Test: `tests/test_pool_curator.py`
- Test: `tests/test_recommendation_engine.py`
- Test: `tests/test_mobile_web_view_models.py`

**Step 1: Run focused verification**

Run:

```bash
uv run --extra dev python -m pytest \
  tests/test_speculator.py \
  tests/test_llm_prompts.py \
  tests/test_memory_manager.py \
  tests/test_refresh_runtime.py \
  tests/test_openclaw_adapter.py \
  tests/test_openclaw_proactive_e2e.py \
  tests/test_api_app.py \
  tests/test_exploration_buffer.py \
  tests/test_pool_curator.py \
  tests/test_recommendation_engine.py \
  tests/test_mobile_web_view_models.py \
  -v
```

Expected: PASS.

**Step 2: Run lint**

Run:

```bash
uv run --extra dev python -m ruff check \
  src/openbiliclaw/soul/speculator.py \
  src/openbiliclaw/soul/exploration_buffer.py \
  src/openbiliclaw/soul/pipeline.py \
  src/openbiliclaw/soul/engine.py \
  src/openbiliclaw/api/app.py \
  src/openbiliclaw/memory/manager.py \
  src/openbiliclaw/runtime/refresh.py \
  src/openbiliclaw/integrations/openclaw/operations.py \
  src/openbiliclaw/storage/database.py \
  src/openbiliclaw/recommendation/curator.py \
  src/openbiliclaw/recommendation/engine.py \
  tests/test_speculator.py \
  tests/test_llm_prompts.py \
  tests/test_memory_manager.py \
  tests/test_refresh_runtime.py \
  tests/test_openclaw_adapter.py \
  tests/test_openclaw_proactive_e2e.py \
  tests/test_api_app.py \
  tests/test_exploration_buffer.py \
  tests/test_pool_curator.py \
  tests/test_recommendation_engine.py \
  tests/test_mobile_web_view_models.py
```

Expected: PASS.

**Step 3: Run type check if core APIs changed**

Run:

```bash
uv run --extra dev python -m mypy src/
```

Expected: PASS.

**Step 4: Run full test suite if focused verification passes**

Run:

```bash
uv run --extra dev python -m pytest
```

Expected: PASS.

**Step 5: Final status**

If all commands pass, report:

- commits created
- tests run
- remaining known tradeoffs
- whether `short_term_exploration_buffer` used runtime state or another storage path
