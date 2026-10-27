"""Comment-thread route — mention extraction, plan hand-off and gating."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openbiliclaw.bilibili.api import CommentInfo, VideoInfo
from openbiliclaw.discovery.strategies.comment_thread import (
    CommentThreadStrategy,
    extract_mentioned_bvids,
)
from openbiliclaw.fish.account import FishAccount
from openbiliclaw.fish.config import FishConfig, save_fish_config
from openbiliclaw.fish.engine import FishCandidate
from openbiliclaw.fish.plan import FishPlanStore
from openbiliclaw.fish.store import FishStore

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
COOKIE = "SESSION=fish-only"
SEED = FishCandidate(bvid="BV1xx411c7mD", title="种子视频", up_name="UP", topic="剪辑")
MENTIONED = "BV1aa411c7mE"
OTHER_MENTION = "BV1bb411c7mF"


class _FakeClient:
    def __init__(
        self,
        comments: dict[str, list[CommentInfo]] | None = None,
        infos: dict[str, VideoInfo] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._comments = comments or {}
        self._infos = infos or {}
        self._errors = errors or {}
        self.comment_calls: list[str] = []
        self.info_calls: list[str] = []
        self.closed = False
        self.cookie = ""

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        self.comment_calls.append(bvid)
        if bvid in self._errors:
            raise self._errors[bvid]
        return self._comments.get(bvid, [])[:limit]

    async def get_video_info(self, bvid: str) -> VideoInfo:
        self.info_calls.append(bvid)
        if bvid not in self._infos:
            raise RuntimeError(f"no such video: {bvid}")
        return self._infos[bvid]

    async def close(self) -> None:
        self.closed = True


def _comment(rpid: int, message: str, *, uname: str = "路人", likes: int = 3) -> CommentInfo:
    return CommentInfo(mid=500 + rpid, uname=uname, message=message, like_count=likes, rpid=rpid)


def _info(bvid: str, **overrides: Any) -> VideoInfo:
    payload: dict[str, Any] = {
        "bvid": bvid,
        "title": f"{bvid} 的标题",
        "description": "简介",
        "up_name": "某个UP",
        "up_mid": 900,
        "view_count": 1234,
        "like_count": 56,
        "duration": 300,
        "pub_date": "2026-10-18T10:00:00+00:00",
    }
    payload.update(overrides)
    return VideoInfo(**payload)


def _build(
    tmp_path: Any,
    *,
    enabled: bool = True,
    cookie: str | None = COOKIE,
    comments: dict[str, list[CommentInfo]] | None = None,
    infos: dict[str, VideoInfo] | None = None,
    errors: dict[str, Exception] | None = None,
    seeds: list[FishCandidate] | None = None,
    max_seeds: int = 3,
    max_mentions: int = 8,
    store: FishStore | None = None,
) -> tuple[CommentThreadStrategy, _FakeClient, FishPlanStore, list[str]]:
    save_fish_config(tmp_path, FishConfig(enabled=enabled))
    if cookie is not None:
        FishAccount(tmp_path).save_cookie(cookie)
    client = _FakeClient(comments, infos, errors)
    factory_cookies: list[str] = []

    def _factory(cookie_value: str) -> _FakeClient:
        factory_cookies.append(cookie_value)
        client.cookie = cookie_value
        return client

    async def _seeds(limit: int) -> list[FishCandidate]:
        pool = seeds if seeds is not None else [SEED]
        return pool[:limit]

    plans = FishPlanStore(tmp_path)
    ledger = store
    strategy = CommentThreadStrategy(
        data_dir=tmp_path,
        seed_provider=_seeds,
        client_factory=_factory,
        plan_store=plans,
        store=ledger,
        max_seeds=max_seeds,
        max_mentions=max_mentions,
        clock=lambda: NOW,
    )
    return strategy, client, plans, factory_cookies


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这个 BV1aa411c7mE 真的好看", ["BV1aa411c7mE"]),
        ("说的是 bv1aa411c7mE 吧", ["BV1aa411c7mE"]),
        ("BV1aa411c7mE 和 BV1bb411c7mF 都推荐", ["BV1aa411c7mE", "BV1bb411c7mF"]),
        ("BV1aa411c7mEextra 不是 id", []),
        ("没有提到任何视频", []),
    ],
)
def test_extract_mentioned_bvids(text: str, expected: list[str]) -> None:
    assert extract_mentioned_bvids(text) == expected


async def test_a_disabled_lab_does_not_touch_the_network(tmp_path) -> None:
    strategy, client, store, factory_cookies = _build(tmp_path, enabled=False)

    assert await strategy.discover(profile=None, limit=5) == []
    assert factory_cookies == []
    assert client.comment_calls == []
    assert client.info_calls == []
    assert store.pending(now=NOW) == []


async def test_a_lab_without_its_own_cookie_is_inert(tmp_path) -> None:
    strategy, client, _store, factory_cookies = _build(tmp_path, cookie=None)

    assert await strategy.discover(profile=None, limit=5) == []
    assert factory_cookies == []
    assert client.comment_calls == []


async def test_mentions_become_evaluable_bilibili_candidates(tmp_path) -> None:
    strategy, client, store, factory_cookies = _build(
        tmp_path,
        comments={SEED.bvid: [_comment(1, f"这个 {MENTIONED} 好看")]},
        infos={MENTIONED: _info(MENTIONED)},
    )

    found = await strategy.discover(profile=None, limit=5)

    assert [item.bvid for item in found] == [MENTIONED]
    item = found[0]
    assert item.title == f"{MENTIONED} 的标题"
    assert item.up_name == "某个UP"
    assert item.up_mid == 900
    assert item.description == "简介"
    assert item.view_count == 1234
    assert item.source_strategy == "comment_thread"
    assert item.source_platform == "bilibili"
    assert item.content_id == MENTIONED
    assert item.published_at
    assert factory_cookies == [COOKIE]
    assert client.closed is True
    # The route is a reader: the plan is the only durable hand-off.
    plan = store.take_fresh(now=NOW)
    assert plan is not None
    assert plan.source == "comment_thread"


async def test_the_seed_and_repeated_mentions_are_excluded(tmp_path) -> None:
    strategy, _client, _store, _cookies = _build(
        tmp_path,
        comments={
            SEED.bvid: [
                _comment(1, f"我再看一遍 {SEED.bvid}"),
                _comment(2, f"{MENTIONED} 和 {MENTIONED} 都不错"),
            ]
        },
        infos={MENTIONED: _info(MENTIONED)},
    )

    found = await strategy.discover(profile=None, limit=5)

    assert [item.bvid for item in found] == [MENTIONED]


async def test_one_plan_per_pass_for_the_first_seed_with_usable_comments(tmp_path) -> None:
    second = FishCandidate(bvid="BV1cc411c7mG", title="第二个种子")
    strategy, _client, store, _cookies = _build(
        tmp_path,
        seeds=[SEED, second],
        comments={
            SEED.bvid: [_comment(1, "好")],
            second.bvid: [_comment(9, "这个转场我反复看了三遍", likes=42)],
        },
    )

    found = await strategy.discover(profile=None, limit=5)

    assert found == []
    plan = store.take_fresh(now=NOW)
    assert plan is not None
    assert plan.bvid == second.bvid
    assert [comment.rpid for comment in plan.comments] == [9]
    assert plan.comments[0].like_count == 42
    assert store.take_fresh(now=NOW) is None


async def test_unusable_comments_leave_no_plan(tmp_path) -> None:
    strategy, _client, store, _cookies = _build(
        tmp_path,
        comments={SEED.bvid: [_comment(1, "顶"), _comment(2, "")]},
    )

    assert await strategy.discover(profile=None, limit=5) == []
    assert store.pending(now=NOW) == []


async def test_a_failed_comment_fetch_moves_on_to_the_next_seed(tmp_path) -> None:
    second = FishCandidate(bvid="BV1cc411c7mG", title="第二个种子")
    strategy, client, store, _cookies = _build(
        tmp_path,
        seeds=[SEED, second],
        comments={second.bvid: [_comment(2, "这段配乐很妙", likes=7)]},
        errors={SEED.bvid: RuntimeError("boom")},
    )

    await strategy.discover(profile=None, limit=5)

    assert client.comment_calls == [SEED.bvid, second.bvid]
    plan = store.take_fresh(now=NOW)
    assert plan is not None
    assert plan.bvid == second.bvid


async def test_an_unresolvable_mention_is_skipped(tmp_path) -> None:
    strategy, client, _store, _cookies = _build(
        tmp_path,
        comments={SEED.bvid: [_comment(1, f"看 {MENTIONED}，还有 {OTHER_MENTION}")]},
        infos={OTHER_MENTION: _info(OTHER_MENTION)},
    )

    found = await strategy.discover(profile=None, limit=5)

    assert [item.bvid for item in found] == [OTHER_MENTION]
    assert client.info_calls == [MENTIONED, OTHER_MENTION]


async def test_the_mention_cap_bounds_one_pass(tmp_path) -> None:
    third = "BV1dd411c7mH"
    strategy, client, _store, _cookies = _build(
        tmp_path,
        max_mentions=2,
        comments={
            SEED.bvid: [
                _comment(1, f"{MENTIONED} {OTHER_MENTION} {third}"),
            ]
        },
        infos={
            MENTIONED: _info(MENTIONED),
            OTHER_MENTION: _info(OTHER_MENTION),
            third: _info(third),
        },
    )

    found = await strategy.discover(profile=None, limit=10)

    assert [item.bvid for item in found] == [MENTIONED, OTHER_MENTION]
    assert client.info_calls == [MENTIONED, OTHER_MENTION]


async def test_a_provider_error_degrades_to_no_candidates(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=True))
    FishAccount(tmp_path).save_cookie(COOKIE)
    store = FishPlanStore(tmp_path)

    async def _broken(_limit: int) -> list[FishCandidate]:
        raise RuntimeError("pool unavailable")

    strategy = CommentThreadStrategy(
        data_dir=tmp_path,
        seed_provider=_broken,
        client_factory=lambda cookie: _FakeClient(),
        plan_store=store,
        clock=lambda: NOW,
    )

    assert await strategy.discover(profile=None, limit=5) == []
    assert store.pending(now=NOW) == []


def _published(store: FishStore, *, bvid: str = SEED.bvid, rpid: int = 1001) -> None:
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": NOW.date().isoformat(),
            "bvid": bvid,
            "target_rpid": rpid,
            "status": "published",
        }
    )


async def test_a_seed_the_fish_already_answered_is_skipped(tmp_path) -> None:
    ledger = FishStore(tmp_path)
    _published(ledger)
    second = FishCandidate(bvid="BV1cc411c7mG", title="第二个种子")
    strategy, client, plans, _cookies = _build(
        tmp_path,
        seeds=[SEED, second],
        comments={
            SEED.bvid: [_comment(1, "这个转场我反复看了三遍")],
            second.bvid: [_comment(2, "这段配乐很妙", likes=7)],
        },
        store=ledger,
    )

    await strategy.discover(profile=None, limit=5)

    assert client.comment_calls == [second.bvid]
    plan = plans.take_fresh(now=NOW)
    assert plan is not None
    assert plan.bvid == second.bvid


async def test_an_already_answered_comment_is_not_planned_again(tmp_path) -> None:
    ledger = FishStore(tmp_path)
    _published(ledger, rpid=1001)
    strategy, _client, plans, _cookies = _build(
        tmp_path,
        comments={
            SEED.bvid: [
                _comment(1001, "这个转场我反复看了三遍", likes=42),
                _comment(1002, "这段配乐很妙", likes=7),
            ]
        },
        store=ledger,
    )
    # Two replies are allowed under one video, so the seed stays eligible and
    # only the already-answered comment is filtered out.
    save_fish_config(tmp_path, FishConfig(enabled=True, per_video_limit=2))

    await strategy.discover(profile=None, limit=5)

    plan = plans.take_fresh(now=NOW)
    assert plan is not None
    assert [comment.rpid for comment in plan.comments] == [1002]
