"""Short-term weak-signal buffer for exploratory interests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

PROMOTION_SCORE = 4.0
PROMOTION_WINDOW_DAYS = 7
BUFFER_TTL_DAYS = 10
COOLDOWN_HOURS = 48
EVENT_WEIGHTS = {
    "weak_positive_chat": 1.5,
    "card_like": 1.5,
    "card_more_like": 1.5,
    "long_watch": 0.5,
    "plain_click": 0.25,
    "negative": -3.0,
}
EXPLICIT_POSITIVE_EVENTS = frozenset(
    {
        "weak_positive_chat",
        "card_like",
        "card_more_like",
    }
)


def normalize_buffer_key(domain: str, specifics: Sequence[str] = ()) -> str:
    """Normalize a domain/specific tuple into a stable buffer key."""
    parts = [domain, *specifics]
    return "|".join("".join(part.strip().lower().split()) for part in parts if part.strip())


def make_buffer_entry(
    *,
    domain: str,
    first_seen: datetime,
    specifics: Sequence[str] = (),
) -> dict[str, object]:
    """Create a normalized empty buffer entry."""
    clean_specifics = [item.strip() for item in specifics if item.strip()]
    return {
        "domain": domain.strip(),
        "specifics": clean_specifics,
        "buffer_key": normalize_buffer_key(domain, clean_specifics),
        "score": 0.0,
        "first_seen": first_seen.isoformat(),
        "expires_at": (first_seen + timedelta(days=BUFFER_TTL_DAYS)).isoformat(),
        "last_seen": first_seen.isoformat(),
        "positive_event_count": 0,
        "explicit_event_count": 0,
        "cooldown_until": "",
        "recent_evidence": [],
    }


def normalize_buffer_state(raw: object) -> dict[str, object]:
    """Return a JSON-safe buffer state."""
    if not isinstance(raw, dict):
        return {"entries": []}
    entries: list[dict[str, object]] = []
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list):
        return {"entries": []}
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "")).strip()
        if not domain:
            continue
        specifics = (
            [str(value).strip() for value in item.get("specifics", []) if str(value).strip()]
            if isinstance(item.get("specifics"), list)
            else []
        )
        first_seen = str(item.get("first_seen", ""))
        entry = {
            "domain": domain,
            "specifics": specifics,
            "buffer_key": str(
                item.get("buffer_key", "") or normalize_buffer_key(domain, specifics)
            ),
            "score": float(item.get("score", 0.0) or 0.0),
            "first_seen": first_seen,
            "expires_at": str(item.get("expires_at", "")),
            "last_seen": str(item.get("last_seen", "")),
            "positive_event_count": int(item.get("positive_event_count", 0) or 0),
            "explicit_event_count": int(item.get("explicit_event_count", 0) or 0),
            "cooldown_until": str(item.get("cooldown_until", "")),
            "recent_evidence": [
                evidence
                for evidence in item.get("recent_evidence", [])
                if isinstance(evidence, dict)
            ][-10:]
            if isinstance(item.get("recent_evidence"), list)
            else [],
        }
        entries.append(entry)
    return {"entries": entries}


def _parse_datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def record_buffer_event(
    state: dict[str, object],
    *,
    domain: str,
    source_event: str,
    now: datetime,
    specifics: Sequence[str] = (),
    evidence_id: str = "",
) -> dict[str, object]:
    """Record one weak/negative event into the short-term exploration buffer."""
    normalized = normalize_buffer_state(state)
    clean_domain = domain.strip()
    if not clean_domain:
        return normalized
    source_event = source_event.strip()
    if source_event not in EVENT_WEIGHTS:
        return normalized

    entries = normalized["entries"]
    assert isinstance(entries, list)
    key = normalize_buffer_key(clean_domain, specifics)
    entry = next(
        (item for item in entries if str(item.get("buffer_key", "")) == key),
        None,
    )
    if entry is None:
        entry = make_buffer_entry(domain=clean_domain, specifics=specifics, first_seen=now)
        entries.append(entry)

    expires_at = _parse_datetime(entry.get("expires_at"))
    if expires_at is not None and now > expires_at:
        entries.remove(entry)
        entry = make_buffer_entry(domain=clean_domain, specifics=specifics, first_seen=now)
        entries.append(entry)

    cooldown_until = _parse_datetime(entry.get("cooldown_until"))
    in_cooldown = cooldown_until is not None and now < cooldown_until
    weight = EVENT_WEIGHTS[source_event]
    if source_event == "negative":
        entry["score"] = weight
        entry["positive_event_count"] = 0
        entry["explicit_event_count"] = 0
        entry["cooldown_until"] = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat()
    elif not in_cooldown:
        entry["score"] = _as_float(entry.get("score", 0.0)) + weight
        if weight > 0:
            entry["positive_event_count"] = _as_int(entry.get("positive_event_count", 0)) + 1
        if source_event in EXPLICIT_POSITIVE_EVENTS:
            entry["explicit_event_count"] = _as_int(entry.get("explicit_event_count", 0)) + 1

    entry["last_seen"] = now.isoformat()
    evidence = {
        "source_event": source_event,
        "seen_at": now.isoformat(),
    }
    if evidence_id:
        evidence["evidence_id"] = evidence_id
    recent = entry.get("recent_evidence", [])
    if not isinstance(recent, list):
        recent = []
    recent.append(evidence)
    entry["recent_evidence"] = recent[-10:]
    return normalized


def pop_promotable_buffer_entries(
    state: dict[str, object],
    *,
    now: datetime,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Return promotable entries and remove them from the buffer."""
    normalized = normalize_buffer_state(state)
    entries = normalized["entries"]
    assert isinstance(entries, list)
    promoted: list[dict[str, object]] = []
    remaining: list[dict[str, object]] = []
    for entry in entries:
        expires_at = _parse_datetime(entry.get("expires_at"))
        if expires_at is not None and now > expires_at:
            continue
        first_seen = _parse_datetime(entry.get("first_seen")) or now
        within_window = now <= first_seen + timedelta(days=PROMOTION_WINDOW_DAYS)
        promotable = (
            within_window
            and float(entry.get("score", 0.0) or 0.0) >= PROMOTION_SCORE
            and int(entry.get("positive_event_count", 0) or 0) >= 3
            and int(entry.get("explicit_event_count", 0) or 0) >= 3
        )
        if promotable:
            promoted.append(
                {
                    "domain": str(entry.get("domain", "")),
                    "specifics": list(entry.get("specifics", []))
                    if isinstance(entry.get("specifics"), list)
                    else [],
                    "confirmation_source": "buffer_promoted",
                    "first_seen": str(entry.get("first_seen", "")),
                    "last_seen": str(entry.get("last_seen", "")),
                    "score": float(entry.get("score", 0.0) or 0.0),
                }
            )
        else:
            remaining.append(entry)
    normalized["entries"] = remaining
    return promoted, normalized
