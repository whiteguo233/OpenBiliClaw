"""Tests for retraction determinstic discounting (event-capture-completion Phase 0).

Covers three faces:
- DB reread path: ``Database.mark_positive_events_retracted`` (Task 0).
- In-memory pipeline: ``ProfileUpdatePipeline`` atomic discount + tombstone (Task 1).
- Render marker + replay invariance (Task 2).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.pipeline import (
    _BUFFERED_LAYERS,
    LayerThreshold,
    ProfileUpdatePipeline,
    signals_from_events,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

# Epoch-ms helpers so event time vs retraction_at causality is explicit.
_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "retraction.db")
    db.initialize()
    return db


def _metadata_for(db: Database, row_id: int) -> dict[str, object]:
    cursor = db.conn.execute("SELECT metadata FROM events WHERE id = ?", (row_id,))
    raw = cursor.fetchone()["metadata"]
    return json.loads(raw)


# --- Task 0: DB reread path --------------------------------------------------


def test_mark_retracted_discounts_matching_like_by_identity_key(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Positive like reported at the /i/status form (earlier than retraction).
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        title="ext like",
        metadata={"source_platform": "twitter", "signal_strength": 0.85, "timestamp": _ms(_T0)},
    )

    # Retraction arrives under the /<handle>/status form — different URL, same key.
    marked = db.mark_positive_events_retracted(
        ["https://x.com/someuser/status/123"],
        "like",
        retraction_at=_T0 + timedelta(hours=1),
    )

    assert marked == 1
    meta = _metadata_for(db, row_id)
    assert meta["retracted"] is True
    assert meta["signal_strength"] == 0.2


def test_mark_retracted_zero_hits_returns_zero(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"timestamp": _ms(_T0)},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/999"],
        "like",
        retraction_at=_T0 + timedelta(hours=1),
    )
    assert marked == 0


def test_mark_retracted_does_not_touch_other_event_types(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    fav_id = db.insert_event(
        "favorite",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 1.0, "timestamp": _ms(_T0)},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"],
        "like",  # action is like → favorite row must be untouched
        retraction_at=_T0 + timedelta(hours=1),
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, fav_id)


def test_mark_retracted_is_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0)},
    )
    at = _T0 + timedelta(hours=1)
    first = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=at
    )
    second = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=at
    )
    assert first == 1
    assert second == 1  # already-retracted rows still match; strength stays capped
    assert _metadata_for(db, row_id)["signal_strength"] == 0.2


def test_mark_retracted_preserves_row_count_and_non_metadata_columns(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        title="original title",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0)},
    )
    before = db.conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    after_rows = db.conn.execute(
        "SELECT event_type, url, title FROM events WHERE id = ?", (row_id,)
    ).fetchone()
    after_count = db.conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    assert before == after_count == 1
    assert after_rows["event_type"] == "like"
    assert after_rows["url"] == "https://x.com/i/status/123"
    assert after_rows["title"] == "original title"


def test_mark_retracted_all_four_key_types(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    note = "0123456789abcdef01234567"
    cases = [
        ("like", "https://x.com/i/status/123", "https://x.com/u/status/123"),
        (
            "favorite",
            "https://www.bilibili.com/video/BV1a",
            "https://www.bilibili.com/video/BV1a?p=2",
        ),
        ("follow", "https://space.bilibili.com/42", "https://space.bilibili.com/42/dynamic"),
        (
            "like",
            f"https://www.xiaohongshu.com/explore/{note}",
            f"https://www.xiaohongshu.com/discovery/item/{note}",
        ),
    ]
    for action, positive_url, retraction_url in cases:
        row_id = db.insert_event(
            action,
            url=positive_url,
            metadata={"signal_strength": 0.9, "timestamp": _ms(_T0)},
        )
        marked = db.mark_positive_events_retracted(
            [retraction_url], action, retraction_at=_T0 + timedelta(hours=1)
        )
        assert marked == 1, f"{action} {positive_url}"
        assert _metadata_for(db, row_id)["retracted"] is True


def test_mark_retracted_skips_events_after_retraction_time(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Re-like AFTER the retraction (like -> retract -> like): must not be marked.
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0 + timedelta(hours=2))},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, row_id)


def test_mark_retracted_skips_events_missing_timestamp(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85},  # no timestamp → conservatively skip
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, row_id)


def test_mark_retracted_rejects_out_of_whitelist_action(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.insert_event("view", url="https://x.com/i/status/123", metadata={"timestamp": _ms(_T0)})
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "view", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0


# --- Task 1: in-memory pipeline atomic discount + tombstone ------------------


class _CapturingAnalyzer:
    """Duck-typed PreferenceAnalyzer that records the events it is asked to analyze."""

    def __init__(self) -> None:
        self.registry = object()
        self.seen_events: list[list[dict[str, Any]]] = []

    async def analyze_events(
        self,
        *,
        events: list[dict[str, Any]],
        existing_preference: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        self.seen_events.append([dict(e) for e in events])
        return dict(existing_preference)


def _make_pipeline(tmp_path: Path, analyzer: Any, *, min_signals: int) -> ProfileUpdatePipeline:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    interval = 0 if min_signals == 1 else 10**9
    thresholds = {
        layer: LayerThreshold(
            min_signals=min_signals, min_interval_seconds=interval, max_buffer_size=1000
        )
        for layer in _BUFFERED_LAYERS
    }
    return ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=analyzer,
        profile_builder=object(),
        thresholds=thresholds,
    )


def _like_event(url: str, at: datetime, strength: float = 0.85) -> dict[str, Any]:
    return {
        "event_type": "like",
        "url": url,
        "title": "a like",
        "context": "点赞了",
        "metadata": {
            "source_platform": "twitter",
            "signal_strength": strength,
            "timestamp": _ms(at),
        },
    }


def _favorite_event(url: str, at: datetime) -> dict[str, Any]:
    return {
        "event_type": "favorite",
        "url": url,
        "title": "a favorite",
        "metadata": {"signal_strength": 1.0, "timestamp": _ms(at)},
    }


def _retraction_event(url: str, action: str, at: datetime) -> dict[str, Any]:
    return {
        "event_type": "feedback",
        "url": url,
        "metadata": {
            "feedback_type": "retraction",
            "retracted_action": action,
            "signal_strength": 0.2,
            "timestamp": _ms(at),
        },
    }


def _find_payload(pipeline: ProfileUpdatePipeline, url: str, event_type: str) -> dict[str, Any]:
    for buf in pipeline._buffers.values():
        for sig in buf.signals:
            payload = sig.get("payload") if isinstance(sig, dict) else None
            if not isinstance(payload, dict):
                continue
            if payload.get("url") == url and payload.get("event_type") == event_type:
                return payload
    raise AssertionError(f"no buffered {event_type} for {url}")


@pytest.mark.asyncio
async def test_discount_applied_before_threshold_consumption(tmp_path: Path) -> None:
    """Threshold-ready batch with a same-batch retraction: the consumed like
    must already be discounted (atomic preprocessing precedes _update_layer)."""
    analyzer = _CapturingAnalyzer()
    pipeline = _make_pipeline(tmp_path, analyzer, min_signals=1)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    signals = signals_from_events(
        [
            _like_event(url, now - timedelta(hours=1)),
            _retraction_event("https://x.com/u/status/123", "like", now),
        ]
    )
    await pipeline.ingest_batch(signals)

    consumed_likes = [
        e
        for batch in analyzer.seen_events
        for e in batch
        if e.get("event_type") == "like" and e.get("url") == url
    ]
    assert consumed_likes, "the like should have been consumed by the interest update"
    for like in consumed_likes:
        assert like["metadata"]["retracted"] is True
        assert like["metadata"]["signal_strength"] == 0.2


@pytest.mark.asyncio
async def test_out_of_order_tombstone_discounts_later_earlier_like(tmp_path: Path) -> None:
    """Retraction first (own batch) → tombstone; a subsequently-ingested like
    with an earlier event time is discounted on entry."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    await pipeline.ingest_batch(
        signals_from_events([_retraction_event(url, "like", now - timedelta(minutes=30))])
    )
    await pipeline.ingest_batch(signals_from_events([_like_event(url, now - timedelta(hours=1))]))
    payload = _find_payload(pipeline, url, "like")
    assert payload["metadata"]["retracted"] is True
    assert payload["metadata"]["signal_strength"] == 0.2


@pytest.mark.asyncio
async def test_relike_after_retraction_is_not_discounted(tmp_path: Path) -> None:
    """like -> retract -> like: the second like (event time after retraction)
    must NOT be discounted."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    await pipeline.ingest_batch(
        signals_from_events([_retraction_event(url, "like", now - timedelta(minutes=30))])
    )
    await pipeline.ingest_batch(signals_from_events([_like_event(url, now)]))
    payload = _find_payload(pipeline, url, "like")
    assert "retracted" not in payload["metadata"]
    assert payload["metadata"]["signal_strength"] == 0.85


@pytest.mark.asyncio
async def test_tombstone_is_action_scoped(tmp_path: Path) -> None:
    """A like-tombstone must not discount a favorite on the same identity key."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    await pipeline.ingest_batch(
        signals_from_events([_retraction_event(url, "like", now - timedelta(minutes=30))])
    )
    await pipeline.ingest_batch(
        signals_from_events([_favorite_event(url, now - timedelta(hours=1))])
    )
    payload = _find_payload(pipeline, url, "favorite")
    assert "retracted" not in payload["metadata"]


@pytest.mark.asyncio
async def test_missing_event_time_is_conservatively_not_discounted(tmp_path: Path) -> None:
    """A positive with no event time cannot be causally ordered → not discounted."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    await pipeline.ingest_batch(signals_from_events([_retraction_event(url, "like", now)]))
    like = _like_event(url, now - timedelta(hours=1))
    del like["metadata"]["timestamp"]
    await pipeline.ingest_batch(signals_from_events([like]))
    payload = _find_payload(pipeline, url, "like")
    assert "retracted" not in payload["metadata"]


@pytest.mark.asyncio
async def test_expired_tombstone_does_not_discount(tmp_path: Path) -> None:
    """A retraction older than the 24h TTL is evicted and no longer discounts."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    await pipeline.ingest_batch(
        signals_from_events([_retraction_event(url, "like", now - timedelta(hours=25))])
    )
    await pipeline.ingest_batch(signals_from_events([_like_event(url, now - timedelta(hours=26))]))
    payload = _find_payload(pipeline, url, "like")
    assert "retracted" not in payload["metadata"]


@pytest.mark.asyncio
async def test_out_of_whitelist_retracted_action_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A retraction naming a non-whitelisted action is ignored (no tombstone)."""
    pipeline = _make_pipeline(tmp_path, _CapturingAnalyzer(), min_signals=1000)
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    with caplog.at_level("WARNING"):
        await pipeline.ingest_batch(signals_from_events([_retraction_event(url, "view", now)]))
    await pipeline.ingest_batch(signals_from_events([_like_event(url, now - timedelta(hours=1))]))
    payload = _find_payload(pipeline, url, "like")
    assert "retracted" not in payload["metadata"]


# --- Task 1: late-arriving positive reconciliation (face 2b) -----------------


@pytest.mark.asyncio
async def test_late_positive_reconciled_against_stored_retraction(tmp_path: Path) -> None:
    """A positive persisted AFTER a retraction is already stored (account_sync
    backfill) is marked retracted at insert time when its event time is earlier."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    # Retraction already in the events table (persistent tombstone).
    memory._database.insert_event(
        "feedback",
        url=url,
        metadata={
            "feedback_type": "retraction",
            "retracted_action": "like",
            "timestamp": _ms(now),
        },
    )
    # Late backfill of the earlier like.
    await memory.propagate_event(_like_event(url, now - timedelta(hours=1)))
    rows = memory._database.query_events(event_types=["like"], limit=10)
    assert len(rows) == 1
    meta = json.loads(rows[0]["metadata"])
    assert meta["retracted"] is True
    assert meta["signal_strength"] == 0.2


@pytest.mark.asyncio
async def test_late_relike_after_retraction_not_reconciled(tmp_path: Path) -> None:
    """A re-like whose event time is after the stored retraction is not marked."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    now = datetime.now(UTC)
    url = "https://x.com/i/status/123"
    memory._database.insert_event(
        "feedback",
        url=url,
        metadata={
            "feedback_type": "retraction",
            "retracted_action": "like",
            "timestamp": _ms(now - timedelta(hours=1)),
        },
    )
    await memory.propagate_event(_like_event(url, now))
    rows = memory._database.query_events(event_types=["like"], limit=10)
    meta = json.loads(rows[0]["metadata"])
    assert "retracted" not in meta


# --- Task 2: render marker + replay invariance -------------------------------

# Fixed no-retraction event set + its rendered-prompt byte snapshot (sha256).
# The snapshot moved once, deliberately: the provenance projection now drops
# ``created_at`` and labels undated rows ``time_quality="ingest"`` so a model
# can never read an import moment as event time. Beyond that change the golden
# still pins byte-identical rendering (invariant 2: replay invariance, scope =
# event rendering) — re-capture only together with an explicit contract change.
_INVARIANCE_EVENTS = [
    {
        "event_type": "like",
        "title": "手冲咖啡入门",
        "url": "https://x.com/i/status/123",
        "context": "在X点赞了《手冲咖啡入门》,作者:豆子老师",
        "metadata": {
            "source_platform": "twitter",
            "author": "豆子老师",
            "signal_strength": 0.85,
        },
    },
    {
        "event_type": "view",
        "title": "讲透历史叙事",
        "url": "https://www.bilibili.com/video/BV1a",
        "context": "在 B 站看了《讲透历史叙事》",
        "metadata": {"source_platform": "bilibili", "bvid": "BV1a", "signal_strength": 0.35},
    },
]
_PREFERENCE_RENDER_SHA256 = "08fb0deaff576c42bfa3668250fa318c5cda9b3559f44b40f8b0afac5163b705"
_AWARENESS_RENDER_SHA256 = "76b2c8f59f84c9b1c11e3b69b39dd100a79a41b72bfbbf9b228672002576928d"


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def test_event_rendering_invariance_without_retractions() -> None:
    """No-retraction event sets render byte-identically after marker wiring."""
    from openbiliclaw.llm.prompts import (
        build_awareness_prompt,
        build_preference_analysis_prompt,
    )

    pref = build_preference_analysis_prompt(
        events=_INVARIANCE_EVENTS, existing_preference={"interests": []}
    )
    assert _sha256(pref[1]["content"]) == _PREFERENCE_RENDER_SHA256

    awareness = build_awareness_prompt(
        events=_INVARIANCE_EVENTS, preference_summary={"interests": []}, soul_profile={}
    )
    assert _sha256(awareness[1]["content"]) == _AWARENESS_RENDER_SHA256


def _retracted_like_event() -> dict[str, Any]:
    return {
        "event_type": "like",
        "title": "手冲咖啡入门",
        "url": "https://x.com/i/status/123",
        "context": "在X点赞了《手冲咖啡入门》",
        "metadata": {
            "source_platform": "twitter",
            "signal_strength": 0.2,
            "retracted": True,
        },
    }


def test_preference_render_marks_retracted_event() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt

    messages = build_preference_analysis_prompt(
        events=[_retracted_like_event()], existing_preference={}
    )
    assert "(已撤销)" in messages[1]["content"]


def test_awareness_render_marks_retracted_event_with_string_metadata() -> None:
    """The awareness face reads raw DB rows whose metadata is a JSON string."""
    from openbiliclaw.llm.prompts import build_awareness_prompt

    row = {
        "event_type": "like",
        "title": "手冲咖啡入门",
        "url": "https://x.com/i/status/123",
        "context": "在X点赞了《手冲咖啡入门》",
        "metadata": json.dumps({"signal_strength": 0.2, "retracted": True}, ensure_ascii=False),
    }
    messages = build_awareness_prompt(events=[row], preference_summary={}, soul_profile={})
    assert "(已撤销)" in messages[1]["content"]


def test_preference_system_prompt_mentions_retraction_semantics() -> None:
    """The static system prompt carries a retraction rule (call-invariant)."""
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt

    a = build_preference_analysis_prompt(events=[_retracted_like_event()], existing_preference={})
    b = build_preference_analysis_prompt(events=[], existing_preference={"interests": [1]})
    assert "撤销" in a[0]["content"]
    assert a[0]["content"] == b[0]["content"]  # system prompt is call-invariant
