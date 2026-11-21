"""Peak-hour deferral policy tests (PR #191 port, issue #353 follow-up)."""

from __future__ import annotations

from datetime import UTC, datetime

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.runtime.presence import (
    PresenceTracker,
    background_llm_work_allowed,
    manual_peak_active,
    now_in_peak_windows,
    parse_peak_windows,
    peak_deferral_active,
)
from openbiliclaw.runtime.refresh import ContinuousRefreshController


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


def _controller(**scheduler_kwargs: object) -> ContinuousRefreshController:
    controller = ContinuousRefreshController(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
        scheduler_config=SchedulerConfig(**scheduler_kwargs),  # type: ignore[arg-type]
        presence=PresenceTracker(),
    )
    controller._llm_budget_allowed = lambda: True  # type: ignore[method-assign]
    return controller


# ---------------------------------------------------------------------------
# Manual window parsing


def test_parse_peak_windows_converts_beijing_to_utc() -> None:
    assert parse_peak_windows("09:00-12:00,14:00-18:00", offset_minutes=480) == [
        (60, 240),
        (360, 600),
    ]


def test_parse_peak_windows_handles_wrap_and_rejects_garbage() -> None:
    assert parse_peak_windows("22:00-06:00", offset_minutes=0) == [(1320, 360)]
    assert parse_peak_windows("", offset_minutes=0) == []
    assert parse_peak_windows("not-a-window", offset_minutes=0) == []
    assert parse_peak_windows("12:00-12:00", offset_minutes=0) == []


def test_now_in_peak_windows_boundaries() -> None:
    windows = parse_peak_windows("09:00-12:00", offset_minutes=480)
    assert now_in_peak_windows(_utc(2026, 11, 23, 0, 59), windows) is False
    assert now_in_peak_windows(_utc(2026, 11, 23, 1, 0), windows) is True
    assert now_in_peak_windows(_utc(2026, 11, 23, 3, 59), windows) is True
    assert now_in_peak_windows(_utc(2026, 11, 23, 4, 0), windows) is False


def test_manual_peak_active_uses_configured_offset() -> None:
    scheduler = SchedulerConfig(
        peak_policy="defer",
        peak_hours="09:00-12:00",
        peak_timezone_offset_minutes=480,
    )
    assert manual_peak_active(scheduler, now_utc=_utc(2026, 11, 23, 2, 0)) is True
    assert manual_peak_active(scheduler, now_utc=_utc(2026, 11, 23, 12, 0)) is False


# ---------------------------------------------------------------------------
# Policy matrix


def test_policy_off_never_defers() -> None:
    scheduler = SchedulerConfig(peak_policy="off")
    assert peak_deferral_active(scheduler, pool_available=100, peak_active=True) is False


def test_defer_blocks_maintenance_and_healthy_refill() -> None:
    scheduler = SchedulerConfig(peak_policy="defer", peak_refill_floor=30)
    assert peak_deferral_active(scheduler, peak_active=True) is True
    assert peak_deferral_active(scheduler, pool_available=100, peak_active=True) is True
    assert peak_deferral_active(scheduler, pool_available=29, peak_active=True) is False


def test_defer_with_zero_floor_blocks_all_refill() -> None:
    scheduler = SchedulerConfig(peak_policy="defer", peak_refill_floor=0)
    assert peak_deferral_active(scheduler, pool_available=500, peak_active=True) is True


def test_prefer_keeps_refill_running_but_defers_maintenance() -> None:
    scheduler = SchedulerConfig(peak_policy="prefer", peak_refill_floor=30)
    assert peak_deferral_active(scheduler, pool_available=100, peak_active=True) is False
    assert peak_deferral_active(scheduler, peak_active=True) is True


def test_peak_active_false_never_defers() -> None:
    scheduler = SchedulerConfig(peak_policy="defer", peak_refill_floor=30)
    assert peak_deferral_active(scheduler, pool_available=100, peak_active=False) is False


def test_background_gate_keeps_presence_behavior_outside_peak() -> None:
    scheduler = SchedulerConfig(peak_policy="off", pause_on_extension_disconnect=False)
    presence = PresenceTracker()
    assert background_llm_work_allowed(scheduler, presence, peak_active=True) is True


def test_background_gate_blocks_during_peak() -> None:
    scheduler = SchedulerConfig(peak_policy="defer", peak_refill_floor=30)
    presence = PresenceTracker()
    assert (
        background_llm_work_allowed(
            scheduler,
            presence,
            pool_available=100,
            peak_active=True,
        )
        is False
    )
    assert (
        background_llm_work_allowed(
            scheduler,
            presence,
            pool_available=10,
            peak_active=True,
        )
        is True
    )


# ---------------------------------------------------------------------------
# Controller wiring


def test_controller_uses_injected_catalog_predicate() -> None:
    controller = _controller(peak_policy="defer", peak_refill_floor=30)
    controller.peak_tariff_predicate = lambda: True
    controller._peak_refill_pool_available = lambda: 100  # type: ignore[method-assign]
    assert controller._llm_work_allowed() is False
    assert controller._llm_work_allowed(maintenance=True) is False


def test_controller_allows_refill_below_the_floor() -> None:
    controller = _controller(peak_policy="defer", peak_refill_floor=30)
    controller.peak_tariff_predicate = lambda: True
    controller._peak_refill_pool_available = lambda: 5  # type: ignore[method-assign]
    assert controller._llm_work_allowed() is True
    assert controller._llm_work_allowed(maintenance=True) is False


def test_controller_prefer_mode_keeps_refill_fresh() -> None:
    controller = _controller(peak_policy="prefer", peak_refill_floor=30)
    controller.peak_tariff_predicate = lambda: True
    controller._peak_refill_pool_available = lambda: 100  # type: ignore[method-assign]
    assert controller._llm_work_allowed() is True
    assert controller._llm_work_allowed(maintenance=True) is False


def test_controller_predicate_failure_fails_open() -> None:
    controller = _controller(peak_policy="defer", peak_refill_floor=30)

    def _boom() -> bool:
        raise RuntimeError("catalog unavailable")

    controller.peak_tariff_predicate = _boom
    controller._peak_refill_pool_available = lambda: 100  # type: ignore[method-assign]
    assert controller._llm_work_allowed() is True


def test_controller_manual_windows_override_the_catalog_predicate() -> None:
    now = datetime.now(UTC)
    start_hour = (now.hour - 1) % 24
    end_hour = (now.hour + 2) % 24
    controller = _controller(
        peak_policy="defer",
        peak_hours=f"{start_hour:02d}:00-{end_hour:02d}:00",
        peak_timezone_offset_minutes=0,
        peak_refill_floor=30,
    )
    controller.peak_tariff_predicate = lambda: False
    controller._peak_refill_pool_available = lambda: 100  # type: ignore[method-assign]
    assert controller._llm_work_allowed() is False


def test_peak_config_round_trips(tmp_path) -> None:
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text(
        "[scheduler]\n"
        'peak_policy = "defer"\n'
        'peak_hours = "09:00-12:00,14:00-18:00"\n'
        "peak_timezone_offset_minutes = 480\n"
        "peak_refill_floor = 25\n",
        encoding="utf-8",
    )
    config = load_config(path, consult_environment=False)
    assert config.scheduler.peak_policy == "defer"
    assert config.scheduler.peak_hours == "09:00-12:00,14:00-18:00"
    assert config.scheduler.peak_timezone_offset_minutes == 480
    assert config.scheduler.peak_refill_floor == 25

    save_config(config, path)
    reloaded = load_config(path, consult_environment=False)
    assert reloaded.scheduler.peak_policy == "defer"
    assert reloaded.scheduler.peak_refill_floor == 25


def test_invalid_peak_config_falls_back_to_off(tmp_path) -> None:
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "[scheduler]\n"
        'peak_policy = "sometimes"\n'
        'peak_hours = "not-a-window"\n'
        "peak_refill_floor = -5\n",
        encoding="utf-8",
    )
    config = load_config(path, consult_environment=False)
    assert config.scheduler.peak_policy == "off"
    assert config.scheduler.peak_hours == ""
    assert config.scheduler.peak_refill_floor == 30


def test_scheduler_config_response_exposes_daily_and_peak_fields() -> None:
    """The API response model is an explicit whitelist; dropping a field makes
    the settings page show a blank and save the default back."""

    from openbiliclaw.api.models import SchedulerConfigOut

    out = SchedulerConfigOut()
    assert out.daily_budget_cny == 0.0
    assert out.budget_on_exceed == "pause"
    assert out.daily_budget_reserve_cny == 0.10
    assert out.peak_policy == "off"
    assert out.peak_hours == ""
    assert out.peak_timezone_offset_minutes == 480
    assert out.peak_refill_floor == 30


def test_manual_parser_rejects_out_of_range_minutes() -> None:
    assert parse_peak_windows("08:99-10:00", offset_minutes=0) == []
    assert parse_peak_windows("09:00-25:00", offset_minutes=0) == []
    assert parse_peak_windows("09:00-24:00", offset_minutes=0) == [(540, 0)]
