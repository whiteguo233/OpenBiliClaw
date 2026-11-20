"""Local calendar-day helpers for spend accounting.

The LLM usage ledger stores ``timestamp`` in UTC (SQLite ``CURRENT_TIMESTAMP``),
while every user-facing spend number is a *local* calendar day. SQLite's
``date(timestamp, 'localtime')`` is the display convention used by
``openbiliclaw cost``, but it is not sargable against
``idx_llm_usage_timestamp``: a budget gate that runs before every provider
request must not full-scan the ledger.

:func:`local_day_window` therefore converts the local day boundaries into UTC
text once, so callers can use a plain ``timestamp >= ? AND timestamp < ?``
range scan. The format matches SQLite's ``CURRENT_TIMESTAMP`` exactly
(``YYYY-MM-DD HH:MM:SS``), which keeps lexicographic comparisons correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_SQLITE_UTC_FORMAT = "%Y-%m-%d %H:%M:%S"


def local_day_window(now: datetime | None = None) -> tuple[str, str, str]:
    """Return ``(day_label, start_utc_text, end_utc_text)`` for the local day.

    ``day_label`` is the local ``YYYY-MM-DD`` used in logs / event metadata.
    ``start_utc_text`` is inclusive, ``end_utc_text`` exclusive, both in the
    UTC text form SQLite writes by default.

    A naive ``now`` is interpreted in the process-local timezone, matching
    ``datetime.now()``. Tests pass an aware datetime to pin the result.
    """

    current = now.astimezone() if now is not None else datetime.now().astimezone()
    start_local = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC).strftime(_SQLITE_UTC_FORMAT)
    end_utc = end_local.astimezone(UTC).strftime(_SQLITE_UTC_FORMAT)
    return start_local.date().isoformat(), start_utc, end_utc
