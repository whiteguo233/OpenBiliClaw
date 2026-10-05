from datetime import UTC, datetime, timedelta, timezone

from openbiliclaw.published_time import (
    PublishedTime,
    format_published_time,
    normalize_published_label,
    normalize_published_time,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_normalize_published_time_accepts_seconds_milliseconds_iso_and_rfc2822() -> None:
    expected = "2026-07-08T06:30:00Z"
    epoch = int(datetime(2026, 7, 8, 6, 30, tzinfo=UTC).timestamp())
    assert normalize_published_time(epoch, now=NOW).published_at == expected
    assert normalize_published_time(epoch * 1000, now=NOW).published_at == expected
    assert normalize_published_time("2026-07-08T14:30:00+08:00", now=NOW).published_at == expected
    assert (
        normalize_published_time("Wed, 08 Jul 2026 06:30:00 +0000", now=NOW).published_at
        == expected
    )
    assert normalize_published_time("20260708", now=NOW).published_at == "2026-07-08T00:00:00Z"


def test_normalize_published_time_accepts_early_unix_milliseconds() -> None:
    assert normalize_published_time(946_684_800_000, now=NOW).published_at == "2000-01-01T00:00:00Z"


def test_normalize_published_time_rejects_oversized_integer_without_raising() -> None:
    assert normalize_published_time(10**1000, label="  archived  ", now=NOW) == PublishedTime(
        "", "archived"
    )


def test_normalize_published_time_rejects_non_finite_numbers() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        assert normalize_published_time(value, label="保留标签", now=NOW) == PublishedTime(
            "", "保留标签"
        )


def test_normalize_published_time_rejects_dates_before_unix_epoch() -> None:
    assert normalize_published_time("1969-12-31T23:59:59Z", now=NOW) == PublishedTime()


def test_normalize_published_time_rejects_overflowing_timezone_conversion() -> None:
    assert normalize_published_time("0001-01-01T00:00:00+14:00", now=NOW) == PublishedTime()


def test_normalize_published_time_accepts_only_through_366_day_future_boundary() -> None:
    boundary = NOW + timedelta(days=366)
    assert normalize_published_time(boundary, now=NOW).published_at == boundary.isoformat().replace(
        "+00:00", "Z"
    )
    assert normalize_published_time(boundary + timedelta(seconds=1), now=NOW) == PublishedTime()


def test_normalize_published_time_keeps_safe_relative_label_only() -> None:
    assert normalize_published_time(label="  3   小时前  ") == PublishedTime("", "3 小时前")
    assert normalize_published_label("x" * 80) == "x" * 64
    for value in (None, "", "unknown", "未知", "N/A", True):
        assert normalize_published_time(value, label=value, now=NOW) == PublishedTime("", "")


def test_format_published_time_uses_local_time_and_relative_boundaries() -> None:
    local = timezone(timedelta(hours=8))
    assert format_published_time("2026-07-11T11:59:30Z", now=NOW, local_tz=local) == "刚刚"
    assert format_published_time("2026-07-11T11:30:00Z", now=NOW, local_tz=local) == "1 小时前"
    assert format_published_time("2026-07-09T12:00:00Z", now=NOW, local_tz=local) == "2 天前"
    assert format_published_time("2026-06-01T12:00:00Z", now=NOW, local_tz=local) == "6月1日"
    assert format_published_time("2025-06-01T12:00:00Z", now=NOW, local_tz=local) == "2025-06-01"
    assert format_published_time("", "2 years ago", now=NOW, local_tz=local) == "2 years ago"
    assert format_published_time("bad", "", now=NOW, local_tz=local) == ""


def test_format_published_time_never_emits_negative_relative_text() -> None:
    assert format_published_time("2026-07-11T12:03:00Z", now=NOW) == "刚刚"
    assert format_published_time("2026-07-20T12:00:00Z", now=NOW) == "7月20日"
