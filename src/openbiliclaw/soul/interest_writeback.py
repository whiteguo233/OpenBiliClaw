"""Helpers for writing confirmed speculative interests into the profile."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.soul.profile import InterestDomain, InterestSpecific, OnionProfile

if TYPE_CHECKING:
    from collections.abc import Sequence

CONFIRMED_INTEREST_WEIGHTS = {
    "probe_confirmed": 0.45,
    "profile_confirmed": 0.60,
    "chat_confirmed": 0.50,
    "buffer_promoted": 0.45,
    "speculated": 0.30,
}


def confirmed_interest_weight(source: str) -> float:
    """Return the default profile weight for a confirmation source."""
    return CONFIRMED_INTEREST_WEIGHTS.get(source, CONFIRMED_INTEREST_WEIGHTS["speculated"])


def _normalize_domain(value: str) -> str:
    return "".join(value.strip().lower().split())


def merge_confirmed_interest(
    profile: OnionProfile,
    *,
    domain: str,
    specifics: Sequence[str] = (),
    source: str,
    first_seen: str = "",
    last_seen: str = "",
) -> bool:
    """Merge a confirmed interest into ``profile.interest.likes`` without duplicates."""
    clean_domain = domain.strip()
    if not clean_domain:
        return False

    target_weight = confirmed_interest_weight(source)
    normalized = _normalize_domain(clean_domain)
    clean_specifics = [item.strip() for item in specifics if item.strip()]

    for existing in profile.interest.likes:
        if _normalize_domain(existing.domain) != normalized:
            continue
        changed = False
        if existing.weight < target_weight:
            existing.weight = target_weight
            changed = True
        if source and existing.source != source:
            existing.source = source
            changed = True
        if first_seen and not existing.first_seen:
            existing.first_seen = first_seen
            changed = True
        if last_seen and existing.last_seen != last_seen:
            existing.last_seen = last_seen
            changed = True

        existing_names = {_normalize_domain(item.name) for item in existing.specifics}
        for specific in clean_specifics:
            if _normalize_domain(specific) not in existing_names:
                existing.specifics.append(
                    InterestSpecific(name=specific, weight=target_weight)
                )
                existing_names.add(_normalize_domain(specific))
                changed = True
        return changed

    profile.interest.likes.append(
        InterestDomain(
            domain=clean_domain,
            weight=target_weight,
            specifics=[
                InterestSpecific(name=specific, weight=target_weight)
                for specific in clean_specifics
            ],
            first_seen=first_seen,
            last_seen=last_seen,
            source=source,
        )
    )
    return True
