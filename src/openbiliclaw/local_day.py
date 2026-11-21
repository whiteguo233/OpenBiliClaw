"""Local calendar-day helpers for spend accounting.

The LLM usage ledger stores ``timestamp`` in UTC (SQLite ``CURRENT_TIMESTAMP``),
while every user-facing spend number is a *local* calendar day. SQLite's
``date(timestamp, 'localtime')`` is the display convention used by
``openbiliclaw cost``, but it is not sargable against
``idx_llm_usage_timestamp``: a budget gate that runs before every provider
request must not full-scan the ledger.

:func:`local_day_window` converts the local day boundaries into UTC text so
callers can use a plain ``timestamp >= ? AND timestamp < ?`` range scan. The
format matches SQLite's ``CURRENT_TIMESTAMP`` exactly (``YYYY-MM-DD HH:MM:SS``),
which keeps lexicographic comparisons correct.

Midnight is resolved through ``time.mktime`` so DST transitions use the offset
that actually applied at each boundary. Computing both bounds from the current
offset is wrong on transition days: New York's fall-back day starts at 04:00
UTC and the next midnight is at 05:00 UTC.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

_SQLITE_UTC_FORMAT = "%Y-%m-%d %H:%M:%S"


def _local_midnight_utc(day: date) -> datetime:
    """Resolve local midnight for ``day`` to an aware UTC datetime."""

    struct = time.struct_time((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))
    epoch = time.mktime(struct)
    return datetime.fromtimestamp(epoch, UTC)


def local_day_window(now: datetime | None = None) -> tuple[str, str, str]:
    """Return ``(day_label, start_utc_text, end_utc_text)`` for the local day.

    ``day_label`` is the local ``YYYY-MM-DD`` used in logs / event metadata.
    ``start_utc_text`` is inclusive, ``end_utc_text`` exclusive, both in the
    UTC text form SQLite writes by default.

    A naive ``now`` is interpreted in the process-local timezone, matching
    ``datetime.now()``. Tests pass an aware datetime to pin the result.
    """

    current = now.astimezone() if now is not None else datetime.now().astimezone()
    local_day = current.date()
    start = _local_midnight_utc(local_day)
    end = _local_midnight_utc(local_day + timedelta(days=1))
    return (
        local_day.isoformat(),
        start.strftime(_SQLITE_UTC_FORMAT),
        end.strftime(_SQLITE_UTC_FORMAT),
    )
