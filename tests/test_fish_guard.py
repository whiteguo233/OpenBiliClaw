"""Blue Fish safety gates — the checks that keep the fish from misbehaving."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openbiliclaw.fish.config import FishConfig
from openbiliclaw.fish.guard import (
    check_can_run,
    check_reply_text,
    mention_prefix,
    sanitize_reply,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _run(config: FishConfig, **overrides: object):
    kwargs: dict[str, object] = {
        "authenticated": True,
        "isolated": True,
        "today_published": 0,
        "last_published_at": None,
        "now": NOW,
        "dry_run": False,
        "manual": False,
    }
    kwargs.update(overrides)
    return check_can_run(config, **kwargs)  # type: ignore[arg-type]


def test_disabled_config_blocks_everything() -> None:
    result = _run(FishConfig(enabled=False))

    assert result.ok is False
    assert result.code == "disabled"


def test_missing_login_blocks_run() -> None:
    result = _run(FishConfig(enabled=True), authenticated=False)

    assert result.code == "not_logged_in"


def test_same_account_as_owner_blocks_run() -> None:
    result = _run(FishConfig(enabled=True), isolated=False)

    assert result.code == "account_not_isolated"


def test_daily_limit_blocks_run() -> None:
    result = _run(FishConfig(enabled=True, daily_limit=2), today_published=2)

    assert result.code == "daily_limit"


def test_min_interval_blocks_automatic_run() -> None:
    config = FishConfig(enabled=True, min_interval_minutes=30)
    result = _run(config, last_published_at=NOW - timedelta(minutes=10))

    assert result.code == "min_interval"
    assert "20 分钟" in result.message


def test_manual_run_ignores_min_interval() -> None:
    config = FishConfig(enabled=True, min_interval_minutes=30)
    result = _run(config, last_published_at=NOW - timedelta(minutes=10), manual=True)

    assert result.ok is True


def test_preview_skips_quota_and_interval() -> None:
    config = FishConfig(enabled=True, daily_limit=1, min_interval_minutes=60)
    result = _run(
        config,
        today_published=5,
        last_published_at=NOW - timedelta(minutes=1),
        dry_run=True,
    )

    assert result.ok is True


def test_reply_length_gate() -> None:
    config = FishConfig(max_chars=20)

    assert check_reply_text(config, "这一条刚刚好").ok is True
    too_long = check_reply_text(config, "这" * 21)
    assert too_long.code == "too_long"


def test_empty_reply_gate() -> None:
    assert check_reply_text(FishConfig(), "   ").code == "empty_reply"


def test_blocked_word_gate() -> None:
    config = FishConfig(blocked_words=["剧透"])

    assert check_reply_text(config, "这里有剧透").code == "blocked_word"


def test_sanitize_strips_markdown_quotes_and_whitespace() -> None:
    assert sanitize_reply('  "**太好笑了**"  ') == "太好笑了"
    assert sanitize_reply("第一行\n第二行") == "第一行 第二行"


def test_mention_prefix() -> None:
    assert mention_prefix("小白") == "@小白 "
    assert mention_prefix("   ") == ""


def test_failure_streak_opens_the_breaker() -> None:
    result = _run(
        FishConfig(enabled=True),
        failure_streak=3,
        last_failure_at=NOW - timedelta(minutes=10),
    )

    assert result.ok is False
    assert result.code == "failure_cooldown"
    assert "3 次" in result.message


def test_failure_breaker_expires_after_the_cooldown() -> None:
    result = _run(
        FishConfig(enabled=True),
        failure_streak=3,
        last_failure_at=NOW - timedelta(minutes=61),
    )

    assert result.ok is True


def test_manual_run_bypasses_the_failure_breaker() -> None:
    result = _run(
        FishConfig(enabled=True),
        failure_streak=4,
        last_failure_at=NOW - timedelta(minutes=1),
        manual=True,
    )

    assert result.ok is True


def test_dry_run_bypasses_the_failure_breaker() -> None:
    result = _run(
        FishConfig(enabled=True),
        failure_streak=5,
        last_failure_at=NOW - timedelta(minutes=1),
        dry_run=True,
    )

    assert result.ok is True


# ── active hours ───────────────────────────────────────────────────────


def test_active_hours_blocks_outside_the_window() -> None:
    config = FishConfig(enabled=True, active_hours="18:00-23:00")
    local_now = NOW.replace(hour=12, minute=0)

    result = _run(config, local_now=local_now)

    assert result.code == "outside_active_hours"


def test_active_hours_allows_inside_the_window() -> None:
    config = FishConfig(enabled=True, active_hours="18:00-23:00")
    local_now = NOW.replace(hour=20, minute=30)

    assert _run(config, local_now=local_now).ok is True


def test_overnight_window_wraps_midnight() -> None:
    config = FishConfig(enabled=True, active_hours="18:00-09:00")

    assert _run(config, local_now=NOW.replace(hour=23, minute=0)).ok is True
    assert _run(config, local_now=NOW.replace(hour=3, minute=0)).ok is True
    assert _run(config, local_now=NOW.replace(hour=12, minute=0)).code == "outside_active_hours"


def test_manual_run_ignores_active_hours() -> None:
    config = FishConfig(enabled=True, active_hours="18:00-23:00")

    result = _run(config, local_now=NOW.replace(hour=12), manual=True)

    assert result.ok is True


def test_empty_active_hours_means_all_day() -> None:
    config = FishConfig(enabled=True, active_hours="")

    assert _run(config, local_now=NOW.replace(hour=4)).ok is True
