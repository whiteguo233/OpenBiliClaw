"""Code-level safety gates for the Blue Fish Lab.

Prompt instructions are advisory; these checks are not. Every gate returns
a machine-readable ``code`` so the UI and the ledger can explain why the
fish stayed quiet instead of silently doing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .config import parse_active_hours

if TYPE_CHECKING:
    from .config import FishConfig

# Failure breaker: N failed publisher runs since the last successful publish,
# within the cooldown window, stop scheduled runs (previews/skips are ignored).
# Not empirically calibrated yet — a product guess, to be revisited after
# real-account integration runs. Rationale: one failure is
# usually transient (B站 5xx, one unreadable video), while three in a row has
# so far meant a persistent cause (dead cookie, changed reply API); 60 minutes
# is long enough for the operator to read the ledger line, short enough that a
# transient outage does not leave the lab dark for a day. Manual runs bypass
# the breaker on purpose.
_FAILURE_STREAK_LIMIT = 3
_FAILURE_COOLDOWN_MINUTES = 60

_WHITESPACE_RE = re.compile(r"\s+")
_WRAPPING_QUOTES = "\"'“”‘’「」『』"


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate check."""

    ok: bool
    code: str = ""
    message: str = ""


_OK = GateResult(ok=True)


def mention_prefix(uname: str) -> str:
    """Build the plain-text ``@`` prefix Bilibili understands."""
    name = str(uname or "").strip()
    return f"@{name} " if name else ""


def sanitize_reply(text: str) -> str:
    """Normalize model output into a comment body.

    Collapses whitespace, strips wrapping quotes, and removes the markdown
    emphasis a model likes to add but Bilibili renders literally.
    """
    cleaned = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    while cleaned and cleaned[0] in _WRAPPING_QUOTES:
        cleaned = cleaned[1:].lstrip()
    while cleaned and cleaned[-1] in _WRAPPING_QUOTES:
        cleaned = cleaned[:-1].rstrip()
    return cleaned.strip()


def check_can_run(
    config: FishConfig,
    *,
    authenticated: bool,
    isolated: bool,
    today_published: int,
    last_published_at: datetime | None,
    now: datetime,
    dry_run: bool,
    manual: bool,
    failure_streak: int = 0,
    last_failure_at: datetime | None = None,
    local_now: datetime | None = None,
) -> GateResult:
    """Decide whether this run may proceed at all."""
    if not config.enabled:
        return GateResult(False, "disabled", "「养蓝色大肥鱼」还没开启。")
    if not authenticated:
        return GateResult(False, "not_logged_in", "大肥鱼还没登录，先给它一个独立的 B 站账号。")
    if not isolated:
        return GateResult(
            False,
            "account_not_isolated",
            "大肥鱼用的是你自己的账号 —— 它必须是独立的小号，已拒绝运行。",
        )
    if dry_run:
        # A preview publishes nothing, so it costs neither quota nor the
        # minimum interval. It still needs a working account to read comments.
        return _OK
    if not manual and failure_streak >= _FAILURE_STREAK_LIMIT and last_failure_at is not None:
        cooldown = timedelta(minutes=_FAILURE_COOLDOWN_MINUTES)
        elapsed = now - last_failure_at
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            return GateResult(
                False,
                "failure_cooldown",
                f"最近连续失败 {failure_streak} 次，先暂停 "
                f"{max(1, int(remaining.total_seconds() // 60))} 分钟再试。",
            )
    if today_published >= config.daily_limit:
        return GateResult(
            False,
            "daily_limit",
            f"今天已经发了 {today_published} 条，达到每日上限 {config.daily_limit} 条。",
        )
    if not manual and local_now is not None:
        window = parse_active_hours(config.active_hours)
        if window is not None and not _within_window(window, local_now):
            return GateResult(
                False,
                "outside_active_hours",
                f"现在不在设定的发言时段（{config.active_hours}）内，先不说话。",
            )
    if not manual and last_published_at is not None:
        gap = now - last_published_at
        if gap < timedelta(minutes=config.min_interval_minutes):
            remaining = timedelta(minutes=config.min_interval_minutes) - gap
            return GateResult(
                False,
                "min_interval",
                f"距离上一次发言还不到 {config.min_interval_minutes} 分钟，"
                f"再等 {max(1, int(remaining.total_seconds() // 60))} 分钟。",
            )
    return _OK


def _within_window(window: tuple[int, int], local_now: datetime) -> bool:
    """Whether ``local_now`` falls inside the configured minute window."""

    start, end = window
    minutes = local_now.hour * 60 + local_now.minute
    if start == end:
        # A zero-length window means "all day" rather than "never".
        return True
    if start < end:
        return start <= minutes < end
    # Overnight: 18:00-09:00 wraps around midnight.
    return minutes >= start or minutes < end


def check_reply_text(config: FishConfig, text: str) -> GateResult:
    """Validate the sanitized reply before it reaches Bilibili."""
    body = sanitize_reply(text)
    if not body:
        return GateResult(False, "empty_reply", "模型没写出内容，这一条跳过。")
    if len(body) > config.max_chars:
        return GateResult(
            False,
            "too_long",
            f"回复 {len(body)} 字，超过上限 {config.max_chars} 字，这一条跳过。",
        )
    lowered = body.lower()
    for word in config.blocked_words:
        needle = str(word).strip().lower()
        if needle and needle in lowered:
            return GateResult(False, "blocked_word", f"回复命中屏蔽词「{word}」，这一条跳过。")
    return _OK
