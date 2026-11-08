"""Canonical names and aliases for the keyword-inspiration search backends.

Config validation (``openbiliclaw.config``), the provider factory
(``openbiliclaw.discovery.inspiration_provider``) and the V2EX external-recall
path all need the same backend vocabulary. Keeping the tables in this leaf
module (stdlib-only, no intra-package imports) means adding a grounding backend
is a one-table change instead of a three-call-site edit, and ``config`` can
import it without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_INSPIRATION_SEARCH_BACKENDS: tuple[str, ...] = (
    "local_cache",
    "platform_sources",
    "bing_rss",
    "exa",
    "you",
    "serply",
)

# Alias -> canonical name. Canonical spellings map to themselves so callers can
# resolve every accepted spelling through a single lookup.
INSPIRATION_SEARCH_BACKEND_ALIASES: Mapping[str, str] = {
    "bing": "bing_rss",
    "bing_rss": "bing_rss",
    "bing-rss": "bing_rss",
    "exa": "exa",
    "parallel": "parallel",
    "local": "local_cache",
    "cache": "local_cache",
    "local_cache": "local_cache",
    "local-cache": "local_cache",
    "platform": "platform_sources",
    "platform-source": "platform_sources",
    "platform_sources": "platform_sources",
    "you": "you",
    "you.com": "you",
    "youcom": "you",
    "you-search": "you",
    "you_search": "you",
    "serply": "serply",
    "serply.io": "serply",
    "keenable": "keenable",
}

# Backends that call an external search API and can therefore serve the
# ``site:v2ex.com/t`` recall path. ``local_cache`` and ``platform_sources`` stay
# on local / Bilibili data and are excluded.
EXTERNAL_INSPIRATION_SEARCH_BACKENDS: frozenset[str] = frozenset(
    {"bing_rss", "exa", "you", "serply", "parallel", "keenable"}
)


def normalize_inspiration_search_backends(
    value: object,
    *,
    default: tuple[str, ...] = DEFAULT_INSPIRATION_SEARCH_BACKENDS,
) -> tuple[str, ...]:
    """Map configured backend spellings onto canonical names, dropping duplicates.

    Accepts a comma-separated string (env / CLI), a list or tuple (TOML), or
    ``None`` for the default chain. Unknown names are dropped rather than
    raising so a config written for a newer build cannot take discovery down.
    """

    raw_values: list[str]
    if value is None:
        raw_values = list(default)
    elif isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_values = []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        backend = INSPIRATION_SEARCH_BACKEND_ALIASES.get(raw.strip().lower())
        if backend is None or backend in seen:
            continue
        normalized.append(backend)
        seen.add(backend)
    return tuple(normalized or default)
