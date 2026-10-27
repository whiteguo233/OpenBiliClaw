from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openbiliclaw.saved_sync.extension_broker import (
    ExtensionNativeSaveBroker,
    ExtensionNativeSaveJob,
    ExtensionNativeSaveResultIn,
)
from openbiliclaw.saved_sync.models import NativeSaveRoute, SavedItemInput
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "extension-native-save.db")
    db.initialize()
    return db


def make_job(
    *,
    platform: str = "reddit",
    platform_slug: str = "reddit",
    item_key: str = "reddit:t3_abc",
    content_id: str = "t3_abc",
    content_url: str = "https://www.reddit.com/r/test/comments/abc/demo/",
) -> ExtensionNativeSaveJob:
    return ExtensionNativeSaveJob(
        job_id=str(uuid4()),
        platform=platform,
        platform_slug=platform_slug,
        item_key=item_key,
        content_id=content_id,
        content_url=content_url,
        content_type="post",
        requested_action="favorite",
        resolved_action="favorite",
        target_label="Reddit Saved",
    )


def make_claimed_job(
    database: Database,
    *,
    platform: str = "twitter",
    platform_slug: str = "x",
    item_key: str = "twitter:123",
    content_id: str = "123",
    content_url: str | None = None,
) -> dict[str, object]:
    database.create_or_reuse_extension_native_save_job(
        make_job(
            platform=platform,
            platform_slug=platform_slug,
            item_key=item_key,
            content_id=content_id,
            content_url=content_url or f"https://x.com/example/status/{content_id}",
        )
    )
    row = database.claim_extension_native_save_job(platform_slug, 60.0)
    assert row is not None
    return row


def test_broker_persists_only_safe_job_fields(database: Database) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    job_id = broker.enqueue(
        SavedItemInput(
            source_platform="reddit",
            content_id="t3_abc",
            content_url="https://www.reddit.com/r/test/comments/abc/demo/",
            content_type="post",
            title="not persisted in extension job",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    row = database.get_extension_native_save_job(job_id)

    assert row is not None
    assert set(row) >= {
        "job_id",
        "platform",
        "platform_slug",
        "item_key",
        "content_id",
        "content_url",
        "content_type",
        "requested_action",
        "resolved_action",
        "target_label",
        "status",
    }
    assert "title" not in row


def test_persisted_url_keeps_only_platform_navigation_query_fields(database: Database) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())

    youtube_id = broker.enqueue(
        SavedItemInput(
            "youtube",
            "video-123",
            "https://www.youtube.com/watch?v=video-123&utm_source=feed&token=secret#comments",
        ),
        NativeSaveRoute("watch_later", "watch_later", "YouTube Watch Later"),
    )
    xhs_id = broker.enqueue(
        SavedItemInput(
            "xiaohongshu",
            "note-123",
            "https://www.xiaohongshu.com/explore/note-123?xsec_token=public-note-token"
            "&xsec_source=pc_feed&utm_source=ignored#comments",
        ),
        NativeSaveRoute("favorite", "favorite", "Xiaohongshu Favorites"),
    )

    youtube_row = database.get_extension_native_save_job(youtube_id)
    xhs_row = database.get_extension_native_save_job(xhs_id)
    assert youtube_row is not None
    assert youtube_row["content_url"] == "https://www.youtube.com/watch?v=video-123"
    assert xhs_row is not None
    assert xhs_row["content_url"] == (
        "https://www.xiaohongshu.com/explore/note-123?"
        "xsec_token=public-note-token&xsec_source=pc_feed"
    )


def test_persisted_url_normalizes_default_port_and_trailing_dot(database: Database) -> None:
    row = database.create_or_reuse_extension_native_save_job(
        make_job(content_url="https://www.reddit.com.:443/r/test/comments/abc/demo/")
    )

    assert row["content_url"] == "https://www.reddit.com/r/test/comments/abc/demo/"


@pytest.mark.parametrize(
    ("platform", "platform_slug", "item_key", "content_id", "content_url"),
    [
        (
            "xiaohongshu",
            "xhs",
            "xiaohongshu:note-123",
            "note-123",
            "https://www.xiaohongshu.com/explore/note-123?xsec_source=pc_feed",
        ),
        (
            "youtube",
            "yt",
            "youtube:video-123",
            "video-123",
            "https://www.youtube.com/watch?v=",
        ),
        (
            "youtube",
            "yt",
            "youtube:video-123",
            "video-123",
            "https://www.youtube.com/watch?v=video-123&v=duplicate",
        ),
    ],
)
def test_persisted_url_rejects_incomplete_or_duplicate_identity_query(
    database: Database,
    platform: str,
    platform_slug: str,
    item_key: str,
    content_id: str,
    content_url: str,
) -> None:
    with pytest.raises(ValueError, match="navigation query"):
        database.create_or_reuse_extension_native_save_job(
            make_job(
                platform=platform,
                platform_slug=platform_slug,
                item_key=item_key,
                content_id=content_id,
                content_url=content_url,
            )
        )


def test_callback_requires_job_and_item_correlation(database: Database) -> None:
    job = make_claimed_job(database)

    assert not database.complete_extension_native_save_job(
        str(job["job_id"]), "x", "twitter:999", "synced", "", ""
    )
    row = database.get_extension_native_save_job(str(job["job_id"]))
    assert row is not None
    assert row["status"] == "in_progress"


def test_duplicate_or_late_completion_is_rejected(database: Database) -> None:
    job = make_claimed_job(database)
    job_id = str(job["job_id"])

    assert database.complete_extension_native_save_job(job_id, "x", "twitter:123", "synced", "", "")
    assert not database.complete_extension_native_save_job(
        job_id, "x", "twitter:123", "failed", "native_save_timeout", "late"
    )
    row = database.get_extension_native_save_job(job_id)
    assert row is not None
    assert row["status"] == "synced"


def test_broker_ownership_includes_terminal_jobs_and_rejects_invalid_ids(
    database: Database,
) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    job = make_claimed_job(database)
    job_id = str(job["job_id"])

    assert broker.owns(job_id, "x")
    assert broker.submit_result("x", ExtensionNativeSaveResultIn(job_id, "twitter:123", "synced"))
    assert broker.owns(job_id, "x")
    assert not broker.owns(str(uuid4()), "x")
    assert not broker.owns("not-a-uuid", "x")


def test_broker_ownership_and_completion_are_bound_to_platform_slug(
    database: Database,
) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    job = make_claimed_job(
        database,
        platform="reddit",
        platform_slug="reddit",
        item_key="reddit:t3_abc",
        content_id="t3_abc",
        content_url="https://www.reddit.com/r/test/comments/abc/demo/",
    )
    job_id = str(job["job_id"])
    result = ExtensionNativeSaveResultIn(job_id, "reddit:t3_abc", "synced")

    assert broker.owns(job_id)
    assert broker.owns(job_id, "reddit")
    assert not broker.owns(job_id, "x")
    assert not broker.submit_result("x", result)
    assert broker.submit_result("reddit", result)


def test_tiktok_native_save_job_round_trips_through_broker(database: Database) -> None:
    """TikTok is a registered extension native-save platform: enqueue maps the
    platform to the ``tiktok`` slug, claim serves the durable job, and a repeat
    favorite completes as ``already_synced``."""
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    item = SavedItemInput(
        "tiktok",
        "7234567890123456789",
        "https://www.tiktok.com/@creator/video/7234567890123456789",
        "video",
    )
    route = NativeSaveRoute("favorite", "favorite", "TikTok Favorites")

    job_id = broker.enqueue(item, route)

    job = broker.claim_next("tiktok")
    assert job is not None
    assert job.job_id == job_id
    assert job.platform == "tiktok"
    assert job.platform_slug == "tiktok"
    assert job.item_key == "tiktok:7234567890123456789"
    assert job.target_label == "TikTok Favorites"
    assert broker.owns(job_id, "tiktok")
    assert not broker.owns(job_id, "dy")
    assert broker.submit_result(
        "tiktok",
        ExtensionNativeSaveResultIn(job_id, job.item_key, "already_synced"),
    )
    row = database.get_extension_native_save_job(job_id)
    assert row is not None
    assert row["status"] == "already_synced"


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        ("made_up", "", ""),
        ("failed", "NOT SAFE!", "safe"),
        ("failed", "safe_code", "unsafe\nmessage"),
        ("failed", "x" * 129, "safe"),
        ("failed", "safe_code", "x" * 513),
    ],
)
def test_result_validation_rejects_unknown_or_unsafe_fields(
    database: Database, status: str, code: str, message: str
) -> None:
    job = make_claimed_job(database)

    with pytest.raises(ValueError):
        database.complete_extension_native_save_job(
            str(job["job_id"]), "x", "twitter:123", status, code, message
        )


@pytest.mark.parametrize(
    "extension_message",
    [
        "Cookie: session=secret-token",
        "<html><body>raw platform response</body></html>",
    ],
)
def test_result_persists_only_backend_owned_safe_message(
    database: Database, extension_message: str
) -> None:
    job = make_claimed_job(database)

    assert database.complete_extension_native_save_job(
        str(job["job_id"]),
        "x",
        "twitter:123",
        "failed",
        "native_save_failed",
        extension_message,
    )

    row = database.get_extension_native_save_job(str(job["job_id"]))
    assert row is not None
    assert row["last_error_message"] == "Platform native save failed"
    assert "secret-token" not in str(row)
    assert "<html>" not in str(row)


@pytest.mark.parametrize(
    "error_code",
    [
        "native_content_not_ready",
        "native_control_not_found",
        "native_dialog_not_opened",
        "native_target_not_found",
        "native_request_rejected",
        "native_confirmation_not_observed",
    ],
)
def test_result_accepts_backend_owned_native_save_stage_codes(
    database: Database,
    error_code: str,
) -> None:
    job = make_claimed_job(database)

    assert database.complete_extension_native_save_job(
        str(job["job_id"]),
        "x",
        "twitter:123",
        "failed",
        error_code,
        "extension detail must not persist",
    )
    row = database.get_extension_native_save_job(str(job["job_id"]))
    assert row is not None
    assert row["last_error_code"] == error_code
    assert row["last_error_message"]
    assert "extension detail" not in str(row)


def test_result_rejects_unknown_code_and_unicode_control(database: Database) -> None:
    unknown_code_job = make_claimed_job(database)
    with pytest.raises(ValueError):
        database.complete_extension_native_save_job(
            str(unknown_code_job["job_id"]),
            "x",
            "twitter:123",
            "failed",
            "unknown_code",
            "safe",
        )

    unicode_control_job = make_claimed_job(
        database,
        item_key="twitter:456",
        content_id="456",
    )
    with pytest.raises(ValueError):
        database.complete_extension_native_save_job(
            str(unicode_control_job["job_id"]),
            "x",
            "twitter:456",
            "failed",
            "native_save_failed",
            "unsafe\u0085message",
        )


def test_job_validation_rejects_mismatched_platform_host_and_control_chars(
    database: Database,
) -> None:
    with pytest.raises(ValueError):
        database.create_or_reuse_extension_native_save_job(
            replace(make_job(), content_url="https://evil.example/t3_abc")
        )
    with pytest.raises(ValueError):
        database.create_or_reuse_extension_native_save_job(
            replace(make_job(), target_label="Reddit\nSaved")
        )
    with pytest.raises(ValueError):
        database.create_or_reuse_extension_native_save_job(replace(make_job(), platform_slug="x"))


def test_job_validation_rejects_nondefault_extension_url_port(database: Database) -> None:
    with pytest.raises(ValueError):
        database.create_or_reuse_extension_native_save_job(
            replace(
                make_job(),
                content_url="https://www.reddit.com:8443/r/test/comments/abc/demo/",
            )
        )


def test_job_validation_strips_nonidentity_query_and_fragment(database: Database) -> None:
    row = database.create_or_reuse_extension_native_save_job(
        replace(
            make_job(),
            content_url=("https://www.reddit.com/r/test/comments/abc/demo/?token=secret#fragment"),
        )
    )

    assert row["content_url"] == "https://www.reddit.com/r/test/comments/abc/demo/"


def test_job_validation_retains_only_youtube_identity_query(database: Database) -> None:
    job = ExtensionNativeSaveJob(
        job_id=str(uuid4()),
        platform="youtube",
        platform_slug="yt",
        item_key="youtube:video-123",
        content_id="video-123",
        content_url="https://www.youtube.com/watch?v=video-123&token=secret#fragment",
        content_type="video",
        requested_action="watch_later",
        resolved_action="watch_later",
        target_label="YouTube Watch Later",
    )

    row = database.create_or_reuse_extension_native_save_job(job)

    assert row["content_url"] == "https://www.youtube.com/watch?v=video-123"


@pytest.mark.parametrize(
    "job",
    [
        replace(make_job(), job_id="not-a-uuid"),
        replace(make_job(), item_key="reddit:t3_other"),
        replace(make_job(), target_label="x" * 257),
    ],
)
def test_job_validation_rejects_invalid_uuid_identity_and_target_length(
    database: Database, job: ExtensionNativeSaveJob
) -> None:
    with pytest.raises(ValueError):
        database.create_or_reuse_extension_native_save_job(job)


def test_active_job_is_reused_across_broker_instances(database: Database) -> None:
    item = SavedItemInput(
        "reddit",
        "t3_abc",
        "https://www.reddit.com/r/test/comments/abc/demo/",
        "post",
    )
    route = NativeSaveRoute("favorite", "favorite", "Reddit Saved")
    first = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    second = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())

    first_id = first.enqueue(item, route)
    second_id = second.enqueue(item, route)

    assert second_id == first_id
    restored = second.claim_next("reddit")
    assert restored is not None
    assert restored.job_id == first_id

    first_row = database.get_extension_native_save_job(first_id)
    assert first_row is not None
    first_row["status"] = "tampered"
    fresh_row = database.get_extension_native_save_job(first_id)
    assert fresh_row is not None
    assert fresh_row["status"] == "in_progress"


def test_active_job_is_atomically_reused_across_database_connections(tmp_path: Path) -> None:
    path = tmp_path / "atomic-reuse.db"
    first = Database(path)
    first.initialize()
    second = Database(path)
    second.initialize()
    barrier = Barrier(2)

    def create(database: Database, job: ExtensionNativeSaveJob) -> str:
        barrier.wait()
        return str(database.create_or_reuse_extension_native_save_job(job)["job_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(create, first, make_job()),
            pool.submit(create, second, make_job()),
        ]
        job_ids = {future.result() for future in futures}

    assert len(job_ids) == 1
    count = first.conn.execute(
        "SELECT COUNT(*) AS count FROM extension_native_save_jobs"
    ).fetchone()
    assert count is not None
    assert count["count"] == 1


def test_active_job_is_atomically_reused_on_one_database_instance(database: Database) -> None:
    barrier = Barrier(2)

    def create(job: ExtensionNativeSaveJob) -> str:
        barrier.wait()
        return str(database.create_or_reuse_extension_native_save_job(job)["job_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create, make_job()), pool.submit(create, make_job())]
        job_ids = {future.result() for future in futures}

    assert len(job_ids) == 1


def test_active_job_uniqueness_includes_requested_action(database: Database) -> None:
    broker = ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())
    item = SavedItemInput(
        "reddit",
        "t3_abc",
        "https://www.reddit.com/r/test/comments/abc/demo/",
        "post",
    )

    favorite_id = broker.enqueue(item, NativeSaveRoute("favorite", "favorite", "Reddit Saved"))
    watch_later_id = broker.enqueue(
        item, NativeSaveRoute("watch_later", "favorite", "Reddit Saved")
    )

    assert favorite_id != watch_later_id


@pytest.mark.asyncio
async def test_submit_result_translates_terminal_durable_row(database: Database) -> None:
    broker: ExtensionNativeSaveBroker

    async def wake(platform_slug: str) -> None:
        assert platform_slug == "reddit"
        job = broker.claim_next(platform_slug)
        assert job is not None
        assert broker.submit_result(
            platform_slug,
            ExtensionNativeSaveResultIn(
                task_id=job.job_id,
                item_key=job.item_key,
                status="already_synced",
                error_code="",
                error_message="",
            ),
        )

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=wake,
        dispatch_deadline_seconds=0.2,
        execution_deadline_seconds=0.2,
        poll_interval_seconds=0.001,
    )

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_abc",
            "https://www.reddit.com/r/test/comments/abc/demo/",
            "post",
        ),
        NativeSaveRoute("watch_later", "favorite", "Reddit Saved"),
    )

    assert result.item_key == "reddit:t3_abc"
    assert result.status == "already_synced"
    assert result.resolved_action == "favorite"
    assert result.resolved_target == "Reddit Saved"


@pytest.mark.asyncio
async def test_terminal_poll_retries_transient_sqlite_lock(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker: ExtensionNativeSaveBroker

    async def wake(platform_slug: str) -> None:
        job = broker.claim_next(platform_slug)
        assert job is not None
        assert broker.submit_result(
            platform_slug,
            ExtensionNativeSaveResultIn(job.job_id, job.item_key, "already_synced"),
        )

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=wake,
        dispatch_deadline_seconds=0.2,
        poll_interval_seconds=0.001,
    )
    get_job = database.get_extension_native_save_job
    calls = 0

    def transient_get(job_id: str) -> dict[str, object] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return get_job(job_id)

    monkeypatch.setattr(database, "get_extension_native_save_job", transient_get)

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_locked",
            "https://www.reddit.com/r/test/comments/locked/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    assert calls >= 2
    assert result.status == "already_synced"


@pytest.mark.asyncio
async def test_in_progress_expiry_check_retries_transient_sqlite_lock(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker: ExtensionNativeSaveBroker
    claimed: ExtensionNativeSaveJob | None = None

    async def wake(platform_slug: str) -> None:
        nonlocal claimed
        claimed = broker.claim_next(platform_slug)
        assert claimed is not None

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=wake,
        dispatch_deadline_seconds=0.2,
        poll_interval_seconds=0.001,
    )
    expire = database.expire_stale_extension_native_save_jobs
    calls = 0

    def transient_expire(platform_slug: str, lease_seconds: float) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        assert claimed is not None
        assert broker.submit_result(
            platform_slug,
            ExtensionNativeSaveResultIn(claimed.job_id, claimed.item_key, "already_synced"),
        )
        return expire(platform_slug, lease_seconds)

    monkeypatch.setattr(database, "expire_stale_extension_native_save_jobs", transient_expire)

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_expiry_locked",
            "https://www.reddit.com/r/test/comments/expiry_locked/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    assert calls >= 2
    assert result.status == "already_synced"


@pytest.mark.asyncio
async def test_unclaimed_dispatch_timeout_returns_extension_required(database: Database) -> None:
    wake = AsyncMock()
    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=wake,
        dispatch_deadline_seconds=0.01,
        execution_deadline_seconds=0.05,
        poll_interval_seconds=0.001,
    )

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_abc",
            "https://www.reddit.com/r/test/comments/abc/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    wake.assert_awaited_once_with("reddit")
    assert result.status == "extension_required"
    assert result.error_code == "extension_unavailable"
    row = database.conn.execute(
        "SELECT * FROM extension_native_save_jobs ORDER BY created_at LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["status"] == "extension_required"
    assert row["last_error_code"] == "extension_unavailable"
    assert not database.complete_extension_native_save_job(
        str(row["job_id"]), "reddit", "reddit:t3_abc", "synced", "", ""
    )
    durable = database.get_extension_native_save_job(str(row["job_id"]))
    assert durable is not None
    assert durable["status"] == "extension_required"


@pytest.mark.asyncio
async def test_hanging_wake_is_bounded_by_dispatch_deadline(database: Database) -> None:
    async def hanging_wake(platform_slug: str) -> None:
        assert platform_slug == "reddit"
        await asyncio.Event().wait()

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=hanging_wake,
        dispatch_deadline_seconds=0.01,
        execution_deadline_seconds=0.05,
        poll_interval_seconds=0.001,
    )

    result = await asyncio.wait_for(
        broker.save(
            SavedItemInput(
                "reddit",
                "t3_hung",
                "https://www.reddit.com/r/test/comments/hung/demo/",
                "post",
            ),
            NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
        ),
        timeout=0.2,
    )

    assert result.status == "extension_required"


@pytest.mark.asyncio
async def test_throwing_wake_still_polls_until_dispatch_deadline(database: Database) -> None:
    async def throwing_wake(platform_slug: str) -> None:
        assert platform_slug == "reddit"
        raise RuntimeError("event hub unavailable")

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=throwing_wake,
        dispatch_deadline_seconds=0.01,
        execution_deadline_seconds=0.05,
        poll_interval_seconds=0.001,
    )

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_throw",
            "https://www.reddit.com/r/test/comments/throw/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    assert result.status == "extension_required"


@pytest.mark.asyncio
async def test_claimed_execution_timeout_fails_without_automatic_replay(
    database: Database,
) -> None:
    broker: ExtensionNativeSaveBroker

    async def claim_without_result(platform_slug: str) -> None:
        assert broker.claim_next(platform_slug) is not None

    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=claim_without_result,
        dispatch_deadline_seconds=0.05,
        execution_deadline_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = await broker.save(
        SavedItemInput(
            "reddit",
            "t3_abc",
            "https://www.reddit.com/r/test/comments/abc/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    )

    assert result.status == "failed"
    assert result.error_code == "extension_task_timeout"
    assert broker.claim_next("reddit") is None


@pytest.mark.asyncio
async def test_cancellation_marks_only_an_unclaimed_job_cancelled(database: Database) -> None:
    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=AsyncMock(),
        dispatch_deadline_seconds=1.0,
        execution_deadline_seconds=1.0,
        poll_interval_seconds=0.001,
    )
    task = asyncio.create_task(
        broker.save(
            SavedItemInput(
                "reddit",
                "t3_abc",
                "https://www.reddit.com/r/test/comments/abc/demo/",
                "post",
            ),
            NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    row = database.conn.execute(
        "SELECT status FROM extension_native_save_jobs ORDER BY created_at LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["status"] == "cancelled"
