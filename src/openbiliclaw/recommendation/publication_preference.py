"""User publication-date preferences for recommendation scoring.

The policy is deliberately independent from discovery, storage, and UI layers.
It describes a user's preferred Bilibili publication window, while leaving
other platforms and the discovery relevance score untouched.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Final

from openbiliclaw.published_time import normalize_published_time
from openbiliclaw.sources.platforms import PLATFORM_BILIBILI, normalize_source_platform

PRESET_ALL: Final = "all"
PRESET_LAST_7_DAYS: Final = "last_7_days"
PRESET_LAST_30_DAYS: Final = "last_30_days"
PRESET_LAST_6_MONTHS: Final = "last_6_months"
PRESET_LAST_1_YEAR: Final = "last_1_year"
PRESET_CUSTOM: Final = "custom"

SUPPORTED_PRESETS: Final = frozenset(
    {
        PRESET_ALL,
        PRESET_LAST_7_DAYS,
        PRESET_LAST_30_DAYS,
        PRESET_LAST_6_MONTHS,
        PRESET_LAST_1_YEAR,
        PRESET_CUSTOM,
    }
)

# 兼容早期设计稿和手工配置中的同义写法；保存时仍应输出上面的规范值。
_PRESET_ALIASES: Final = {
    "unlimited": PRESET_ALL,
    "none": PRESET_ALL,
    "recent_7_days": PRESET_LAST_7_DAYS,
    "recent_30_days": PRESET_LAST_30_DAYS,
    "recent_6_months": PRESET_LAST_6_MONTHS,
    "recent_1_year": PRESET_LAST_1_YEAR,
}


def _parse_date(value: date | str | None, *, field_name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc


def _normalize_preset(value: object) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _PRESET_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_PRESETS:
        supported = ", ".join(sorted(SUPPORTED_PRESETS))
        raise ValueError(f"preset must be one of: {supported}")
    return normalized


def _parse_weight(value: float | int | str) -> float:
    """Validate and normalize a user-configured score penalty."""

    if isinstance(value, bool):
        raise ValueError("weight must be a finite number in [0, 1]")
    try:
        weight = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("weight must be a finite number in [0, 1]") from exc
    if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be a finite number in [0, 1]")
    return weight


@dataclass(frozen=True, slots=True)
class PublicationDatePreference:
    """Validated Bilibili publication-date preference.

    ``weight`` controls the penalty for out-of-window content.  A value of
    ``1`` is strict mode; a value below ``1`` keeps out-of-window content
    eligible but lowers its score.  ``all`` disables the date policy.
    """

    preset: str = PRESET_ALL
    start_date: date | None = None
    end_date: date | None = None
    weight: float = 0.5

    def __init__(
        self,
        preset: str = PRESET_ALL,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        weight: float = 0.5,
    ) -> None:
        normalized_preset = _normalize_preset(preset)
        start = _parse_date(start_date, field_name="start_date")
        end = _parse_date(end_date, field_name="end_date")
        normalized_weight = _parse_weight(weight)
        self._validate_normalized_values(start, end, preset=normalized_preset)
        object.__setattr__(self, "preset", normalized_preset)
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "weight", normalized_weight)

    @staticmethod
    def _validate_normalized_values(
        start: date | None,
        end: date | None,
        *,
        preset: str,
    ) -> None:
        if start is not None and end is not None and start > end:
            raise ValueError("start_date must be on or before end_date")
        if preset == PRESET_CUSTOM and start is None and end is None:
            raise ValueError("custom preset requires start_date or end_date")


@dataclass(frozen=True, slots=True)
class PublicationDateWindow:
    """Inclusive UTC publication window; either boundary may be open."""

    start_utc: datetime | None
    end_utc: datetime | None

    def contains(self, published_at: datetime) -> bool:
        """Return whether an aware timestamp falls inside this window."""

        if published_at.tzinfo is None:
            return False
        timestamp = published_at.astimezone(UTC)
        if self.start_utc is not None and timestamp < self.start_utc:
            return False
        if self.end_utc is None:
            return True
        return timestamp <= self.end_utc


@dataclass(frozen=True, slots=True)
class PublicationDateDecision:
    """Outcome used by recommendation scoring and strict serving gates."""

    in_range: bool
    score_multiplier: float
    eligible: bool


def _local_timezone(now: datetime, local_tz: tzinfo | None) -> tzinfo:
    if local_tz is not None:
        return local_tz
    return now.astimezone().tzinfo or UTC


def _shift_months(value: date, months: int) -> date:
    """Shift a calendar date while clamping the day to the target month."""

    absolute_month = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute_month, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _window_for_dates(
    start_date: date | None,
    end_date: date | None,
    *,
    local_tz: tzinfo,
) -> PublicationDateWindow:
    # 用户输入的是本地自然日：起点取当天 00:00，终点取当天最后一微秒，
    # 再统一转成 UTC，避免东八区日期在 UTC 零点处错开一天。
    start_utc: datetime | None = None
    if start_date is not None:
        start_utc = datetime.combine(start_date, time.min, tzinfo=local_tz).astimezone(UTC)

    end_utc: datetime | None = None
    if end_date is not None:
        end_utc = datetime.combine(end_date, time.max, tzinfo=local_tz).astimezone(UTC)

    return PublicationDateWindow(start_utc=start_utc, end_utc=end_utc)


def _parse_published_datetime(value: object, *, now: datetime) -> datetime | None:
    """Normalize a candidate publication value and return an aware UTC datetime."""

    normalized = normalize_published_time(value, now=now)
    if not normalized.published_at:
        return None
    return datetime.fromisoformat(normalized.published_at.replace("Z", "+00:00"))


def resolve_publication_window(
    preference: PublicationDatePreference,
    *,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> PublicationDateWindow | None:
    """Resolve a preset/custom range into inclusive UTC boundaries.

    Rolling presets use the current local calendar date.  For example,
    ``last_7_days`` includes today and the six preceding local dates.
    """

    current = (now or datetime.now(UTC)).astimezone(UTC)
    timezone = _local_timezone(current, local_tz)
    local_today = current.astimezone(timezone).date()

    if preference.preset == PRESET_ALL:
        return None
    if preference.preset == PRESET_CUSTOM:
        return _window_for_dates(
            preference.start_date,
            preference.end_date,
            local_tz=timezone,
        )

    # “最近 N 天”包含今天，所以分别向前取 N-1 天；半年/一年按日历月移动，
    # 例如闰年 2 月 29 日回退一年时会安全收敛到 2 月 28 日。
    if preference.preset == PRESET_LAST_7_DAYS:
        start = local_today - timedelta(days=6)
    elif preference.preset == PRESET_LAST_30_DAYS:
        start = local_today - timedelta(days=29)
    elif preference.preset == PRESET_LAST_6_MONTHS:
        start = _shift_months(local_today, -6)
    elif preference.preset == PRESET_LAST_1_YEAR:
        start = _shift_months(local_today, -12)
    else:  # pragma: no cover - PublicationDatePreference validates presets.
        raise AssertionError(f"unsupported preset: {preference.preset}")
    return _window_for_dates(start, local_today, local_tz=timezone)


def _evaluate_window(
    *,
    published_at: object,
    preference: PublicationDatePreference,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> PublicationDateDecision:
    """Evaluate a candidate against an active date window."""

    window = resolve_publication_window(preference, now=now, local_tz=local_tz)
    if window is None:
        return PublicationDateDecision(in_range=True, score_multiplier=1.0, eligible=True)

    current = (now or datetime.now(UTC)).astimezone(UTC)
    published = _parse_published_datetime(published_at, now=current)
    # 缺失或无法解析的发布时间按“范围外”处理，不能用发现/缓存时间冒充。
    in_range = published is not None and window.contains(published)
    # 范围内不改分；范围外乘 (1-weight)。weight=1 时还要显式判为不可服务，
    # 不能只依赖零分自然沉底，否则候选不足时仍可能被选中。
    multiplier = 1.0 if in_range else 1.0 - preference.weight
    eligible = in_range or preference.weight < 1.0
    return PublicationDateDecision(
        in_range=in_range,
        score_multiplier=multiplier,
        eligible=eligible,
    )


def evaluate_publication_preference(
    *,
    source_platform: object,
    published_at: object,
    preference: PublicationDatePreference,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> PublicationDateDecision:
    """Evaluate a Bilibili candidate without mutating its relevance score.

    Non-Bilibili candidates are always neutral (historical pool-scoring
    contract). Missing or malformed publication timestamps are treated as
    out of range only when a Bilibili date window is active.
    """

    platform = normalize_source_platform(source_platform)
    # 池子评分/库存统计沿用“只作用于 B 站”的历史契约。
    if platform != PLATFORM_BILIBILI:
        return PublicationDateDecision(in_range=True, score_multiplier=1.0, eligible=True)

    return _evaluate_window(
        published_at=published_at,
        preference=preference,
        now=now,
        local_tz=local_tz,
    )


def evaluate_source_publication_preference(
    *,
    published_at: object,
    preference: PublicationDatePreference,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> PublicationDateDecision:
    """Evaluate a candidate for a caller-selected source date preference.

    Discovery strategies pass only candidates from their own source and use
    this variant before LLM evaluation, so out-of-window items are filtered
    out before spending any evaluation budget.
    """

    return _evaluate_window(
        published_at=published_at,
        preference=preference,
        now=now,
        local_tz=local_tz,
    )
