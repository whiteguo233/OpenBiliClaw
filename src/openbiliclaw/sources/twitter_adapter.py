"""X (Twitter) source adapter — server-side cookie-replay discovery.

Unlike the Xiaohongshu stub adapter (whose content enters via extension API
endpoints), the X source runs a **real** ``fetch()`` like Bilibili / Douyin-
direct: the three injected strategies drive an :class:`XClient` (cookie replay
over ``twitter-cli``) and return normalized :class:`DiscoveredContent`.

``fetch()`` dispatches by ``recipe.strategy``:

* ``"search"``  → ``XSearchStrategy``  (keyword(s) from the Soul profile / a
  recipe ``query``)
* ``"feed"``    → ``XForYouStrategy``  (the "For You" home timeline)
* ``"creator"`` → ``XCreatorStrategy`` (a subscribed handle from
  ``recipe.config["handle"]``)

The adapter never imports ``twitter_cli`` — the injected ``XClient`` owns the
lazy import on the network seam, and the strategies only reference it through
a structural protocol. Constructing/registering the adapter on the
``enabled=false`` path is therefore safe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "twitter"


def _coerce_keyword_list(value: Any) -> list[str] | None:
    """Coerce a recipe-config ``keywords`` value into a clean list of strings.

    Returns ``None`` when the recipe carries no ``keywords`` (so the adapter
    keeps the legacy single-``query`` call path byte-for-byte). A present but
    empty / all-blank list yields ``[]`` (an explicit "no keywords" injection).
    """
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _coerce_keyword_id_map(value: Any) -> dict[str, int]:
    """Coerce a recipe-config ``keyword_ids`` value into a ``{str: int}`` map.

    Tolerant of missing / malformed input (returns ``{}``) so a recipe without
    P1.8 provenance is a clean no-op. Only well-formed ``keyword → int`` pairs
    survive.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for raw_key, raw_id in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        try:
            out[key] = int(raw_id)
        except (TypeError, ValueError):
            continue
    return out


class _SupportsDiscover(Protocol):
    """Structural type for the three injected strategy callables.

    Each strategy accepts the profile, a ``limit``, and strategy-specific
    keyword arguments (``query`` for search, ``handle`` for creator).
    """

    async def discover(
        self, profile: Any, *, limit: int = 20, **kwargs: Any
    ) -> list[DiscoveredContent]: ...


class XAdapter:
    """Adapter that fetches X content via three server-side strategies.

    The ``client`` is retained for lifecycle parity with the other real
    adapters (and so the runtime owns a single :class:`XClient`); the actual
    network calls go through the injected strategies, which hold their own
    reference to the same client.
    """

    def __init__(
        self,
        *,
        client: Any,
        search: _SupportsDiscover,
        feed: _SupportsDiscover,
        creator: _SupportsDiscover,
    ) -> None:
        self._client = client
        self._search = search
        self._feed = feed
        self._creator = creator

    # ── SourceAdapter protocol ──────────────────────────────────────

    @property
    def source_type(self) -> str:
        return _SOURCE_TYPE

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Dispatch to the strategy named by ``recipe.strategy``."""
        config = recipe.config if isinstance(recipe.config, dict) else {}
        strategy = recipe.strategy

        if strategy == "search":
            query = str(config.get("query", "") or "")
            # ``queries`` is the unified-planner injection key — it maps to the
            # real ``XSearchStrategy.discover(queries=)`` param. ``keywords``
            # remains the legacy config key (forwarded as ``keywords=``), which
            # the real strategy ignores; both stay supported for back-compat.
            queries = _coerce_keyword_list(config.get("queries"))
            keywords = _coerce_keyword_list(config.get("keywords"))
            # P1.8 yield provenance: optional ``keyword → id`` map forwarded
            # alongside ``queries``. Only passed when present so the legacy call
            # shape stays byte-identical for non-planner recipes.
            keyword_ids = _coerce_keyword_id_map(config.get("keyword_ids"))
            extra_ids: dict[str, Any] = {"keyword_ids": keyword_ids} if keyword_ids else {}
            if queries is not None:
                items = await self._search.discover(
                    profile, limit=limit, query=query, queries=queries, **extra_ids
                )
            elif keywords is not None:
                items = await self._search.discover(
                    profile, limit=limit, query=query, keywords=keywords, **extra_ids
                )
            else:
                items = await self._search.discover(profile, limit=limit, query=query)
        elif strategy == "feed":
            items = await self._feed.discover(profile, limit=limit)
        elif strategy == "creator":
            handle = str(config.get("handle", "") or "")
            items = await self._creator.discover(profile, limit=limit, handle=handle)
        else:
            logger.warning(
                "XAdapter: unknown strategy %r (expected search/feed/creator)",
                strategy,
            )
            return []

        # Defensive: every X item must carry source_platform="twitter" so the
        # mixed-source pool attributes it correctly even if a strategy forgot.
        for item in items:
            if not item.source_platform:
                item.source_platform = _SOURCE_TYPE
        return items
