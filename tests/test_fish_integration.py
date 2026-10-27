"""Real-account checks for the Blue Fish Lab (opt-in; never runs in CI).

Everything else in the lab is covered by fake clients. These tests are the
bridge to reality: the reply API's JSON shape, the rpid/root/parent wiring,
and whether a plain-text ``@昵称`` still means "mention" on B站. They only run
when you hand them a real fish cookie:

    export OPENBILICLAW_FISH_IT_COOKIE='SESSDATA=…; bili_jct=…; DedeUserID=…'
    export OPENBILICLAW_FISH_IT_BVID=BV1xxxxxxxxx
    pytest -m integration tests/test_fish_integration.py -v

Publishing is a second, deliberate opt-in because it puts a real public
comment under a real video (and may notify the person being @-ed). The test
deletes the comment again right after posting, but there is still a window
where it is visible:

    export OPENBILICLAW_FISH_IT_POST=1

Optionally export ``OPENBILICLAW_FISH_IT_MAIN_COOKIE`` to also assert the
account-isolation rule against the owner's account, and to run the owner-@
check: post ``@主人 …``, wait for it to show up in the owner's message feed
(``/x/msgfeed/reply``), then delete it. That is the one thing fakes cannot
tell us — whether a plain-text ``@昵称`` still produces a real mention.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from openbiliclaw.bilibili.api import CommentInfo

pytestmark = pytest.mark.integration


def _requires(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} not set — real-account integration check skipped")
    return value


def _fish_cookie() -> str:
    return _requires("OPENBILICLAW_FISH_IT_COOKIE")


def _video_bvid() -> str:
    value = _requires("OPENBILICLAW_FISH_IT_BVID")
    if not value.startswith("BV"):
        pytest.skip("OPENBILICLAW_FISH_IT_BVID must be a BV id")
    return value


async def _fetch_comments(bvid: str) -> list[CommentInfo]:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    client = BilibiliAPIClient(cookie=_fish_cookie())
    try:
        return await client.get_video_comments(bvid, limit=20)
    finally:
        await client.close()


async def test_fish_account_can_read_the_comment_section() -> None:
    bvid = _video_bvid()

    comments = await _fetch_comments(bvid)

    assert comments, "the reply API returned no comments for this video"
    assert all(comment.rpid > 0 for comment in comments), "every comment needs an rpid"
    assert any(comment.message.strip() for comment in comments)


async def test_fish_account_is_not_the_owner() -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    fish_cookie = _fish_cookie()
    main_cookie = os.environ.get("OPENBILICLAW_FISH_IT_MAIN_COOKIE", "").strip()

    fish_client = BilibiliAPIClient(cookie=fish_cookie)
    try:
        fish_nav = await fish_client.get_nav_info()
    finally:
        await fish_client.close()
    assert fish_nav.is_login, "the fish cookie is not logged in"
    assert fish_cookie != main_cookie, "the fish must not reuse the owner's cookie"

    if not main_cookie:
        return
    main_client = BilibiliAPIClient(cookie=main_cookie)
    try:
        main_nav = await main_client.get_nav_info()
    finally:
        await main_client.close()
    if main_nav.is_login:
        assert fish_nav.mid != main_nav.mid, "the fish is logged in as the owner's account"


async def test_post_then_delete_a_real_thread_reply() -> None:
    """Round-trip the only two write paths the lab depends on.

    Two explicit opt-ins: a real cookie/bvid, plus ``OPENBILICLAW_FISH_IT_POST=1``.
    The reply is deleted in the same test; if deletion fails the test fails and
    leaves the rpid in the log for manual cleanup.
    """
    if os.environ.get("OPENBILICLAW_FISH_IT_POST", "").strip() != "1":
        pytest.skip("set OPENBILICLAW_FISH_IT_POST=1 to publish/delete a real reply")

    from openbiliclaw.bilibili.api import BilibiliAPIClient

    bvid = _video_bvid()
    comments = await _fetch_comments(bvid)
    assert comments, "need at least one comment to answer"
    target = comments[0]
    root = int(target.root) if int(target.root) > 0 else int(target.rpid)

    client = BilibiliAPIClient(cookie=_fish_cookie())
    rpid = 0
    try:
        posted = await client.post_comment(
            bvid,
            message=f"@{target.uname} 蓝鱼联调测试，这条会被立即删除。",
            root=root,
            parent=int(target.rpid),
        )
        rpid = int(str(posted.get("rpid") or posted.get("rpid_str") or 0).strip() or 0)
        assert rpid > 0, "the reply API response did not carry an rpid"
    finally:
        if rpid > 0:
            await client.delete_comment(bvid, rpid)
        await client.close()


async def test_owner_mention_reaches_the_owner_message_feed() -> None:
    """Prove that a fish comment really notifies the owner.

    Needs the write opt-in **and** the owner's own cookie:

        OPENBILICLAW_FISH_IT_POST=1
        OPENBILICLAW_FISH_IT_MAIN_COOKIE='SESSDATA=…; bili_jct=…'

    The comment is deleted immediately after the check. While it exists it is
    public and may notify the owner — that is exactly what is being tested.
    """
    if os.environ.get("OPENBILICLAW_FISH_IT_POST", "").strip() != "1":
        pytest.skip("set OPENBILICLAW_FISH_IT_POST=1 to publish/delete a real mention")
    main_cookie = _requires("OPENBILICLAW_FISH_IT_MAIN_COOKIE")
    bvid = _video_bvid()

    from openbiliclaw.bilibili.api import BilibiliAPIClient

    fish = BilibiliAPIClient(cookie=_fish_cookie())
    owner = BilibiliAPIClient(cookie=main_cookie)
    rpid = 0
    started = int(time.time()) - 5
    try:
        owner_nav = await owner.get_nav_info()
        assert owner_nav.is_login, "the owner cookie is not logged in"
        info = await fish.get_video_info(bvid)
        text = f"@{owner_nav.uname} 蓝鱼联调测试：这条 @ 通知会立即删除。"
        posted = await fish.post_comment(bvid, message=text)
        rpid = int(str(posted.get("rpid") or posted.get("rpid_str") or 0).strip() or 0)
        assert rpid > 0, "the reply API response did not carry an rpid"

        found: dict[str, object] | None = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for item in await owner.get_reply_notifications(limit=50):
                detail = item.get("item")
                if not isinstance(detail, dict):
                    continue
                at_mids = {
                    int(entry.get("mid") or 0)
                    for entry in detail.get("at_details") or []
                    if isinstance(entry, dict)
                }
                if int(detail.get("subject_id") or 0) == int(info.aid) and owner_nav.mid in at_mids:
                    found = item
                    break
            if found is not None:
                break
            await asyncio.sleep(3)

        assert found is not None, (
            "the owner feed never showed this mention — a plain-text @昵称 stopped "
            "producing a real mention on the current B站 API; the owner notice needs "
            "a different delivery path"
        )
        assert int(found.get("reply_time") or 0) >= started
    finally:
        if rpid > 0:
            await fish.delete_comment(bvid, rpid)
        await fish.close()
        await owner.close()
