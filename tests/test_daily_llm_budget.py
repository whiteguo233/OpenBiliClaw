"""Daily money cap for LLM spend (issue #353).

The call-count quota (issue #344) cannot bound a bill: 120 short evaluations
and 120 long generations differ by roughly 10x. These tests lock in:

1. local-calendar-day boundaries (not rolling 24h) for the spend query;
2. one ``budget_exceeded`` event per day, shared across ledgers;
3. ``pause`` refusing background calls while interactive chat keeps working;
4. ``stop`` refusing interactive calls too, and ``warn`` allowing everything;
5. the calibrated estimate (``llm.cost_multiplier``) actually reaching
   ``llm_usage.estimated_cost_cny``;
6. diagnostics classifying a daily stop as ``budget_exceeded``, not
   ``rate_limited``.
"""

from __future__ import annotations

import sqlite3
import types
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import (
    LLMDailyBudgetExceededError,
    LLMResponse,
    _classify_llm_error_code,
)
from openbiliclaw.llm.budget import DailySpendLedger
from openbiliclaw.llm.concurrency import LLMConcurrencyGate, apply_background_budget_config
from openbiliclaw.llm.pricing import PRICING, estimate_cost
from openbiliclaw.llm.usage_recorder import UsageRecorder
from openbiliclaw.local_day import local_day_window
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def usage_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "usage.db")
    db.initialize()
    return db


def _insert_spend(db: Database, *, when: datetime, cost: float) -> int:
    db.conn.execute(
        "INSERT INTO llm_usage (timestamp, provider, model, estimated_cost_cny) "
        "VALUES (?, ?, ?, ?)",
        (_utc_text(when), "deepseek", "deepseek-v4-flash", cost),
    )
    db.conn.commit()
    return int(db.max_llm_usage_id())


# ---------------------------------------------------------------------------
# Local day window


def test_local_day_window_uses_local_calendar_midnight() -> None:
    """Pin the process timezone so UTC CI and UTC+8 dev agree."""

    import os
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "from datetime import datetime, timedelta, timezone\n"
        "from openbiliclaw.local_day import local_day_window\n"
        "print(*local_day_window(datetime(2026, 3, 1, 12, tzinfo=timezone(timedelta(hours=8)))))\n"
    )
    env = dict(os.environ)
    env["TZ"] = "Asia/Shanghai"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    day, start_date, start_time, end_date, end_time = result.stdout.split()
    assert day == "2026-03-01"
    # UTC+8 midnight == 16:00 UTC the previous day.
    assert f"{start_date} {start_time}" == "2026-02-28 16:00:00"
    assert f"{end_date} {end_time}" == "2026-03-01 16:00:00"


def test_local_day_window_is_not_a_rolling_24h_window() -> None:
    """A 'yesterday 23:00 local' row must fall outside today's window."""

    _, start_utc, _ = local_day_window(_FIXED_NOW)
    yesterday_late = _utc_text(_FIXED_NOW - timedelta(hours=13))
    assert yesterday_late < start_utc


# ---------------------------------------------------------------------------
# Spend query + ledger


def test_daily_ledger_sums_only_the_local_day(usage_db: Database, tmp_path: Path) -> None:
    db_path = usage_db._db_path
    now = datetime.now().astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _insert_spend(usage_db, when=now - timedelta(seconds=1), cost=0.25)
    _insert_spend(usage_db, when=day_start - timedelta(minutes=1), cost=9.0)
    _insert_spend(usage_db, when=day_start - timedelta(days=1), cost=9.0)

    ledger = DailySpendLedger(db_path)
    assert ledger.spend_cny() == pytest.approx(0.25, rel=1e-9)


def test_daily_ledger_fails_open_without_usage_table(tmp_path: Path) -> None:
    ledger = DailySpendLedger(tmp_path / "empty.db")
    assert ledger.spend_cny() == 0.0


def test_record_exceeded_writes_one_event_per_day(usage_db: Database) -> None:
    ledger = DailySpendLedger(usage_db._db_path, now=lambda: _FIXED_NOW)

    assert ledger.record_exceeded(spend_cny=1.5, limit_cny=1.0, on_exceed="pause") is True
    assert ledger.record_exceeded(spend_cny=1.6, limit_cny=1.0, on_exceed="pause") is False

    rows = usage_db.conn.execute(
        "SELECT event_type, metadata FROM events WHERE event_type = 'budget_exceeded'"
    ).fetchall()
    assert len(rows) == 1
    assert '"limit_cny": 1.0' in str(rows[0]["metadata"])
    assert '"mode": "pause"' in str(rows[0]["metadata"])


# ---------------------------------------------------------------------------
# Gate admission


def _gate(tmp_path: Path, *, limit: float, mode: str, db: Database) -> LLMConcurrencyGate:
    return LLMConcurrencyGate(
        4,
        daily_budget_cny=limit,
        daily_budget_on_exceed=mode,
        daily_budget_ledger=DailySpendLedger(db._db_path, now=lambda: _FIXED_NOW),
    )


async def test_pause_refuses_background_but_keeps_interactive(
    usage_db: Database, tmp_path: Path
) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.02)
    gate = _gate(tmp_path, limit=0.01, mode="pause", db=usage_db)

    with pytest.raises(LLMDailyBudgetExceededError) as excinfo:
        async with gate.slot(caller="soul.insight"):
            pass
    assert "scheduler.daily_budget_cny" in str(excinfo.value)

    # Interactive (user-initiated) chat is deliberately still admitted.
    async with gate.slot(caller="soul.dialogue"):
        pass

    snapshot = gate.daily_budget_snapshot()
    assert snapshot["llm_daily_budget_state"] == "exceeded"
    assert snapshot["llm_today_cost_cny"] == pytest.approx(0.02)


async def test_stop_refuses_interactive_calls_too(usage_db: Database, tmp_path: Path) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.02)
    gate = _gate(tmp_path, limit=0.01, mode="stop", db=usage_db)

    with pytest.raises(LLMDailyBudgetExceededError):
        async with gate.slot(caller="soul.dialogue"):
            pass


async def test_warn_allows_all_calls_and_writes_the_event(
    usage_db: Database, tmp_path: Path
) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.02)
    gate = _gate(tmp_path, limit=0.01, mode="warn", db=usage_db)

    async with gate.slot(caller="soul.insight"):
        pass
    async with gate.slot(caller="soul.dialogue"):
        pass

    count = usage_db.conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE event_type = 'budget_exceeded'"
    ).fetchone()["c"]
    assert count == 1


async def test_loop_gate_records_event_without_attempting_a_call(
    usage_db: Database, tmp_path: Path
) -> None:
    """A controller that pauses before any request must still leave the event."""

    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.02)
    gate = _gate(tmp_path, limit=0.01, mode="pause", db=usage_db)

    gate.record_daily_budget_exceeded()
    gate.record_daily_budget_exceeded()

    count = usage_db.conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE event_type = 'budget_exceeded'"
    ).fetchone()["c"]
    assert count == 1


async def test_disabled_cap_admits_everything(usage_db: Database, tmp_path: Path) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=100.0)
    gate = _gate(tmp_path, limit=0.0, mode="pause", db=usage_db)

    for _ in range(3):
        async with gate.slot(caller="soul.insight"):
            pass

    snapshot = gate.daily_budget_snapshot()
    assert snapshot["llm_daily_budget_state"] == "disabled"
    # Disabled caps promise no ledger read, so today's display stays zero.
    assert snapshot["llm_today_cost_cny"] == 0.0


def test_apply_background_budget_config_wires_daily_cap(tmp_path: Path) -> None:
    import types

    gate = LLMConcurrencyGate(4)
    config = types.SimpleNamespace(
        scheduler=types.SimpleNamespace(
            llm_budget_max_calls=7,
            llm_budget_window_seconds=1800,
            daily_budget_cny=3.5,
            budget_on_exceed="warn",
        ),
        data_path=tmp_path,
    )

    apply_background_budget_config(gate, config)  # type: ignore[arg-type]

    assert gate.daily_budget_cny == 3.5
    assert gate.daily_budget_on_exceed == "warn"
    payload = gate.status_payload()
    assert payload["llm_daily_budget_cny"] == 3.5
    assert payload["llm_daily_budget_on_exceed"] == "warn"
    assert payload["llm_daily_budget_state"] in {"ok", "exceeded"}


# ---------------------------------------------------------------------------
# Estimate calibration + diagnostics


def test_openai_compatible_relay_has_an_explicit_default_rate() -> None:
    """The missing row silently priced every relay model at the generic rate."""

    assert "default" in PRICING["openai_compatible"]
    assert estimate_cost("openai_compatible", "glm-5.3-flash", 1000, 1000) > 0


def test_cost_multiplier_scales_the_whole_estimate() -> None:
    base = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    calibrated = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000, cost_multiplier=2.0)
    assert calibrated == pytest.approx(base * 2.0, rel=1e-9)


def test_usage_recorder_persists_calibrated_cost(usage_db: Database) -> None:
    recorder = UsageRecorder(sink=usage_db, cost_multiplier=2.0)
    recorder.record(
        LLMResponse(
            content="ok",
            provider="deepseek",
            model="deepseek-v4-flash",
            usage={"prompt_tokens": 5000, "completion_tokens": 3000},
        ),
        caller="discovery.evaluate_batch",
    )

    row = usage_db.conn.execute(
        "SELECT estimated_cost_cny FROM llm_usage ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert float(row["estimated_cost_cny"]) == pytest.approx(0.03672, rel=1e-6)


def test_daily_budget_error_is_classified_as_budget_exceeded() -> None:
    assert _classify_llm_error_code(LLMDailyBudgetExceededError("stop")) == "budget_exceeded"


def test_query_llm_usage_today_matches_daily_ledger(usage_db: Database) -> None:
    now = datetime.now().astimezone()
    _insert_spend(usage_db, when=now, cost=0.01)
    _insert_spend(usage_db, when=now - timedelta(days=2), cost=5.0)

    today = usage_db.query_llm_usage_today()
    assert today["calls"] == 1
    assert today["cost_cny"] == pytest.approx(0.01)
    assert today["day"] == now.date().isoformat()


def test_controller_loop_gate_pauses_on_daily_exceeded() -> None:
    """The controller must stop *before* repeatedly triggering gate raises."""

    import types

    from openbiliclaw.runtime.refresh import ContinuousRefreshController

    class _Gate:
        def __init__(self, state: str, mode: str = "pause") -> None:
            self._state = state
            self._mode = mode

        def daily_budget_snapshot(self) -> dict[str, object]:
            return {
                "llm_daily_budget_cny": 10.0,
                "llm_daily_budget_on_exceed": self._mode,
                "llm_today_cost_cny": 12.0,
                "llm_daily_budget_state": self._state,
                "llm_daily_budget_day": "2026-03-01",
            }

        def background_call_count(self, _window: int) -> int:
            return 0

        def record_daily_budget_exceeded(self) -> None:
            return None

    class _Stub:
        _last_daily_budget_warned_at = -float("inf")
        _last_llm_budget_warned_at = -float("inf")
        scheduler_config = types.SimpleNamespace(
            llm_budget_max_calls=0,
            llm_budget_window_seconds=3600,
        )

        def __init__(self, gate: _Gate) -> None:
            self.llm_concurrency_gate = gate
            # Bind the real helpers so the combined gate has every method it
            # reaches for; only the dataclass state is stubbed.
            for name in ("_call_budget_allowed", "_daily_budget_allowed"):
                setattr(
                    self,
                    name,
                    types.MethodType(getattr(ContinuousRefreshController, name), self),
                )

    paused = _Stub(_Gate("exceeded", "pause"))
    assert ContinuousRefreshController._daily_budget_allowed(paused) is False
    assert ContinuousRefreshController._llm_budget_allowed(paused) is False

    warning = _Stub(_Gate("exceeded", "warn"))
    assert ContinuousRefreshController._daily_budget_allowed(warning) is True

    ok = _Stub(_Gate("ok"))
    assert ContinuousRefreshController._llm_budget_allowed(ok) is True


# ---------------------------------------------------------------------------
# Config parse / save round-trip


def test_daily_budget_and_multiplier_round_trip(tmp_path: Path) -> None:
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text(
        '[scheduler]\ndaily_budget_cny = 12.5\nbudget_on_exceed = "stop"\n'
        "daily_budget_reserve_cny = 0.25\n"
        "[llm]\ncost_multiplier = 1.8\n",
        encoding="utf-8",
    )
    config = load_config(path, consult_environment=False)
    assert config.scheduler.daily_budget_cny == pytest.approx(12.5)
    assert config.scheduler.budget_on_exceed == "stop"
    assert config.scheduler.daily_budget_reserve_cny == pytest.approx(0.25)
    assert config.llm.cost_multiplier == pytest.approx(1.8)

    save_config(config, path)
    reloaded = load_config(path, consult_environment=False)
    assert reloaded.scheduler.daily_budget_cny == pytest.approx(12.5)
    assert reloaded.scheduler.budget_on_exceed == "stop"
    assert reloaded.scheduler.daily_budget_reserve_cny == pytest.approx(0.25)
    assert reloaded.llm.cost_multiplier == pytest.approx(1.8)


def test_invalid_budget_values_fall_back_to_safe_defaults(tmp_path: Path) -> None:
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        '[scheduler]\ndaily_budget_cny = -3\nbudget_on_exceed = "explode"\n'
        "[llm]\ncost_multiplier = 0\n",
        encoding="utf-8",
    )
    config = load_config(path, consult_environment=False)

    assert config.scheduler.daily_budget_cny == 0.0
    assert config.scheduler.budget_on_exceed == "pause"
    assert config.llm.cost_multiplier == 1.0


async def test_reservations_stop_concurrent_calls_from_sharing_the_cap(
    usage_db: Database, tmp_path: Path
) -> None:
    """Two in-flight calls must not both read the same accrued spend."""

    gate = LLMConcurrencyGate(
        4,
        daily_budget_cny=0.15,
        daily_budget_on_exceed="pause",
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=DailySpendLedger(usage_db._db_path),
    )

    async with gate.slot(caller="soul.insight"):
        assert gate.daily_budget_snapshot()["llm_daily_budget_reserved_cny"] == pytest.approx(0.10)
        with pytest.raises(LLMDailyBudgetExceededError) as excinfo:
            async with gate.slot(caller="soul.insight"):
                pass
        assert "reserved in flight" in str(excinfo.value)

    # The first call released its reservation; the next one is admitted.
    async with gate.slot(caller="soul.insight"):
        pass
    assert gate.daily_budget_snapshot()["llm_daily_budget_reserved_cny"] == pytest.approx(0.0)


async def test_warn_mode_does_not_reserve(usage_db: Database, tmp_path: Path) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.02)
    gate = _gate(tmp_path, limit=0.01, mode="warn", db=usage_db)
    gate.configure_daily_budget(limit_cny=0.01, on_exceed="warn", reserve_cny=0.10)
    async with gate.slot(caller="soul.insight"):
        assert gate.daily_budget_snapshot()["llm_daily_budget_reserved_cny"] == pytest.approx(0.0)


async def test_stop_mode_reserves_interactive_calls_too(usage_db: Database, tmp_path: Path) -> None:
    gate = _gate(tmp_path, limit=0.15, mode="stop", db=usage_db)
    gate.configure_daily_budget(limit_cny=0.15, on_exceed="stop", reserve_cny=0.10)
    async with gate.slot(caller="soul.dialogue"):
        with pytest.raises(LLMDailyBudgetExceededError):
            async with gate.slot(caller="soul.dialogue"):
                pass


def test_local_day_window_uses_each_boundary_dst_offset(tmp_path) -> None:
    """Fall-back days are 25 hours long; both midnights need their own offset.

    ``time.tzset`` is unavailable in this Python build, so run the probe in a
    child process that starts with ``TZ=America/New_York`` (libc's mktime
    picks it up at startup).
    """

    import os
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "from datetime import datetime, timezone\n"
        "from openbiliclaw.local_day import local_day_window\n"
        "print(*local_day_window(datetime(2026, 11, 1, 12, tzinfo=timezone.utc)))\n"
    )
    env = dict(os.environ)
    env["TZ"] = "America/New_York"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    day, start_date, start_time, end_date, end_time = result.stdout.split()
    assert day == "2026-11-01"
    # EDT midnight → 04:00 UTC; the next midnight is EST → 05:00 UTC.
    assert f"{start_date} {start_time}" == "2026-11-01 04:00:00"
    assert f"{end_date} {end_time}" == "2026-11-02 05:00:00"


def test_public_failure_classifier_distinguishes_local_budget_from_429() -> None:
    from openbiliclaw.llm.base import (
        LLMBudgetExhaustedError,
        classify_llm_failure_kind,
        classify_llm_unavailability,
        safe_llm_failure_message,
    )

    daily = LLMDailyBudgetExceededError("daily cap")
    assert classify_llm_failure_kind(daily) == "budget_exhausted"
    assert classify_llm_unavailability(daily) is None
    message = safe_llm_failure_message(daily)
    assert "本地" in message
    assert "429" not in message

    call_count = LLMBudgetExhaustedError("call cap")
    assert classify_llm_failure_kind(call_count) == "budget_exhausted"
    assert "429" not in safe_llm_failure_message(call_count)


async def test_shared_reservation_ledger_blocks_a_sibling_gate(usage_db: Database) -> None:
    """Two gates on the same database must share in-flight reservations."""

    first_ledger = DailySpendLedger(usage_db._db_path)
    second_ledger = DailySpendLedger(usage_db._db_path)
    first = LLMConcurrencyGate(
        2,
        daily_budget_cny=0.15,
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=first_ledger,
    )
    second = LLMConcurrencyGate(
        2,
        daily_budget_cny=0.15,
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=second_ledger,
    )

    async with first.slot(caller="soul.insight"):
        with pytest.raises(LLMDailyBudgetExceededError):
            async with second.slot(caller="soul.insight"):
                pass

    async with second.slot(caller="soul.insight"):
        pass


async def test_explicit_zero_reservation_is_honored(usage_db: Database) -> None:
    gate = LLMConcurrencyGate(
        2,
        daily_budget_cny=10.0,
        daily_budget_reserve_cny=0.0,
        daily_budget_ledger=DailySpendLedger(usage_db._db_path),
    )
    async with gate.slot(caller="soul.insight"):
        assert gate.daily_budget_snapshot()["llm_daily_budget_reserved_cny"] == pytest.approx(0.0)


async def test_partial_headroom_stops_the_controller_before_admission(usage_db: Database) -> None:
    gate = LLMConcurrencyGate(
        4,
        daily_budget_cny=0.15,
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=DailySpendLedger(usage_db._db_path, now=lambda: _FIXED_NOW),
    )
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.06)
    snapshot = gate.daily_budget_snapshot()
    assert snapshot["llm_daily_budget_remaining_cny"] == pytest.approx(0.09)
    assert snapshot["llm_daily_budget_state"] == "exceeded"
    assert gate.daily_budget_allowed() is False
    # No actual spend event is written for a reservation-only stop.
    gate.record_daily_budget_exceeded()
    with sqlite3.connect(usage_db._db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'budget_exceeded'"
        ).fetchone()[0]
    assert count == 0


async def test_config_wiring_keeps_an_explicit_zero_reserve(tmp_path: Path) -> None:
    from openbiliclaw.llm.concurrency import apply_background_budget_config

    config = types.SimpleNamespace(
        scheduler=types.SimpleNamespace(
            daily_budget_cny=1.0,
            budget_on_exceed="pause",
            daily_budget_reserve_cny=0.0,
            llm_budget_max_calls=0,
            llm_budget_window_seconds=3600,
        ),
        data_path=tmp_path,
    )
    gate = LLMConcurrencyGate(2)
    apply_background_budget_config(gate, config)
    async with gate.slot(caller="soul.insight"):
        assert gate.daily_budget_snapshot()["llm_daily_budget_reserved_cny"] == pytest.approx(0.0)


async def test_missing_sqlite_tables_fall_back_instead_of_stopping(tmp_path: Path) -> None:
    gate = LLMConcurrencyGate(
        2,
        daily_budget_cny=1.0,
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=DailySpendLedger(tmp_path / "fresh.db"),
    )
    async with gate.slot(caller="soul.insight"):
        pass


async def test_audit_uses_raw_spend_not_the_rounded_display(usage_db: Database) -> None:
    ledger = DailySpendLedger(usage_db._db_path, now=lambda: _FIXED_NOW)
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.9999996)
    gate = LLMConcurrencyGate(
        2,
        daily_budget_cny=1.0,
        daily_budget_reserve_cny=0.0,
        daily_budget_ledger=ledger,
    )
    gate.record_daily_budget_exceeded()
    with sqlite3.connect(usage_db._db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'budget_exceeded'"
        ).fetchone()[0]
    assert count == 0


def test_audit_write_failure_is_retryable(tmp_path: Path) -> None:
    ledger = DailySpendLedger(tmp_path / "no-events.db")
    assert ledger.record_exceeded(spend_cny=1.2, limit_cny=1.0, on_exceed="pause") is None


def test_warn_mode_does_not_report_exceeded_before_actual_spend(
    usage_db: Database, tmp_path: Path
) -> None:
    _insert_spend(usage_db, when=_FIXED_NOW, cost=0.95)
    gate = _gate(tmp_path, limit=1.0, mode="warn", db=usage_db)
    snapshot = gate.daily_budget_snapshot()
    assert snapshot["llm_daily_budget_state"] == "ok"
    assert snapshot["llm_daily_budget_remaining_cny"] == pytest.approx(0.05)


async def test_switching_to_warn_with_an_active_hold_is_not_exceeded(
    usage_db: Database,
) -> None:
    gate = LLMConcurrencyGate(
        2,
        daily_budget_cny=0.10,
        daily_budget_reserve_cny=0.10,
        daily_budget_ledger=DailySpendLedger(usage_db._db_path),
    )
    async with gate.slot(caller="soul.insight"):
        gate.configure_daily_budget(limit_cny=0.10, on_exceed="warn", reserve_cny=0.10)
        snapshot = gate.daily_budget_snapshot()
        assert snapshot["llm_daily_budget_state"] == "ok"
        # The hold is still active; warn mode just doesn't count it as spend.
        assert snapshot["llm_daily_budget_reserved_cny"] == pytest.approx(0.10)
        assert snapshot["llm_daily_budget_remaining_cny"] == pytest.approx(0.0)


def test_nan_cost_multiplier_falls_back_to_one(tmp_path: Path) -> None:
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.usage_recorder import UsageRecorder

    path = tmp_path / "config.toml"
    path.write_text("[llm]\ncost_multiplier = nan\n", encoding="utf-8")
    config = load_config(path, consult_environment=False)
    assert config.llm.cost_multiplier == pytest.approx(1.0)
    assert UsageRecorder(sink=None, cost_multiplier=float("nan"))._cost_multiplier == 1.0


def test_oversized_integer_config_value_falls_back(tmp_path: Path) -> None:
    from openbiliclaw.config import _normalize_scheduler_float, load_config

    assert _normalize_scheduler_float(10**400, default=64, min_value=1) == 64
    path = tmp_path / "config.toml"
    path.write_text(f"[agent]\nloop_max_steps = {10**400}\n", encoding="utf-8")
    assert load_config(path, consult_environment=False).agent.loop_max_steps == 64


def test_absurd_usage_multiplier_falls_back_to_one() -> None:
    from openbiliclaw.llm.usage_recorder import UsageRecorder

    assert UsageRecorder(sink=None, cost_multiplier=1e308)._cost_multiplier == 1.0


def test_oversized_pricing_override_is_ignored(tmp_path: Path) -> None:
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "[[llm.pricing.overrides]]\n"
        'provider = "deepseek"\n'
        'model = "deepseek-v4-flash"\n'
        f"input = {10**400}\n"
        "output = 1.0\n",
        encoding="utf-8",
    )
    config = load_config(path, consult_environment=False)
    assert config.llm.pricing.overrides == []


def test_usage_recorder_accepts_huge_integer_multiplier() -> None:
    from openbiliclaw.llm.usage_recorder import UsageRecorder

    assert UsageRecorder(sink=None, cost_multiplier=10**400)._cost_multiplier == 1.0
