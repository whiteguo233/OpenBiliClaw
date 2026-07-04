"""Runtime Bilibili extension-search fallback producer."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.strategies._utils import (
    build_profile_summary,
    search_cooldown_remaining,
)
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.prompts import build_search_queries_prompt
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.runtime.keyword_fetch import PLATFORM_BILIBILI as _PLATFORM_BILIBILI

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.bili_tasks import BiliTaskQueue

logger = logging.getLogger(__name__)

BiliKickCallable = Callable[[], Awaitable[None] | None]


async def generate_bili_search_keywords(
    llm_service: LLMService,
    profile: SoulProfile,
    *,
    count: int = 5,
) -> list[str]:
    """Generate Bilibili search queries for extension fallback tasks."""

    try:
        messages = build_search_queries_prompt(profile_summary=build_profile_summary(profile))
        complete_structured = llm_service.complete_structured_task
        response = await complete_structured(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            caller="runtime.bilibili_extension_search.queries",
            max_tokens=512,
            **without_core_memory_kwargs(complete_structured),
        )
        queries = _parse_queries(str(getattr(response, "content", "")), limit=count)
        if queries:
            return queries
    except Exception:
        logger.exception("bili extension search keyword generation failed")
    return _fallback_queries(profile, count)


@dataclass
class BilibiliExtensionSearchProducer:
    """Enqueue Bilibili search tasks when API search is degraded."""

    task_queue: BiliTaskQueue
    soul_engine: Any
    llm_service: LLMService
    bilibili_client: Any
    presence: Any
    enabled: bool = True
    daily_budget: int = 0
    min_interval_minutes: int = 30
    keywords_per_cycle: int = 3
    page_size: int = 20
    presence_grace_seconds: int = 90
    candidate_pipeline: Any | None = None
    keyword_fetch: Any | None = None
    kick: BiliKickCallable | None = None
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, object]:
        """Run one fallback cycle if Bilibili API search needs DOM help."""

        if not self.enabled:
            return self._skip("disabled")
        if not self._api_search_fallback_needed():
            return self._skip("search_not_cooling")
        if not self._extension_present():
            return self._skip("extension_absent")
        if self._candidate_pool_full():
            return self._skip("pool_full")
        if not self._is_due():
            return self._skip("throttled")

        keyword_count = min(
            self.keywords_per_cycle,
            max(1, int(limit or self.keywords_per_cycle)),
        )

        coordinator = self.keyword_fetch
        if (
            keywords is None
            and coordinator is not None
            and bool(getattr(coordinator, "should_claim", lambda: False)())
        ):
            claimed = coordinator.claim(_PLATFORM_BILIBILI, keyword_count)
            if not claimed:
                return self._skip("no_keywords")
            result = self._enqueue_claimed_keywords(claimed)
            await self._kick_if_needed(result)
            return result

        if keywords is not None:
            resolved_keywords = _dedupe_keywords(keywords)[:keyword_count]
            if not resolved_keywords:
                return self._skip("no_keywords")
            result = self._enqueue_keywords(resolved_keywords)
            await self._kick_if_needed(result)
            return result

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.warning("bili extension producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        resolved_keywords = await generate_bili_search_keywords(
            self.llm_service,
            profile,
            count=keyword_count,
        )
        if not resolved_keywords:
            return self._skip("no_keywords")
        result = self._enqueue_keywords(resolved_keywords[:keyword_count])
        await self._kick_if_needed(result)
        return result

    def _enqueue_keywords(self, keywords: list[str]) -> dict[str, object]:
        enqueued = 0
        for keyword in keywords:
            task_id = self.task_queue.enqueue_with_id(
                "search",
                self._task_payload(keyword),
                daily_budget=self.daily_budget,
            )
            if task_id is None:
                break
            enqueued += 1
        logger.info("bili extension producer enqueued %d/%d search tasks", enqueued, len(keywords))
        return {"enqueued": enqueued, "attempted": len(keywords), "reason": "ok"}

    def _enqueue_claimed_keywords(self, claimed: list[Any]) -> dict[str, object]:
        coordinator = self.keyword_fetch
        enqueued = 0
        for item in claimed:
            task_id = self.task_queue.enqueue_with_id(
                "search",
                self._task_payload(item.keyword, source_keyword_id=int(item.id)),
                daily_budget=self.daily_budget,
            )
            if task_id is not None:
                enqueued += 1
                if coordinator is not None:
                    coordinator.mark_executing(item)
                continue
            if coordinator is not None:
                coordinator.rollback(item)
            break
        if coordinator is not None and enqueued < len(claimed):
            for item in claimed[enqueued + 1 :]:
                coordinator.rollback(item)
        logger.info(
            "bili extension producer enqueued %d/%d claimed search tasks",
            enqueued,
            len(claimed),
        )
        return {"enqueued": enqueued, "attempted": len(claimed), "reason": "ok"}

    def _task_payload(self, query: str, *, source_keyword_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "limit": self.page_size,
            "page": 1,
            "page_size": self.page_size,
            "source": "bili-extension-search",
        }
        if source_keyword_id is not None:
            payload["source_keyword_id"] = int(source_keyword_id)
        return payload

    async def _kick_if_needed(self, result: dict[str, object]) -> None:
        enqueued_raw = result.get("enqueued", 0)
        try:
            enqueued = int(enqueued_raw) if isinstance(enqueued_raw, int | float | str) else 0
        except (TypeError, ValueError):
            enqueued = 0
        if enqueued <= 0 or self.kick is None:
            return
        try:
            maybe_awaitable = self.kick()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.debug("bili extension producer kick failed", exc_info=True)

    def _extension_present(self) -> bool:
        is_present = getattr(self.presence, "is_present", None)
        if not callable(is_present):
            return False
        try:
            return bool(is_present(max(1, int(self.presence_grace_seconds))))
        except Exception:
            logger.debug("bili extension producer: presence unavailable", exc_info=True)
            return False

    def _api_search_fallback_needed(self) -> bool:
        if search_cooldown_remaining(self.bilibili_client) > 0:
            return True
        remaining = getattr(self.bilibili_client, "search_dom_fallback_remaining", None)
        if not callable(remaining):
            return False
        try:
            return float(remaining()) > 0
        except Exception:
            logger.debug("bili extension producer: DOM fallback state unavailable", exc_info=True)
            return False

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug(
                "bili extension producer: candidate pool fullness unavailable",
                exc_info=True,
            )
            return False

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        recent = self.task_queue.find_recent_task(
            "search",
            recent_hours=max(0.0, float(self.min_interval_minutes) / 60.0),
            statuses=("pending", "in_progress", "completed"),
        )
        return recent is None

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("bili extension producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"enqueued": 0, "attempted": 0, "reason": reason}


def _parse_queries(content: str, *, limit: int) -> list[str]:
    text = content.strip()
    if not text:
        return []
    payload = parse_llm_json_tolerant(text)
    if payload is None:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("queries")
    if not isinstance(raw, list):
        return []
    return _dedupe_keywords([str(item) for item in raw])[: max(1, int(limit))]


def _fallback_queries(profile: Any, count: int) -> list[str]:
    preferences = getattr(profile, "preferences", None)
    interests = getattr(preferences, "interests", []) if preferences is not None else []
    out: list[str] = []
    for item in interests:
        name = str(getattr(item, "name", "") or "").strip()
        if name:
            out.append(name)
    return _dedupe_keywords(out)[: max(1, int(count))]


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
