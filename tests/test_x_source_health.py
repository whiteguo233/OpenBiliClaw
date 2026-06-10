"""Tests for the X (Twitter) source health state machine (spec §7).

The health store persists the current X source state and per-code backoff so
the producer can skip / cool down without re-hitting x.com after a known
failure. It maps the typed :class:`XClient` errors to discrete states:

    XMissingCookieError → missing_cookie
    XAuthError (401)    → expired_cookie
    XBlockedError (403) → blocked
    XRateLimitError(429)→ rate_limited (with a cooldown window)
    success             → ok

Repeated For-You failures auto-pause the For-You feed. ``GET
/api/sources/x/status`` exposes the current state for the settings UI.

All tests run offline against a real on-disk :class:`Database`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.sources.x_client import (
    XAuthError,
    XBlockedError,
    XMissingCookieError,
    XRateLimitError,
)
from openbiliclaw.storage.database import Database
from openbiliclaw.storage.x_health import (
    BLOCKED,
    EXPIRED_COOKIE,
    MISSING_COOKIE,
    OK,
    RATE_LIMITED,
    XSourceHealthStore,
    health_state_for_error,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "x_health.db")
    db.initialize()
    return db


# ── error → state mapping ────────────────────────────────────────────


def test_missing_cookie_error_maps_to_missing_cookie() -> None:
    assert health_state_for_error(XMissingCookieError("no cookie")) == MISSING_COOKIE


def test_auth_error_maps_to_expired_cookie() -> None:
    assert health_state_for_error(XAuthError("401")) == EXPIRED_COOKIE


def test_blocked_error_maps_to_blocked() -> None:
    assert health_state_for_error(XBlockedError("403")) == BLOCKED


def test_rate_limit_error_maps_to_rate_limited() -> None:
    assert health_state_for_error(XRateLimitError("429")) == RATE_LIMITED


# ── store: record + read ─────────────────────────────────────────────


def test_default_state_is_ok(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path))
    health = store.get()
    assert health["state"] == OK
    assert health["consecutive_failures"] == 0
    assert health["feed_paused"] is False


def test_record_success_clears_failures_and_cooldown(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path))
    store.record_error(XRateLimitError("429"), strategy="search")
    store.record_success(strategy="search")
    health = store.get()
    assert health["state"] == OK
    assert health["consecutive_failures"] == 0
    assert health["cooldown_until"] in ("", None)


def test_rate_limit_sets_cooldown(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path))
    store.record_error(XRateLimitError("429"), strategy="search")
    health = store.get()
    assert health["state"] == RATE_LIMITED
    assert not store.is_ready()  # cooled down → not ready right now


def test_auth_error_blocks_until_relogin(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path))
    store.record_error(XAuthError("401"), strategy="feed")
    health = store.get()
    assert health["state"] == EXPIRED_COOKIE
    # 401/403 require re-login; the source is not ready to fetch.
    assert not store.is_ready()


def test_blocked_error_not_ready(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path))
    store.record_error(XBlockedError("403"), strategy="feed")
    assert store.get()["state"] == BLOCKED
    assert not store.is_ready()


def test_cooldown_expires_and_becomes_ready(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path), rate_limit_cooldown_minutes=30)
    store.record_error(XRateLimitError("429"), strategy="search")
    assert not store.is_ready()
    # Simulate the cooldown having elapsed by writing a past timestamp.
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store.set_cooldown_until(past)
    assert store.is_ready()


# ── For-You auto-pause ───────────────────────────────────────────────


def test_for_you_auto_pauses_after_repeated_failures(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path), feed_pause_after=3)
    for _ in range(3):
        store.record_error(XRateLimitError("429"), strategy="feed")
    health = store.get()
    assert health["feed_paused"] is True
    assert not store.feed_allowed()


def test_for_you_not_paused_below_threshold(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path), feed_pause_after=3)
    store.record_error(XRateLimitError("429"), strategy="feed")
    store.record_error(XRateLimitError("429"), strategy="feed")
    assert store.get()["feed_paused"] is False
    assert store.feed_allowed()


def test_feed_success_resets_pause(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path), feed_pause_after=2)
    store.record_error(XRateLimitError("429"), strategy="feed")
    store.record_error(XRateLimitError("429"), strategy="feed")
    assert store.get()["feed_paused"] is True
    store.record_success(strategy="feed")
    assert store.get()["feed_paused"] is False
    assert store.feed_allowed()


def test_non_feed_failures_do_not_pause_feed(tmp_path: Path) -> None:
    store = XSourceHealthStore(_db(tmp_path), feed_pause_after=2)
    store.record_error(XRateLimitError("429"), strategy="search")
    store.record_error(XRateLimitError("429"), strategy="search")
    # search failures bump the global failure counter but must not pause For-You.
    assert store.get()["feed_paused"] is False


# ── status endpoint ──────────────────────────────────────────────────


def test_status_endpoint_returns_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.config import Config, save_config

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(Config(), tmp_path / "config.toml")

    database = _db(tmp_path)
    store = XSourceHealthStore(database)
    store.record_error(XAuthError("401"), strategy="search")

    app = create_app(memory_manager=object(), database=database, soul_engine=object())
    with TestClient(app) as client:
        resp = client.get("/api/sources/x/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["state"] == EXPIRED_COOKIE
    assert "feed_paused" in payload
    assert "consecutive_failures" in payload
