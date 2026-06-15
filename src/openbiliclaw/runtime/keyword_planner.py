"""Unified keyword planner — deficit-pulled merged keyword generation (P1.6).

The planner is the generation half of the Discover double-buffered
backpressure model (design spec §5.2). It runs as its own background object
(constructed in ``api/runtime_context.py``, launched by the refresh
controller's ``run_forever``) and, when the
``[discovery].unified_keyword_planner_enabled`` flag is on, periodically:

1. Finds the ``due`` platforms — those whose keyword cache (``pending`` rows
   for the current ``profile_kw_digest``) is below ``kw_cache_low`` **and**
   that have a real search deficit (the controller's existing pool-replenish
   口径, including raw-material headroom + in-flight rows — NOT just visible
   pool rows). B站 additionally enters ``due`` on its existing catalysts
   (pool-below-target or ≥ ``signal_event_threshold`` pending signal events),
   even when its cache is not below low.
2. For every due platform, expires any stale-digest ``pending`` rows, then
   builds one merged ``<platforms>`` block and issues a **single** structured
   LLM call covering all due platforms. Parsed keywords are inserted as
   ``pending`` per platform under the current digest.
3. Decline vs failure (P2.2). When the merged call **succeeds**, a platform the
   model explicitly returned an empty list ``[]`` for is an **intentional
   decline** (its supply advantage doesn't fit the user) — it is skipped this
   cycle with NO interest-name fallback (it stays at its current pending and is
   re-offered next cycle if still due). A platform the model **omits** still
   falls back. When the merged call **fails entirely** (raised / no usable
   response), ALL due platforms fall back to deterministic interest names.
4. Rotation polish (P2.3). ``claim_keywords`` is FIFO (oldest pending first), so
   generated words rotate fairly. After a generation cycle, a non-declined due
   platform whose pending is still below ``kw_cache_low`` is conservatively
   topped up from its oldest ``used`` words via ``recycle_oldest_used`` (no
   extra LLM call) so variety keeps flowing; a declined platform is left alone.
   The sparse-profile recycle (generation + fallback produced nothing new)
   stays as the deeper safety valve.

It never fetches — fetch (claim → search) is P1.7. Single-flight is enforced
through the DB-level planner lock, whose write transaction is released
**before** the LLM call so a slow provider never blocks other writers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import socket
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.discovery.pool_snapshot import build_pool_distribution_snapshot
from openbiliclaw.llm.prompts import (
    build_merged_keywords_prompt,
    parse_merged_keywords_with_presence,
)

if TYPE_CHECKING:
    from openbiliclaw.config import Config, DiscoveryConfig
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

# Canonical long-form platform identifiers. These match the keys the keyword
# store, pool-source shares, and the merged prompt builder all expect — short
# codes (xhs/dy/yt/bili) are NOT used here.
_PLANNER_PLATFORMS: tuple[str, ...] = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
)
_BILIBILI = "bilibili"
# The planner reclaims in-flight rows that leaked past the claim lease before
# each generation pass. ``executing`` rows belong to genuinely async (XHS)
# tasks, so give them a much wider timeout than a plain claim lease.
_EXECUTING_TIMEOUT_MULTIPLIER = 6
# P3.2 dynamic cache high-water: a platform's generation target may grow up to
# this multiple of the static ``kw_cache_high`` when its observed yield is low
# (lots of duplicate hits → need more words to fill the same deficit). Below
# ``_DYNAMIC_MIN_SAMPLES`` used keywords the yield estimate is too noisy → fall
# back to the static high.
_DYNAMIC_HIGH_CAP_MULT = 3
_DYNAMIC_MIN_SAMPLES = 10
# P3.1 per-platform topic saturation: a platform with fewer than this many of
# its own fresh pooled rows falls back to the global avoid (too little data to
# judge); above the floor, a topic is "saturated for a platform" once its count
# reaches max(_MIN, platform_total // _DIV) of that platform's own pool.
_PER_PLATFORM_AVOID_FLOOR = 10
_PER_PLATFORM_AVOID_MIN_THRESHOLD = 5
_PER_PLATFORM_AVOID_DIVISOR = 5
# P3.3 data-driven supply advantage: the top topic_groups a platform has
# actually admitted (non-disliked, all-time) ride along as a per-call hint that
# complements the static <supply_advantage> table. A platform needs at least
# _FLOOR admitted rows before the signal is trusted (else cold start → static
# table alone); a topic needs max(_MIN, total // _DIV) admits to count as a
# strength, and at most _TOP are surfaced. The platform's current avoid set is
# subtracted so a topic is never both "lean in" and "avoid".
_PER_PLATFORM_SUPPLY_FLOOR = 10
_PER_PLATFORM_SUPPLY_MIN_THRESHOLD = 3
_PER_PLATFORM_SUPPLY_DIVISOR = 10
_PER_PLATFORM_SUPPLY_TOP = 8
# Merged-generation token budget. The merged call is the largest-output call in
# the system (every due platform × up to gen_batch keywords in one JSON), so a
# fixed max_tokens can truncate the trailing platforms — they then fall onto the
# interest-name fallback. Size max_tokens from the actual per-cycle ask (sum of
# the gen_batch-capped needs) with a generous per-keyword budget (Chinese phrase
# + JSON quoting). Over-provisioning is effectively free: max_tokens is a ceiling
# billed on real output, not a charge. Never drop below the prior 4096 default.
_MERGED_TOKENS_PER_KEYWORD = 48
_MERGED_JSON_OVERHEAD_TOKENS = 1024
_MERGED_MIN_MAX_TOKENS = 4096


def _as_str_list(value: object) -> list[str]:
    """Coerce a loosely-typed JSON value into a clean ``list[str]``."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


class KeywordDeficitSource(Protocol):
    """The deficit口径 the planner reuses (satisfied by the refresh controller).

    The planner deliberately does NOT recompute the pool deficit itself — it
    asks the controller, so it shares the exact same in-flight / raw-headroom
    accounting that drives ``_build_source_replenishment_plan``.
    """

    def keyword_planner_real_deficit(self, platform: str) -> int: ...

    def keyword_planner_bilibili_catalyst(self) -> bool: ...


class _SoulEngineLike(Protocol):
    async def get_profile(self) -> Any: ...


class KeywordPlanner:
    """Deficit-pulled merged keyword generator (design spec §5.2).

    Holds its own ``llm_service`` + ``database`` + ``config`` (the controller
    has no LLM field). The deficit source is injected after construction via
    :meth:`bind_deficit_source` because the controller is built after the
    planner.
    """

    def __init__(
        self,
        *,
        llm_service: Any,
        database: Any,
        config: Config,
        soul_engine: _SoulEngineLike | None = None,
        pool_target_count: int | None = None,
        signal_event_threshold: int = 6,
        owner: str | None = None,
    ) -> None:
        self._llm = llm_service
        self._db = database
        self._config = config
        self._soul_engine = soul_engine
        self._deficit_source: KeywordDeficitSource | None = None
        self._pool_target_count = pool_target_count
        self._signal_event_threshold = signal_event_threshold
        # Unique-per-process lock owner so the CAS single-flight lock can tell
        # this planner instance apart from a stale crashed one.
        self._owner = owner or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        # In-process single-flight: the DB planner lock is re-entrant for the
        # same ``owner`` (so a crashed-then-restarted planner can retake it), so
        # it does NOT stop two overlapping ``run_once`` calls on the SAME
        # instance from double-generating. This lock does — cross-process /
        # cross-instance contention is still handled by the DB lock below.
        self._inflight_lock = asyncio.Lock()
        # P1.9 per-cycle observability ledger: the most recent
        # ``{platform: {"generated": n, "yield": y}}`` snapshot emitted by a
        # generation pass. Empty until the first pass that generates anything.
        self.last_cycle_ledger: dict[str, dict[str, int]] = {}

    # ── wiring ──────────────────────────────────────────────────────────

    def bind_deficit_source(self, source: KeywordDeficitSource) -> None:
        """Inject the controller as the shared pool-deficit / catalyst口径."""
        self._deficit_source = source

    def bind_soul_engine(self, soul_engine: _SoulEngineLike) -> None:
        """Inject the soul engine (the planner always reads the live profile)."""
        self._soul_engine = soul_engine

    @property
    def owner(self) -> str:
        return self._owner

    # ── config helpers ──────────────────────────────────────────────────

    @property
    def _discovery(self) -> DiscoveryConfig:
        return self._config.discovery

    @property
    def enabled(self) -> bool:
        return bool(self._discovery.unified_keyword_planner_enabled)

    @property
    def poll_seconds(self) -> int:
        return max(1, int(self._discovery.planner_poll_seconds))

    def _resolved_pool_target(self) -> int:
        if self._pool_target_count is not None:
            return int(self._pool_target_count)
        scheduler = getattr(self._config, "scheduler", None)
        return int(getattr(scheduler, "pool_target_count", 300))

    # ── loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Poll loop: reclaim leases + run one planning pass each interval.

        When the feature flag is OFF this is a pure no-op (it still sleeps so
        ``run_forever``'s gather keeps a live task, but it never touches the
        store or the LLM) — guaranteeing zero behavior change pre-cutover.
        """
        poll_seconds = self.poll_seconds
        while True:
            if self.enabled:
                try:
                    self.reclaim_leases()
                except Exception:
                    logger.exception("keyword planner lease reclaim failed")
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("keyword planner run_once failed")
            await asyncio.sleep(poll_seconds)

    def reclaim_leases(self) -> None:
        reclaim = getattr(self._db, "reclaim_leased_keywords", None)
        if not callable(reclaim):
            return
        claim_lease_minutes = float(self._discovery.claim_lease_minutes)
        executing_timeout_minutes = claim_lease_minutes * _EXECUTING_TIMEOUT_MULTIPLIER
        reclaimed = int(
            reclaim(
                claim_lease_minutes=claim_lease_minutes,
                executing_timeout_minutes=executing_timeout_minutes,
            )
        )
        if reclaimed:
            logger.info("keyword planner reclaimed %d leased keyword(s) to pending", reclaimed)

    def _retire_min_age_minutes(self) -> float:
        """Age floor before a 0-yield ``used`` word may be retired.

        Must comfortably exceed the worst-case admit latency so a freshly-used
        word whose yield is still pending (fetch-only X/YT, async XHS — marked
        ``used`` at handoff, credited only once the shared pipeline admits) is
        not retired prematurely. Reuse the (much wider) ``executing`` timeout so
        even an in-flight XHS task's eventual admit lands before retirement.
        """
        claim_lease_minutes = float(self._discovery.claim_lease_minutes)
        return max(60.0, claim_lease_minutes * _EXECUTING_TIMEOUT_MULTIPLIER)

    def retire_zero_yield(self) -> int:
        """Retire barren ``used`` words across all planner platforms (P1.8).

        Best-effort; a retire failure on one platform never aborts the pass.
        Returns the total number of rows retired (for observability / tests).
        """
        retire = getattr(self._db, "retire_zero_yield_keywords", None)
        if not callable(retire):
            return 0
        min_age = self._retire_min_age_minutes()
        total = 0
        for platform in _PLANNER_PLATFORMS:
            try:
                total += int(retire(platform, min_age_minutes=min_age))
            except Exception:
                logger.exception("retire_zero_yield_keywords failed for %s", platform)
        if total:
            logger.info("keyword planner retired %d zero-yield keyword(s)", total)
        return total

    # ── one planning pass ───────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Run a single deficit-pulled merged-generation pass.

        Returns a per-platform ``{platform: inserted}`` ledger (empty when
        nothing was due or the flag is off) for observability / tests.
        """
        if not self.enabled:
            return {}

        # P1.8: retire demonstrably-barren search words (``used`` with yield 0
        # past a conservative age floor) every pass. Cheap single UPDATE, runs
        # before the due short-circuit so it fires even when nothing is due, and
        # decoupled from generation/fetch. The age floor protects freshly-used
        # words still pending their async (X / YT / XHS) admit.
        self.retire_zero_yield()

        # In-process single-flight: a second overlapping pass on this instance
        # bails immediately (the DB lock is re-entrant for our own owner).
        if self._inflight_lock.locked():
            logger.debug("keyword planner pass skipped: a pass is already in flight")
            return {}
        async with self._inflight_lock:
            return await self._run_once_locked()

    async def _run_once_locked(self) -> dict[str, int]:
        profile = await self._load_profile()
        if profile is None:
            return {}

        digest = profile_kw_digest(profile)
        due = self._due_platforms(digest)
        if not due:
            return {}

        # Flush stale-digest pending for every due platform up front so the
        # cache count below low / the merged need both reflect the live digest.
        for platform in due:
            try:
                self._db.expire_pending_by_digest(platform, digest)
            except Exception:
                logger.exception("expire_pending_by_digest failed for %s", platform)

        # Single-flight: short CAS lock, released BEFORE the LLM call.
        lease_seconds = max(1.0, float(self._discovery.claim_lease_minutes) * 60.0)
        if not self._acquire_lock(lease_seconds):
            logger.debug("keyword planner pass skipped: another owner holds the lock")
            return {}

        ledger: dict[str, int] = {}
        try:
            ledger = await self._generate_for(due, profile=profile, digest=digest)
        finally:
            self._release_lock()
        return ledger

    async def _generate_for(
        self,
        due: list[str],
        *,
        profile: SoulProfile,
        digest: str,
    ) -> dict[str, int]:
        from openbiliclaw.discovery.strategies._utils import build_profile_summary

        hints_by_platform = self._avoid_hints()
        supply_by_platform = self._supply_hints(hints_by_platform)
        blocks: list[dict[str, object]] = []
        needs: dict[str, int] = {}
        total_ask = 0
        gen_batch = max(0, int(self._discovery.gen_batch))
        for platform in due:
            current_pending = self._count_pending(platform, digest)
            need = max(0, self._target_high(platform) - current_pending)
            # Never ask the model for more than we keep this cycle: the parse caps
            # each platform at gen_batch, so asking for the full (possibly dynamic,
            # up to high × _DYNAMIC_HIGH_CAP_MULT) gap only bloats the merged JSON
            # and pushes the trailing platforms toward truncation. Cap the ask.
            shown_need = min(need, gen_batch)
            if shown_need <= 0:
                # No gap to fill (or gen_batch disabled). The B站 catalyst can mark
                # a platform due while its cache is already full — skip it.
                continue
            needs[platform] = need
            total_ask += shown_need
            avoid = hints_by_platform.get(platform, {})
            blocks.append(
                {
                    "platform": platform,
                    "need": shown_need,
                    "recent_keywords": self._history(platform),
                    "avoid_topics": list(avoid.get("avoid_topics", [])),
                    "avoid_styles": list(avoid.get("avoid_styles", [])),
                    "avoid_franchises": list(avoid.get("avoid_franchises", [])),
                    "supply_hint": list(supply_by_platform.get(platform, [])),
                }
            )

        generated: dict[str, list[str]] = {}
        present: set[str] = set()
        # ``call_failed`` distinguishes "the merged LLM call raised / returned
        # nothing usable" (→ fall back for ALL due platforms) from "the call
        # succeeded but platform X returned an explicit empty list" (→ X
        # declined, skip it without a fallback). It stays False when there was
        # nothing to call (``blocks`` empty) — no failure, just nothing to do.
        call_failed = False
        if blocks:
            target_platforms = [str(block["platform"]) for block in blocks]
            # Budget the merged call's max_tokens from the actual ask (sum of the
            # gen_batch-capped needs) so the trailing platforms in the JSON are
            # never truncated onto the interest-name fallback. Scales with
            # platform count and gen_batch; floored at the prior 4096 default.
            merged_max_tokens = max(
                _MERGED_MIN_MAX_TOKENS,
                total_ask * _MERGED_TOKENS_PER_KEYWORD + _MERGED_JSON_OVERHEAD_TOKENS,
            )
            try:
                profile_summary = build_profile_summary(profile)
                messages = build_merged_keywords_prompt(
                    profile_summary=profile_summary,
                    platform_blocks=blocks,
                )
                response = await self._llm.complete_structured_task(
                    system_instruction=messages[0]["content"],
                    user_input=messages[1]["content"],
                    caller="discovery.keyword_planner",
                    reasoning_effort="",
                    max_tokens=merged_max_tokens,
                )
                content = str(getattr(response, "content", "") or "")
                generated, present = parse_merged_keywords_with_presence(
                    content,
                    target_platforms,
                    per_platform_cap=gen_batch,
                )
            except Exception:
                logger.exception(
                    "keyword planner merged generation failed; "
                    "falling back to interest names for %s",
                    target_platforms,
                )
                generated = {}
                present = set()
                call_failed = True

        low = int(self._discovery.kw_cache_low)
        ledger: dict[str, int] = {}
        # Only platforms with a real need (need > 0) generate / insert. A
        # platform marked due purely by the B站 catalyst whose cache is already
        # at high (need == 0) was dropped from ``blocks`` above and must NOT
        # receive a fallback insert.
        for platform in needs:
            words = generated.get(platform, [])
            declined = False
            if not words:
                if not call_failed and platform in present:
                    # P2.2 decline: the merged call succeeded and the model
                    # explicitly returned [] for this platform → intentional
                    # decline (interests don't fit its supply advantage). Skip:
                    # NO fallback, NO recycle. It keeps its current pending and
                    # is re-offered next cycle if still due.
                    declined = True
                else:
                    # Call failed entirely, or the model omitted this platform →
                    # deterministic interest-name fallback (P1.3 mirror).
                    cap = max(0, int(self._discovery.gen_batch))
                    words = self._interest_fallback(profile, cap)

            if declined:
                ledger[platform] = 0
                continue

            inserted = self._insert(platform, words, digest)
            if inserted <= 0:
                # Sparse profile: generation + fallback produced nothing new
                # for a due platform → recycle its oldest used keywords so the
                # cache does not starve.
                inserted += self._recycle(platform, needs[platform], digest)
            else:
                # P2.3 recycle-on-shortfall: the platform produced SOME new
                # words but its pending is still below the low watermark → top
                # it up from its oldest used words (no extra LLM call) so
                # variety keeps flowing. Conservative: only the remaining gap to
                # low, and never for a declined platform (handled above).
                shortfall = low - self._count_pending(platform, digest)
                if shortfall > 0:
                    inserted += self._recycle(platform, shortfall, digest)
            ledger[platform] = inserted

        self._emit_cycle_ledger(ledger, digest)
        return ledger

    # ── per-cycle observability ledger (P1.9) ───────────────────────────

    def _emit_cycle_ledger(
        self, generated: dict[str, int], digest: str
    ) -> dict[str, dict[str, int]]:
        """Record + log the per-platform production/yield ledger for this cycle.

        The merged generation is a **single** ``discovery.keyword_planner`` LLM
        response (P1.6), so token cost can NOT be apportioned per platform — the
        cost ledger keeps one caller. To still give operators per-platform
        visibility this structured line surfaces, for every platform generated
        this cycle, how many keywords it produced (``generated``) plus the
        platform's cumulative admit-credited ``yield`` (cheap ``SUM(yield_count)``
        via :meth:`Database.keyword_yield_total`, when available). It does NOT
        fake token-level platform attribution.

        Stored on :attr:`last_cycle_ledger` (for observability / tests) and
        emitted as one ``logger.info`` structured line. Returns the structured
        ``{platform: {"generated": n, "yield": y}}`` dict.
        """
        structured: dict[str, dict[str, int]] = {}
        for platform, count in generated.items():
            structured[platform] = {
                "generated": int(count),
                "yield": self._yield_total(platform),
            }
        self.last_cycle_ledger = structured
        if structured:
            logger.info(
                "keyword planner cycle ledger (digest=%s): %s",
                digest,
                ", ".join(
                    f"{p}=generated:{v['generated']}/yield:{v['yield']}"
                    for p, v in structured.items()
                ),
            )
        return structured

    def _yield_total(self, platform: str) -> int:
        """Cumulative admit-credited yield for a platform (0 if unavailable)."""
        getter = getattr(self._db, "keyword_yield_total", None)
        if not callable(getter):
            return 0
        try:
            return int(getter(platform))
        except Exception:
            logger.debug("keyword_yield_total lookup failed for %s", platform, exc_info=True)
            return 0

    # ── due computation ─────────────────────────────────────────────────

    def _due_platforms(self, digest: str) -> list[str]:
        low = int(self._discovery.kw_cache_low)
        due: list[str] = []
        for platform in _PLANNER_PLATFORMS:
            cache_below_low = self._count_pending(platform, digest) < low
            has_deficit = self._real_deficit(platform) > 0
            platform_due = cache_below_low and has_deficit
            if platform == _BILIBILI and not platform_due and self._bilibili_catalyst():
                platform_due = True
            if platform_due:
                due.append(platform)
        return due

    def _real_deficit(self, platform: str) -> int:
        source = self._deficit_source
        if source is None:
            return 0
        try:
            return max(0, int(source.keyword_planner_real_deficit(platform)))
        except Exception:
            logger.exception("keyword planner deficit lookup failed for %s", platform)
            return 0

    def _bilibili_catalyst(self) -> bool:
        source = self._deficit_source
        if source is None:
            return False
        try:
            return bool(source.keyword_planner_bilibili_catalyst())
        except Exception:
            logger.exception("keyword planner bilibili catalyst lookup failed")
            return False

    # ── store + snapshot helpers ────────────────────────────────────────

    def _count_pending(self, platform: str, digest: str) -> int:
        try:
            return int(self._db.count_pending_keywords(platform, digest))
        except Exception:
            logger.exception("count_pending_keywords failed for %s", platform)
            return 0

    def _history(self, platform: str) -> list[str]:
        try:
            return list(
                self._db.history_keywords(
                    platform,
                    int(self._discovery.history_window_size),
                    float(self._discovery.history_window_hours),
                )
            )
        except Exception:
            logger.exception("history_keywords failed for %s", platform)
            return []

    def _insert(self, platform: str, words: list[str], digest: str) -> int:
        if not words:
            return 0
        try:
            return int(self._db.insert_pending_keywords(platform, words, digest))
        except Exception:
            logger.exception("insert_pending_keywords failed for %s", platform)
            return 0

    def _recycle(self, platform: str, n: int, digest: str) -> int:
        recycle = getattr(self._db, "recycle_oldest_used", None)
        if not callable(recycle) or n <= 0:
            return 0
        try:
            return int(recycle(platform, n, digest))
        except Exception:
            logger.exception("recycle_oldest_used failed for %s", platform)
            return 0

    def _avoid_hints(self) -> dict[str, dict[str, list[str]]]:
        """Per-platform topic avoid + global style/franchise avoid (P3.1).

        P1/P2 fed every platform the GLOBAL avoid, which over-avoids — a topic
        saturated on B站 may be absent on 小红书. P3.1 gives each platform its
        OWN saturated topics (relative to that platform's own pool); styles and
        franchises stay global (coarser, less platform-specific). A platform
        with too little of its own pool falls back to the global topic avoid.
        ``prefer_axes`` stays disabled.
        """
        hints: dict[str, object] = {}
        try:
            snapshot = build_pool_distribution_snapshot(
                self._db,
                pool_target_count=self._resolved_pool_target(),
                source_targets=self._source_targets(),
            )
            hints = snapshot.to_prompt_hints()
        except Exception:
            logger.exception("keyword planner failed to build pool distribution snapshot")
        global_topics = _as_str_list(hints.get("avoid_topics"))
        shared_styles = _as_str_list(hints.get("avoid_styles"))
        shared_franchises = _as_str_list(hints.get("avoid_franchises"))

        per_platform: dict[str, dict[str, int]] = {}
        getter = getattr(self._db, "get_pool_topic_counts_by_platform", None)
        if callable(getter):
            try:
                per_platform = getter()
            except Exception:
                logger.exception("keyword planner failed to read per-platform topic counts")

        result: dict[str, dict[str, list[str]]] = {}
        for platform in _PLANNER_PLATFORMS:
            topic_counts = per_platform.get(platform, {})
            total = sum(int(count) for count in topic_counts.values())
            if total < _PER_PLATFORM_AVOID_FLOOR:
                avoid_topics = list(global_topics)
            else:
                threshold = max(
                    _PER_PLATFORM_AVOID_MIN_THRESHOLD, total // _PER_PLATFORM_AVOID_DIVISOR
                )
                avoid_topics = [
                    topic
                    for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                    if int(count) >= threshold
                ][:12]
            result[platform] = {
                "avoid_topics": avoid_topics,
                "avoid_styles": list(shared_styles),
                "avoid_franchises": list(shared_franchises),
            }
        return result

    def _supply_hints(
        self, avoid_by_platform: dict[str, dict[str, list[str]]]
    ) -> dict[str, list[str]]:
        """Per-platform data-driven supply-advantage topics (P3.3).

        The static ``<supply_advantage>`` table in the system prompt gives
        platform priors; this augments it with THIS user's actual admit
        history — the ``topic_group``s each platform has most delivered into the
        cache. The platform's current avoid set is subtracted so a topic is
        never both "lean in" and "avoid" (a saturated-now strength stays only in
        avoid this cycle). Empty until a platform has admitted
        ``_PER_PLATFORM_SUPPLY_FLOOR`` rows (cold start → static table only).
        """
        admitted: dict[str, dict[str, int]] = {}
        getter = getattr(self._db, "get_admitted_topic_counts_by_platform", None)
        if callable(getter):
            try:
                admitted = getter()
            except Exception:
                logger.exception(
                    "keyword planner failed to read per-platform admitted topic counts"
                )
        result: dict[str, list[str]] = {}
        for platform in _PLANNER_PLATFORMS:
            topic_counts = admitted.get(platform, {})
            total = sum(int(count) for count in topic_counts.values())
            if total < _PER_PLATFORM_SUPPLY_FLOOR:
                result[platform] = []
                continue
            avoid = set(avoid_by_platform.get(platform, {}).get("avoid_topics", []))
            threshold = max(
                _PER_PLATFORM_SUPPLY_MIN_THRESHOLD, total // _PER_PLATFORM_SUPPLY_DIVISOR
            )
            result[platform] = [
                topic
                for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                if int(count) >= threshold and topic not in avoid
            ][:_PER_PLATFORM_SUPPLY_TOP]
        return result

    def _target_high(self, platform: str) -> int:
        """P3.2 dynamic cache high-water for a platform.

        Sizes the pending target from the live search deficit ÷ the platform's
        observed average yield-per-keyword, so a low-yield platform (lots of
        duplicate hits) generates MORE words to fill the same gap and a
        high-yield one fewer. Falls back to the static ``kw_cache_high`` on cold
        start (too little yield history), when there is no deficit source, or
        when the deficit is non-positive. Clamped to ``[low+fetch_batch ..
        kw_cache_high × _DYNAMIC_HIGH_CAP_MULT]`` so the cache stays functional.
        """
        static_high = max(1, int(self._discovery.kw_cache_high))
        source = self._deficit_source
        if source is None:
            return static_high
        try:
            deficit = int(source.keyword_planner_real_deficit(platform))
        except Exception:
            return static_high
        if deficit <= 0:
            return static_high
        avg_yield = self._avg_yield(platform)
        if avg_yield <= 0.0:
            return static_high
        target = math.ceil(deficit / avg_yield)
        floor = max(1, int(self._discovery.kw_cache_low) + int(self._discovery.fetch_batch))
        cap = static_high * _DYNAMIC_HIGH_CAP_MULT
        return max(floor, min(target, cap))

    def _avg_yield(self, platform: str) -> float:
        """Observed yield-per-keyword (total yield ÷ used keywords) for a platform.

        Returns 0.0 (→ caller uses the static high) until at least
        ``_DYNAMIC_MIN_SAMPLES`` used keywords exist, so the cold-start estimate
        isn't driven by one or two noisy samples.
        """
        used = 0
        getter = getattr(self._db, "used_keyword_count", None)
        if callable(getter):
            try:
                used = int(getter(platform))
            except Exception:
                used = 0
        if used < _DYNAMIC_MIN_SAMPLES:
            return 0.0
        total = 0
        total_getter = getattr(self._db, "keyword_yield_total", None)
        if callable(total_getter):
            try:
                total = int(total_getter(platform))
            except Exception:
                total = 0
        return total / used if used > 0 else 0.0

    def _source_targets(self) -> dict[str, int]:
        source = self._deficit_source
        getter = getattr(source, "_source_target_counts", None)
        if callable(getter):
            try:
                return {str(k): int(v) for k, v in dict(getter()).items()}
            except Exception:
                logger.exception("keyword planner source-target lookup failed")
        return {}

    # ── lock ────────────────────────────────────────────────────────────

    def _acquire_lock(self, lease_seconds: float) -> bool:
        acquire = getattr(self._db, "acquire_planner_lock", None)
        if not callable(acquire):
            # No lock support → behave single-process (still safe in tests).
            return True
        try:
            return bool(acquire(self._owner, lease_seconds))
        except Exception:
            logger.exception("acquire_planner_lock failed")
            return False

    def _release_lock(self) -> None:
        release = getattr(self._db, "release_planner_lock", None)
        if not callable(release):
            return
        try:
            release(self._owner)
        except Exception:
            logger.exception("release_planner_lock failed")

    # ── profile + fallback ──────────────────────────────────────────────

    async def _load_profile(self) -> SoulProfile | None:
        if self._soul_engine is None:
            return None
        try:
            profile = await self._soul_engine.get_profile()
        except Exception:
            logger.info("keyword planner skipped: soul profile unavailable", exc_info=True)
            return None
        return cast("SoulProfile | None", profile)

    @staticmethod
    def _interest_fallback(profile: SoulProfile, count: int) -> list[str]:
        """Deterministic weight-ranked interest names (mirrors P1.3 XHS/X)."""
        if count <= 0:
            return []
        ranked = sorted(
            profile.preferences.interests,
            key=lambda tag: float(tag.weight or 0.0),
            reverse=True,
        )
        seen: set[str] = set()
        out: list[str] = []
        for tag in ranked:
            name = str(tag.name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) >= count:
                break
        return out
