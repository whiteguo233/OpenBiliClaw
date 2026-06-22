"""X (Twitter) source health state machine (spec §7).

Discovery for X is server-side cookie replay; a stale cookie, a block, or a
rate-limit costs a real round-trip against the user's main account. To avoid
re-hitting x.com after a known failure, the producer persists the source's
last health state and a per-code backoff window here, then reads it back at
the top of every cycle.

States:

    ``ok``             — last call succeeded; fetch freely
    ``missing_cookie`` — no usable ``auth_token`` / ``ct0`` yet
    ``expired_cookie`` — HTTP 401: cookie expired, wait for re-login
    ``blocked``        — HTTP 403: account/endpoint forbidden, wait for re-login
    ``rate_limited``   — HTTP 429: back off until ``cooldown_until``

401 / 403 require the user to log back in on x.com (the extension re-syncs the
cookie), so the source stays "not ready" until a later success flips it back
to ``ok``. 429 sets a timed cooldown and recovers on its own.

For-You is the highest-visibility (and riskiest) fetch, so it auto-pauses
after ``feed_pause_after`` consecutive For-You failures; any For-You success
lifts the pause.

State lives in a one-row ``x_source_health`` table so it survives restarts.
This module mirrors the lightweight, self-contained storage style of
``sources.x_tasks.XCreatorStore``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.x_client import (
    XAuthError,
    XBlockedError,
    XClientError,
    XMissingCookieError,
    XRateLimitError,
)

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

OK = "ok"
MISSING_COOKIE = "missing_cookie"
EXPIRED_COOKIE = "expired_cookie"
BLOCKED = "blocked"
RATE_LIMITED = "rate_limited"

# States that require the user to re-login on x.com before discovery can
# resume — there is no timed recovery, only a later success flips them back.
_RELOGIN_STATES = frozenset({MISSING_COOKIE, EXPIRED_COOKIE, BLOCKED})

# The singleton key (single-user model — one X account).
_ROW_KEY = "x"


def health_state_for_error(exc: BaseException) -> str:
    """Map a typed :class:`XClientError` onto a discrete health state.

    Falls back to :data:`RATE_LIMITED` for an unknown ``XClientError`` (a
    transient back-off is safer than treating it as healthy) and to
    :data:`OK` for anything that is not an X error at all.
    """
    if isinstance(exc, XMissingCookieError):
        return MISSING_COOKIE
    if isinstance(exc, XAuthError):
        return EXPIRED_COOKIE
    if isinstance(exc, XBlockedError):
        return BLOCKED
    if isinstance(exc, XRateLimitError):
        return RATE_LIMITED
    if isinstance(exc, XClientError):
        return RATE_LIMITED
    return OK


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class XSourceHealthStore:
    """Persisted X source health + per-code backoff."""

    def __init__(
        self,
        db: Database,
        *,
        rate_limit_cooldown_minutes: int = 30,
        feed_pause_after: int = 3,
    ) -> None:
        self._db = db
        self._rate_limit_cooldown_minutes = max(1, int(rate_limit_cooldown_minutes))
        self._feed_pause_after = max(1, int(feed_pause_after))
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS x_source_health (
                key                  TEXT PRIMARY KEY,
                state                TEXT NOT NULL DEFAULT 'ok',
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                feed_failures        INTEGER NOT NULL DEFAULT 0,
                feed_paused          INTEGER NOT NULL DEFAULT 0,
                cooldown_until       TEXT NOT NULL DEFAULT '',
                detail               TEXT NOT NULL DEFAULT '',
                updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._db.conn.execute(
            "INSERT OR IGNORE INTO x_source_health (key, state) VALUES (?, 'ok')",
            (_ROW_KEY,),
        )
        self._db.conn.commit()

    # ── reads ────────────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        """Return the current health row as a JSON-friendly dict."""
        row = self._db.conn.execute(
            "SELECT * FROM x_source_health WHERE key = ?",
            (_ROW_KEY,),
        ).fetchone()
        if row is None:
            return {
                "state": OK,
                "consecutive_failures": 0,
                "feed_paused": False,
                "cooldown_until": "",
                "detail": "",
                "updated_at": "",
            }
        data = dict(row)
        return {
            "state": str(data.get("state") or OK),
            "consecutive_failures": int(data.get("consecutive_failures") or 0),
            "feed_paused": bool(data.get("feed_paused")),
            "cooldown_until": str(data.get("cooldown_until") or ""),
            "detail": str(data.get("detail") or ""),
            "updated_at": str(data.get("updated_at") or ""),
        }

    def is_ready(self) -> bool:
        """Return True when the source may fetch right now.

        ``ok`` is always ready. ``rate_limited`` becomes ready once its
        cooldown window has elapsed. Re-login states (``missing_cookie`` /
        ``expired_cookie`` / ``blocked``) stay not-ready until a later success.
        """
        health = self.get()
        state = health["state"]
        if state == OK:
            return True
        if state in _RELOGIN_STATES:
            return False
        if state == RATE_LIMITED:
            cooldown = _parse_iso(health["cooldown_until"])
            if cooldown is None:
                return True
            return _now() >= cooldown
        return True

    def feed_allowed(self) -> bool:
        """Return True when For-You is not auto-paused."""
        return not self.get()["feed_paused"]

    # ── writes ───────────────────────────────────────────────────────

    def record_success(self, *, strategy: str = "") -> None:
        """Reset to ``ok`` after a successful fetch.

        Any success clears the global failure counter and cooldown. A
        For-You success additionally lifts the For-You auto-pause.
        """
        feed_clear = self._is_feed(strategy)
        self._db.conn.execute(
            """
            UPDATE x_source_health
               SET state = 'ok',
                   consecutive_failures = 0,
                   cooldown_until = '',
                   detail = '',
                   feed_failures = CASE WHEN ? THEN 0 ELSE feed_failures END,
                   feed_paused = CASE WHEN ? THEN 0 ELSE feed_paused END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE key = ?
            """,
            (1 if feed_clear else 0, 1 if feed_clear else 0, _ROW_KEY),
        )
        self._db.conn.commit()

    def clear_relogin_block(self) -> bool:
        """Clear a re-login block after a fresh valid cookie is synced.

        Re-login states (``missing_cookie`` / ``expired_cookie`` / ``blocked``)
        have no timed recovery: :meth:`is_ready` parks the producer, so it can
        never earn the "later success" that would reset them. A new browser
        cookie *is* that external re-login signal, so reset to ``ok`` here —
        otherwise discovery stays dead-locked even after the user re-logs in.

        Leaves ``rate_limited`` untouched (its cooldown is time-based, not a
        cookie problem). Also lifts any For-You auto-pause, since the failures
        that tripped it were attributable to the same expired session. Returns
        True when a block was actually cleared.
        """
        if self.get()["state"] not in _RELOGIN_STATES:
            return False
        self._db.conn.execute(
            """
            UPDATE x_source_health
               SET state = 'ok',
                   consecutive_failures = 0,
                   feed_failures = 0,
                   feed_paused = 0,
                   cooldown_until = '',
                   detail = '',
                   updated_at = CURRENT_TIMESTAMP
             WHERE key = ?
            """,
            (_ROW_KEY,),
        )
        self._db.conn.commit()
        return True

    def record_error(self, exc: BaseException, *, strategy: str = "") -> str:
        """Map an error to a health state, persist it, and return the state."""
        state = health_state_for_error(exc)
        cooldown_until = ""
        if state == RATE_LIMITED:
            cooldown_until = (
                _now() + timedelta(minutes=self._rate_limit_cooldown_minutes)
            ).isoformat()
        is_feed = self._is_feed(strategy)
        current = self.get()
        # feed_failures is an internal counter (not surfaced by get()).
        raw = self._db.conn.execute(
            "SELECT feed_failures FROM x_source_health WHERE key = ?",
            (_ROW_KEY,),
        ).fetchone()
        feed_failures = int(raw["feed_failures"]) if raw is not None else 0
        if is_feed:
            feed_failures += 1
        feed_paused = bool(current["feed_paused"]) or (
            is_feed and feed_failures >= self._feed_pause_after
        )
        self._db.conn.execute(
            """
            UPDATE x_source_health
               SET state = ?,
                   consecutive_failures = consecutive_failures + 1,
                   feed_failures = ?,
                   feed_paused = ?,
                   cooldown_until = ?,
                   detail = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE key = ?
            """,
            (
                state,
                feed_failures,
                1 if feed_paused else 0,
                cooldown_until,
                str(exc)[:500],
                _ROW_KEY,
            ),
        )
        self._db.conn.commit()
        return state

    def set_cooldown_until(self, value: str) -> None:
        """Override the cooldown timestamp (test seam / manual recovery)."""
        self._db.conn.execute(
            "UPDATE x_source_health SET cooldown_until = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = ?",
            (str(value or ""), _ROW_KEY),
        )
        self._db.conn.commit()

    @staticmethod
    def _is_feed(strategy: str) -> bool:
        s = str(strategy or "").strip().lower()
        return s in {"feed", "for_you", "for-you", "foryou", "x-feed"}
