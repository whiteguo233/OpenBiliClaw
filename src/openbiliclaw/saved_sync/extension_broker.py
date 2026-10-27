"""Durable, credential-free native-save jobs for the browser extension."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from .models import NativeSaveAction, NativeSaveResult, NativeSaveStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.storage.database import Database

    from .models import NativeSaveRoute, SavedItemInput

ExtensionNativeSaveResultStatus = Literal[
    "synced",
    "already_synced",
    "login_required",
    "rate_limited",
    "unsupported",
    "failed",
]

_PLATFORM_SLUGS = {
    "youtube": "yt",
    "xiaohongshu": "xhs",
    "douyin": "dy",
    "twitter": "x",
    "zhihu": "zhihu",
    "reddit": "reddit",
    "tiktok": "tiktok",
}
_TERMINAL_JOB_STATUSES = frozenset(
    {
        "synced",
        "already_synced",
        "login_required",
        "rate_limited",
        "unsupported",
        "failed",
        "extension_required",
        "cancelled",
    }
)

# A kick is immediate, while every existing extension dispatcher also has a
# 60-second alarm fallback. Five seconds of scheduling margin keeps that fallback usable.
_DEFAULT_DISPATCH_DEADLINE_SECONDS = 65.0
# Existing platform dispatcher caps reach 360 seconds (YouTube/Douyin/XHS). The broker
# uses the same upper bound so its lease never undercuts an extension-owned timeout.
_DEFAULT_EXECUTION_DEADLINE_SECONDS = 360.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.1


def _is_transient_sqlite_lock(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


@dataclass(frozen=True, slots=True)
class ExtensionNativeSaveJob:
    """Sanitized extension job persisted by the backend ledger."""

    job_id: str
    platform: str
    platform_slug: str
    item_key: str
    content_id: str
    content_url: str
    content_type: str
    requested_action: NativeSaveAction
    resolved_action: NativeSaveAction
    target_label: str


@dataclass(frozen=True, slots=True)
class ExtensionNativeSaveResultIn:
    """Correlated safe result returned by one extension executor."""

    task_id: str
    item_key: str
    status: ExtensionNativeSaveResultStatus
    error_code: str = ""
    error_message: str = ""


class ExtensionNativeSaveBroker:
    """Bridge existing native-save adapters to a durable extension job ledger."""

    def __init__(
        self,
        database: Database,
        *,
        wake_platform: Callable[[str], Awaitable[None]],
        dispatch_deadline_seconds: float = _DEFAULT_DISPATCH_DEADLINE_SECONDS,
        execution_deadline_seconds: float = _DEFAULT_EXECUTION_DEADLINE_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if dispatch_deadline_seconds <= 0:
            raise ValueError("dispatch_deadline_seconds must be positive")
        if execution_deadline_seconds <= 0:
            raise ValueError("execution_deadline_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._database = database
        self._wake_platform = wake_platform
        self._dispatch_deadline_seconds = dispatch_deadline_seconds
        self._execution_deadline_seconds = execution_deadline_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def enqueue(self, item: SavedItemInput, route: NativeSaveRoute) -> str:
        job = self._job_from_item(item, route)
        row = self._database.create_or_reuse_extension_native_save_job(job)
        return str(row["job_id"])

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        job_id = self.enqueue(item, route)
        dispatch_deadline_at = time.monotonic() + self._dispatch_deadline_seconds
        try:
            await self._wake_before_deadline(
                self._platform_slug(item.platform), dispatch_deadline_at
            )
            row = await self._wait_for_terminal(job_id, dispatch_deadline_at)
        except asyncio.CancelledError:
            if self._database.cancel_unclaimed_extension_native_save_job(job_id):
                raise
            row = await self._wait_for_terminal(job_id, dispatch_deadline_at)
        return self._native_result_from_row(row)

    def claim_next(self, platform_slug: str) -> ExtensionNativeSaveJob | None:
        row = self._database.claim_extension_native_save_job(
            platform_slug, self._execution_deadline_seconds
        )
        return self._job_from_row(row) if row is not None else None

    def owns(self, task_id: str, platform_slug: str | None = None) -> bool:
        """Return global ownership, optionally restricted to one exact slug."""
        try:
            return self._database.owns_extension_native_save_job(task_id, platform_slug)
        except ValueError:
            return False

    def submit_result(self, platform_slug: str, result: ExtensionNativeSaveResultIn) -> bool:
        return self._database.complete_extension_native_save_job(
            result.task_id,
            platform_slug,
            result.item_key,
            result.status,
            result.error_code,
            result.error_message,
        )

    async def _wake_before_deadline(self, platform_slug: str, deadline_at: float) -> None:
        try:
            wake_task: asyncio.Future[None] = asyncio.ensure_future(
                self._wake_platform(platform_slug)
            )
        except Exception:
            return
        try:
            done, _ = await asyncio.wait(
                (wake_task,), timeout=max(0.0, deadline_at - time.monotonic())
            )
        except asyncio.CancelledError:
            wake_task.cancel()
            raise
        if not done:
            wake_task.cancel()
            return
        try:
            wake_task.result()
        except Exception:
            return

    async def _wait_for_terminal(
        self, job_id: str, dispatch_deadline_at: float
    ) -> dict[str, object]:
        while True:
            try:
                row = await asyncio.to_thread(
                    self._database.get_extension_native_save_job,
                    job_id,
                )
            except Exception as exc:
                if _is_transient_sqlite_lock(exc):
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                raise
            if row is None:
                raise RuntimeError("extension native-save job disappeared")
            status = str(row["status"])
            if status in _TERMINAL_JOB_STATUSES:
                return row
            if status == "pending":
                try:
                    timed_out = time.monotonic() >= dispatch_deadline_at
                    marked = timed_out and await asyncio.to_thread(
                        self._database.mark_unclaimed_extension_native_save_job_extension_required,
                        job_id,
                    )
                    terminal = (
                        await asyncio.to_thread(
                            self._database.get_extension_native_save_job,
                            job_id,
                        )
                        if marked
                        else None
                    )
                except Exception as exc:
                    if _is_transient_sqlite_lock(exc):
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                    raise
                if marked:
                    if terminal is None:
                        raise RuntimeError("extension native-save job disappeared")
                    return terminal
            elif status == "in_progress":
                try:
                    await asyncio.to_thread(
                        self._database.expire_stale_extension_native_save_jobs,
                        str(row["platform_slug"]),
                        self._execution_deadline_seconds,
                    )
                except Exception as exc:
                    if _is_transient_sqlite_lock(exc):
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                    raise
            else:
                raise RuntimeError("invalid extension native-save job status")
            await asyncio.sleep(self._poll_interval_seconds)

    def _job_from_item(
        self, item: SavedItemInput, route: NativeSaveRoute
    ) -> ExtensionNativeSaveJob:
        platform = item.platform
        return ExtensionNativeSaveJob(
            job_id=str(uuid4()),
            platform=platform,
            platform_slug=self._platform_slug(platform),
            item_key=item.item_key,
            content_id=item.content_id,
            content_url=item.content_url,
            content_type=item.content_type,
            requested_action=route.requested_action,
            resolved_action=route.resolved_action,
            target_label=route.resolved_target,
        )

    @staticmethod
    def _job_from_row(row: dict[str, object]) -> ExtensionNativeSaveJob:
        return ExtensionNativeSaveJob(
            job_id=str(row["job_id"]),
            platform=str(row["platform"]),
            platform_slug=str(row["platform_slug"]),
            item_key=str(row["item_key"]),
            content_id=str(row["content_id"]),
            content_url=str(row["content_url"]),
            content_type=str(row["content_type"]),
            requested_action=cast("NativeSaveAction", row["requested_action"]),
            resolved_action=cast("NativeSaveAction", row["resolved_action"]),
            target_label=str(row["target_label"]),
        )

    @staticmethod
    def _native_result_from_row(row: dict[str, object]) -> NativeSaveResult:
        status = str(row["status"])
        if status == "cancelled":
            status = "extension_required"
        return NativeSaveResult(
            item_key=str(row["item_key"]),
            status=cast("NativeSaveStatus", status),
            resolved_action=cast("NativeSaveAction", row["resolved_action"]),
            resolved_target=str(row["target_label"]),
            error_code=str(row["last_error_code"]),
            error_message=str(row["last_error_message"]),
        )

    @staticmethod
    def _platform_slug(platform: str) -> str:
        try:
            return _PLATFORM_SLUGS[platform]
        except KeyError as exc:
            raise ValueError(f"unsupported extension native-save platform: {platform}") from exc
