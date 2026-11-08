"""Plugin-backed Douyin search discovery helper.

Direct-cookie Douyin search can return HTTP 200 with a soft-empty
``antispam_check / hit_shark`` payload. The browser extension path is
more robust because it runs inside the logged-in page and can use the
page's MAIN-world acrawler signer. This module exposes that path behind
the same small client protocol used by ``DouyinDirectStrategy``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from urllib import error, request

from openbiliclaw.sources.dy_tasks import DyTaskQueue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# One hot term stays newsworthy ≈ half a day; re-seeding the same
# ``sentence_id`` sooner only yields dedupe-absorbed repeats (2026-07-17 log
# evidence: sentence_id=2574280 seeded 3 consecutive hot runs, runs 2-3
# produced only duplicates the pool dedupe dropped). Calibrated against that
# incident; revisit if Douyin's hot-board refresh cadence changes.
_HOT_SEED_REUSE_TTL_SECONDS = 6 * 3600
# Pull a wider hot-board pool than we seed so rotation has fresh terms to pick
# from after filtering out the recently-used ones.
_HOT_SEED_POOL_LIMIT = 30
DouyinTaskOutcome = Literal["used", "empty", "timeout", "failed", "budget_exhausted"]


class DouyinBudgetExhausted(Exception):  # noqa: N818 - plan-mandated name (no Error suffix)
    """Plugin-search task budget exhausted — a *distinguishable* "not searched".

    Raised (only when ``raise_on_budget`` is set on the client — the unified
    keyword planner fetch path) when the plugin ``search`` task could not be
    enqueued because the daily task budget is spent, so NO search ran. This is
    deliberately distinct from a genuinely empty result (``[]``): the unified
    keyword planner caller rolls the claimed keyword back to ``pending`` instead
    of burning it as ``used``. Legacy callers leave ``raise_on_budget`` off and
    keep the historical "budget → fall back to direct-cookie" behavior.
    """


def _normalize_daily_budget(value: int) -> int:
    """Normalize daily task budget; 0 disables the per-day cap."""

    return max(0, int(value))


class SupportsDouyinSearchFallback(Protocol):
    """Subset of the direct client used as fallback/delegate."""

    cookie: str

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]: ...
    async def get_hot_terms(self, *, limit: int = 30) -> list[dict[str, object]]: ...
    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]: ...

    async def get_creator_posts(
        self,
        sec_uid: str,
        *,
        limit: int = 30,
    ) -> list[dict[str, object]]: ...

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]: ...


def plugin_search_item_to_aweme(item: dict[str, Any]) -> dict[str, object] | None:
    """Map one plugin ``dy_search`` item to aweme-like JSON."""
    aweme_id = str(item.get("aweme_id", "") or "").strip()
    if not aweme_id:
        return None
    title = str(item.get("title", "") or "").strip()
    author = str(item.get("author", "") or "").strip()
    author_sec_uid = str(item.get("author_sec_uid", "") or "").strip()
    cover_url = str(item.get("cover_url", "") or "").strip()

    aweme: dict[str, object] = {
        "aweme_id": aweme_id,
        "desc": title,
        "author": {"nickname": author, "sec_uid": author_sec_uid},
    }
    published_time = item.get("published_at") or item.get("create_time")
    if published_time not in (None, ""):
        aweme["create_time"] = published_time
    if cover_url:
        aweme["video"] = {"cover": {"url_list": [cover_url]}}
    else:
        aweme["video"] = {}
    statistics = {
        "play_count": _to_int(item.get("view_count")),
        "digg_count": _to_int(item.get("like_count")),
        "collect_count": _to_int(item.get("collect_count")),
        "comment_count": _to_int(item.get("comment_count")),
        "share_count": _to_int(item.get("share_count")),
    }
    if any(statistics.values()):
        aweme["statistics"] = statistics
    return aweme


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", "")))
    except (TypeError, ValueError):
        return 0


class DouyinPluginSearchClient:
    """Client wrapper that resolves ``search_aweme`` through dy_tasks."""

    search_source_strategy = "dy-plugin-search"
    hot_source_strategy = "dy-plugin-hot-related"
    feed_source_strategy = "dy-plugin-feed"

    def __init__(
        self,
        *,
        database: Any,
        direct_client: SupportsDouyinSearchFallback,
        wait_seconds: float = 120.0,
        poll_interval_seconds: float = 0.5,
        daily_budget: int = 100,
        daily_search_budget: int | None = None,
        daily_hot_budget: int | None = None,
        daily_feed_budget: int | None = None,
        kick: Callable[[], None] | None = None,
        raise_on_budget: bool = False,
        allow_direct_fallback: bool = False,
    ) -> None:
        self._database = database
        self._direct_client = direct_client
        self._wait_seconds = max(0.0, float(wait_seconds))
        self._poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        # Unified keyword planner fetch path: surface plugin-search budget
        # exhaustion as ``DouyinBudgetExhausted`` (distinguishable from an empty
        # result) so a claimed keyword can be rolled back instead of burned.
        # OFF by default → legacy "budget → fall back to direct-cookie".
        self._raise_on_budget = bool(raise_on_budget)
        self._allow_direct_fallback = bool(allow_direct_fallback)
        self._daily_search_budget = _normalize_daily_budget(
            daily_search_budget if daily_search_budget is not None else daily_budget
        )
        self._daily_hot_budget = _normalize_daily_budget(
            daily_hot_budget if daily_hot_budget is not None else daily_budget
        )
        self._daily_feed_budget = _normalize_daily_budget(
            daily_feed_budget if daily_feed_budget is not None else daily_budget
        )
        self._kick = kick or kick_douyin_task_dispatcher
        # Hot-seed rotation state: sentence_id -> monotonic timestamp of last
        # use. Filters recently-seeded hot terms so consecutive hot runs don't
        # re-pick the same top term and yield only dedupe-absorbed repeats.
        self._recent_hot_sentence_ids: dict[str, float] = {}
        self.last_search_outcome: DouyinTaskOutcome = "empty"
        self.last_hot_outcome: DouyinTaskOutcome = "empty"
        self.last_feed_outcome: DouyinTaskOutcome = "empty"

    @property
    def cookie(self) -> str:
        """Expose direct cookie for existing diagnostics/tests."""
        return self._direct_client.cookie

    @property
    def discovery_cycle_timeout_seconds(self) -> float:
        """Maximum wall time one strategy cycle may spend on plugin tasks.

        A strategy can fan out into several search/hot/feed calls. Exposing the
        shared budget prevents those calls from each consuming a fresh full
        wait window when the extension is offline.
        """
        return self._wait_seconds + min(self._poll_interval_seconds, 1.0) + 0.25

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        """Search via the browser plugin; direct-cookie fallback is opt-in diagnostics only."""
        keyword = keyword.strip()
        if not keyword or limit <= 0:
            self.last_search_outcome = "empty"
            return []
        if self._discovery_is_cooling():
            self.last_search_outcome = "failed"
            return []

        plugin_items = await self._search_via_plugin(keyword, limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        if self._allow_direct_fallback and not self._discovery_is_cooling():
            logger.info("douyin plugin search empty; falling back to direct-cookie search")
            fallback_items = await self._direct_client.search_aweme(keyword, limit=limit)
            self.last_search_outcome = "used" if fallback_items else "empty"
            return fallback_items
        logger.info("douyin plugin search empty; direct-cookie fallback disabled")
        return []

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        """Resolve hot candidates through the plugin; fallback is opt-in diagnostics only."""
        if limit <= 0:
            self.last_hot_outcome = "empty"
            return []
        if self._discovery_is_cooling():
            self.last_hot_outcome = "failed"
            return []

        hot_terms = await self._load_hot_terms(limit=_HOT_SEED_POOL_LIMIT)
        # Prioritize actionable seeds across the whole board before truncating
        # the rotation window; sorting only the selected rows loses later seeds.
        hot_terms = _normalize_hot_task_items(hot_terms)
        hot_terms = self._rotate_hot_terms(hot_terms, seed_count=_hot_seed_count(limit))
        plugin_items = await self._hot_via_plugin(hot_terms, limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        if self._allow_direct_fallback and not self._discovery_is_cooling():
            logger.info("douyin plugin hot empty; falling back to direct-cookie hot")
            fallback_items = await self._direct_client.get_hot_board(limit=limit)
            self.last_hot_outcome = "used" if fallback_items else "empty"
            return fallback_items
        logger.info("douyin plugin hot empty; direct-cookie fallback disabled")
        return []

    async def get_creator_posts(
        self,
        sec_uid: str,
        *,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        """Delegate creator timeline to the direct client."""
        return await self._direct_client.get_creator_posts(sec_uid, limit=limit)

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        """Resolve home recommendation feed through the browser plugin."""
        if limit <= 0:
            self.last_feed_outcome = "empty"
            return []
        if self._discovery_is_cooling():
            self.last_feed_outcome = "failed"
            return []

        plugin_items = await self._feed_via_plugin(limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        fallback = getattr(self._direct_client, "get_recommend_feed", None)
        if self._allow_direct_fallback and callable(fallback) and not self._discovery_is_cooling():
            logger.info("douyin plugin feed empty; falling back to direct-cookie feed")
            typed_fallback = cast(
                "Callable[..., Awaitable[list[dict[str, object]]]]",
                fallback,
            )
            fallback_items = await typed_fallback(limit=limit)
            self.last_feed_outcome = "used" if fallback_items else "empty"
            return fallback_items
        logger.info("douyin plugin feed empty; direct-cookie fallback disabled")
        return []

    def _discovery_is_cooling(self) -> bool:
        retry_at = DyTaskQueue(self._database).discovery_retry_at()
        if retry_at is None:
            return False
        logger.info("douyin discovery paused until %s after browser failure", retry_at.isoformat())
        return True

    async def _search_via_plugin(self, keyword: str, *, limit: int) -> list[dict[str, object]]:
        try:
            queue = DyTaskQueue(self._database)
            self._expire_stale_discovery_tasks(queue)
            task_id = queue.enqueue_with_id(
                "search",
                {
                    "keywords": [keyword],
                    "max_items_per_keyword": max(1, int(limit)),
                },
                daily_budget=self._daily_search_budget,
            )
        except Exception as exc:
            logger.info("douyin plugin search enqueue failed: %s", exc)
            self.last_search_outcome = "failed"
            return []

        if not task_id:
            logger.info("douyin plugin search skipped: task budget exhausted")
            self.last_search_outcome = "budget_exhausted"
            if self._raise_on_budget:
                # Distinguishable signal for the unified keyword planner fetch
                # path: budget exhausted → no search ran → roll the word back.
                raise DouyinBudgetExhausted("douyin plugin search task budget exhausted")
            return []

        with suppress(Exception):
            self._kick()

        result, outcome = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        items = [item for item in awemes if item is not None]
        self.last_search_outcome = ("used" if items else "empty") if outcome == "used" else outcome
        return items

    async def _load_hot_terms(self, *, limit: int) -> list[dict[str, object]]:
        try:
            return await self._direct_client.get_hot_terms(limit=limit)
        except Exception as exc:
            logger.info("douyin hot terms fetch failed: %s", exc)
            return []

    def _rotate_hot_terms(
        self,
        hot_terms: list[dict[str, object]],
        *,
        seed_count: int,
    ) -> list[dict[str, object]]:
        """Pick ``seed_count`` hot terms, deprioritizing recently-used ones.

        Fresh terms (no recent reuse within the TTL) come first; if fewer than
        ``seed_count`` are fresh, recently-used terms top up the tail — we prefer
        stale seeds over returning fewer than are available. The sentence_ids of
        the terms actually chosen are recorded so the next run rotates past them.
        """
        self._prune_recent_hot_seeds()
        if seed_count <= 0:
            return []
        # The runtime deliberately recreates this client on every round.
        # Hydrate from enqueued choices so that lifecycle cannot reset the
        # existing six-hour rotation policy (including across processes).
        recent_ids = set(self._recent_hot_sentence_ids)
        recent_ids.update(
            DyTaskQueue(self._database).recent_hot_sentence_ids(
                within_seconds=_HOT_SEED_REUSE_TTL_SECONDS
            )
        )
        fresh: list[dict[str, object]] = []
        recent: list[dict[str, object]] = []
        for term in hot_terms:
            sentence_id = _hot_term_sentence_id(term)
            if sentence_id and sentence_id in recent_ids:
                recent.append(term)
            else:
                fresh.append(term)
        chosen = (fresh + recent)[:seed_count]
        now = time.monotonic()
        for term in chosen:
            sentence_id = _hot_term_sentence_id(term)
            if sentence_id:
                self._recent_hot_sentence_ids[sentence_id] = now
        return chosen

    def _prune_recent_hot_seeds(self) -> None:
        cutoff = time.monotonic() - _HOT_SEED_REUSE_TTL_SECONDS
        expired = [sid for sid, ts in self._recent_hot_sentence_ids.items() if ts <= cutoff]
        for sentence_id in expired:
            del self._recent_hot_sentence_ids[sentence_id]

    async def _hot_via_plugin(
        self,
        hot_terms: list[dict[str, object]],
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        hot_items = _normalize_hot_task_items(hot_terms)
        if not hot_items:
            self.last_hot_outcome = "empty"
            return []

        try:
            queue = DyTaskQueue(self._database)
            self._expire_stale_discovery_tasks(queue)
            task_id = queue.enqueue_with_id(
                "hot",
                {
                    "hot_items": hot_items,
                    "max_items_per_hot": max(1, min(20, int(limit))),
                    "max_items": max(1, int(limit)),
                },
                daily_budget=self._daily_hot_budget,
            )
        except Exception as exc:
            logger.info("douyin plugin hot enqueue failed: %s", exc)
            self.last_hot_outcome = "failed"
            return []

        if not task_id:
            logger.info("douyin plugin hot skipped: task budget exhausted")
            self.last_hot_outcome = "budget_exhausted"
            return []

        with suppress(Exception):
            self._kick()

        result, outcome = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        items = [item for item in awemes if item is not None]
        self.last_hot_outcome = ("used" if items else "empty") if outcome == "used" else outcome
        return items

    async def _feed_via_plugin(self, *, limit: int) -> list[dict[str, object]]:
        try:
            queue = DyTaskQueue(self._database)
            self._expire_stale_discovery_tasks(queue)
            task_id = queue.enqueue_with_id(
                "feed",
                {
                    "max_items": max(1, int(limit)),
                },
                daily_budget=self._daily_feed_budget,
            )
        except Exception as exc:
            logger.info("douyin plugin feed enqueue failed: %s", exc)
            self.last_feed_outcome = "failed"
            return []

        if not task_id:
            logger.info("douyin plugin feed skipped: task budget exhausted")
            self.last_feed_outcome = "budget_exhausted"
            return []

        with suppress(Exception):
            self._kick()

        result, outcome = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        items = [item for item in awemes if item is not None]
        self.last_feed_outcome = ("used" if items else "empty") if outcome == "used" else outcome
        return items

    async def _wait_for_task(
        self,
        queue: DyTaskQueue,
        task_id: str,
    ) -> tuple[dict[str, Any], DouyinTaskOutcome]:
        deadline = asyncio.get_running_loop().time() + self._wait_seconds
        outcome: DouyinTaskOutcome = "used"
        try:
            while True:
                task = queue.get(task_id)
                status = str((task or {}).get("status", "")).strip()
                if status in {"completed", "failed"}:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    if queue.fail(
                        task_id,
                        error="wait_timeout",
                        debug={"wait_seconds": self._wait_seconds},
                    ):
                        task = queue.get(task_id)
                        outcome = "timeout"
                        break
                    # A terminal callback may have won the race with the timeout
                    # transition. Re-read it instead of misreporting success as
                    # a timeout.
                    task = queue.get(task_id)
                    if str((task or {}).get("status", "")).strip() in {
                        "completed",
                        "failed",
                    }:
                        break
                    return {}, "timeout"
                await asyncio.sleep(self._poll_interval_seconds)
        except asyncio.CancelledError:
            # A cycle-level deadline or process cancellation must not strand an
            # extension task in pending/in_progress. ``fail`` is atomic and
            # refuses to overwrite a callback that already reached a terminal
            # state.
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="wait_cancelled",
                    debug={"wait_seconds": self._wait_seconds},
                )
            raise

        if not task:
            return {}, "failed"
        if task.get("status") != "completed" and outcome != "timeout":
            outcome = "failed"
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            return {}, "failed"
        if not isinstance(result, dict):
            return {}, "failed"
        return result, outcome

    def _expire_stale_discovery_tasks(self, queue: DyTaskQueue) -> None:
        expire = getattr(queue, "expire_stale_pending", None)
        if not callable(expire):
            return
        with suppress(Exception):
            expired = expire(
                ("search", "hot", "feed"),
                older_than_seconds=max(60.0, self._wait_seconds),
            )
            if expired:
                logger.info("douyin plugin expired %d stale pending discovery task(s)", expired)


def kick_douyin_task_dispatcher() -> None:
    """Best-effort wake-up on the configured local API port."""
    daemon_port = 8420
    with suppress(Exception):
        from openbiliclaw.config import load_config

        configured_port = int(load_config().api.port)
        if 1 <= configured_port <= 65535:
            daemon_port = configured_port
    req = request.Request(
        f"http://127.0.0.1:{daemon_port}/api/sources/dy/kick",
        method="POST",
        data=b"",
    )
    with suppress(error.URLError, TimeoutError, OSError):
        request.urlopen(req, timeout=1.0).close()


def _hot_term_sentence_id(term: dict[str, object]) -> str:
    """Extract the normalized hot-term sentence id (matches task normalization)."""
    return str(term.get("sentence_id") or term.get("sentenceId") or term.get("id") or "").strip()


def _normalize_hot_task_items(hot_terms: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for term in hot_terms:
        sentence_id = _hot_term_sentence_id(term)
        if not sentence_id or sentence_id in seen:
            continue
        seen.add(sentence_id)
        item: dict[str, object] = {"sentence_id": sentence_id}
        word = str(term.get("word") or term.get("sentence") or "").strip()
        if word:
            item["word"] = word
        if "hot_value" in term:
            item["hot_value"] = term["hot_value"]
        seed_aweme_id = str(
            term.get("seed_aweme_id") or term.get("group_id") or term.get("aweme_id") or ""
        ).strip()
        if seed_aweme_id:
            item["seed_aweme_id"] = seed_aweme_id
        items.append(item)
    return sorted(items, key=lambda item: not bool(item.get("seed_aweme_id")))


def _hot_seed_count(limit: int) -> int:
    """Return how many hot-board rows to expand for one discovery request."""
    if limit <= 0:
        return 0
    if limit <= 10:
        return 5
    return 2
