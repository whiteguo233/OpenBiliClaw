"""Regression tests for source task queue SQLite transactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.sources.dy_tasks import DyTaskQueue
from openbiliclaw.sources.xhs_tasks import XhsTaskQueue
from openbiliclaw.sources.yt_tasks import YtTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.parametrize(
    ("queue_factory", "payload"),
    [
        (XhsTaskQueue, {"keyword": "机械键盘"}),
        (DyTaskQueue, {"keywords": ["摄影"]}),
        (YtTaskQueue, {"scopes": ["yt_history"]}),
    ],
)
def test_next_pending_uses_independent_transaction_connection(
    tmp_path: Path,
    queue_factory: Callable[[Database], Any],
    payload: dict[str, Any],
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    queue = queue_factory(db)
    task_id = queue.enqueue_with_id("bootstrap_profile", payload)
    assert task_id is not None

    db.conn.execute("BEGIN")
    try:
        task = queue.next_pending()
    finally:
        db.conn.rollback()

    assert task is not None
    assert task["id"] == task_id
    assert task["status"] == "in_progress"
