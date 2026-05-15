# Interest Probe Novelty Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent speculative interest probes from repeating existing profile interests, recent probe history, or overused experience axes while preserving useful lateral exploration.

**Architecture:** Add a local novelty guard in `openbiliclaw.soul.speculator` that builds coverage from the current profile, speculation state, and runtime probe history. Route LLM-generated candidates, PreferenceAnalyzer seeds, runtime push, and OpenClaw probe selection through the same domain / axis history model. Keep the first version string-based with Chinese bigram overlap, no embedding dependency.

**Tech Stack:** Python dataclasses, existing `InterestSpeculator`, `MemoryManager`, runtime controller, OpenClaw adapter, pytest.

---

### Task 1: Persist Probe Axis History

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing tests**

Update `test_discovery_runtime_state_defaults_when_missing` to expect:

```python
"probed_domains": {},
"probed_axes": {},
```

Extend `test_discovery_runtime_state_round_trips_to_json` input with:

```python
"probed_domains": {"建筑美学": "2026-03-10T10:30:00"},
"probed_axes": {"aesthetic|light": "2026-03-10T10:30:00"},
```

Assert both fields round-trip:

```python
assert state["probed_domains"] == {"建筑美学": "2026-03-10T10:30:00"}
assert state["probed_axes"] == {"aesthetic|light": "2026-03-10T10:30:00"}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_manager.py -k discovery_runtime_state -v`

Expected: FAIL because `probed_axes` is not returned or saved.

**Step 3: Implement persistence**

In `load_discovery_runtime_state()`, add `probed_domains` and `probed_axes` to `default_state` and returned payload:

```python
"probed_domains": loaded.get("probed_domains", {}),
"probed_axes": loaded.get("probed_axes", {}),
```

In `save_discovery_runtime_state()`, add:

```python
"probed_axes": state.get("probed_axes", {}),
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_manager.py -k discovery_runtime_state -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "fix: persist probe axis history"
```

---

### Task 2: Add ProbeNoveltyGuard

**Files:**
- Modify: `src/openbiliclaw/soul/speculator.py`
- Test: `tests/test_speculator.py`

**Step 1: Write failing tests**

Add tests that build an `OnionProfile` with:

```python
InterestDomain(
    domain="AI",
    specifics=[
        InterestSpecific(name="ComfyUI工作流"),
        InterestSpecific(name="图像生成实战"),
    ],
)
```

Test expected behaviors:

```python
guard = ProbeNoveltyGuard.from_profile_and_state(profile, state)
assert guard.is_duplicate_domain("AI") is True
assert guard.is_duplicate_domain("ComfyUI工作流拆解") is True
assert guard.filter_specifics(["ComfyUI工作流", "Stable Diffusion LoRA"]) == ["Stable Diffusion LoRA"]
```

Also test recent probe history:

```python
guard = ProbeNoveltyGuard.from_profile_and_state(
    profile,
    state,
    probed_domains={"城市漫游"},
)
assert guard.is_duplicate_domain("城市漫游路线") is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_speculator.py -k novelty_guard -v`

Expected: FAIL because `ProbeNoveltyGuard` does not exist.

**Step 3: Implement minimal guard**

In `speculator.py`, add:

```python
@dataclass
class ProbeNoveltyGuard:
    exact_terms: set[str] = field(default_factory=set)
    fuzzy_terms: set[str] = field(default_factory=set)

    @classmethod
    def from_profile_and_state(
        cls,
        profile: OnionProfile | None,
        state: SpeculativeState,
        *,
        probed_domains: set[str] | None = None,
    ) -> "ProbeNoveltyGuard":
        ...

    def is_duplicate_domain(self, domain: str) -> bool:
        ...

    def filter_specifics(self, specifics: list[str]) -> list[str]:
        ...
```

Use helpers:

```python
def _normalize_probe_term(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())

def _has_probe_term_overlap(candidate: str, existing: str) -> bool:
    normalized_candidate = _normalize_probe_term(candidate)
    normalized_existing = _normalize_probe_term(existing)
    if not normalized_candidate or not normalized_existing:
        return False
    if normalized_candidate in normalized_existing or normalized_existing in normalized_candidate:
        return True
    candidate_bigrams = _chinese_bigrams(normalized_candidate)
    existing_bigrams = _chinese_bigrams(normalized_existing)
    return len(candidate_bigrams) >= 4 and len(candidate_bigrams & existing_bigrams) >= 2
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_speculator.py -k novelty_guard -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/speculator.py tests/test_speculator.py
git commit -m "feat: add probe novelty guard"
```

---

### Task 3: Apply Guard to LLM Generation and Seeds

**Files:**
- Modify: `src/openbiliclaw/soul/speculator.py`
- Test: `tests/test_speculator.py`

**Step 1: Write failing tests**

Add a fake LLM response containing:

```json
{
  "domain": "ComfyUI工作流拆解",
  "category": "AI",
  "reason": "你已经在图像生成方向有持续观看，这个方向只是更具体的工作流拆解。",
  "confidence": 0.5,
  "experience_mode": "knowledge",
  "entry_load": "heavy",
  "specifics": ["ComfyUI工作流", "节点搭建技巧"]
}
```

With the profile from Task 2, `force_tick()` should not add that duplicate candidate.

Add a seed test:

```python
added = speculator.ingest_seeds(
    [{"name": "ComfyUI工作流拆解", "category": "AI", "weight": 0.5}],
    profile=profile,
)
assert added == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_speculator.py -k "duplicate_profile_interest or seed_existing_profile" -v`

Expected: FAIL because generation and seed ingestion do not consult profile specifics.

**Step 3: Implement guard usage**

Change `ingest_seeds()` signature to:

```python
def ingest_seeds(
    self,
    seeds: list[dict[str, Any]],
    *,
    profile: OnionProfile | None = None,
    probed_domains: set[str] | None = None,
) -> int:
```

Build:

```python
guard = ProbeNoveltyGuard.from_profile_and_state(
    profile,
    state,
    probed_domains=probed_domains,
)
```

Skip seeds when `guard.is_duplicate_domain(domain)`.

In `_generate()`, build the same guard from `profile` and `state`. For each LLM candidate:

```python
if guard.is_duplicate_domain(domain):
    rejected_reasons.append(f"{domain} (duplicate coverage)")
    continue
filtered_specific_names = guard.filter_specifics([s.name for s in specifics])
specifics = [SpeculativeSpecific(name=name) for name in filtered_specific_names]
if len(specifics) < 2:
    rejected_reasons.append(f"{domain} (specifics<2 after novelty)")
    continue
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_speculator.py -k "duplicate_profile_interest or seed_existing_profile or novelty_guard" -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/speculator.py tests/test_speculator.py
git commit -m "fix: dedupe probes against profile coverage"
```

---

### Task 4: Make Active Pool Diversity Aware

**Files:**
- Modify: `src/openbiliclaw/soul/speculator.py`
- Test: `tests/test_speculator.py`

**Step 1: Write failing test**

Seed state with active probes:

```python
SpeculativeInterest(domain="量子物理", experience_mode="knowledge", entry_load="heavy")
SpeculativeInterest(domain="AI治理", experience_mode="knowledge", entry_load="heavy")
```

Fake LLM returns one high-confidence `knowledge|heavy` candidate and one lower-confidence `wander_observe|light` candidate. With one free slot, expected generated domain is the lower-confidence fresh axis.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_speculator.py -k active_pool_diversity -v`

Expected: FAIL because `_select_diverse_candidates()` starts from an empty selected list.

**Step 3: Implement active pool context**

Change selector signature:

```python
def _select_diverse_candidates(
    candidates: list[SpeculativeInterest],
    *,
    limit: int,
    existing: list[SpeculativeInterest] | None = None,
) -> list[SpeculativeInterest]:
```

Initialize `selected` scoring context with `existing or []`, but return only newly selected candidates:

```python
context = list(existing or [])
selected: list[SpeculativeInterest] = []
...
score = _candidate_priority(candidate, context + selected)
```

Call it from `_generate()` with current active specs:

```python
existing_active = [s for s in state.active if s.status == "active"]
for candidate in _select_diverse_candidates(candidates, limit=slots, existing=existing_active):
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_speculator.py -k active_pool_diversity -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/speculator.py tests/test_speculator.py
git commit -m "fix: diversify probes against active pool"
```

---

### Task 5: Record OpenClaw Probe History

**Files:**
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing test**

Add a test where `get_next_probe()` is called twice against two active specs:

```python
first = await adapter.get_next_probe()
second = await adapter.get_next_probe()

assert first.probe is not None
assert second.probe is not None
assert second.probe.domain != first.probe.domain
```

Assert runtime state now contains the first domain and axis:

```python
assert "量子物理" in memory_manager.runtime_state["probed_domains"]
assert "knowledge|heavy" in memory_manager.runtime_state["probed_axes"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openclaw_adapter.py -k next_probe_records_history -v`

Expected: FAIL because OpenClaw reads but does not write probe history.

**Step 3: Implement history write**

Import `build_probe_axis`. After selecting `top`, update runtime state:

```python
probed_domains = dict((runtime_state.get("probed_domains") or {}))
probed_axes = dict((runtime_state.get("probed_axes") or {}))
now = datetime.now().isoformat()
probed_domains[domain.lower()] = now
axis = build_probe_axis(
    experience_mode=getattr(top, "experience_mode", ""),
    entry_load=getattr(top, "entry_load", ""),
)
if axis:
    probed_axes[axis] = now
runtime_state["probed_domains"] = probed_domains
runtime_state["probed_axes"] = probed_axes
save_runtime_state = getattr(self.services.memory_manager, "save_discovery_runtime_state", None)
if callable(save_runtime_state):
    save_runtime_state(runtime_state)
```

Pass both histories into `choose_next_probe_candidate()`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openclaw_adapter.py -k next_probe_records_history -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations/openclaw/operations.py tests/test_openclaw_adapter.py
git commit -m "fix: record openclaw probe history"
```

---

### Task 6: Wire Seed Profile Context and Docs

**Files:**
- Modify: `src/openbiliclaw/soul/layer_updaters.py`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/changelog.md`
- Test: `tests/test_soul_engine.py` or existing seed ingestion tests if sufficient

**Step 1: Write or update test**

If an existing layer updater test covers `speculative_interests`, add a profile with an existing matching interest and assert no duplicate seed is injected. If no suitable test exists, keep coverage in `tests/test_speculator.py` from Task 3 and use this task for wiring only.

**Step 2: Implement wiring**

In `_update_interest()`, change:

```python
added = speculator.ingest_seeds(speculative_seeds)
```

to:

```python
added = speculator.ingest_seeds(speculative_seeds, profile=profile)
```

**Step 3: Update docs**

In `docs/modules/soul.md`, document that speculative probes dedupe against profile domain/specifics, active/cooldown state, and recent probe history.

In `docs/modules/memory.md`, document `discovery_runtime_state.probed_axes`.

In `docs/changelog.md`, add a current-version note describing probe novelty and axis persistence.

**Step 4: Run focused tests**

Run:

```bash
pytest tests/test_speculator.py tests/test_memory_manager.py tests/test_openclaw_adapter.py -k "novelty_guard or duplicate_profile_interest or seed_existing_profile or active_pool_diversity or discovery_runtime_state or next_probe_records_history" -v
```

Expected: PASS.

---

### Task 7: Record and Use Probe Feedback History

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/soul/speculator.py`
- Modify: `src/openbiliclaw/soul/layer_updaters.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/changelog.md`
- Test: `tests/test_memory_manager.py`
- Test: `tests/test_speculator.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add a `test_discovery_runtime_state_round_trips_probe_feedback_history` case:

```python
memory.save_discovery_runtime_state(
    {
        "probe_feedback_history": [
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "axis": "wander_observe|light",
                "created_at": "2026-05-15T10:00:00",
            }
        ]
    }
)
state = memory.load_discovery_runtime_state()
assert state["probe_feedback_history"][0]["domain"] == "城市漫游路线"
```

Add a speculator guard test:

```python
guard = ProbeNoveltyGuard.from_profile_and_state(
    None,
    SpeculativeState(),
    feedback_history=[
        {"domain": "城市漫游路线", "response": "reject", "specifics": ["老街路线"]}
    ],
)
assert guard.is_duplicate_domain("城市漫游隐藏路线")
assert guard.filter_specifics(["老街路线", "城市声音采样"]) == ["城市声音采样"]
```

Add an API test for `/api/interest-probes/respond` reject:

```python
response = client.post(
    "/api/interest-probes/respond",
    json={"domain": "城市漫游路线", "response": "reject"},
)
assert response.status_code == 200
assert memory.runtime_state["probe_feedback_history"][0]["response"] == "reject"
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest \
  tests/test_memory_manager.py::test_discovery_runtime_state_round_trips_probe_feedback_history \
  tests/test_speculator.py::test_probe_novelty_guard_matches_negative_feedback_history \
  tests/test_api_app.py::TestBackendAPI::test_interest_probe_reject_records_feedback_history \
  -q
```

Expected: FAIL because the runtime state has no `probe_feedback_history`, the guard ignores feedback history, and the API endpoint does not append feedback records.

**Step 3: Implement persistence and helpers**

In `MemoryManager.load_discovery_runtime_state()` and `save_discovery_runtime_state()`, add:

```python
"probe_feedback_history": self._as_dict_list(loaded.get("probe_feedback_history", []))[-100:]
```

In `openbiliclaw.soul.speculator`, add a small helper surface:

```python
PROBE_FEEDBACK_HISTORY_LIMIT = 100
NEGATIVE_PROBE_FEEDBACK_RESPONSES = {"reject", "chat_negative"}

def append_probe_feedback_history(history: object, entry: dict[str, object]) -> list[dict[str, object]]:
    ...
```

Keep entries to `domain`, `response`, `axis`, `category`, `reason`, `specifics`, `message`, and `created_at`.

**Step 4: Apply feedback history to novelty and selection**

Extend `ProbeNoveltyGuard.from_profile_and_state(..., feedback_history=None)` so negative feedback entries add domain and specifics to coverage.

Extend `InterestSpeculator.tick()`, `force_tick()`, `_generate()`, and `ingest_seeds()` with optional `feedback_history` and pass it into the guard.

Extend `choose_next_probe_candidate(..., feedback_history=None)` so it skips domains overlapping negative feedback and, among otherwise equal candidates, prefers axes without negative feedback.

Load `probe_feedback_history` from runtime state in `ProfileUpdatePipeline._run_speculator_tick()`, `layer_updaters._update_interest()`, runtime probe push, and OpenClaw `get_next_probe()`.

**Step 5: Record endpoint feedback**

In `/api/interest-probes/respond`, append one feedback-history record for each valid confirm / reject / chat response. Capture active spec metadata before mutating speculator state so rejected probes still preserve category, reason, specifics, and axis. Store chat responses as `chat_positive`, `chat_negative`, or `chat_neutral`.

**Step 6: Run tests to verify they pass**

Run:

```bash
uv run --extra dev python -m pytest \
  tests/test_memory_manager.py::test_discovery_runtime_state_round_trips_probe_feedback_history \
  tests/test_speculator.py::test_probe_novelty_guard_matches_negative_feedback_history \
  tests/test_api_app.py::TestBackendAPI::test_interest_probe_reject_records_feedback_history \
  -q
```

Expected: PASS.

**Step 7: Run focused regression checks**

Run:

```bash
uv run --extra dev python -m pytest \
  tests/test_speculator.py tests/test_memory_manager.py tests/test_openclaw_adapter.py \
  -q
```

Expected: PASS.

**Step 5: Run broader checks**

Run:

```bash
ruff check src/ tests/
pytest tests/test_speculator.py tests/test_memory_manager.py tests/test_openclaw_adapter.py
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/soul/layer_updaters.py docs/modules/soul.md docs/modules/memory.md docs/changelog.md tests/test_speculator.py tests/test_memory_manager.py tests/test_openclaw_adapter.py
git commit -m "docs: describe probe novelty governance"
```
