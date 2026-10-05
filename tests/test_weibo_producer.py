from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

import openbiliclaw.runtime.weibo_producer as weibo_producer_module
from openbiliclaw.runtime.keyword_fetch import ClaimedKeyword
from openbiliclaw.runtime.weibo_producer import (
    WeiboDiscoveryProducer,
    weibo_source_status,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "weibo-producer.db")
    database.initialize()
    return database


class _Soul:
    def __init__(self) -> None:
        self.calls = 0

    async def get_profile(self) -> dict[str, object]:
        self.calls += 1
        return {"preferences": {"interests": [{"name": "科幻"}]}}


class _ClientError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _Client:
    search_results: dict[str, object] = field(default_factory=dict)
    hot_result: object = field(default_factory=list)
    creator_results: dict[str, object] = field(default_factory=dict)
    search_errors: dict[str, BaseException] = field(default_factory=dict)
    creator_errors: dict[str, BaseException] = field(default_factory=dict)
    search_calls: list[tuple[str, int, int]] = field(default_factory=list)
    hot_calls: list[int] = field(default_factory=list)
    creator_calls: list[tuple[str, int, int]] = field(default_factory=list)

    async def search_posts(
        self,
        keyword: str,
        *,
        page: int = 1,
        limit: int = 10,
    ) -> object:
        self.search_calls.append((keyword, page, limit))
        if error := self.search_errors.get(keyword):
            raise error
        return self.search_results.get(keyword, [])

    async def hot_topics(self, *, limit: int = 10) -> object:
        self.hot_calls.append(limit)
        return self.hot_result

    async def creator_posts(
        self,
        uid: str,
        *,
        page: int = 1,
        limit: int = 10,
    ) -> object:
        self.creator_calls.append((uid, page, limit))
        if error := self.creator_errors.get(uid):
            raise error
        return self.creator_results.get(uid, [])


class _Keywords:
    def __init__(self, claims: list[ClaimedKeyword]) -> None:
        self.claims = claims
        self.claim_calls: list[tuple[str, int | None]] = []
        self.used: list[int] = []
        self.failed: list[int] = []
        self.rolled_back: list[int] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
        self.claim_calls.append((platform, n))
        return list(self.claims)

    def mark_used(self, claimed: list[ClaimedKeyword]) -> None:
        self.used.extend(item.id for item in claimed)

    def mark_failed(self, claimed: list[ClaimedKeyword]) -> None:
        self.failed.extend(item.id for item in claimed)

    def rollback(self, claimed: ClaimedKeyword) -> None:
        self.rolled_back.append(claimed.id)


class _Pipeline:
    def __init__(
        self,
        *,
        full: bool = False,
        under_share_families: tuple[str, ...] = (),
    ) -> None:
        self.full = full
        self.under_share_families = set(under_share_families)
        self.full_for_calls: list[str] = []
        self.enqueued: list[tuple[list[Any], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self.full

    def pool_full_for_source(self, source_family: str) -> bool:
        self.full_for_calls.append(source_family)
        return self.full and source_family not in self.under_share_families

    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.append((list(items), source_context))
        return len(items)

    async def drain_pending(self, *, profile: object, batch_size: int) -> dict[str, int]:
        self.drains.append(batch_size)
        return {"cached": 1, "rejected": 0}


def _post(post_id: str, uid: int, text: str) -> dict[str, object]:
    return {
        "id": post_id,
        "bid": f"bid-{post_id}",
        "text": f"<p>{text}</p>",
        "created_at": "Sun Aug 09 12:00:00 +0800 2026",
        "user": {"id": uid, "screen_name": f"作者{uid}"},
        "attitudes_count": 10,
        "comments_count": 2,
        "reposts_count": 3,
    }


@pytest.mark.asyncio
async def test_three_modes_enqueue_only_canonical_posts(db: Database) -> None:
    keywords = _Keywords([ClaimedKeyword(id=7, keyword="机甲")])
    client = _Client(
        search_results={
            "机甲": [_post("s1", 11, "机甲新作"), _post("s2", 12, "机器人设计")],
            "暑期档": [_post("h1", 13, "暑期档观察")],
        },
        hot_result={"data": {"realtime": [{"word": "暑期档", "realpos": 3}]}},
        creator_results={
            "11": [_post("c11", 11, "作者十一的新帖")],
            "12": [_post("c12", 12, "作者十二的新帖")],
        },
    )
    pipeline = _Pipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=6)

    assert result["reason"] == "ok"
    assert result["discovered"] == 5
    assert result["source_counts"] == {
        "weibo-search": 2,
        "weibo-hot": 1,
        "weibo-creator": 2,
    }
    assert result["enqueued"] == 5
    assert keywords.claim_calls == [("weibo", 2)]
    assert keywords.used == [7]
    assert keywords.failed == []
    assert {context for _, context in pipeline.enqueued} == {
        "weibo-search",
        "weibo-hot",
        "weibo-creator",
    }
    assert pipeline.drains == [6]
    items = [item for batch, _ in pipeline.enqueued for item in batch]
    assert {item.content_id for item in items} == {"s1", "s2", "h1", "c11", "c12"}
    assert {item.content_type for item in items} == {"post"}
    assert {item.source_platform for item in items} == {"weibo"}
    assert {item.score_threshold for item in items} == {0.0}
    assert {item.source_keyword_id for item in items if item.source_strategy == "weibo-search"} == {
        7
    }
    hot_item = next(item for item in items if item.source_strategy == "weibo-hot")
    assert hot_item.source_rank == 3
    assert [uid for uid, _, _ in client.creator_calls] == ["11", "12"]
    assert producer.consumed_today("search") == 2
    assert producer.consumed_today("hot") == 1
    assert producer.consumed_today("creator") == 2


@pytest.mark.asyncio
async def test_hot_topics_are_seeds_and_never_synthetic_candidates(db: Database) -> None:
    client = _Client(
        search_results={"真实热词": [_post("real-post", 21, "真实微博正文")]},
        hot_result={"data": {"realtime": [{"word": "真实热词", "realpos": 8}]}},
    )
    pipeline = _Pipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("hot",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=2)

    items = [item for batch, _ in pipeline.enqueued for item in batch]
    assert result["discovered"] == 1
    assert [item.content_id for item in items] == ["real-post"]
    assert items[0].content_type == "post"
    assert items[0].source_strategy == "weibo-hot"
    assert items[0].source_rank == 8


@pytest.mark.asyncio
async def test_hot_upstream_rejected_skips_branch_without_marking_run_error(
    db: Database,
) -> None:
    class _HotRejectedClient(_Client):
        async def hot_topics(self, *, limit: int = 10) -> object:
            raise _ClientError("upstream_rejected")

    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_HotRejectedClient(),
        enabled=True,
        source_modes=("hot",),
        min_interval_minutes=0,
        candidate_pipeline=_Pipeline(),
    )

    result = await producer.produce_if_due(limit=2)

    assert result["reason"] == "empty"
    assert result["mode_results"] == {"hot": "upstream_rejected"}
    assert result["ran"] is True


@pytest.mark.asyncio
async def test_hot_seed_search_upstream_rejected_skips_branch(db: Database) -> None:
    client = _Client(
        hot_result={"data": {"realtime": [{"word": "真实热词", "realpos": 1}]}},
        search_errors={"真实热词": _ClientError("upstream_rejected")},
    )
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("hot",),
        min_interval_minutes=0,
        candidate_pipeline=_Pipeline(),
    )

    result = await producer.produce_if_due(limit=2)

    assert result["reason"] == "empty"
    assert result["mode_results"] == {"hot": "upstream_rejected"}
    assert result["ran"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["timeout", "network_error", "upstream_error"])
async def test_search_partial_error_keeps_posts_and_rolls_back_unretained_leases(
    db: Database,
    error_code: str,
) -> None:
    keywords = _Keywords(
        [
            ClaimedKeyword(id=1, keyword="成功词"),
            ClaimedKeyword(id=2, keyword="失败词"),
            ClaimedKeyword(id=3, keyword="未执行词"),
        ]
    )
    client = _Client(
        search_results={"成功词": [_post("kept", 31, "保留候选")]},
        search_errors={"失败词": _ClientError(error_code)},
    )

    used_during_handoff: list[tuple[int, ...]] = []

    class _ObservingPipeline(_Pipeline):
        def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
            used_during_handoff.append(tuple(keywords.used))
            return super().enqueue_candidates(items, source_context=source_context)

    pipeline = _ObservingPipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["mode_results"] == {"search": error_code}
    assert used_during_handoff == [()]
    assert keywords.used == [1]
    assert keywords.failed == []
    assert keywords.rolled_back == [2, 3]
    assert producer.consumed_today("search") == 1
    row = db.conn.execute(
        "SELECT units, discovered, reason, error_code FROM weibo_discovery_runs"
    ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 1, "partial", error_code)


@pytest.mark.asyncio
async def test_second_keyword_enqueue_error_preserves_first_handoff_and_cadence(
    db: Database,
) -> None:
    keywords = _Keywords(
        [
            ClaimedKeyword(id=1, keyword="先入池"),
            ClaimedKeyword(id=2, keyword="后异常"),
        ]
    )
    client = _Client(
        search_results={
            "先入池": [_post("first-kept", 32, "第一组")],
            "后异常": [_post("second-not-kept", 33, "第二组")],
        }
    )

    class _FailSecondEnqueuePipeline(_Pipeline):
        def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
            self.enqueued.append((list(items), source_context))
            if len(self.enqueued) == 2:
                raise RuntimeError("second keyword enqueue failed")
            return len(items)

    pipeline = _FailSecondEnqueuePipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=60,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    first = await producer.produce_if_due(limit=2)
    second = await producer.produce_if_due(limit=2)

    assert first["reason"] == "partial"
    assert first["discovered"] == 2
    assert first["enqueued"] == 1
    assert first["mode_results"] == {"search": "candidate_enqueue_error"}
    assert second == {"discovered": 0, "reason": "throttled"}
    assert keywords.used == [1]
    assert keywords.rolled_back == [2]
    assert keywords.failed == []
    assert producer.consumed_today("search") == 1
    assert pipeline.drains == []
    assert len(client.search_calls) == 2
    row = db.conn.execute(
        "SELECT units, discovered, reason, error_code FROM weibo_discovery_runs"
    ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 2, "partial", "candidate_enqueue_error")


@pytest.mark.asyncio
async def test_second_keyword_normalization_error_hands_off_first_and_rolls_back_rest(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keywords = _Keywords(
        [
            ClaimedKeyword(id=1, keyword="正常词"),
            ClaimedKeyword(id=2, keyword="归一化异常词"),
            ClaimedKeyword(id=3, keyword="未执行词"),
        ]
    )
    client = _Client(
        search_results={
            "正常词": [_post("normalized-kept", 34, "可保留")],
            "归一化异常词": [_post("normalizer-error", 35, "触发异常")],
            "未执行词": [_post("never-fetched", 36, "不应请求")],
        }
    )
    real_normalize = weibo_producer_module._normalize_posts

    def _raise_for_second_keyword(
        result: object,
        *,
        strategy: str,
        source_keyword_id: int | None = None,
        source_rank: int = 0,
    ) -> list[Any]:
        if source_keyword_id == 2:
            raise ValueError("normalization failed")
        return real_normalize(
            result,
            strategy=strategy,
            source_keyword_id=source_keyword_id,
            source_rank=source_rank,
        )

    monkeypatch.setattr(
        weibo_producer_module,
        "_normalize_posts",
        _raise_for_second_keyword,
    )
    pipeline = _Pipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["enqueued"] == 1
    assert result["mode_results"] == {"search": "error"}
    assert keywords.used == [1]
    assert keywords.rolled_back == [2, 3]
    assert keywords.failed == []
    assert client.search_calls == [
        ("正常词", 1, 3),
        ("归一化异常词", 1, 2),
    ]
    assert pipeline.drains == [3]
    handed_off = [item for batch, _ in pipeline.enqueued for item in batch]
    assert [item.content_id for item in handed_off] == ["normalized-kept"]
    assert producer.consumed_today("search") == 1


@pytest.mark.asyncio
async def test_rate_limit_rolls_back_keywords_and_persists_cooldown(db: Database) -> None:
    keywords = _Keywords(
        [
            ClaimedKeyword(id=1, keyword="限流词"),
            ClaimedKeyword(id=2, keyword="未执行词"),
        ]
    )
    client = _Client(
        search_errors={
            "限流词": _ClientError("rate_limited", retry_after_seconds=60),
        }
    )
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    first = await producer.produce_if_due(limit=4)
    second = await producer.produce_if_due(limit=4)

    assert first["reason"] == "error"
    assert first["mode_results"] == {"search": "rate_limited"}
    assert second == {"discovered": 0, "reason": "rate_limited"}
    assert keywords.failed == []
    assert keywords.rolled_back == [1, 2]
    assert weibo_source_status(db, enabled=True)["state"] == "rate_limited"


@pytest.mark.asyncio
async def test_empty_claim_pool_falls_back_to_profile_keywords(db: Database) -> None:
    keywords = _Keywords([])
    client = _Client(search_results={"科幻": [_post("fallback", 41, "不应抓取")]})
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert client.search_calls == [("科幻", 1, 3)]
    assert keywords.claim_calls == [("weibo", 3)]
    assert keywords.used == []
    assert keywords.failed == []
    assert keywords.rolled_back == []


@pytest.mark.asyncio
async def test_zero_pipeline_acceptance_spends_no_budget_or_cadence_and_rolls_back_claim(
    db: Database,
) -> None:
    keywords = _Keywords([ClaimedKeyword(id=9, keyword="零接收")])
    client = _Client(search_results={"零接收": [_post("drop", 49, "最终未接收")]})

    class _RejectingPipeline(_Pipeline):
        def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
            self.enqueued.append((list(items), source_context))
            return 0

    pipeline = _RejectingPipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        daily_search_budget=1,
        min_interval_minutes=60,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    first = await producer.produce_if_due(limit=1)
    second = await producer.produce_if_due(limit=1)

    assert first["enqueued"] == 0
    assert first["reason"] == "ok"
    assert first["ran"] is True
    assert first["made_progress"] is False
    assert first["productive_supply"] == 0
    assert second["reason"] == "no_output_backoff"
    assert second["backoff_reason"] == "no_progress"
    assert keywords.used == []
    assert keywords.failed == []
    assert keywords.rolled_back == [9]
    assert producer.consumed_today("search") == 0
    assert len(client.search_calls) == 1
    assert pipeline.drains == []
    rows = db.conn.execute(
        "SELECT units, discovered, reason FROM weibo_discovery_runs ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(0, 1, "ok")]
    state = db.conn.execute(
        "SELECT state_key FROM weibo_discovery_state WHERE state_key LIKE 'outcome:%'"
    ).fetchone()
    assert state is not None
    assert state[0] == "outcome:no_progress"


@pytest.mark.asyncio
async def test_partial_pipeline_acceptance_is_attributed_by_mode_and_keyword(
    db: Database,
) -> None:
    keywords = _Keywords(
        [
            ClaimedKeyword(id=1, keyword="接收词"),
            ClaimedKeyword(id=2, keyword="拒绝词"),
        ]
    )
    client = _Client(
        search_results={
            "接收词": [
                _post("accepted-one", 81, "接收一"),
                _post("accepted-two", 82, "接收二"),
            ],
            "拒绝词": [_post("rejected", 83, "拒绝")],
            "热点": [_post("hot-accepted", 84, "热点正文")],
        },
        hot_result={"data": {"realtime": [{"word": "热点", "realpos": 6}]}},
    )

    class _SelectivePipeline(_Pipeline):
        def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
            self.enqueued.append((list(items), source_context))
            if source_context == "weibo-hot":
                return 1
            keyword_id = items[0].source_keyword_id
            return 1 if keyword_id == 1 else 0

    pipeline = _SelectivePipeline()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search", "hot"),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=6)

    assert result["enqueued"] == 2
    assert keywords.used == [1]
    assert keywords.rolled_back == [2]
    assert keywords.failed == []
    assert producer.consumed_today("search") == 1
    assert producer.consumed_today("hot") == 1
    rows = db.conn.execute(
        "SELECT mode, units, discovered, reason FROM weibo_discovery_runs ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("search", 1, 3, "ok"),
        ("hot", 1, 1, "ok"),
    ]


@pytest.mark.asyncio
async def test_hot_nested_containers_are_not_stringified_into_queries(db: Database) -> None:
    client = _Client(
        search_results={"有效热词": [_post("valid-hot", 85, "有效热点正文")]},
        hot_result={
            "data": {
                "realtime": [
                    {"word": {"nested": "字典"}},
                    {"word": ["列表"]},
                    {"word": "#有效热词#", "realpos": 3},
                ]
            }
        },
    )
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("hot",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["discovered"] == 1
    assert client.search_calls == [("有效热词", 1, 3)]


@pytest.mark.asyncio
async def test_daily_budget_exhaustion_skips_network(db: Database) -> None:
    client = _Client(search_results={"科幻": [_post("post", 51, "微博")]})
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        daily_search_budget=1,
        min_interval_minutes=0,
    )
    producer.record_strategy_run(
        "search",
        units_used=1,
        discovered=1,
        reason="ok",
    )

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "budget_exhausted"
    assert result["mode_results"] == {"search": "budget_exhausted"}
    assert client.search_calls == []


@pytest.mark.asyncio
async def test_productive_cadence_survives_restart_and_empty_round_backs_off(
    db: Database,
    tmp_path: Path,
) -> None:
    productive_client = _Client(search_results={"科幻": [_post("productive", 61, "产出一条")]})
    productive = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=productive_client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=60,
    )
    first = await productive.produce_if_due(limit=1)
    restarted_client = _Client()
    restarted = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=restarted_client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=60,
    )

    assert first["discovered"] == 1
    assert await restarted.produce_if_due(limit=1) == {
        "discovered": 0,
        "reason": "throttled",
    }
    assert restarted_client.search_calls == []

    empty_db = Database(tmp_path / "weibo-empty.db")
    empty_db.initialize()
    empty_client = _Client()
    empty = WeiboDiscoveryProducer(
        database=empty_db,
        soul_engine=_Soul(),
        client=empty_client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=60,
    )
    empty_first = await empty.produce_if_due(limit=1)
    empty_second = await empty.produce_if_due(limit=1)
    assert empty_first["reason"] == "empty"
    assert empty_first["ran"] is True
    assert empty_first["made_progress"] is False
    assert empty_first["productive_supply"] == 0
    assert empty_second["reason"] == "no_output_backoff"
    assert empty_second["backoff_reason"] == "valid_empty"
    assert len(empty_client.search_calls) == 1


@pytest.mark.asyncio
async def test_infrastructure_failure_has_short_distinct_backoff(db: Database) -> None:
    client = _Client(search_errors={"科幻": _ClientError("network_error")})
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
    )

    first = await producer.produce_if_due(limit=1)
    second = await producer.produce_if_due(limit=1)

    assert first["reason"] == "error"
    assert first["ran"] is True
    assert first["made_progress"] is False
    assert second["reason"] == "no_output_backoff"
    assert second["backoff_reason"] == "infrastructure_failure"
    assert len(client.search_calls) == 1
    assert len(set(weibo_producer_module._OUTCOME_BACKOFF_SECONDS.values())) == 3
    assert (
        weibo_producer_module._OUTCOME_BACKOFF_SECONDS["infrastructure_failure"]
        < weibo_producer_module._OUTCOME_BACKOFF_SECONDS["no_progress"]
        < weibo_producer_module._OUTCOME_BACKOFF_SECONDS["valid_empty"]
    )


@pytest.mark.asyncio
async def test_force_bypasses_no_output_backoff(db: Database) -> None:
    client = _Client()
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=60,
    )

    assert (await producer.produce_if_due(limit=1))["reason"] == "empty"
    assert (await producer.produce_if_due(limit=1, force=True))["reason"] == "empty"
    assert len(client.search_calls) == 2


@pytest.mark.asyncio
async def test_share_aware_pool_gate_allows_under_share_weibo(db: Database) -> None:
    client = _Client(search_results={"科幻": [_post("supply", 71, "补足供给")]})
    pipeline = _Pipeline(full=True, under_share_families=("weibo",))
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "ok"
    assert pipeline.full_for_calls == ["weibo"]
    assert result["enqueued"] == 1


@pytest.mark.asyncio
async def test_creator_mode_requires_same_round_author_seeds(db: Database) -> None:
    client = _Client(creator_results={"71": [_post("old", 71, "不应请求")]})
    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("creator",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "no_creator_seeds"
    assert result["mode_results"] == {"creator": "no_creator_seeds"}
    assert client.creator_calls == []


def test_source_status_is_local_and_reports_latest_mode_runs(db: Database) -> None:
    assert weibo_source_status(db, enabled=False)["state"] == "disabled"
    assert weibo_source_status(db, enabled=True)["state"] == "unverified"

    producer = WeiboDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
    )
    producer.record_strategy_run(
        "search",
        units_used=1,
        discovered=1,
        reason="ok",
    )
    assert weibo_source_status(db, enabled=True) == {
        "state": "ready",
        "detail": "微博使用匿名访客会话读取公开内容，无需账号登录。",
        "modes": {"search": {"reason": "ok", "error_code": ""}},
    }

    producer.record_strategy_run(
        "hot",
        units_used=0,
        discovered=0,
        reason="error",
        error_code="upstream_error",
    )
    partial = weibo_source_status(db, enabled=True)
    assert partial["state"] == "partial"
    assert partial["modes"]["hot"]["error_code"] == "upstream_error"

    search_only = weibo_source_status(db, enabled=True, source_modes=("search",))
    assert search_only == {
        "state": "ready",
        "detail": "微博使用匿名访客会话读取公开内容，无需账号登录。",
        "modes": {"search": {"reason": "ok", "error_code": ""}},
    }
