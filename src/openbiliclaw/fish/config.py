"""Blue Fish Lab configuration.

The lab keeps its own JSON config instead of a ``config.toml`` section on
purpose:

* the fields are still moving, and folding them into the main config would
  widen the compatibility surface of the signed / hot-reloaded config path;
* the fish account is isolated by design, so its settings and credentials
  live next to it under ``<data_dir>/fish/``;
* deleting that directory resets the whole lab.

Every value is clamped on the way in and out. A lab feature must never be
able to take the settings page down with one bad number.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FISH_CONFIG_FILENAME = "config.json"

DEFAULT_PERSONA_PROMPT = (
    "你是一只叫「蓝色大肥鱼」的 B 站小用户，圆滚滚的，反应慢半拍。\n"
    "你真心喜欢看视频，遇到喜欢的会忍不住「哇」「救命」「谁懂啊」。\n"
    "你说话像在弹幕里随口接一句梗，偶尔自嘲一下，语气软软的，但不装可爱。\n"
    "你不端着、不装懂、不追热点，只说你自己真的觉得有意思的那个点。"
)

DEFAULT_REPLY_RULES = (
    "只回答摆在你面前的那一条评论，不要替视频作者说话。\n"
    "说一个具体的点：一个画面、一句台词、一个反常识的细节。\n"
    "可以接梗，但不要阴阳怪气，也不要跟人抬杠。\n"
    "不要提 AI、模型、提示词、OpenBiliClaw，也不要暴露任何人的账号和机器信息。\n"
    "不确定的事就换个说法，不要编。"
)

_MAX_CHARS_BOUNDS = (10, 300)
_DAILY_LIMIT_BOUNDS = (0, 20)
_MIN_INTERVAL_BOUNDS = (5, 1440)
_PER_VIDEO_LIMIT_BOUNDS = (1, 3)
_MAX_CANDIDATES_BOUNDS = (1, 20)
_COMMENTS_PER_VIDEO_BOUNDS = (1, 50)
_MIN_COMMENT_LIKES_BOUNDS = (0, 10_000)

_MAX_PERSONA_CHARS = 4_000
_MAX_AUDIENCE_CHARS = 300
_MAX_TRIGGER_CHARS = 300
_MAX_OWNER_CHARS = 30
_NOTIFY_LIMIT_BOUNDS = (0, 3)
_MIN_VIEW_COUNT_BOUNDS = (0, 10_000_000)
_OWNER_MID_BOUNDS = (0, 10_000_000_000_000)
_MIN_PUBLISH_AGE_BOUNDS = (0, 365)
NOTIFY_CHANNELS = ("comment", "dm", "both")
_MAX_NAME_CHARS = 20
_MAX_BLOCKED_WORDS = 50
_MAX_BLOCKED_WORD_CHARS = 20


@dataclass
class FishConfig:
    """User-tunable settings for the Blue Fish Lab."""

    enabled: bool = False
    dry_run: bool = True
    persona_name: str = "蓝色大肥鱼"
    persona_prompt: str = DEFAULT_PERSONA_PROMPT
    reply_rules: str = DEFAULT_REPLY_RULES
    # Optional targeting hint: "only answer people who look like X". Empty
    # means "no restriction" — the fish just picks the most natural comment.
    audience_prompt: str = ""
    # Optional "only speak when …" policy. Goes into the <trigger> section;
    # the model may still skip, but this is the user's own condition.
    trigger_prompt: str = ""
    # Optional daily window ("09:00-23:00", overnight allowed). Empty means
    # the fish may act at any hour; scheduled runs outside it are skipped.
    active_hours: str = ""
    max_chars: int = 80
    daily_limit: int = 3
    min_interval_minutes: int = 30
    per_video_limit: int = 1
    max_candidates: int = 5
    comments_per_video: int = 10
    min_comment_likes: int = 0
    # Optional supply-quality gates on the pool candidate itself. 0 = off;
    # unknown stats fail an enabled gate rather than silently passing.
    min_view_count: int = 0
    min_publish_age_days: int = 0
    blocked_words: list[str] = field(default_factory=list)
    allow_top_level: bool = False
    # Owner notification: the fish posts one public comment per day that
    # mentions the owner's B站 account, so the recommendation arrives as a
    # normal "@了你" message instead of waiting for the app to be opened.
    # Default off: it is the only fish act that touches the owner's identity.
    notify_owner: bool = False
    # B站 resolves ``@昵称`` from the comment text, so the nickname must match
    # the owner's profile exactly. Empty = read it from the main account's nav.
    owner_name: str = ""
    # Owner mid, used when the main account's cookie is dead: it is both the
    # DM receiver and the isolation reference (the fish must differ from it).
    owner_mid: int = 0
    notify_daily_limit: int = 1
    # How the owner gets told: a public comment @-mention, a private message,
    # or both. "both" mirrors the subagent-style flow — the public comment is
    # the primary act and the DM only goes out after it succeeded.
    notify_channel: str = "comment"

    def to_public_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view for the settings UI."""
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "persona_name": self.persona_name,
            "persona_prompt": self.persona_prompt,
            "reply_rules": self.reply_rules,
            "audience_prompt": self.audience_prompt,
            "trigger_prompt": self.trigger_prompt,
            "active_hours": self.active_hours,
            "max_chars": self.max_chars,
            "daily_limit": self.daily_limit,
            "min_interval_minutes": self.min_interval_minutes,
            "per_video_limit": self.per_video_limit,
            "max_candidates": self.max_candidates,
            "comments_per_video": self.comments_per_video,
            "min_comment_likes": self.min_comment_likes,
            "min_view_count": self.min_view_count,
            "min_publish_age_days": self.min_publish_age_days,
            "blocked_words": list(self.blocked_words),
            "allow_top_level": self.allow_top_level,
            "notify_owner": self.notify_owner,
            "owner_name": self.owner_name,
            "owner_mid": self.owner_mid,
            "notify_daily_limit": self.notify_daily_limit,
            "notify_channel": self.notify_channel,
        }


def fish_home(data_dir: Path) -> Path:
    """Return the lab's private directory under the runtime data dir."""
    return Path(data_dir) / "fish"


def fish_config_path(data_dir: Path) -> Path:
    """Return the lab config file path."""
    return fish_home(data_dir) / FISH_CONFIG_FILENAME


def _as_int(value: object) -> int | None:
    """Coerce a JSON scalar to int, or return None when it is not numeric."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _clamp_int(
    value: object,
    *,
    default: int,
    bounds: tuple[int, int],
    field_name: str,
    warnings: list[str],
) -> int:
    if value is None:
        # Missing key on load / untouched field on save: the default is
        # the normal case, not something worth warning about.
        return default
    parsed = _as_int(value)
    if parsed is None:
        warnings.append(f"{field_name} 不是整数，已用默认值 {default}")
        return default
    low, high = bounds
    if parsed < low or parsed > high:
        clamped = max(low, min(high, parsed))
        warnings.append(f"{field_name} 超出范围，已钳到 {clamped}")
        return clamped
    return parsed


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _clean_text(
    value: object,
    *,
    default: str,
    max_chars: int,
    field_name: str,
    warnings: list[str],
) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) > max_chars:
        warnings.append(f"{field_name} 超过 {max_chars} 字，已截断")
        return text[:max_chars]
    return text


_ACTIVE_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def parse_active_hours(value: object) -> tuple[int, int] | None:
    """Parse ``HH:MM-HH:MM`` into (start, end) minutes, or ``None`` if invalid.

    An overnight window (``18:00-09:00``) is expressed by ``start > end`` —
    the caller wraps. Empty input means "no window".
    """

    text = str(value or "").strip()
    if not text:
        return None
    match = _ACTIVE_HOURS_RE.match(text)
    if match is None:
        return None
    start_hour, start_minute, end_hour, end_minute = (int(part) for part in match.groups())
    if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
        return None
    return start_hour * 60 + start_minute, end_hour * 60 + end_minute


def _clean_active_hours(value: object, *, warnings: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if parse_active_hours(text) is None:
        warnings.append("active_hours 需要形如 09:00-23:00，已忽略（全天可发言）")
        return ""
    return text


def _clean_channel(value: object, *, default: str, warnings: list[str]) -> str:
    """Whitelist the owner-notification delivery channel."""
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in NOTIFY_CHANNELS:
        return text
    warnings.append(f"notify_channel 只能是 {' / '.join(NOTIFY_CHANNELS)}，已回落 {default}")
    return default


def _clean_blocked_words(value: object, *, warnings: list[str]) -> list[str]:
    if not isinstance(value, list):
        if value not in (None, ""):
            warnings.append("blocked_words 不是数组，已忽略")
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        word = str(item or "").strip()
        if not word:
            continue
        if len(word) > _MAX_BLOCKED_WORD_CHARS:
            word = word[:_MAX_BLOCKED_WORD_CHARS]
        if word in seen:
            continue
        seen.add(word)
        cleaned.append(word)
        if len(cleaned) >= _MAX_BLOCKED_WORDS:
            warnings.append(f"blocked_words 最多 {_MAX_BLOCKED_WORDS} 个，多余的已丢弃")
            break
    return cleaned


def parse_fish_config(raw: object) -> tuple[FishConfig, list[str]]:
    """Parse a raw mapping into a clamped :class:`FishConfig`.

    Unknown keys are ignored, bad values fall back to defaults, and every
    coercion is reported so the caller can log or show it.
    """
    warnings: list[str] = []
    if not isinstance(raw, dict):
        if raw not in (None, ""):
            warnings.append("配置不是 JSON 对象，已使用默认值")
        return FishConfig(), warnings

    defaults = FishConfig()
    config = FishConfig(
        enabled=_coerce_bool(raw.get("enabled"), default=defaults.enabled),
        dry_run=_coerce_bool(raw.get("dry_run"), default=defaults.dry_run),
        persona_name=_clean_text(
            raw.get("persona_name"),
            default=defaults.persona_name,
            max_chars=_MAX_NAME_CHARS,
            field_name="persona_name",
            warnings=warnings,
        ),
        persona_prompt=_clean_text(
            raw.get("persona_prompt"),
            default=defaults.persona_prompt,
            max_chars=_MAX_PERSONA_CHARS,
            field_name="persona_prompt",
            warnings=warnings,
        ),
        reply_rules=_clean_text(
            raw.get("reply_rules"),
            default=defaults.reply_rules,
            max_chars=_MAX_PERSONA_CHARS,
            field_name="reply_rules",
            warnings=warnings,
        ),
        audience_prompt=_clean_text(
            raw.get("audience_prompt"),
            default=defaults.audience_prompt,
            max_chars=_MAX_AUDIENCE_CHARS,
            field_name="audience_prompt",
            warnings=warnings,
        ),
        trigger_prompt=_clean_text(
            raw.get("trigger_prompt"),
            default=defaults.trigger_prompt,
            max_chars=_MAX_TRIGGER_CHARS,
            field_name="trigger_prompt",
            warnings=warnings,
        ),
        active_hours=_clean_active_hours(raw.get("active_hours"), warnings=warnings),
        max_chars=_clamp_int(
            raw.get("max_chars"),
            default=defaults.max_chars,
            bounds=_MAX_CHARS_BOUNDS,
            field_name="max_chars",
            warnings=warnings,
        ),
        daily_limit=_clamp_int(
            raw.get("daily_limit"),
            default=defaults.daily_limit,
            bounds=_DAILY_LIMIT_BOUNDS,
            field_name="daily_limit",
            warnings=warnings,
        ),
        min_interval_minutes=_clamp_int(
            raw.get("min_interval_minutes"),
            default=defaults.min_interval_minutes,
            bounds=_MIN_INTERVAL_BOUNDS,
            field_name="min_interval_minutes",
            warnings=warnings,
        ),
        per_video_limit=_clamp_int(
            raw.get("per_video_limit"),
            default=defaults.per_video_limit,
            bounds=_PER_VIDEO_LIMIT_BOUNDS,
            field_name="per_video_limit",
            warnings=warnings,
        ),
        max_candidates=_clamp_int(
            raw.get("max_candidates"),
            default=defaults.max_candidates,
            bounds=_MAX_CANDIDATES_BOUNDS,
            field_name="max_candidates",
            warnings=warnings,
        ),
        comments_per_video=_clamp_int(
            raw.get("comments_per_video"),
            default=defaults.comments_per_video,
            bounds=_COMMENTS_PER_VIDEO_BOUNDS,
            field_name="comments_per_video",
            warnings=warnings,
        ),
        min_comment_likes=_clamp_int(
            raw.get("min_comment_likes"),
            default=defaults.min_comment_likes,
            bounds=_MIN_COMMENT_LIKES_BOUNDS,
            field_name="min_comment_likes",
            warnings=warnings,
        ),
        min_view_count=_clamp_int(
            raw.get("min_view_count"),
            default=defaults.min_view_count,
            bounds=_MIN_VIEW_COUNT_BOUNDS,
            field_name="min_view_count",
            warnings=warnings,
        ),
        min_publish_age_days=_clamp_int(
            raw.get("min_publish_age_days"),
            default=defaults.min_publish_age_days,
            bounds=_MIN_PUBLISH_AGE_BOUNDS,
            field_name="min_publish_age_days",
            warnings=warnings,
        ),
        blocked_words=_clean_blocked_words(raw.get("blocked_words"), warnings=warnings),
        allow_top_level=_coerce_bool(raw.get("allow_top_level"), default=defaults.allow_top_level),
        notify_owner=_coerce_bool(raw.get("notify_owner"), default=defaults.notify_owner),
        owner_name=_clean_text(
            raw.get("owner_name"),
            default=defaults.owner_name,
            max_chars=_MAX_OWNER_CHARS,
            field_name="owner_name",
            warnings=warnings,
        ),
        owner_mid=_clamp_int(
            raw.get("owner_mid"),
            default=defaults.owner_mid,
            bounds=_OWNER_MID_BOUNDS,
            field_name="owner_mid",
            warnings=warnings,
        ),
        notify_daily_limit=_clamp_int(
            raw.get("notify_daily_limit"),
            default=defaults.notify_daily_limit,
            bounds=_NOTIFY_LIMIT_BOUNDS,
            field_name="notify_daily_limit",
            warnings=warnings,
        ),
        notify_channel=_clean_channel(
            raw.get("notify_channel"),
            default=defaults.notify_channel,
            warnings=warnings,
        ),
    )
    return config, warnings


def load_fish_config(data_dir: Path) -> FishConfig:
    """Load the lab config, falling back to defaults when absent or broken."""
    path = fish_config_path(data_dir)
    if not path.exists():
        return FishConfig()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Blue Fish config unreadable (%s) — using defaults", exc)
        return FishConfig()
    config, warnings = parse_fish_config(raw)
    for warning in warnings:
        logger.warning("Blue Fish config: %s", warning)
    return config


def save_fish_config(data_dir: Path, config: FishConfig) -> FishConfig:
    """Persist a clamped copy of ``config`` and return the effective value."""
    normalized, warnings = parse_fish_config(config.to_public_dict())
    for warning in warnings:
        logger.warning("Blue Fish config: %s", warning)
    path = fish_config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(normalized.to_public_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)
    return normalized
