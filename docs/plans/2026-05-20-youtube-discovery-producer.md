# YouTube Discovery Producer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move YouTube steady-state discovery out of the inline pool-deficit refresh plan into an independent `YoutubeDiscoveryProducer` with its own loop, min-interval throttle, and per-strategy daily execution budgets.

**Architecture:** Keep `ContentDiscoveryEngine` as the caching/evaluation boundary for YouTube strategy results, but stop scheduling YouTube from `_build_source_replenishment_plan()`. Add a runtime producer that is only ticked when the YouTube platform family is under quota, uses a lightweight SQLite execution ledger for true daily budget accounting, and invokes `yt_search`, `yt_trending`, and `yt_channel` directly through the discovery engine. XHS and Douyin remain unchanged except for refresh-controller parity and docs.

**Tech Stack:** Python dataclasses / asyncio / SQLite / pytest for runtime and producer tests; FastAPI config models and popup vanilla JS for config round-trip; existing `YoutubeSearchStrategy`, `YoutubeTrendingStrategy`, `YoutubeChannelStrategy`, and `YtScraperClient`.

---

## Design Decisions

- **Budget unit:** Use an execution ledger, not `content_cache`, because cache rows miss rejected candidates, duplicate cache conflicts, and failed runs. Budgets are counted per day from actual strategy work:
  - `daily_search_budget`: number of YouTube search queries generated/executed by `yt_search`.
  - `daily_trending_budget`: number of trending raw candidates fetched by `yt_trending`.
  - `daily_channel_budget`: number of subscribed channels selected by `yt_channel`.
- **Config compatibility:** Keep existing field names and defaults. Update docs/UI copy from "per-run size" to "daily execution budget". Add `min_interval_minutes = 60`.
- **Scheduling:** `ContinuousRefreshController` owns source quota checks. `_loop_youtube_producer()` ticks independently, but `_tick_youtube_producer()` only calls the producer when `source_deficit("youtube") > 0`.
- **No task queue:** YouTube discovery stays backend-direct. The existing `yt_tasks` queue remains only for bootstrap profile import through the browser extension.
- **Caching path:** The producer calls `ContentDiscoveryEngine.discover(...)` so YouTube results still get topic normalization, cache persistence, pool-source accounting, and embedding warmup.

---

### Task 1: Add YouTube Runtime Config Field And Round-Trip It

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `tests/test_config.py`
- Test: `tests/test_api_app.py`
- Test: `extension/tests/popup-settings.test.ts`
- Modify: `config.example.toml`

**Step 1: Write failing config tests**

Add coverage in `tests/test_config.py`:

```python
def test_youtube_config_reads_min_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[sources.youtube]
enabled = true
daily_search_budget = 7
daily_trending_budget = 31
daily_channel_budget = 4
request_interval_seconds = 2
min_interval_minutes = 45
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

    cfg = load_config()

    assert cfg.sources.youtube.min_interval_minutes == 45
```

Also add a default assertion:

```python
def test_youtube_config_default_min_interval() -> None:
    assert Config().sources.youtube.min_interval_minutes == 60
```

**Step 2: Write failing API tests**

In the existing config API test file, add or extend a config response/update test:

```python
def test_config_api_round_trips_youtube_min_interval(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["sources"]["youtube"]["min_interval_minutes"] == 60

    update = {
        "sources": {
            "youtube": {
                "enabled": True,
                "daily_search_budget": 8,
                "daily_trending_budget": 40,
                "daily_channel_budget": 5,
                "request_interval_seconds": 2,
                "min_interval_minutes": 30,
            }
        }
    }
    response = client.put("/api/config", json=update)
    assert response.status_code == 200
    assert response.json()["sources"]["youtube"]["min_interval_minutes"] == 30
```

**Step 3: Write failing popup test**

Extend `extension/tests/popup-settings.test.ts` to check both load and save:

```ts
assert.match(
  popupJs,
  /setVal\("cfgYoutubeMinInterval", cfg\.sources\?\.youtube\?\.min_interval_minutes\)/,
);
assert.match(popupJs, /min_interval_minutes: getInt\("cfgYoutubeMinInterval", 60\)/);
```

**Step 4: Run targeted tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_config.py -k youtube_config -q
uv run --extra dev python -m pytest tests/test_api_app.py -k youtube -q
cd extension && npm test -- popup-settings
```

Expected: fail because `min_interval_minutes` is not defined or not serialized.

**Step 5: Implement config dataclass and loader**

In `YoutubeSourceConfig`, add:

```python
min_interval_minutes: int = 60
```

In `_build_config()`, when constructing `YoutubeSourceConfig`, add:

```python
min_interval_minutes=max(0, int(youtube_raw.get("min_interval_minutes", 60))),
```

If the file already has a config-render helper, include `min_interval_minutes` under `[sources.youtube]`.

**Step 6: Implement API output/update**

In `YoutubeSourceConfigOut`, add:

```python
min_interval_minutes: int = 60
```

In `_config_to_response()`, pass:

```python
min_interval_minutes=cfg.sources.youtube.min_interval_minutes,
```

In the config update path, include the new key:

```python
for key in (
    "daily_search_budget",
    "daily_trending_budget",
    "daily_channel_budget",
    "request_interval_seconds",
    "min_interval_minutes",
):
    if key in yt_data:
        setattr(cfg.sources.youtube, key, int(yt_data[key]))
```

**Step 7: Implement popup control**

Add a compact numeric input beside the existing YouTube source settings:

```html
<label for="cfgYoutubeMinInterval">YouTube 最小调度间隔（分钟）</label>
<input id="cfgYoutubeMinInterval" type="number" min="0" step="1" />
```

In `popup.js`, load:

```js
setVal("cfgYoutubeMinInterval", cfg.sources?.youtube?.min_interval_minutes);
```

and save:

```js
min_interval_minutes: getInt("cfgYoutubeMinInterval", 60),
```

**Step 8: Update example config comments**

In `config.example.toml`, change the YouTube comments to say YouTube now has an independent backend producer loop, and add:

```toml
# YouTube producer 两次执行之间的最小间隔；0 表示每个 refresh tick 都允许检查执行。
min_interval_minutes = 60
```

**Step 9: Run tests**

```bash
uv run --extra dev python -m pytest tests/test_config.py tests/test_api_app.py -k "youtube or config" -q
cd extension && npm test -- popup-settings
```

Expected: pass.

**Step 10: Commit**

```bash
git add src/openbiliclaw/config.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py extension/popup/popup.html extension/popup/popup.js extension/tests/popup-settings.test.ts config.example.toml tests/test_config.py tests/test_api_app.py
git commit -m "feat: add youtube producer interval config"
```

---

### Task 2: Add YouTube Producer Unit With Daily Execution Ledger

**Files:**
- Create: `src/openbiliclaw/runtime/youtube_producer.py`
- Test: `tests/test_youtube_producer.py`

**Step 1: Write failing producer tests**

Create `tests/test_youtube_producer.py` with tests for the contract:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from openbiliclaw.runtime.youtube_producer import (
    YoutubeDiscoveryProducer,
    YoutubeStrategyRunResult,
)
from openbiliclaw.storage.database import Database


class _Soul:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


@dataclass
class _Discover:
    calls: list[tuple[str, int, int]]

    async def __call__(
        self,
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        self.calls.append((strategy, unit_budget, result_limit))
        return YoutubeStrategyRunResult(
            items=[object()] * min(2, result_limit),
            units_used=unit_budget,
            source_counts={strategy: min(2, result_limit)},
        )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "yt-producer.db")
    database.initialize()
    return database
```

Add these tests:

```python
@pytest.mark.asyncio
async def test_youtube_producer_produces_when_due(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_search_budget=3,
        daily_trending_budget=5,
        daily_channel_budget=2,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "ok"
    assert result["discovered"] == 6
    assert discover.calls == [
        ("yt_search", 3, 4),
        ("yt_trending", 5, 4),
        ("yt_channel", 2, 4),
    ]
```

```python
@pytest.mark.asyncio
async def test_youtube_producer_throttles_recent_run(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=60,
    )
    producer._last_run_at = datetime.now(UTC) - timedelta(minutes=5)

    assert await producer.produce_if_due(limit=5) == {"discovered": 0, "reason": "throttled"}
    assert discover.calls == []
```

```python
@pytest.mark.asyncio
async def test_youtube_producer_skips_when_daily_budget_exhausted(db: Database) -> None:
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=_Discover([]),
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        daily_channel_budget=1,
    )
    producer.record_strategy_run("yt_search", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_trending", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=1, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "budget_exhausted"}
```

Also cover:

- `enabled=False` returns `{"discovered": 0, "reason": "disabled"}`
- `soul_engine.get_profile()` returns `None` or raises: `reason == "no_profile"`
- partial remaining budgets only call strategies with remaining units
- `min_interval_minutes=0` always due
- one failing strategy records/returns `"error"` only when all strategies fail; one failure plus one success still returns `"ok"`

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_youtube_producer.py -q
```

Expected: import failure because `runtime.youtube_producer` does not exist.

**Step 3: Implement producer module**

Create `src/openbiliclaw/runtime/youtube_producer.py`:

```python
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

YOUTUBE_STRATEGY_BUDGET_FIELDS = {
    "yt_search": "daily_search_budget",
    "yt_trending": "daily_trending_budget",
    "yt_channel": "daily_channel_budget",
}


@dataclass(frozen=True)
class YoutubeStrategyRunResult:
    items: list[Any]
    units_used: int
    source_counts: dict[str, int]


YoutubeDiscoverCallable = Callable[
    [Any],
    Awaitable[YoutubeStrategyRunResult],
]
```

Use a callable signature with keyword-only arguments:

```python
YoutubeDiscoverCallable = Callable[..., Awaitable[YoutubeStrategyRunResult]]
```

Then implement:

```python
@dataclass
class YoutubeDiscoveryProducer:
    database: Any
    soul_engine: Any
    discover: YoutubeDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 60
    daily_search_budget: int = 6
    daily_trending_budget: int = 50
    daily_channel_budget: int = 10
    strategies: tuple[str, ...] = ("yt_search", "yt_trending", "yt_channel")
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("youtube producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        remaining = self.remaining_budgets()
        runnable = [
            strategy
            for strategy in self.strategies
            if int(remaining.get(strategy, 0)) > 0
        ]
        if not runnable:
            return self._skip("budget_exhausted")

        requested_limit = max(1, int(limit or 10))
        discovered_total = 0
        source_counts: Counter[str] = Counter()
        errors = 0

        for strategy in runnable:
            unit_budget = max(0, int(remaining[strategy]))
            if unit_budget <= 0:
                continue
            try:
                result = await self.discover(
                    profile,
                    strategy=strategy,
                    unit_budget=unit_budget,
                    result_limit=requested_limit,
                )
            except Exception as exc:
                errors += 1
                logger.warning("youtube producer strategy failed: strategy=%s error=%s", strategy, exc)
                self.record_strategy_run(strategy, units_used=0, discovered=0, reason="error")
                continue

            units_used = max(0, min(unit_budget, int(result.units_used)))
            discovered = len(result.items)
            self.record_strategy_run(strategy, units_used=units_used, discovered=discovered, reason="ok")
            discovered_total += discovered
            source_counts.update(result.source_counts)

        self._last_run_at = datetime.now(UTC)
        if discovered_total <= 0 and errors >= len(runnable):
            return {"discovered": 0, "reason": "error"}
        return {
            "discovered": discovered_total,
            "source_counts": dict(source_counts),
            "reason": "ok",
        }
```

Implement helpers:

```python
def _ensure_ledger_table(self) -> None:
    self.database.conn.executescript("""
        CREATE TABLE IF NOT EXISTS youtube_discovery_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            units INTEGER NOT NULL DEFAULT 0,
            discovered INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT 'ok',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_youtube_discovery_runs_strategy_created
            ON youtube_discovery_runs(strategy, created_at);
    """)
    self.database.conn.commit()
```

```python
def consumed_today(self, strategy: str) -> int:
    self._ensure_ledger_table()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row = self.database.conn.execute(
        """
        SELECT COALESCE(SUM(units), 0)
        FROM youtube_discovery_runs
        WHERE strategy = ? AND created_at >= ? AND reason = 'ok'
        """,
        (strategy, today),
    ).fetchone()
    return int(row[0] if row else 0)
```

```python
def remaining_budgets(self) -> dict[str, int]:
    configured = {
        "yt_search": max(0, int(self.daily_search_budget)),
        "yt_trending": max(0, int(self.daily_trending_budget)),
        "yt_channel": max(0, int(self.daily_channel_budget)),
    }
    return {
        strategy: max(0, budget - self.consumed_today(strategy))
        for strategy, budget in configured.items()
    }
```

```python
def record_strategy_run(self, strategy: str, *, units_used: int, discovered: int, reason: str) -> None:
    self._ensure_ledger_table()
    self.database.conn.execute(
        """
        INSERT INTO youtube_discovery_runs(strategy, units, discovered, reason)
        VALUES (?, ?, ?, ?)
        """,
        (strategy, max(0, int(units_used)), max(0, int(discovered)), reason),
    )
    self.database.conn.commit()
```

```python
def _is_due(self) -> bool:
    if self.min_interval_minutes <= 0:
        return True
    if self._last_run_at is None:
        return True
    return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)
```

```python
def _skip(self, reason: str) -> dict[str, object]:
    if reason != self._last_skip_reason:
        logger.info("youtube producer skip: reason=%s", reason)
    self._last_skip_reason = reason
    return {"discovered": 0, "reason": reason}
```

**Step 4: Run producer tests**

```bash
uv run --extra dev python -m pytest tests/test_youtube_producer.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/youtube_producer.py tests/test_youtube_producer.py
git commit -m "feat: add youtube discovery producer"
```

---

### Task 3: Add YouTube Producer Factory And Strategy Invocation

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_youtube_discovery_strategy.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write failing factory tests**

Extend `tests/test_youtube_discovery_strategy.py`:

```python
def test_build_youtube_discovery_producer_uses_source_config(tmp_path: Path) -> None:
    from openbiliclaw.api.runtime_context import build_youtube_discovery_producer
    from openbiliclaw.config import Config
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "yt.db")
    db.initialize()
    config = Config()
    config.sources.youtube.enabled = True
    config.scheduler.enabled = True
    config.sources.youtube.min_interval_minutes = 42
    config.sources.youtube.daily_search_budget = 4
    config.sources.youtube.daily_trending_budget = 37
    config.sources.youtube.daily_channel_budget = 6

    producer = build_youtube_discovery_producer(
        config=config,
        database=db,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        llm_service=_FakeLLMService("{}"),
        memory=_MemoryWithYoutubeUrls(),
        concurrency=None,
    )

    assert producer is not None
    assert producer.min_interval_minutes == 42
    assert producer.daily_search_budget == 4
    assert producer.daily_trending_budget == 37
    assert producer.daily_channel_budget == 6
```

Add tests that the factory returns `None` when:

- `sources.youtube.enabled` is false
- `scheduler.enabled` is false
- `scrapetube`/`yt_dlp` import raises `ImportError`

For the dependency-unavailable test, monkeypatch the factory helper that imports `YtScraperClient` so it raises `ImportError("missing yt deps")`, then assert the factory returns `None` and logs a skip message.

**Step 2: Run factory tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_youtube_discovery_strategy.py -k youtube_discovery_producer -q
```

Expected: fail because `build_youtube_discovery_producer` does not exist.

**Step 3: Implement `build_youtube_discovery_producer()`**

In `src/openbiliclaw/api/runtime_context.py`, add a factory near `build_youtube_discovery_strategies()`:

```python
def build_youtube_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
    llm_service: Any,
    memory: Any,
    concurrency: Any,
) -> Any | None:
    yt_cfg = getattr(getattr(config, "sources", None), "youtube", None)
    scheduler = getattr(config, "scheduler", None)
    if yt_cfg is None or not bool(getattr(yt_cfg, "enabled", False)):
        return None
    if not bool(getattr(scheduler, "enabled", True)):
        return None
    if not hasattr(database, "conn"):
        logger.info("youtube producer disabled: database does not expose sqlite connection")
        return None

    from openbiliclaw.runtime.youtube_producer import (
        YoutubeDiscoveryProducer,
        YoutubeStrategyRunResult,
    )
    from openbiliclaw.youtube.client import YtScraperClient

    client = YtScraperClient()
```

Implement the strategy run callable:

```python
    async def _discover(
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        strategies = build_youtube_discovery_strategies(
            config=config,
            client=client,
            llm_service=llm_service,
            memory=memory,
            concurrency=concurrency,
            strategy_unit_budget={strategy: unit_budget},
        )
        selected = [item for item in strategies if item.name == strategy]
        if not selected:
            return YoutubeStrategyRunResult(items=[], units_used=0, source_counts={})
        for item in selected:
            discovery_engine.register_strategy(item)
        items = await discovery_engine.discover(
            profile,
            strategies=[strategy],
            limit=max(1, int(result_limit)),
        )
        selected_strategy = selected[0]
        units_used = _youtube_strategy_units_used(selected_strategy, fallback=unit_budget)
        return YoutubeStrategyRunResult(
            items=items,
            units_used=units_used,
            source_counts={strategy: len(items)},
        )
```

Return:

```python
    return YoutubeDiscoveryProducer(
        database=database,
        soul_engine=soul_engine,
        discover=_discover,
        enabled=True,
        min_interval_minutes=int(getattr(yt_cfg, "min_interval_minutes", 60)),
        daily_search_budget=int(getattr(yt_cfg, "daily_search_budget", 6)),
        daily_trending_budget=int(getattr(yt_cfg, "daily_trending_budget", 50)),
        daily_channel_budget=int(getattr(yt_cfg, "daily_channel_budget", 10)),
    )
```

**Step 4: Extend strategy builder for unit caps**

Change `build_youtube_discovery_strategies()` signature:

```python
def build_youtube_discovery_strategies(
    *,
    config: Any,
    client: Any,
    llm_service: Any,
    memory: Any,
    concurrency: Any,
    strategy_unit_budget: dict[str, int] | None = None,
) -> list[Any]:
```

Use:

```python
budgets = strategy_unit_budget or {}
search_budget = int(budgets.get("yt_search", getattr(yt_cfg, "daily_search_budget", 6)))
trending_budget = int(budgets.get("yt_trending", getattr(yt_cfg, "daily_trending_budget", 50)))
channel_budget = int(budgets.get("yt_channel", getattr(yt_cfg, "daily_channel_budget", 10)))
```

Then wire:

```python
queries_per_run=max(0, search_budget)
fetch_limit=max(0, trending_budget)
max_channels=max(0, channel_budget)
```

If a budget is zero, the producer should not call that strategy. The builder can still clamp to zero for tests, but producer prevents zero calls.

**Step 5: Implement unit extraction helper**

Add:

```python
def _youtube_strategy_units_used(strategy: Any, *, fallback: int) -> int:
    name = str(getattr(strategy, "name", ""))
    intermediates = getattr(strategy, "last_intermediates", {}) or {}
    if name == "yt_search":
        queries = intermediates.get("queries")
        if isinstance(queries, list):
            return len(queries)
    if name == "yt_trending":
        fetched = intermediates.get("fetched")
        if isinstance(fetched, int):
            return fetched
    if name == "yt_channel":
        channel_ids = intermediates.get("channel_ids")
        if isinstance(channel_ids, list):
            return len(channel_ids)
    return max(0, int(fallback))
```

**Step 6: Wire RuntimeContext**

In `_rebuild_components()`:

- Stop unconditional YouTube strategy registration for runtime scheduling.
- Keep `build_youtube_discovery_strategies()` available for tests/manual discovery if needed.
- Add `new_youtube_producer: Any = None`.
- Inside the `hasattr(self.database, "conn")` block, build:

```python
new_youtube_producer = build_youtube_discovery_producer(
    config=new_config,
    database=self.database,
    soul_engine=new_soul_engine,
    discovery_engine=new_discovery_engine,
    llm_service=new_llm_service,
    memory=cast("Any", self.memory_manager),
    concurrency=concurrency,
)
```

- Pass `youtube_producer=new_youtube_producer` into `ContinuousRefreshController`.

**Step 7: Wire OpenClaw bootstrap**

In `src/openbiliclaw/integrations/openclaw/bootstrap.py`:

- Import/reuse `build_youtube_discovery_producer` from runtime context.
- Build the producer after `douyin_producer`.
- Pass it into `ContinuousRefreshController`.

Use the same `llm_service`, `memory_manager`, and `concurrency` already built in bootstrap.

**Step 8: Run factory/OpenClaw tests**

```bash
uv run --extra dev python -m pytest tests/test_youtube_discovery_strategy.py tests/test_openclaw_adapter.py -k "youtube or openclaw" -q
```

Expected: pass.

**Step 9: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_youtube_discovery_strategy.py tests/test_openclaw_adapter.py
git commit -m "feat: build youtube discovery producer"
```

---

### Task 4: Add Refresh Controller Loop And Remove Inline YouTube Planning

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Step 1: Write failing loop/gate tests**

In `tests/test_refresh_runtime.py`, update `_LOOP_BODY_ATTRS`:

```python
("_loop_youtube_producer", ("_tick_youtube_producer",)),
```

Add fake producer:

```python
class _FakeYoutubeProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"discovered": 0, "reason": "ok"}
```

Add tests:

```python
async def test_youtube_producer_runs_when_youtube_under_quota() -> None:
    producer = _FakeYoutubeProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "xiaohongshu": 0, "douyin": 0, "youtube": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        discovery_limit=30,
        youtube_producer=producer,
    )

    await controller._tick_youtube_producer()

    assert producer.calls == [30]
```

```python
async def test_youtube_producer_skips_when_youtube_at_quota() -> None:
    producer = _FakeYoutubeProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=600, source_counts={"bilibili": 480, "youtube": 120}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        youtube_producer=producer,
    )

    await controller._tick_youtube_producer()

    assert producer.calls == []
```

Replace `test_source_replenishment_plan_maps_youtube_deficit_to_youtube_strategies` with:

```python
def test_source_replenishment_plan_leaves_youtube_deficit_to_youtube_producer() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=80, source_counts={"bilibili": 80, "youtube": 0}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "youtube": 2},
    )

    assert controller._build_source_replenishment_plan() == []
```

Add a stranded-source warning test using `caplog`:

```python
def test_warn_on_stranded_source_shares_checks_youtube_producer(caplog: pytest.LogCaptureFixture) -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=80, source_counts={"bilibili": 80, "youtube": 0}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        youtube_producer=None,
    )

    controller._warn_on_stranded_source_shares()

    assert "youtube" in caplog.text
```

**Step 2: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_refresh_runtime.py -k "youtube_producer or source_replenishment_plan or stranded or scheduler_disabled" -q
```

Expected: fail because the controller has no YouTube producer field/loop yet and still plans inline YouTube strategies.

**Step 3: Add controller field**

In `ContinuousRefreshController`, add:

```python
youtube_producer: Any | None = None
```

**Step 4: Add loop to `run_forever()`**

Update the architecture comment and task list:

```python
asyncio.create_task(self._loop_youtube_producer()),
```

Add the loop:

```python
async def _loop_youtube_producer(self) -> None:
    """YouTube production — backend-direct discovery when YouTube is below quota."""
    while True:
        if not self._llm_work_allowed():
            await asyncio.sleep(self.check_interval_seconds)
            continue
        with suppress(Exception):
            await self._tick_youtube_producer()
        await asyncio.sleep(self.check_interval_seconds)
```

**Step 5: Add tick helper**

```python
async def _tick_youtube_producer(self) -> None:
    """Invoke the YouTube discovery producer if YouTube is under quota."""
    producer = self.youtube_producer
    if producer is None:
        return
    if not self._is_initialized():
        return
    deficit = self._source_deficit("youtube")
    if deficit <= 0:
        return
    produce_fn = getattr(producer, "produce_if_due", None)
    if not callable(produce_fn):
        return
    limit = max(1, min(deficit, self.discovery_limit))
    if _call_accepts_limit(produce_fn):
        await produce_fn(limit=limit)
    else:
        await produce_fn()
```

**Step 6: Remove inline YouTube replenishment**

In `_build_source_replenishment_plan()`, delete:

```python
elif source == "youtube":
    plan.append((list(_YOUTUBE_DISCOVERY_SOURCES), deficit))
```

Keep `_YOUTUBE_DISCOVERY_SOURCES` if it is still used for diagnostics/tests; otherwise remove it.

**Step 7: Update stranded warning**

Change:

```python
elif source == "youtube" and not self._has_registered_discovery_sources(_YOUTUBE_DISCOVERY_SOURCES):
    stranded.append("youtube")
```

to:

```python
elif source == "youtube" and self.youtube_producer is None:
    stranded.append("youtube")
```

Remove `_has_registered_discovery_sources()` if no other code uses it.

**Step 8: Run refresh tests**

```bash
uv run --extra dev python -m pytest tests/test_refresh_runtime.py -q
```

Expected: pass.

**Step 9: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "feat: run youtube discovery producer loop"
```

---

### Task 5: Ensure YouTube Producer Results Become Ready Pool Candidates

**Files:**
- Test: `tests/test_youtube_producer.py`
- Test: `tests/test_youtube_discovery_strategy.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/runtime/youtube_producer.py`

**Step 1: Write failing integration-ish test for strategy unit accounting**

In `tests/test_youtube_discovery_strategy.py`, add:

```python
def test_youtube_strategy_units_used_reads_intermediates() -> None:
    from openbiliclaw.api.runtime_context import _youtube_strategy_units_used
    from openbiliclaw.discovery.strategies.youtube import YoutubeTrendingStrategy

    strategy = YoutubeSearchStrategy(
        client=_FakeYtClient(),
        llm_service=_FakeLLMService("{}"),
        queries_per_run=3,
    )
    strategy.last_intermediates = {"queries": ["a", "b"]}
    assert _youtube_strategy_units_used(strategy, fallback=3) == 2

    trending = YoutubeTrendingStrategy(
        client=_FakeYtClient(),
        llm_service=_FakeLLMService("{}"),
        fetch_limit=50,
    )
    trending.last_intermediates = {"fetched": 12}
    assert _youtube_strategy_units_used(trending, fallback=50) == 12

    channel = YoutubeChannelStrategy(
        client=_FakeYtClient(),
        llm_service=_FakeLLMService("{}"),
        memory=_MemoryWithYoutubeUrls(),
        max_channels=10,
    )
    channel.last_intermediates = {"channel_ids": ["UC1", "UC2"]}
    assert _youtube_strategy_units_used(channel, fallback=10) == 2
```

Use existing fakes from the test file; avoid real YouTube calls.

**Step 2: Write failing test for result count/source count**

Use a fake discovery engine whose `discover()` returns `DiscoveredContent(source_platform="youtube", source_strategy=strategy)` and assert the producer result includes source counts and the ledger rows.

**Step 3: Run tests to verify failure**

```bash
uv run --extra dev python -m pytest tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py -k "units_used or source_counts or ledger" -q
```

Expected: fail until unit extraction and factory callable are complete.

**Step 4: Implement/fix callable details**

Make sure the factory callable:

- builds one capped strategy per call;
- registers that strategy before `discovery_engine.discover()`;
- passes `strategies=[strategy]`;
- returns `YoutubeStrategyRunResult(items=items, units_used=actual_units, source_counts={strategy: len(items)})`.

If `discovery_engine.discover()` returns non-YouTube backfill rows, filter:

```python
items = [
    item
    for item in raw_items
    if getattr(item, "source_platform", "") == "youtube"
       or str(getattr(item, "source_strategy", "")).startswith("yt_")
]
```

This mirrors the Douyin service's `_douyin_items()` guard.

**Step 5: Run targeted tests**

```bash
uv run --extra dev python -m pytest tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/runtime/youtube_producer.py tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py
git commit -m "test: cover youtube producer budget accounting"
```

---

### Task 6: Update Documentation And Architecture Diagrams

**Files:**
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/youtube.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`
- Optional Modify: `docs/index.md`

**Step 1: Update module docs**

In `docs/modules/runtime.md`:

- Add `YoutubeDiscoveryProducer` to implemented runtime features.
- Update background loop list to include `_loop_youtube_producer()`.
- State it is backend-direct, unlike XHS/Douyin extension task producers.
- Document the shared background LLM gate covers the YouTube loop.

In `docs/modules/youtube.md`:

- Add producer to "已实现功能":

```markdown
| 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立调度 `yt_search` / `yt_trending` / `yt_channel`，按 `min_interval_minutes` 与每日执行 ledger 控制频率和预算 |
```

- Add public API snippet:

```python
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

- Clarify `yt_tasks` remains bootstrap-only.

In `docs/modules/config.md`:

- Change YouTube budget descriptions from "per runtime run" to "daily execution budget".
- Add `min_interval_minutes`.
- Update pool-source paragraph to say YouTube deficit is handled by `YoutubeDiscoveryProducer`, not inline strategy scheduling.

In `docs/modules/discovery.md`:

- Update multi-source discovery notes: Bilibili inline; XHS task producer; Douyin producer; YouTube backend-direct producer.

**Step 2: Update architecture docs**

In `docs/architecture.md` and `docs/spec.md`:

- Update runtime loop diagram to include YouTube producer.
- Remove wording that says YouTube discovery lives only inside main refresh replenishment.
- Keep YouTube bootstrap task bridge in the extension/bootstrap section.

In `README.md` and `README_EN.md` top architecture diagrams:

- Add "YouTube producer" or "backend-direct YouTube discovery loop" in the runtime source layer.

**Step 3: Update changelog**

At top of `docs/changelog.md`, add a current unreleased/current version bullet:

```markdown
- Runtime: YouTube steady-state discovery now runs through an independent backend producer loop with per-strategy daily execution budgets and source-deficit gating.
```

Follow the existing changelog version style.

**Step 4: Verify docs references**

```bash
rg -n "YouTube discovery|yt_search|yt_trending|yt_channel|youtube producer|plugin producer|inline" docs README.md README_EN.md config.example.toml
```

Expected: no stale statement says YouTube has no independent producer or is only inline in the main refresh loop.

**Step 5: Commit**

```bash
git add docs/modules/runtime.md docs/modules/youtube.md docs/modules/config.md docs/modules/discovery.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md docs/index.md
git commit -m "docs: document youtube discovery producer"
```

---

### Task 7: Full Verification

**Files:**
- No code changes unless verification finds issues.

**Step 1: Run targeted Python tests**

```bash
uv run --extra dev python -m pytest tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py tests/test_refresh_runtime.py tests/test_config.py tests/test_api_app.py tests/test_openclaw_adapter.py -q
```

Expected: pass.

**Step 2: Run extension settings test**

```bash
cd extension && npm test -- popup-settings
```

Expected: pass.

**Step 3: Run lint/type checks**

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
```

Expected: pass.

**Step 4: Run full test suite if time allows**

```bash
uv run --extra dev python -m pytest
```

Expected: pass.

**Step 5: Manual smoke check**

With `[sources.youtube].enabled=true`, a ready soul profile, and YouTube scraper deps installed:

```bash
openbiliclaw start
```

Watch logs for:

- `youtube producer skip: reason=throttled` after a successful run inside the min interval.
- `youtube producer skip: reason=budget_exhausted` when all three daily budgets are spent.
- No `refresh.strategy` event for `yt_search+yt_trending+yt_channel`; YouTube should not be scheduled by `_run_refresh_plan()`.

**Step 6: Final status**

Run:

```bash
git status --short
```

Expected: only intentional changes are present.

---

## Execution Notes

- Do not count daily budget from `content_cache`; that would only count surviving cache rows and would undercount actual YouTube work.
- Keep Bilibili refresh behavior unchanged. This work only removes YouTube from the inline source replenishment plan.
- Keep YouTube bootstrap privacy/opt-in behavior unchanged. This producer only affects steady-state discovery after `[sources.youtube].enabled=true`.
- If a test double lacks `database.conn`, producer factories should return `None` instead of failing construction.
- If YouTube scraper dependencies are unavailable, source policy may still include YouTube when enabled; the controller should warn that YouTube quota is stranded because `youtube_producer` is absent.
