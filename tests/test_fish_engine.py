"""Blue Fish engine — isolation, quotas, dry-run and the publish path."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from openbiliclaw.bilibili.api import CommentInfo, NavInfo
from openbiliclaw.fish.account import FishAccount
from openbiliclaw.fish.config import FishConfig, save_fish_config
from openbiliclaw.fish.engine import BlueFishEngine, FishCandidate
from openbiliclaw.fish.plan import FishPlan, FishPlanStore, PlannedComment
from openbiliclaw.fish.store import FishStore

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
FISH_COOKIE = "SESSION=fish-only"
RECOMMENDATION_ID = 7


class _FakeLLM:
    def __init__(self, content: str, notice: str | None = None) -> None:
        self.content = content
        self.notice = notice
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        system = str(kwargs.get("system_instruction") or "")
        if self.notice is not None and "不是回复网友" in system:
            return SimpleNamespace(content=json.dumps({"notice": self.notice}, ensure_ascii=False))
        return SimpleNamespace(content=self.content)


class _FakeNavClient:
    def __init__(self, *, mid: int, uname: str = "大肥鱼") -> None:
        self._mid = mid
        self._uname = uname

    async def get_nav_info(self) -> NavInfo:
        return NavInfo(is_login=True, uname=self._uname, mid=self._mid)

    async def send_private_message(self, uid: int, text: str) -> dict[str, Any]:
        if self.message_error is not None:
            raise self.message_error
        self.messages.append({"uid": uid, "text": text})
        return {"msg_key": 1}

    async def close(self) -> None:
        return None


class _FakeClient:
    def __init__(
        self, comments: list[CommentInfo], *, fetch_error: Exception | None = None
    ) -> None:
        self.comments = comments
        self.fetch_error = fetch_error
        self.posts: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.cookies: list[str] = []
        self.fetch_calls: list[str] = []
        self.message_error: Exception | None = None

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        self.fetch_calls.append(bvid)
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.comments[:limit]

    async def post_comment(
        self,
        bvid: str,
        *,
        message: str,
        root: int | None = None,
        parent: int | None = None,
    ) -> dict[str, Any]:
        self.posts.append({"bvid": bvid, "message": message, "root": root, "parent": parent})
        return {"rpid": 900_000 + len(self.posts)}

    async def send_private_message(self, uid: int, text: str) -> dict[str, Any]:
        if self.message_error is not None:
            raise self.message_error
        self.messages.append({"uid": uid, "text": text})
        return {"msg_key": 1}

    async def close(self) -> None:
        return None


COMMENTS = [
    CommentInfo(
        mid=501,
        uname="小白",
        message="这个转场我反复看了三遍",
        like_count=42,
        rpid=1001,
        root=0,
        parent=0,
    ),
    CommentInfo(
        mid=502,
        uname="路人",
        message="打卡",
        like_count=1,
        rpid=1002,
    ),
]


def _decision(**overrides: object) -> str:
    payload: dict[str, object] = {
        "action": "reply",
        "target_rpid": 1001,
        "reply": "那个 0.5 秒的空镜确实值得倒回去看",
        "reason": "有具体细节可接",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _candidate(**overrides: object) -> FishCandidate:
    payload: dict[str, object] = {
        "bvid": "BV1xx411c7mD",
        "title": "一条测试视频",
        "up_name": "UP",
        "recommendation_id": RECOMMENDATION_ID,
        "topic": "剪辑",
    }
    payload.update(overrides)
    return FishCandidate(**payload)  # type: ignore[arg-type]


def _build(
    tmp_path: Any,
    *,
    config: FishConfig | None = None,
    decision: str = _decision(),
    comments: list[CommentInfo] | None = None,
    account_mid: int = 222,
    main_mid: int = 111,
    main_cookie: str = "SESSION=owner",
    main_uname: str = "主人",
    candidates: list[FishCandidate] | None = None,
    fetch_error: Exception | None = None,
    plan_store: FishPlanStore | None = None,
    local_tz: Any = None,
    notice: str | None = None,
) -> tuple[BlueFishEngine, _FakeClient, _FakeLLM, FishStore]:
    effective = config or FishConfig(enabled=True, dry_run=True)
    save_fish_config(tmp_path, effective)
    account = FishAccount(tmp_path, client_factory=lambda _cookie: _FakeNavClient(mid=account_mid))
    account.save_cookie(FISH_COOKIE)
    client = _FakeClient(
        list(comments if comments is not None else COMMENTS),
        fetch_error=fetch_error,
    )
    llm = _FakeLLM(decision, notice=notice)
    store = FishStore(tmp_path)

    async def _main_account() -> tuple[int, str, str]:
        return main_mid, main_cookie, main_uname

    async def _candidates(limit: int) -> list[FishCandidate]:
        pool = candidates if candidates is not None else [_candidate()]
        return pool[:limit]

    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=store,
        llm=llm,
        candidate_provider=_candidates,
        client_factory=lambda cookie: _client_with_cookie(client, cookie),
        clock=lambda: NOW,
        main_account_provider=_main_account,
        plan_store=plan_store,
        local_tz=local_tz,
    )
    return engine, client, llm, store


def _client_with_cookie(client: _FakeClient, cookie: str) -> _FakeClient:
    client.cookies.append(cookie)
    return client


def _seed_published(store: FishStore, *, count: int, day: str = "2026-10-19") -> None:
    for index in range(count):
        store.append(
            {
                "ts": f"{day}T0{index}:00:00+00:00",
                "day": day,
                "recommendation_id": RECOMMENDATION_ID,
                "bvid": "BV1xx411c7mD",
                "target_rpid": 5000 + index,
                "status": "published",
            }
        )


async def test_dry_run_previews_without_posting(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path)

    result = await engine.run_once(trigger="manual")

    assert result.status == "preview"
    assert result.ok is True
    assert client.posts == []
    assert result.reply.startswith("@小白 ")
    ledger = store.read()
    assert ledger[-1]["status"] == "preview"
    assert "memory_recorded" not in ledger[-1]
    assert store.view(NOW.date()).stats.xp == 0


async def test_publish_replies_with_root_parent_and_mention(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert client.posts == [
        {
            "bvid": "BV1xx411c7mD",
            "message": "@小白 那个 0.5 秒的空镜确实值得倒回去看",
            "root": 1001,
            "parent": 1001,
        }
    ]
    stats = store.view(NOW.date()).stats
    assert stats.xp == 3
    assert stats.level == 1
    assert stats.today_published == 1
    assert stats.published_total == 1


async def test_publish_writes_only_the_fish_ledger(tmp_path) -> None:
    """The owner's profile/recommendation state must not be touched.

    The engine has no feedback callback at all; the ledger line is the only
    durable trace of the action.
    """
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert len(client.posts) == 1
    records = store.read()
    assert [item["status"] for item in records] == ["published"]
    assert "memory_recorded" not in records[0]
    assert records[0]["recommendation_id"] == RECOMMENDATION_ID


async def test_a_video_answered_on_an_earlier_day_is_not_visited_again(tmp_path) -> None:
    """Dedupe is ledger-only now that the recommendation row stays untouched."""
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    store.append(
        {
            "ts": "2026-10-18T09:00:00+00:00",
            "day": "2026-10-18",
            "recommendation_id": RECOMMENDATION_ID,
            "bvid": "BV1xx411c7mD",
            "target_rpid": 700,
            "status": "published",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []


async def test_publish_uses_only_the_fish_cookie(tmp_path) -> None:
    engine, client, _llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    await engine.run_once(trigger="manual")

    assert client.cookies == [FISH_COOKIE]
    assert FISH_COOKIE != "SESSION=owner"


async def test_same_account_as_owner_is_refused(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        account_mid=111,
        main_mid=111,
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "account_not_isolated"
    assert client.posts == []


async def test_identical_cookie_as_owner_is_refused(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        main_mid=999,
        main_cookie=FISH_COOKIE,
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "account_not_isolated"
    assert client.posts == []


async def test_daily_limit_blocks_publishing(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path, config=FishConfig(enabled=True, dry_run=False, daily_limit=1)
    )
    _seed_published(store, count=1)

    result = await engine.run_once(trigger="schedule")

    assert result.status == "daily_limit"
    assert client.posts == []


async def test_already_answered_rpid_is_not_answered_twice(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1xx411c7mD",
            "target_rpid": 1001,
            "status": "published",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []


async def test_skip_decision_is_recorded_and_nothing_is_posted(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        decision=_decision(action="skip", target_rpid=0, reply=""),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []
    assert store.read()[-1]["status"] == "skipped"


async def test_too_long_reply_is_gated(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, max_chars=10),
        decision=_decision(reply="这一句明显超过了十个字的限制"),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []
    assert store.read()[-1]["status"] == "skipped"
    assert "超过上限" in store.read()[-1]["error"]


async def test_status_derives_growth_from_the_ledger(tmp_path) -> None:
    engine, _client, _llm, store = _build(tmp_path)
    _seed_published(store, count=5)

    status = await engine.status()

    assert status["config"]["enabled"] is True
    assert status["account"]["authenticated"] is True
    assert status["account"]["mid"] == 222
    assert status["state"]["xp"] == 15
    assert status["state"]["level"] == 2
    assert status["state"]["published_total"] == 5
    assert status["state"]["today_published"] == 5
    assert "topics" not in status["state"]


async def test_prompt_receives_the_pool_topic_not_a_growth_memory(tmp_path) -> None:
    engine, _client, llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    await engine.run_once(trigger="manual")

    user_prompt = llm.calls[-1]["user_input"]
    assert "<growth" not in user_prompt
    assert '"topic": "剪辑"' in user_prompt
    assert "topic" not in json.loads(_decision())


async def test_disabled_lab_does_nothing(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=False, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "disabled"
    assert client.posts == []
    assert store.read() == []


async def test_disabled_lab_never_validates_the_fish_account(tmp_path) -> None:
    """Off is off: no cookie read, no nav call, not even a ledger line."""
    calls: list[str] = []

    def _nav_factory(cookie: str) -> Any:
        calls.append(cookie)
        return _FakeNavClient(mid=222)

    async def _candidates(limit: int) -> list[FishCandidate]:
        raise AssertionError("a disabled lab must not look for candidates")

    async def _main_account() -> tuple[int, str]:
        return 111, "SESSION=owner"

    save_fish_config(tmp_path, FishConfig(enabled=False, dry_run=False))
    account = FishAccount(tmp_path, client_factory=_nav_factory)
    account.save_cookie(FISH_COOKIE)
    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=FishStore(tmp_path),
        llm=_FakeLLM(_decision()),
        candidate_provider=_candidates,
        client_factory=lambda _cookie: _FakeClient([]),
        clock=lambda: NOW,
        main_account_provider=_main_account,
    )

    result = await engine.run_once(trigger="schedule")

    assert result.status == "disabled"
    assert calls == []


async def test_publish_records_the_reply_rpid(tmp_path) -> None:
    engine, _client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert result.reply_rpid == 900_001
    assert store.read()[-1]["reply_rpid"] == 900_001


async def test_top_level_comment_posts_without_root_or_parent(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, allow_top_level=True),
        decision=_decision(action="top_level", target_rpid=0),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert client.posts == [
        {
            "bvid": "BV1xx411c7mD",
            "message": "那个 0.5 秒的空镜确实值得倒回去看",
            "root": None,
            "parent": None,
        }
    ]
    assert store.read()[-1]["mode"] == "top_level"
    assert store.read()[-1]["target_rpid"] == 0


async def test_top_level_is_refused_when_config_disallows_it(tmp_path) -> None:
    """The prompt says top_level is off; the engine must enforce it anyway."""
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, allow_top_level=False),
        decision=_decision(action="top_level", target_rpid=0),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []
    assert store.read()[-1]["status"] == "skipped"
    assert "allow_top_level" in store.read()[-1]["error"]


async def test_comment_fetch_failure_is_recorded_as_failed(tmp_path) -> None:
    engine, _client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        fetch_error=RuntimeError("reply api down"),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "comment_fetch_failed"
    record = store.read()[-1]
    assert record["status"] == "failed"
    assert "reply api down" in record["error"]


async def test_three_consecutive_failures_pause_scheduled_runs(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    for index in range(3):
        store.append(
            {
                "ts": NOW.isoformat(),
                "day": "2026-10-19",
                "bvid": f"BVfail{index}",
                "status": "failed",
                "error": "boom",
            }
        )

    result = await engine.run_once(trigger="schedule")

    assert result.status == "failure_cooldown"
    assert client.posts == []


async def test_previews_do_not_mask_a_failure_streak(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    for index in range(3):
        store.append(
            {
                "ts": NOW.isoformat(),
                "day": "2026-10-19",
                "bvid": f"BVfail{index}",
                "status": "failed",
                "error": "boom",
            }
        )
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BVpreview",
            "status": "preview",
        }
    )

    result = await engine.run_once(trigger="schedule")

    assert result.status == "failure_cooldown"
    assert client.posts == []


async def test_manual_run_still_allowed_during_failure_cooldown(tmp_path) -> None:
    engine, _client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    for index in range(3):
        store.append(
            {
                "ts": NOW.isoformat(),
                "day": "2026-10-19",
                "bvid": f"BVfail{index}",
                "status": "failed",
                "error": "boom",
            }
        )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"


async def test_audience_prompt_reaches_the_user_message(tmp_path) -> None:
    engine, _client, llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, audience_prompt="只想回在找片单的人"),
    )

    await engine.run_once(trigger="manual")

    user_prompt = llm.calls[-1]["user_input"]
    assert "<audience>" in user_prompt
    assert "只想回在找片单的人" in user_prompt


async def test_empty_audience_means_no_restriction(tmp_path) -> None:
    engine, _client, llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    await engine.run_once(trigger="manual")

    user_prompt = llm.calls[-1]["user_input"]
    assert "<audience>" in user_prompt
    assert "没有限定" in user_prompt


# ── discovery-produced plans ────────────────────────────────────────────


def _plan(
    *,
    bvid: str = "BV1plan411c7",
    created_at: datetime = NOW,
    rpid: int = 1001,
) -> FishPlan:
    return FishPlan(
        bvid=bvid,
        title="计划里的视频",
        up_name="UP",
        description="简介",
        topic="剪辑",
        recommendation_id=RECOMMENDATION_ID,
        comments=(
            PlannedComment(
                rpid=rpid,
                uname="小白",
                message="这个转场我反复看了三遍",
                like_count=42,
            ),
        ),
        created_at=created_at,
    )


async def test_a_discovery_plan_reuses_the_fetched_comment_snapshot(tmp_path) -> None:
    plans = FishPlanStore(tmp_path)
    plans.enqueue(_plan())
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        plan_store=plans,
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "published"
    assert result.bvid == "BV1plan411c7"
    # The route already paid for that fetch; the engine must not ask again.
    assert client.fetch_calls == []
    assert client.posts[0]["bvid"] == "BV1plan411c7"
    assert store.read()[-1]["bvid"] == "BV1plan411c7"


async def test_a_consumed_plan_is_not_replayed(tmp_path) -> None:
    plans = FishPlanStore(tmp_path)
    plans.enqueue(_plan(bvid="BV1plan411c7"))
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=True),
        plan_store=plans,
    )

    first = await engine.run_once(trigger="manual")
    second = await engine.run_once(trigger="manual")

    assert first.bvid == "BV1plan411c7"
    assert second.bvid == "BV1xx411c7mD"
    assert plans.take_fresh(now=NOW) is None


async def test_an_expired_plan_falls_back_to_the_pool(tmp_path) -> None:
    plans = FishPlanStore(tmp_path)
    plans.enqueue(_plan(created_at=NOW - timedelta(minutes=90)))
    engine, _client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=True),
        plan_store=plans,
    )

    result = await engine.run_once(trigger="manual")

    assert result.bvid == "BV1xx411c7mD"


async def test_a_plan_for_an_answered_video_still_respects_the_ledger(tmp_path) -> None:
    plans = FishPlanStore(tmp_path)
    plans.enqueue(_plan())
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        candidates=[_candidate(bvid="BV1plan411c7")],
        plan_store=plans,
    )
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1plan411c7",
            "target_rpid": 1,
            "status": "published",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []


async def test_the_ledger_records_which_path_produced_the_action(tmp_path) -> None:
    plans = FishPlanStore(tmp_path)
    plans.enqueue(_plan())
    engine, _client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=True),
        plan_store=plans,
    )

    await engine.run_once(trigger="manual")
    await engine.run_once(trigger="manual")

    assert [row["via"] for row in store.read()] == ["plan", "pool"]


# ── owner notification (@主人) ──────────────────────────────────────────


async def test_owner_notice_posts_a_top_level_mention_once(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert len(client.posts) == 1
    posted = client.posts[0]
    assert posted["bvid"] == "BV1xx411c7mD"
    assert posted["root"] is None and posted["parent"] is None
    assert posted["message"].startswith("@主人 ")
    assert "一条测试视频" in posted["message"]
    assert store.read()[-1]["status"] == "notified"
    assert store.read()[-1]["mode"] == "notify"
    assert store.view(NOW.date()).stats.today_notified == 1
    # The notification is the only act of the round: no reply followed it.
    assert [row["status"] for row in store.read()] == ["notified"]


async def test_owner_notice_consumes_the_min_interval(tmp_path) -> None:
    engine, _client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
    )

    await engine.run_once(trigger="schedule", manual=False)
    second = await engine.run_once(trigger="schedule", manual=False)

    assert second.status == "min_interval"


async def test_owner_notice_respects_the_daily_cap(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
    )
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1xx411c7mD",
            "status": "notified",
            "mode": "notify",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status != "notified"
    assert client.posts == []


async def test_owner_notice_uses_the_configured_nickname(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(
            enabled=True,
            dry_run=False,
            notify_owner=True,
            owner_name="老白",
        ),
        main_uname="另一个昵称",
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert client.posts[0]["message"].startswith("@老白 ")


async def test_owner_notice_falls_through_without_a_nickname(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
        main_uname="",
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    # No nickname = nothing to mention; the pass falls back to the reply flow.
    assert result.status == "published"
    assert client.posts[0]["root"] == 1001


async def test_owner_notice_dry_run_only_records(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=True, notify_owner=True),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "preview"
    assert client.posts == []
    assert store.read()[-1]["status"] == "preview"
    assert store.read()[-1]["mode"] == "notify"
    assert store.view(NOW.date()).stats.today_notified == 0


class _NotifyFailingClient(_FakeClient):
    """Fails the top-level notification, keeps the reply path working."""

    async def post_comment(self, bvid: str, *, message: str, root=None, parent=None):
        if root is None and parent is None:
            self.posts.append({"bvid": bvid, "message": message, "root": root, "parent": parent})
            raise RuntimeError("评论区已关闭")
        return await super().post_comment(bvid, message=message, root=root, parent=parent)


async def test_a_failed_owner_notice_is_not_replaced_by_a_public_reply(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=True, dry_run=False, notify_owner=True))
    account = FishAccount(tmp_path, client_factory=lambda _cookie: _FakeNavClient(mid=222))
    account.save_cookie(FISH_COOKIE)
    client = _NotifyFailingClient(list(COMMENTS))
    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=FishStore(tmp_path),
        llm=_FakeLLM(_decision()),
        candidate_provider=_candidates_for([_candidate()]),
        client_factory=lambda _cookie: client,
        clock=lambda: NOW,
        main_account_provider=_main_account_for(111, "SESSION=owner", "主人"),
        plan_store=None,
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notify_failed"
    assert [row["status"] for row in FishStore(tmp_path).read()] == ["failed"]
    # Only the attempted (failed) comment is recorded; no reply was published.
    assert len(client.posts) == 1


def _candidates_for(pool: list[FishCandidate]) -> Any:
    async def _provider(limit: int) -> list[FishCandidate]:
        return pool[:limit]

    return _provider


def _main_account_for(mid: int, cookie: str, uname: str) -> Any:
    async def _provider() -> tuple[int, str, str]:
        return mid, cookie, uname

    return _provider


# ── delivery channel: comment / DM / both ──────────────────────────────


async def test_owner_notice_can_be_a_private_message(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(
            enabled=True,
            dry_run=False,
            notify_owner=True,
            notify_channel="dm",
        ),
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert client.posts == []
    assert len(client.messages) == 1
    assert client.messages[0]["uid"] == 111
    assert "一条测试视频" in client.messages[0]["text"]
    assert "https://www.bilibili.com/video/BV1xx411c7mD" in client.messages[0]["text"]
    row = store.read()[-1]
    assert row["status"] == "notified"
    assert row["delivery"] == "dm"


async def test_owner_notice_can_do_both(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(
            enabled=True,
            dry_run=False,
            notify_owner=True,
            notify_channel="both",
        ),
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert len(client.posts) == 1
    assert len(client.messages) == 1
    assert store.read()[-1]["delivery"] == "comment+dm"


async def test_a_failed_dm_after_the_comment_is_still_a_notification(tmp_path) -> None:
    save_fish_config(
        tmp_path,
        FishConfig(enabled=True, dry_run=False, notify_owner=True, notify_channel="both"),
    )
    account = FishAccount(tmp_path, client_factory=lambda _cookie: _FakeNavClient(mid=222))
    account.save_cookie(FISH_COOKIE)
    client = _FakeClient(list(COMMENTS))
    client.message_error = RuntimeError("对方设置了不接收陌生人私信")
    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=FishStore(tmp_path),
        llm=_FakeLLM(_decision()),
        candidate_provider=_candidates_for([_candidate()]),
        client_factory=lambda _cookie: client,
        clock=lambda: NOW,
        main_account_provider=_main_account_for(111, "SESSION=owner", "主人"),
        plan_store=None,
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    row = FishStore(tmp_path).read()[-1]
    assert row["delivery"] == "comment"
    assert "私信没发出去" in row["error"]


async def test_a_dm_only_notice_without_the_owner_mid_falls_back_to_reply(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(
            enabled=True,
            dry_run=False,
            notify_owner=True,
            notify_channel="dm",
        ),
        main_mid=0,
        main_cookie="",
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "published"
    assert client.messages == []


async def test_a_dm_only_failure_surfaces_without_publishing(tmp_path) -> None:
    save_fish_config(
        tmp_path,
        FishConfig(enabled=True, dry_run=False, notify_owner=True, notify_channel="dm"),
    )
    account = FishAccount(tmp_path, client_factory=lambda _cookie: _FakeNavClient(mid=222))
    account.save_cookie(FISH_COOKIE)
    client = _FakeClient(list(COMMENTS))
    client.message_error = RuntimeError("风控拦截")
    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=FishStore(tmp_path),
        llm=_FakeLLM(_decision()),
        candidate_provider=_candidates_for([_candidate()]),
        client_factory=lambda _cookie: client,
        clock=lambda: NOW,
        main_account_provider=_main_account_for(111, "SESSION=owner", "主人"),
        plan_store=None,
    )

    result = await engine.run_once(trigger="manual")

    # The failure is recorded with its cause and surfaces as this round's
    # result: the owner asked for a private notice, so silently publishing a
    # public reply instead would be a different act.
    rows = FishStore(tmp_path).read()
    assert rows[0]["status"] == "failed"
    assert "风控拦截" in rows[0]["error"]
    assert client.messages == []
    assert client.posts == []
    assert result.status == "notify_failed"


async def test_trigger_prompt_reaches_the_user_message(tmp_path) -> None:
    engine, _client, llm, _store = _build(
        tmp_path,
        config=FishConfig(
            enabled=True,
            dry_run=False,
            trigger_prompt="只在评论区认真讨论技术时插话",
        ),
    )

    await engine.run_once(trigger="manual")

    user_prompt = llm.calls[-1]["user_input"]
    assert "<trigger>" in user_prompt
    assert "只在评论区认真讨论技术时插话" in user_prompt


async def test_empty_trigger_means_no_extra_condition(tmp_path) -> None:
    engine, _client, llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    await engine.run_once(trigger="manual")

    user_prompt = llm.calls[-1]["user_input"]
    assert "<trigger>" in user_prompt
    assert "没有额外条件" in user_prompt


async def test_active_hours_are_evaluated_in_the_local_timezone(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    shanghai = ZoneInfo("Asia/Shanghai")
    # NOW is 12:00 UTC = 20:00 in Shanghai.
    blocked, blocked_client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, active_hours="23:00-09:00"),
        local_tz=shanghai,
    )
    blocked_result = await blocked.run_once(trigger="schedule", manual=False)

    assert blocked_result.status == "outside_active_hours"
    assert blocked_client.posts == []

    import shutil

    allowed_dir = tmp_path / "allowed"
    shutil.copytree(tmp_path, allowed_dir, ignore=shutil.ignore_patterns("allowed"))
    allowed, allowed_client, _llm2, _store2 = _build(
        allowed_dir,
        config=FishConfig(enabled=True, dry_run=False, active_hours="18:00-23:00"),
        local_tz=shanghai,
    )
    allowed_result = await allowed.run_once(trigger="schedule", manual=False)

    assert allowed_result.status == "published"
    assert len(allowed_client.posts) == 1


# ── daily report ───────────────────────────────────────────────────────


async def test_each_act_is_also_written_to_a_daily_report(tmp_path) -> None:
    engine, _client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
    )

    await engine.run_once(trigger="manual")

    report = tmp_path / "fish" / "out" / f"{NOW.date().isoformat()}.md"
    text = report.read_text(encoding="utf-8")
    assert text.startswith(f"# 大肥鱼日报 {NOW.date().isoformat()}")
    assert "[published]" in text
    assert "一条测试视频" in text
    assert "https://www.bilibili.com/video/BV1xx411c7mD" in text


async def test_the_report_appends_one_bullet_per_attempt(tmp_path) -> None:
    engine, _client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=True),
    )

    await engine.run_once(trigger="manual")
    await engine.run_once(trigger="manual")

    text = (tmp_path / "fish" / "out" / f"{NOW.date().isoformat()}.md").read_text(encoding="utf-8")
    assert text.count("- ") == 2
    assert text.count("# 大肥鱼日报") == 1


# ── supply quality gates ───────────────────────────────────────────────


async def test_min_view_count_blocks_a_low_view_candidate(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, min_view_count=1000),
        candidates=[_candidate(view_count=12)],
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "quality_gate"
    assert client.posts == []


async def test_min_view_count_allows_a_popular_candidate(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, min_view_count=1000),
        candidates=[_candidate(view_count=50_000)],
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert len(client.posts) == 1


async def test_publish_age_gate_blocks_fresh_and_undated_videos(tmp_path) -> None:
    fresh = _candidate(published_at=(NOW - timedelta(hours=6)).isoformat())
    undated = _candidate(bvid="BV1undated01", published_at="")

    for candidate in (fresh, undated):
        engine, client, _llm, _store = _build(
            tmp_path / candidate.bvid,
            config=FishConfig(enabled=True, dry_run=False, min_publish_age_days=3),
            candidates=[candidate],
        )
        result = await engine.run_once(trigger="manual")
        assert result.status == "quality_gate", candidate.bvid
        assert client.posts == []


async def test_publish_age_gate_allows_an_old_video(tmp_path) -> None:
    old = _candidate(published_at=(NOW - timedelta(days=10)).isoformat())
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, min_publish_age_days=3),
        candidates=[old],
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert len(client.posts) == 1


async def test_quality_gates_are_off_by_default(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        candidates=[_candidate(view_count=0, published_at="")],
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert len(client.posts) == 1


async def test_today_report_is_exposed_on_the_status_endpoint(tmp_path) -> None:
    engine, _client, _llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    await engine.run_once(trigger="manual")

    status = await engine.status()

    assert "大肥鱼日报" in status["today_report"]
    assert "一条测试视频" in status["today_report"]


async def test_a_dm_only_notice_does_not_use_up_the_video_slot(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_channel="dm"),
    )
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1xx411c7mD",
            "status": "notified",
            "mode": "notify",
            "delivery": "dm",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert len(client.posts) == 1


async def test_a_comment_notice_does_use_up_the_video_slot(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1xx411c7mD",
            "status": "notified",
            "mode": "notify",
            "delivery": "comment",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []


async def test_the_owner_notice_uses_the_model_line(tmp_path) -> None:
    engine, client, llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
        notice="这个院子才 55 平，我截图截了三张。",
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert client.posts[0]["message"] == "@主人 这个院子才 55 平，我截图截了三张。"
    # The notice round spent exactly one model call — for the notice itself.
    assert len(llm.calls) == 1
    assert "不是回复网友" in str(llm.calls[0]["system_instruction"])


async def test_the_owner_notice_falls_back_to_the_template_without_the_model(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, notify_owner=True),
    )

    result = await engine.run_once(trigger="schedule", manual=False)

    assert result.status == "notified"
    assert client.posts[0]["message"].startswith("@主人 推荐你看看这条：《一条测试视频》")
