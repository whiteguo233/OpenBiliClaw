"""Shared utilities and protocols for discovery strategies."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

# Profile→prompt serializers were moved to ``soul/profile_views.py`` verbatim
# (Task 5, Wave B of the profile-views plan) so every profile serialization
# lives in one module. They are re-exported here — with the two leaf utilities
# the query-generation view depends on (``normalize_match_text`` /
# ``_coerce_query_embedding_vector``) — so existing
# discovery/recommendation/runtime/sources import paths keep working unchanged.
#
# DEPRECATED import site: new profile-bearing prompts must import these from
# ``openbiliclaw.soul.profile_views`` directly, never re-invent a serializer.
# ``soul`` (that module's layer) must not import ``discovery``, which is why the
# two shared leaf utilities live there and travel back here as re-exports.
from openbiliclaw.soul.profile_views import (
    _CONTENT_PROMPT_DOMAIN_CAP as _CONTENT_PROMPT_DOMAIN_CAP,
)
from openbiliclaw.soul.profile_views import (
    _CONTENT_PROMPT_INTEREST_CAP as _CONTENT_PROMPT_INTEREST_CAP,
)
from openbiliclaw.soul.profile_views import (
    _coerce_query_embedding_vector as _coerce_query_embedding_vector,
)
from openbiliclaw.soul.profile_views import (
    build_profile_summary as build_profile_summary,
)
from openbiliclaw.soul.profile_views import (
    build_query_generation_profile_summary as build_query_generation_profile_summary,
)
from openbiliclaw.soul.profile_views import (
    compact_content_prompt_profile_summary as compact_content_prompt_profile_summary,
)
from openbiliclaw.soul.profile_views import (
    normalize_match_text as normalize_match_text,
)
from openbiliclaw.soul.profile_views import (
    set_topic_lifecycle_serialization as set_topic_lifecycle_serialization,
)
from openbiliclaw.soul.profile_views import (
    topic_lifecycle_serialization_enabled as topic_lifecycle_serialization_enabled,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile

_T = TypeVar("_T")


async def _gather_bounded(
    awaitables: list[Awaitable[_T]],
    *,
    runner: Callable[[Awaitable[_T]], Awaitable[_T]] | None = None,
) -> list[object]:
    """Gather awaitables, optionally routing them through a bounded runner."""
    if runner is None:
        return cast(
            "list[object]",
            await asyncio.gather(*awaitables, return_exceptions=True),
        )
    return cast(
        "list[object]",
        await asyncio.gather(
            *(runner(awaitable) for awaitable in awaitables),
            return_exceptions=True,
        ),
    )


# ---------------------------------------------------------------------------
# Protocol classes
# ---------------------------------------------------------------------------


class SupportsSearchClient(Protocol):
    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
        *,
        pubtime_begin: int | None = None,
        pubtime_end: int | None = None,
    ) -> list[dict[str, object]]: ...


def search_cooldown_remaining(client: object) -> float:
    """Return process/client search cooldown seconds when the client exposes it."""
    remaining = getattr(client, "search_cooldown_remaining", None)
    if not callable(remaining):
        return 0.0
    try:
        return max(0.0, float(remaining()))
    except Exception:
        return 0.0


class SupportsRankingClient(Protocol):
    async def get_ranking(self, rid: int = 0) -> list[dict[str, object]]: ...


class SupportsMemoryManager(Protocol):
    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]: ...


class SupportsSeedStrategy(Protocol):
    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]: ...


class SupportsRelatedClient(Protocol):
    async def get_related_videos(self, bvid: str) -> list[dict[str, object]]: ...

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]: ...


# ---------------------------------------------------------------------------
# Shared helper functions (extracted from SearchStrategy static methods)
# ---------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """Strip HTML tags from *value*."""
    return re.sub(r"<[^>]+>", "", value).strip()


def to_int(raw_value: object) -> int:
    """Best-effort conversion of *raw_value* to ``int``."""
    if isinstance(raw_value, bool):
        return int(raw_value)
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value)
    if isinstance(raw_value, str):
        digits = raw_value.replace(",", "").strip()
        if digits.isdigit():
            return int(digits)
    return 0


def parse_duration(raw_value: object) -> int:
    """Parse a duration value (int seconds or ``HH:MM:SS`` / ``MM:SS`` string)."""
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and ":" in raw_value:
        parts = [part for part in raw_value.split(":") if part.isdigit()]
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return to_int(raw_value)


def cached_embedding_lookup(
    embedding_service: object | None,
) -> Callable[[str], list[float]] | None:
    """Return a safe cache-only embedding lookup for prompt shaping.

    Query-generation prompts must not trigger fresh embedding API calls; that
    would move cost from chat completion to embedding and add latency to every
    planner/search cycle. ``lookup_cached`` keeps this helper opportunistic:
    use semantic diversity when cache is warm, otherwise preserve the old
    deterministic order.
    """
    lookup = getattr(embedding_service, "lookup_cached", None)
    if not callable(lookup):
        return None

    def _lookup(text: str) -> list[float]:
        try:
            return _coerce_query_embedding_vector(lookup(text))
        except Exception:
            return []

    return _lookup


def interest_aliases(name: str) -> set[str]:
    """Return a set of normalised alias tokens for a given interest *name*."""
    cleaned = re.sub(r"\s+", "", name).strip().lower()
    if not cleaned:
        return set()
    aliases = {cleaned}
    stripped = re.sub(r"(系列|作品集|作品)$", "", cleaned).strip()
    if stripped:
        aliases.add(stripped)
    for token in re.split(r"[\s/&、，,+\-]+|与|和|及|之|的", cleaned):
        token = token.strip()
        if not token:
            continue
        if token.isascii():
            if len(token) >= 2:
                aliases.add(token)
            continue
        if len(token) >= 2:
            aliases.add(token)
    return aliases


def interest_anchors(profile: SoulProfile) -> list[tuple[str, float]]:
    """Build weighted interest anchor pairs from the top profile interests."""
    anchors: dict[str, float] = {}
    for interest_item in profile.preferences.interests[:5]:
        raw_name = str(interest_item.name).strip()
        if not raw_name:
            continue
        weight = max(0.0, min(1.0, float(interest_item.weight)))
        for alias in interest_aliases(raw_name):
            anchors[alias] = max(anchors.get(alias, 0.0), weight)
    return list(anchors.items())
