"""Tests for the runtime X (Twitter) discovery producer.

The X producer is **fetch-only**: it generates soul-driven search keyword(s),
runs the three server-side strategies (search / For-You / creator), and
enqueues the resulting :class:`DiscoveredContent` into the ``discovery_candidates``
pending pool. It NEVER evaluates or writes ``content_cache`` — the shared
mixed-source evaluator owns that downstream (unified-pool spec).

Constraints exercised here:

* ``enabled=false`` → ``produce_if_due`` is a no-op and never imports
  ``twitter_cli``.
* ``enabled`` + due → claimable ``discovery_candidates`` rows appear with
  ``source_platform="twitter"`` and the right ``content_type`` / ``body_text``.
* ``content_cache`` stays empty and no evaluator is invoked.
* For-You is throttled to a low daily cadence; budgets / intervals respected.

All tests run offline with a fake XAdapter (no network, no real cookie).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.x_producer import XDiscoveryProducer
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.sources.x_tasks import XCreatorStore
from openbiliclaw.storage.database import Database
from openbiliclaw.storage.x_health import XSourceHealthStore

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "x_producer.db")
    db.initialize()
    return db


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="rust async", category="科技", weight=0.9)]
        )
    )


@dataclass
class _FakeSoulEngine:
    profile: SoulProfile | None = field(default_factory=_profile)

    async def get_profile(self) -> SoulProfile | None:
        return self.profile

    def is_profile_ready(self) -> bool:
        return self.profile is not None


def _tweet_item(
    content_id: str, strategy: str, *, content_type: str = "tweet"
) -> DiscoveredContent:
    return DiscoveredContent(
        title="A thread on systems",
        content_id=content_id,
        content_url=f"https://x.com/handle/status/{content_id}",
        source_platform="twitter",
        source_strategy=strategy,
        author_name="@handle",
        content_type=content_type,
        body_text=f"1/ long-form body for {content_id}",
    )


@dataclass
class _FakeXAdapter:
    """Records every fetch() and returns canned twitter DiscoveredContent."""

    by_strategy: dict[str, list[DiscoveredContent]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any], int]] = field(default_factory=list)
    source_type: str = "twitter"

    async def fetch(self, recipe: Any, profile: Any, limit: int = 20) -> list[DiscoveredContent]:
        config = recipe.config if isinstance(recipe.config, dict) else {}
        self.calls.append((recipe.strategy, dict(config), limit))
        return list(self.by_strategy.get(recipe.strategy, []))


def _count_content_cache(db: Database) -> int:
    row = db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()
    return int(row[0])


def _claim_all(db: Database) -> list[dict[str, Any]]:
    return db.claim_discovery_candidates_for_eval(limit=100)


# ── disabled path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_producer_is_noop_and_never_imports_twitter_cli(tmp_path: Path) -> None:
    sys.modules.pop("twitter_cli", None)
    db = _db(tmp_path)
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=_FakeXAdapter(by_strategy={"search": [_tweet_item("1", "x-search")]}),
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=False,
    )

    result = await producer.produce_if_due(limit=10)

    assert result["reason"] == "disabled"
    assert result["enqueued"] == 0
    assert _claim_all(db) == []
    assert "twitter_cli" not in sys.modules  # disabled path must stay lazy


# ── enabled + due → enqueue into the pending pool ────────────────────


@pytest.mark.asyncio
async def test_enabled_producer_enqueues_claimable_candidates(tmp_path: Path) -> None:
    db = _db(tmp_path)
    adapter = _FakeXAdapter(
        by_strategy={
            "search": [_tweet_item("1790000000000000001", "x-search", content_type="thread")],
        }
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=10)

    assert result["reason"] == "ok"
    assert int(result["enqueued"]) >= 1
    rows = _claim_all(db)
    assert rows, "expected claimable discovery_candidates rows"
    twitter_rows = [r for r in rows if r["source_platform"] == "twitter"]
    assert twitter_rows
    target = next(r for r in twitter_rows if r["content_id"] == "1790000000000000001")
    assert target["content_type"] == "thread"
    assert str(target["body_text"]).startswith("1/ long-form")


@pytest.mark.asyncio
async def test_producer_does_not_touch_content_cache_or_evaluate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    adapter = _FakeXAdapter(
        by_strategy={"search": [_tweet_item("2790000000000000001", "x-search")]}
    )

    # A discovery engine whose evaluator MUST NOT be called (fetch-only contract).
    class _ExplodingEngine:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"producer invoked evaluator/engine: {name}")

    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        discovery_engine=_ExplodingEngine(),
    )

    await producer.produce_if_due(limit=10)

    assert _count_content_cache(db) == 0  # fetch-only: nothing admitted to content_cache


# ── For-You throttling ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_for_you_runs_when_feed_due(tmp_path: Path) -> None:
    db = _db(tmp_path)
    adapter = _FakeXAdapter(
        by_strategy={
            "search": [],
            "feed": [_tweet_item("3790000000000000001", "x-feed")],
        }
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        daily_feed_budget=2,
    )

    await producer.produce_if_due(limit=10)

    feed_calls = [c for c in adapter.calls if c[0] == "feed"]
    assert feed_calls, "For-You should run on the first due cycle"


@pytest.mark.asyncio
async def test_for_you_throttled_by_daily_budget(tmp_path: Path) -> None:
    db = _db(tmp_path)
    adapter = _FakeXAdapter(
        by_strategy={
            "search": [],
            "feed": [_tweet_item("4790000000000000001", "x-feed")],
        }
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        daily_feed_budget=1,
    )

    # First run consumes the only For-You unit for the day.
    await producer.produce_if_due(limit=10)
    feed_calls_after_first = len([c for c in adapter.calls if c[0] == "feed"])
    # Second run: For-You budget exhausted → no further feed fetch.
    await producer.produce_if_due(limit=10)
    feed_calls_after_second = len([c for c in adapter.calls if c[0] == "feed"])

    assert feed_calls_after_first == 1
    assert feed_calls_after_second == 1  # throttled: no second For-You fetch


@pytest.mark.asyncio
async def test_feed_skipped_when_health_paused(tmp_path: Path) -> None:
    db = _db(tmp_path)
    health = XSourceHealthStore(db, feed_pause_after=1)
    from openbiliclaw.sources.x_client import XRateLimitError

    health.record_error(XRateLimitError("429"), strategy="feed")  # auto-pause For-You
    adapter = _FakeXAdapter(
        by_strategy={
            "search": [_tweet_item("5790000000000000001", "x-search")],
            "feed": [_tweet_item("5790000000000000002", "x-feed")],
        }
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=health,
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        daily_feed_budget=5,
    )

    # Rate-limit cooldown blocks all fetches; clear it so search can run.
    health.set_cooldown_until("")
    await producer.produce_if_due(limit=10)

    feed_calls = [c for c in adapter.calls if c[0] == "feed"]
    assert feed_calls == []  # For-You paused → not fetched


# ── interval throttle ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_throttled_when_recently_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    adapter = _FakeXAdapter(
        by_strategy={"search": [_tweet_item("6790000000000000001", "x-search")]}
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=60,
    )

    first = await producer.produce_if_due(limit=10)
    second = await producer.produce_if_due(limit=10)

    assert first["reason"] == "ok"
    assert second["reason"] == "throttled"


# ── creator scheduling ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_due_creators_are_fetched_and_marked(tmp_path: Path) -> None:
    db = _db(tmp_path)
    creators = XCreatorStore(db)
    creators.add("@somehandle")
    adapter = _FakeXAdapter(
        by_strategy={
            "search": [],
            "creator": [_tweet_item("7790000000000000001", "x-creator")],
        }
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=creators,
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        daily_creator_budget=5,
    )

    await producer.produce_if_due(limit=10)

    creator_calls = [c for c in adapter.calls if c[0] == "creator"]
    assert creator_calls, "due creator subscription should be fetched"
    assert creator_calls[0][1].get("handle") == "somehandle"
    # last_fetched_at updated → no longer due
    assert creators.due_for_fetch(hours=24) == []


# ── re-login recovery (clear_relogin_block) ──────────────────────────


def test_clear_relogin_block_recovers_missing_cookie(tmp_path: Path) -> None:
    """A re-login state has no timed recovery, so clear_relogin_block is the
    only path back to is_ready()=True after a fresh cookie syncs."""
    from openbiliclaw.sources.x_client import XMissingCookieError

    db = _db(tmp_path)
    health = XSourceHealthStore(db)
    health.record_error(XMissingCookieError("missing auth_token / ct0"), strategy="search")
    assert health.get()["state"] == "missing_cookie"
    assert health.is_ready() is False

    cleared = health.clear_relogin_block()

    assert cleared is True
    assert health.get()["state"] == "ok"
    assert health.is_ready() is True


def test_clear_relogin_block_recovers_expired_cookie_and_feed_pause(tmp_path: Path) -> None:
    from openbiliclaw.sources.x_client import XAuthError

    db = _db(tmp_path)
    health = XSourceHealthStore(db, feed_pause_after=1)
    health.record_error(XAuthError("401"), strategy="feed")  # expired_cookie + feed pause
    assert health.get()["state"] == "expired_cookie"
    assert health.feed_allowed() is False

    assert health.clear_relogin_block() is True
    assert health.get()["state"] == "ok"
    assert health.feed_allowed() is True  # cookie refresh also lifts the feed pause


def test_clear_relogin_block_is_noop_for_rate_limited(tmp_path: Path) -> None:
    """A time-based rate-limit cooldown is not a cookie problem — leave it."""
    from openbiliclaw.sources.x_client import XRateLimitError

    db = _db(tmp_path)
    health = XSourceHealthStore(db)
    health.record_error(XRateLimitError("429"), strategy="search")
    assert health.get()["state"] == "rate_limited"

    cleared = health.clear_relogin_block()

    assert cleared is False
    assert health.get()["state"] == "rate_limited"


def test_clear_relogin_block_is_noop_when_already_ok(tmp_path: Path) -> None:
    db = _db(tmp_path)
    health = XSourceHealthStore(db)
    assert health.get()["state"] == "ok"
    assert health.clear_relogin_block() is False
    assert health.get()["state"] == "ok"


# ── P1.7 unified keyword planner fetch path (fetch-only lifecycle) ───────


from dataclasses import dataclass as _dataclass  # noqa: E402

from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator  # noqa: E402


@_dataclass
class _DiscoveryCfg:
    unified_keyword_planner_enabled: bool = False
    fetch_batch: int = 5


def _kw_statuses(db: Database) -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT keyword, status FROM discovery_keywords WHERE platform = 'twitter' ORDER BY id"
    ).fetchall()
    return {str(r["keyword"]): str(r["status"]) for r in rows}


@pytest.mark.asyncio
async def test_flag_off_search_does_not_claim(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.insert_pending_keywords("twitter", ["stored-kw"], "dig")
    adapter = _FakeXAdapter(by_strategy={"search": [_tweet_item("1", "x-search")]})
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(False)),
    )
    await producer.produce_if_due(limit=10)
    # Flag off → legacy self-gen search; the stored word stays pending, and the
    # search recipe carries no injected ``queries`` (byte-identical to pre-P1.7).
    search_calls = [c for c in adapter.calls if c[0] == "search"]
    assert search_calls and "queries" not in search_calls[0][1]
    assert _kw_statuses(db) == {"stored-kw": "pending"}


@pytest.mark.asyncio
async def test_flag_on_injects_queries_and_marks_used_on_handoff(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.insert_pending_keywords("twitter", ["rust async", "ml papers"], "dig")
    adapter = _FakeXAdapter(
        by_strategy={"search": [_tweet_item("1790000000000000099", "x-search")]}
    )
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(
            database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=5)
        ),
    )
    result = await producer.produce_if_due(limit=10)
    assert result["reason"] == "ok"
    # Claimed words injected verbatim into the search recipe config.
    search_calls = [c for c in adapter.calls if c[0] == "search"]
    assert search_calls[0][1]["queries"] == ["rust async", "ml papers"]
    # Fetch-only handoff → both words are USED (admission is downstream).
    assert _kw_statuses(db) == {"rust async": "used", "ml papers": "used"}


@pytest.mark.asyncio
async def test_flag_on_empty_store_skips_search_but_runs_others(tmp_path: Path) -> None:
    db = _db(tmp_path)  # store empty
    adapter = _FakeXAdapter(by_strategy={"search": [_tweet_item("1", "x-search")], "feed": []})
    producer = XDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        adapter=adapter,
        creator_store=XCreatorStore(db),
        health_store=XSourceHealthStore(db),
        enabled=True,
        request_interval_seconds=0,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    result = await producer.produce_if_due(limit=10)
    assert result["reason"] == "ok"
    # Store empty → the search strategy is skipped entirely this cycle.
    assert not [c for c in adapter.calls if c[0] == "search"]
    assert _kw_statuses(db) == {}
