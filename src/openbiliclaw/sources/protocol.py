"""Core abstractions for multi-source content discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile


@dataclass
class SourceRecipe:
    """A persistent subscription to a content source.

    Recipes describe *what* to fetch and *where* to fetch it from.
    They are created by the system (built-in defaults), by the user
    (settings UI), or by the agent (conversational subscription).

    Attributes:
        id: Unique identifier (UUID string).
        source_type: Platform key, e.g. ``"bilibili"``, ``"xiaohongshu"``, ``"web"``.
        name: Human-readable label, e.g. "B站-搜索" or "小红书-机械键盘".
        strategy: Discovery strategy within the adapter, e.g. ``"search"``,
            ``"trending"``, ``"feed"``, ``"explore"``, ``"related_chain"``.
        config: Adapter-specific parameters (search query, feed URL, etc.).
        target_share: Weight used by the scheduler to distribute pool slots.
        enabled: Whether this recipe participates in discovery cycles.
        created_by: Origin — ``"system"`` for built-in defaults, ``"user"``
            for manual creation, ``"agent"`` for conversationally created.
        created_at: ISO-8601 timestamp.
        last_fetched_at: ISO-8601 timestamp of the most recent successful fetch.
    """

    id: str
    source_type: str
    name: str
    strategy: str
    config: dict[str, Any] = field(default_factory=dict)
    target_share: int = 4
    enabled: bool = True
    created_by: str = "system"
    created_at: str = ""
    last_fetched_at: str = ""


SourceFetchStatus = Literal[
    "ok",
    "empty",
    "not_modified",
    "degraded",
    "login_required",
    "rate_limited",
    "failed",
]


@dataclass
class SourceFetchResult:
    """Structured source outcome used by durable recipe schedulers.

    A naked empty list cannot say whether a source was truly empty, unchanged,
    blocked by login, rate limited, or broken.  Existing adapters may keep the
    legacy ``fetch() -> list`` protocol; recipe-driven producers use this
    result when an adapter exposes ``fetch_result``.
    """

    items: list[DiscoveredContent] = field(default_factory=list)
    status: SourceFetchStatus = "ok"
    scope_complete: bool = True
    etag: str = ""
    last_modified: str = ""
    validator_url: str = ""
    cursor: dict[str, Any] = field(default_factory=dict)
    # Degraded sitemap runs carry a bounded recovery frontier and clean-sweep
    # proof. The durable canonical cursor and HTTP validators remain untouched
    # until every unit in the current root topology completes without error.
    retry_cursor: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    retry_after_seconds: int = 0
    detail: str = ""


@runtime_checkable
class SourceAdapter(Protocol):
    """Unified interface for content source adapters.

    Every adapter (Bilibili, Xiaohongshu, generic web, …) implements this
    protocol.  The discovery engine and scheduler interact exclusively
    through this interface — everything above is source-agnostic.
    """

    @property
    def source_type(self) -> str:
        """Platform identifier, e.g. ``"bilibili"``."""
        ...

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch content according to *recipe* and return normalised items.

        Implementations are free to use APIs, browser automation, or any
        other mechanism.  The returned items **must** have ``content_id``,
        ``content_url``, and ``source_platform`` populated.

        Args:
            recipe: The subscription recipe that defines what to fetch.
            profile: Current user soul profile for relevance guidance.
            limit: Maximum number of items to return.

        Returns:
            List of discovered content items ready for evaluation.
        """
        ...
