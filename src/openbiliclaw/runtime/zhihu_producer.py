"""Runtime Zhihu discovery producer.

Zhihu discovery is plugin-backed because the useful APIs rely on the user's
logged-in browser session. The producer enqueues search tasks into
``zhihu_tasks``, wakes the extension, waits for the task terminal result, and
hands raw candidates to the shared discovery candidate pool.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib import error, request

from openbiliclaw.runtime.keyword_fetch import PLATFORM_ZHIHU
from openbiliclaw.sources.zhihu_tasks import (
    ZhihuTaskQueue,
    recent_zhihu_creator_urls,
    recent_zhihu_related_urls,
    zhihu_discovery_items_to_contents,
)

logger = logging.getLogger(__name__)

ZHIHU_SOURCE_ORDER = ("search", "hot", "feed", "creator", "related")
ZHIHU_SOURCE_STRATEGIES = {
    "search": "zhihu-search",
    "hot": "zhihu-hot",
    "feed": "zhihu-feed",
    "creator": "zhihu-creator",
    "related": "zhihu-related",
}


@dataclass
class ZhihuDiscoveryProducer:
    """Throttle and invoke Zhihu plugin-backed discovery."""

    task_queue: Any
    soul_engine: Any
    enabled: bool = True
    sources: tuple[str, ...] = ("search",)
    min_interval_minutes: int = 60
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    daily_related_budget: int = 0
    wait_seconds: float = 180.0
    poll_interval_seconds: float = 0.5
    max_items_per_keyword: int = 20
    max_seed_count: int = 5
    candidate_pipeline: Any | None = None
    keyword_fetch: Any | None = None
    creator_seed_loader: Any | None = None
    related_seed_loader: Any | None = None
    kick: Any | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one Zhihu discovery cycle if enabled, due, and under quota."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("zhihu producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or self.max_items_per_keyword))
        source_modes = _normalize_sources(self.sources)
        claimed: list[Any] = []
        keyword_ids: dict[str, int] = {}
        all_items: list[dict[str, Any]] = []
        enqueued_task_count = 0
        skipped_reasons: list[str] = []
        coordinator = self.keyword_fetch
        for source in source_modes:
            task_type = source
            payload: dict[str, object] = {}
            daily_budget = self._daily_budget_for(source)

            if source == "search":
                keywords: list[str]
                if coordinator is not None and bool(
                    getattr(coordinator, "should_claim", lambda: False)()
                ):
                    claimed = list(coordinator.claim(PLATFORM_ZHIHU))
                    keywords = [
                        str(item.keyword).strip() for item in claimed if str(item.keyword).strip()
                    ]
                    keyword_ids = {str(item.keyword).strip(): int(item.id) for item in claimed}
                else:
                    keywords = _fallback_profile_keywords(profile, requested_limit)
                if not keywords:
                    skipped_reasons.append("no_keywords")
                    continue
                payload = {
                    "keywords": keywords,
                    "max_items_per_keyword": requested_limit,
                }
                if keyword_ids:
                    payload["source_keyword_ids"] = keyword_ids
            elif source in {"hot", "feed"}:
                payload = {"max_items": requested_limit}
            elif source == "creator":
                creator_urls = await self._load_seed_values(self.creator_seed_loader)
                if not creator_urls:
                    creator_urls = _same_run_seed_values(all_items, key="author_url")
                if not creator_urls:
                    skipped_reasons.append("no_creator_seeds")
                    continue
                payload = {
                    "creator_urls": creator_urls[: max(1, int(self.max_seed_count))],
                    "max_items_per_creator": requested_limit,
                }
            elif source == "related":
                related_urls = await self._load_seed_values(self.related_seed_loader)
                if not related_urls:
                    related_urls = _same_run_seed_values(all_items, key="url")
                if not related_urls:
                    skipped_reasons.append("no_related_seeds")
                    continue
                payload = {
                    "related_urls": related_urls[: max(1, int(self.max_seed_count))],
                    "max_items_per_seed": requested_limit,
                }

            task_id = self._enqueue_task(task_type, payload, daily_budget=daily_budget)
            if task_id is None:
                if source == "search" and coordinator is not None:
                    for item in claimed:
                        coordinator.rollback(item)
                skipped_reasons.append("budget_exhausted")
                continue

            enqueued_task_count += 1
            await self._kick_dispatcher()
            result = await self._wait_for_task(task_id)
            items = [item for item in result.get("items", []) if isinstance(item, dict)]
            all_items.extend(items)

            if source == "search" and coordinator is not None and claimed:
                self._mark_search_keywords(coordinator, claimed, items)

        self._last_run_at = datetime.now(UTC)
        if enqueued_task_count == 0:
            return self._skip(skipped_reasons[0] if skipped_reasons else "no_sources")

        contents = zhihu_discovery_items_to_contents(all_items, source_keyword_ids=keyword_ids)
        if not contents:
            return {"discovered": 0, "reason": "empty"}

        source_counts: dict[str, int] = {}
        for content in contents:
            source_counts[content.source_strategy] = (
                source_counts.get(content.source_strategy, 0) + 1
            )
        result_payload: dict[str, object] = {
            "discovered": len(contents),
            "source_counts": source_counts,
            "reason": "ok",
        }
        if self.candidate_pipeline is not None:
            enqueued = 0
            for strategy in _ordered_strategies(source_modes):
                grouped = [item for item in contents if item.source_strategy == strategy]
                if not grouped:
                    continue
                enqueued += int(
                    self.candidate_pipeline.enqueue_candidates(
                        grouped,
                        source_context=strategy,
                    )
                )
            result_payload["enqueued"] = enqueued
            if enqueued > 0:
                drain_result = await self.candidate_pipeline.drain_pending(
                    profile=profile,
                    batch_size=requested_limit,
                )
                result_payload.update(drain_result)
        return result_payload

    def _enqueue_search_task(self, keywords: list[str], keyword_ids: dict[str, int]) -> str | None:
        payload: dict[str, object] = {
            "keywords": keywords,
            "max_items_per_keyword": max(1, int(self.max_items_per_keyword)),
        }
        if keyword_ids:
            payload["source_keyword_ids"] = keyword_ids
        return self._enqueue_task("search", payload, daily_budget=int(self.daily_search_budget))

    def _enqueue_task(
        self,
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget: int,
    ) -> str | None:
        expire = getattr(self.task_queue, "expire_stale_pending", None)
        if callable(expire):
            with suppress(Exception):
                expire((task_type,), older_than_seconds=max(60.0, self.wait_seconds))
        return cast(
            "str | None",
            self.task_queue.enqueue_with_id(
                task_type,
                payload,
                daily_budget=max(0, int(daily_budget)),
            ),
        )

    def _daily_budget_for(self, source: str) -> int:
        if source == "search":
            return int(self.daily_search_budget)
        if source == "hot":
            return int(self.daily_hot_budget)
        if source == "feed":
            return int(self.daily_feed_budget)
        if source == "creator":
            return int(self.daily_creator_budget)
        if source == "related":
            return int(self.daily_related_budget)
        return 0

    async def _load_seed_values(self, loader: Any | None) -> list[str]:
        if loader is None:
            return []
        try:
            result = loader()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.debug("zhihu producer: seed loader failed", exc_info=True)
            return []
        if not isinstance(result, (list, tuple, set)):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for value in result:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def _mark_search_keywords(
        self,
        coordinator: Any,
        claimed: list[Any],
        items: list[dict[str, Any]],
    ) -> None:
        produced_keywords = {
            str(item.get("search_keyword", "")).strip()
            for item in items
            if isinstance(item, dict) and str(item.get("search_keyword", "")).strip()
        }
        contents = zhihu_discovery_items_to_contents(items)
        if contents:
            used = [
                item
                for item in claimed
                if not produced_keywords or str(item.keyword).strip() in produced_keywords
            ]
            failed = [item for item in claimed if item not in used]
            if used:
                coordinator.mark_used(used)
            if failed:
                coordinator.mark_failed(failed)
        else:
            coordinator.mark_failed(claimed)

    async def _wait_for_task(self, task_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + max(0.0, float(self.wait_seconds))
        while True:
            task = self.task_queue.get(task_id)
            status = str((task or {}).get("status", "")).strip()
            if status in {"completed", "failed"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                return {}
            await asyncio.sleep(max(0.01, float(self.poll_interval_seconds)))

        if not task or str(task.get("status", "")) != "completed":
            return {}
        try:
            parsed = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _kick_dispatcher(self) -> None:
        kick = self.kick or kick_zhihu_task_dispatcher
        try:
            result = kick()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("zhihu producer: task dispatcher kick failed", exc_info=True)

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug("zhihu producer: candidate pool fullness unavailable", exc_info=True)
            return False

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("zhihu producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def build_zhihu_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
    kick: Any | None = None,
) -> ZhihuDiscoveryProducer | None:
    """Build the runtime Zhihu producer if Zhihu discovery is enabled."""
    zh_cfg = getattr(getattr(config, "sources", None), "zhihu", None)
    if zh_cfg is None or not bool(getattr(zh_cfg, "enabled", False)):
        return None
    scheduler = getattr(config, "scheduler", None)
    if not bool(getattr(scheduler, "enabled", True)):
        return None
    if not hasattr(database, "conn"):
        logger.info("zhihu producer disabled: database does not expose sqlite connection")
        return None

    wait_seconds = float(getattr(zh_cfg, "wait_seconds", 0) or 180.0)
    return ZhihuDiscoveryProducer(
        task_queue=ZhihuTaskQueue(database),
        soul_engine=soul_engine,
        enabled=True,
        sources=_normalize_sources(getattr(zh_cfg, "source_modes", ZHIHU_SOURCE_ORDER)),
        min_interval_minutes=int(getattr(zh_cfg, "min_interval_minutes", 60)),
        daily_search_budget=int(getattr(zh_cfg, "daily_search_budget", 0)),
        daily_hot_budget=int(getattr(zh_cfg, "daily_hot_budget", 0)),
        daily_feed_budget=int(getattr(zh_cfg, "daily_feed_budget", 0)),
        daily_creator_budget=int(getattr(zh_cfg, "daily_creator_budget", 0)),
        daily_related_budget=int(getattr(zh_cfg, "daily_related_budget", 0)),
        wait_seconds=wait_seconds,
        poll_interval_seconds=max(0.1, float(getattr(zh_cfg, "request_interval_seconds", 3))),
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
        creator_seed_loader=lambda: recent_zhihu_creator_urls(database, limit=10),
        related_seed_loader=lambda: recent_zhihu_related_urls(database, limit=10),
        kick=kick,
    )


def kick_zhihu_task_dispatcher() -> None:
    """Best-effort wake-up for the extension dispatcher."""
    req = request.Request(
        "http://127.0.0.1:8420/api/sources/zhihu/kick",
        method="POST",
        data=b"",
    )
    with suppress(error.URLError, TimeoutError, OSError):
        request.urlopen(req, timeout=1.0).close()


def _fallback_profile_keywords(profile: Any, limit: int) -> list[str]:
    preferences = getattr(profile, "preferences", None)
    interests = list(getattr(preferences, "interests", []) or [])
    out: list[str] = []
    seen: set[str] = set()
    for interest in interests:
        name = str(getattr(interest, "name", "") or interest).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _same_run_seed_values(items: list[dict[str, Any]], *, key: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key, "") or "").strip()
        if not value or value in seen:
            continue
        if key == "author_url" and "zhihu.com/people/" not in value:
            continue
        if key == "url" and not _is_zhihu_content_url(value):
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_zhihu_content_url(value: str) -> bool:
    return "zhihu.com/question/" in value or "zhuanlan.zhihu.com/p/" in value


def _normalize_sources(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip() for part in value]
    else:
        raw = ["search"]
    seen: set[str] = set()
    out: list[str] = []
    for source in raw:
        if source not in ZHIHU_SOURCE_ORDER or source in seen:
            continue
        seen.add(source)
        out.append(source)
    if not out:
        out.append("search")
    return tuple(source for source in ZHIHU_SOURCE_ORDER if source in set(out))


def _ordered_strategies(sources: tuple[str, ...]) -> list[str]:
    return [
        ZHIHU_SOURCE_STRATEGIES[source] for source in sources if source in ZHIHU_SOURCE_STRATEGIES
    ]
