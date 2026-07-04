"""Runtime Reddit discovery producer."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error, request

from openbiliclaw.runtime.keyword_fetch import PLATFORM_REDDIT
from openbiliclaw.sources.reddit_tasks import (
    REDDIT_SOURCE_ORDER,
    REDDIT_SOURCE_STRATEGIES,
    CommandRunner,
    RedditTaskQueue,
    build_reddit_command,
    probe_reddit_command_backend,
    recent_reddit_related_urls,
    recent_reddit_subreddits,
    reddit_items_to_contents,
    run_reddit_command,
)

logger = logging.getLogger(__name__)


@dataclass
class RedditDiscoveryProducer:
    """Throttle and invoke Reddit command-backed discovery with plugin fallback.

    Reddit production is fetch-only: it hands raw candidates to the unified
    discovery candidate pool and lets the shared evaluator drain them later.
    That keeps real plugin E2E bounded by Reddit fetch latency instead of a
    local/cloud LLM scoring pass.
    """

    soul_engine: Any
    database: Any | None = None
    task_queue: Any | None = None
    backend: str = "opencli"
    enabled: bool = True
    sources: tuple[str, ...] = ("search",)
    min_interval_minutes: int = 60
    daily_search_budget: int = 300
    daily_hot_budget: int = 300
    daily_subreddit_budget: int = 300
    daily_related_budget: int = 300
    request_interval_seconds: int = 3
    candidate_pipeline: Any | None = None
    keyword_fetch: Any | None = None
    subreddit_seed_loader: Any | None = None
    related_seed_loader: Any | None = None
    kick: Any | None = None
    wait_seconds: float = 180.0
    poll_interval_seconds: float = 0.5
    max_seed_count: int = 5
    which: Any | None = None
    runner: CommandRunner | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        if _is_extension_backend(self.backend):
            return await self._produce_with_extension(limit=limit)

        status = probe_reddit_command_backend(
            self.backend,
            which=self.which if self.which is not None else None,
            runner=self.runner,
        )
        if status.state != "ready":
            if self.task_queue is not None:
                logger.info(
                    "reddit command backend unavailable; falling back to extension: "
                    "backend=%s state=%s message=%s",
                    self.backend,
                    status.state,
                    status.message,
                )
                return await self._produce_with_extension(limit=limit)
            return {"discovered": 0, "reason": status.state, "message": status.message}

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("reddit producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        backend = status.backend or ("rdt" if str(self.backend).lower() == "rdt" else "opencli")
        remaining = self.remaining_budgets(per_run_budget=requested_limit)
        source_modes = tuple(
            source
            for source in _normalize_sources(self.sources)
            if int(remaining.get(source, 0)) > 0
        )
        if not source_modes:
            return self._skip("budget_exhausted")
        all_items: list[dict[str, Any]] = []
        source_counts: Counter[str] = Counter()
        skipped_reasons: list[str] = []
        keyword_ids: dict[str, int] = {}
        claimed_keywords: list[Any] = []

        for source in source_modes:
            strategy = REDDIT_SOURCE_STRATEGIES[source]
            try:
                command_inputs = self._inputs_for_source(
                    source,
                    profile=profile,
                    requested_limit=requested_limit,
                    current_items=all_items,
                )
                if (
                    source == "search"
                    and self.keyword_fetch is not None
                    and bool(getattr(self.keyword_fetch, "should_claim", lambda: False)())
                ):
                    claimed_keywords = list(self.keyword_fetch.claim(PLATFORM_REDDIT))
                    claimed_inputs = [
                        str(item.keyword).strip()
                        for item in claimed_keywords
                        if str(item.keyword).strip()
                    ]
                    if claimed_inputs:
                        command_inputs = claimed_inputs
                        keyword_ids = {
                            str(item.keyword).strip(): int(item.id) for item in claimed_keywords
                        }
                if not command_inputs:
                    skipped_reasons.append(f"no_{source}_seeds")
                    continue

                source_remaining = max(0, int(remaining.get(source, requested_limit)))
                for command_input in command_inputs[:requested_limit]:
                    if source_remaining <= 0:
                        break
                    command_limit = max(1, min(requested_limit, source_remaining))
                    args = build_reddit_command(
                        backend,
                        mode=source,
                        query=command_input,
                        subreddit=command_input if source in {"hot", "subreddit"} else "",
                        limit=command_limit,
                    )
                    rows = run_reddit_command(
                        args,
                        runner=self.runner,
                        timeout=max(15.0, float(self.request_interval_seconds) * command_limit),
                    )
                    for row in rows:
                        row.setdefault("source_strategy", strategy)
                        if source == "search":
                            row.setdefault("search_keyword", command_input)
                    contents = reddit_items_to_contents(
                        rows,
                        strategy=strategy,
                        source_keyword_ids=keyword_ids,
                    )
                    units_used = max(0, min(command_limit, len(contents)))
                    self.record_strategy_run(
                        source,
                        units_used=units_used,
                        discovered=len(contents),
                        reason="ok",
                    )
                    source_remaining -= units_used
                    source_counts[strategy] += len(contents)
                    all_items.extend(rows)
            except Exception as exc:
                logger.warning("reddit producer strategy failed: source=%s error=%s", source, exc)
                skipped_reasons.append("error")
                if source == "search" and claimed_keywords and self.keyword_fetch is not None:
                    self.keyword_fetch.mark_failed(claimed_keywords)

        self._last_run_at = datetime.now(UTC)
        if not all_items:
            reason = skipped_reasons[0] if skipped_reasons else "empty"
            return {"discovered": 0, "reason": reason}

        contents = []
        for source in source_modes:
            strategy = REDDIT_SOURCE_STRATEGIES[source]
            rows = [row for row in all_items if row.get("source_strategy") == strategy]
            contents.extend(
                reddit_items_to_contents(rows, strategy=strategy, source_keyword_ids=keyword_ids)
            )
        if not contents:
            return {"discovered": 0, "reason": "empty"}
        if claimed_keywords and self.keyword_fetch is not None:
            self.keyword_fetch.mark_used(claimed_keywords)

        payload: dict[str, object] = {
            "discovered": len(contents),
            "source_counts": dict(source_counts),
            "reason": "ok",
            "backend": backend,
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
            payload["enqueued"] = enqueued
        return payload

    async def _produce_with_extension(self, *, limit: int | None = None) -> dict[str, object]:
        if self.task_queue is None:
            return {"discovered": 0, "reason": "missing", "message": "Reddit 插件任务队列不可用。"}

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("reddit producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        remaining = self.remaining_budgets(per_run_budget=requested_limit)
        source_modes = tuple(
            source
            for source in _normalize_sources(self.sources)
            if int(remaining.get(source, 0)) > 0
        )
        if not source_modes:
            return self._skip("budget_exhausted")

        all_items: list[dict[str, Any]] = []
        source_counts: Counter[str] = Counter()
        skipped_reasons: list[str] = []
        keyword_ids: dict[str, int] = {}
        claimed_keywords: list[Any] = []
        enqueued_task_count = 0

        for source in source_modes:
            strategy = REDDIT_SOURCE_STRATEGIES[source]
            try:
                inputs = await self._extension_inputs_for_source(
                    source,
                    profile=profile,
                    requested_limit=requested_limit,
                    current_items=all_items,
                )
                if (
                    source == "search"
                    and self.keyword_fetch is not None
                    and bool(getattr(self.keyword_fetch, "should_claim", lambda: False)())
                ):
                    claimed_keywords = list(self.keyword_fetch.claim(PLATFORM_REDDIT))
                    claimed_inputs = [
                        str(item.keyword).strip()
                        for item in claimed_keywords
                        if str(item.keyword).strip()
                    ]
                    if claimed_inputs:
                        inputs = claimed_inputs
                        keyword_ids = {
                            str(item.keyword).strip(): int(item.id) for item in claimed_keywords
                        }
                if not inputs:
                    skipped_reasons.append(f"no_{source}_seeds")
                    continue

                source_remaining = max(0, int(remaining.get(source, requested_limit)))
                if source_remaining <= 0:
                    continue
                payload = self._extension_payload_for_source(
                    source,
                    inputs=inputs[: max(1, int(self.max_seed_count))],
                    requested_limit=min(requested_limit, source_remaining),
                    keyword_ids=keyword_ids,
                )
                task_id = self._enqueue_extension_task(
                    source,
                    payload,
                    daily_budget=self._daily_budget_for(source),
                )
                if task_id is None:
                    skipped_reasons.append("budget_exhausted")
                    if source == "search" and claimed_keywords and self.keyword_fetch is not None:
                        self.keyword_fetch.mark_failed(claimed_keywords)
                    continue

                enqueued_task_count += 1
                await self._kick_dispatcher()
                result = await self._wait_for_task(task_id)
                task_status = str(result.get("_task_status", "completed"))
                if task_status != "completed":
                    skipped_reasons.append(str(result.get("error") or task_status or "failed"))
                    if source == "search" and claimed_keywords and self.keyword_fetch is not None:
                        self.keyword_fetch.mark_failed(claimed_keywords)
                    continue

                rows = [item for item in result.get("items", []) if isinstance(item, dict)]
                for row in rows:
                    row.setdefault("source_strategy", strategy)
                    if source == "search" and not row.get("search_keyword") and inputs:
                        row["search_keyword"] = str(inputs[0])
                    if source == "search" and keyword_ids:
                        keyword = str(row.get("search_keyword", "")).strip()
                        if keyword in keyword_ids:
                            row.setdefault("source_keyword_id", keyword_ids[keyword])
                contents = reddit_items_to_contents(
                    rows,
                    strategy=strategy,
                    source_keyword_ids=keyword_ids,
                )
                units_used = max(0, min(source_remaining, len(contents)))
                self.record_strategy_run(
                    source,
                    units_used=units_used,
                    discovered=len(contents),
                    reason="ok",
                )
                source_counts[strategy] += len(contents)
                all_items.extend(rows)
            except Exception as exc:
                logger.warning("reddit extension strategy failed: source=%s error=%s", source, exc)
                skipped_reasons.append("error")
                if source == "search" and claimed_keywords and self.keyword_fetch is not None:
                    self.keyword_fetch.mark_failed(claimed_keywords)

        self._last_run_at = datetime.now(UTC)
        if enqueued_task_count == 0:
            return self._skip(skipped_reasons[0] if skipped_reasons else "no_sources")
        if not all_items:
            return {"discovered": 0, "reason": skipped_reasons[0] if skipped_reasons else "empty"}

        contents = []
        for source in source_modes:
            strategy = REDDIT_SOURCE_STRATEGIES[source]
            rows = [row for row in all_items if row.get("source_strategy") == strategy]
            contents.extend(
                reddit_items_to_contents(rows, strategy=strategy, source_keyword_ids=keyword_ids)
            )
        if not contents:
            return {"discovered": 0, "reason": "empty"}
        if claimed_keywords and self.keyword_fetch is not None:
            self.keyword_fetch.mark_used(claimed_keywords)

        payload_out: dict[str, object] = {
            "discovered": len(contents),
            "source_counts": dict(source_counts),
            "reason": "ok",
            "backend": "extension",
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
            payload_out["enqueued"] = enqueued
        return payload_out

    def _inputs_for_source(
        self,
        source: str,
        *,
        profile: Any,
        requested_limit: int,
        current_items: list[dict[str, Any]],
    ) -> list[str]:
        if source == "search":
            return _fallback_profile_keywords(profile, requested_limit)
        if source == "hot":
            return ["all"]
        if source == "subreddit":
            return _same_run_subreddits(current_items) or _fallback_profile_keywords(profile, 3)
        if source == "related":
            return _same_run_urls(current_items)
        return []

    async def _extension_inputs_for_source(
        self,
        source: str,
        *,
        profile: Any,
        requested_limit: int,
        current_items: list[dict[str, Any]],
    ) -> list[str]:
        if source == "subreddit":
            return (
                await self._load_seed_values(self.subreddit_seed_loader)
                or _same_run_subreddits(current_items)
                or _fallback_profile_keywords(profile, min(3, requested_limit))
            )
        if source == "related":
            return await self._load_seed_values(self.related_seed_loader) or _same_run_urls(
                current_items
            )
        return self._inputs_for_source(
            source,
            profile=profile,
            requested_limit=requested_limit,
            current_items=current_items,
        )

    async def _load_seed_values(self, loader: Any | None) -> list[str]:
        if loader is None:
            return []
        try:
            result = loader()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.debug("reddit producer: seed loader failed", exc_info=True)
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

    def _extension_payload_for_source(
        self,
        source: str,
        *,
        inputs: list[str],
        requested_limit: int,
        keyword_ids: dict[str, int],
    ) -> dict[str, object]:
        max_items = max(1, int(requested_limit))
        if source == "search":
            payload: dict[str, object] = {
                "keywords": inputs,
                "max_items_per_keyword": max_items,
            }
            if keyword_ids:
                payload["source_keyword_ids"] = keyword_ids
            return payload
        if source == "hot":
            return {"subreddit": inputs[0] if inputs else "all", "max_items": max_items}
        if source == "subreddit":
            return {"subreddits": inputs, "max_items_per_subreddit": max_items}
        if source == "related":
            return {"related_urls": inputs, "max_items_per_seed": max_items}
        return {}

    def _enqueue_extension_task(
        self,
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget: int,
    ) -> str | None:
        if self.task_queue is None:
            return None
        expire = getattr(self.task_queue, "expire_stale_pending", None)
        if callable(expire):
            with suppress(Exception):
                expire((task_type,), older_than_seconds=max(60.0, float(self.wait_seconds)))
        enqueue = getattr(self.task_queue, "enqueue_with_id", None)
        if not callable(enqueue):
            return None
        task_id = enqueue(task_type, payload, daily_budget=max(0, int(daily_budget)))
        return str(task_id) if task_id is not None else None

    async def _wait_for_task(self, task_id: str) -> dict[str, Any]:
        if self.task_queue is None:
            return {"_task_status": "missing"}
        get = getattr(self.task_queue, "get", None)
        if not callable(get):
            return {"_task_status": "missing"}
        deadline = asyncio.get_running_loop().time() + max(0.0, float(self.wait_seconds))
        task: dict[str, Any] | None = None
        while True:
            task = get(task_id)
            status = str((task or {}).get("status", "")).strip()
            if status in {"completed", "failed"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                fail = getattr(self.task_queue, "fail", None)
                if callable(fail):
                    with suppress(Exception):
                        fail(task_id, error="extension_result_timeout")
                return {"_task_status": "timeout", "error": "extension_result_timeout"}
            await asyncio.sleep(max(0.01, float(self.poll_interval_seconds)))

        if not task:
            return {"_task_status": "missing"}
        if str(task.get("status", "")) != "completed":
            try:
                parsed_error = json.loads(str(task.get("result_json") or "{}"))
            except json.JSONDecodeError:
                parsed_error = {}
            error_value = (
                str(parsed_error.get("error", "failed"))
                if isinstance(parsed_error, dict)
                else "failed"
            )
            return {"_task_status": "failed", "error": error_value}
        try:
            parsed = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            return {"_task_status": "failed", "error": "invalid_result_json"}
        if not isinstance(parsed, dict):
            return {"_task_status": "failed", "error": "invalid_result_json"}
        parsed["_task_status"] = "completed"
        return parsed

    async def _kick_dispatcher(self) -> None:
        kick = self.kick or kick_reddit_task_dispatcher
        try:
            result = kick()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("reddit producer: task dispatcher kick failed", exc_info=True)

    def remaining_budgets(self, *, per_run_budget: int | None = None) -> dict[str, int]:
        """Return remaining daily execution units by Reddit source branch."""
        run_budget = max(1, int(per_run_budget or 10))
        configured = {
            "search": int(self.daily_search_budget),
            "hot": int(self.daily_hot_budget),
            "subreddit": int(self.daily_subreddit_budget),
            "related": int(self.daily_related_budget),
        }
        remaining: dict[str, int] = {}
        for source, budget in configured.items():
            if budget == 0:
                remaining[source] = run_budget
            elif budget < 0:
                remaining[source] = 0
            else:
                remaining[source] = max(0, budget - self.consumed_today(source))
        return remaining

    def _daily_budget_for(self, source: str) -> int:
        if source == "search":
            return int(self.daily_search_budget)
        if source == "hot":
            return int(self.daily_hot_budget)
        if source == "subreddit":
            return int(self.daily_subreddit_budget)
        if source == "related":
            return int(self.daily_related_budget)
        return 0

    def consumed_today(self, source: str) -> int:
        """Return today's successful execution units for one Reddit branch."""
        if self.database is None or not hasattr(self.database, "conn"):
            return 0
        self._ensure_ledger_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM reddit_discovery_runs
            WHERE source = ? AND created_at >= ? AND reason = 'ok'
            """,
            (source, today),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def record_strategy_run(
        self,
        source: str,
        *,
        units_used: int,
        discovered: int,
        reason: str,
    ) -> None:
        """Record one Reddit source-branch execution in the daily budget ledger."""
        if self.database is None or not hasattr(self.database, "conn"):
            return
        self._ensure_ledger_table()
        self.database.conn.execute(
            """
            INSERT INTO reddit_discovery_runs(source, units, discovered, reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                source,
                max(0, int(units_used)),
                max(0, int(discovered)),
                reason,
            ),
        )
        self.database.conn.commit()

    def _ensure_ledger_table(self) -> None:
        if self.database is None or not hasattr(self.database, "conn"):
            return
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reddit_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_reddit_discovery_runs_source_created
                ON reddit_discovery_runs(source, created_at);
            """
        )
        self.database.conn.commit()

    def _is_due(self) -> bool:
        if self._last_run_at is None:
            return True
        min_interval = max(0, int(self.min_interval_minutes))
        if min_interval <= 0:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=min_interval)

    def _candidate_pool_full(self) -> bool:
        pipeline = self.candidate_pipeline
        if pipeline is None:
            return False
        checker = getattr(pipeline, "is_candidate_pool_full", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            logger.debug("reddit producer: candidate pool fullness unavailable", exc_info=True)
            return False

    def _skip(self, reason: str) -> dict[str, object]:
        self._last_skip_reason = reason
        logger.info("reddit producer skip: reason=%s", reason)
        return {"discovered": 0, "reason": reason}


def build_reddit_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
) -> RedditDiscoveryProducer | None:
    """Build the runtime Reddit producer if Reddit discovery is enabled."""

    rd_cfg = getattr(getattr(config, "sources", None), "reddit", None)
    if rd_cfg is None or not bool(getattr(rd_cfg, "enabled", False)):
        return None
    scheduler = getattr(config, "scheduler", None)
    if not bool(getattr(scheduler, "enabled", True)):
        return None
    backend = str(getattr(rd_cfg, "backend", "rdt") or "rdt")
    task_queue = RedditTaskQueue(database) if hasattr(database, "conn") else None
    return RedditDiscoveryProducer(
        soul_engine=soul_engine,
        database=database,
        enabled=True,
        task_queue=task_queue,
        backend=backend,
        sources=_normalize_sources(getattr(rd_cfg, "source_modes", REDDIT_SOURCE_ORDER)),
        min_interval_minutes=int(getattr(rd_cfg, "min_interval_minutes", 60)),
        daily_search_budget=int(getattr(rd_cfg, "daily_search_budget", 300)),
        daily_hot_budget=int(getattr(rd_cfg, "daily_hot_budget", 300)),
        daily_subreddit_budget=int(getattr(rd_cfg, "daily_subreddit_budget", 300)),
        daily_related_budget=int(getattr(rd_cfg, "daily_related_budget", 300)),
        request_interval_seconds=int(getattr(rd_cfg, "request_interval_seconds", 3)),
        wait_seconds=float(getattr(rd_cfg, "wait_seconds", 0) or 180.0),
        poll_interval_seconds=max(0.1, float(getattr(rd_cfg, "request_interval_seconds", 3))),
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
        subreddit_seed_loader=lambda: recent_reddit_subreddits(database, limit=10),
        related_seed_loader=lambda: recent_reddit_related_urls(database, limit=10),
    )


def kick_reddit_task_dispatcher() -> None:
    """Best-effort wake-up for the OpenBiliClaw Reddit extension dispatcher."""
    req = request.Request(
        "http://127.0.0.1:8420/api/sources/reddit/kick",
        method="POST",
        data=b"",
    )
    with suppress(error.URLError, TimeoutError, OSError):
        request.urlopen(req, timeout=1.0).close()


def _is_extension_backend(backend: Any) -> bool:
    return str(backend or "").strip().lower() in {"extension", "openbiliclaw", "plugin"}


def _normalize_sources(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip() for part in value]
    else:
        raw = ["search"]
    selected = {source for source in raw if source in REDDIT_SOURCE_ORDER}
    if not selected:
        selected.add("search")
    return tuple(source for source in REDDIT_SOURCE_ORDER if source in selected)


def _ordered_strategies(sources: tuple[str, ...]) -> list[str]:
    return [
        REDDIT_SOURCE_STRATEGIES[source] for source in sources if source in REDDIT_SOURCE_STRATEGIES
    ]


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


def _same_run_subreddits(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item.get("subreddit") or item.get("subreddit_name_prefixed") or "").strip()
        value = value.removeprefix("r/")
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _same_run_urls(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item.get("url") or item.get("permalink") or "").strip()
        if value.startswith("/"):
            value = f"https://www.reddit.com{value}"
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
