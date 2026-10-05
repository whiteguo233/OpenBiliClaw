from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from openbiliclaw.recommendation.publication_preference import (
    PRESET_CUSTOM,
    PRESET_LAST_1_YEAR,
    PRESET_LAST_7_DAYS,
    PublicationDatePreference,
    evaluate_publication_preference,
    resolve_publication_window,
)

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def test_non_bilibili_content_is_neutral() -> None:
    decision = evaluate_publication_preference(
        source_platform="youtube",
        published_at="2000-01-01T00:00:00Z",
        preference=PublicationDatePreference(
            preset=PRESET_CUSTOM,
            start_date="2026-01-01",
            end_date="2026-12-31",
            weight=1.0,
        ),
        now=NOW,
        local_tz=CN_TZ,
    )

    assert decision.in_range is True
    assert decision.score_multiplier == 1.0
    assert decision.eligible is True


def test_all_dates_is_neutral_for_bilibili() -> None:
    decision = evaluate_publication_preference(
        source_platform="bili",
        published_at="2000-01-01T00:00:00Z",
        preference=PublicationDatePreference(),
        now=NOW,
        local_tz=CN_TZ,
    )

    assert decision.score_multiplier == 1.0
    assert decision.eligible is True


def test_custom_window_includes_local_start_and_end_dates() -> None:
    preference = PublicationDatePreference(
        preset=PRESET_CUSTOM,
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        weight=1.0,
    )
    start = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="2022-12-31T16:00:00Z",  # 2023-01-01 00:00 in China
        preference=preference,
        now=NOW,
        local_tz=CN_TZ,
    )
    end = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="2023-12-31T15:59:59Z",  # 2023-12-31 23:59:59 in China
        preference=preference,
        now=NOW,
        local_tz=CN_TZ,
    )

    assert start.in_range is True
    assert end.in_range is True
    assert start.score_multiplier == end.score_multiplier == 1.0


def test_soft_weight_penalizes_out_of_range_bilibili_content() -> None:
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="2022-12-31T15:59:59Z",
        preference=PublicationDatePreference(
            preset=PRESET_CUSTOM,
            start_date="2023-01-01",
            end_date="2023-12-31",
            weight=0.5,
        ),
        now=NOW,
        local_tz=CN_TZ,
    )

    assert decision.in_range is False
    assert decision.score_multiplier == 0.5
    assert decision.eligible is True


def test_strict_weight_excludes_out_of_range_and_missing_dates() -> None:
    preference = PublicationDatePreference(
        preset=PRESET_CUSTOM,
        start_date="2023-01-01",
        end_date="2023-12-31",
        weight=1.0,
    )
    for published_at in ("2022-12-31T15:59:59Z", "", "not-a-date"):
        decision = evaluate_publication_preference(
            source_platform="bilibili",
            published_at=published_at,
            preference=preference,
            now=NOW,
            local_tz=CN_TZ,
        )
        assert decision.in_range is False
        assert decision.score_multiplier == 0.0
        assert decision.eligible is False


def test_rolling_week_uses_local_calendar_days_and_utc_boundaries() -> None:
    preference = PublicationDatePreference(preset=PRESET_LAST_7_DAYS, weight=1.0)
    window = resolve_publication_window(preference, now=NOW, local_tz=CN_TZ)
    assert window is not None
    assert window.start_utc == datetime(2026, 8, 10, 16, 0, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 8, 17, 15, 59, 59, 999999, tzinfo=UTC)


def test_rolling_year_clamps_calendar_day() -> None:
    now = datetime(2024, 2, 29, 12, 0, tzinfo=UTC)
    preference = PublicationDatePreference(preset=PRESET_LAST_1_YEAR, weight=1.0)
    window = resolve_publication_window(preference, now=now, local_tz=UTC)
    assert window is not None
    assert window.start_utc == datetime(2023, 2, 28, tzinfo=UTC)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"preset": "bad"},
        {"preset": PRESET_CUSTOM},
        {"preset": PRESET_CUSTOM, "start_date": "2024-02-30"},
        {"preset": PRESET_CUSTOM, "start_date": "2024-03-02", "end_date": "2024-03-01"},
        {"weight": -0.01},
        {"weight": 1.01},
        {"weight": float("nan")},
        {"weight": True},
    ],
)
def test_invalid_preferences_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PublicationDatePreference(**kwargs)
