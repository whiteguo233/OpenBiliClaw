"""Inspiration keyword pipeline — the ①–⑥ orchestration extracted from
``KeywordPlanner`` (Phase 2 Part D).

This class carries the search-inspired keyword flow: interest selection, axis
fetch, grounding-probe build, provider grounding, the single axis+keyword LLM
call, coverage-first materialization, and the axis upsert / yield-backfill tick.
It holds injected dependencies (db, llm, inspiration provider, discovery-config
+ breadth-params views, clock) plus a ``host`` back-reference for the handful of
planner infra helpers shared with the merged-keyword path (``_history`` /
``_insert`` / ``_avoid_hints`` / ``_supply_hints`` / ``_load_profile``).

Behaviour is byte-for-byte identical to the pre-extraction planner; the planner
keeps thin compatibility delegates that forward here.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.discovery.inspiration import (
    AllocationTarget,
    AxisRow,
    BrainstormBranch,
    GroundedProbe,
    InspirationSeed,
    MaterializeCandidate,
    ProfileAspect,
    SecondaryInterest,
    build_grounding_probes,
    build_like_secondary_interest_window,
    derive_inspiration_axis_id,
    derive_inspiration_seeds,
    materialize_platform_keywords,
)
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.llm.embedding import cosine_similarity
from openbiliclaw.llm.prompts import (
    build_inspiration_axis_keyword_prompt,
    platform_supply_advantage,
)
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.runtime.keyword_planner import (
    _AXIS_BACKFILL_MIN_INTERVAL_HOURS,
    _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS,
    _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS_CEIL,
    _INSPIRATION_AXIS_KEYWORD_SLOT_THRESHOLD,
    _INSPIRATION_MIN_AXES_PER_INTEREST,
    _INSPIRATION_PROVIDER_TIMEOUT_SECONDS,
    _PER_SLOT_TOKEN_BUDGET,
    _PLATFORM_QUERY_STYLES,
    _allocation_target_platforms,
    _as_str_list,
    _cap_inspiration_axis_keyword_inputs,
    _ledger_int,
    _parse_inspiration_axis_keyword_payload,
)
from openbiliclaw.storage.database import _parse_axis_datetime

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.config import DiscoveryConfig, InspirationBreadthParams
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

# Spec Part E: a new axis whose label embeds cosine >= this against an active
# same-interest axis is folded into it (evidence union, no new row). Embedding
# resolution happens in this async layer; the sync upsert DAO stays zero-I/O.
_AXIS_EMBEDDING_MERGE_THRESHOLD = 0.92
_AXIS_EMBEDDING_TIMEOUT_SECONDS = 4.0

# Substrings that identify a max_tokens / context-length rejection surfaced by
# the provider as a generic API error (there is no dedicated exception type —
# openai_compatible passes max_tokens verbatim and the gateway returns a 400).
# Only these errors trigger the bounded floor-retry (F2, Codex R1 S2); every
# other exception falls straight through to the deterministic fallback.
_MAX_TOKENS_ERROR_MARKERS: tuple[str, ...] = (
    "max_tokens",
    "max tokens",
    "maximum tokens",
    "max_completion_tokens",
    "maximum context length",
    "context length",
    "context_length_exceeded",
    "too many tokens",
    "reduce the length",
    "exceeds the maximum",
)


def _is_max_tokens_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _MAX_TOKENS_ERROR_MARKERS)


class InspirationKeywordPipeline:
    """Search-inspired keyword orchestration (moved out of ``KeywordPlanner``)."""

    def __init__(
        self,
        *,
        host: Any,
        db: Any,
        llm_service: Any,
        inspiration_provider: Any,
        discovery: Callable[[], DiscoveryConfig],
        inspiration_params: Callable[[], InspirationBreadthParams],
        clock: Callable[[], datetime],
        embedding_service: Any | None = None,
    ) -> None:
        self._host = host
        self._db = db
        self._llm = llm_service
        self._inspiration_provider = inspiration_provider
        self._discovery_getter = discovery
        self._inspiration_params_getter = inspiration_params
        self._clock = clock
        # Optional embedding helper for near-duplicate axis merge (Spec Part E).
        # ``None`` (or any failure) degrades to Phase-1 string-normalized dedup.
        self._embedding_service = embedding_service
        # Latest axis backfill+lifecycle tick summary (production stages only).
        self.last_axis_backfill: dict[str, object] = {
            "ran": False,
            "staled": 0,
            "retired": 0,
            "purged": 0,
        }

    @property
    def _discovery(self) -> DiscoveryConfig:
        return self._discovery_getter()

    @property
    def _inspiration_params(self) -> InspirationBreadthParams:
        return self._inspiration_params_getter()

    @staticmethod
    def _match_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    async def plan_inspiration_axis_keywords(
        self,
        *,
        profile_digest: object,
        platform_guides: object,
        selected_interests: Sequence[object],
        existing_axes: Sequence[object],
        fresh_evidence: Sequence[object],
        allocation_targets: Mapping[str, object],
        explore_request: Mapping[str, object] | None = None,
    ) -> tuple[list[AxisRow], list[MaterializeCandidate], dict[str, object]]:
        """Run the standalone axis-plus-keyword inspiration LLM call.

        ``explore_request`` (Phase 2.3, E2) is an optional per-call block: when
        provided it is threaded into the (static-system) prompt's user message to
        flag a cross-domain exploration round. ``None`` (the default) leaves the
        prompt byte-identical to the regular path.
        """

        (
            capped_guides,
            capped_interests,
            capped_axes,
            capped_evidence,
            cap_telemetry,
        ) = _cap_inspiration_axis_keyword_inputs(
            platform_guides=platform_guides,
            selected_interests=selected_interests,
            existing_axes=existing_axes,
            fresh_evidence=fresh_evidence,
            allocation_targets=allocation_targets,
        )
        telemetry: dict[str, object] = {
            **cap_telemetry,
            "llm_call_failed": False,
            "parse_salvaged": False,
            "parse_dropped_count": 0,
        }
        messages = build_inspiration_axis_keyword_prompt(
            profile_digest=profile_digest,
            platform_guides=capped_guides,
            selected_interests=capped_interests,
            existing_axes=capped_axes,
            fresh_evidence=capped_evidence,
            allocation_targets=allocation_targets,
            explore_request=explore_request,
        )
        # F2: scale max_tokens with the slot count so high-platform runs (longer
        # core_concepts) don't clip the output. slots use the pre-cap counts the
        # formula names; the value is recorded on telemetry.
        target_platforms = _allocation_target_platforms(allocation_targets)
        slots = len(list(selected_interests)) * len(target_platforms)
        requested_max_tokens = self._axis_keyword_max_tokens(slots)
        telemetry["max_tokens_requested"] = requested_max_tokens
        telemetry["max_tokens_retry"] = False
        complete_structured = self._llm.complete_structured_task
        try:
            response = await self._invoke_axis_keyword_call(
                complete_structured, messages, requested_max_tokens
            )
        except Exception as exc:
            # provider protection (Codex R1 S2): a max_tokens rejection at a
            # scaled request gets ONE bounded retry at the 8192 floor — error
            # recovery, NOT a salvage-repair loop. Only max_tokens errors, and
            # only when we actually asked for more than the floor.
            if requested_max_tokens > _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS and _is_max_tokens_error(
                exc
            ):
                telemetry["max_tokens_retry"] = True
                telemetry["max_tokens_requested"] = _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS
                try:
                    response = await self._invoke_axis_keyword_call(
                        complete_structured, messages, _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS
                    )
                except Exception:
                    logger.debug(
                        "keyword inspiration axis-keyword retry at floor failed", exc_info=True
                    )
                    telemetry["llm_call_failed"] = True
                    return [], [], telemetry
            else:
                logger.debug("keyword inspiration axis-keyword LLM call failed", exc_info=True)
                telemetry["llm_call_failed"] = True
                return [], [], telemetry

        axes, candidates, parse_telemetry = _parse_inspiration_axis_keyword_payload(
            str(getattr(response, "content", "") or ""),
            selected_interests=capped_interests,
            platforms=list(_allocation_target_platforms(allocation_targets)),
        )
        telemetry.update(parse_telemetry)
        if not axes and not candidates:
            telemetry["llm_call_failed"] = True
        return axes, candidates, telemetry

    @staticmethod
    def _axis_keyword_max_tokens(slots: int) -> int:
        """Scale max_tokens with slot count (F2): floor below the threshold."""
        over = max(0, int(slots) - _INSPIRATION_AXIS_KEYWORD_SLOT_THRESHOLD)
        return min(
            _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS_CEIL,
            _INSPIRATION_AXIS_KEYWORD_MAX_TOKENS + over * _PER_SLOT_TOKEN_BUDGET,
        )

    async def _invoke_axis_keyword_call(
        self,
        complete_structured: Any,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> Any:
        return await complete_structured(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            caller="discovery.keyword_inspiration",
            reasoning_effort=None,
            max_tokens=max_tokens,
            **without_core_memory_kwargs(complete_structured),
        )

    @staticmethod
    def _inspiration_allocation_targets(
        selected_interests: list[SecondaryInterest],
        platforms: list[str],
    ) -> dict[str, AllocationTarget]:
        return {
            item.label: AllocationTarget(
                platforms=tuple(platforms),
                min_axes=_INSPIRATION_MIN_AXES_PER_INTEREST,
            )
            for item in selected_interests
            if item.label
        }

    @staticmethod
    def _prompt_allocation_targets(
        allocation: Mapping[str, AllocationTarget],
    ) -> dict[str, dict[str, object]]:
        return {
            interest: {"platforms": list(target.platforms), "min_axes": target.min_axes}
            for interest, target in allocation.items()
        }

    @staticmethod
    def _fresh_evidence_from_grounding(
        grounding_records: list[GroundedProbe],
    ) -> list[dict[str, object]]:
        evidence: list[dict[str, object]] = []
        for record in grounding_records:
            max_len = max(len(record.evidence_titles), len(record.evidence_urls), 1)
            for index in range(max_len):
                title = record.evidence_titles[index] if index < len(record.evidence_titles) else ""
                url = record.evidence_urls[index] if index < len(record.evidence_urls) else ""
                if not title and not url and index > 0:
                    continue
                evidence.append(
                    {
                        "interest": record.secondary_interest,
                        "probe_query": record.probe_query,
                        "title": title,
                        "url": url,
                        "source_terms": list(record.source_terms),
                        "source": record.grounding_source or "provider",
                        "lens_family": record.lens_family,
                    }
                )
        return evidence

    @staticmethod
    def _deterministic_fallback_materialize_candidates(
        *,
        selected_interests: list[SecondaryInterest],
        existing_axes: list[AxisRow],
        platforms: list[str],
    ) -> list[MaterializeCandidate]:
        axes_by_interest: dict[str, list[AxisRow]] = {}
        for axis in existing_axes:
            interest_key = InspirationKeywordPipeline._match_text(axis.interest_label)
            if interest_key:
                axes_by_interest.setdefault(interest_key, []).append(axis)

        candidates: list[MaterializeCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for interest in selected_interests:
            label = str(interest.label or "").strip()
            if not label:
                continue
            axes = axes_by_interest.get(InspirationKeywordPipeline._match_text(label), [])
            if axes:
                for axis in axes:
                    axis_label = str(axis.axis_label or "").strip()
                    terms = tuple(axis.example_terms) or ((axis_label,) if axis_label else ())
                    for term in terms:
                        core = f"{label} {str(term).strip()}".strip()
                        for platform in platforms:
                            seen_key = (label, axis_label, platform)
                            if core and seen_key not in seen:
                                seen.add(seen_key)
                                candidates.append(
                                    MaterializeCandidate(
                                        interest=label,
                                        axis_label=axis_label or "existing_axis",
                                        platform=platform,
                                        core_concept=core,
                                        decoration="",
                                        recency_sensitivity="low",
                                        origin="deterministic_fill",
                                        axis_id=axis.axis_id,
                                    )
                                )
                continue
            for platform in platforms:
                seen_key = (label, "interest_only", platform)
                if seen_key in seen:
                    continue
                seen.add(seen_key)
                candidates.append(
                    MaterializeCandidate(
                        interest=label,
                        axis_label="interest_only",
                        platform=platform,
                        core_concept=label,
                        decoration="",
                        recency_sensitivity="low",
                        origin="deterministic_fill",
                    )
                )
        return candidates

    @staticmethod
    def _storage_platform_from_materialized(platform: object) -> str:
        value = str(platform or "").strip()
        return "twitter" if value == "x" else value

    @staticmethod
    def _build_axis_id_index(
        axes: Sequence[AxisRow],
    ) -> tuple[set[str], dict[tuple[str, str], str]]:
        """Return (known axis ids, (interest, label) → axis id) for attribution."""

        known_ids: set[str] = set()
        by_interest_label: dict[tuple[str, str], str] = {}
        for axis in axes:
            if not axis.axis_id:
                continue
            known_ids.add(axis.axis_id)
            key = (
                InspirationKeywordPipeline._match_text(axis.interest_label),
                InspirationKeywordPipeline._match_text(axis.axis_label),
            )
            by_interest_label.setdefault(key, axis.axis_id)
        return known_ids, by_interest_label

    @staticmethod
    def _resolve_realized_axis_id(
        *,
        raw_axis_id: str,
        source_interest: str,
        axis_label: str,
        axis_id_index: tuple[set[str], dict[tuple[str, str], str]],
    ) -> str:
        known_ids, by_interest_label = axis_id_index
        if raw_axis_id:
            return raw_axis_id
        # An LLM ``axis_id_or_label`` that is itself a real axis id → use verbatim.
        if axis_label and axis_label in known_ids:
            return axis_label
        mapped = by_interest_label.get(
            (
                InspirationKeywordPipeline._match_text(source_interest),
                InspirationKeywordPipeline._match_text(axis_label),
            )
        )
        if mapped:
            return mapped
        if axis_label:
            return derive_inspiration_axis_id(source_interest, axis_label)
        return ""

    @staticmethod
    def _axes_for_upsert(axes: Sequence[AxisRow], *, bump_usage: bool) -> list[AxisRow]:
        now = datetime.now(UTC).isoformat()
        result: list[AxisRow] = []
        seen: set[str] = set()
        for axis in axes:
            if not axis.axis_id or axis.axis_id in seen:
                continue
            seen.add(axis.axis_id)
            result.append(
                AxisRow(
                    interest_label=axis.interest_label,
                    axis_label=axis.axis_label,
                    axis_kind=axis.axis_kind,
                    source=axis.source,
                    axis_id=axis.axis_id,
                    interest_id=axis.interest_id,
                    example_terms=axis.example_terms,
                    evidence_refs=axis.evidence_refs,
                    time_sensitive=axis.time_sensitive,
                    freshness_ttl_days=axis.freshness_ttl_days,
                    yield_score=axis.yield_score,
                    admissions=axis.admissions,
                    use_count=axis.use_count,
                    status=axis.status,
                    created_at=axis.created_at or now,
                    last_used_at=now if bump_usage else axis.last_used_at,
                    last_refreshed_at=now,
                )
            )
        return result

    def _upsert_inspiration_axes(
        self,
        axes: Sequence[AxisRow],
        *,
        bump_usage: bool,
    ) -> None:
        upserter = getattr(self._db, "upsert_inspiration_axes", None)
        if not callable(upserter) or not axes:
            return
        try:
            upserter(self._axes_for_upsert(axes, bump_usage=bump_usage), bump_usage=bump_usage)
        except Exception:
            logger.debug("keyword inspiration axis upsert failed", exc_info=True)

    async def _resolve_axis_merges(
        self,
        new_axes: list[AxisRow],
        existing_axes: list[AxisRow],
    ) -> tuple[list[AxisRow], bool]:
        """Fold near-duplicate new axes into an active same-interest axis.

        Spec Part E: a new axis whose label is cosine ``>= 0.92`` to an active
        same-interest library axis is rewritten to carry that axis's id (with an
        evidence/example-term union) so the synchronous upsert UPDATEs the
        existing row instead of inserting a near-twin — no new row, one saved
        cap slot. All embedding I/O happens here; the DAO stays synchronous.

        Degradation contract (Spec AC9): no service, an unavailable ``embed``,
        an empty vector, a timeout, or any exception falls back silently to the
        Phase-1 string-normalized behaviour (axes pass through unchanged) and
        returns ``degraded=True`` when a present service actually failed. The
        stage is never blocked.
        """

        service = self._embedding_service
        if service is None or not new_axes or not existing_axes:
            return new_axes, False
        embed = getattr(service, "embed", None)
        if not callable(embed):
            return new_axes, False

        existing_by_interest: dict[str, list[AxisRow]] = {}
        for axis in existing_axes:
            if str(getattr(axis, "status", "active")) != "active":
                continue
            existing_by_interest.setdefault(self._match_text(axis.interest_label), []).append(axis)

        vectors: dict[str, list[float]] = {}

        async def _vector(text: str) -> list[float]:
            if text not in vectors:
                vectors[text] = list(
                    await asyncio.wait_for(embed(text), timeout=_AXIS_EMBEDDING_TIMEOUT_SECONDS)
                )
            return vectors[text]

        try:
            resolved: list[AxisRow] = []
            for axis in new_axes:
                candidates = existing_by_interest.get(self._match_text(axis.interest_label), [])
                if not candidates or not axis.axis_label:
                    resolved.append(axis)
                    continue
                new_vec = await _vector(axis.axis_label)
                if not new_vec:
                    resolved.append(axis)
                    continue
                best: AxisRow | None = None
                best_sim = _AXIS_EMBEDDING_MERGE_THRESHOLD
                for candidate in candidates:
                    if candidate.axis_id == axis.axis_id or not candidate.axis_label:
                        continue
                    candidate_vec = await _vector(candidate.axis_label)
                    if not candidate_vec:
                        continue
                    similarity = cosine_similarity(new_vec, candidate_vec)
                    if similarity >= best_sim:
                        best_sim = similarity
                        best = candidate
                resolved.append(self._merge_axis_into(axis, best) if best is not None else axis)
            return resolved, False
        except Exception:
            # Timeout / provider error / bad vector → Phase-1 string behaviour.
            logger.debug("axis embedding merge degraded to string dedup", exc_info=True)
            return new_axes, True

    @staticmethod
    def _merge_axis_into(new_axis: AxisRow, existing: AxisRow) -> AxisRow:
        """Return ``new_axis`` folded onto ``existing``'s identity + evidence.

        Carries the existing axis id and identity fields (so the upsert UPDATEs
        that row, never inserting) while unioning example terms / evidence refs.
        Cross-source rule (E5): ``source`` comes from ``existing`` — an
        explore-source axis merging onto a regular axis keeps ``source`` regular;
        only a genuinely new cross-domain axis stays ``source='explore'``.
        """

        return AxisRow(
            interest_label=existing.interest_label,
            axis_label=existing.axis_label,
            axis_kind=existing.axis_kind,
            source=existing.source,
            axis_id=existing.axis_id,
            interest_id=existing.interest_id,
            example_terms=(*existing.example_terms, *new_axis.example_terms),
            evidence_refs=(*existing.evidence_refs, *new_axis.evidence_refs),
            time_sensitive=existing.time_sensitive,
            freshness_ttl_days=existing.freshness_ttl_days,
            yield_score=existing.yield_score,
            admissions=existing.admissions,
            use_count=existing.use_count,
            status=existing.status,
            created_at=existing.created_at,
            last_used_at=existing.last_used_at,
            last_refreshed_at=existing.last_refreshed_at,
        )

    def _axis_backfill_last_run(self) -> datetime | None:
        """Return the parsed table-wide MAX(yield_backfilled_at), if any.

        Parsed Python-side (not SQL MAX) because the column holds mixed ISO
        shapes (``Z`` vs ``+00:00``) where string ordering is unreliable —
        same rationale as the lifecycle purge comparison.
        """

        conn = getattr(self._db, "conn", None)
        if conn is None:
            return None
        try:
            rows = conn.execute(
                "SELECT yield_backfilled_at FROM discovery_inspiration_axis "
                "WHERE yield_backfilled_at IS NOT NULL"
            ).fetchall()
        except Exception:
            logger.debug("keyword inspiration axis backfill timestamp lookup failed", exc_info=True)
            return None
        parsed = [_parse_axis_datetime(row["yield_backfilled_at"]) for row in rows]
        stamps = [stamp for stamp in parsed if stamp is not None]
        return max(stamps) if stamps else None

    def _run_axis_backfill_tick(self) -> dict[str, object]:
        """Run the yield backfill + lifecycle pass, throttled to once per 6h.

        Called at the start of PRODUCTION inspiration stages only (before the
        axis fetch, so this round's selection sees fresh scores). Pure SQL —
        zero LLM calls. Preview never reaches this method.
        """

        telemetry: dict[str, object] = {
            "ran": False,
            "staled": 0,
            "retired": 0,
            "purged": 0,
        }
        backfill = getattr(self._db, "backfill_inspiration_axis_yield", None)
        lifecycle = getattr(self._db, "apply_inspiration_axis_lifecycle", None)
        if not callable(backfill) or not callable(lifecycle):
            return telemetry
        now = self._clock()
        last_run = self._axis_backfill_last_run()
        if last_run is not None and (now - last_run) < timedelta(
            hours=_AXIS_BACKFILL_MIN_INTERVAL_HOURS
        ):
            self.last_axis_backfill = telemetry
            return telemetry
        try:
            backfill(now=now)
            summary = lifecycle(now=now)
        except Exception:
            logger.debug("keyword inspiration axis backfill tick failed", exc_info=True)
            return telemetry
        telemetry["ran"] = True
        if isinstance(summary, Mapping):
            for key in ("staled", "retired", "purged"):
                telemetry[key] = int(summary.get(key, 0) or 0)
        self.last_axis_backfill = telemetry
        return telemetry

    @staticmethod
    def _empty_inspiration_axis_report(
        platforms: Sequence[str],
        *,
        query_kind: str,
        inserted: bool,
    ) -> dict[str, object]:
        return {
            "inserted": inserted,
            "query_kind": query_kind,
            "platforms": list(platforms),
            "selected_secondary_interests": [],
            "realized_secondary_interests": [],
            "brainstorm_branches": [],
            "grounding_records": [],
            "grounding_ledger": {},
            "platform_keywords": {platform: [] for platform in platforms},
            "rejected_reasons": {platform: [] for platform in platforms},
            "repair_applied": {platform: False for platform in platforms},
            "materialize_telemetry": {},
            "llm_telemetry": {},
            "axis_coverage": {},
            "soft_score_summary": {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0},
            "deterministic_fill": 0,
            "coverage_shortfall": [],
            "parse_salvaged": False,
            "llm_call_failed": False,
            "axis_backfill": {"ran": False, "staled": 0, "retired": 0, "purged": 0},
            "axis_embedding_degraded": False,
        }

    async def _run_inspiration_axis_pipeline(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        query_kind: str,
        persist_keywords: bool,
        persist_grounding: bool,
        persist_axes: bool,
        bump_axis_usage: bool,
        selection_scope: str,
        keyword_kind_by_platform: Mapping[str, str] | None = None,
        seed_interests: Sequence[SecondaryInterest] | None = None,
        axis_source: Sequence[AxisRow] | None = None,
        explore_request: Mapping[str, object] | None = None,
        allow_deterministic_llm_fallback: bool = True,
        allowed_interest_labels: Sequence[str] | None = None,
        new_axis_source: str | None = None,
    ) -> tuple[dict[str, int], dict[str, object]]:
        # E0 parameterization (Phase 2.3): the keyword-only params below let an
        # upper layer (the explore stage) reuse this skeleton without forking or
        # polluting the regular path. Every default reproduces the pre-refactor
        # behaviour BYTE-FOR-BYTE — ``seed_interests=None`` falls back to
        # ``_selected_inspiration_interests`` (like interests), ``axis_source=None``
        # to the interest-listed axes, ``explore_request=None`` adds no prompt
        # extras, ``allow_deterministic_llm_fallback=True`` keeps the Phase-1
        # two-level deterministic fill on LLM failure, ``allowed_interest_labels
        # =None`` clamps nothing, and ``new_axis_source=None`` keeps the axes'
        # LLM-assigned source.
        report = self._empty_inspiration_axis_report(
            platforms,
            query_kind=query_kind,
            inserted=persist_keywords,
        )
        provider = self._inspiration_provider
        if not platforms or provider is None:
            return {}, report
        if not bool(getattr(self._discovery, "inspiration_search_enabled", False)):
            return {}, report

        # Learning tick (backfill + lifecycle) runs BEFORE the axis fetch so
        # this round's selection sees fresh scores — production only; preview
        # must never mutate the observed system (same principle as
        # ``bump_axis_usage=False``).
        if selection_scope == "production":
            report["axis_backfill"] = self._run_axis_backfill_tick()

        if seed_interests is None:
            coverage_snapshot = self._keyword_interest_coverage_snapshot(
                selection_scope=selection_scope
            )
            selected_interests = self._selected_inspiration_interests(profile, coverage_snapshot)
        else:
            selected_interests = list(seed_interests)
        report["selected_secondary_interests"] = [
            {
                "interest_id": item.interest_id,
                "label": item.label,
                "parent": item.parent,
                "weight": item.weight,
                "source": item.source,
                "coverage_score": item.coverage_score,
                "low_specificity": item.low_specificity,
            }
            for item in selected_interests
        ]
        if not selected_interests:
            return {}, report

        if axis_source is None:
            existing_axes = self._listed_inspiration_axes(selected_interests)
        else:
            existing_axes = list(axis_source)
        branches = build_grounding_probes(
            selected_interests,
            existing_axes,
            self._pooled_grounding_terms(platforms),
            limit=max(1, self._inspiration_params.max_probe_searches_per_stage),
        )
        report["brainstorm_branches"] = [
            {
                "secondary_interest": branch.secondary_interest,
                "branch_id": branch.branch_id,
                "branch_label": branch.branch_label,
                "lens_family": branch.lens_family,
                "kind_fit": branch.kind_fit,
                "probe_queries": list(branch.probe_queries),
                "expected_platform_fit": list(branch.expected_platform_fit),
                "avoid": list(branch.avoid),
                "why_it_might_work": branch.why_it_might_work,
            }
            for branch in branches
        ]
        aspects = self._aspects_from_secondary_interests(selected_interests)
        seed_records, grounding_records, grounding_ledger = await self._ground_inspiration_branches(
            provider=provider,
            platforms=platforms,
            digest=digest,
            aspects=aspects,
            branches=branches,
            query_kind=query_kind,
            persist=persist_grounding,
        )
        del seed_records
        report["grounding_ledger"] = grounding_ledger
        report["grounding_records"] = [
            {
                "secondary_interest": record.secondary_interest,
                "branch_id": record.branch_id,
                "probe_query": record.probe_query,
                "source_terms": list(record.source_terms),
                "evidence_titles": list(record.evidence_titles),
                "evidence_urls": list(record.evidence_urls),
                "lens_family": record.lens_family,
                "evidence_quality": record.evidence_quality,
                "grounding_source": record.grounding_source,
            }
            for record in grounding_records
        ]

        allocation = self._inspiration_allocation_targets(selected_interests, platforms)
        recent_by_platform = {
            platform: self._host._history(platform)[:30] for platform in platforms
        }
        platform_guides = self._inspiration_platform_guides(
            platforms,
            profile=profile,
            recent_by_platform=recent_by_platform,
        )
        new_axes, candidates, llm_telemetry = await self.plan_inspiration_axis_keywords(
            profile_digest=digest,
            platform_guides=platform_guides,
            selected_interests=selected_interests,
            existing_axes=existing_axes,
            fresh_evidence=self._fresh_evidence_from_grounding(grounding_records),
            allocation_targets=self._prompt_allocation_targets(allocation),
            explore_request=explore_request,
        )
        report["llm_telemetry"] = llm_telemetry
        # When the LLM call fails, the Phase-1 two-level deterministic fill kicks
        # in — UNLESS the caller opted out (explore stage), in which case the
        # empty output signals "degraded" so an upper layer can fall back.
        if bool(llm_telemetry.get("llm_call_failed")) and allow_deterministic_llm_fallback:
            candidates = self._deterministic_fallback_materialize_candidates(
                selected_interests=selected_interests,
                existing_axes=existing_axes,
                platforms=platforms,
            )

        # Allowed-interest clamp (R3, explore's mechanical AC2 guarantee): the
        # parser trusts the LLM's raw ``interest`` verbatim, so a drifted keyword
        # could carry a stale/old-domain ``source_interest``. Dropping candidates
        # whose interest is not in the current seed-label set (normalized match)
        # guarantees every emitted keyword's source_interest ∈ the seeds.
        if allowed_interest_labels is not None:
            allowed = {self._match_text(label) for label in allowed_interest_labels}
            candidates = [c for c in candidates if self._match_text(c.interest) in allowed]

        # Every regular inspiration candidate must also remain attached to one
        # of this round's selected interests. Beyond the label clamp, reject an
        # affirmative conflict where the core concept names a *different*
        # known profile interest but does not name its claimed source interest.
        # This catches attribution drift such as a candidate labelled
        # ``Overlord 故事`` whose core is actually ``无职转生 B站下架`` without
        # requiring broad semantic interest merging.
        candidates, source_interest_rejects = self._filter_source_interest_drift(
            profile,
            selected_interests,
            candidates,
        )

        max_keywords = int(self._inspiration_params.max_keywords_per_platform)
        materialized, materialize_telemetry = materialize_platform_keywords(
            candidates,
            allocation,
            axes=[*existing_axes, *new_axes],
            max_keywords_per_platform=max_keywords,
        )
        hard_gate_rejects = materialize_telemetry.get("hard_gate_rejects")
        if isinstance(hard_gate_rejects, list):
            hard_gate_rejects.extend(source_interest_rejects)
        pipeline_materialize_telemetry: dict[str, object] = {
            **materialize_telemetry,
            "parse_salvaged": bool(llm_telemetry.get("parse_salvaged")),
            "llm_call_failed": bool(llm_telemetry.get("llm_call_failed")),
        }
        report["materialize_telemetry"] = pipeline_materialize_telemetry
        report["axis_coverage"] = pipeline_materialize_telemetry.get("axis_coverage", {})
        report["soft_score_summary"] = pipeline_materialize_telemetry.get(
            "soft_score_distribution",
            {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0},
        )
        deterministic_fill_count = pipeline_materialize_telemetry.get(
            "deterministic_fill_count",
            0,
        )
        report["deterministic_fill"] = (
            deterministic_fill_count if isinstance(deterministic_fill_count, int) else 0
        )
        report["coverage_shortfall"] = pipeline_materialize_telemetry.get(
            "coverage_shortfall",
            [],
        )
        report["parse_salvaged"] = bool(llm_telemetry.get("parse_salvaged"))
        report["llm_call_failed"] = bool(llm_telemetry.get("llm_call_failed"))
        if bool(llm_telemetry.get("llm_call_failed")) and not existing_axes:
            shortfalls = cast(
                "list[dict[str, object]]",
                pipeline_materialize_telemetry.setdefault("coverage_shortfall", []),
            )
            seen_shortfalls = {
                (
                    str(item.get("interest") or ""),
                    self._storage_platform_from_materialized(item.get("platform")),
                )
                for item in shortfalls
            }
            for interest in selected_interests:
                for platform in platforms:
                    key = (interest.label, platform)
                    if key in seen_shortfalls:
                        continue
                    shortfalls.append(
                        {
                            "interest": interest.label,
                            "platform": platform,
                            "reason": "missing_axes",
                            "missing_axes": 1,
                            "missing_platforms": [platform],
                        }
                    )
            report["coverage_shortfall"] = shortfalls
        if persist_axes:
            # Retag freshly-generated axes with the caller's source (explore →
            # ``source='explore'``) so the axis library records them under that
            # source and Phase-2 backfill + ``list_inspiration_axes_by_source``
            # can surface them as the explore lane's own reusable axes (E5).
            # axis_id is derived from (interest, label), so retagging is stable.
            if new_axis_source is not None:
                new_axes = [replace(axis, source=new_axis_source) for axis in new_axes]
            # ⑥ Embedding near-dup axis merge (Spec Part E), resolved here in the
            # async layer so the synchronous upsert DAO stays zero-I/O. Preview
            # (persist_axes=False) never embeds — observation stays read-only.
            axes_to_upsert, embedding_degraded = await self._resolve_axis_merges(
                new_axes, existing_axes
            )
            report["axis_embedding_degraded"] = embedding_degraded
            self._upsert_inspiration_axes(
                [*axes_to_upsert, *existing_axes], bump_usage=bump_axis_usage
            )

        keywords_by_platform: dict[str, list[str]] = {platform: [] for platform in platforms}
        metadata_by_platform: dict[str, dict[str, dict[str, object]]] = {
            platform: {} for platform in platforms
        }
        platform_set = set(platforms)
        grounding_source_by_interest: dict[str, str] = {}
        for record in grounding_records:
            if record.secondary_interest and record.grounding_source:
                grounding_source_by_interest.setdefault(
                    record.secondary_interest,
                    record.grounding_source,
                )
        axis_id_index = self._build_axis_id_index([*existing_axes, *new_axes])
        for item in materialized:
            platform = self._storage_platform_from_materialized(item.metadata.get("source_domain"))
            if platform not in platform_set:
                continue
            keywords_by_platform[platform].append(item.keyword)
            metadata = dict(item.metadata)
            axis_label = str(metadata.get("axis_label") or "").strip()
            source_interest = str(metadata.get("source_interest") or "").strip()
            # Attribution rides the persisted angle_id/angle_label columns: write
            # the REAL axis_id into angle_id (never the label) so backfill can key
            # yield on the axis. Deterministic-fill candidates carry the id
            # verbatim; an LLM ref resolves against the library; otherwise the id
            # is derived from (source_interest, axis_label) — the same stable hash
            # the axis stores, so legacy rows reconstruct identically.
            axis_id = self._resolve_realized_axis_id(
                raw_axis_id=str(metadata.get("axis_id") or "").strip(),
                source_interest=source_interest,
                axis_label=axis_label,
                axis_id_index=axis_id_index,
            )
            metadata.pop("axis_id", None)
            metadata["angle_id"] = axis_id
            metadata["angle_label"] = axis_label
            metadata.setdefault("generation_reason", metadata.get("origin", ""))
            metadata.setdefault("inspiration_backend", "axis_keyword")
            metadata.setdefault(
                "grounding_source",
                grounding_source_by_interest.get(source_interest, ""),
            )
            metadata["query_kind"] = (
                keyword_kind_by_platform.get(platform, query_kind)
                if (keyword_kind_by_platform is not None)
                else query_kind
            )
            metadata_by_platform[platform][item.keyword] = metadata

        # Apply the same recent-query family guard used by persistence before
        # reporting or recording realized interests. Preview and production
        # therefore agree, and a suffix-only replay does not falsely cool down
        # an interest that produced no usable keyword this round.
        for platform, words in keywords_by_platform.items():
            keyword_kind = (
                keyword_kind_by_platform.get(platform, query_kind)
                if keyword_kind_by_platform is not None
                else query_kind
            )
            novel_words = self._host._novel_keywords(
                platform,
                words,
                keyword_kind=keyword_kind,
            )
            keywords_by_platform[platform] = novel_words
            metadata_by_platform[platform] = {
                word: metadata_by_platform[platform][word]
                for word in novel_words
                if word in metadata_by_platform[platform]
            }

        realized_interest_keys = {
            self._match_text(metadata.get("source_interest"))
            for platform_metadata in metadata_by_platform.values()
            for metadata in platform_metadata.values()
            if self._match_text(metadata.get("source_interest"))
        }
        realized_interests = [
            interest
            for interest in selected_interests
            if self._match_text(interest.label) in realized_interest_keys
        ]
        self._record_inspiration_interest_selection(
            realized_interests,
            digest=digest,
            query_kind=query_kind,
            selection_scope=selection_scope,
        )
        report["realized_secondary_interests"] = [interest.label for interest in realized_interests]
        report["platform_keywords"] = keywords_by_platform
        report["rejected_reasons"] = {
            platform: [
                item
                for item in cast(
                    "list[dict[str, object]]",
                    materialize_telemetry.get("hard_gate_rejects", []),
                )
                if self._storage_platform_from_materialized(item.get("platform")) == platform
                and item.get("reason") != "platform_style_mismatch"
            ]
            for platform in platforms
        }
        # F3 (Codex R1 S2): expose the per-keyword metadata (incl. core_concept /
        # decoration) on the report explicitly. ``metadata_by_platform`` is
        # otherwise built only for the insert call, and preview returns BEFORE
        # inserting — so without this the preview path would never surface it.
        report["metadata_by_platform"] = metadata_by_platform

        ledger: dict[str, int] = {}
        if not persist_keywords:
            return ledger, report
        for platform, words in keywords_by_platform.items():
            keyword_kind = (
                keyword_kind_by_platform.get(platform, query_kind)
                if keyword_kind_by_platform is not None
                else query_kind
            )
            inserted = self._host._insert(
                platform,
                words,
                digest,
                keyword_kind=keyword_kind,
                metadata_by_keyword=metadata_by_platform[platform],
            )
            if inserted > 0:
                ledger[platform] = inserted
        return ledger, report

    @classmethod
    def _filter_source_interest_drift(
        cls,
        profile: SoulProfile,
        selected_interests: Sequence[SecondaryInterest],
        candidates: Sequence[MaterializeCandidate],
    ) -> tuple[list[MaterializeCandidate], list[dict[str, object]]]:
        """Reject candidates affirmatively anchored to another profile interest."""

        selected = {cls._match_text(item.label) for item in selected_interests if item.label}
        profile_interests = build_like_secondary_interest_window(
            profile,
            coverage_snapshot={},
            max_interests=256,
        )
        anchors = [
            (item.label, cls._interest_anchor_key(item.label))
            for item in profile_interests
            if cls._interest_anchor_key(item.label)
        ]
        kept: list[MaterializeCandidate] = []
        rejected: list[dict[str, object]] = []
        for candidate in candidates:
            claimed_match = cls._match_text(candidate.interest)
            claimed_anchor = cls._interest_anchor_key(candidate.interest)
            core_anchor = cls._interest_anchor_key(candidate.core_concept)
            reason = ""
            conflicting_interest = ""
            if claimed_match not in selected:
                reason = "unselected_source_interest"
            elif core_anchor:
                for label, anchor in anchors:
                    if cls._match_text(label) == claimed_match:
                        continue
                    # Nested labels are often aliases at different granularity;
                    # interest consolidation owns those, not this guard.
                    if anchor in claimed_anchor or claimed_anchor in anchor:
                        continue
                    if anchor in core_anchor and claimed_anchor not in core_anchor:
                        reason = "source_interest_mismatch"
                        conflicting_interest = label
                        break
            if reason:
                rejected.append(
                    {
                        "keyword": " ".join(
                            part for part in (candidate.core_concept, candidate.decoration) if part
                        ).strip(),
                        "platform": candidate.platform,
                        "reason": reason,
                        "source_interest": candidate.interest,
                        "conflicting_interest": conflicting_interest,
                    }
                )
                continue
            kept.append(candidate)
        return kept, rejected

    @staticmethod
    def _interest_anchor_key(value: object) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").strip().casefold())

    async def preview_inspiration_keywords(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile | None = None,
        query_kind: str = "regular",
        persist_axes: bool = False,
    ) -> dict[str, object]:
        """Run the inspiration flow without writing keyword rows."""

        normalized_platforms = [str(platform).strip() for platform in platforms if platform]
        normalized_platforms = [platform for platform in normalized_platforms if platform]
        if profile is None:
            profile = await self._host._load_profile()
        if not normalized_platforms or profile is None:
            return self._empty_inspiration_axis_report(
                normalized_platforms,
                query_kind=query_kind,
                inserted=False,
            )

        _ledger, report = await self._run_inspiration_axis_pipeline(
            normalized_platforms,
            profile=profile,
            digest=profile_kw_digest(profile),
            query_kind=query_kind,
            persist_keywords=False,
            persist_grounding=False,
            persist_axes=persist_axes,
            bump_axis_usage=False,
            selection_scope="preview",
        )
        return report

    async def _run_shared_inspiration_stage(
        self,
        regular_platforms: list[str],
        *,
        explore_platforms: list[str],
        profile: SoulProfile,
        digest: str,
    ) -> tuple[dict[str, int], int]:
        provider = self._inspiration_provider
        if provider is None or not bool(
            getattr(self._discovery, "inspiration_search_enabled", False)
        ):
            return {}, 0
        all_platforms = list(dict.fromkeys([*regular_platforms, *explore_platforms]))
        if not all_platforms:
            return {}, 0
        keyword_kind_by_platform = {
            **{platform: "regular" for platform in regular_platforms},
            **{platform: "explore" for platform in explore_platforms},
        }
        ledger, _report = await self._run_inspiration_axis_pipeline(
            all_platforms,
            profile=profile,
            digest=digest,
            query_kind="both",
            persist_keywords=True,
            persist_grounding=True,
            persist_axes=True,
            bump_axis_usage=True,
            selection_scope="production",
            keyword_kind_by_platform=keyword_kind_by_platform,
        )
        explore_inserted = sum(int(ledger.get(platform, 0)) for platform in explore_platforms)
        return ledger, explore_inserted

    async def _run_inspiration_stage(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        query_kind: str = "regular",
    ) -> dict[str, int]:
        if not platforms:
            return {}
        if not bool(getattr(self._discovery, "inspiration_search_enabled", False)):
            return {}
        provider = self._inspiration_provider
        if provider is None:
            return {}
        ledger, _report = await self._run_inspiration_axis_pipeline(
            platforms,
            profile=profile,
            digest=digest,
            query_kind=query_kind,
            persist_keywords=True,
            persist_grounding=True,
            persist_axes=True,
            bump_axis_usage=True,
            selection_scope="production",
        )
        return ledger

    async def _run_explore_inspiration_stage(
        self,
        explore_platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        explore_domains: Sequence[Mapping[str, object]],
        covered_topic_groups: Sequence[str],
    ) -> tuple[dict[str, int], dict[str, object]]:
        """Cross-domain explore stage (Phase 2.3, E1+E2) via the E0 skeleton.

        Seeds are the CURRENT merged ``explore_domains`` ONLY (each domain becomes
        a pseudo ``seed_interest``) — never like interests, never historical
        explore axes (those are old domains; seeding them would drift
        ``source_interest`` to a stale domain and break AC2). Historical
        high-yield explore axes matched to the current domains ride in as
        ``existing_axes`` to enrich them (E5). The round asks the parameterized
        pipeline for ``keyword_kind='explore'`` on Bilibili, tags fresh axes
        ``source='explore'``, sends the explore_request prompt block, opts OUT of
        the deterministic fallback (LLM failure → empty + degraded so the
        dispatch layer can fall back to the flat ``explore_domains`` queries), and
        clamps candidate interests to the seed set. Cold-start (no domains / no
        explore axes) is a no-op, not an error.
        """

        empty_report = self._empty_inspiration_axis_report(
            explore_platforms, query_kind="explore", inserted=False
        )
        empty_report["explore_degraded"] = False
        provider = self._inspiration_provider
        if not explore_platforms or provider is None:
            return {}, empty_report
        if not bool(getattr(self._discovery, "inspiration_search_enabled", False)):
            return {}, empty_report

        domain_labels: list[str] = []
        seen_labels: set[str] = set()
        for item in explore_domains:
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("domain") or "").strip()
            norm = self._match_text(label)
            if not label or not norm or norm in seen_labels:
                continue
            seen_labels.add(norm)
            domain_labels.append(label)
        if not domain_labels:
            return {}, empty_report

        seed_interests = [
            SecondaryInterest(
                interest_id=f"explore:{label}",
                label=label,
                source="explore",
                weight=1.0,
            )
            for label in domain_labels
        ]

        # E5: historical explore axes matched to the CURRENT domains feed in as
        # existing_axes only (never as seeds).
        lister = getattr(self._db, "list_inspiration_axes_by_source", None)
        matched_axes: list[AxisRow] = []
        if callable(lister):
            axis_limit = max(1, int(self._inspiration_params.aspect_window_size))
            try:
                explore_axes = lister("explore", limit=axis_limit, now=self._clock())
            except Exception:
                logger.debug("explore axis-by-source lookup failed", exc_info=True)
                explore_axes = []
            matched_axes = [
                axis
                for axis in explore_axes
                if self._match_text(axis.interest_label) in seen_labels
            ]

        keyword_kind_by_platform = {platform: "explore" for platform in explore_platforms}
        ledger, report = await self._run_inspiration_axis_pipeline(
            explore_platforms,
            profile=profile,
            digest=digest,
            query_kind="explore",
            persist_keywords=True,
            persist_grounding=True,
            persist_axes=True,
            bump_axis_usage=True,
            selection_scope="production",
            keyword_kind_by_platform=keyword_kind_by_platform,
            seed_interests=seed_interests,
            axis_source=matched_axes,
            explore_request={"avoid_covered": list(covered_topic_groups)},
            allow_deterministic_llm_fallback=False,
            allowed_interest_labels=domain_labels,
            new_axis_source="explore",
        )
        report["explore_degraded"] = bool(report.get("llm_call_failed"))
        return ledger, report

    @staticmethod
    def _keyword_quality_norm(value: object) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()

    @staticmethod
    def _platform_query_style(platform: str) -> dict[str, list[str]]:
        style = _PLATFORM_QUERY_STYLES.get(str(platform or "").strip().lower(), {})
        return {key: list(values) for key, values in style.items()}

    @staticmethod
    def _interest_hint_terms(
        selected_interests: list[SecondaryInterest],
        branches: list[BrainstormBranch],
        grounding_records: list[GroundedProbe],
    ) -> dict[str, set[str]]:
        hints: dict[str, set[str]] = {
            item.label: set() for item in selected_interests if item.label
        }
        for item in selected_interests:
            if not item.label:
                continue
            hints.setdefault(item.label, set()).update(
                InspirationKeywordPipeline._keyword_hint_tokens(item.label)
            )
            hints[item.label].update(InspirationKeywordPipeline._keyword_hint_tokens(item.parent))
            hints[item.label].update(
                InspirationKeywordPipeline._interest_semantic_hint_tokens(item.label)
            )
        for branch in branches:
            if branch.secondary_interest not in hints:
                continue
            values = [branch.branch_label, *branch.probe_queries]
            for value in values:
                hints[branch.secondary_interest].update(
                    InspirationKeywordPipeline._keyword_hint_tokens(value)
                )
        for record in grounding_records:
            if record.secondary_interest not in hints:
                continue
            values = [
                record.probe_query,
                *record.source_terms,
                *record.evidence_titles,
            ]
            for value in values:
                hints[record.secondary_interest].update(
                    InspirationKeywordPipeline._keyword_hint_tokens(value)
                )
        return {label: terms for label, terms in hints.items() if terms}

    @staticmethod
    def _infer_source_interest_from_keyword(
        keyword: str,
        interest_hint_terms: dict[str, set[str]],
    ) -> str:
        keyword_norm = InspirationKeywordPipeline._keyword_quality_norm(keyword)
        if not keyword_norm:
            return ""
        scored: list[tuple[int, str]] = []
        for label, terms in interest_hint_terms.items():
            score = sum(len(term) for term in terms if term and term in keyword_norm)
            if score:
                scored.append((score, label))
        if not scored:
            return ""
        scored.sort(reverse=True)
        best_score, best_label = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0
        return best_label if best_score >= 4 and best_score > second_score else ""

    @staticmethod
    def _keyword_hint_tokens(value: object) -> set[str]:
        text = str(value or "")
        tokens: set[str] = set()
        blocked = {
            "best",
            "guide",
            "guides",
            "explained",
            "review",
            "reviews",
            "tips",
            "discussion",
            "recommendation",
            "recommendations",
            "how",
            "what",
            "why",
            "the",
            "and",
            "with",
        }
        for raw_part in re.split(r"[\s,，、/_|:：;；()（）【】\[\]《》\"'“”‘’]+", text):
            part = InspirationKeywordPipeline._keyword_quality_norm(raw_part)
            if len(part) < 2 or part.isdigit() or part in blocked:
                continue
            if re.search(r"[\u4e00-\u9fff]", part):
                upper = min(8, len(part))
                for size in range(2, upper + 1):
                    for index in range(0, len(part) - size + 1):
                        tokens.add(part[index : index + size])
            elif len(part) >= 3:
                tokens.add(part)
        return tokens

    @staticmethod
    def _interest_semantic_hint_tokens(label: str) -> set[str]:
        text = str(label or "").casefold()
        hints: set[str] = set()
        if "动漫" in text or "动画" in text or "anime" in text:
            hints.update({"anime", "animation", "sakuga"})
        if "美食" in text or "探店" in text or "餐厅" in text or "food" in text:
            hints.update({"food", "restaurant", "recipe", "sukiyaki", "cuisine"})
        if "游戏" in text or "game" in text or "switch" in text:
            hints.update({"game", "gaming", "switch"})
        if "ai" in text or "人工智能" in text or "agent" in text:
            hints.update({"ai", "agent", "workflow", "tool", "tools"})
        return hints

    def _keyword_interest_coverage_snapshot(
        self,
        *,
        selection_scope: str = "production",
    ) -> dict[str, dict[str, object]]:
        getter = getattr(self._db, "get_keyword_interest_coverage_snapshot", None)
        if not callable(getter):
            return {}
        try:
            raw = getter(selection_scope=selection_scope)
        except TypeError:
            try:
                raw = getter()
            except Exception:
                logger.debug("keyword interest coverage snapshot failed", exc_info=True)
                return {}
        except Exception:
            logger.debug("keyword interest coverage snapshot failed", exc_info=True)
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, object]] = {}
        for key, value in raw.items():
            label = str(key or "").strip()
            if label and isinstance(value, dict):
                result[label] = dict(value)
        return result

    def _record_inspiration_interest_selection(
        self,
        selected_interests: list[SecondaryInterest],
        *,
        digest: str,
        query_kind: str,
        selection_scope: str,
    ) -> None:
        recorder = getattr(self._db, "record_keyword_interest_selection", None)
        if not callable(recorder):
            return
        labels = [item.label for item in selected_interests if item.label]
        if not labels:
            return
        try:
            recorder(
                labels,
                query_kind=query_kind,
                selection_scope=selection_scope,
                profile_kw_digest=digest,
            )
        except Exception:
            logger.debug("keyword interest selection ledger write failed", exc_info=True)

    def _selected_inspiration_interests(
        self,
        profile: SoulProfile,
        coverage_snapshot: dict[str, dict[str, object]],
    ) -> list[SecondaryInterest]:
        max_aspects = int(self._inspiration_params.aspect_window_size)
        sample_size = min(4, int(self._inspiration_params.interest_sample_size))
        base_coverage = self._coverage_without_interest_selection_counts(coverage_snapshot)
        authorization_window = build_like_secondary_interest_window(
            profile,
            coverage_snapshot=base_coverage,
            max_interests=max_aspects,
        )
        authorized_coverage = self._coverage_with_interest_authorization_penalties(
            base_coverage,
            authorization_window,
        )
        window = build_like_secondary_interest_window(
            profile,
            coverage_snapshot=authorized_coverage,
            max_interests=max_aspects,
        )
        return window[: max(1, sample_size)]

    @staticmethod
    def _coverage_without_interest_selection_counts(
        coverage_snapshot: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for label, coverage in coverage_snapshot.items():
            if not isinstance(coverage, dict):
                continue
            clean_coverage = dict(coverage)
            clean_coverage.pop("interest_selection_count", None)
            clean_coverage.pop("last_interest_selected_at", None)
            result[str(label)] = clean_coverage
        return result

    def _coverage_with_interest_authorization_penalties(
        self,
        coverage_snapshot: dict[str, dict[str, object]],
        candidate_window: list[SecondaryInterest],
    ) -> dict[str, dict[str, object]]:
        result = {label: dict(coverage) for label, coverage in coverage_snapshot.items()}
        display_by_norm = {self._match_text(label): label for label in result}
        candidate_labels = [item.label for item in candidate_window if item.label]

        def coverage_for(label: str) -> dict[str, object]:
            norm = self._match_text(label)
            display = display_by_norm.setdefault(norm, label)
            return result.setdefault(display, {})

        production_counts = self._production_interest_selection_counts()
        axis_penalties = self._axis_saturation_penalties(candidate_labels)
        for label in candidate_labels:
            norm = self._match_text(label)
            count = production_counts.get(norm, 0)
            penalty = axis_penalties.get(norm, 0)
            if count <= 0 and penalty <= 0:
                continue
            coverage = coverage_for(label)
            coverage["interest_selection_count"] = count + penalty
        return result

    def _production_interest_selection_counts(self) -> dict[str, int]:
        conn = getattr(self._db, "conn", None)
        if conn is None:
            return {}
        window_days = max(
            1,
            int(getattr(self._discovery, "inspiration_interest_selection_window_days", 14)),
        )
        try:
            rows = conn.execute(
                """
                SELECT normalized_interest,
                       COUNT(*) AS interest_selection_count
                FROM discovery_interest_selection_ledger
                WHERE selection_scope = 'production'
                  AND selected_at >= datetime('now', ?)
                GROUP BY normalized_interest
                """,
                (f"-{window_days} days",),
            ).fetchall()
        except Exception:
            logger.debug("keyword production interest selection lookup failed", exc_info=True)
            return {}
        result: dict[str, int] = {}
        for row in rows:
            norm = self._match_text(row["normalized_interest"])
            if norm:
                result[norm] = _ledger_int(row["interest_selection_count"] or 0)
        return result

    def _axis_saturation_penalties(self, interest_labels: list[str]) -> dict[str, int]:
        labels = [str(label).strip() for label in interest_labels if str(label).strip()]
        if not labels:
            return {}
        lister = getattr(self._db, "list_inspiration_axes", None)
        if not callable(lister):
            return {}
        axis_limit = max(
            1,
            int(getattr(self._discovery, "inspiration_axis_selection_window_size", 4)),
        )
        now = datetime.now(UTC)
        try:
            axes = lister(labels, limit=axis_limit, now=now)
        except Exception:
            logger.debug("keyword inspiration axis saturation lookup failed", exc_info=True)
            return {}
        axes_by_interest: dict[str, list[AxisRow]] = {}
        for axis in axes:
            norm = self._match_text(getattr(axis, "interest_label", ""))
            if norm:
                axes_by_interest.setdefault(norm, []).append(axis)
        recent_days = max(
            1,
            int(getattr(self._discovery, "inspiration_axis_saturation_window_days", 14)),
        )
        result: dict[str, int] = {}
        for label in labels:
            norm = self._match_text(label)
            interest_axes = axes_by_interest.get(norm, [])
            if interest_axes and all(
                self._axis_recently_used(axis, now=now, recent_days=recent_days)
                for axis in interest_axes
            ):
                result[norm] = 2
        return result

    @classmethod
    def _axis_recently_used(cls, axis: AxisRow, *, now: datetime, recent_days: int) -> bool:
        used_at = cls._parse_axis_timestamp(getattr(axis, "last_used_at", None))
        if used_at is None:
            return False
        return (now - used_at).total_seconds() <= float(max(1, recent_days) * 86400)

    @staticmethod
    def _parse_axis_timestamp(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _begin_inspiration_provider_stage(provider: Any) -> None:
        begin = getattr(provider, "begin_stage", None)
        if callable(begin):
            try:
                begin()
            except Exception:
                logger.debug("inspiration provider begin_stage failed", exc_info=True)

    @staticmethod
    def _provider_grounding_ledger(provider: Any) -> dict[str, object]:
        getter = getattr(provider, "grounding_ledger", None)
        if not callable(getter):
            return {
                "platforms": {},
                "skipped_cooldown": 0,
                "skipped_budget": 0,
                "timeouts": 0,
            }
        try:
            ledger = getter()
        except Exception:
            logger.debug("inspiration provider ledger failed", exc_info=True)
            return {
                "platforms": {},
                "skipped_cooldown": 0,
                "skipped_budget": 0,
                "timeouts": 0,
            }
        return ledger if isinstance(ledger, dict) else {}

    @staticmethod
    def _provider_bilibili_cooldown(provider: Any) -> float:
        getter = getattr(provider, "bilibili_search_cooldown_remaining", None)
        if not callable(getter):
            return 0.0
        try:
            return max(0.0, float(getter()))
        except Exception:
            logger.debug("inspiration provider cooldown lookup failed", exc_info=True)
            return 0.0

    @staticmethod
    def _new_grounding_ledger(provider: Any) -> dict[str, object]:
        return {
            "searches": 0,
            "platforms": {},
            "platform_complete": {},
            "platform_affirmative_empty": {},
            "platform_partial": {},
            "platform_degraded": {},
            "platform_terminal_evidence": {},
            "skipped_cooldown": 0,
            "skipped_budget": 0,
            "timeouts": 0,
            "local_hits": 0,
            "local_misses": 0,
            "external_searches_saved": 0,
            "local_sources": {},
            "provider_successes": {},
            "provider_failures": {},
            "provider_empty": {},
            "provider_degraded": {},
            "provider_augmentations": 0,
            "bilibili_search_cooldown_remaining": (
                InspirationKeywordPipeline._provider_bilibili_cooldown(provider)
            ),
        }

    @staticmethod
    def _merge_provider_grounding_ledger(
        ledger: dict[str, object],
        provider_ledger: dict[str, object],
        provider: Any,
    ) -> None:
        platforms: dict[str, int] = {}
        raw_platforms = provider_ledger.get("platforms", {})
        if isinstance(raw_platforms, dict):
            for platform, count in raw_platforms.items():
                platforms[str(platform)] = _ledger_int(count or 0)
        ledger["platforms"] = platforms
        for key in (
            "platform_complete",
            "platform_affirmative_empty",
            "platform_partial",
            "platform_degraded",
        ):
            counts: dict[str, int] = {}
            raw_counts = provider_ledger.get(key, {})
            if isinstance(raw_counts, dict):
                for platform, count in raw_counts.items():
                    counts[str(platform)] = _ledger_int(count or 0)
            ledger[key] = counts
        terminal_evidence: dict[str, dict[str, int]] = {}
        raw_terminal_evidence = provider_ledger.get("platform_terminal_evidence", {})
        if isinstance(raw_terminal_evidence, dict):
            for platform, raw_reasons in raw_terminal_evidence.items():
                if not isinstance(raw_reasons, dict):
                    continue
                terminal_evidence[str(platform)] = {
                    str(reason): _ledger_int(count or 0) for reason, count in raw_reasons.items()
                }
        ledger["platform_terminal_evidence"] = terminal_evidence
        for key in ("skipped_cooldown", "skipped_budget", "timeouts"):
            ledger[key] = _ledger_int(ledger.get(key, 0) or 0) + _ledger_int(
                provider_ledger.get(key, 0) or 0
            )
        for key in ("local_hits", "local_misses"):
            ledger[key] = _ledger_int(ledger.get(key, 0) or 0) + _ledger_int(
                provider_ledger.get(key, 0) or 0
            )
        local_sources: dict[str, int] = {}
        raw_local_sources = provider_ledger.get("local_sources", {})
        if isinstance(raw_local_sources, dict):
            for source, count in raw_local_sources.items():
                local_sources[str(source)] = _ledger_int(count or 0)
        ledger["local_sources"] = local_sources
        for key in (
            "provider_successes",
            "provider_failures",
            "provider_empty",
            "provider_degraded",
        ):
            counters: dict[str, int] = {}
            raw_counters = provider_ledger.get(key, {})
            if isinstance(raw_counters, dict):
                for name, count in raw_counters.items():
                    counters[str(name)] = _ledger_int(count or 0)
            ledger[key] = counters
        ledger["provider_augmentations"] = _ledger_int(
            provider_ledger.get("provider_augmentations", 0) or 0
        )
        ledger["bilibili_search_cooldown_remaining"] = (
            InspirationKeywordPipeline._provider_bilibili_cooldown(provider)
        )

    def _deterministic_inspiration_branches(
        self,
        platforms: list[str],
        selected_interests: list[SecondaryInterest],
    ) -> list[BrainstormBranch]:
        probe_limit = max(1, self._inspiration_params.max_probe_searches_per_stage)
        axes = self._listed_inspiration_axes(selected_interests)
        pooled_terms = self._pooled_grounding_terms(platforms)
        return build_grounding_probes(
            selected_interests,
            axes,
            pooled_terms,
            limit=probe_limit,
        )

    def _listed_inspiration_axes(
        self,
        selected_interests: list[SecondaryInterest],
    ) -> list[AxisRow]:
        labels = [item.label for item in selected_interests if item.label]
        if not labels:
            return []
        lister = getattr(self._db, "list_inspiration_axes", None)
        if not callable(lister):
            return []
        axis_limit = max(
            1,
            int(getattr(self._discovery, "inspiration_axis_probe_limit_per_interest", 4)),
        )
        try:
            rows = lister(labels, limit=axis_limit, now=datetime.now(UTC))
        except Exception:
            logger.debug("keyword inspiration axis lookup failed", exc_info=True)
            return []
        return list(rows)

    def _pooled_grounding_terms(self, platforms: list[str]) -> tuple[str, ...]:
        del platforms
        return ()

    async def _ground_inspiration_branches(
        self,
        *,
        provider: Any,
        platforms: list[str],
        digest: str,
        aspects: list[ProfileAspect],
        branches: list[BrainstormBranch],
        query_kind: str,
        persist: bool,
    ) -> tuple[
        list[tuple[ProfileAspect, str, InspirationSeed]],
        list[GroundedProbe],
        dict[str, object],
    ]:
        self._begin_inspiration_provider_stage(provider)
        ledger = self._new_grounding_ledger(provider)
        aspect_by_label = {aspect.label: aspect for aspect in aspects}
        seed_records: list[tuple[ProfileAspect, str, InspirationSeed]] = []
        grounding_records: list[GroundedProbe] = []
        search_limit = int(self._inspiration_params.search_results_per_query)
        max_seeds = int(self._inspiration_params.max_seeds_per_aspect)
        search_budget = int(self._inspiration_params.max_probe_searches_per_stage)
        for branch in branches:
            if _ledger_int(ledger["searches"]) >= search_budget:
                break
            aspect = aspect_by_label.get(branch.secondary_interest)
            if aspect is None:
                continue
            for seed_query in branch.probe_queries[:2]:
                if _ledger_int(ledger["searches"]) >= search_budget:
                    break
                try:
                    previews = await asyncio.wait_for(
                        provider.search(seed_query, limit=search_limit),
                        timeout=_INSPIRATION_PROVIDER_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    ledger["searches"] = _ledger_int(ledger["searches"]) + 1
                    ledger["timeouts"] = _ledger_int(ledger.get("timeouts", 0) or 0) + 1
                    logger.debug("keyword inspiration search timed out for %s", seed_query)
                    continue
                except Exception:
                    ledger["searches"] = _ledger_int(ledger["searches"]) + 1
                    logger.debug(
                        "keyword inspiration search failed for %s",
                        seed_query,
                        exc_info=True,
                    )
                    continue
                grounding_source = str(
                    getattr(provider, "last_search_provider", "") or "none"
                ).strip()
                if grounding_source != "local_cache":
                    ledger["searches"] = _ledger_int(ledger["searches"]) + 1
                for seed in derive_inspiration_seeds(
                    seed_query,
                    previews,
                    max_seeds=max_seeds,
                ):
                    grounding_records.append(
                        GroundedProbe(
                            secondary_interest=branch.secondary_interest,
                            branch_id=branch.branch_id,
                            probe_query=seed_query,
                            source_terms=tuple(seed.source_terms),
                            evidence_titles=tuple(seed.evidence_titles),
                            evidence_urls=tuple(seed.evidence_urls),
                            lens_family=branch.lens_family,
                            evidence_quality=1.0,
                            grounding_source=grounding_source,
                        )
                    )
                    if persist:
                        for platform in platforms:
                            self._upsert_inspiration_seed(
                                platform,
                                digest,
                                aspect,
                                seed_query,
                                seed,
                                query_kind=query_kind,
                            )
                    seed_records.append((aspect, seed_query, seed))
        self._merge_provider_grounding_ledger(
            ledger,
            self._provider_grounding_ledger(provider),
            provider,
        )
        ledger["external_searches_saved"] = min(
            _ledger_int(ledger.get("local_hits", 0) or 0),
            max(0, search_budget - _ledger_int(ledger.get("searches", 0) or 0)),
        )
        logger.info(
            "inspiration grounding ledger kind=%s searches=%d platforms=%s "
            "skipped_cooldown=%d skipped_budget=%d timeouts=%d",
            query_kind,
            _ledger_int(ledger.get("searches", 0) or 0),
            ledger.get("platforms", {}),
            _ledger_int(ledger.get("skipped_cooldown", 0) or 0),
            _ledger_int(ledger.get("skipped_budget", 0) or 0),
            _ledger_int(ledger.get("timeouts", 0) or 0),
        )
        return seed_records, grounding_records, ledger

    @staticmethod
    def _aspects_from_secondary_interests(
        interests: list[SecondaryInterest],
    ) -> list[ProfileAspect]:
        return [
            ProfileAspect(
                aspect_id=item.interest_id,
                label=item.label,
                source="like_secondary_interest",
                weight=item.weight,
                seed_queries=(),
            )
            for item in interests
            if item.label
        ]

    def _inspiration_platform_guides(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile | None,
        recent_by_platform: dict[str, list[str]],
    ) -> list[dict[str, object]]:
        avoid_by_platform = self._host._avoid_hints(profile)
        supply_by_platform = self._host._supply_hints(avoid_by_platform)
        guides: list[dict[str, object]] = []
        for platform in platforms:
            avoid = avoid_by_platform.get(platform, {})
            guides.append(
                {
                    "platform": platform,
                    "supply_advantage": platform_supply_advantage(platform),
                    "query_style": self._platform_query_style(platform),
                    "recent_keywords": list(recent_by_platform.get(platform, [])),
                    "avoid_topics": _as_str_list(avoid.get("avoid_topics")),
                    "avoid_styles": _as_str_list(avoid.get("avoid_styles")),
                    "avoid_franchises": _as_str_list(avoid.get("avoid_franchises")),
                    "prefer_axes": _as_str_list(avoid.get("prefer_axes")),
                    "cold_start": bool(avoid.get("cold_start")),
                    "supply_hint": list(supply_by_platform.get(platform, [])),
                }
            )
        return guides

    def _upsert_inspiration_seed(
        self,
        platform: str,
        digest: str,
        aspect: ProfileAspect,
        seed_query: str,
        seed: InspirationSeed,
        *,
        query_kind: str = "regular",
    ) -> None:
        upsert = getattr(self._db, "upsert_discovery_inspiration_seed", None)
        if not callable(upsert):
            return
        try:
            upsert(
                platform=platform,
                profile_kw_digest=digest,
                aspect_id=aspect.aspect_id,
                query_kind=query_kind,
                seed_query=seed_query,
                inspiration_id=seed.inspiration_id,
                source_terms=list(seed.source_terms),
                evidence_titles=list(seed.evidence_titles),
                evidence_urls=list(seed.evidence_urls),
                reason=seed.reason,
                probe_backend="exa",
            )
        except Exception:
            logger.debug("upsert_discovery_inspiration_seed failed", exc_info=True)
