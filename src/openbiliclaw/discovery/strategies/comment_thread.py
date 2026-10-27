"""Comment-thread discovery route for the Blue Fish Lab.

This is the fish's route on the Bilibili discovery surface, but only the
*reading* half of it. The pass does two things and publishes neither:

1. It turns comment sections into candidates. Commenters routinely name a
   video ("这个 UP 早期那期更好，BV1xx…"), and those ids are the cheapest
   high-precision supply the Bilibili surface has: one comment page can hand
   back a dozen related videos the main pipeline has never scored. They are
   returned as ordinary :class:`DiscoveredContent`, so the owning engine's
   batch evaluator scores them exactly like search or related-chain results —
   this route never bypasses evaluation.
2. It leaves one *reply plan* behind: the seed video plus the comment
   snapshot it just fetched. Actually speaking in public is the action
   layer's job (``fish.engine``), which applies the account isolation, quota,
   interval, breaker and ``dry_run`` gates before anything is posted.

Why a plan instead of posting here: ``discover()`` is a read-only producer
invoked concurrently by refresh, init bootstrap, the candidate pipeline and
CLI. Running a public side effect inside it would eventually post during an
unrelated flow, and strategy exceptions are swallowed by ``asyncio.gather``.

The route is inert unless the lab is enabled and has its own cookie; with the
default (off) config this returns immediately without touching the network.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryStrategy,
)
from openbiliclaw.discovery.strategies._utils import clean_text, to_int
from openbiliclaw.fish.account import FishAccount
from openbiliclaw.fish.comments import usable_comments
from openbiliclaw.fish.config import FishConfig, load_fish_config
from openbiliclaw.fish.engine import FishCandidate
from openbiliclaw.fish.plan import FishPlan, FishPlanStore, PlannedComment
from openbiliclaw.published_time import normalize_published_time

if TYPE_CHECKING:
    from openbiliclaw.bilibili.api import CommentInfo, VideoInfo
    from openbiliclaw.fish.store import FishLedgerView, FishStore
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

STRATEGY_NAME = "comment_thread"

# A BV id is "BV" + 10 base58 characters. The prefix is matched
# case-insensitively because people type "bv1xx…" all the time; the id part is
# kept verbatim because it is case-sensitive. The lookahead stops
# "BV1xx411c7mDextra" from being truncated into a valid-looking id.
_COMMENT_BVID_RE = re.compile(r"[Bb][Vv]([0-9A-Za-z]{10})(?![0-9A-Za-z])")

# One pass visits at most this many seed comment sections. Three keeps the
# route to a single-digit number of API calls per tick; the seeds themselves
# come from the already-ranked pool, so breadth is not the goal.
MAX_SEEDS = 3

# A single comment section can mention a lot of videos; each mention costs one
# ``/view`` call to turn into an evaluable candidate. Eight is the point where
# a tick still costs notably less than a search-page sweep.
MAX_MENTIONS = 8


class SupportsCommentThreadClient(Protocol):
    """The read-only slice of the Bilibili client this route needs."""

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]: ...

    async def get_video_info(self, bvid: str) -> VideoInfo: ...

    async def close(self) -> None: ...


SeedProvider = Callable[[int], Awaitable[list[FishCandidate]]]
ClientFactory = Callable[[str], SupportsCommentThreadClient]


def extract_mentioned_bvids(text: str) -> list[str]:
    """Return the BV ids named in one comment, in order, prefixed ``BV``."""

    return [f"BV{match.group(1)}" for match in _COMMENT_BVID_RE.finditer(str(text or ""))]


class CommentThreadStrategy(DiscoveryStrategy):
    """Read comment sections: produce candidates and one reply plan."""

    def __init__(
        self,
        *,
        data_dir: Path,
        seed_provider: SeedProvider,
        client_factory: ClientFactory,
        plan_store: FishPlanStore | None = None,
        store: FishStore | None = None,
        max_seeds: int = MAX_SEEDS,
        max_mentions: int = MAX_MENTIONS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._seed_provider = seed_provider
        self._client_factory = client_factory
        self._plan_store = plan_store or FishPlanStore(self._data_dir)
        # The ledger is the only source of "already answered". Without it the
        # route would keep planning the same top-ranked seed every tick, and
        # the action layer would reject each plan (per_video_limit) before
        # falling back to the old pool scan.
        self._store = store
        self._max_seeds = max(1, int(max_seeds))
        self._max_mentions = max(1, int(max_mentions))
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    @property
    def source_platform(self) -> str:
        return "bilibili"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Read comment sections, return named videos, leave one reply plan.

        Args:
            profile: Current soul profile. Deliberately unused: the relevance
                decision belongs to the owning engine's evaluator, not to this
                route. The parameter is the strategy contract.
            limit: Maximum candidates to return.

        Returns:
            Videos mentioned by commenters, ready for normal evaluation.
        """

        del profile  # kept for the strategy contract
        config = load_fish_config(self._data_dir)
        if not config.enabled:
            return []
        cookie = FishAccount(self._data_dir).load_cookie()
        if not cookie:
            logger.debug("comment_thread: lab has no cookie, skipping")
            return []
        target = max(1, int(limit))
        view = self._ledger_view()
        # Ask for more seeds than one pass visits so answered videos can be
        # filtered out without starving the pass.
        try:
            seeds = await self._seed_provider(max(self._max_seeds * 4, target))
        except Exception as exc:  # noqa: BLE001 - a route must not take refresh down
            logger.warning("comment_thread: seed lookup failed: %s", exc)
            return []
        seeds = self._unanswered(seeds, view=view, config=config)
        if not seeds:
            return []

        client = self._client_factory(cookie)
        try:
            return await self._scan(client, seeds, config=config, limit=target, view=view)
        except Exception as exc:  # noqa: BLE001
            # Strategy exceptions are swallowed by the engine's gather; log the
            # real cause here or the route dies silently.
            logger.warning("comment_thread: scan failed: %s", exc)
            return []
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                logger.debug("comment_thread: client close failed", exc_info=True)

    async def _scan(
        self,
        client: SupportsCommentThreadClient,
        seeds: list[FishCandidate],
        *,
        config: FishConfig,
        limit: int,
        view: FishLedgerView | None,
    ) -> list[DiscoveredContent]:
        cap = min(limit, self._max_mentions)
        discovered: list[DiscoveredContent] = []
        seen: set[str] = set()
        visited: set[str] = set()
        plan: FishPlan | None = None

        for seed in seeds:
            if not seed.bvid or seed.bvid in visited:
                continue
            visited.add(seed.bvid)
            comments = await self._comments(client, seed.bvid, limit=config.comments_per_video)
            if not comments:
                continue
            if plan is None:
                usable = usable_comments(
                    comments,
                    config=config,
                    answered=view.answered if view is not None else frozenset(),
                    bvid=seed.bvid,
                )
                if usable:
                    plan = self._plan(seed, usable, source=self.name)
            for bvid in self._mentioned(comments, seed=seed.bvid):
                if len(discovered) >= cap:
                    break
                if bvid in seen:
                    continue
                seen.add(bvid)
                item = await self._content(client, bvid)
                if item is not None:
                    discovered.append(item)
            if plan is not None and len(discovered) >= cap:
                break

        if plan is not None:
            self._plan_store.enqueue(plan)
        return discovered

    def _ledger_view(self) -> FishLedgerView | None:
        """Scan the fish ledger, tolerating a missing / unreadable file."""

        if self._store is None:
            return None
        try:
            return self._store.view(self._clock().date())
        except Exception:  # noqa: BLE001 - discovery must not fail on a bad ledger
            logger.debug("comment_thread: ledger unavailable", exc_info=True)
            return None

    def _unanswered(
        self,
        seeds: list[FishCandidate],
        *,
        view: FishLedgerView | None,
        config: FishConfig,
    ) -> list[FishCandidate]:
        """Keep seeds the fish may still speak under, capped at ``max_seeds``."""

        out: list[FishCandidate] = []
        seen: set[str] = set()
        for seed in seeds:
            if seed.bvid in seen:
                continue
            seen.add(seed.bvid)
            if view is not None and (
                view.per_video_total.get(seed.bvid, 0) >= config.per_video_limit
            ):
                continue
            out.append(seed)
            if len(out) >= self._max_seeds:
                break
        return out

    async def _comments(
        self,
        client: SupportsCommentThreadClient,
        bvid: str,
        *,
        limit: int,
    ) -> list[CommentInfo]:
        try:
            return await client.get_video_comments(bvid, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.info("comment_thread: comments unavailable for %s: %s", bvid, exc)
            return []

    async def _content(
        self,
        client: SupportsCommentThreadClient,
        bvid: str,
    ) -> DiscoveredContent | None:
        try:
            info = await client.get_video_info(bvid)
        except Exception as exc:  # noqa: BLE001
            logger.info("comment_thread: %s is not resolvable: %s", bvid, exc)
            return None
        title = clean_text(str(getattr(info, "title", "") or ""))
        if not title:
            # A deleted / private video still answers /view with a shell; an
            # empty title cannot become an evaluable candidate.
            return None
        description = clean_text(str(getattr(info, "description", "") or ""))[:500]
        published = normalize_published_time(
            getattr(info, "pub_date", ""),
            now=self._clock(),
        )
        return DiscoveredContent(
            bvid=str(getattr(info, "bvid", "") or bvid),
            title=title,
            up_name=clean_text(str(getattr(info, "up_name", "") or "")),
            up_mid=to_int(getattr(info, "up_mid", 0)),
            cover_url=str(getattr(info, "cover_url", "") or ""),
            duration=to_int(getattr(info, "duration", 0)),
            view_count=to_int(getattr(info, "view_count", 0)),
            like_count=to_int(getattr(info, "like_count", 0)),
            favorite_count=to_int(getattr(info, "favorite_count", 0)),
            danmaku_count=to_int(getattr(info, "danmaku_count", 0)),
            share_count=to_int(getattr(info, "share_count", 0)),
            description=description,
            style_key=ContentDiscoveryEngine.infer_style_key(
                title=title,
                description=description,
                source_strategy=self.name,
            ),
            source_strategy=self.name,
            source_platform="bilibili",
            content_type="video",
            published_at=published.published_at,
            published_label=published.published_label,
        )

    @staticmethod
    def _mentioned(comments: list[CommentInfo], *, seed: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for comment in comments:
            for bvid in extract_mentioned_bvids(comment.message):
                if bvid == seed or bvid in seen:
                    continue
                seen.add(bvid)
                out.append(bvid)
        return out

    def _plan(
        self,
        seed: FishCandidate,
        comments: list[CommentInfo],
        *,
        source: str,
    ) -> FishPlan:
        planned = tuple(
            PlannedComment(
                rpid=int(item.rpid),
                uname=str(item.uname or ""),
                message=str(item.message or "").strip(),
                like_count=int(item.like_count or 0),
                ctime=int(item.ctime or 0),
                root=int(getattr(item, "root", 0) or 0),
                parent=int(getattr(item, "parent", 0) or 0),
            )
            for item in comments
        )
        return FishPlan(
            bvid=seed.bvid,
            title=seed.title,
            up_name=seed.up_name,
            description=seed.description[:500],
            topic=seed.topic,
            recommendation_id=int(seed.recommendation_id),
            comments=planned,
            created_at=self._clock(),
            source=source,
        )
