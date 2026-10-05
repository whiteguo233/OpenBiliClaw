"""Runtime producer for anonymous Weibo discovery.

The client owns Weibo's short-lived anonymous visitor session.  This module
only orchestrates public discovery branches and hands canonical posts to the
shared candidate pipeline:

* ``search`` claims unified planner keywords (or falls back to profile terms);
* ``hot`` treats the hot-search response as query seeds, then fetches real
  posts — a trending word is never admitted as synthetic content;
* ``creator`` follows author ids observed in this same search/hot round.

There is deliberately no ``related`` branch until Weibo exposes a stable,
anonymous related-post endpoint.  Re-searching a hashtag is still search, and
reusing an author is still creator; relabelling either would make source
provenance dishonest.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.runtime.keyword_fetch import PLATFORM_WEIBO
from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.runtime.producer_cadence import (
    ledger_available,
    producer_ran_within,
    record_producer_run,
)
from openbiliclaw.sources.weibo import (
    extract_weibo_mblogs,
    weibo_hot_topic_query,
    weibo_post_to_content,
)

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)

WEIBO_SOURCE_ORDER = ("search", "hot", "creator")
WEIBO_SOURCE_STRATEGIES = {
    "search": "weibo-search",
    "hot": "weibo-hot",
    "creator": "weibo-creator",
}
_WEIBO_DISABLED_DETAIL = "微博来源未启用。"
_WEIBO_PUBLIC_DETAIL = "微博使用匿名访客会话读取公开内容，无需账号登录。"
_OUTCOME_BACKOFF_SECONDS = {
    "valid_empty": 300,
    "no_progress": 120,
    "infrastructure_failure": 60,
}


class WeiboDiscoveryClient(Protocol):
    """Minimal anonymous-client surface consumed by the producer."""

    async def search_posts(
        self,
        keyword: str,
        *,
        page: int = 1,
        limit: int = 10,
    ) -> object: ...

    async def hot_topics(self, *, limit: int = 10) -> object: ...

    async def creator_posts(
        self,
        uid: str,
        *,
        page: int = 1,
        limit: int = 10,
    ) -> object: ...


@dataclass(frozen=True)
class _ModeFetch:
    items: list[DiscoveredContent]
    attempted: bool = True
    skip_reason: str = ""
    search_claims: tuple[Any, ...] = ()


class _PartialModeError(Exception):
    """Preserve posts fetched before a later query/creator request failed."""

    def __init__(
        self,
        error: BaseException,
        items: list[DiscoveredContent],
        *,
        search_claims: tuple[Any, ...] = (),
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.items = items
        self.search_claims = search_claims


@dataclass(frozen=True)
class _EnqueueOutcome:
    enqueued: int
    retained_counts: Counter[str]
    retained_keyword_ids: set[int]
    error: BaseException | None = None


@dataclass
class WeiboDiscoveryProducer:
    """Feed public Weibo posts into the shared raw candidate pool."""

    database: Any
    soul_engine: Any
    client: WeiboDiscoveryClient
    enabled: bool = False
    source_modes: tuple[str, ...] = WEIBO_SOURCE_ORDER
    daily_search_budget: int = 60
    daily_hot_budget: int = 10
    daily_creator_budget: int = 30
    min_interval_minutes: int = 10
    candidate_pipeline: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    keyword_fetch: Any | None = None
    max_hot_topic_seeds: int = 5
    max_creator_seeds: int = 5
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Run enabled branches while respecting cooldown, cadence and budgets."""

        if not self.enabled:
            return self._skip("disabled")
        self._ensure_tables()
        cooldown_until = _read_cooldown_until(self.database)
        if cooldown_until is not None and cooldown_until > datetime.now(UTC):
            return self._skip("rate_limited")
        if not force:
            outcome_backoff = _read_outcome_backoff(self.database)
            if outcome_backoff is not None:
                backoff_reason, backoff_until = outcome_backoff
                if backoff_until > datetime.now(UTC):
                    skip_payload = self._skip("no_output_backoff")
                    skip_payload["backoff_reason"] = backoff_reason
                    skip_payload["cooldown_until"] = backoff_until.isoformat()
                    return skip_payload
        if not force and not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("weibo producer: soul profile unavailable: %s", exc)
            self._set_outcome_backoff("infrastructure_failure")
            return self._skip("no_profile")
        if profile is None:
            self._set_outcome_backoff("infrastructure_failure")
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        modes = _normalize_modes(self.source_modes)
        if not modes:
            return self._skip("mode_disabled")

        allocations = _allocate(requested_limit, modes)
        all_items: list[DiscoveredContent] = []
        mode_results: dict[str, str] = {}
        run_records: dict[str, tuple[int, str, str]] = {}
        errors: list[str] = []
        search_claims: dict[int, Any] = {}

        # The fixed source order is load-bearing: creator consumes author ids
        # from search/hot posts fetched earlier in this same cycle.
        for mode in modes:
            allocation = int(allocations.get(mode, 0))
            if allocation <= 0:
                mode_results[mode] = "not_allocated"
                continue
            branch_limit = min(
                allocation,
                self.remaining_budget(mode, per_run_budget=allocation),
            )
            if branch_limit <= 0:
                mode_results[mode] = "budget_exhausted"
                continue

            try:
                if mode == "search":
                    fetched = await self._run_search(profile, branch_limit)
                elif mode == "hot":
                    fetched = await self._run_hot(branch_limit)
                else:
                    fetched = await self._run_creator(all_items, branch_limit)
            except _PartialModeError as partial:
                code = _error_code(partial.error)
                items = _dedupe_items(partial.items)
                all_items.extend(items)
                search_claims.update((int(item.id), item) for item in partial.search_claims)
                mode_results[mode] = code
                errors.append(code)
                run_records[mode] = (len(items), "partial", code)
                if self._persist_error_cooldown(partial.error):
                    break
                continue
            except Exception as exc:
                code = _error_code(exc)
                logger.warning(
                    "weibo producer branch failed: mode=%s code=%s",
                    mode,
                    code,
                )
                mode_results[mode] = code
                errors.append(code)
                self.record_strategy_run(
                    mode,
                    units_used=0,
                    discovered=0,
                    reason="error",
                    error_code=code,
                )
                if self._persist_error_cooldown(exc):
                    break
                continue

            if not fetched.attempted:
                mode_results[mode] = fetched.skip_reason or f"no_{mode}_seeds"
                continue
            items = _dedupe_items(fetched.items)
            all_items.extend(items)
            search_claims.update((int(item.id), item) for item in fetched.search_claims)
            reason = "ok" if items else "empty"
            mode_results[mode] = reason
            run_records[mode] = (len(items), reason, "")

        items = _dedupe_items(all_items)[:requested_limit]
        source_counts = Counter(item.source_strategy for item in items)

        enqueue_outcome = self._enqueue_retained(items)
        enqueued = enqueue_outcome.enqueued
        retained_counts = enqueue_outcome.retained_counts
        retained_keyword_ids = enqueue_outcome.retained_keyword_ids
        self._finalize_search_claims(search_claims, retained_keyword_ids)

        enqueue_error_code = ""
        if enqueue_outcome.error is not None:
            enqueue_error_code = "candidate_enqueue_error"
            errors.append(enqueue_error_code)
            logger.warning(
                "weibo producer candidate handoff stopped after partial success: error_type=%s",
                type(enqueue_outcome.error).__name__,
            )
            for mode, (discovered, _previous_reason, _previous_error) in tuple(run_records.items()):
                strategy = WEIBO_SOURCE_STRATEGIES[mode]
                if retained_counts.get(strategy, 0) >= source_counts.get(strategy, 0):
                    continue
                accepted = retained_counts.get(strategy, 0)
                run_records[mode] = (
                    discovered,
                    "partial" if accepted > 0 else "error",
                    enqueue_error_code,
                )
                mode_results[mode] = enqueue_error_code

        for mode, (discovered, run_reason, error_code) in run_records.items():
            self.record_strategy_run(
                mode,
                units_used=retained_counts.get(WEIBO_SOURCE_STRATEGIES[mode], 0),
                discovered=discovered,
                reason=run_reason,
                error_code=error_code,
            )
        productive_supply = sum(retained_counts.values())
        self._stamp_run(productive_supply)

        reason = _overall_reason(items, errors, mode_results)
        if enqueue_error_code and enqueued <= 0:
            reason = "error"
        hot_rejected = any(value == "upstream_rejected" for value in mode_results.values())
        ran = bool(run_records or errors or hot_rejected)
        made_progress = productive_supply > 0
        if made_progress:
            self._clear_outcome_backoff()
        elif ran and not any(code in {"rate_limited", "too_many_requests"} for code in errors):
            if errors:
                self._set_outcome_backoff("infrastructure_failure")
            elif items:
                self._set_outcome_backoff("no_progress")
            else:
                self._set_outcome_backoff("valid_empty")
        payload: dict[str, object] = {
            "discovered": len(items),
            "source_counts": dict(source_counts),
            "mode_results": mode_results,
            "reason": reason,
            "ran": ran,
            "made_progress": made_progress,
            "productive_supply": productive_supply,
        }
        if self.candidate_pipeline is not None:
            payload["enqueued"] = enqueued
            if (
                enqueued > 0
                and not enqueue_error_code
                and not self.candidate_evaluation_owned_by_coordinator
            ):
                payload.update(
                    await self.candidate_pipeline.drain_pending(
                        profile=profile,
                        batch_size=requested_limit,
                    )
                )
        return payload

    def _enqueue_retained(
        self,
        items: list[DiscoveredContent],
    ) -> _EnqueueOutcome:
        """Hand off final candidates and report only rows actually retained."""

        if self.candidate_pipeline is None:
            counts = Counter(item.source_strategy for item in items)
            keyword_ids = {
                keyword_id for item in items if (keyword_id := _source_keyword_id(item)) is not None
            }
            return _EnqueueOutcome(len(items), counts, keyword_ids)

        accepted_total = 0
        accepted_counts: Counter[str] = Counter()
        accepted_keyword_ids: set[int] = set()
        for strategy in WEIBO_SOURCE_STRATEGIES.values():
            strategy_items = [item for item in items if item.source_strategy == strategy]
            if not strategy_items:
                continue
            if strategy != WEIBO_SOURCE_STRATEGIES["search"]:
                groups: list[tuple[int | None, list[DiscoveredContent]]] = [(None, strategy_items)]
            else:
                by_keyword: dict[int | None, list[DiscoveredContent]] = {}
                for item in strategy_items:
                    by_keyword.setdefault(_source_keyword_id(item), []).append(item)
                groups = list(by_keyword.items())

            for keyword_id, grouped in groups:
                try:
                    inserted_raw = self.candidate_pipeline.enqueue_candidates(
                        grouped,
                        source_context=strategy,
                    )
                except Exception as exc:
                    return _EnqueueOutcome(
                        accepted_total,
                        accepted_counts,
                        accepted_keyword_ids,
                        exc,
                    )
                inserted = _bounded_insert_count(inserted_raw, available=len(grouped))
                accepted_total += inserted
                accepted_counts[strategy] += inserted
                if inserted > 0 and keyword_id is not None:
                    accepted_keyword_ids.add(keyword_id)
        return _EnqueueOutcome(
            accepted_total,
            accepted_counts,
            accepted_keyword_ids,
        )

    def _finalize_search_claims(
        self,
        claims: dict[int, Any],
        retained_keyword_ids: set[int],
    ) -> None:
        """Resolve keyword leases only after final dedupe/filter handoff."""

        coordinator = self.keyword_fetch
        if coordinator is None:
            return
        for keyword_id, claimed in claims.items():
            if keyword_id in retained_keyword_ids:
                coordinator.mark_used([claimed])
            else:
                coordinator.rollback(claimed)

    async def _run_search(self, profile: Any, limit: int) -> _ModeFetch:
        coordinator = self.keyword_fetch
        should_claim = coordinator is not None and bool(
            getattr(coordinator, "should_claim", lambda: False)()
        )
        claimed: list[Any] = []
        if should_claim and coordinator is not None:
            claimed = list(coordinator.claim(PLATFORM_WEIBO, n=min(limit, 5)))

        queries: list[tuple[str, int | None, Any | None]]
        if claimed:
            queries = [(str(item.keyword).strip(), int(item.id), item) for item in claimed]
        else:
            queries = [
                (keyword, None, None)
                for keyword in _fallback_profile_keywords(profile, min(limit, 5))
            ]
        if not queries:
            return _ModeFetch([], attempted=False, skip_reason="no_search_keywords")

        items: list[DiscoveredContent] = []
        produced_claims: dict[int, Any] = {}
        for index, (keyword, keyword_id, claimed_item) in enumerate(queries):
            if not keyword:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                continue
            if len(items) >= limit:
                _rollback_claims(coordinator, [item for _, _, item in queries[index:] if item])
                break
            try:
                result = await self.client.search_posts(
                    keyword,
                    page=1,
                    limit=min(20, limit - len(items)),
                )
                produced = _normalize_posts(
                    result,
                    strategy=WEIBO_SOURCE_STRATEGIES["search"],
                    source_keyword_id=keyword_id,
                )
            except Exception as exc:
                if claimed_item is not None and coordinator is not None:
                    # HTTP/visitor/schema failures describe the platform, not
                    # this query. Re-pend the lease; only a successful empty
                    # response is a definite no-yield keyword failure.
                    coordinator.rollback(claimed_item)
                _rollback_claims(
                    coordinator,
                    [item for _, _, item in queries[index + 1 :] if item],
                )
                if items:
                    raise _PartialModeError(
                        exc,
                        items[:limit],
                        search_claims=tuple(produced_claims.values()),
                    ) from exc
                if _error_code(exc) == "upstream_rejected":
                    return _ModeFetch([], attempted=False, skip_reason="upstream_rejected")
                raise

            items.extend(produced)
            if claimed_item is not None and coordinator is not None:
                if produced:
                    produced_claims[int(claimed_item.id)] = claimed_item
                else:
                    coordinator.mark_failed([claimed_item])
        return _ModeFetch(
            _dedupe_items(items)[:limit],
            search_claims=tuple(produced_claims.values()),
        )

    async def _run_hot(self, limit: int) -> _ModeFetch:
        seed_limit = min(max(1, int(self.max_hot_topic_seeds)), max(1, limit))
        try:
            topics_result = await self.client.hot_topics(limit=seed_limit)
        except Exception as exc:
            if _error_code(exc) == "upstream_rejected":
                # Weibo's anonymous hot-search endpoint intermittently rejects
                # the visitor session. Treat that as an empty branch instead of
                # a producer-wide error so search/creator can still run and the
                # outcome backoff doesn't stall the whole source.
                return _ModeFetch([], attempted=False, skip_reason="upstream_rejected")
            raise
        topic_rows = _hot_topic_rows(topics_result)
        seeds = _hot_topic_seeds(topic_rows, limit=seed_limit)
        if not seeds:
            return _ModeFetch([])

        items: list[DiscoveredContent] = []
        for index, (query, rank) in enumerate(seeds):
            if len(items) >= limit:
                break
            remaining_seeds = max(1, len(seeds) - index)
            per_seed_limit = max(1, math.ceil((limit - len(items)) / remaining_seeds))
            try:
                result = await self.client.search_posts(
                    query,
                    page=1,
                    limit=min(20, per_seed_limit),
                )
            except Exception as exc:
                if items:
                    raise _PartialModeError(exc, items[:limit]) from exc
                if _error_code(exc) == "upstream_rejected":
                    return _ModeFetch([], attempted=False, skip_reason="upstream_rejected")
                raise
            produced = _normalize_posts(
                result,
                strategy=WEIBO_SOURCE_STRATEGIES["hot"],
                source_rank=rank,
            )
            items.extend(produced)
        return _ModeFetch(_dedupe_items(items)[:limit])

    async def _run_creator(
        self,
        seed_items: list[DiscoveredContent],
        limit: int,
    ) -> _ModeFetch:
        creator_ids = _creator_seed_ids(seed_items)[: max(1, int(self.max_creator_seeds))]
        if not creator_ids:
            return _ModeFetch([], attempted=False, skip_reason="no_creator_seeds")

        allocations = _allocate(limit, tuple(creator_ids))
        items: list[DiscoveredContent] = []
        for uid in creator_ids:
            creator_limit = int(allocations.get(uid, 0))
            if creator_limit <= 0:
                continue
            try:
                result = await self.client.creator_posts(
                    uid,
                    page=1,
                    limit=min(20, creator_limit),
                )
            except Exception as exc:
                if items:
                    raise _PartialModeError(exc, items[:limit]) from exc
                raise
            items.extend(
                _normalize_posts(
                    result,
                    strategy=WEIBO_SOURCE_STRATEGIES["creator"],
                )
            )
        return _ModeFetch(_dedupe_items(items)[:limit])

    def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
        configured = {
            "search": int(self.daily_search_budget),
            "hot": int(self.daily_hot_budget),
            "creator": int(self.daily_creator_budget),
        }.get(mode, -1)
        if configured == 0:
            return max(0, int(per_run_budget))
        if configured < 0:
            return 0
        return max(0, configured - self.consumed_today(mode))

    def consumed_today(self, mode: str) -> int:
        self._ensure_tables()
        row = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM weibo_discovery_runs
            WHERE mode = ? AND reason IN ('ok', 'empty', 'partial')
              AND created_at >= datetime('now', 'start of day')
            """,
            (mode,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def record_strategy_run(
        self,
        mode: str,
        *,
        units_used: int,
        discovered: int,
        reason: str,
        error_code: str = "",
    ) -> None:
        """Persist one mode result for daily budgets and local-only status."""

        self._ensure_tables()
        self.database.conn.execute(
            """
            INSERT INTO weibo_discovery_runs(mode, units, discovered, reason, error_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mode,
                max(0, int(units_used)),
                max(0, int(discovered)),
                str(reason or "error")[:32],
                str(error_code or "")[:80],
            ),
        )
        self.database.conn.commit()

    def _persist_error_cooldown(self, error: BaseException) -> bool:
        if not _is_rate_limited(error):
            return False
        retry_after = _retry_after_seconds(error) or 300
        self._set_cooldown(retry_after)
        return True

    def _set_cooldown(self, seconds: int) -> None:
        until = datetime.now(UTC) + timedelta(seconds=min(86_400, max(1, int(seconds))))
        self.database.conn.execute(
            """
            INSERT INTO weibo_discovery_state(state_key, cooldown_until, updated_at)
            VALUES ('global', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = CURRENT_TIMESTAMP
            """,
            (until.isoformat(),),
        )
        self.database.conn.commit()

    def _set_outcome_backoff(self, reason: str) -> None:
        seconds = _OUTCOME_BACKOFF_SECONDS[reason]
        until = datetime.now(UTC) + timedelta(seconds=seconds)
        self.database.conn.execute(
            "DELETE FROM weibo_discovery_state WHERE state_key LIKE 'outcome:%'"
        )
        self.database.conn.execute(
            """
            INSERT INTO weibo_discovery_state(state_key, cooldown_until, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (f"outcome:{reason}", until.isoformat()),
        )
        self.database.conn.commit()

    def _clear_outcome_backoff(self) -> None:
        self.database.conn.execute(
            "DELETE FROM weibo_discovery_state WHERE state_key LIKE 'outcome:%'"
        )
        self.database.conn.commit()

    def _stamp_run(self, discovered: int) -> None:
        productive = max(0, int(discovered))
        record_producer_run(self.database, PLATFORM_WEIBO, productive)
        if productive > 0:
            self._last_run_at = datetime.now(UTC)

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if ledger_available(self.database):
            return not producer_ran_within(
                self.database,
                PLATFORM_WEIBO,
                self.min_interval_minutes,
            )
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _candidate_pool_full(self) -> bool:
        return candidate_pool_full_for_source(
            self.candidate_pipeline,
            PLATFORM_WEIBO,
            logger=logger,
            label="weibo producer",
        )

    def _ensure_tables(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS weibo_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                error_code TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_weibo_runs_mode_created
                ON weibo_discovery_runs(mode, created_at);
            CREATE TABLE IF NOT EXISTS weibo_discovery_state (
                state_key TEXT PRIMARY KEY,
                cooldown_until TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.database.conn.commit()

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("weibo producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def weibo_source_status(
    database: Any,
    *,
    enabled: bool,
    source_modes: object = WEIBO_SOURCE_ORDER,
) -> dict[str, object]:
    """Return local-only Weibo discovery health without contacting Weibo."""

    if not enabled:
        return {"state": "disabled", "detail": _WEIBO_DISABLED_DETAIL}
    try:
        cooldown = _read_cooldown_until(database)
    except Exception:
        cooldown = None
    if cooldown is not None and cooldown > datetime.now(UTC):
        return {
            "state": "rate_limited",
            "detail": "微博公开接口正在退避冷却，到期后自动重试。",
            "cooldown_until": cooldown.isoformat(),
        }
    try:
        rows = database.conn.execute(
            """
            SELECT r.mode, r.reason, r.error_code
            FROM weibo_discovery_runs AS r
            JOIN (
                SELECT mode, MAX(id) AS id FROM weibo_discovery_runs GROUP BY mode
            ) AS latest ON latest.id = r.id
            """
        ).fetchall()
    except Exception:
        rows = []
    active_modes = set(_normalize_modes(source_modes))
    rows = [row for row in rows if str(row["mode"]) in active_modes]
    if not rows:
        return {"state": "unverified", "detail": "尚未运行微博内容发现。"}

    successes = sum(1 for row in rows if str(row["reason"]) in {"ok", "empty"})
    partials = sum(1 for row in rows if str(row["reason"]) == "partial")
    failures = len(rows) - successes - partials
    if partials or (successes and failures):
        state = "partial"
        detail = "微博部分公开发现分支最近失败，将自动重试。"
    elif successes:
        state = "ready"
        detail = _WEIBO_PUBLIC_DETAIL
    else:
        state = "error"
        detail = "微博公开发现最近失败，将按节流策略自动重试。"
    return {
        "state": state,
        "detail": detail,
        "modes": {
            str(row["mode"]): {
                "reason": str(row["reason"]),
                "error_code": str(row["error_code"]),
            }
            for row in rows
        },
    }


def _read_cooldown_until(database: Any) -> datetime | None:
    row = database.conn.execute(
        "SELECT cooldown_until FROM weibo_discovery_state WHERE state_key = 'global'"
    ).fetchone()
    if row is None or not str(row[0] or "").strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _read_outcome_backoff(database: Any) -> tuple[str, datetime] | None:
    row = database.conn.execute(
        """
        SELECT state_key, cooldown_until
        FROM weibo_discovery_state
        WHERE state_key LIKE 'outcome:%'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    state_key = str(row[0] or "")
    reason = state_key.removeprefix("outcome:")
    if reason not in _OUTCOME_BACKOFF_SECONDS:
        return None
    try:
        parsed = datetime.fromisoformat(str(row[1] or ""))
    except ValueError:
        return None
    return reason, parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _normalize_modes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = [str(part).strip() for part in value]
    else:
        raw = []
    selected = {mode for mode in raw if mode in WEIBO_SOURCE_ORDER}
    return tuple(mode for mode in WEIBO_SOURCE_ORDER if mode in selected)


def _allocate(limit: int, keys: tuple[str, ...]) -> dict[str, int]:
    if not keys:
        return {}
    total = max(0, int(limit))
    base, remainder = divmod(total, len(keys))
    return {key: base + (1 if index < remainder else 0) for index, key in enumerate(keys)}


def _fallback_profile_keywords(profile: Any, limit: int) -> list[str]:
    preferences = (
        profile.get("preferences")
        if isinstance(profile, Mapping)
        else getattr(profile, "preferences", None)
    )
    interests = (
        preferences.get("interests", [])
        if isinstance(preferences, Mapping)
        else getattr(preferences, "interests", [])
    )
    out: list[str] = []
    seen: set[str] = set()
    for interest in interests or []:
        raw_name = (
            interest.get("name")
            if isinstance(interest, Mapping)
            else interest
            if isinstance(interest, str)
            else getattr(interest, "name", None)
        )
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _result_rows(result: object) -> list[dict[str, Any]]:
    value = result
    if not isinstance(value, Mapping):
        for attribute in ("items", "rows", "data"):
            candidate = getattr(value, attribute, None)
            if candidate is not None and not callable(candidate):
                value = candidate
                break
    if isinstance(value, Mapping):
        for key in ("items", "rows", "posts", "mblogs"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                value = candidate
                break
        else:
            return extract_weibo_mblogs(value)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []

    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        mblog = item.get("mblog")
        rows.append(dict(mblog) if isinstance(mblog, Mapping) else dict(item))
    return rows


def _normalize_posts(
    result: object,
    *,
    strategy: str,
    source_keyword_id: int | None = None,
    source_rank: int = 0,
) -> list[DiscoveredContent]:
    items: list[DiscoveredContent] = []
    for row in _result_rows(result):
        content = weibo_post_to_content(
            row,
            strategy=strategy,
            source_keyword_id=source_keyword_id,
        )
        if content is None:
            continue
        if source_rank > 0:
            content.source_rank = source_rank
        items.append(content)
    return _dedupe_items(items)


def _hot_topic_rows(result: object) -> list[dict[str, Any]]:
    value = result
    if not isinstance(value, Mapping):
        for attribute in ("items", "rows", "data"):
            candidate = getattr(value, attribute, None)
            if candidate is not None and not callable(candidate):
                value = candidate
                break
    if isinstance(value, Mapping):
        data = value.get("data")
        if isinstance(data, Mapping):
            value = data
        if isinstance(value, Mapping):
            for key in ("realtime", "hotgov", "items", "rows", "topics"):
                candidate = value.get(key)
                if isinstance(candidate, list):
                    value = candidate
                    break
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _hot_topic_seeds(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        query = weibo_hot_topic_query(row)
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        rank = _non_negative_int(row.get("realpos") or row.get("rank")) or index + 1
        out.append((query, rank))
        if len(out) >= max(1, int(limit)):
            break
    return out


def _creator_seed_ids(items: list[DiscoveredContent]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        uid = str(max(0, int(getattr(item, "up_mid", 0) or 0)))
        if uid == "0" or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _dedupe_items(items: list[DiscoveredContent]) -> list[DiscoveredContent]:
    out: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        key = item.item_key or f"weibo:{item.content_id or item.bvid}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _source_keyword_id(item: DiscoveredContent) -> int | None:
    value = item.source_keyword_id
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _bounded_insert_count(value: object, *, available: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return min(max(0, value), max(0, int(available)))


def _rollback_claims(coordinator: Any | None, claims: list[Any]) -> None:
    if coordinator is None:
        return
    for claimed in claims:
        coordinator.rollback(claimed)


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value)) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            return max(0, int(value.strip() or "0"))
        except ValueError:
            return 0
    return 0


def _error_code(error: BaseException) -> str:
    code = str(getattr(error, "code", "") or "").strip().lower()
    return code[:80] if code else "error"


def _retry_after_seconds(error: BaseException) -> int:
    return _non_negative_int(getattr(error, "retry_after_seconds", 0))


def _is_rate_limited(error: BaseException) -> bool:
    return _error_code(error) in {"rate_limited", "too_many_requests"}


def _overall_reason(
    items: list[DiscoveredContent],
    errors: list[str],
    mode_results: dict[str, str],
) -> str:
    if items and errors:
        return "partial"
    if errors:
        return "error"
    outcomes = list(mode_results.values())
    allocated = [outcome for outcome in outcomes if outcome != "not_allocated"]
    if allocated and all(outcome == "budget_exhausted" for outcome in allocated):
        return "budget_exhausted"
    if items:
        return "ok"
    if allocated and all(outcome == "no_creator_seeds" for outcome in allocated):
        return "no_creator_seeds"
    if allocated and all(outcome == "no_search_keywords" for outcome in allocated):
        return "no_search_keywords"
    return "empty"
