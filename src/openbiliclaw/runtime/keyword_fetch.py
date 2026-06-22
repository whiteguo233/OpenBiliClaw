"""Deficit-driven keyword fetch coordinator (Discover backpressure, P1.7).

P1.6 made the keyword *planner* fill the ``discovery_keywords`` store with
``pending`` search words. P1.7 makes the five search *fetch* sites consume that
store: when the ``[discovery].unified_keyword_planner_enabled`` flag is on, each
site claims words from the store (atomic ``claim_keywords``), injects them via
the P1.5 injection param, fetches, and walks each claimed word through its
lifecycle terminal — ``used`` / ``failed`` / (async) ``executing`` — depending
on the site's *execution shape* (design spec §5.1 / §11):

* **Inline-admit** (B站 search, 抖音 plugin): fetch → evaluate → admit happen
  synchronously in the call. A successful return marks every claimed word
  ``used``; a fetch exception / empty result marks them ``failed``.
* **Fetch-only → deferred pipeline admit** (X, YouTube): the producer fetches
  raw candidates and hands them to ``discovery_candidates`` / the candidate
  pipeline; admission is downstream. The word is *consumed* on that handoff →
  ``used`` (yield is backfilled later, P1.8, decoupled from ``used``).
* **Truly async** (小红书 only): the extension executes the search out-of-band.
  Claim → enqueue the xhs task carrying ``source_keyword_id`` → mark the word
  ``executing`` (NOT ``used``). The xhs task-result handler marks it ``used`` /
  ``failed`` on the terminal callback. A missing callback is covered by the
  planner's ``reclaim_leased_keywords`` lease sweep.

**Budget-rejection-after-claim rollback.** If a word is claimed but the
downstream enqueue / fetch is then refused by a budget cap (so no fetch ever
ran), the word must go back to ``pending`` rather than be burned. This needs a
*distinguishable* signal: XHS enqueue returns ``ok=False``; 抖音 ``search_aweme``
raises :class:`~openbiliclaw.sources.douyin_plugin_search.DouyinBudgetExhausted`.
The coordinator's :meth:`rollback` issues ``rollback_keyword_to_pending``.

The flag stays OFF by default; the flag-on cutover + E2E is P1.9. With the flag
off (or no coordinator wired) every site takes its byte-identical legacy path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.config import DiscoveryConfig

logger = logging.getLogger(__name__)

# Canonical long-form platform identifiers — these are the keys the keyword
# store + planner share (NOT short codes xhs/dy/yt/bili).
PLATFORM_BILIBILI = "bilibili"
PLATFORM_XIAOHONGSHU = "xiaohongshu"
PLATFORM_DOUYIN = "douyin"
PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITTER = "twitter"


@dataclass(frozen=True)
class ClaimedKeyword:
    """One claimed search keyword + its store row id (lifecycle correlation)."""

    id: int
    keyword: str


class KeywordFetchCoordinator:
    """Claim-from-store + word-lifecycle helper shared by the 5 fetch sites.

    Holds the database (the ``discovery_keywords`` DAO) and the discovery
    config (the flag + ``fetch_batch``). One coordinator instance is wired into
    each producer / the refresh controller; each fetch site asks
    :meth:`should_claim` whether the flag-on path is active, then drives
    :meth:`claim` and the terminal markers below.
    """

    def __init__(self, *, database: Any, discovery_config: DiscoveryConfig) -> None:
        self._db = database
        self._discovery = discovery_config

    # ── flag / gate ─────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether the unified keyword planner flag is on (default OFF)."""
        return bool(getattr(self._discovery, "unified_keyword_planner_enabled", False))

    @property
    def fetch_batch(self) -> int:
        """How many words to claim per fetch (``[discovery].fetch_batch``)."""
        return max(1, int(getattr(self._discovery, "fetch_batch", 5)))

    def should_claim(self) -> bool:
        """Return whether a fetch site should take the claim-from-store path.

        The deficit gate (deficit > 0) and the distinct floor (each platform's
        existing ``min_interval`` / ``_is_due``) are enforced by the site
        itself *before* calling this — the coordinator only owns the flag. The
        store-non-empty gate is enforced by :meth:`claim` returning ``[]``.
        """
        return self.enabled

    # ── claim ───────────────────────────────────────────────────────────

    def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
        """Atomically claim up to ``n`` (default ``fetch_batch``) pending words.

        Returns ``[]`` when the store has no claimable ``pending`` words for the
        platform (the "store non-empty" gate) — the caller must then fall back
        to its legacy/no-op path and must NOT mark any lifecycle state.
        """
        count = self.fetch_batch if n is None else max(0, int(n))
        if count <= 0:
            return []
        claim_fn = getattr(self._db, "claim_keywords", None)
        if not callable(claim_fn):
            return []
        try:
            rows = claim_fn(platform, count)
        except Exception:
            logger.exception("keyword fetch: claim_keywords failed for %s", platform)
            return []
        claimed: list[ClaimedKeyword] = []
        for row in rows or []:
            try:
                kid = int(row["id"])
                word = str(row["keyword"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if word:
                claimed.append(ClaimedKeyword(id=kid, keyword=word))
        return claimed

    # ── lifecycle terminals ─────────────────────────────────────────────

    def mark_used(self, claimed: list[ClaimedKeyword]) -> None:
        """Mark every claimed word ``used`` (inline success / fetch-only handoff)."""
        mark = getattr(self._db, "mark_keyword_used", None)
        if not callable(mark):
            return
        for item in claimed:
            try:
                mark(item.id)
            except Exception:
                logger.exception("keyword fetch: mark_keyword_used failed for id=%s", item.id)

    def mark_failed(self, claimed: list[ClaimedKeyword]) -> None:
        """Mark every claimed word ``failed`` (fetch exception / empty result)."""
        mark = getattr(self._db, "mark_keyword_failed", None)
        if not callable(mark):
            return
        for item in claimed:
            try:
                mark(item.id)
            except Exception:
                logger.exception("keyword fetch: mark_keyword_failed failed for id=%s", item.id)

    def mark_executing(self, claimed: ClaimedKeyword) -> None:
        """Mark one claimed word ``executing`` (async XHS task enqueued)."""
        mark = getattr(self._db, "mark_keyword_executing", None)
        if not callable(mark):
            return
        try:
            mark(claimed.id)
        except Exception:
            logger.exception("keyword fetch: mark_keyword_executing failed for id=%s", claimed.id)

    def rollback(self, claimed: ClaimedKeyword) -> None:
        """Return one claimed word to ``pending`` (budget rejection after claim)."""
        rollback = getattr(self._db, "rollback_keyword_to_pending", None)
        if not callable(rollback):
            return
        try:
            rollback(claimed.id)
        except Exception:
            logger.exception(
                "keyword fetch: rollback_keyword_to_pending failed for id=%s", claimed.id
            )


def mark_keyword_terminal_from_xhs_task(
    database: Any,
    payload_json: str | None,
    *,
    success: bool,
) -> None:
    """Mark an xhs-task's ``source_keyword_id`` word ``used`` / ``failed``.

    Called by the xhs task-result handler (``api/app.py``) on a terminal
    callback. The keyword id rides on the task payload (P1.7 lifecycle
    correlation); a task with no ``source_keyword_id`` (legacy / non-planner
    task) is a silent no-op. Tolerates a missing / malformed payload.
    """
    keyword_id = _extract_source_keyword_id(payload_json)
    if keyword_id is None:
        return
    method = "mark_keyword_used" if success else "mark_keyword_failed"
    mark = getattr(database, method, None)
    if not callable(mark):
        return
    try:
        mark(keyword_id)
    except Exception:
        logger.exception("keyword fetch: %s failed for id=%s", method, keyword_id)


def source_keyword_id_from_xhs_task(payload_json: str | None) -> int | None:
    """Public: read ``source_keyword_id`` off an xhs-task payload, or ``None``.

    Used by the xhs task-result handler (P1.8) to thread the producing keyword's
    id onto the candidates ingested from that task, so admission can backfill the
    keyword's yield. Tolerates a missing / malformed / legacy payload.
    """
    return _extract_source_keyword_id(payload_json)


def _extract_source_keyword_id(payload_json: str | None) -> int | None:
    """Parse ``source_keyword_id`` out of an xhs-task payload JSON blob."""
    if not payload_json:
        return None
    import json

    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("source_keyword_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
