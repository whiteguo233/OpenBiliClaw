"""Blue Fish plan queue — freshness, dedupe and corrupt-file tolerance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openbiliclaw.fish.plan import (
    MAX_PLANS,
    FishPlan,
    FishPlanStore,
    PlannedComment,
)

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _plan(bvid: str = "BV1xx411c7mD", *, created_at: datetime = NOW, rpid: int = 1001) -> FishPlan:
    return FishPlan(
        bvid=bvid,
        title="一条测试视频",
        up_name="UP",
        description="简介",
        topic="剪辑",
        recommendation_id=7,
        comments=(
            PlannedComment(
                rpid=rpid,
                uname="小白",
                message="这个转场我反复看了三遍",
                like_count=42,
                ctime=1_700_000_000,
            ),
        ),
        created_at=created_at,
        source="comment_thread",
    )


def test_a_plan_round_trips_through_the_file(tmp_path) -> None:
    store = FishPlanStore(tmp_path)

    store.enqueue(_plan())
    taken = store.take_fresh(now=NOW)

    assert taken is not None
    assert taken.bvid == "BV1xx411c7mD"
    assert taken.title == "一条测试视频"
    assert taken.recommendation_id == 7
    assert taken.source == "comment_thread"
    assert taken.comments[0].rpid == 1001
    assert taken.comments[0].like_count == 42
    assert store.take_fresh(now=NOW) is None


def test_a_second_plan_for_the_same_video_replaces_the_first(tmp_path) -> None:
    store = FishPlanStore(tmp_path)

    store.enqueue(_plan(rpid=1001))
    store.enqueue(_plan(rpid=2002))

    taken = store.take_fresh(now=NOW)
    assert taken is not None
    assert [comment.rpid for comment in taken.comments] == [2002]
    assert store.take_fresh(now=NOW) is None


def test_the_queue_is_bounded(tmp_path) -> None:
    store = FishPlanStore(tmp_path)

    for index in range(MAX_PLANS + 2):
        store.enqueue(_plan(f"BV1xx411c7m{index}"))

    pending = store.pending(now=NOW)
    assert len(pending) == MAX_PLANS
    # Oldest entries were dropped, newest kept.
    assert pending[-1].bvid == f"BV1xx411c7m{MAX_PLANS + 1}"


def test_an_expired_plan_is_discarded_not_replayed(tmp_path) -> None:
    store = FishPlanStore(tmp_path)
    created = NOW - timedelta(minutes=46)

    store.enqueue(_plan(created_at=created))

    assert store.take_fresh(now=NOW) is None
    assert store.path.exists()


def test_a_fresh_plan_survives_the_ttl_boundary(tmp_path) -> None:
    store = FishPlanStore(tmp_path)

    store.enqueue(_plan(created_at=NOW - timedelta(minutes=44)))

    assert store.take_fresh(now=NOW) is not None


def test_a_plan_stamped_in_the_future_is_rejected(tmp_path) -> None:
    store = FishPlanStore(tmp_path)

    store.enqueue(_plan(created_at=NOW + timedelta(minutes=10)))

    assert store.take_fresh(now=NOW) is None


def test_a_corrupt_plan_file_degrades_to_empty(tmp_path) -> None:
    store = FishPlanStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ definitely not json", encoding="utf-8")

    assert store.take_fresh(now=NOW) is None
    store.enqueue(_plan())
    assert store.take_fresh(now=NOW) is not None


def test_rows_without_a_bvid_or_timestamp_are_skipped(tmp_path) -> None:
    store = FishPlanStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        '{"version": 1, "plans": [{"bvid": ""}, {"bvid": "BV1xx411c7mD"}, '
        '{"bvid": "BV1xx411c7mX", "created_at": "2026-10-20T03:00:00+00:00"}]}',
        encoding="utf-8",
    )

    pending = store.pending(now=NOW)

    assert [plan.bvid for plan in pending] == ["BV1xx411c7mX"]
