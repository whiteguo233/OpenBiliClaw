"""Runtime producer for V2EX's read-only public discovery paths."""

from __future__ import annotations

import html
import logging
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from openbiliclaw.runtime.keyword_fetch import PLATFORM_V2EX
from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.sources.v2ex import v2ex_topic_to_content
from openbiliclaw.sources.v2ex_client import V2EXAPIError, V2EXPage

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)

V2EX_SOURCE_MODES = ("search", "node", "tab", "hot", "latest")
V2EX_SOURCE_STRATEGIES = {mode: f"v2ex-{mode}" for mode in V2EX_SOURCE_MODES}
V2EX_SOURCE_WEIGHTS = {"search": 40, "node": 40, "tab": 10, "hot": 5, "latest": 5}


def build_v2ex_external_search_provider(config: object) -> Any | None:
    """Build the configured Exa/You chain used for ``site:v2ex.com/t`` recall."""

    discovery = getattr(config, "discovery", None)
    configured = tuple(getattr(discovery, "inspiration_search_backends", ()) or ())
    external = tuple(
        str(value).strip().lower()
        for value in configured
        if str(value).strip().lower() in {"bing_rss", "exa", "you", "serply"}
    )
    if not external:
        return None
    from openbiliclaw.discovery.inspiration_provider import build_inspiration_search_provider

    discovery = getattr(config, "discovery", None)
    return build_inspiration_search_provider(
        external,
        exa_api_key=str(getattr(discovery, "exa_api_key", "") or ""),
        you_api_key=str(getattr(discovery, "you_api_key", "") or ""),
        serply_api_key=str(getattr(discovery, "serply_api_key", "") or ""),
    )


@dataclass
class V2EXDiscoveryProducer:
    """Fetch V2EX topics and hand them to the shared candidate pipeline."""

    database: Any
    soul_engine: Any
    client: Any
    enabled: bool = False
    access_token: str = ""
    identity_username: str = ""
    source_modes: tuple[str, ...] = V2EX_SOURCE_MODES
    tab_modes: tuple[str, ...] = ("tech", "creative", "qna")
    node_allowlist: tuple[str, ...] = ()
    node_blocklist: tuple[str, ...] = ("sandbox",)
    node_downweight: tuple[str, ...] = ("promotions", "jobs", "deals")
    daily_search_budget: int = 120
    daily_node_budget: int = 180
    daily_tab_budget: int = 80
    daily_hot_budget: int = 40
    daily_latest_budget: int = 40
    min_interval_minutes: int = 5
    detail_fetch_limit: int = 15
    reply_enrichment_limit: int = 10
    max_topic_chars: int = 6000
    max_reply_digest_chars: int = 1200
    max_profile_nodes: int = 12
    candidate_pipeline: Any | None = None
    candidate_evaluation_owned_by_coordinator: bool = False
    keyword_fetch: Any | None = None
    search_provider: Any | None = None
    _last_skip_reason: str = field(default="", init=False)
    _detail_fetch_remaining: int = field(default=0, init=False)
    _reply_enrichment_remaining: int = field(default=0, init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Run the configured V2EX branches while respecting pool and budgets."""

        if not self.enabled:
            return self._skip("disabled")
        self._ensure_tables()
        cooldown_until = self._cooldown_until()
        if cooldown_until is not None and cooldown_until > datetime.now(UTC):
            return self._skip("rate_limited")
        if not force and not self._is_due():
            return self._skip("throttled")
        if candidate_pool_full_for_source(
            self.candidate_pipeline,
            "v2ex",
            logger=logger,
            label="v2ex producer",
        ):
            return self._skip("pool_full")
        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("v2ex producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        self._detail_fetch_remaining = max(0, int(self.detail_fetch_limit))
        self._reply_enrichment_remaining = max(0, int(self.reply_enrichment_limit))
        modes = tuple(mode for mode in V2EX_SOURCE_MODES if mode in self.source_modes)
        if not modes:
            return self._skip("mode_disabled")
        allocations = _allocate_weighted(requested_limit, modes, V2EX_SOURCE_WEIGHTS)
        all_items: list[DiscoveredContent] = []
        mode_results: dict[str, str] = {}
        errors: list[str] = []
        requests_by_mode: dict[str, int] = {}
        for mode in modes:
            if allocations[mode] <= 0:
                mode_results[mode] = "deferred_by_mix"
                continue
            branch_limit = min(
                allocations[mode], self.remaining_budget(mode, per_run_budget=allocations[mode])
            )
            if branch_limit <= 0:
                mode_results[mode] = "budget_exhausted"
                continue
            try:
                items, units = await self._run_mode(mode, profile, branch_limit)
            except V2EXAPIError as exc:
                mode_results[mode] = exc.code
                errors.append(exc.code)
                self._record_run(mode, units=0, discovered=0, reason="error", error_code=exc.code)
                if exc.code == "rate_limited":
                    self._set_cooldown(exc.retry_after_seconds or 300)
                    break
                if exc.code == "unauthorized":
                    self._degrade_access_token()
                continue
            except Exception:
                logger.exception("v2ex producer branch failed: mode=%s", mode)
                mode_results[mode] = "error"
                errors.append("error")
                self._record_run(mode, units=0, discovered=0, reason="error", error_code="error")
                continue
            unique = _dedupe_items(items)
            all_items.extend(unique)
            requests_by_mode[mode] = units
            mode_results[mode] = "ok" if unique else "empty"

        items = self._diversify_items(_dedupe_items(all_items), limit=requested_limit)
        source_counts = Counter(item.source_strategy for item in items)

        enqueued = 0
        retained_by_strategy: Counter[str] = Counter(source_counts)
        if self.candidate_pipeline is not None and items:
            retained_by_strategy.clear()
            for strategy in V2EX_SOURCE_STRATEGIES.values():
                grouped = [item for item in items if item.source_strategy == strategy]
                if grouped:
                    retained = min(
                        len(grouped),
                        max(
                            0,
                            int(
                                self.candidate_pipeline.enqueue_candidates(
                                    grouped,
                                    source_context=strategy,
                                )
                            ),
                        ),
                    )
                    enqueued += retained
                    retained_by_strategy[strategy] = retained

        # Daily source budgets count only candidates retained after global
        # canonical dedupe and the shared candidate-pool prefilter. HTTP calls,
        # detail enrichment, and known duplicates remain diagnostics only.
        for mode in requests_by_mode:
            strategy = V2EX_SOURCE_STRATEGIES[mode]
            self._record_run(
                mode,
                units=retained_by_strategy.get(strategy, 0),
                discovered=source_counts.get(strategy, 0),
                reason=mode_results.get(mode, "empty"),
                error_code="",
            )

        if not items and errors:
            reason = "error"
        elif errors:
            reason = "partial"
        elif mode_results and all(value == "budget_exhausted" for value in mode_results.values()):
            reason = "budget_exhausted"
        elif not items:
            reason = "empty"
        else:
            reason = "ok"
        payload: dict[str, object] = {
            "discovered": len(items),
            "source_counts": dict(source_counts),
            "mode_results": mode_results,
            "request_counts": requests_by_mode,
            "reason": reason,
            "rate_limit": dict(getattr(self.client, "last_rate_limit", {}) or {}),
        }
        if self.candidate_pipeline is not None:
            payload["enqueued"] = enqueued
            if enqueued and not self.candidate_evaluation_owned_by_coordinator:
                payload.update(
                    await self.candidate_pipeline.drain_pending(
                        profile=profile,
                        batch_size=requested_limit,
                    )
                )
        return payload

    async def _run_mode(
        self, mode: str, profile: Any, limit: int
    ) -> tuple[list[DiscoveredContent], int]:
        if mode == "search":
            return await self._run_search(profile, limit)
        if mode == "node":
            return await self._run_nodes(profile, limit)
        if mode == "tab":
            return await self._run_tabs(limit)
        if mode == "hot":
            return await self._run_page("hot", limit)
        if mode == "latest":
            return await self._run_page("latest", limit)
        raise ValueError(f"unsupported V2EX source mode: {mode}")

    async def _run_search(self, profile: Any, limit: int) -> tuple[list[DiscoveredContent], int]:
        claimed: list[Any] = []
        coordinator = self.keyword_fetch
        if coordinator is not None and bool(getattr(coordinator, "should_claim", lambda: False)()):
            claimed = list(coordinator.claim(PLATFORM_V2EX, n=min(limit, 5)))
        queries: list[tuple[str, int | None, Any | None]] = []
        if claimed:
            queries = [(str(item.keyword).strip(), int(item.id), item) for item in claimed]
        else:
            queries = [
                (keyword, None, None) for keyword in _fallback_profile_keywords(profile, limit)
            ]
        if not queries:
            return [], 0

        items: list[DiscoveredContent] = []
        requests = 0
        for index, (keyword, keyword_id, claimed_item) in enumerate(queries):
            if not keyword:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                continue
            try:
                row_limit = max(1, limit - len(items))
                rows: list[dict[str, Any]] = []
                if self.search_provider is not None:
                    try:
                        previews = await self.search_provider.search(
                            f"site:v2ex.com/t {keyword}",
                            limit=row_limit,
                        )
                        requests += 1
                        rows = _external_search_rows(previews)
                    except Exception:
                        logger.debug(
                            "v2ex external search provider failed; using official bounded fallback",
                            exc_info=True,
                        )
                if not rows:
                    page = await self.client.search_topics(keyword, limit=row_limit)
                    requests += 1
                    rows = _page_rows(page)
            except V2EXAPIError:
                if claimed_item is not None and coordinator is not None:
                    coordinator.mark_failed([claimed_item])
                    for _, _, pending in queries[index + 1 :]:
                        if pending is not None:
                            coordinator.rollback(pending)
                raise
            rows, enrichment_requests = await self._enrich_rows(rows)
            requests += enrichment_requests
            produced = self._normalize_rows(
                rows,
                strategy=V2EX_SOURCE_STRATEGIES["search"],
                source_keyword_id=keyword_id,
            )
            items.extend(produced)
            if claimed_item is not None and coordinator is not None:
                if produced:
                    coordinator.mark_used([claimed_item])
                else:
                    coordinator.mark_failed([claimed_item])
            if len(items) >= limit:
                for _, _, pending in queries[index + 1 :]:
                    if pending is not None and coordinator is not None:
                        coordinator.rollback(pending)
                break
        return items[:limit], requests

    async def _run_nodes(self, profile: Any, limit: int) -> tuple[list[DiscoveredContent], int]:
        nodes = self._configured_nodes(profile)
        if not nodes:
            return [], 0
        start = self._cursor("node_rotation") % len(nodes)
        ordered = nodes[start:] + nodes[:start]
        allocations = _allocate(limit, tuple(ordered))
        items: list[DiscoveredContent] = []
        requests = 0
        for node in ordered:
            branch_limit = allocations.get(node, 0)
            if branch_limit <= 0:
                continue
            page_number = max(1, self._cursor(f"node:{node}"))
            try:
                page = await self.client.get_node_topics(
                    node,
                    page=page_number,
                    limit=branch_limit,
                )
                requests += 1
            except V2EXAPIError as exc:
                if exc.code != "unauthorized":
                    raise
                self._degrade_access_token()
                page = await self.client.get_node_topics(
                    node,
                    page=page_number,
                    limit=branch_limit,
                )
                requests += 2
            rows, enrichment_requests = await self._enrich_rows(
                _page_rows(page),
                node_name=node,
            )
            requests += enrichment_requests
            items.extend(
                self._normalize_rows(
                    rows,
                    strategy=V2EX_SOURCE_STRATEGIES["node"],
                    node_name=node,
                )
            )
            total = _page_total(page)
            next_page = (
                page_number + 1
                if _page_rows(page) and page_number * max(1, branch_limit) < total
                else 1
            )
            self._set_cursor(f"node:{node}", next_page)
        self._set_cursor("node_rotation", (start + 1) % len(nodes))
        return _dedupe_items(items)[:limit], requests

    async def _run_tabs(self, limit: int) -> tuple[list[DiscoveredContent], int]:
        tabs = tuple(dict.fromkeys(tab for tab in self.tab_modes if tab))
        if not tabs:
            return [], 0
        start = self._cursor("tab_rotation") % len(tabs)
        ordered = tabs[start:] + tabs[:start]
        allocations = _allocate(limit, ordered)
        items: list[DiscoveredContent] = []
        requests = 0
        for tab in ordered:
            branch_limit = allocations.get(tab, 0)
            if branch_limit <= 0:
                continue
            page = await self.client.get_tab(tab, limit=branch_limit)
            requests += 1
            rows, enrichment_requests = await self._enrich_rows(_page_rows(page))
            requests += enrichment_requests
            items.extend(
                self._normalize_rows(
                    rows,
                    strategy=V2EX_SOURCE_STRATEGIES["tab"],
                )
            )
        self._set_cursor("tab_rotation", (start + 1) % len(tabs))
        return _dedupe_items(items)[:limit], requests

    async def _run_page(self, mode: str, limit: int) -> tuple[list[DiscoveredContent], int]:
        page = await getattr(self.client, f"get_{mode}")(limit=limit)
        rows, enrichment_requests = await self._enrich_rows(_page_rows(page))
        return (
            self._normalize_rows(rows, strategy=V2EX_SOURCE_STRATEGIES[mode])[:limit],
            1 + enrichment_requests,
        )

    async def _enrich_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        node_name: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Boundedly add canonical Topic details and one reply digest page."""

        enriched = [dict(row) for row in rows]
        requests = 0
        get_topic = getattr(self.client, "get_topic", None)
        if callable(get_topic):
            for index, row in enumerate(enriched):
                if self._detail_fetch_remaining <= 0:
                    break
                if not _topic_needs_detail(row, node_name=node_name):
                    continue
                topic_id = _row_topic_id(row)
                if not topic_id:
                    continue
                self._detail_fetch_remaining -= 1
                requests += 1
                try:
                    detail = await get_topic(topic_id)
                except V2EXAPIError as exc:
                    if exc.code == "rate_limited":
                        raise
                    if exc.code == "unauthorized":
                        self._degrade_access_token()
                        requests += 1
                        try:
                            detail = await get_topic(topic_id)
                        except V2EXAPIError as fallback_exc:
                            if fallback_exc.code == "rate_limited":
                                raise
                            logger.debug(
                                "v2ex detail enrichment fallback failed: topic=%s code=%s",
                                topic_id,
                                fallback_exc.code,
                            )
                            continue
                    else:
                        logger.debug(
                            "v2ex detail enrichment failed: topic=%s code=%s",
                            topic_id,
                            exc.code,
                        )
                        continue
                if isinstance(detail, Mapping):
                    enriched[index] = _merge_topic_detail(row, detail)

        get_replies = getattr(self.client, "get_topic_replies", None)
        if not callable(get_replies) or not bool(getattr(self.client, "has_access_token", False)):
            return enriched, requests
        for row in enriched:
            if self._reply_enrichment_remaining <= 0:
                break
            topic_id = _row_topic_id(row)
            if not topic_id or _safe_count(row.get("replies")) <= 0:
                continue
            self._reply_enrichment_remaining -= 1
            requests += 1
            try:
                reply_page = await get_replies(topic_id, page=1, limit=20)
            except V2EXAPIError as exc:
                if exc.code == "rate_limited":
                    raise
                if exc.code == "unauthorized":
                    self._degrade_access_token()
                    break
                logger.debug(
                    "v2ex reply enrichment failed: topic=%s code=%s",
                    topic_id,
                    exc.code,
                )
                continue
            digest = _reply_digest(
                _page_rows(reply_page),
                max_chars=self.max_reply_digest_chars,
            )
            if digest:
                row["discussion_digest"] = digest
        return enriched, requests

    def _normalize_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        strategy: str,
        source_keyword_id: int | None = None,
        node_name: str = "",
    ) -> list[DiscoveredContent]:
        blocked = {value.casefold() for value in self.node_blocklist}
        downweighted = {value.casefold() for value in self.node_downweight}
        result: list[DiscoveredContent] = []
        for row in rows:
            node_value = row.get("node")
            node: Mapping[str, Any] = node_value if isinstance(node_value, Mapping) else {}
            current_node = str(node.get("name") or node_name or "").strip()
            if current_node.casefold() in blocked:
                continue
            item = v2ex_topic_to_content(
                row,
                strategy=strategy,
                source_keyword_id=source_keyword_id,
                node_name=node_name,
                max_body_chars=self.max_topic_chars,
                max_reply_digest_chars=self.max_reply_digest_chars,
            )
            if item is not None:
                threshold = (
                    0.68 if bool(row.get("_search_summary_only")) or not item.body_text else 0.62
                )
                if current_node.casefold() in downweighted:
                    threshold = max(threshold, 0.72)
                item.score_threshold = max(float(item.score_threshold or 0.0), threshold)
                result.append(item)
        return result

    def _diversify_items(
        self,
        items: list[DiscoveredContent],
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        queues = {
            strategy: [item for item in items if item.source_strategy == strategy]
            for strategy in V2EX_SOURCE_STRATEGIES.values()
        }
        node_counts: Counter[str] = Counter()
        author_counts: Counter[str] = Counter()
        downweighted_count = 0
        downweighted = {value.casefold() for value in self.node_downweight}
        result: list[DiscoveredContent] = []
        while len(result) < max(0, int(limit)) and any(queues.values()):
            accepted_this_round = False
            for strategy in V2EX_SOURCE_STRATEGIES.values():
                queue = queues[strategy]
                while queue:
                    item = queue.pop(0)
                    node = str((item.tags or [""])[0] or "").strip().casefold()
                    author = str(item.author_name or "").strip().casefold()
                    is_downweighted = bool(node and node in downweighted)
                    if node and node_counts[node] >= 3:
                        continue
                    if author and author_counts[author] >= 2:
                        continue
                    if is_downweighted and downweighted_count >= 2:
                        continue
                    result.append(item)
                    if node:
                        node_counts[node] += 1
                    if author:
                        author_counts[author] += 1
                    if is_downweighted:
                        downweighted_count += 1
                    accepted_this_round = True
                    break
                if len(result) >= max(0, int(limit)):
                    break
            if not accepted_this_round:
                break
        return result

    def _configured_nodes(self, profile: Any) -> tuple[str, ...]:
        values = list(self.node_allowlist)
        if not values:
            preferences = profile.get("preferences", {}) if isinstance(profile, dict) else {}
            if isinstance(preferences, dict):
                values = list(preferences.get("v2ex_nodes", []) or [])
        if not values and hasattr(self.database, "conn"):
            try:
                from openbiliclaw.sources.v2ex_affinity import V2EXNodeAffinityStore

                values = V2EXNodeAffinityStore(self.database).top_nodes(
                    limit=max(1, int(self.max_profile_nodes)),
                    username=self._affinity_username(),
                )
            except Exception:
                logger.debug("v2ex node affinity unavailable", exc_info=True)
        blocked = {value.casefold() for value in self.node_blocklist}
        result: list[str] = []
        for value in values:
            node = str(value or "").strip().lower()
            if node and node not in result and node.casefold() not in blocked:
                result.append(node)
        return tuple(result[: max(1, int(self.max_profile_nodes))])

    def _affinity_username(self) -> str:
        """Use the committed profile owner, not a newly observed browser user."""

        get_active = getattr(self.database, "get_v2ex_profile_identity", None)
        if callable(get_active):
            try:
                active = str(get_active()[0] or "").strip()
            except Exception:
                logger.debug("v2ex active profile identity unavailable", exc_info=True)
            else:
                if active:
                    return active
        return str(self.identity_username or "").strip()

    def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
        configured = {
            "search": self.daily_search_budget,
            "node": self.daily_node_budget,
            "tab": self.daily_tab_budget,
            "hot": self.daily_hot_budget,
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
            FROM v2ex_discovery_runs
            WHERE mode = ? AND reason IN ('ok', 'empty', 'partial')
              AND created_at >= datetime('now', 'start of day')
            """,
            (mode,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def _ensure_tables(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS v2ex_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                error_code TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_v2ex_runs_mode_created
                ON v2ex_discovery_runs(mode, created_at);
            CREATE TABLE IF NOT EXISTS v2ex_discovery_state (
                state_key TEXT PRIMARY KEY,
                cursor INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.database.conn.commit()

    def _record_run(
        self, mode: str, *, units: int, discovered: int, reason: str, error_code: str
    ) -> None:
        self.database.conn.execute(
            """
            INSERT INTO v2ex_discovery_runs(mode, units, discovered, reason, error_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mode, max(0, units), max(0, discovered), reason, error_code[:80]),
        )
        self.database.conn.commit()

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        row = self.database.conn.execute(
            """
            SELECT 1 FROM v2ex_discovery_runs
            WHERE created_at >= datetime('now', ?)
            LIMIT 1
            """,
            (f"-{int(self.min_interval_minutes)} minutes",),
        ).fetchone()
        return row is None

    def _cursor(self, key: str) -> int:
        row = self.database.conn.execute(
            "SELECT cursor FROM v2ex_discovery_state WHERE state_key = ?", (key,)
        ).fetchone()
        try:
            return max(0, int(row[0])) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    def _set_cursor(self, key: str, value: int) -> None:
        self.database.conn.execute(
            """
            INSERT INTO v2ex_discovery_state(state_key, cursor, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cursor = excluded.cursor,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, max(0, int(value))),
        )
        self.database.conn.commit()

    def _cooldown_until(self) -> datetime | None:
        row = self.database.conn.execute(
            "SELECT cooldown_until FROM v2ex_discovery_state WHERE state_key = 'global'"
        ).fetchone()
        if row is None or not str(row[0] or "").strip():
            return None
        try:
            parsed = datetime.fromisoformat(str(row[0]))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _set_cooldown(self, seconds: int) -> None:
        until = datetime.now(UTC) + timedelta(seconds=min(86_400, max(1, seconds)))
        self.database.conn.execute(
            """
            INSERT INTO v2ex_discovery_state(state_key, cooldown_until, updated_at)
            VALUES ('global', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = CURRENT_TIMESTAMP
            """,
            (until.isoformat(),),
        )
        self.database.conn.commit()

    def _degrade_access_token(self) -> None:
        if not self.access_token and not bool(getattr(self.client, "has_access_token", False)):
            return
        rejected_token = self.access_token
        self.access_token = ""
        disable = getattr(self.client, "disable_access_token", None)
        if callable(disable):
            disable()
        if rejected_token:
            try:
                from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
                from openbiliclaw.api.source_auth.write import credential_fingerprint

                fingerprint = credential_fingerprint("v2ex", rejected_token)
                LIVE_PROBES.record(
                    "v2ex",
                    authenticated=False,
                    detail="V2EX rejected the configured PAT",
                    network_error=False,
                    fingerprint=fingerprint,
                )
                clear_identity = getattr(self.database, "clear_v2ex_pat_identity", None)
                if callable(clear_identity):
                    clear_identity(credential_fingerprint=fingerprint)
            except Exception:
                logger.debug("v2ex PAT rejection state could not be persisted", exc_info=True)
        logger.warning(
            "v2ex producer: access token rejected; falling back to anonymous public discovery"
        )

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("v2ex producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def _row_topic_id(row: Mapping[str, Any]) -> str:
    raw = str(row.get("id") or "").strip()
    if raw.isdigit():
        return raw
    parsed = urlparse(str(row.get("url") or raw))
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        hostname == "v2ex.com" or hostname.endswith(".v2ex.com")
    ):
        return ""
    match = re.search(r"/t/(\d+)", parsed.path)
    return match.group(1) if match else ""


def _mapping_text(value: object, key: str) -> str:
    return str(value.get(key) or "").strip() if isinstance(value, Mapping) else ""


def _topic_needs_detail(row: Mapping[str, Any], *, node_name: str) -> bool:
    body = next(
        (
            str(row.get(key) or "").strip()
            for key in ("content", "content_rendered", "content_text", "content_html")
            if str(row.get(key) or "").strip()
        ),
        "",
    )
    author = (
        _mapping_text(row.get("member"), "username") or str(row.get("author_name") or "").strip()
    )
    node = _mapping_text(row.get("node"), "name") or str(node_name or "").strip()
    return not body or not author or not node


def _merge_topic_detail(
    row: Mapping[str, Any],
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(row)
    search_summary_only = bool(merged.get("_search_summary_only"))
    for key in (
        "title",
        "url",
        "content",
        "content_rendered",
        "created",
        "replies",
        "deleted",
    ):
        if (search_summary_only or merged.get(key) in (None, "", 0, {}, [])) and detail.get(
            key
        ) not in (None, "", {}, []):
            merged[key] = detail[key]
    if (search_summary_only or not _mapping_text(merged.get("member"), "username")) and isinstance(
        detail.get("member"), Mapping
    ):
        merged["member"] = dict(detail["member"])
    if (search_summary_only or not _mapping_text(merged.get("node"), "name")) and isinstance(
        detail.get("node"), Mapping
    ):
        merged["node"] = dict(detail["node"])
    merged["_search_summary_only"] = False
    return merged


def _safe_count(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _plain_text(value: object, *, limit: int = 400) -> str:
    raw = html.unescape(str(value or ""))
    raw = re.sub(r"<\s*br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit]


def _reply_digest(rows: list[dict[str, Any]], *, max_chars: int) -> str:
    limit = max(100, min(10_000, int(max_chars)))
    excerpts: list[str] = []
    for row in rows:
        content = _plain_text(row.get("content") or row.get("content_rendered"), limit=300)
        if not content:
            continue
        author = _mapping_text(row.get("member"), "username")
        excerpt = f"{author}：{content}" if author else content
        if excerpt not in excerpts:
            excerpts.append(excerpt)
        if len(excerpts) >= 3:
            break
    if not excerpts:
        return ""
    return ("讨论摘要：" + "；".join(excerpts))[:limit]


def _allocate(limit: int, keys: tuple[str, ...]) -> dict[str, int]:
    if not keys:
        return {}
    base, remainder = divmod(max(0, int(limit)), len(keys))
    return {key: base + (1 if index < remainder else 0) for index, key in enumerate(keys)}


def _allocate_weighted(
    limit: int,
    keys: tuple[str, ...],
    weights: Mapping[str, int],
) -> dict[str, int]:
    """Largest-remainder allocation with one slot per mode when capacity permits."""

    requested = max(0, int(limit))
    if not keys:
        return {}
    normalized = {key: max(0, int(weights.get(key, 0))) for key in keys}
    total_weight = sum(normalized.values()) or len(keys)
    raw = {key: requested * (normalized[key] or 1) / total_weight for key in keys}
    result = {key: int(raw[key]) for key in keys}
    remaining = requested - sum(result.values())
    order = sorted(
        keys,
        key=lambda key: (raw[key] - result[key], normalized[key], -keys.index(key)),
        reverse=True,
    )
    for key in order[:remaining]:
        result[key] += 1
    if requested >= len(keys):
        for empty in (key for key in keys if result[key] == 0):
            donors = [key for key in keys if result[key] > 1]
            if not donors:
                break
            donor = max(donors, key=lambda key: (result[key] - raw[key], result[key]))
            result[donor] -= 1
            result[empty] = 1
    return result


def _external_search_rows(previews: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(previews, list):
        return rows
    seen: set[str] = set()
    for preview in previews:
        if isinstance(preview, Mapping):
            title = str(preview.get("title") or "").strip()
            url = str(preview.get("url") or "").strip()
            highlights = preview.get("highlights")
        else:
            title = str(getattr(preview, "title", "") or "").strip()
            url = str(getattr(preview, "url", "") or "").strip()
            highlights = getattr(preview, "highlights", ())
        topic_id = _row_topic_id({"url": url})
        if not topic_id or not title or topic_id in seen:
            continue
        seen.add(topic_id)
        highlight_values = highlights if isinstance(highlights, (list, tuple)) else ()
        rows.append(
            {
                "id": topic_id,
                "url": f"https://www.v2ex.com/t/{topic_id}",
                "title": title,
                "content": "\n".join(
                    str(value).strip() for value in highlight_values if str(value).strip()
                ),
                "_search_summary_only": True,
            }
        )
    return rows


def _page_rows(page: V2EXPage | object) -> list[dict[str, Any]]:
    rows = getattr(page, "data", page)
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _page_total(page: V2EXPage | object) -> int:
    try:
        return max(0, int(getattr(page, "total", 0)))
    except (TypeError, ValueError):
        return len(_page_rows(page))


def _dedupe_items(items: list[DiscoveredContent]) -> list[DiscoveredContent]:
    result: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        key = str(getattr(item, "item_key", "") or "")
        if not key:
            key = f"v2ex:{getattr(item, 'content_id', '') or getattr(item, 'bvid', '')}"
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _fallback_profile_keywords(profile: Any, limit: int) -> list[str]:
    preferences = profile.get("preferences", {}) if isinstance(profile, dict) else {}
    interests = preferences.get("interests", []) if isinstance(preferences, dict) else []
    result: list[str] = []
    seen: set[str] = set()
    for interest in interests or []:
        name = (
            str(interest.get("name") or "").strip()
            if isinstance(interest, dict)
            else str(getattr(interest, "name", "") or interest).strip()
        )
        if name and name not in seen:
            seen.add(name)
            result.append(name)
        if len(result) >= max(1, int(limit)):
            break
    return result
