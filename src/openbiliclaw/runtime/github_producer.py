"""Runtime producer for GitHub's public repository search API.

The integration is intentionally repository-only and public-only.  An optional
personal access token raises the upstream rate limit, but never expands the
visibility scope: every server-owned query carries ``is:public`` and the
normalizer rejects rows marked private.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.sources.github import (
    github_public_repository_query,
    github_repository_to_content,
)
from openbiliclaw.sources.github_client import GitHubAPIError

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)

GITHUB_SOURCE_MODES = ("search", "ranked", "latest")
GITHUB_SOURCE_STRATEGIES = {mode: f"github-{mode}" for mode in GITHUB_SOURCE_MODES}
_MAX_SEARCH_PAGE = 10  # 100 rows/page × 10 pages = GitHub's 1,000-result ceiling.
_TOKEN_REJECTED_STATE_KEY = "token_rejected"
_PARTIAL_EVIDENCE_CODES = frozenset({"incomplete_results", "rejected_rows", "search_capped"})


@dataclass(frozen=True)
class _ModeFetch:
    items: list[DiscoveredContent]
    partial: bool = False
    error_code: str = ""
    claims: tuple[Any, ...] = ()


class _PartialModeError(Exception):
    """Preserve repository rows accepted before a later request failed."""

    def __init__(
        self,
        error: GitHubAPIError,
        items: list[DiscoveredContent],
        *,
        claims: tuple[Any, ...] = (),
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.items = items
        self.claims = claims


@dataclass(frozen=True)
class _EnqueueOutcome:
    enqueued: int
    retained_counts: Counter[str]
    retained_keyword_ids: set[int]
    error: BaseException | None = None


@dataclass
class GitHubDiscoveryProducer:
    """Feed public GitHub repositories into the shared candidate pipeline."""

    database: Any
    soul_engine: Any
    client: Any
    enabled: bool = False
    # Presence/fingerprint input only. The value is never persisted or logged.
    access_token: str = ""
    source_modes: tuple[str, ...] = GITHUB_SOURCE_MODES
    daily_search_budget: int = 120
    daily_ranked_budget: int = 60
    daily_latest_budget: int = 60
    min_interval_minutes: int = 10
    latest_window_days: int = 30
    candidate_pipeline: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    keyword_fetch: Any | None = None
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Run search/ranked/latest with durable budgets and honest degradation."""

        if not self.enabled:
            return self._skip("disabled")
        self._ensure_tables()
        self._reconcile_token_rejection()
        cooldown_until = _read_cooldown_until(self.database)
        if cooldown_until is not None and cooldown_until > datetime.now(UTC):
            cooldown_payload = self._skip("rate_limited")
            cooldown_payload["cooldown_until"] = cooldown_until.isoformat()
            return cooldown_payload
        if not force and not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("github producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
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
                fetched = (
                    await self._run_search(profile, branch_limit)
                    if mode == "search"
                    else await self._run_browse(mode, branch_limit)
                )
            except _PartialModeError as partial:
                code = partial.error.code
                items = _dedupe_items(partial.items)
                all_items.extend(items)
                search_claims.update((int(item.id), item) for item in partial.claims)
                mode_results[mode] = code
                errors.append(code)
                run_records[mode] = (len(items), "partial", code)
                if self._handle_api_error(partial.error):
                    break
                continue
            except GitHubAPIError as exc:
                code = exc.code
                mode_results[mode] = code
                errors.append(code)
                self._record_run(
                    mode,
                    units=0,
                    discovered=0,
                    reason="error",
                    error_code=code,
                )
                if self._handle_api_error(exc):
                    break
                continue
            except Exception:
                logger.exception("github producer branch failed: mode=%s", mode)
                mode_results[mode] = "error"
                errors.append("error")
                self._record_run(
                    mode,
                    units=0,
                    discovered=0,
                    reason="error",
                    error_code="error",
                )
                continue

            items = _dedupe_items(fetched.items)
            all_items.extend(items)
            search_claims.update((int(item.id), item) for item in fetched.claims)
            if fetched.partial:
                code = fetched.error_code or "incomplete_results"
                mode_results[mode] = code
                errors.append(code)
                run_records[mode] = (len(items), "partial", code)
            else:
                reason = "ok" if items else "empty"
                mode_results[mode] = reason
                run_records[mode] = (len(items), reason, "")

        # A successful authenticated request proves the token has recovered.
        if (
            "unauthorized" not in errors
            and run_records
            and bool(getattr(self.client, "has_access_token", False))
        ):
            self._clear_token_rejection()

        items = _dedupe_items(all_items)[:requested_limit]
        source_counts = Counter(item.source_strategy for item in items)
        enqueue_outcome = self._enqueue_retained(items)
        self._finalize_search_claims(
            search_claims,
            enqueue_outcome.retained_keyword_ids,
        )

        if enqueue_outcome.error is not None:
            errors.append("candidate_enqueue_error")
            logger.warning(
                "github producer candidate handoff stopped after partial success: error_type=%s",
                type(enqueue_outcome.error).__name__,
            )
        for mode, (discovered, run_reason, error_code) in run_records.items():
            retained = enqueue_outcome.retained_counts.get(
                GITHUB_SOURCE_STRATEGIES[mode],
                0,
            )
            if enqueue_outcome.error is not None and retained < source_counts.get(
                GITHUB_SOURCE_STRATEGIES[mode],
                0,
            ):
                run_reason = "partial" if retained else "error"
                error_code = "candidate_enqueue_error"
                mode_results[mode] = error_code
            self._record_run(
                mode,
                units=retained,
                discovered=discovered,
                reason=run_reason,
                error_code=error_code,
            )

        reason = _overall_reason(items, errors, mode_results)
        payload: dict[str, object] = {
            "discovered": len(items),
            "source_counts": dict(source_counts),
            "mode_results": mode_results,
            "reason": reason,
            "degraded": bool(errors),
        }
        if self.candidate_pipeline is not None:
            payload["enqueued"] = enqueue_outcome.enqueued
            if (
                enqueue_outcome.enqueued > 0
                and enqueue_outcome.error is None
                and not self.candidate_evaluation_owned_by_coordinator
            ):
                payload.update(
                    await self.candidate_pipeline.drain_pending(
                        profile=profile,
                        batch_size=requested_limit,
                    )
                )
        return payload

    async def _run_search(self, profile: Any, limit: int) -> _ModeFetch:
        coordinator = self.keyword_fetch
        claimed: list[Any] = []
        if coordinator is not None and bool(getattr(coordinator, "should_claim", lambda: False)()):
            # Use the canonical long-form slug; no gh alias is persisted in the
            # shared keyword store.
            claimed = list(coordinator.claim("github", n=min(limit, 5)))

        queries: list[tuple[str, int | None, Any | None]]
        if claimed:
            queries = [(str(item.keyword).strip(), int(item.id), item) for item in claimed]
        else:
            queries = [
                (keyword, None, None)
                for keyword in _fallback_profile_keywords(profile, min(limit, 5))
            ]
        if not queries:
            return _ModeFetch([])

        items: list[DiscoveredContent] = []
        produced_claims: dict[int, Any] = {}
        partial_code = ""
        for index, (keyword, keyword_id, claimed_item) in enumerate(queries):
            if not keyword:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                continue
            if len(items) >= limit:
                _rollback_claims(coordinator, [item for _, _, item in queries[index:] if item])
                break
            query = _public_repository_query(keyword)
            if not query:
                # A planner row made only of private-scope qualifiers becomes
                # empty after the server-owned public filter is applied.  Do
                # not call the API with an invalid query and, critically, do
                # not leave the claimed keyword lease stuck in-flight.
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                continue
            cursor_key = f"search:{_query_fingerprint(query)}"
            page_number = self._cursor(cursor_key)
            try:
                page = await self._search_page(
                    query,
                    page=page_number,
                    # Keep cursor semantics stable across runs whose requested
                    # candidate limits differ.
                    per_page=100,
                    cursor_key=cursor_key,
                )
            except GitHubAPIError as exc:
                if claimed_item is not None and coordinator is not None:
                    coordinator.rollback(claimed_item)
                _rollback_claims(
                    coordinator,
                    [item for _, _, item in queries[index + 1 :] if item],
                )
                if items:
                    raise _PartialModeError(
                        exc,
                        items[:limit],
                        claims=tuple(produced_claims.values()),
                    ) from exc
                raise

            produced = _normalize_page_items(
                page,
                strategy=GITHUB_SOURCE_STRATEGIES["search"],
                source_keyword_id=keyword_id,
            )
            degradation = _page_degradation(page, accepted_count=len(produced))
            if degradation == "search_capped":
                # The 1,000-result ceiling is terminal for this result set.
                # Report partial honestly, then cycle to the head next run
                # instead of pinning this query to page 10 forever.
                self._set_cursor(cursor_key, 1)
            elif not degradation:
                self._advance_cursor(cursor_key, page)
            if degradation:
                partial_code = partial_code or degradation
            items.extend(produced)
            if claimed_item is not None and coordinator is not None:
                if produced:
                    produced_claims[int(claimed_item.id)] = claimed_item
                elif degradation:
                    coordinator.rollback(claimed_item)
                else:
                    coordinator.mark_failed([claimed_item])

        return _ModeFetch(
            _dedupe_items(items)[:limit],
            partial=bool(partial_code),
            error_code=partial_code,
            claims=tuple(produced_claims.values()),
        )

    async def _run_browse(self, mode: str, limit: int) -> _ModeFetch:
        if mode == "ranked":
            query = "stars:>=1 is:public fork:false"
            sort = "stars"
            cursor_key = "ranked"
        else:
            cutoff = (datetime.now(UTC) - timedelta(days=max(1, self.latest_window_days))).date()
            query = f"created:>={cutoff.isoformat()} is:public fork:false"
            sort = "updated"
            cursor_key = f"latest:{cutoff.isoformat()}"
        page_number = self._cursor(cursor_key)
        page = await self._search_page(
            query,
            sort=sort,
            page=page_number,
            per_page=100,
            cursor_key=cursor_key,
        )
        items = _normalize_page_items(
            page,
            strategy=GITHUB_SOURCE_STRATEGIES[mode],
        )
        degradation = _page_degradation(page, accepted_count=len(items))
        if degradation == "search_capped":
            self._set_cursor(cursor_key, 1)
        elif not degradation:
            self._advance_cursor(cursor_key, page)
        return _ModeFetch(
            _dedupe_items(items)[:limit],
            partial=bool(degradation),
            error_code=degradation,
        )

    async def _search_page(
        self,
        query: str,
        *,
        sort: str = "",
        page: int,
        per_page: int,
        cursor_key: str,
    ) -> Any:
        try:
            return await self.client.search_repositories(
                query,
                sort=sort,
                order="desc",
                page=page,
                per_page=per_page,
            )
        except GitHubAPIError as exc:
            if exc.code != "invalid_request" or page <= 1:
                raise
            # A moving search result set can invalidate an old page. Reset once
            # and retry; never loop and never advance on the failed response.
            logger.info(
                "github producer: resetting stale page cursor: key=%s page=%s",
                cursor_key,
                page,
            )
            self._set_cursor(cursor_key, 1)
            return await self.client.search_repositories(
                query,
                sort=sort,
                order="desc",
                page=1,
                per_page=per_page,
            )

    def _advance_cursor(self, key: str, page: Any) -> None:
        next_page = getattr(page, "next_page", None)
        try:
            next_value = int(next_page) if next_page is not None else 1
        except (TypeError, ValueError):
            next_value = 1
        if next_value < 1 or next_value > _MAX_SEARCH_PAGE:
            next_value = 1
        self._set_cursor(key, next_value)

    def _enqueue_retained(self, items: list[DiscoveredContent]) -> _EnqueueOutcome:
        if self.candidate_pipeline is None:
            return _EnqueueOutcome(
                len(items),
                Counter(item.source_strategy for item in items),
                {
                    keyword_id
                    for item in items
                    if (keyword_id := _source_keyword_id(item)) is not None
                },
            )

        accepted_total = 0
        accepted_counts: Counter[str] = Counter()
        accepted_keyword_ids: set[int] = set()
        for strategy in GITHUB_SOURCE_STRATEGIES.values():
            strategy_items = [item for item in items if item.source_strategy == strategy]
            if not strategy_items:
                continue
            if strategy == GITHUB_SOURCE_STRATEGIES["search"]:
                grouped_items: dict[int | None, list[DiscoveredContent]] = {}
                for item in strategy_items:
                    grouped_items.setdefault(_source_keyword_id(item), []).append(item)
                groups = list(grouped_items.items())
            else:
                groups = [(None, strategy_items)]
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
        coordinator = self.keyword_fetch
        if coordinator is None:
            return
        for keyword_id, claimed in claims.items():
            if keyword_id in retained_keyword_ids:
                coordinator.mark_used([claimed])
            else:
                # The query produced a row, but global dedupe/prefilter did not
                # retain it. Re-pend instead of inventing successful yield.
                coordinator.rollback(claimed)

    def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
        configured = {
            "search": self.daily_search_budget,
            "ranked": self.daily_ranked_budget,
            "latest": self.daily_latest_budget,
        }.get(mode, -1)
        if configured == 0:
            return max(0, int(per_run_budget))
        if configured < 0:
            return 0
        return max(0, int(configured) - self.consumed_today(mode))

    def consumed_today(self, mode: str) -> int:
        self._ensure_tables()
        row = self.database.conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM github_discovery_runs
            WHERE mode = ? AND reason IN ('ok', 'empty', 'partial')
              AND created_at >= datetime('now', 'start of day')
            """,
            (mode,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def _record_run(
        self,
        mode: str,
        *,
        units: int,
        discovered: int,
        reason: str,
        error_code: str,
    ) -> None:
        self.database.conn.execute(
            """
            INSERT INTO github_discovery_runs(mode, units, discovered, reason, error_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mode,
                max(0, int(units)),
                max(0, int(discovered)),
                str(reason or "error")[:32],
                str(error_code or "")[:80],
            ),
        )
        self.database.conn.commit()

    def _handle_api_error(self, error: GitHubAPIError) -> bool:
        if error.code == "rate_limited":
            self._set_cooldown(error.retry_after_seconds or 300)
            return True
        if error.code == "unauthorized":
            self._degrade_access_token()
        return False

    def _set_cooldown(self, seconds: int) -> None:
        persist_github_cooldown(self.database, seconds)

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        row = self.database.conn.execute(
            """
            SELECT 1 FROM github_discovery_runs
            WHERE created_at >= datetime('now', ?)
            LIMIT 1
            """,
            (f"-{int(self.min_interval_minutes)} minutes",),
        ).fetchone()
        return row is None

    def _cursor(self, key: str) -> int:
        row = self.database.conn.execute(
            "SELECT cursor FROM github_discovery_state WHERE state_key = ?",
            (key,),
        ).fetchone()
        value = int(row[0] if row is not None else 1)
        return value if 1 <= value <= _MAX_SEARCH_PAGE else 1

    def _set_cursor(self, key: str, cursor: int) -> None:
        bounded = min(_MAX_SEARCH_PAGE, max(1, int(cursor)))
        self.database.conn.execute(
            """
            INSERT INTO github_discovery_state(state_key, cursor, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cursor = excluded.cursor,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, bounded),
        )
        self.database.conn.commit()

    def _degrade_access_token(self) -> None:
        fingerprint = _token_fingerprint(self.access_token)
        self.access_token = ""
        disable = getattr(self.client, "disable_access_token", None)
        if not callable(disable):
            disable = getattr(self.client, "disable_token", None)
        if callable(disable):
            disable()
        if fingerprint:
            with suppress(Exception):
                _persist_token_rejection(self.database, fingerprint)
        logger.warning(
            "github producer: optional token rejected; falling back to anonymous "
            "public-repository discovery. Update [sources.github].access_token or "
            "OPENBILICLAW_GITHUB_TOKEN to re-enable authenticated rate limits."
        )

    def _reconcile_token_rejection(self) -> None:
        token = str(self.access_token or "").strip()
        if not token:
            return
        marker = _read_token_rejection(self.database)
        if marker is None or str(marker.get("fingerprint") or "") != _token_fingerprint(token):
            return
        disable = getattr(self.client, "disable_access_token", None)
        if not callable(disable):
            disable = getattr(self.client, "disable_token", None)
        if callable(disable):
            disable()

    def _clear_token_rejection(self) -> None:
        clear_github_token_rejection(self.database)

    def _candidate_pool_full(self) -> bool:
        return candidate_pool_full_for_source(
            self.candidate_pipeline,
            "github",
            logger=logger,
            label="github producer",
        )

    def _ensure_tables(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS github_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                error_code TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_github_runs_mode_created
                ON github_discovery_runs(mode, created_at);
            CREATE TABLE IF NOT EXISTS github_discovery_state (
                state_key TEXT PRIMARY KEY,
                cursor INTEGER NOT NULL DEFAULT 1,
                cooldown_until TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.database.conn.commit()

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("github producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def github_source_status(
    database: Any,
    *,
    enabled: bool,
    access_token: object = "",
    source_modes: object = GITHUB_SOURCE_MODES,
) -> dict[str, object]:
    """Return local-only GitHub discovery health without contacting GitHub."""

    token = str(access_token or "").strip()
    token_configured = bool(token)
    token_rejected = github_token_rejected(database, token)
    token_axis = {"token_state": "rejected" if token_rejected else "ok"} if token_configured else {}
    if not enabled:
        detail = "GitHub 来源未启用。"
        if token_configured:
            detail = "已保存 GitHub PAT，但来源未启用；启用并保存后才会使用。"
        return {
            "state": "disabled",
            "detail": detail,
            **token_axis,
        }
    cooldown = _read_cooldown_until(database)
    if cooldown is not None and cooldown > datetime.now(UTC):
        return {
            "state": "rate_limited",
            "detail": (
                "GitHub PAT 被拒绝，已降级为匿名公开仓库发现；匿名 API 正在退避冷却。"
                if token_rejected
                else "GitHub API 正在退避冷却，到期后自动重试。"
            ),
            "cooldown_until": cooldown.isoformat(),
            **token_axis,
        }
    enabled_modes = _normalize_modes(source_modes) or GITHUB_SOURCE_MODES
    try:
        placeholders = ",".join("?" for _ in enabled_modes)
        rows = database.conn.execute(
            f"""
            SELECT r.mode, r.reason, r.error_code
            FROM github_discovery_runs AS r
            JOIN (
                SELECT mode, MAX(id) AS id
                FROM github_discovery_runs
                WHERE mode IN ({placeholders})
                GROUP BY mode
            ) AS latest ON latest.id = r.id
            """,  # noqa: S608 - placeholders are generated from a fixed tuple length.
            enabled_modes,
        ).fetchall()
    except Exception:
        rows = []
    rows_by_mode = {str(row["mode"]): row for row in rows}
    missing_modes = [mode for mode in enabled_modes if mode not in rows_by_mode]
    if not rows:
        return {
            "state": "unverified",
            "detail": (
                "GitHub PAT 被拒绝，已降级为匿名公开仓库发现；尚未完成新的发现运行。"
                if token_rejected
                else "尚未运行 GitHub 仓库发现。"
            ),
            **token_axis,
        }
    successes = sum(1 for row in rows if str(row["reason"]) in {"ok", "empty"})
    partials = sum(1 for row in rows if str(row["reason"]) == "partial")
    failures = len(rows) - successes - partials
    if missing_modes or partials or (successes and failures):
        state = "partial"
        detail = (
            "GitHub 已启用的发现分支尚未全部完成验证。"
            if missing_modes
            else "GitHub 部分公开仓库发现不完整，已保留结果并将在退避后重试。"
        )
    elif successes:
        state = "ready"
        detail = "GitHub 使用官方 API 读取公开仓库；PAT 仅用于身份与提高限额。"
    else:
        state = "error"
        detail = "GitHub 公开仓库发现最近失败，将自动重试。"
    payload: dict[str, object] = {
        "state": state,
        "detail": detail,
        "modes": {
            mode: {
                "reason": (
                    str(rows_by_mode[mode]["reason"]) if mode in rows_by_mode else "unverified"
                ),
                "error_code": (
                    str(rows_by_mode[mode]["error_code"]) if mode in rows_by_mode else ""
                ),
            }
            for mode in enabled_modes
        },
    }
    if token_configured:
        payload.update(token_axis)
        if token_rejected:
            payload["detail"] = "GitHub PAT 被拒绝，已降级为匿名公开仓库发现；请更新 PAT。"
    return payload


def _normalize_modes(value: object) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple)) else ()
    selected = {str(item).strip().lower() for item in raw}
    return tuple(mode for mode in GITHUB_SOURCE_MODES if mode in selected)


def _public_repository_query(value: object) -> str:
    return github_public_repository_query(value)


def _normalize_page_items(
    page: Any,
    *,
    strategy: str,
    source_keyword_id: int | None = None,
) -> list[DiscoveredContent]:
    result: list[DiscoveredContent] = []
    for row in getattr(page, "items", []) or []:
        if not isinstance(row, dict) or bool(row.get("private", False)):
            continue
        content = github_repository_to_content(
            row,
            strategy=strategy,
            source_keyword_id=source_keyword_id,
        )
        if content is not None:
            result.append(content)
    return result


def github_token_rejected(database: Any, access_token: object = "") -> bool:
    """Return whether GitHub rejected the exact PAT currently in use.

    The marker is fingerprint-scoped, so rotating or clearing a credential
    immediately stops carrying the prior token's verdict into status and
    guided-init capability checks.  This helper is intentionally local-only.
    """

    token = str(access_token or "").strip()
    if not token:
        return False
    rejection = _read_token_rejection(database)
    return bool(
        rejection is not None
        and str(rejection.get("fingerprint") or "") == _token_fingerprint(token)
    )


def _page_degradation(page: Any, *, accepted_count: int) -> str:
    """Classify incomplete search evidence without treating it as empty."""

    if bool(getattr(page, "incomplete_results", False)):
        return "incomplete_results"
    if bool(getattr(page, "search_capped", False)) and getattr(page, "next_page", None) is None:
        return "search_capped"
    raw_items = getattr(page, "items", None)
    if isinstance(raw_items, list) and len(raw_items) > max(0, int(accepted_count)):
        # The official envelope claimed rows, but at least one failed the
        # public-only/schema normalizer. Keep any accepted siblings, do not
        # advance the cursor or declare an affirmative empty result, and leave
        # the keyword claim pending for a later healthy page.
        return "rejected_rows"
    return ""


def _allocate(limit: int, keys: tuple[str, ...]) -> dict[str, int]:
    if not keys:
        return {}
    total = max(0, int(limit))
    base, remainder = divmod(total, len(keys))
    return {key: base + (1 if index < remainder else 0) for index, key in enumerate(keys)}


def _dedupe_items(items: list[DiscoveredContent]) -> list[DiscoveredContent]:
    result: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        key = item.item_key or f"github:repository:{item.content_id or item.bvid}"
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


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


def _overall_reason(
    items: list[DiscoveredContent],
    errors: list[str],
    mode_results: dict[str, str],
) -> str:
    if errors:
        return (
            "partial"
            if items or all(error in _PARTIAL_EVIDENCE_CODES for error in errors)
            else "error"
        )
    if mode_results and all(value == "budget_exhausted" for value in mode_results.values()):
        return "budget_exhausted"
    return "ok" if items else "empty"


def _fallback_profile_keywords(profile: Any, limit: int) -> list[str]:
    preferences = (
        profile.get("preferences")
        if isinstance(profile, dict)
        else getattr(profile, "preferences", None)
    )
    interests = (
        preferences.get("interests", [])
        if isinstance(preferences, dict)
        else getattr(preferences, "interests", [])
    )
    result: list[str] = []
    seen: set[str] = set()
    for interest in interests or []:
        name = (
            str(interest.get("name") or "").strip()
            if isinstance(interest, dict)
            else str(getattr(interest, "name", "") or interest).strip()
        )
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        result.append(name)
        if len(result) >= max(1, int(limit)):
            break
    return result


def _query_fingerprint(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def _token_fingerprint(token: object) -> str:
    raw = str(token or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12] if raw else ""


def _persist_token_rejection(database: Any, fingerprint: str) -> None:
    note = json.dumps(
        {"fingerprint": fingerprint, "rejected_at": datetime.now(UTC).isoformat()},
        ensure_ascii=False,
        sort_keys=True,
    )
    database.conn.execute(
        """
        INSERT INTO github_discovery_state(state_key, note, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(state_key) DO UPDATE SET
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (_TOKEN_REJECTED_STATE_KEY, note),
    )
    database.conn.commit()


def clear_github_token_rejection(database: Any) -> None:
    """Forget a rejected-PAT marker after the credential is changed or cleared."""

    with suppress(Exception):
        database.conn.execute(
            "DELETE FROM github_discovery_state WHERE state_key = ?",
            (_TOKEN_REJECTED_STATE_KEY,),
        )
        database.conn.commit()


def persist_github_cooldown(database: Any, seconds: int) -> datetime:
    """Persist the source-wide API cooldown shared by every GitHub path."""

    delay = min(86_400, max(1, int(seconds)))
    until = datetime.now(UTC) + timedelta(seconds=delay)
    # Inspiration search can be the first GitHub path touched after startup,
    # before the formal producer has created its source-local tables.
    database.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS github_discovery_state (
            state_key TEXT PRIMARY KEY,
            cursor INTEGER NOT NULL DEFAULT 1,
            cooldown_until TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    database.conn.execute(
        """
        INSERT INTO github_discovery_state(state_key, cooldown_until, updated_at)
        VALUES ('global', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(state_key) DO UPDATE SET
            cooldown_until = excluded.cooldown_until,
            updated_at = CURRENT_TIMESTAMP
        """,
        (until.isoformat(),),
    )
    database.conn.commit()
    return until


def github_cooldown_remaining(database: Any) -> float:
    """Return seconds left in the shared source cooldown, without I/O."""

    cooldown = _read_cooldown_until(database)
    if cooldown is None:
        return 0.0
    return max(0.0, (cooldown - datetime.now(UTC)).total_seconds())


def _read_token_rejection(database: Any) -> dict[str, Any] | None:
    try:
        row = database.conn.execute(
            "SELECT note FROM github_discovery_state WHERE state_key = ?",
            (_TOKEN_REJECTED_STATE_KEY,),
        ).fetchone()
    except Exception:
        return None
    if row is None or not str(row[0] or "").strip():
        return None
    try:
        payload = json.loads(str(row[0]))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_cooldown_until(database: Any) -> datetime | None:
    try:
        row = database.conn.execute(
            "SELECT cooldown_until FROM github_discovery_state WHERE state_key = 'global'"
        ).fetchone()
    except Exception:
        return None
    if row is None or not str(row[0] or "").strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
