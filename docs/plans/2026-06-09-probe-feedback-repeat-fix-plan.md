# Probe Feedback Repeat Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make interest and avoidance probe feedback durable under packaged-app background concurrency, so handled probes do not reappear after the user clicks confirm/reject.

**Architecture:** Replace stale JSON snapshot writes with atomic latest-state update APIs. Keep LLM calls outside locks, then merge generated candidates into the latest state. Add extension-side handled-key dedupe only as a UI fallback; the backend remains the source of truth.

**Tech Stack:** Python 3.12, FastAPI, JSON state files, pytest, vanilla JS Chrome extension tests, Ruff, MyPy.

---

## Source Spec

- Spec: `docs/plans/2026-06-09-probe-feedback-repeat-fix-spec.md`
- Root cause: lost update across `speculative_state.json`, `avoidance_state.json`, and `discovery_runtime.json`.

## Preconditions

- Work in a clean branch or worktree.
- Do not touch user-local `config.toml`, runtime `data/`, or existing unrelated worktree changes.
- Use `.venv/bin/pytest` or `uv run pytest`; direct `pytest` may not be on PATH.
- Commit per task.

---

### Task 1: Add Atomic JSON State Update Helper

**Files:**
- Create: `src/openbiliclaw/memory/json_state.py`
- Test: `tests/test_json_state.py`

**Step 1: Write failing tests**

Create `tests/test_json_state.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from openbiliclaw.memory.json_state import update_json_state


class _CounterState:
    def __init__(self, count: int = 0) -> None:
        self.count = count

    @classmethod
    def from_dict(cls, raw: object) -> "_CounterState":
        if not isinstance(raw, dict):
            return cls()
        return cls(count=int(raw.get("count", 0)))

    def to_dict(self) -> dict[str, int]:
        return {"count": self.count}


def test_update_json_state_reads_latest_on_each_update(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    update_json_state(
        path,
        default_factory=lambda: {"items": []},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"items": []},
        serialize=lambda state: state,
        mutate=lambda state: state["items"].append("a"),
    )
    update_json_state(
        path,
        default_factory=lambda: {"items": []},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"items": []},
        serialize=lambda state: state,
        mutate=lambda state: state["items"].append("b"),
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {"items": ["a", "b"]}


def test_update_json_state_recovers_from_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    state = update_json_state(
        path,
        default_factory=lambda: {"count": 0},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"count": 0},
        serialize=lambda state: state,
        mutate=lambda state: state.update({"count": state["count"] + 1}),
    )

    assert state == {"count": 1}
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 1}


def test_update_json_state_serializes_typed_state_without_re_normalizing(tmp_path: Path) -> None:
    path = tmp_path / "typed.json"

    first = update_json_state(
        path,
        default_factory=_CounterState,
        normalize=_CounterState.from_dict,
        serialize=lambda state: state.to_dict(),
        mutate=lambda state: setattr(state, "count", state.count + 1),
    )
    second = update_json_state(
        path,
        default_factory=_CounterState,
        normalize=_CounterState.from_dict,
        serialize=lambda state: state.to_dict(),
        mutate=lambda state: setattr(state, "count", state.count + 1),
    )

    assert first.count == 1
    assert second.count == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 2}
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_json_state.py -q
```

Expected: FAIL because `openbiliclaw.memory.json_state` does not exist.

**Step 3: Implement helper**

Create `src/openbiliclaw/memory/json_state.py` with:

```python
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

T = TypeVar("T")
_MISSING = object()

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _process_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return _MISSING
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return _MISSING


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_json_state(
    path: Path,
    *,
    default_factory: Callable[[], T],
    normalize: Callable[[Any], T],
    serialize: Callable[[T], Any],
    mutate: Callable[[T], T | None],
) -> T:
    path = Path(path)
    with _process_lock(path):
        with _file_lock(path):
            raw = _read_json(path)
            state = default_factory() if raw is _MISSING else normalize(raw)
            result = mutate(state)
            next_state = state if result is None else result
            _atomic_write_json(path, serialize(next_state))
            return next_state
```

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/pytest tests/test_json_state.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/memory/json_state.py tests/test_json_state.py
git commit -m "feat: add atomic json state updates"
```

---

### Task 2: Protect Discovery Runtime State From Lost Updates

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`
- Test: `tests/test_memory_manager.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing MemoryManager update test**

Add to `tests/test_memory_manager.py`:

```python
def test_update_discovery_runtime_state_preserves_feedback_history(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    latest = memory.update_discovery_runtime_state(
        lambda state: state["probe_feedback_history"].append(
            {"domain": "建筑美学", "response": "confirm"}
        )
    )
    assert latest["probe_feedback_history"]

    stale["probed_domains"] = {"建筑美学": "2026-06-09T10:00:00"}
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["probe_feedback_history"][0]["domain"] == "建筑美学"
```

Expected behavior after the fix: a legacy full save must not erase append-only feedback history.

Also add a real-disk concurrency regression; do not use an in-memory fake for this case:

```python
import threading


def test_save_discovery_runtime_state_merges_concurrent_feedback_history(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    first = memory.load_discovery_runtime_state()
    second = memory.load_discovery_runtime_state()
    first["probe_feedback_history"] = [{"domain": "建筑美学", "response": "confirm", "created_at": "2026-06-09T10:00:00"}]
    second["probe_feedback_history"] = [{"domain": "城市基础设施", "response": "reject", "created_at": "2026-06-09T10:00:01"}]

    barrier = threading.Barrier(2)

    def _save(payload: dict[str, object]) -> None:
        barrier.wait(timeout=5)
        memory.save_discovery_runtime_state(payload)

    t1 = threading.Thread(target=_save, args=(first,))
    t2 = threading.Thread(target=_save, args=(second,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    domains = {
        str(item.get("domain"))
        for item in memory.load_discovery_runtime_state()["probe_feedback_history"]
    }
    assert {"建筑美学", "城市基础设施"} <= domains
```

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_memory_manager.py -k update_discovery_runtime_state_preserves_feedback_history -q
```

Expected: FAIL because `update_discovery_runtime_state` does not exist, or because stale save clears history.

**Step 3: Implement runtime update API**

In `MemoryManager`:

- Add `_normalize_discovery_runtime_state(raw: dict[str, object]) -> dict[str, object]` to share normalization between load/save/update.
- Add:

```python
def update_discovery_runtime_state(
    self,
    mutator: Callable[[dict[str, object]], dict[str, object] | None],
) -> dict[str, object]:
    from openbiliclaw.memory.json_state import update_json_state

    def _mutate(state: dict[str, object]) -> dict[str, object]:
        result = mutator(state)
        return state if result is None else result

    return update_json_state(
        self._discovery_runtime_state_path,
        default_factory=self._default_discovery_runtime_state,
        normalize=self._normalize_discovery_runtime_state,
        serialize=lambda state: state,
        mutate=_mutate,
    )
```

- Make `save_discovery_runtime_state()` a thin wrapper around `update_json_state()` for legacy full saves only.
- When `save_discovery_runtime_state()` receives stale payloads, preserve append-only histories from latest disk state:
  - `probe_feedback_history`
  - `avoidance_probe_feedback_history`
  - `short_term_exploration_buffer.entries`

The preservation merge must run inside the same exclusive lock as the read and final atomic write:

```python
def save_discovery_runtime_state(self, payload: dict[str, object]) -> None:
    incoming = self._normalize_discovery_runtime_state(payload)

    def _merge(latest: dict[str, object]) -> dict[str, object]:
        merged = self._merge_discovery_runtime_state(latest=latest, incoming=incoming)
        return merged

    self.update_discovery_runtime_state(_merge)
```

Keep this preservation conservative:

- Merge history records by `(domain, response, created_at, raw_text_excerpt)`.
- Merge `short_term_exploration_buffer.entries` by stable event identity.
- Preserve probe runtime maps from both latest disk state and incoming payload: `probed_domains`, `probed_axes`, `probed_distance_bands`, `probed_avoidance_domains`, `probed_avoidance_axes`, and `last_probe_kind`.
- For timestamp maps, keep the newer timestamp per key when both sides contain the same key.
- `last_probe_kind` is scalar, not a timestamp map. Production updates to it must use `update_discovery_runtime_state()`. In legacy full-save merge, if latest disk state already has a non-empty `last_probe_kind`, prefer latest disk over incoming to prevent a stale full save from reverting the alternation state. If latest is empty and incoming is non-empty, keep incoming.
- Do not cap or truncate append-only histories in this fix; retention must be a separate documented data-retention change if needed later.

**Step 4: Replace all production runtime read-modify-write call sites**

First run:

```bash
rg -n "save_discovery_runtime_state" src/openbiliclaw
```

Convert every production load-mutate-save runtime write to `update_discovery_runtime_state()`, not just probe-specific paths. At minimum this includes:

- `api/app.py::_record_probe_feedback_history()`
- `api/app.py::_record_exploration_buffer_event()`
- `api/app.py` manual delight cooldown clear
- `api/app.py::_persist_xhs_self_info()`
- `runtime/refresh.py::mark_notification_sent()`
- `runtime/refresh.py::mark_delight_sent()`
- `runtime/refresh.py::_publish_precompute_delta_if_available()`
- `runtime/refresh.py` refresh bookkeeping that writes `last_event_refresh_at`, `last_processed_event_id`, `last_trending_refresh_at`, `last_explore_refresh_at`, `last_discovered_count`, `last_replenished_count`, and `recent_pool_topics`
- `runtime/refresh.py::_publish_interest_probe_if_available()`
- `runtime/refresh.py::_publish_avoidance_probe_if_available()`
- `runtime/refresh.py::_publish_probe_if_available()`
- `integrations/openclaw/operations.py::_record_probe_history()`

Pattern:

```python
def _mutate(state: dict[str, object]) -> None:
    probed = dict(state.get("probed_domains", {}))
    probed[domain.lower()] = now.isoformat()
    state["probed_domains"] = probed

memory_manager.update_discovery_runtime_state(_mutate)
```

Do not save an old `state` object after publish.

After conversion, run `rg -n "save_discovery_runtime_state" src/openbiliclaw` again. Acceptable production references are:

- the `MemoryManager` method implementation;
- protocol/interface definitions;
- one-shot full initialization/migration code that does not follow `load -> mutate -> save`.

If any production `load -> mutate -> save_discovery_runtime_state` remains, convert it or add a specific reason and regression test before moving on.

**Step 5: Add API regression for feedback history vs stale push**

In `tests/test_api_app.py`, use a real `MemoryManager(tmp_path)` and real JSON file state. A fake memory manager is acceptable for call-shape tests, but not for the lost-update regression.

```python
def test_interest_probe_respond_history_survives_stale_runtime_save() -> None:
    # 1. respond confirm writes probe_feedback_history
    # 2. call memory.save_discovery_runtime_state(stale_state_without_history)
    # 3. reload memory.load_discovery_runtime_state()
    # 4. assert history still contains confirm
```

Add a non-history lost-update regression:

```python
def test_notification_bookkeeping_does_not_overwrite_probe_runtime_maps(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    memory.update_discovery_runtime_state(
        lambda state: state.setdefault("probed_domains", {}).update({"建筑美学": "2026-06-09T10:00:00"})
    )

    # Simulate a non-probe path that loaded before the probe map update.
    stale["last_notification_at"] = "2026-06-09T10:00:01"
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["last_notification_at"] == "2026-06-09T10:00:01"
    assert state["probed_domains"]["建筑美学"] == "2026-06-09T10:00:00"
```

Add a scalar merge regression:

```python
def test_stale_runtime_save_does_not_revert_last_probe_kind(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    stale = memory.load_discovery_runtime_state()
    stale["last_probe_kind"] = "interest"
    memory.update_discovery_runtime_state(lambda state: state.update({"last_probe_kind": "avoidance"}))

    stale["last_notification_at"] = "2026-06-09T10:00:01"
    memory.save_discovery_runtime_state(stale)

    state = memory.load_discovery_runtime_state()
    assert state["last_probe_kind"] == "avoidance"
```

**Step 6: Add OpenClaw regression**

In `tests/test_openclaw_adapter.py`:

```python
async def test_get_next_probe_history_does_not_drop_feedback_history() -> None:
    memory.runtime_state = {
        "probe_feedback_history": [{"domain": "建筑美学", "response": "reject"}],
        "probed_domains": {},
        "probed_axes": {},
        "probed_distance_bands": {},
    }
    await adapter.get_next_probe()
    assert memory.runtime_state["probe_feedback_history"][0]["domain"] == "建筑美学"
```

Use a fake memory manager exposing `update_discovery_runtime_state`; keep compatibility with current fake helpers.

**Step 7: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_memory_manager.py tests/test_api_app.py tests/test_openclaw_adapter.py -k "discovery_runtime_state or probe_feedback_history or get_next_probe" -q
```

Expected: PASS.

**Step 8: Commit**

```bash
git add src/openbiliclaw/memory/manager.py src/openbiliclaw/api/app.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/integrations/openclaw/operations.py tests/test_memory_manager.py tests/test_api_app.py tests/test_openclaw_adapter.py
git commit -m "fix: preserve probe runtime feedback under concurrent writes"
```

---

### Task 3: Make Interest Speculator Updates Concurrency-Safe

**Files:**
- Modify: `src/openbiliclaw/soul/speculator.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_speculator.py`

**Step 1: Write failing lost-update tests**

Add to `tests/test_speculator.py`:

```python
class _PausingSpeculationLLM:
    def __init__(self, domain: str = "城市基础设施观察") -> None:
        self.domain = domain
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def complete_structured_task(self, **_kwargs: object) -> object:
        self.started.set()
        await self.resume.wait()
        return SimpleNamespace(
            content=json.dumps(
                {
                    "interests": [
                        {
                            "domain": self.domain,
                            "category": "人文",
                            "reason": "用户喜欢系统结构拆解,这个方向提供城市系统视角。",
                            "specifics": ["排水系统", "道路层级"],
                            "confidence": 0.55,
                            "probe_mode": "near",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


async def test_force_tick_does_not_restore_user_confirmed_interest(tmp_path: Path) -> None:
    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="active")]),
    )
    llm = _PausingSpeculationLLM()
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)
    profile = OnionProfile()

    task = asyncio.create_task(speculator.force_tick(profile))
    await llm.started.wait()

    assert speculator.user_confirm_speculation("建筑美学") is True
    llm.resume.set()
    await task

    state = load_speculative_state(tmp_path)
    assert all(
        not (item.domain == "建筑美学" and item.status == "active")
        for item in state.active
    )


async def test_force_tick_does_not_restore_user_rejected_interest(tmp_path: Path) -> None:
    # Same structure, call user_reject_speculation("建筑美学")
    # Assert 建筑美学 not active and cooldown contains 建筑美学.


async def test_force_tick_drops_fresh_candidate_for_just_confirmed_domain(tmp_path: Path) -> None:
    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="active")]),
    )
    llm = _PausingSpeculationLLM(domain="建筑美学")
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()
    assert speculator.user_confirm_speculation("建筑美学") is True
    llm.resume.set()
    await task

    state = load_speculative_state(tmp_path)
    assert all(
        not (item.domain == "建筑美学" and item.status == "active")
        for item in state.active
    )


async def test_force_tick_drops_fresh_candidate_for_rejected_status_in_latest_state(tmp_path: Path) -> None:
    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="rejected")]),
    )
    llm = _PausingSpeculationLLM(domain="建筑美学")
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    await speculator.force_tick(OnionProfile())

    state = load_speculative_state(tmp_path)
    assert all(
        not (item.domain == "建筑美学" and item.status == "active")
        for item in state.active
    )
```

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/test_speculator.py -k "does_not_restore_user" -q
```

Expected: FAIL. Current `force_tick()` saves the old state loaded before the user click.

**Step 3: Add update API**

In `speculator.py`:

```python
def update_speculative_state(
    data_dir: Path,
    mutator: Callable[[SpeculativeState], SpeculativeState | None],
) -> SpeculativeState:
    path = data_dir / "memory" / "speculative_state.json"

    def _mutate(state: SpeculativeState) -> SpeculativeState:
        result = mutator(state)
        return state if result is None else result

    return update_json_state(
        path,
        default_factory=SpeculativeState,
        normalize=lambda raw: SpeculativeState.from_dict(raw) if isinstance(raw, dict) else SpeculativeState(),
        serialize=lambda state: state.to_dict(),
        mutate=_mutate,
    )
```

Keep `save_speculative_state()` for tests and seed writes, but make it use atomic write.

**Step 4: Convert user mutations**

Change these methods to mutate latest state via `update_speculative_state()`:

- `user_confirm_speculation()`
- `user_reject_speculation()`
- `observe()`
- `ingest_seeds()`

Return booleans based on values captured inside the mutator. Use an explicit closure variable; do not infer success from `update_speculative_state()` returning a state object:

```python
def user_confirm_speculation(self, domain: str, ...) -> bool:
    found = False

    def _mutate(state: SpeculativeState) -> None:
        nonlocal found
        for spec in state.active:
            if spec.domain.lower() == domain.lower() and spec.status == "active":
                found = True
                spec.status = "confirmed"
                ...
                break

    update_speculative_state(self._data_dir, _mutate)
    return found
```

Add negative-contract tests:

```python
def test_user_confirm_speculation_returns_false_for_missing_domain(tmp_path: Path) -> None:
    speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)
    assert speculator.user_confirm_speculation("不存在") is False


def test_user_reject_speculation_returns_false_for_missing_domain(tmp_path: Path) -> None:
    speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)
    assert speculator.user_reject_speculation("不存在") is False
```

**Step 5: Split `tick()` / `force_tick()`**

Refactor so neither method saves a stale state loaded before an await.

Recommended shape:

```python
def _prepare_tick_state(self, profile: OnionProfile, now: datetime, *, force: bool) -> tuple[SpeculatorTickResult, bool]:
    # update latest state with expire/promote only
    # return result + should_generate flag

async def _generate_candidates_from_snapshot(...): ...

def _merge_generated_candidates(
    self,
    candidates,
    profile,
    now,
    *,
    feedback_history_loader: Callable[[], object],
) -> list[SpeculativeInterest]:
    # update latest state; re-run novelty guard against latest active/cooldown/profile/latest feedback history
```

The LLM call may use a snapshot of active domains for prompt context, but final append must re-check latest state. The merge-phase filter must drop any candidate whose normalized domain matches:

- any item in latest `state.active` with `status in {"active", "confirmed", "user_rejected", "rejected"}` or any other non-terminal status;
- any latest cooldown entry;
- any profile confirmed like domain or specific;
- any latest `probe_feedback_history` entry for the same domain that represents user-handled interest feedback.

Do not reuse the `feedback_history` object captured before the LLM await. The merge helper must require a concrete callable, never `None`.

Production call sites must provide a real runtime-state loader (for example `lambda: memory.load_discovery_runtime_state().get("probe_feedback_history", [])`) so the merge phase sees feedback written while the LLM was pending. Do not use any closure over the pre-await `feedback_history` snapshot in production. If a production entry point cannot access `MemoryManager`, plumb it through first or skip speculative generation with a debug log; do not run generation with stale feedback history.

Update these call sites to pass the loader while preserving backwards compatibility with old speculator signatures:

- `SoulEngine.build_initial_profile()`
- `ProfileUpdatePipeline._run_speculator_tick()`
- `RuntimeContext._safe_post_reload_speculate()`
- `api/app.py` background `_bg_force_tick()`
- `RefreshRuntime` paths that invoke speculator generation
- `cli.py` direct `force_tick()` command path

Add a loader-invocation test:

```python
async def test_force_tick_uses_feedback_history_loader_during_merge(tmp_path: Path) -> None:
    llm = _PausingSpeculationLLM(domain="建筑美学")
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)
    calls = 0

    def _loader() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"domain": "建筑美学", "response": "confirm"}]

    task = asyncio.create_task(speculator.force_tick(OnionProfile(), feedback_history_loader=_loader))
    await llm.started.wait()
    llm.resume.set()
    await task

    assert calls >= 1
    assert all(item.domain != "建筑美学" for item in load_speculative_state(tmp_path).active)


async def test_force_tick_loader_blocks_duplicate_after_confirmed_item_was_promoted(tmp_path: Path) -> None:
    save_speculative_state(tmp_path, SpeculativeState(active=[]))
    llm = _PausingSpeculationLLM(domain="建筑美学")
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    def _loader() -> list[dict[str, object]]:
        return [{"domain": "建筑美学", "response": "confirm", "created_at": "2026-06-09T10:00:00"}]

    task = asyncio.create_task(speculator.force_tick(OnionProfile(), feedback_history_loader=_loader))
    await llm.started.wait()
    llm.resume.set()
    await task

    assert all(item.domain != "建筑美学" for item in load_speculative_state(tmp_path).active)
```

Add call-site smoke tests where practical: use a fake memory manager whose `load_discovery_runtime_state()` increments a counter, trigger `ProfileUpdatePipeline._run_speculator_tick()` and post-reload/background force-tick paths, and assert the counter is incremented after the LLM resumes.

**Step 6: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_speculator.py -k "does_not_restore_user or user_confirm_speculation_records_source_and_confirmed_at or promote_ready_handles_user_confirmed_status" -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/soul/speculator.py src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/api/app.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/cli.py tests/test_speculator.py
git commit -m "fix: protect interest probe state from stale background saves"
```

---

### Task 4: Make Avoidance Speculator Updates Concurrency-Safe

**Files:**
- Modify: `src/openbiliclaw/soul/avoidance_speculator.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/soul/pipeline.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_avoidance_speculator.py`

**Step 1: Write failing lost-update tests**

Add:

```python
class _PausingAvoidanceLLM:
    def __init__(self, domain: str = "低信息密度热点复读") -> None:
        self.domain = domain
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def complete_structured_task(self, **_kwargs: object) -> object:
        self.started.set()
        await self.resume.wait()
        return SimpleNamespace(
            content=json.dumps(
                {
                    "avoidances": [
                        {
                            "domain": self.domain,
                            "reason": "用户近期对浅层热点表达了排斥。",
                            "source_mode": "negative_signal",
                            "source_signal": "dislike",
                            "specifics": ["标题党热点", "重复观点"],
                            "confidence": 0.55,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


async def test_force_tick_does_not_restore_user_confirmed_avoidance(tmp_path: Path) -> None:
    save_avoidance_state(
        tmp_path,
        AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读", status="active")]),
    )
    llm = _PausingAvoidanceLLM()
    speculator = AvoidanceSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()
    assert speculator.user_confirm_avoidance("浅层热点复读") is not None
    llm.resume.set()
    await task

    state = load_avoidance_state(tmp_path)
    assert all(item.domain != "浅层热点复读" for item in state.active)
```

Add:

- a reject variant asserting cooldown persists;
- a variant where `_PausingAvoidanceLLM(domain="浅层热点复读")` returns a fresh same-domain candidate while the user confirms/rejects, and assert that same domain is not active after `force_tick()` finishes.
- a rejected-status variant where latest `AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读", status="rejected")])` blocks an LLM candidate for the same domain.

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/test_avoidance_speculator.py -k "does_not_restore_user" -q
```

Expected: FAIL.

**Step 3: Add update API**

Add:

```python
def update_avoidance_state(
    data_dir: Path,
    mutator: Callable[[AvoidanceState], AvoidanceState | None],
) -> AvoidanceState:
    path = data_dir / "memory" / "avoidance_state.json"

    def _mutate(state: AvoidanceState) -> AvoidanceState:
        result = mutator(state)
        return state if result is None else result

    return update_json_state(
        path,
        default_factory=AvoidanceState,
        normalize=lambda raw: AvoidanceState.from_dict(raw) if isinstance(raw, dict) else AvoidanceState(),
        serialize=lambda state: state.to_dict(),
        mutate=_mutate,
    )
```

Use the same `update_json_state()` helper and atomic write.

**Step 4: Convert user mutations and tick**

Convert:

- `user_confirm_avoidance()`
- `user_reject_avoidance()`
- `observe()`
- `tick()`
- `force_tick()`

Use the same two-stage pattern as Task 3: no stale state save after awaited LLM calls.

Return contracts must remain explicit:

- `user_confirm_avoidance("missing") is None`
- `user_reject_avoidance("missing") is False`

For generation merge, do not reuse the `feedback_history` object captured before the LLM await. Production call sites must provide a real runtime-state loader for `avoidance_probe_feedback_history`. If a production entry point cannot access `MemoryManager`, plumb it through first or skip speculative generation with a debug log. The merge-phase novelty guard must drop any fresh candidate whose normalized domain/source-topic key matches latest active items with `status in {"active", "confirmed", "user_rejected", "rejected"}` or any other non-terminal status, cooldown entries, confirmed profile dislikes/likes, or latest avoidance feedback history.

Add the avoidance equivalents of `test_force_tick_uses_feedback_history_loader_during_merge()` and `test_force_tick_loader_blocks_duplicate_after_confirmed_item_was_promoted()`.

**Step 5: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_avoidance_speculator.py tests/test_api_app.py -k "avoidance_probe or does_not_restore_user" -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/soul/avoidance_speculator.py src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/pipeline.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/cli.py tests/test_avoidance_speculator.py tests/test_api_app.py
git commit -m "fix: protect avoidance probe state from stale background saves"
```

---

### Task 5: API Regression - Respond Then Pending/Profile Must Not Re-surface

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_refresh_runtime.py`
- No production code expected if Tasks 2-4 are correct.

**Step 1: Add interest API regression**

```python
def test_interest_probe_confirm_disappears_from_profile_and_pending(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="active")]),
    )
    speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)
    app = create_app(memory_manager=memory, database=memory._database, soul_engine=SimpleNamespace(_speculator=speculator))
    app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
    client = TestClient(app)

    response = client.post("/api/interest-probes/respond", json={"domain": "建筑美学", "response": "confirm"})

    assert response.json()["ok"] is True
    assert client.get("/api/interest-probes/pending").json()["items"] == []
    assert client.get("/api/profile-summary").json()["speculative_interests"] == []
```

Add reject variant.

**Step 2: Add avoidance API regression**

Mirror the test with:

- `save_avoidance_state(...)`
- `/api/avoidance-probes/respond`
- `/api/avoidance-probes/pending`
- `speculative_avoidances`

**Step 3: Add proactive-push read-side regression**

In `tests/test_refresh_runtime.py`, add tests next to the existing `_publish_interest_probe_if_available()` cases. Reuse `_FakeEventHub`, `_FakeMemoryManager`, `_FakeSoulEngine`, `_FakeSpeculator`, `_FakeSpeculation`, and `ContinuousRefreshController`. Extend the fake speculation object with a `status` attribute if it does not already have one.

```python
async def test_publish_interest_probe_skips_confirmed_or_rejected_items() -> None:
    event_hub = _FakeEventHub()
    memory = _FakeMemoryManager({"probed_domains": {}, "probed_axes": {}, "probed_distance_bands": {}})

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [
                    _FakeSpeculation(domain="建筑美学", reason="handled", status="confirmed"),
                    _FakeSpeculation(domain="城市基础设施", reason="handled", status="rejected"),
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    delivered = await controller._publish_interest_probe_if_available()

    assert delivered is False
    assert [event for event in event_hub.events if event["type"] == "interest.probe"] == []
```

Add the avoidance equivalent using `_FakeAvoidanceSpeculator` / `_FakeAvoidance` or the existing avoidance fake helper in that file; the fake items must expose `status="confirmed"` and `status="rejected"`.

If `_publish_*_probe_if_available()` currently calls `get_active_*()` that already filters status, keep the test anyway to lock that read-side contract.

**Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_api_app.py tests/test_refresh_runtime.py -k "disappears_from_profile_and_pending or publish_interest_probe_skips_confirmed_or_rejected_items or publish_avoidance_probe_skips_confirmed_or_rejected_items" -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_api_app.py tests/test_refresh_runtime.py
git commit -m "test: cover handled probes disappearing from api surfaces"
```

---

### Task 6: Extension Popup Handled-Key Dedupe And `ok=false`

**Files:**
- Modify: `extension/popup/popup.js`
- Create: `extension/popup/popup-helpers.js`
- Create: `extension/tests/popup-probe-handled.test.ts`
- Modify: `extension/package.json` only if the existing `npm test` script does not pick up the new test file

**Step 1: Extract pure helpers**

Extract pure functions into `extension/popup/popup-helpers.js` unconditionally:

```js
export function probeMessageKey(type, domain) { ... }
export function shouldHydrateProbe({ handledKeys, type, domain, existingDomains }) { ... }
export function shouldDisplayProbeFromWebSocket({ handledKeys, type, domain, existingDomains }) { ... }
export function buildStaleProbeResponseState({ handledKeys, type, domain }) { ... }
```

**Step 2: Write failing tests**

Create `extension/tests/popup-probe-handled.test.ts`:

```js
import assert from "node:assert/strict";
import test from "node:test";
import {
  buildStaleProbeResponseState,
  shouldDisplayProbeFromWebSocket,
  shouldHydrateProbe,
} from "../popup/popup-helpers.js";

test("popup hydration skips handled probe keys", () => {
  assert.equal(shouldHydrateProbe({
    handledKeys: new Set(["interest.probe:建筑美学"]),
    type: "interest.probe",
    domain: "建筑美学",
    existingDomains: new Set(),
  }), false);
});

test("popup websocket skips handled probe keys", () => {
  assert.equal(shouldDisplayProbeFromWebSocket({
    handledKeys: new Set(["interest.probe:建筑美学"]),
    type: "interest.probe",
    domain: "建筑美学",
    existingDomains: new Set(),
  }), false);
});

test("popup stale api response marks handled and requests forced refresh", () => {
  const handledKeys = new Set();
  const state = buildStaleProbeResponseState({ handledKeys, type: "interest.probe", domain: "建筑美学" });
  assert.equal(handledKeys.has("interest.probe:建筑美学"), true);
  assert.equal(state.message, "这条已处理或已过期,正在刷新...");
  assert.equal(state.forceProfileRefresh, true);
});
```

Add avoidance variants and a positive control where the same domain with a different probe type is still allowed.

**Step 3: Add handled-key state**

In `extension/popup/popup.js` state:

```js
handledProbeKeys: new Set(),
```

When the user starts a confirm/reject/chat action, add:

```js
state.handledProbeKeys.add(probeMessageKey(type, domain));
```

This is intentionally optimistic and session-only: it prevents a profile refresh or WebSocket push from flashing the same card while the response is in flight. If the HTTP request throws before reaching the backend, keep the existing visible error/busy behavior; a full popup reload clears the session-only key.

Use it in:

- `addProbeMessage()`
- `hydrateInboxFromSpeculations()`
- WebSocket `interest.probe` / `avoidance.probe` handling

The WebSocket handler must call `shouldDisplayProbeFromWebSocket()` before mutating `state.pendingProbe`, `state.pendingAvoidanceProbe`, or `state.messages`.

**Step 4: Handle `ok=false` everywhere**

Update:

- `handleSpecResponse()`
- `handleProbeResponse()`
- `handleMessageResponse()`

If `apiResp?.ok === false`:

- Do not show success text.
- Show stale text: `这条已处理或已过期,正在刷新...`
- Add handled key.
- Remove local message.
- Force `loadProfileSummary({ force: true })`.

**Step 5: Run extension tests**

Run:

```bash
cd extension && npm test
```

Expected: PASS.

**Step 6: Commit**

```bash
git add extension/popup/popup.js extension/popup/popup-helpers.js extension/tests
git commit -m "fix: prevent handled probes from rehydrating in popup"
```

---

### Task 7: Desktop/Mobile Web Handled-Key Dedupe And Stale Response Sanity

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/js/views/chat.js`
- Modify: `src/openbiliclaw/web/js/views/profile.js`
- Create: `src/openbiliclaw/web/js/views/probe-notification-helpers.js`
- Create: `tests/js/mobile-probe-notification-helpers.test.mjs`
- Modify: `tests/test_mobile_web_view_models.py` only if existing Python coverage needs import/export smoke checks

**Step 1: Audit current behavior**

Desktop Web already has `handledProbeKeys`. Confirm it handles:

- message card response
- speculative row response
- WebSocket probe merge
- profile hydrate

Mobile Web currently handles `ok=false` in `views/chat.js`, but it still needs a session-level handled set so profile/pending refresh and WebSocket pushes cannot re-add a clicked probe.

**Step 2: Extract mobile handled-key helpers and add behavioral Node tests**

Create `src/openbiliclaw/web/js/views/probe-notification-helpers.js`:

```js
export function normalizeProbeType(type) {
  return type === "avoidance.probe" ? "avoidance.probe" : "interest.probe";
}

export function probeKey(type, domain) {
  return `${normalizeProbeType(type)}:${String(domain || "").trim()}`;
}

export const handledProbeKeys = new Set();

export function markHandledProbe(type, domain, handledKeys = handledProbeKeys) {
  const key = probeKey(type, domain);
  if (!key.endsWith(":")) handledKeys.add(key);
}

export function shouldDisplayProbe(type, domain, handledKeys = handledProbeKeys) {
  return Boolean(domain) && !handledKeys.has(probeKey(type, domain));
}

export function mergeProbeNotifications({ handledKeys = handledProbeKeys, pending = [], websocket = [] }) {
  const result = [];
  const seen = new Set();
  for (const item of [...pending, ...websocket]) {
    const type = normalizeProbeType(item?.type);
    const domain = item?.domain;
    const key = probeKey(type, domain);
    if (!shouldDisplayProbe(type, domain, handledKeys) || seen.has(key)) continue;
    result.push({ ...item, type });
    seen.add(key);
  }
  return result;
}

export function filterProfileSpeculations({ handledKeys = handledProbeKeys, speculations = [], type }) {
  return speculations.filter((item) => shouldDisplayProbe(type, item?.domain, handledKeys));
}
```

In `src/openbiliclaw/web/js/views/chat.js`, import these helpers and use the shared `handledProbeKeys` for notification merge and WebSocket handling.

In `src/openbiliclaw/web/js/views/profile.js`, import `filterProfileSpeculations()` and `markHandledProbe()` so speculative interest / avoidance rows are filtered after profile-summary refresh and marked handled when the user clicks a profile row action.

Use `shouldDisplayProbe()` in:

- `loadNotifications()` when merging pending interest / avoidance probes;
- the loop that merges already-received `notifications`;
- `onStreamEvent()` before pushing WebSocket `interest.probe` / `avoidance.probe`;
- action handlers before/after `respondToProbe()` and `respondToAvoidanceProbe()`.
- `profile.js` speculative row render path before rendering `speculative_interests` / `speculative_avoidances`.

When a user clicks confirm/reject/chat, call `markHandledProbe(type, domain)` before removing the local card. If the HTTP request throws before reaching the backend, leave existing error behavior intact; the handled key is session-only and avoids flashing stale data after a user action.

Add `tests/js/mobile-probe-notification-helpers.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import {
  filterProfileSpeculations,
  markHandledProbe,
  mergeProbeNotifications,
} from "../../src/openbiliclaw/web/js/views/probe-notification-helpers.js";

test("mobile probe notifications skip handled pending and websocket probes", () => {
  const handledKeys = new Set();
  markHandledProbe("interest.probe", "建筑美学", handledKeys);

  const merged = mergeProbeNotifications({
    handledKeys,
    pending: [{ type: "interest.probe", domain: "建筑美学" }],
    websocket: [{ type: "interest.probe", domain: "建筑美学" }],
  });

  assert.deepEqual(merged, []);
});

test("mobile handled keys are scoped by probe type", () => {
  const handledKeys = new Set();
  markHandledProbe("interest.probe", "建筑美学", handledKeys);

  const merged = mergeProbeNotifications({
    handledKeys,
    pending: [{ type: "avoidance.probe", domain: "建筑美学" }],
  });

  assert.equal(merged.length, 1);
  assert.equal(merged[0].type, "avoidance.probe");
});

test("mobile profile speculations skip handled probe rows", () => {
  const handledKeys = new Set();
  markHandledProbe("interest.probe", "建筑美学", handledKeys);

  const rows = filterProfileSpeculations({
    handledKeys,
    type: "interest.probe",
    speculations: [{ domain: "建筑美学" }, { domain: "城市基础设施" }],
  });

  assert.deepEqual(rows.map((row) => row.domain), ["城市基础设施"]);
});
```

**Step 3: Add/adjust desktop tests only where a gap exists**

If desktop speculative row ignores `ok=false`, add stale handling in `respondSpeculativeInterest()`:

```js
const resp = await requestJson(endpoint, ...);
if (resp && resp.ok === false) {
  state.handledProbeKeys.add(probeKey(type, domain));
  row.innerHTML = `<p class="spec-result">这条已处理或已过期,正在刷新...</p>`;
  setTimeout(() => { void refreshProfile(); }, 800);
  return;
}
```

Also confirm desktop WebSocket merge drops probes whose `probeKey(type, domain)` is already in `state.handledProbeKeys`; add a helper-level test if a desktop JS test harness exists.

**Step 4: Run focused frontend tests**

Run:

```bash
.venv/bin/pytest tests/test_mobile_web_view_models.py -q
node --experimental-default-type=module --test tests/js/mobile-probe-notification-helpers.test.mjs
```

If desktop JS has node tests, run them too.

**Step 5: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/js/views/chat.js src/openbiliclaw/web/js/views/profile.js src/openbiliclaw/web/js/views/probe-notification-helpers.js tests/js/mobile-probe-notification-helpers.test.mjs tests/test_mobile_web_view_models.py
git commit -m "fix: handle stale probe responses in web ui"
```

---

### Task 8: Package Runtime Regression

**Files:**
- Modify: `tests/test_packaging_entry.py`
- Possibly modify: `packaging/entry.py` only if tests reveal path/lock regression

**Step 1: Add no-path-regression assertion**

Add a test documenting that this fix does not move packaged user data:

```python
def test_packaged_probe_fix_keeps_unified_user_data_root(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "Programs" / "OpenBiliClaw"
    install_dir.mkdir(parents=True)
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(install_dir / "OpenBiliClaw"))

    project_root, bundled = entry._resolve_runtime_paths()

    assert bundled == install_dir
    assert project_root == entry._user_data_root()
```

This may already be covered; if identical coverage exists, do not duplicate. Instead add a short comment in the plan execution notes and skip production changes.

**Step 2: Run packaging tests**

Run:

```bash
.venv/bin/pytest tests/test_packaging_entry.py -q
```

Expected: PASS.

**Step 3: Commit if any test/doc assertion was added**

```bash
git add tests/test_packaging_entry.py
git commit -m "test: lock packaged data root during probe state fix"
```

---

### Task 9: Documentation Updates

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update module docs**

Add concise notes:

- `docs/modules/soul.md`: interest/avoidance probe feedback is now persisted through atomic latest-state updates; background speculator generation cannot restore handled active probes.
- `docs/modules/memory.md`: `MemoryManager.update_discovery_runtime_state()` and atomic JSON update helper.
- `docs/modules/runtime.md`: proactive push writes probe history via update API and does not overwrite feedback history.
- `docs/modules/extension.md`: popup session `handledProbeKeys` and `ok=false` stale response behavior.

**Step 2: Update changelog**

Add under the current top version block:

```markdown
- 修复兴趣/避雷探针点击后重复出现:探针 state 与 runtime feedback history 改为基于最新状态的原子更新,避免安装包后台任务用旧 JSON 快照覆盖用户反馈;插件 popup 同步增加已处理探针兜底去重。
```

**Step 3: Run docs-sensitive checks**

Run:

```bash
.venv/bin/pytest tests/test_install_contract_docs.py -q
```

Expected: PASS.

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/modules/memory.md docs/modules/runtime.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: document durable probe feedback handling"
```

---

### Task 10: Final Verification

**Files:**
- No edits expected.

**Step 1: Run focused backend suites**

Run:

```bash
.venv/bin/pytest tests/test_speculator.py tests/test_avoidance_speculator.py tests/test_memory_manager.py tests/test_api_app.py tests/test_openclaw_adapter.py -q
```

Expected: PASS.

**Step 2: Run static checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
```

Expected: PASS.

**Step 3: Run extension checks**

Run:

```bash
cd extension && npm test
```

Expected: PASS.

**Step 4: Manual sanity**

Start backend and reproduce:

```bash
openbiliclaw start
```

Manual flow:

1. Open extension popup.
2. Confirm an interest probe.
3. Immediately trigger profile refresh and wait for a proactive push interval.
4. Same domain must not reappear.
5. Repeat with reject and with avoidance confirm/reject.

**Step 5: Commit verification-only formatting changes if any**

```bash
git status --short
git add <only-formatting-files-if-any>
git commit -m "style: format probe feedback durability changes"
```

---

## Execution Notes

- The highest-risk part is Task 3/4 two-stage LLM refactor. Do not hold file locks across LLM calls.
- Keep `save_*_state()` available for tests and one-shot seed setup, but business read-modify-write paths must use update APIs.
- If a direct stale full save still exists after Tasks 2-4, add a regression before changing it.
- Avoid broad refactors of probe generation quality gates; only change persistence and merge boundaries.
