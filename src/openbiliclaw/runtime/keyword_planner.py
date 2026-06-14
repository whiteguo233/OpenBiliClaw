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
3. On LLM failure — or for any platform the model omits / returns nothing for
   — that platform falls back to deterministic weight-ranked interest names.
   If a due platform still yields nothing new (sparse profile), the planner
   recycles that platform's oldest ``used`` keywords back to ``pending``.

It never fetches — fetch (claim → search) is P1.7. Single-flight is enforced
through the DB-level planner lock, whose write transaction is released
**before** the LLM call so a slow provider never blocks other writers.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.discovery.pool_snapshot import build_pool_distribution_snapshot
from openbiliclaw.llm.prompts import build_merged_keywords_prompt, parse_merged_keywords

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
        blocks: list[dict[str, object]] = []
        needs: dict[str, int] = {}
        high = max(0, int(self._discovery.kw_cache_high))
        for platform in due:
            current_pending = self._count_pending(platform, digest)
            need = max(0, high - current_pending)
            if need <= 0:
                # Bilibili catalyst can mark a platform due while its cache is
                # already full — nothing to generate, skip it from the prompt.
                continue
            needs[platform] = need
            avoid = hints_by_platform.get(platform, {})
            blocks.append(
                {
                    "platform": platform,
                    "need": need,
                    "recent_keywords": self._history(platform),
                    "avoid_topics": list(avoid.get("avoid_topics", [])),
                    "avoid_styles": list(avoid.get("avoid_styles", [])),
                    "avoid_franchises": list(avoid.get("avoid_franchises", [])),
                }
            )

        generated: dict[str, list[str]] = {}
        if blocks:
            target_platforms = [str(block["platform"]) for block in blocks]
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
                )
                content = str(getattr(response, "content", "") or "")
                generated = parse_merged_keywords(
                    content,
                    target_platforms,
                    per_platform_cap=max(0, int(self._discovery.gen_batch)),
                )
            except Exception:
                logger.exception(
                    "keyword planner merged generation failed; "
                    "falling back to interest names for %s",
                    target_platforms,
                )
                generated = {}

        ledger: dict[str, int] = {}
        # Only platforms with a real need (need > 0) generate / insert. A
        # platform marked due purely by the B站 catalyst whose cache is already
        # at high (need == 0) was dropped from ``blocks`` above and must NOT
        # receive a fallback insert.
        for platform in needs:
            words = generated.get(platform, [])
            if not words:
                # Missing platform / LLM failure / empty → deterministic
                # interest-name fallback (P1.3 mirror), capped at gen_batch.
                cap = max(0, int(self._discovery.gen_batch))
                words = self._interest_fallback(profile, cap)
            inserted = self._insert(platform, words, digest)
            if inserted <= 0:
                # Sparse profile: generation + fallback produced nothing new
                # for a due platform → recycle its oldest used keywords so the
                # cache does not starve.
                inserted = self._recycle(platform, needs[platform], digest)
            ledger[platform] = inserted

        if ledger:
            logger.info(
                "keyword planner generated keywords (digest=%s): %s",
                digest,
                ", ".join(f"{p}={n}" for p, n in ledger.items()),
            )
        return ledger

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
        """Global avoid_* hints, reused for every platform block.

        P1 uses GLOBAL pool-saturation avoidance (per-platform saturation is
        P2). ``prefer_axes`` stays disabled. One snapshot is built and its
        hints shared across all due platform blocks.
        """
        hints: dict[str, object] = {}
        try:
            source_targets = self._source_targets()
            snapshot = build_pool_distribution_snapshot(
                self._db,
                pool_target_count=self._resolved_pool_target(),
                source_targets=source_targets,
            )
            hints = snapshot.to_prompt_hints()
        except Exception:
            logger.exception("keyword planner failed to build pool distribution snapshot")
        shared: dict[str, list[str]] = {
            "avoid_topics": _as_str_list(hints.get("avoid_topics")),
            "avoid_styles": _as_str_list(hints.get("avoid_styles")),
            "avoid_franchises": _as_str_list(hints.get("avoid_franchises")),
        }
        return dict.fromkeys(_PLANNER_PLATFORMS, shared)

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
