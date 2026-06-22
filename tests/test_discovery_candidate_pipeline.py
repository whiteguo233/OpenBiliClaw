from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import (
    REJECTED_FRANCHISE_QUOTA,
    REJECTED_LOW_SCORE,
    DiscoveryCandidateWrite,
)
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.storage.database import Database

from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from pathlib import Path


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _ScoringLLM:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "caller": caller,
            }
        )
        return _Response(json.dumps(self.payload, ensure_ascii=False))


class _FailingEvalEngine:
    async def evaluate_content_batch(
        self,
        items: list[object],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        raise RuntimeError("temporary llm outage")


class _AdmissionCountingEngine:
    def __init__(self, visible_count: dict[str, int]) -> None:
        self.visible_count = visible_count

    async def evaluate_content_batch(
        self,
        items: list[object],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        for item in items:
            item.relevance_score = 0.9
            item.relevance_reason = "fit"
            item.topic_group = "tech"
            item.style_key = "deep_dive"
        return [0.9] * len(items)

    def cache_evaluated_results(self, items: list[object]) -> int:
        self.visible_count["count"] += len(items)
        return len(items)


class _ShortEvalEngine:
    async def evaluate_content_batch(
        self,
        items: list[object],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        return [0.9]


class _LongEvalEngine:
    async def evaluate_content_batch(
        self,
        items: list[object],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        return [0.9, 0.8, 0.7]


class _NormalizingEvalEngine:
    async def evaluate_content_batch(
        self,
        items: list[Any],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        for item in items:
            item.relevance_score = 0.9
            item.relevance_reason = "fit"
            item.topic_key = "raw-key"
            item.topic_group = "raw-group"
            item.style_key = "deep_dive"
        return [0.9] * len(items)

    async def normalize_evaluated_results(self, items: list[Any]) -> None:
        for item in items:
            item.topic_key = "canonical-key"
            item.topic_group = "canonical-group"

    def cache_evaluated_results(self, items: list[object]) -> int:
        return len(items)


class _BlockingEvalEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate_content_batch(
        self,
        items: list[Any],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        self.started.set()
        await self.release.wait()
        for item in items:
            item.relevance_score = 0.9
            item.relevance_reason = "fit"
            item.topic_group = "tech"
            item.style_key = "deep_dive"
        return [0.9] * len(items)

    def cache_evaluated_results(self, items: list[object]) -> int:
        return len(items)


class _HardCapEvalEngine:
    _EVALUATE_BATCH_HARD_CAP = 2

    def __init__(self) -> None:
        self.batch_lengths: list[int] = []

    async def evaluate_content_batch(
        self,
        items: list[Any],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        self.batch_lengths.append(len(items))
        for item in items:
            item.relevance_score = 0.9
            item.relevance_reason = "fit"
            item.topic_group = "tech"
            item.style_key = "deep_dive"
        return [0.9] * len(items)

    def cache_evaluated_results(self, items: list[object]) -> int:
        return len(items)


class _ProducingEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def produce_candidates(self, *args: object, **kwargs: object) -> list[object]:
        self.calls += 1
        return [DiscoveredContent(bvid="BVPRODUCE", title="Produce", source_strategy="search")]


class _NicknameRecordingDatabase:
    def __init__(self) -> None:
        self.nicknames: list[str] = []

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
        self.nicknames.append(xhs_self_nickname)
        return 0


def _seed_visible_pool_row(db: Database, bvid: str) -> None:
    db.cache_content(
        bvid,
        title=bvid,
        up_name="UP",
        source="search",
        source_platform="bilibili",
        relevance_score=0.8,
        relevance_reason="seed",
        pool_expression="推荐文案",
        pool_topic_label="推荐主题",
        style_key="deep_dive",
        topic_group="技术",
    )


def test_pipeline_pool_count_uses_dynamic_xhs_self_nickname() -> None:
    db = _NicknameRecordingDatabase()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=object(),  # type: ignore[arg-type]
        pool_target_count=30,
        xhs_self_nickname="stale",
        xhs_self_nickname_provider=lambda: "current",
    )

    assert pipeline._pool_available_count() == 0  # noqa: SLF001
    assert db.nicknames == ["current"]


@pytest.mark.asyncio
async def test_pipeline_evaluates_mixed_pending_and_caches_accepted(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BV1",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BV1",
                content_url="https://www.bilibili.com/video/BV1",
                title="Bili",
            ),
            DiscoveryCandidateWrite(
                candidate_key="youtube:yt1",
                source_platform="youtube",
                source_strategy="yt_search",
                content_id="yt1",
                content_url="https://www.youtube.com/watch?v=yt1",
                title="YT",
            ),
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BV1",
                "score": 0.80,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            },
            {
                "content_id": "yt1",
                "score": 0.40,
                "reason": "weak",
                "topic_group": "misc",
                "style_key": "light_chat",
            },
        ]
    )
    discovery_engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=discovery_engine,
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["evaluated"] == 2
    assert result["cached"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache WHERE bvid='BV1'").fetchone()[0] == 1
    counts = db.count_discovery_candidates_by_status()
    assert counts["cached"] == 1
    assert counts["rejected_low_score"] == 1


@pytest.mark.asyncio
async def test_pipeline_stops_admission_when_pool_reaches_target(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    _seed_visible_pool_row(db, "already-ready")
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BV2",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BV2",
                content_url="https://www.bilibili.com/video/BV2",
                title="Bili 2",
            ),
            DiscoveryCandidateWrite(
                candidate_key="youtube:yt2",
                source_platform="youtube",
                source_strategy="yt_search",
                content_id="yt2",
                content_url="https://www.youtube.com/watch?v=yt2",
                title="YT 2",
            ),
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BV2",
                "score": 0.90,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            },
            {
                "content_id": "yt2",
                "score": 0.85,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            },
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=1,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["evaluated"] == 0
    assert result["cached"] == 0
    assert not llm.calls
    assert db.count_discovery_candidates_by_status()["pending_eval"] == 2


@pytest.mark.asyncio
async def test_pipeline_marks_recently_viewed_candidates_without_caching(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.insert_event("view", url="https://www.bilibili.com/video/BVVIEWED")
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVVIEWED",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVVIEWED",
                content_url="https://www.bilibili.com/video/BVVIEWED",
                title="Viewed",
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVVIEWED",
                "score": 0.90,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    counts = db.count_discovery_candidates_by_status()
    assert counts["rejected_recently_viewed"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_pipeline_target_zero_keeps_admission_unbounded(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVZERO",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVZERO",
                content_url="https://www.bilibili.com/video/BVZERO",
                title="Zero target",
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVZERO",
                "score": 0.90,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=0,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 1, "rejected": 0}
    assert db.count_discovery_candidates_by_status()["cached"] == 1


@pytest.mark.asyncio
async def test_pipeline_retries_evaluated_rows_left_when_pool_fills_mid_admission(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    visible_count = {"count": 1}
    db.count_pool_candidates = lambda **_: visible_count["count"]  # type: ignore[method-assign]
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVMID{i}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"BVMID{i}",
                title=f"Mid {i}",
            )
            for i in range(3)
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_AdmissionCountingEngine(visible_count),  # type: ignore[arg-type]
        pool_target_count=2,
    )

    first = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert first["cached"] == 1
    assert db.count_discovery_candidates_by_status()["evaluated"] == 2

    visible_count["count"] = 0
    second = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert second["cached"] == 2
    counts = db.count_discovery_candidates_by_status()
    assert counts.get("evaluated", 0) == 0
    assert counts["cached"] == 3


@pytest.mark.asyncio
async def test_pipeline_resets_transient_eval_failures_to_pending(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVFAIL",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVFAIL",
                title="Retry me",
            )
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_FailingEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 0, "cached": 0, "rejected": 0, "failed": 1}
    assert db.count_discovery_candidates_by_status()["pending_eval"] == 1


@pytest.mark.asyncio
async def test_pipeline_retries_short_eval_score_batches(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVSHORT{i}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"BVSHORT{i}",
                title=f"Short {i}",
            )
            for i in range(2)
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_ShortEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    rows = db.conn.execute(
        "SELECT status, eval_attempts, batch_eval_attempts "
        "FROM discovery_candidates ORDER BY id ASC"
    ).fetchall()
    assert result == {"evaluated": 0, "cached": 0, "rejected": 0, "failed": 2}
    assert [row["status"] for row in rows] == ["pending_eval", "pending_eval"]
    assert [row["eval_attempts"] for row in rows] == [0, 0]
    assert [row["batch_eval_attempts"] for row in rows] == [1, 1]


@pytest.mark.asyncio
async def test_pipeline_retries_long_eval_score_batches_without_orphaning_claims(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVLONG{i}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"BVLONG{i}",
                title=f"Long {i}",
            )
            for i in range(2)
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_LongEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    rows = db.conn.execute(
        "SELECT status, eval_attempts, batch_eval_attempts "
        "FROM discovery_candidates ORDER BY id ASC"
    ).fetchall()
    assert result == {"evaluated": 0, "cached": 0, "rejected": 0, "failed": 2}
    assert [row["status"] for row in rows] == ["pending_eval", "pending_eval"]
    assert [row["eval_attempts"] for row in rows] == [0, 0]
    assert [row["batch_eval_attempts"] for row in rows] == [1, 1]


@pytest.mark.asyncio
async def test_pipeline_keeps_batch_eval_failures_pending_without_burning_retry_budget(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVPOISON",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVPOISON",
                title="Poison",
            )
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_FailingEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    for _ in range(5):
        await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    row = db.conn.execute(
        "SELECT status, eval_attempts, batch_eval_attempts "
        "FROM discovery_candidates WHERE content_id='BVPOISON'"
    ).fetchone()
    counts = db.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 1
    assert counts.get("failed_eval", 0) == 0
    assert row["eval_attempts"] == 0
    assert row["batch_eval_attempts"] == 5


@pytest.mark.asyncio
async def test_pipeline_batch_eval_failure_backstop_marks_failed_eval(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVBATCHFAIL",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVBATCHFAIL",
                title="Batch fail",
            )
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_FailingEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
        max_batch_eval_attempts=2,
    )

    for _ in range(2):
        await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    row = db.conn.execute(
        "SELECT status, eval_attempts, batch_eval_attempts "
        "FROM discovery_candidates WHERE content_id='BVBATCHFAIL'"
    ).fetchone()
    assert row["status"] == "failed_eval"
    assert row["eval_attempts"] == 0
    assert row["batch_eval_attempts"] == 2


@pytest.mark.asyncio
async def test_pipeline_normalizes_topics_before_persisting_evaluated_candidates(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVNORM",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVNORM",
                title="Normalize me",
            )
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_NormalizingEvalEngine(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    row = db.conn.execute(
        "SELECT status, topic_key, topic_group FROM discovery_candidates WHERE content_id='BVNORM'"
    ).fetchone()
    assert result == {"evaluated": 1, "cached": 1, "rejected": 0}
    assert row["status"] == "cached"
    assert row["topic_key"] == "canonical-key"
    assert row["topic_group"] == "canonical-group"


@pytest.mark.asyncio
async def test_pipeline_skips_concurrent_drain_calls(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVLOCK",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVLOCK",
                title="Lock me",
            )
        ]
    )
    engine = _BlockingEvalEngine()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=engine,  # type: ignore[arg-type]
        pool_target_count=30,
    )

    first_task = asyncio.create_task(pipeline.drain_pending(profile=_build_profile()))
    await engine.started.wait()
    second = await pipeline.drain_pending(profile=_build_profile())
    engine.release.set()
    first = await first_task

    assert second == {"evaluated": 0, "cached": 0, "rejected": 0}
    assert first == {"evaluated": 1, "cached": 1, "rejected": 0}
    assert db.count_discovery_candidates_by_status()["cached"] == 1


@pytest.mark.asyncio
async def test_pipeline_clears_admitted_snapshot_when_drain_lock_is_held(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=object(),  # type: ignore[arg-type]
        pool_target_count=30,
    )
    pipeline.last_admitted_items = [
        DiscoveredContent(content_id="old", title="Old", source_strategy="search")
    ]

    async with pipeline._drain_lock:  # noqa: SLF001
        result = await pipeline.drain_pending(profile=_build_profile())

    assert result == {"evaluated": 0, "cached": 0, "rejected": 0}
    assert pipeline.last_admitted_items == []


@pytest.mark.asyncio
async def test_pipeline_clamps_claim_batch_to_evaluator_hard_cap(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVCAP{i}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"BVCAP{i}",
                title=f"Cap {i}",
            )
            for i in range(3)
        ]
    )
    engine = _HardCapEvalEngine()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=engine,  # type: ignore[arg-type]
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=5)

    counts = db.count_discovery_candidates_by_status()
    assert result == {"evaluated": 2, "cached": 2, "rejected": 0}
    assert engine.batch_lengths == [2]
    assert counts["cached"] == 2
    assert counts["pending_eval"] == 1


@pytest.mark.asyncio
async def test_pipeline_marks_franchise_quota_admission_rejection(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    for i in range(10):
        db.cache_content(
            f"seed-{i}",
            title=f"Seed {i}",
            up_name="UP",
            source="search",
            source_platform="bilibili",
            relevance_score=0.9,
            relevance_reason="seed",
            pool_expression="推荐文案",
            pool_topic_label="推荐主题",
            style_key="deep_dive",
            topic_group="tech",
            franchise_key="genshin",
        )
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVFRANCHISE",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVFRANCHISE",
                title="Quota candidate",
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVFRANCHISE",
                "score": 0.90,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
                "franchise_key": "genshin",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    row = db.conn.execute(
        "SELECT status, eval_error FROM discovery_candidates WHERE content_id='BVFRANCHISE'"
    ).fetchone()
    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    assert row["status"] == REJECTED_FRANCHISE_QUOTA
    assert "franchise quota" in row["eval_error"]


@pytest.mark.asyncio
async def test_pipeline_uses_candidate_score_threshold_from_raw_payload(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVSTRICT",
                source_platform="bilibili",
                source_strategy="related_chain",
                content_id="BVSTRICT",
                title="Strict related",
                raw_payload={"score_threshold": 0.70},
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVSTRICT",
                "score": 0.66,
                "reason": "borderline",
                "topic_group": "tech",
                "style_key": "deep_dive",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    assert db.count_discovery_candidates_by_status()["rejected_low_score"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_pipeline_observed_candidates_use_unified_relevance_floor_after_eval(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="xiaohongshu:xhs-observed-low",
                source_platform="xiaohongshu",
                source_strategy="xhs-extension-search",
                content_id="xhs-observed-low",
                title="Observed low score",
                raw_payload={"admission_policy": "observed"},
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "xhs-observed-low",
                "score": 0.30,
                "reason": "new direction",
                "topic_group": "new",
                "style_key": "story_doc",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    assert db.count_discovery_candidates_by_status()[REJECTED_LOW_SCORE] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_pipeline_xhs_observed_above_unified_default_floor_is_cached(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="xiaohongshu:xhs-observed-normal",
                source_platform="xiaohongshu",
                source_strategy="xhs-extension-task",
                content_id="xhs-observed-normal",
                title="Observed normal score",
                raw_payload={"admission_policy": "observed"},
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "xhs-observed-normal",
                "score": 0.61,
                "reason": "ordinary fit",
                "topic_group": "daily",
                "style_key": "story_doc",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 1, "rejected": 0}
    row = db.conn.execute(
        "SELECT relevance_score, source_platform FROM content_cache WHERE content_id = ?",
        ("xhs-observed-normal",),
    ).fetchone()
    assert row is not None
    assert row["source_platform"] == "xiaohongshu"
    assert row["relevance_score"] == pytest.approx(0.61)


@pytest.mark.asyncio
async def test_pipeline_bili_extension_search_observed_low_score_is_rejected(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVLOWOBS",
                source_platform="bilibili",
                source_strategy="bili-extension-search",
                content_id="BVLOWOBS",
                title="Observed Bilibili low score",
                score_threshold=0.60,
                raw_payload={"admission_policy": "observed", "score_threshold": 0.60},
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVLOWOBS",
                "score": 0.18,
                "reason": "weak fit",
                "topic_group": "misc",
                "style_key": "quick_scan",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    row = db.conn.execute(
        "SELECT status, eval_error FROM discovery_candidates WHERE content_id='BVLOWOBS'"
    ).fetchone()
    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    assert row["status"] == REJECTED_LOW_SCORE
    assert "below threshold" in row["eval_error"]


@pytest.mark.asyncio
async def test_pipeline_always_admit_policy_does_not_bypass_relevance_floor(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVALWAYS",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVALWAYS",
                title="Old always admit low score",
                raw_payload={"admission_policy": "always_admit"},
            )
        ]
    )
    llm = _ScoringLLM(
        [
            {
                "content_id": "BVALWAYS",
                "score": 0.20,
                "reason": "weak fit",
                "topic_group": "misc",
                "style_key": "quick_scan",
            }
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
        pool_target_count=30,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result == {"evaluated": 1, "cached": 0, "rejected": 1}
    assert db.count_discovery_candidates_by_status()[REJECTED_LOW_SCORE] == 1


def test_pipeline_target_zero_still_bounds_enqueued_candidates(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=object(),  # type: ignore[arg-type]
        pool_target_count=0,
    )
    items = [
        DiscoveredContent(
            content_id=f"xhs-zero-{i}",
            title=f"Zero {i}",
            source_platform="xiaohongshu",
            source_strategy="xhs-extension-search",
        )
        for i in range(605)
    ]

    enqueued = pipeline.enqueue_candidates(items, source_context="search")

    count = db.conn.execute(
        "SELECT COUNT(*) FROM discovery_candidates WHERE source_platform='xiaohongshu'"
    ).fetchone()[0]
    assert enqueued == 605
    assert count == 600


@pytest.mark.asyncio
async def test_pipeline_produce_and_enqueue_short_circuits_when_pool_full(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.count_pool_candidates = lambda **_: 30  # type: ignore[method-assign]
    engine = _ProducingEngine()
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=engine,  # type: ignore[arg-type]
        pool_target_count=30,
    )

    enqueued = await pipeline.produce_and_enqueue(
        profile=_build_profile(),
        strategies=["search"],
        limit=10,
    )

    assert enqueued == 0
    assert engine.calls == 0
