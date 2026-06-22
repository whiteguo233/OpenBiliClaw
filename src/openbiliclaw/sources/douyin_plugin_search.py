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
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib import error, request

from openbiliclaw.sources.dy_tasks import DyTaskQueue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


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

    @property
    def cookie(self) -> str:
        """Expose direct cookie for existing diagnostics/tests."""
        return self._direct_client.cookie

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        """Search via the browser plugin; direct-cookie fallback is opt-in diagnostics only."""
        keyword = keyword.strip()
        if not keyword or limit <= 0:
            return []

        plugin_items = await self._search_via_plugin(keyword, limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        if self._allow_direct_fallback:
            logger.info("douyin plugin search empty; falling back to direct-cookie search")
            return await self._direct_client.search_aweme(keyword, limit=limit)
        logger.info("douyin plugin search empty; direct-cookie fallback disabled")
        return []

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        """Resolve hot candidates through the plugin; fallback is opt-in diagnostics only."""
        if limit <= 0:
            return []

        hot_terms = await self._load_hot_terms(limit=_hot_seed_count(limit))
        plugin_items = await self._hot_via_plugin(hot_terms, limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        if self._allow_direct_fallback:
            logger.info("douyin plugin hot empty; falling back to direct-cookie hot")
            return await self._direct_client.get_hot_board(limit=limit)
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
            return []

        plugin_items = await self._feed_via_plugin(limit=max(1, limit))
        if plugin_items:
            return plugin_items[:limit]

        fallback = getattr(self._direct_client, "get_recommend_feed", None)
        if self._allow_direct_fallback and callable(fallback):
            logger.info("douyin plugin feed empty; falling back to direct-cookie feed")
            typed_fallback = cast(
                "Callable[..., Awaitable[list[dict[str, object]]]]",
                fallback,
            )
            return await typed_fallback(limit=limit)
        logger.info("douyin plugin feed empty; direct-cookie fallback disabled")
        return []

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
            return []

        if not task_id:
            logger.info("douyin plugin search skipped: task budget exhausted")
            if self._raise_on_budget:
                # Distinguishable signal for the unified keyword planner fetch
                # path: budget exhausted → no search ran → roll the word back.
                raise DouyinBudgetExhausted("douyin plugin search task budget exhausted")
            return []

        with suppress(Exception):
            self._kick()

        result = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        return [item for item in awemes if item is not None]

    async def _load_hot_terms(self, *, limit: int) -> list[dict[str, object]]:
        try:
            return await self._direct_client.get_hot_terms(limit=limit)
        except Exception as exc:
            logger.info("douyin hot terms fetch failed: %s", exc)
            return []

    async def _hot_via_plugin(
        self,
        hot_terms: list[dict[str, object]],
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        hot_items = _normalize_hot_task_items(hot_terms)
        if not hot_items:
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
            return []

        if not task_id:
            logger.info("douyin plugin hot skipped: task budget exhausted")
            return []

        with suppress(Exception):
            self._kick()

        result = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        return [item for item in awemes if item is not None]

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
            return []

        if not task_id:
            logger.info("douyin plugin feed skipped: task budget exhausted")
            return []

        with suppress(Exception):
            self._kick()

        result = await self._wait_for_task(queue, task_id)
        videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
        awemes = [plugin_search_item_to_aweme(video) for video in videos]
        return [item for item in awemes if item is not None]

    async def _wait_for_task(self, queue: DyTaskQueue, task_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self._wait_seconds
        while True:
            task = queue.get(task_id)
            status = str((task or {}).get("status", "")).strip()
            if status in {"completed", "failed"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                return {}
            await asyncio.sleep(self._poll_interval_seconds)

        if not task or task.get("status") != "completed":
            return {}
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            return {}
        return result if isinstance(result, dict) else {}

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
    """Best-effort wake-up for the extension dispatcher."""
    req = request.Request(
        "http://127.0.0.1:8420/api/sources/dy/kick",
        method="POST",
        data=b"",
    )
    with suppress(error.URLError, TimeoutError, OSError):
        request.urlopen(req, timeout=1.0).close()


def _normalize_hot_task_items(hot_terms: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for term in hot_terms:
        sentence_id = str(
            term.get("sentence_id") or term.get("sentenceId") or term.get("id") or ""
        ).strip()
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
