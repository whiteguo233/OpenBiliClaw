"""Append-only ledger for the Blue Fish Lab.

One file under ``<data_dir>/fish/``: ``ledger.jsonl``. Every attempt — even a
``skip`` — appends one line, so "nothing happened" is always inspectable.

There is deliberately **no state file**. Quota counters, level and "already
answered" bookkeeping are all derived from the ledger, because the fish must
not grow a second memory system next to the soul engine. When a reply is
actually published it is recorded as an ordinary recommendation-feedback
event, and the *existing* memory evolution takes it from there.

JSONL is deliberate: the ledger is inspection-first (users and support both
read it by hand), it must never fail a run because of a schema change, and a
lab feature does not deserve a migration in the 17k-line database module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .config import fish_home

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "ledger.jsonl"
REPORT_DIRNAME = "out"

# Level pacing is a product choice, not a calibrated threshold: one real
# published reply = 3 XP and one level every 12 XP, i.e. roughly four
# genuine comment-section conversations per level. The numbers are derived
# from the ledger, so retuning them re-labels history instead of rewriting it.
_XP_PER_LEVEL = 12
_XP_PER_PUBLISHED = 3


def _report_line(record: dict[str, Any], when: datetime) -> str:
    """Format one ledger row as a Markdown bullet."""

    stamp = when.astimezone().strftime("%H:%M")
    status = str(record.get("status") or "")
    title = str(record.get("video_title") or record.get("bvid") or "").strip() or "（无视频）"
    reply = " ".join(str(record.get("reply") or "").split())[:120]
    detail = f" · {reply}" if reply else ""
    error = str(record.get("error") or "").strip()
    suffix = f" · 失败原因：{error}" if error else ""
    bvid = str(record.get("bvid") or "").strip()
    link = f" · https://www.bilibili.com/video/{bvid}" if bvid else ""
    return f"- {stamp} [{status}] 《{title}》{detail}{suffix}{link}"


def level_for_xp(xp: int) -> int:
    """Map total XP onto a 1-based level."""
    return 1 + max(0, int(xp)) // _XP_PER_LEVEL


@dataclass(frozen=True)
class FishStats:
    """Everything the fish "is" today, derived from the ledger."""

    xp: int = 0
    level: int = 1
    published_total: int = 0
    today_published: int = 0
    # Owner-notification comments posted today. A separate counter from
    # ``today_published``: the notification has its own daily cap and does not
    # consume the reply quota, but it is a public comment all the same.
    today_notified: int = 0
    last_run_at: str = ""
    last_published_at: datetime | None = None
    # Trailing run of ``failed`` ledger rows. The engine refuses new publisher
    # runs while this is high and recent (see ``fish.guard``), so a broken
    # cookie / changed API cannot turn into a retry loop against B站.
    failure_streak: int = 0
    last_failure_at: datetime | None = None


@dataclass(frozen=True)
class FishLedgerView:
    """One ledger scan, shared by every check in a single run."""

    stats: FishStats
    answered: frozenset[str]
    per_video_total: dict[str, int]


class FishStore:
    """Filesystem-backed ledger with derived stats."""

    def __init__(self, data_dir: Path) -> None:
        self._home = fish_home(data_dir)
        self._ledger_path = self._home / LEDGER_FILENAME

    @property
    def home(self) -> Path:
        return self._home

    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    def append(self, record: dict[str, Any]) -> None:
        """Append one ledger record. Never raises into the run loop."""
        try:
            self._home.mkdir(parents=True, exist_ok=True)
            with open(self._ledger_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        except OSError as exc:
            logger.warning("Blue Fish ledger append failed: %s", exc)

    def append_report(self, record: dict[str, Any], *, when: datetime) -> None:
        """Append one human-readable line to ``out/<day>.md``.

        The ledger is the source of truth; the daily report is just the same
        act written for a person: one bullet per attempt, so "今天它做了什么"
        can be read without parsing JSONL. Never raises into the run loop.
        """
        day = when.date().isoformat()
        try:
            self._home.mkdir(parents=True, exist_ok=True)
            report_dir = self._home / REPORT_DIRNAME
            report_dir.mkdir(parents=True, exist_ok=True)
            path = report_dir / f"{day}.md"
            created = not path.exists()
            line = _report_line(record, when)
            with open(path, "a", encoding="utf-8") as handle:
                if created:
                    handle.write(f"# 大肥鱼日报 {day}\n\n")
                handle.write(line)
                handle.write("\n")
        except OSError as exc:
            logger.warning("Blue Fish report write failed: %s", exc)

    def report_path(self, day: date) -> Path:
        """Path of the human-readable report for ``day``."""

        return self._home / REPORT_DIRNAME / f"{day.isoformat()}.md"

    def read_report(self, day: date, *, max_chars: int = 8_000) -> str:
        """Return the daily report text (empty when nothing happened yet)."""

        path = self.report_path(day)
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Blue Fish report unreadable: %s", exc)
            return ""
        return text[-max_chars:] if len(text) > max_chars else text

    def read(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read ledger records, oldest first, skipping corrupt lines."""
        if not self._ledger_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with open(self._ledger_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except OSError as exc:
            logger.warning("Blue Fish ledger unreadable: %s", exc)
            return []
        if limit is not None and limit > 0:
            return rows[-limit:]
        return rows

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest ``limit`` records, newest first."""
        rows = self.read(limit=limit)
        rows.reverse()
        return rows

    def view(self, today: date) -> FishLedgerView:
        """Scan the ledger once and derive every per-run check from it."""
        rows = self.read()
        published = [row for row in rows if str(row.get("status") or "") == "published"]
        notified = [row for row in rows if str(row.get("status") or "") == "notified"]
        today_key = today.isoformat()

        answered: set[str] = set()
        per_video_total: dict[str, int] = {}
        published_today = 0
        notified_today = 0
        last_published_at: datetime | None = None
        for row in published:
            bvid = str(row.get("bvid") or "")
            rpid = str(row.get("target_rpid") or "")
            if bvid and rpid:
                answered.add(f"{bvid}:{rpid}")
            if bvid:
                per_video_total[bvid] = per_video_total.get(bvid, 0) + 1
            if str(row.get("day") or "") == today_key:
                published_today += 1
            parsed = _parse_dt(row.get("ts"))
            if parsed is not None:
                last_published_at = parsed
        for row in notified:
            bvid = str(row.get("bvid") or "")
            delivery = str(row.get("delivery") or "comment")
            if bvid and "comment" in delivery:
                # Only a notification that actually left a public comment
                # under the video counts toward ``per_video_limit``; a DM-only
                # notice never touched the comment section, so it must not
                # block the next reply under the same video.
                per_video_total[bvid] = per_video_total.get(bvid, 0) + 1
            if str(row.get("day") or "") == today_key:
                notified_today += 1
            parsed = _parse_dt(row.get("ts"))
            if parsed is not None and (last_published_at is None or parsed > last_published_at):
                # The min-interval gate is about public acts, not about replies
                # specifically.
                last_published_at = parsed

        xp = len(published) * _XP_PER_PUBLISHED
        last_run_at = ""
        for row in reversed(rows):
            candidate = str(row.get("ts") or "")
            if candidate:
                last_run_at = candidate
                break

        # Count failures since the last successful publish. Dry-run previews
        # and model ``skipped`` rows are transparent: they neither prove the
        # pipeline is healthy nor count as failures, so clicking "preview"
        # cannot mask a broken account and reset the breaker.
        failure_streak = 0
        last_failure_at: datetime | None = None
        for row in reversed(rows):
            status = str(row.get("status") or "")
            if status == "published":
                break
            if status != "failed":
                continue
            failure_streak += 1
            if last_failure_at is None:
                last_failure_at = _parse_dt(row.get("ts"))

        return FishLedgerView(
            stats=FishStats(
                xp=xp,
                level=level_for_xp(xp),
                published_total=len(published),
                today_published=published_today,
                today_notified=notified_today,
                last_run_at=last_run_at,
                last_published_at=last_published_at,
                failure_streak=failure_streak,
                last_failure_at=last_failure_at,
            ),
            answered=frozenset(answered),
            per_video_total=per_video_total,
        )


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
