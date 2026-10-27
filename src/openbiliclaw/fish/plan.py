"""One-shot engagement plans produced by the discovery route.

The comment-thread route is a read-only discovery strategy: it may read
comment sections and hand BV ids back to the engine for normal evaluation,
but it must never publish anything. What it *can* do is leave a plan behind —
the seed video plus the comment snapshot it just fetched — for the action
layer (``BlueFishEngine``) to execute in its own gated step.

This module is that hand-off medium. Plans are deliberately short-lived: a
plan older than :data:`PLAN_TTL_MINUTES` is discarded instead of replayed, so
a daemon that was down for an hour never wakes up to post yesterday's reply.
The store is a single JSON file under ``<data_dir>/fish/`` for the same reason
the ledger is JSONL: it is inspection-first and a lab feature does not deserve
a database migration.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import fish_home

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

PLAN_FILENAME = "plans.json"

# Calibration note: the scheduled loop wakes every 5 minutes, and the route may
# also run from an explicit refresh. 45 minutes is nine loop ticks — long
# enough that a briefly busy scheduler still executes the plan, short enough
# that a comment section's context cannot go stale around it. This is a
# product bound, not a measured optimum; retuning only changes the discard
# window, never what was already posted.
PLAN_TTL_MINUTES = 45

# A tiny queue on purpose. Plans are only useful for the round that produced
# them; keeping three means a burst of refresh rounds cannot silently stack
# up a backlog of public replies.
MAX_PLANS = 3


def _utc(value: str) -> datetime | None:
    """Parse an ISO timestamp, treating a missing zone as UTC."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class PlannedComment:
    """One comment the route suggests answering."""

    rpid: int
    uname: str
    message: str
    like_count: int = 0
    ctime: int = 0
    root: int = 0
    parent: int = 0


@dataclass(frozen=True)
class FishPlan:
    """A reply the discovery route prepared but did not send.

    ``recommendation_id`` is carried only as a trace id: it points at the
    ranked-pool row the main pipeline already scored, and is never written
    back to that row.
    """

    bvid: str
    title: str = ""
    up_name: str = ""
    description: str = ""
    topic: str = ""
    recommendation_id: int = 0
    comments: tuple[PlannedComment, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "comment_thread"


class FishPlanStore:
    """Short-lived plan queue shared by the route and the action layer."""

    def __init__(self, data_dir: Path, *, ttl_minutes: int = PLAN_TTL_MINUTES) -> None:
        self._home = fish_home(data_dir)
        self._path = self._home / PLAN_FILENAME
        self._ttl = timedelta(minutes=max(1, int(ttl_minutes)))
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def enqueue(self, plan: FishPlan) -> None:
        """Store one plan, replacing an older plan for the same video."""
        if not plan.bvid.strip():
            return
        with self._lock:
            plans = [existing for existing in self._load() if existing.bvid != plan.bvid]
            plans.append(plan)
            self._write(plans[-MAX_PLANS:])

    def take_fresh(self, *, now: datetime | None = None) -> FishPlan | None:
        """Pop the newest fresh plan; drop everything that expired."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            plans = self._load()
            fresh = [plan for plan in plans if self._is_fresh(plan, current)]
            if len(fresh) != len(plans):
                # Expired plans are removed on read so a disabled lab does not
                # leave them lying around forever.
                self._write(fresh)
            if not fresh:
                return None
            chosen = fresh[-1]
            self._write(fresh[:-1])
            return chosen

    def pending(self, *, now: datetime | None = None) -> list[FishPlan]:
        """Return fresh plans without consuming them (status/debug only)."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            return [plan for plan in self._load() if self._is_fresh(plan, current)]

    def _is_fresh(self, plan: FishPlan, now: datetime) -> bool:
        age = now - plan.created_at
        # A plan stamped slightly in the future (clock skew between the
        # discovery route and the loop) is still usable, but anything beyond a
        # minute means the timestamp cannot be trusted.
        if age < -timedelta(minutes=1):
            return False
        return age <= self._ttl

    def _load(self) -> list[FishPlan]:
        if not self._path.exists():
            return []
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Blue Fish plan file unreadable: %s", exc)
            return []
        rows = raw.get("plans") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return []
        plans: list[FishPlan] = []
        for row in rows:
            plan = _plan_from_payload(row)
            if plan is not None:
                plans.append(plan)
        return plans

    def _write(self, plans: list[FishPlan]) -> None:
        payload = {"version": 1, "plans": [_plan_payload(plan) for plan in plans]}
        try:
            self._home.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            # A plan is an optimisation, never a precondition: a read-only data
            # directory must not make the fish loop crash.
            logger.warning("Blue Fish plan write failed: %s", exc)


def _plan_payload(plan: FishPlan) -> dict[str, Any]:
    return {
        "bvid": plan.bvid,
        "title": plan.title,
        "up_name": plan.up_name,
        "description": plan.description,
        "topic": plan.topic,
        "recommendation_id": int(plan.recommendation_id),
        "created_at": plan.created_at.astimezone(UTC).isoformat(),
        "source": plan.source,
        "comments": [
            {
                "rpid": int(comment.rpid),
                "uname": comment.uname,
                "message": comment.message,
                "like_count": int(comment.like_count),
                "ctime": int(comment.ctime),
                "root": int(comment.root),
                "parent": int(comment.parent),
            }
            for comment in plan.comments
        ],
    }


def _plan_from_payload(row: Any) -> FishPlan | None:
    if not isinstance(row, dict):
        return None
    bvid = str(row.get("bvid") or "").strip()
    if not bvid:
        return None
    created = _utc(str(row.get("created_at") or ""))
    if created is None:
        return None
    comments: list[PlannedComment] = []
    raw_comments = row.get("comments")
    if isinstance(raw_comments, list):
        for item in raw_comments:
            if not isinstance(item, dict):
                continue
            try:
                rpid = int(item.get("rpid") or 0)
            except (TypeError, ValueError):
                continue
            message = str(item.get("message") or "").strip()
            if rpid <= 0 or not message:
                continue
            comments.append(
                PlannedComment(
                    rpid=rpid,
                    uname=str(item.get("uname") or ""),
                    message=message,
                    like_count=_int(item.get("like_count")),
                    ctime=_int(item.get("ctime")),
                    root=_int(item.get("root")),
                    parent=_int(item.get("parent")),
                )
            )
    try:
        recommendation_id = int(row.get("recommendation_id") or 0)
    except (TypeError, ValueError):
        recommendation_id = 0
    return FishPlan(
        bvid=bvid,
        title=str(row.get("title") or ""),
        up_name=str(row.get("up_name") or ""),
        description=str(row.get("description") or ""),
        topic=str(row.get("topic") or ""),
        recommendation_id=recommendation_id,
        comments=tuple(comments),
        created_at=created,
        source=str(row.get("source") or "comment_thread"),
    )


def _int(value: object) -> int:
    try:
        return int(str(value or 0).strip() or 0)
    except (TypeError, ValueError):
        return 0
