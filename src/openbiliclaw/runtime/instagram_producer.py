"""Queue-backed Instagram discovery producer.

All Instagram network traffic stays in the browser extension.  The producer
owns only bounded work allocation, keyword lifecycle, task waiting and the
handoff of sanitized rows to the shared candidate pool.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from openbiliclaw.runtime.keyword_fetch import PLATFORM_INSTAGRAM
from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.runtime.producer_cadence import (
    ledger_available,
    producer_ran_within,
    record_producer_run,
)
from openbiliclaw.sources.instagram import INSTAGRAM_SOURCE_STRATEGIES, instagram_item_to_content
from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue, is_instagram_username

logger = logging.getLogger(__name__)

INSTAGRAM_SOURCE_ORDER = ("topic", "creator")
# These broad public routes were observed with media on 2026-09-26. They are
# seeds, not search results or availability guarantees; each task still needs
# affirmative upstream evidence and the shared evaluator retains relevance.
_TOPIC_ALIASES = {
    "animation": ("动漫", "动画", "二次元", "anime", "manga", "animation"),
    "music": ("音乐", "乐器", "吉他", "钢琴", "作曲", "music", "guitar", "drums"),
    "technology": ("科技", "人工智能", "机器学习", "机器人", "编程", "technology", "ai"),
    "gaming": ("游戏", "电竞", "gaming", "games", "game"),
    "art": ("艺术", "绘画", "摄影", "设计", "建筑", "art", "photography", "design"),
}
_DISCOVER_BOUNDED_PARTIAL_CODES = frozenset(
    {"item_cap_reached", "page_cap_reached", "bounded_public_snapshot"}
)


@dataclass(frozen=True)
class _RecoveredKeywordClaim:
    id: int
    keyword: str


@dataclass
class InstagramDiscoveryProducer:
    """Feed public Instagram topic/creator media into the candidate pool."""

    database: Any
    task_queue: Any
    soul_engine: Any
    enabled: bool = False
    source_modes: tuple[str, ...] = INSTAGRAM_SOURCE_ORDER
    daily_topic_budget: int = 60
    daily_creator_budget: int = 30
    min_interval_minutes: int = 10
    # The extension's absolute task deadline is 12 minutes. Wait beyond that
    # boundary so a normal bounded run is consumed by the producer that
    # created it instead of becoming an orphaned late result.
    wait_seconds: float = 780.0
    # One refresh tick must never hold the coordinator lock for the sum of all
    # topic/creator task waits. Tasks finishing after this absolute boundary
    # remain producer-owned and are adopted by the next tick.
    cycle_wait_seconds: float = 780.0
    poll_interval_seconds: float = 0.5
    request_interval_ms: int = 3000
    max_seed_count: int = 5
    candidate_pipeline: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    keyword_fetch: Any | None = None
    kick: Any | None = None
    # Daemon composition injects shared extension presence so background
    # discovery does not strand tasks while no browser can claim them.  CLI
    # construction leaves this unset for an explicit operator-triggered run.
    presence: Any | None = None
    presence_grace_seconds: int = 90
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_attempt_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)
    _pending_consumption_task_ids: set[str] = field(default_factory=set, init=False)
    _finalized_keyword_ids: set[int] = field(default_factory=set, init=False)
    _topic_claim_outcomes: dict[int, str] = field(default_factory=dict, init=False)
    _cycle_deadline: float | None = field(default=None, init=False)
    _owner_token: str = field(default_factory=lambda: uuid.uuid4().hex, init=False)
    _run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _register_task: Callable[[str], None] | None = field(default=None, init=False, repr=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
        register_task: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Serialize this instance and run one bounded discovery cycle."""

        if self._run_lock.locked():
            return self._skip("already_running")
        async with self._run_lock:
            self._register_task = register_task
            try:
                return await self._produce_if_due(limit=limit, force=force)
            finally:
                self._register_task = None
                self._release_owner_leases()

    async def _produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Run one bounded browser-task discovery cycle."""

        # Consumption candidates belong to one handoff attempt only.  A
        # failed prior cycle deliberately leaves owner-ledger rows unconsumed;
        # carrying its in-memory ids into a smaller/reordered recovery cycle
        # could acknowledge rows that this cycle never scanned or handed off.
        self._pending_consumption_task_ids.clear()
        self._finalized_keyword_ids.clear()
        self._topic_claim_outcomes.clear()
        if not self.enabled:
            return self._skip("disabled")
        self._cycle_deadline = asyncio.get_running_loop().time() + max(
            0.0, float(self.cycle_wait_seconds)
        )
        extension_present = self._extension_present()
        if not extension_present and not hasattr(self.database, "conn"):
            return self._skip("extension_absent")
        self._ensure_budget_table()
        if self._candidate_pool_full():
            return self._skip("pool_full")
        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("instagram producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 20))
        modes = _normalize_modes(self.source_modes)
        if not modes:
            return self._skip("mode_disabled")
        (
            all_contents,
            recovered_modes,
            mode_results,
            recovered_claims,
            recovery_blocked,
        ) = await self._recover_owned_tasks(
            limit=requested_limit,
            modes=modes,
            extension_present=extension_present,
        )
        if not recovered_modes and not recovery_blocked and not force and not self._is_due():
            return self._skip("throttled")
        if not recovered_modes and not recovery_blocked and not extension_present:
            return self._skip("extension_absent")

        topic_claims: list[Any] = []
        fresh_modes = _fresh_modes_after_recovery(modes, recovered_modes)
        # Persist cadence before any new browser task is enqueued. Recovery is
        # deliberately excluded: consuming a late result must not manufacture
        # a new discovery attempt.  A recovered lane may only resume the
        # ordered suffix of its original cycle: recovering a late creator
        # result must not start a new topic lane and ping-pong around cadence.
        can_create_tasks = not recovery_blocked and extension_present
        if recovered_modes and not extension_present:
            for mode in modes:
                if mode not in recovered_modes:
                    mode_results.setdefault(mode, "extension_absent")
        if (
            can_create_tasks
            and not recovered_modes
            and bool(fresh_modes)
            and not self._claim_attempt(force=force)
        ):
            return self._skip("throttled")

        if "topic" in fresh_modes and can_create_tasks:
            # Reserve room for creator provenance when both lanes are enabled;
            # otherwise a full topic page would always win the final slice and
            # creator tasks would consume upstream work without retaining a
            # single candidate.
            topic_target = (
                requested_limit if "creator" not in modes else max(1, (requested_limit + 1) // 2)
            )
            topic_limit = self._bounded_by_remaining_budget("topic", topic_target)
            if topic_limit <= 0:
                mode_results["topic"] = "budget_exhausted"
            else:
                topic_rows, topic_claims, topic_reason = await self._run_topics(
                    profile,
                    topic_limit,
                )
                all_contents.extend(topic_rows)
                mode_results["topic"] = topic_reason
                if self._cycle_time_exhausted() or topic_reason not in {
                    "ok",
                    "empty",
                    "partial",
                }:
                    can_create_tasks = False

        if "creator" in fresh_modes and can_create_tasks:
            creator_capacity = max(
                0,
                requested_limit - len(_dedupe_contents(all_contents)),
            )
            creator_limit = (
                self._bounded_by_remaining_budget("creator", creator_capacity)
                if creator_capacity > 0
                else 0
            )
            if creator_limit <= 0:
                mode_results["creator"] = (
                    "limit_reached" if creator_capacity <= 0 else "budget_exhausted"
                )
            else:
                creator_rows, creator_reason = await self._run_creators(
                    all_contents,
                    creator_limit,
                )
                all_contents.extend(creator_rows)
                mode_results["creator"] = creator_reason

        contents = _dedupe_contents(all_contents)[:requested_limit]
        (
            inserted,
            retained_keyword_ids,
            retained_counts,
            handoff_failed,
        ) = self._enqueue_contents(contents)
        self._finish_topic_claims(topic_claims, retained_keyword_ids, mode_results.get("topic", ""))
        for claim, outcome in recovered_claims:
            self._finish_topic_claims([claim], retained_keyword_ids, outcome)
        # A transient candidate-pipeline failure must leave the canonical
        # producer-owned task adoptable. The next run safely replays every row;
        # already accepted candidates dedupe while failed rows get another
        # durable handoff attempt.
        if not handoff_failed:
            self._commit_task_consumptions()
        self._finalized_keyword_ids.clear()
        self._stamp_run(inserted)
        if not contents:
            return {
                "discovered": 0,
                "enqueued": 0,
                "mode_results": mode_results,
                "reason": (
                    "recovery_blocked"
                    if recovery_blocked
                    else next(iter(mode_results.values()), "empty")
                ),
            }

        payload: dict[str, object] = {
            "discovered": len(contents),
            "enqueued": inserted,
            "mode_results": mode_results,
            "source_counts": {
                strategy: sum(1 for item in contents if item.source_strategy == strategy)
                for strategy in INSTAGRAM_SOURCE_STRATEGIES.values()
            },
            "reason": "ok" if inserted > 0 else "no_progress",
        }
        if (
            inserted > 0
            and self.candidate_pipeline is not None
            and not self.candidate_evaluation_owned_by_coordinator
        ):
            payload.update(
                await self.candidate_pipeline.drain_pending(
                    profile=profile,
                    batch_size=requested_limit,
                )
            )
        return payload

    async def _run_topics(
        self,
        profile: Any,
        limit: int,
    ) -> tuple[list[Any], list[Any], str]:
        coordinator = self.keyword_fetch
        claims: list[Any] = []
        if coordinator is not None and bool(getattr(coordinator, "should_claim", lambda: False)()):
            claims = list(coordinator.claim(PLATFORM_INSTAGRAM, n=min(5, limit)))
        queries: list[tuple[str, int | None]] = [
            (_topic_seed(str(item.keyword)), int(item.id)) for item in claims
        ]
        # Instagram topic discovery is not a general site-search lane.  The
        # unified planner may therefore have no Instagram-specific pending
        # claims; keep the formal source productive by falling back to bounded
        # profile interests without manufacturing a planner claim/id.
        if not queries:
            queries = [(keyword, None) for keyword in _profile_keywords(profile, min(5, limit))]
        queries = [(keyword, keyword_id) for keyword, keyword_id in queries if keyword]
        if not queries:
            return [], claims, "no_topics"

        contents: list[Any] = []
        degraded = False
        for keyword, keyword_id in queries:
            if self._cycle_time_exhausted():
                return contents, claims, "cycle_deadline"
            payload: dict[str, object] = {
                "mode": "topic",
                "topic": keyword,
                "keyword": keyword,
                "max_items": max(1, min(30, limit)),
                "request_interval_ms": self.request_interval_ms,
            }
            if keyword_id is not None:
                payload["source_keyword_id"] = keyword_id
            task_id = self._enqueue_owned_discover(
                payload,
                mode="topic",
                source_keyword_id=keyword_id,
            )
            if task_id is None:
                return contents, claims, "budget_exhausted"
            if keyword_id is not None and coordinator is not None:
                claimed = next(
                    (item for item in claims if int(item.id) == keyword_id),
                    None,
                )
                if claimed is not None:
                    coordinator.mark_executing(claimed)
            await self._kick_dispatcher()
            result = await self._wait_for_task(str(task_id))
            status = str(result.get("status", "") or "")
            failure = _discover_failure(result)
            if status not in {"ok", "empty", "partial"} and failure != "public_page_unavailable":
                return contents, claims, failure
            for row in result.get("items", []) if status in {"ok", "partial"} else []:
                content = instagram_item_to_content(
                    row,
                    strategy=INSTAGRAM_SOURCE_STRATEGIES["topic"],
                    source_keyword_id=keyword_id,
                )
                if content is not None:
                    contents.append(content)
                    if len(contents) >= limit:
                        break
            if failure == "public_page_unavailable":
                degraded = True
                if keyword_id is not None:
                    self._topic_claim_outcomes[keyword_id] = failure
            elif failure:
                # Keep already accepted rows, but never issue another task
                # after a challenge, login wall, rate limit or schema failure.
                return contents, claims, failure
            elif status == "empty" and keyword_id is not None:
                self._topic_claim_outcomes[keyword_id] = "empty"
            degraded = degraded or status == "partial"
            if len(contents) >= limit:
                break
        if degraded:
            return contents, claims, "partial" if contents else "topics_unavailable"
        return contents, claims, "ok" if contents else "empty"

    async def _run_creators(
        self,
        seed_contents: list[Any],
        limit: int,
    ) -> tuple[list[Any], str]:
        usernames = _creator_seed_usernames(seed_contents)[: max(1, int(self.max_seed_count))]
        if not usernames:
            return [], "no_creator_seeds"
        contents: list[Any] = []
        degraded = False
        for username in usernames:
            if self._cycle_time_exhausted():
                return contents, "cycle_deadline"
            task_id = self._enqueue_owned_discover(
                {
                    "mode": "creator",
                    "username": username,
                    "max_items": max(1, min(30, limit)),
                    "request_interval_ms": self.request_interval_ms,
                },
                mode="creator",
                source_keyword_id=None,
            )
            if task_id is None:
                return contents, "budget_exhausted"
            await self._kick_dispatcher()
            result = await self._wait_for_task(str(task_id))
            status = str(result.get("status", "") or "")
            failure = _discover_failure(result)
            if status not in {"ok", "empty", "partial"} and failure != "public_page_unavailable":
                return contents, failure
            for row in result.get("items", []) if status in {"ok", "partial"} else []:
                content = instagram_item_to_content(
                    row,
                    strategy=INSTAGRAM_SOURCE_STRATEGIES["creator"],
                )
                if content is not None:
                    contents.append(content)
                    if len(contents) >= limit:
                        break
            if failure == "public_page_unavailable":
                degraded = True
            elif failure:
                return contents, failure
            degraded = degraded or status == "partial"
            if len(contents) >= limit:
                break
        if degraded:
            return contents, "partial" if contents else "creators_unavailable"
        return contents, "ok" if contents else "empty"

    async def _wait_for_task(self, task_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + max(0.0, float(self.wait_seconds))
        if self._cycle_deadline is not None:
            deadline = min(deadline, self._cycle_deadline)
        while True:
            if not self._still_owns_task(task_id):
                return {"status": "owner_reassigned", "items": []}
            task = self.task_queue.get(task_id)
            state = str((task or {}).get("status", "") or "")
            if state in {"completed", "failed", "cancelled"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                return {"status": "timeout", "items": []}
            await asyncio.sleep(max(0.01, float(self.poll_interval_seconds)))
        if not self._still_owns_task(task_id):
            return {"status": "owner_reassigned", "items": []}
        if not task or state == "cancelled" or (state == "failed" and not task.get("result_json")):
            if state in {"failed", "cancelled"}:
                self._pending_consumption_task_ids.add(task_id)
            return {
                "status": str((task or {}).get("error", "") or state or "task_failed"),
                "items": [],
            }
        # Failed terminal tasks also have a staged canonical result. Their
        # machine error code lives there, not in a queue-level error column.
        # Dropping it would turn a page-local soft 404 into a cycle-wide failure.
        try:
            parsed = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            self._pending_consumption_task_ids.add(task_id)
            return {"status": "invalid_result", "items": []}
        self._pending_consumption_task_ids.add(task_id)
        return parsed if isinstance(parsed, dict) else {"status": "invalid_result", "items": []}

    def _cycle_time_exhausted(self) -> bool:
        return self._cycle_deadline is not None and (
            asyncio.get_running_loop().time() >= self._cycle_deadline
        )

    async def _recover_owned_tasks(
        self,
        *,
        limit: int,
        modes: tuple[str, ...],
        extension_present: bool,
    ) -> tuple[
        list[Any],
        set[str],
        dict[str, str],
        list[tuple[_RecoveredKeywordClaim, str]],
        bool,
    ]:
        """Adopt producer-owned work left by a prior process.

        Ownership is recorded separately from the shared browser queue, so a
        bootstrap task or a discover task created by another caller can never
        be consumed here. At most one unfinished task can exist in the normal
        sequential producer flow; it is always awaited before fresh work is
        created, preventing restart-driven queue growth.
        """

        queue_table = self.database.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instagram_tasks' LIMIT 1"
        ).fetchone()
        if queue_table is None:
            return [], set(), {}, [], False
        rows = self.database.conn.execute(
            "SELECT owned.task_id, owned.mode, owned.source_keyword_id, "
            "owned.keyword, tasks.status "
            "FROM instagram_discovery_owned_task AS owned "
            "JOIN instagram_tasks AS tasks ON tasks.id=owned.task_id "
            "WHERE owned.consumed_at IS NULL AND tasks.type='discover' "
            "ORDER BY owned.created_at ASC, owned.rowid ASC"
        ).fetchall()
        contents: list[Any] = []
        recovered_modes: set[str] = set()
        mode_results: dict[str, str] = {}
        recovered_claims: list[tuple[_RecoveredKeywordClaim, str]] = []
        blocked = False
        for row in rows:
            task_id = str(row["task_id"])
            mode = str(row["mode"] or "")
            state = str(row["status"] or "")
            if not self._claim_owned_task_for_recovery(task_id):
                mode_results[mode] = "owned_task_active"
                blocked = True
                break
            if self._register_task is not None:
                self._register_task(task_id)
            if state not in {"completed", "failed", "cancelled"}:
                if not extension_present:
                    mode_results[mode] = "extension_absent"
                    blocked = True
                    break
                await self._kick_dispatcher()
            result = await self._wait_for_task(task_id)
            raw_status = str(result.get("status", "") or "task_failed")
            status = _discover_failure(result) or raw_status
            if status == "timeout":
                mode_results[mode] = status
                blocked = True
                break
            recovered_modes.add(mode)
            mode_results[mode] = status
            keyword_id = row["source_keyword_id"]
            if mode == "topic" and isinstance(keyword_id, int) and keyword_id > 0:
                recovered_claims.append(
                    (
                        _RecoveredKeywordClaim(
                            id=keyword_id,
                            keyword=str(row["keyword"] or ""),
                        ),
                        status,
                    )
                )
            if status == "public_page_unavailable" and raw_status != "partial":
                # A missing public seed is local to that page, not an auth or
                # transport failure. Preserve the same exception on recovery.
                continue
            if status not in {"ok", "empty", "partial", "public_page_unavailable"}:
                blocked = True
                if raw_status != "partial":
                    continue
            if mode not in modes:
                continue
            strategy = INSTAGRAM_SOURCE_STRATEGIES[mode]
            task_contents: list[Any] = []
            for item in result.get("items", []):
                content = instagram_item_to_content(
                    item,
                    strategy=strategy,
                    source_keyword_id=(keyword_id if mode == "topic" else None),
                )
                if content is not None:
                    task_contents.append(content)

            # Scan the entire owner ledger even after the candidate limit is
            # reached so an unfinished/live owner later in the sequence can
            # block fresh suffix creation.  Count globally deduplicated
            # contents, not raw rows; duplicate completed tasks must not hide
            # that later unfinished owner.  If this task has unique rows beyond
            # the current handoff budget, keep its owner row unconsumed so a
            # later cycle can recover them instead of silently acknowledging
            # work it did not hand off.
            merged = _dedupe_contents([*contents, *task_contents])
            if len(merged) > limit:
                self._pending_consumption_task_ids.discard(task_id)
            contents = merged[:limit]
        return contents, recovered_modes, mode_results, recovered_claims, blocked

    def _enqueue_owned_discover(
        self,
        payload: dict[str, object],
        *,
        mode: str,
        source_keyword_id: int | None,
    ) -> str | None:
        """Atomically enqueue and tag a formal-producer discover task."""

        conn = getattr(self.database, "conn", None)
        if conn is None:
            raw_task_id = self.task_queue.enqueue_with_id("discover", payload, daily_budget=0)
            if raw_task_id is not None and self._register_task is not None:
                self._register_task(str(raw_task_id))
            return str(raw_task_id) if raw_task_id is not None else None
        participating = bool(conn.in_transaction)
        try:
            if not participating:
                conn.execute("BEGIN IMMEDIATE")
            raw_task_id = self.task_queue.enqueue_with_id("discover", payload, daily_budget=0)
            task_id = str(raw_task_id) if raw_task_id is not None else None
            if task_id is not None:
                conn.execute(
                    "INSERT INTO instagram_discovery_owned_task "
                    "(task_id, mode, source_keyword_id, keyword, owner_token, lease_until) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        mode,
                        source_keyword_id,
                        str(payload.get("topic") or payload.get("username") or ""),
                        self._owner_token,
                        self._owner_lease_until(),
                    ),
                )
            if not participating:
                conn.commit()
            if task_id is not None and self._register_task is not None:
                self._register_task(task_id)
            return task_id
        except Exception:
            if not participating and conn.in_transaction:
                conn.rollback()
            raise

    def _commit_task_consumptions(self) -> None:
        if not self._pending_consumption_task_ids:
            return
        self.database.conn.executemany(
            "UPDATE instagram_discovery_owned_task SET consumed_at=CURRENT_TIMESTAMP "
            "WHERE task_id=? AND owner_token=? AND consumed_at IS NULL",
            [
                (task_id, self._owner_token)
                for task_id in sorted(self._pending_consumption_task_ids)
            ],
        )
        self.database.conn.commit()
        self._pending_consumption_task_ids.clear()

    async def _kick_dispatcher(self) -> None:
        kick = self.kick or kick_instagram_task_dispatcher
        try:
            result = kick()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("instagram producer: task dispatcher kick failed", exc_info=True)

    def _enqueue_contents(
        self,
        contents: list[Any],
    ) -> tuple[int, set[int], Counter[str], bool]:
        retained_keyword_ids: set[int] = set()
        retained_counts: Counter[str] = Counter()
        if self.candidate_pipeline is None:
            retained_keyword_ids.update(
                int(item.source_keyword_id)
                for item in contents
                if isinstance(item.source_keyword_id, int) and item.source_keyword_id > 0
            )
            retained_counts.update(_mode_for_strategy(item.source_strategy) for item in contents)
            self._record_retained_budget(retained_counts)
            return len(contents), retained_keyword_ids, retained_counts, False
        inserted = 0
        handoff_failed = False
        for content in contents:
            try:
                accepted = int(
                    self.candidate_pipeline.enqueue_candidates(
                        [content],
                        source_context=content.source_strategy,
                    )
                )
            except Exception:
                handoff_failed = True
                logger.exception(
                    "instagram producer: candidate handoff failed for content_id=%s",
                    getattr(content, "content_id", ""),
                )
                continue
            if accepted <= 0:
                continue
            inserted += 1
            mode = _mode_for_strategy(content.source_strategy)
            retained_counts[mode] += 1
            # Persist each accepted handoff before attempting the next item.
            # A later candidate failure must not erase budget accounting for
            # data that the durable candidate pool already retained.
            self._record_retained_budget(Counter({mode: 1}))
            keyword_id = getattr(content, "source_keyword_id", None)
            if isinstance(keyword_id, int) and keyword_id > 0:
                retained_keyword_ids.add(keyword_id)
                self._mark_retained_keyword_used(keyword_id)
        return inserted, retained_keyword_ids, retained_counts, handoff_failed

    def _mark_retained_keyword_used(self, keyword_id: int) -> None:
        coordinator = self.keyword_fetch
        if coordinator is None:
            return
        coordinator.mark_used([_RecoveredKeywordClaim(id=keyword_id, keyword="")])
        self._finalized_keyword_ids.add(keyword_id)

    def _ensure_budget_table(self) -> None:
        self.database.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_discovery_budget (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                retained_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.database.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_discovery_attempt (
                source TEXT PRIMARY KEY,
                attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.database.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_discovery_owned_task (
                task_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                source_keyword_id INTEGER,
                keyword TEXT NOT NULL DEFAULT '',
                owner_token TEXT NOT NULL DEFAULT '',
                lease_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consumed_at TIMESTAMP
            )
            """
        )
        owner_columns = {
            str(row[1])
            for row in self.database.conn.execute(
                "PRAGMA table_info(instagram_discovery_owned_task)"
            ).fetchall()
        }
        if "owner_token" not in owner_columns:
            self.database.conn.execute(
                "ALTER TABLE instagram_discovery_owned_task "
                "ADD COLUMN owner_token TEXT NOT NULL DEFAULT ''"
            )
        if "lease_until" not in owner_columns:
            self.database.conn.execute(
                "ALTER TABLE instagram_discovery_owned_task ADD COLUMN lease_until TIMESTAMP"
            )
        self.database.conn.execute(
            "UPDATE instagram_discovery_owned_task "
            "SET lease_until=COALESCE(lease_until, created_at) "
            "WHERE consumed_at IS NULL"
        )
        self.database.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instagram_discovery_owned_open "
            "ON instagram_discovery_owned_task (consumed_at, created_at)"
        )
        self.database.conn.execute(
            "DELETE FROM instagram_discovery_owned_task "
            "WHERE consumed_at IS NOT NULL AND consumed_at<datetime('now', '-7 days')"
        )
        self.database.conn.commit()

    def _owner_lease_until(self) -> str:
        seconds = (
            max(
                60.0,
                float(self.wait_seconds),
                float(self.cycle_wait_seconds),
            )
            + 60.0
        )
        return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")

    def _claim_owned_task_for_recovery(self, task_id: str) -> bool:
        """CAS one expired owner lease so only one process may resume it."""

        conn = self.database.conn
        participating = bool(conn.in_transaction)
        try:
            if not participating:
                conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE instagram_discovery_owned_task "
                "SET owner_token=?, lease_until=? "
                "WHERE task_id=? AND consumed_at IS NULL AND "
                "(owner_token=? OR owner_token='' OR lease_until IS NULL "
                "OR lease_until<=CURRENT_TIMESTAMP)",
                (
                    self._owner_token,
                    self._owner_lease_until(),
                    task_id,
                    self._owner_token,
                ),
            )
            claimed = bool(cursor.rowcount == 1)
            if not participating:
                conn.commit()
            return claimed
        except Exception:
            if not participating and conn.in_transaction:
                conn.rollback()
            raise

    def _still_owns_task(self, task_id: str) -> bool:
        conn = getattr(self.database, "conn", None)
        if conn is None:
            return True
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='instagram_discovery_owned_task'"
            ).fetchone()
            if table is None:
                return True
            row = conn.execute(
                "SELECT owner_token, consumed_at FROM instagram_discovery_owned_task "
                "WHERE task_id=?",
                (task_id,),
            ).fetchone()
        except Exception:
            return True
        if row is None:
            return True
        return row["consumed_at"] is None and str(row["owner_token"] or "") == self._owner_token

    def _release_owner_leases(self) -> None:
        """Make cleanly abandoned tasks immediately adoptable after this run."""

        conn = getattr(self.database, "conn", None)
        if conn is None:
            return
        try:
            participating = bool(conn.in_transaction)
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='instagram_discovery_owned_task'"
            ).fetchone()
            if table is None:
                return
            conn.execute(
                "UPDATE instagram_discovery_owned_task "
                "SET lease_until=CURRENT_TIMESTAMP "
                "WHERE owner_token=? AND consumed_at IS NULL",
                (self._owner_token,),
            )
            if not participating:
                conn.commit()
        except Exception:
            logger.debug("instagram producer: owner lease release failed", exc_info=True)

    def _claim_attempt(self, *, force: bool) -> bool:
        """Atomically reserve the cadence window across producer processes."""

        conn = self.database.conn
        participating = bool(conn.in_transaction)
        try:
            if not participating:
                conn.execute("BEGIN IMMEDIATE")
            if not force and not self._is_due():
                if not participating:
                    conn.commit()
                return False
            conn.execute(
                "INSERT INTO instagram_discovery_attempt (source, attempted_at) "
                "VALUES ('instagram', CURRENT_TIMESTAMP) "
                "ON CONFLICT(source) DO UPDATE SET attempted_at=CURRENT_TIMESTAMP"
            )
            if not participating:
                conn.commit()
            self._last_attempt_at = datetime.now(UTC)
            return True
        except Exception:
            if not participating and conn.in_transaction:
                conn.rollback()
            raise

    def _attempted_within(self, minutes: int) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(minutes=max(0, minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        try:
            row = self.database.conn.execute(
                "SELECT 1 FROM instagram_discovery_attempt "
                "WHERE source='instagram' AND attempted_at>=? LIMIT 1",
                (cutoff,),
            ).fetchone()
        except Exception:
            row = None
        if row is not None:
            return True
        return self._last_attempt_at is not None and (
            datetime.now(UTC) - self._last_attempt_at < timedelta(minutes=minutes)
        )

    def _bounded_by_remaining_budget(self, mode: str, requested: int) -> int:
        budget = int(self.daily_topic_budget) if mode == "topic" else int(self.daily_creator_budget)
        if budget <= 0:
            return max(1, int(requested))
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.database.conn.execute(
            "SELECT COALESCE(SUM(retained_count), 0) "
            "FROM instagram_discovery_budget WHERE mode=? AND created_at>=?",
            (mode, today),
        ).fetchone()
        used = int(row[0] if row else 0)
        return max(0, min(int(requested), budget - used))

    def _record_retained_budget(self, counts: Counter[str]) -> None:
        rows = [
            (mode, int(count))
            for mode, count in counts.items()
            if mode in INSTAGRAM_SOURCE_ORDER and int(count) > 0
        ]
        if not rows:
            return
        self.database.conn.executemany(
            "INSERT INTO instagram_discovery_budget (mode, retained_count) VALUES (?, ?)",
            rows,
        )
        self.database.conn.commit()

    def _finish_topic_claims(
        self,
        claims: list[Any],
        retained_keyword_ids: set[int],
        outcome: str,
    ) -> None:
        coordinator = self.keyword_fetch
        if coordinator is None:
            return
        for claim in claims:
            if int(claim.id) in retained_keyword_ids:
                if int(claim.id) not in self._finalized_keyword_ids:
                    coordinator.mark_used([claim])
            elif self._topic_claim_outcomes.get(int(claim.id), outcome) in {
                "empty",
                "public_page_unavailable",
            }:
                coordinator.mark_failed([claim])
            else:
                requeue = getattr(coordinator, "requeue_transient", None)
                if callable(requeue):
                    requeue(claim)
                else:
                    coordinator.rollback(claim)

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._attempted_within(self.min_interval_minutes):
            return False
        if ledger_available(self.database):
            return not producer_ran_within(
                self.database,
                "instagram",
                self.min_interval_minutes,
            )
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _stamp_run(self, discovered: int) -> None:
        if discovered <= 0:
            return
        record_producer_run(self.database, "instagram", int(discovered))
        self._last_run_at = datetime.now(UTC)

    def _candidate_pool_full(self) -> bool:
        return bool(
            candidate_pool_full_for_source(
                self.candidate_pipeline,
                "instagram",
                logger=logger,
                label="instagram producer",
            )
        )

    def _extension_present(self) -> bool:
        if self.presence is None:
            return True
        is_present = getattr(self.presence, "is_present", None)
        if not callable(is_present):
            return False
        try:
            return bool(is_present(max(1, int(self.presence_grace_seconds))))
        except Exception:
            logger.debug("instagram producer: extension presence unavailable", exc_info=True)
            return False

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("instagram producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def build_instagram_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
    kick: Any | None = None,
    presence: Any | None = None,
    presence_grace_seconds: int = 90,
    manual: bool = False,
) -> InstagramDiscoveryProducer | None:
    """Build a source-enabled producer; explicit init need not enable scheduling."""

    source_cfg = getattr(getattr(config, "sources", None), "instagram", None)
    if source_cfg is None or not bool(getattr(source_cfg, "enabled", False)):
        return None
    if not manual and not bool(getattr(getattr(config, "scheduler", None), "enabled", True)):
        return None
    if not hasattr(database, "conn"):
        logger.info("instagram producer disabled: database does not expose sqlite connection")
        return None
    return InstagramDiscoveryProducer(
        database=database,
        task_queue=InstagramTaskQueue(database),
        soul_engine=soul_engine,
        enabled=True,
        source_modes=_normalize_modes(getattr(source_cfg, "source_modes", INSTAGRAM_SOURCE_ORDER)),
        daily_topic_budget=int(getattr(source_cfg, "daily_topic_budget", 60)),
        daily_creator_budget=int(getattr(source_cfg, "daily_creator_budget", 30)),
        min_interval_minutes=int(getattr(source_cfg, "min_interval_minutes", 10)),
        request_interval_ms=min(
            30_000,
            max(1_000, int(getattr(source_cfg, "request_interval_seconds", 3)) * 1000),
        ),
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
        kick=kick,
        presence=presence,
        presence_grace_seconds=max(1, int(presence_grace_seconds)),
    )


def kick_instagram_task_dispatcher() -> None:
    """No-op fallback; production injects the runtime EventHub broadcaster."""

    logger.debug("instagram task kick skipped: no runtime broadcaster injected")


def _profile_keywords(profile: Any, limit: int) -> list[str]:
    interests = list(getattr(getattr(profile, "preferences", None), "interests", []) or [])
    out: list[str] = []
    seen: set[str] = set()
    for interest in interests:
        text = _topic_seed(str(getattr(interest, "name", "") or interest))
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _topic_seed(value: str) -> str:
    """Map known interest aliases to observed routes, without inventing search."""

    value = value.strip().lstrip("#").casefold()
    for topic, aliases in _TOPIC_ALIASES.items():
        for alias in aliases:
            if (alias.isascii() and re.search(rf"\b{re.escape(alias)}\b", value)) or (
                not alias.isascii() and alias in value
            ):
                return topic
    # Unknown terms remain bounded candidate routes, not asserted-valid URLs.
    return re.sub(r"\s+", "-", value)[:300]


def _discover_failure(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "task_failed")
    error = str(result.get("error") or "")
    if status not in {"ok", "empty", "partial"}:
        return error or status
    if status == "partial" and error and error not in _DISCOVER_BOUNDED_PARTIAL_CODES:
        return error
    return ""


def _creator_seed_usernames(contents: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for content in contents:
        username = str(getattr(content, "author_name", "") or "").strip().lstrip("@")
        folded = username.casefold()
        if not is_instagram_username(username) or folded in seen:
            continue
        seen.add(folded)
        out.append(username)
    return out


def _dedupe_contents(contents: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for content in contents:
        key = str(getattr(content, "content_id", "") or getattr(content, "bvid", "")).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(content)
    return out


def _normalize_modes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip() for part in value]
    else:
        raw = list(INSTAGRAM_SOURCE_ORDER)
    selected = {item for item in raw if item in INSTAGRAM_SOURCE_ORDER}
    # Creator discovery is seeded by authors observed in a bounded topic
    # envelope. A creator-only configuration has no independent username
    # input, so normalize it to the only productive, documented composition.
    if "creator" in selected:
        selected.add("topic")
    return tuple(item for item in INSTAGRAM_SOURCE_ORDER if item in selected)


def _fresh_modes_after_recovery(
    modes: tuple[str, ...],
    recovered_modes: set[str],
) -> frozenset[str]:
    """Return only the unstarted suffix of one durable logical cycle."""

    if not recovered_modes:
        return frozenset(modes)
    recovered_indexes = [
        INSTAGRAM_SOURCE_ORDER.index(mode)
        for mode in recovered_modes
        if mode in INSTAGRAM_SOURCE_ORDER
    ]
    if not recovered_indexes:
        return frozenset()
    last_recovered = max(recovered_indexes)
    return frozenset(mode for mode in modes if INSTAGRAM_SOURCE_ORDER.index(mode) > last_recovered)


def _mode_for_strategy(strategy: object) -> str:
    return "creator" if str(strategy) == INSTAGRAM_SOURCE_STRATEGIES["creator"] else "topic"


__all__ = [
    "INSTAGRAM_SOURCE_ORDER",
    "InstagramDiscoveryProducer",
    "build_instagram_discovery_producer",
    "kick_instagram_task_dispatcher",
]
