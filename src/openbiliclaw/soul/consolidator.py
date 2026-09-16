"""LLM-judged consolidation of like / dislike topics at the prompt-cap boundary.

Interest tags and disliked topics accumulate wording variants forever:
the merge path only collapses exact ``(name, category)`` matches, and
weight decay never removes a variant that keeps getting reinforced. On
real profiles this leaves the weight-sorted top-48 (the slice that
actually reaches LLM prompts) half-occupied by duplicates of the same
concept, crowding genuinely distinct interests out of the boundary.

The consolidator runs a staged, mostly-free pipeline:

1. **Rule layer** — identical names within the same category merge in
   code (no LLM); identical names across categories are forced to LLM
   judgement as homonym-safety clusters.
2. **Clustering** — a high-recall similarity graph (embedding plus lexical
   overlap, or lexical-only fallback) groups suspect duplicates by connected
   component. This preserves bridge matches that seed-first greedy grouping
   used to lose. Only multi-member clusters proceed.
3. **No-merge memory** — pairs an earlier run already judged "distinct"
   are not re-asked; a cluster with no unjudged pair is skipped, so
   steady-state runs make zero LLM calls.
4. **LLM judgement** — batched calls (32 clusters per call) return
   merge/keep *operations*, never a rewritten list.
5. **Deterministic apply** — code validates every op (members verbatim,
   full cluster coverage, anti-generalization canonical rules) and
   applies it to the flat preference layer; the Onion interest tree is
   rebuilt via ``populate_from_flat_preference`` exactly like the
   regular layer-update path.

Every applied run writes a full before-snapshot to
``data/memory/consolidation_runs/<run_id>.json`` (revert source) and an
audit entry to ``soul_changelog.md``.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_profile_consolidation_prompt
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.soul.ledger import ProfileLedger

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

# Consolidation works well past the 64-entry prompt caps: top-512 likes
# by weight and the full dislike store (<= 128 by
# _DISLIKED_TOPICS_STORE_CAP). Real profiles accumulate 1000+ interest
# tags; a narrow boundary (128 until v0.3.121) left most wording
# variants untouched, so duplicate weight stayed split across variants
# and never re-entered the truncated top-48. 512 covers the whole
# meaningful store; only the deep <0.5-weight tail is left to decay.
_LIKES_BOUNDARY = 512
# Likes are only *candidate-recalled* at this threshold; the LLM still makes
# the final merge/keep decision. A higher 0.85 cut missed many real secondary
# duplicates on bge-m3 (e.g. 社会时事/时事新闻 around 0.81), so likes use a
# recall-oriented boundary while dislikes retain the stricter old boundary.
_SIMILARITY_THRESHOLD = 0.80
_DISLIKE_SIMILARITY_THRESHOLD = 0.85
_SAME_CATEGORY_SIMILARITY_MARGIN = 0.04
_OVER_TARGET_SIMILARITY_FLOOR = 0.72
_OVER_TARGET_SIMILARITY_MAX_DROP = 0.08
_DEFAULT_MIN_INTERVAL_SECONDS = 12 * 3600
_STATE_FILENAME = "consolidation_state.json"
_RUNS_DIRNAME = "consolidation_runs"
_CHANGELOG_FILENAME = "soul_changelog.md"
# Bump when candidate recall or merge semantics change. Old keep decisions
# were made under the stricter "true synonym only" policy and must not pin
# redundant same-intent likes forever after this policy changes.
_CONSOLIDATION_POLICY_VERSION = 2
# Known-distinct pair memory is capped so the state file stays bounded
# even after months of 12h runs. Sized for the 512-likes boundary: a
# wide first pass can judge hundreds of clusters in one run.
_NO_MERGE_PAIRS_CAP = 16000
# Clusters per LLM judgement call. One giant call over a wide boundary
# risks blowing the output token ceiling mid-JSON (the parse then fails
# and every cluster gets rejected); batches keep each response small
# and a single failed batch only loses its own clusters.
_JUDGE_CLUSTER_BATCH = 32
# Anti-generalization guard for canonical names. Bare umbrella words
# would turn a specific avoid-pattern into a broad content ban.
_BANNED_GENERIC_CANONICALS = frozenset(
    {
        "低质",
        "低质内容",
        "营销",
        "营销内容",
        "标题党",
        "广告",
        "无聊",
        "套路",
        "水分",
        "游戏",
        "视频",
        "内容",
    }
)


class SupportsStructuredTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse: ...


class SupportsEmbed(Protocol):
    async def embed(self, text: str) -> list[float]: ...


def rebuild_profile_tree(memory: MemoryManager, preference_data: dict[str, object]) -> None:
    """Rebuild the Onion interest tree from a flat preference payload."""
    from openbiliclaw.soul.profile import OnionProfile

    soul_layer = memory.get_layer("soul")
    if not soul_layer.data:
        return
    try:
        profile = OnionProfile.from_dict(dict(soul_layer.data))
        profile.populate_from_flat_preference(preference_data)
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        sync = getattr(memory, "sync_profile_files", None)
        if callable(sync):
            sync(profile)
    except Exception:
        logger.exception("Failed to rebuild profile tree after consolidation")


@dataclass
class ConsolidationReport:
    """Outcome of one consolidation pass."""

    ran: bool = False
    throttled: bool = False
    skipped_clean: bool = False
    dry_run: bool = False
    applied: bool = False
    write_conflict: bool = False
    run_id: str = ""
    rule_merges: list[str] = field(default_factory=list)
    clusters_sent: int = 0
    llm_batches: int = 0
    merges: list[dict[str, object]] = field(default_factory=list)
    rejected_clusters: list[str] = field(default_factory=list)
    likes_before: int = 0
    likes_after: int = 0
    dislikes_before: int = 0
    dislikes_after: int = 0
    likes_target_upper: int = _LIKES_BOUNDARY
    likes_target_soft: int = 450
    like_similarity_threshold: float = _SIMILARITY_THRESHOLD
    archived_interests: list[str] = field(default_factory=list)
    protected_interests: list[str] = field(default_factory=list)
    inventory_reason: str = ""
    retry_pending: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class _Cluster:
    cluster_id: str
    scope: str  # "likes" | "dislikes"
    members: list[str]
    member_categories: list[str] | None = None
    known_distinct_pairs: list[list[str]] = field(default_factory=list)

    @property
    def member_keys(self) -> list[str]:
        """No-merge pair keys. Homonym clusters qualify duplicate names by category."""
        if self.member_categories is None:
            return list(self.members)
        return [
            f"{name}::{category}"
            for name, category in zip(self.members, self.member_categories, strict=True)
        ]


def _pair_key(a: str, b: str) -> str:
    return "||".join(sorted((a, b)))


def _preference_revision(data: dict[str, Any]) -> str:
    """Hash every preference field that consolidation may overwrite."""
    import hashlib

    payload = {
        "interests": data.get("interests", []),
        "archived_interests": data.get("archived_interests", []),
        "disliked_topics": data.get("disliked_topics", []),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _batch_count(item_count: int, batch_size: int) -> int:
    if item_count <= 0 or batch_size <= 0:
        return 0
    return (item_count + batch_size - 1) // batch_size


def _log_run_summary(report: ConsolidationReport, *, changed: bool) -> None:
    logger.info(
        "profile consolidation run completed: "
        "run_id=%s dry_run=%s clusters=%d llm_batches=%d changed=%s applied=%s conflict=%s "
        "merges=%d rule_merges=%d rejected=%d archived=%d "
        "likes=%d->%d dislikes=%d->%d retry_pending=%s errors=%d",
        report.run_id,
        report.dry_run,
        report.clusters_sent,
        report.llm_batches,
        changed,
        report.applied,
        report.write_conflict,
        len(report.merges),
        len(report.rule_merges),
        len(report.rejected_clusters),
        len(report.archived_interests),
        report.likes_before,
        report.likes_after,
        report.dislikes_before,
        report.dislikes_after,
        report.retry_pending,
        len(report.errors),
    )


def _qualified_member_key(name: str, category: str) -> str:
    return f"{name}::{category}" if category else name


def _member_name(ref: object) -> str:
    if isinstance(ref, dict):
        return str(ref.get("name", "")).strip()
    return str(ref).strip()


def _member_ref_key(ref: object) -> str:
    if isinstance(ref, dict):
        return _qualified_member_key(
            str(ref.get("name", "")).strip(),
            str(ref.get("category", "")).strip(),
        )
    return str(ref).strip()


def _interest_member_key(item: dict[str, Any]) -> str:
    return _qualified_member_key(
        str(item.get("name", "")).strip(),
        str(item.get("category", "")).strip(),
    )


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def _lexical_form(name: str) -> str:
    """Normalize a label for conservative character-overlap recall."""
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(name or "").lower())


def _longest_common_substring_length(a: str, b: str) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for char_a in a:
        current = [0] * (len(b) + 1)
        for index, char_b in enumerate(b, start=1):
            if char_a == char_b:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def _lexically_related(a: str, b: str) -> bool:
    """High-recall lexical gate for like labels; the LLM remains final judge."""
    left = _lexical_form(a)
    right = _lexical_form(b)
    if not left or not right or left == right:
        return left == right and bool(left)
    shorter = min(len(left), len(right))
    if shorter < 2:
        return False
    left_numbers = re.findall(r"\d+", left)
    right_numbers = re.findall(r"\d+", right)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return False
    # Version/model suffixes are often the *meaningful* distinction (GPT-4 vs
    # GPT-5), and test/eval fixtures also use A/B or numeric suffixes. Do not
    # let a long shared prefix collapse those enumerated labels into one giant
    # connected component; embeddings can still recall them when appropriate.
    if len(left) == len(right):
        differing = [(x, y) for x, y in zip(left, right, strict=True) if x != y]
        if differing and all(
            x.isascii() and x.isalnum() and y.isascii() and y.isalnum() for x, y in differing
        ):
            return False
    left_suffix = re.fullmatch(r"(.+?)([a-z0-9]+)", left)
    right_suffix = re.fullmatch(r"(.+?)([a-z0-9]+)", right)
    if (
        left_suffix is not None
        and right_suffix is not None
        and left_suffix.group(1) == right_suffix.group(1)
        and left_suffix.group(2) != right_suffix.group(2)
    ):
        return False
    if left in right or right in left:
        return True
    overlap = _longest_common_substring_length(left, right)
    return overlap >= 2 and overlap / shorter >= 0.5


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _cosine(a: list[float], b: list[float]) -> float:
    from openbiliclaw.llm.embedding import cosine_similarity

    return cosine_similarity(a, b)


class ProfileConsolidator:
    """Staged like/dislike topic consolidation with LLM-judged merges."""

    def __init__(
        self,
        *,
        memory: MemoryManager,
        llm_service: SupportsStructuredTask | None,
        embedding_service: SupportsEmbed | None = None,
        data_dir: Path | str | None = None,
        min_interval_seconds: int = _DEFAULT_MIN_INTERVAL_SECONDS,
        likes_boundary: int = _LIKES_BOUNDARY,
        similarity_threshold: float = _SIMILARITY_THRESHOLD,
        like_target_upper: int = _LIKES_BOUNDARY,
        like_target_soft: int = 450,
        archive_enabled: bool = True,
        database: Database | None = None,
    ) -> None:
        self._memory = memory
        self._llm_service = llm_service
        self._embedding_service = embedding_service
        resolved_dir = data_dir or getattr(memory, "_data_dir", None)
        self._data_dir = Path(resolved_dir) if resolved_dir else None
        self._min_interval_seconds = int(min_interval_seconds)
        self._likes_boundary = int(likes_boundary)
        self._similarity_threshold = float(similarity_threshold)
        self._like_target_upper = max(1, int(like_target_upper))
        self._like_target_soft = max(1, int(like_target_soft))
        self._archive_enabled = bool(archive_enabled)
        self._database = database

    def _profile_ledger(self) -> ProfileLedger:
        """Best-effort audit ledger over the consolidator's database handle."""
        return ProfileLedger(self._database or getattr(self._memory, "_database", None))

    # -- Public API -----------------------------------------------------------

    def set_embedding_service(self, embedding_service: SupportsEmbed | None) -> None:
        """Attach or replace the embedding service after construction."""
        self._embedding_service = embedding_service

    async def run_if_due(self, *, now: datetime | None = None) -> ConsolidationReport:
        """Run a consolidation pass if the throttle interval elapsed.

        Also skips (cheaply) when the boundary-region input is unchanged
        since the last completed run, so 12h ticks on a stable profile
        cost nothing.
        """
        current = now or datetime.now()
        state = self._load_state()
        last_run_at = _parse_iso(str(state.get("last_run_at", "")))
        if (
            last_run_at is not None
            and (current - last_run_at).total_seconds() < self._min_interval_seconds
        ):
            return ConsolidationReport(throttled=True)

        digest = self._input_digest()
        if (
            digest
            and digest == state.get("last_input_digest")
            and state.get("policy_version") == _CONSOLIDATION_POLICY_VERSION
            and not self._is_like_inventory_over_target()
        ):
            state["last_run_at"] = current.isoformat()
            self._save_state(state)
            return ConsolidationReport(skipped_clean=True)

        return await self.run(dry_run=False, now=current)

    async def run(self, *, dry_run: bool, now: datetime | None = None) -> ConsolidationReport:
        """Execute one consolidation pass. ``dry_run`` never writes anything."""
        current = now or datetime.now()
        report = ConsolidationReport(
            ran=True,
            dry_run=dry_run,
            run_id=current.strftime("%Y%m%d-%H%M%S"),
            likes_target_upper=self._like_target_upper,
            likes_target_soft=min(self._like_target_soft, self._like_target_upper),
        )

        preference_layer = self._memory.get_layer("preference")
        preference_data = preference_layer.data
        source_revision = _preference_revision(preference_data)
        interests_raw = [
            dict(item)
            for item in preference_data.get("interests", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        dislikes_raw = [
            str(item).strip()
            for item in preference_data.get("disliked_topics", [])
            if str(item).strip()
        ]
        archived_raw = [
            dict(item)
            for item in preference_data.get("archived_interests", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        report.likes_before = len(interests_raw)
        report.dislikes_before = len(dislikes_raw)

        before_snapshot = {
            "interests": [dict(item) for item in interests_raw],
            "archived_interests": [dict(item) for item in archived_raw],
            "disliked_topics": list(dislikes_raw),
        }
        # ``populate_from_flat_preference`` is intentionally lossy: it rebuilds
        # the interest tree from the flat store and can change domain ordering,
        # metadata and weights that pre-date this consolidation pass. Keep the
        # exact raw Soul layer so revert is a true restore rather than another
        # derived rebuild.
        soul_before = deepcopy(dict(self._memory.get_layer("soul").data))

        # ── Topic lifecycle scan (Phase 4): decay/archive/trial graduation +
        # subdivision shadow proposals. Folds into the same 12h cadence. ────
        interests_raw, lifecycle_transitions = self._scan_topic_lifecycle(
            interests_raw, current, record=not dry_run
        )

        # ── Stage 0: rule layer — same name + same category ───────────────
        interests, rule_merges, homonym_groups = self._rule_merge_exact_names(interests_raw)
        report.rule_merges = rule_merges

        # ── Boundary slice ─────────────────────────────────────────────────
        ranked = sorted(interests, key=lambda item: _coerce_float(item.get("weight")), reverse=True)
        likes_boundary = (
            len(ranked) if len(ranked) > self._like_target_upper else self._likes_boundary
        )
        like_similarity_threshold = self._effective_like_similarity_threshold(len(ranked))
        report.like_similarity_threshold = like_similarity_threshold
        like_slice_names = [str(item["name"]) for item in ranked[:likes_boundary]]

        # ── Stage 1: clustering ────────────────────────────────────────────
        state = self._load_state()
        policy_is_current = state.get("policy_version") == _CONSOLIDATION_POLICY_VERSION
        protected_no_merge = self._protected_no_merge_pairs(state)
        no_merge: set[str] = (
            set(str(p) for p in state.get("no_merge_pairs", [])) | protected_no_merge
            if policy_is_current
            else set(protected_no_merge)
        )
        forced_clusters = [
            _Cluster(
                cluster_id=f"H{idx + 1}",
                scope="likes",
                members=[str(item.get("name", "")) for item in group],
                member_categories=[str(item.get("category", "")) for item in group],
            )
            for idx, group in enumerate(homonym_groups)
        ]
        for cluster in forced_clusters:
            cluster.known_distinct_pairs = self._known_distinct_pairs(cluster, no_merge)
        homonym_names = {
            _normalize_name(str(group[0].get("name", ""))) for group in homonym_groups if group
        }
        ordinary_like_names = [
            name for name in like_slice_names if _normalize_name(name) not in homonym_names
        ]
        like_category_by_name = {
            str(item.get("name", "")): str(item.get("category", ""))
            for item in ranked[:likes_boundary]
            if _normalize_name(str(item.get("name", ""))) not in homonym_names
        }
        like_clusters = await self._cluster(
            ordinary_like_names,
            scope="likes",
            similarity_threshold=like_similarity_threshold,
            category_by_name=like_category_by_name,
            no_merge=no_merge,
        )
        dislike_clusters = await self._cluster(
            dislikes_raw,
            scope="dislikes",
            similarity_threshold=_DISLIKE_SIMILARITY_THRESHOLD,
            no_merge=no_merge,
        )
        clusters = [
            cluster
            for cluster in (*forced_clusters, *like_clusters, *dislike_clusters)
            if self._has_unjudged_pair(cluster, no_merge)
        ]
        report.clusters_sent = len(clusters)
        if clusters and self._llm_service is not None:
            report.llm_batches = _batch_count(len(clusters), _JUDGE_CLUSTER_BATCH)

        # ── Stage 2: LLM judgement ─────────────────────────────────────────
        valid_ops: list[dict[str, object]] = []
        judged_clusters: list[_Cluster] = []
        if clusters and self._llm_service is not None:
            try:
                ops_by_cluster = await self._judge(clusters)
            except Exception as exc:
                logger.warning("profile consolidation LLM call failed: %s", exc)
                report.errors.append(f"llm: {exc}")
                ops_by_cluster = {}
            for cluster in clusters:
                ops = ops_by_cluster.get(cluster.cluster_id, [])
                problem = self._validate_cluster_ops(cluster, ops)
                if problem:
                    report.rejected_clusters.append(f"{cluster.cluster_id}: {problem}")
                    continue
                judged_clusters.append(cluster)
                valid_ops.extend(
                    {**op, "scope": cluster.scope, "cluster_id": cluster.cluster_id}
                    for op in ops
                    if op.get("op") == "merge"
                )
        elif clusters:
            report.errors.append("llm: service unavailable")
        report.retry_pending = bool(clusters) and len(judged_clusters) < len(clusters)

        # ── Stage 3: apply ─────────────────────────────────────────────────
        rename_map: dict[str, str] = {}
        keyword_interest_rename_map: dict[str, str] = {}
        for op in valid_ops:
            raw_members = op.get("members")
            display_members = raw_members if isinstance(raw_members, list) else []
            members = [_member_name(member) for member in display_members]
            member_keys = _as_str_list(op.get("_member_keys"))
            canonical = str(op.get("canonical", ""))
            if op["scope"] == "likes":
                interests = self._apply_like_merge(
                    interests, members, canonical, member_keys=member_keys
                )
            else:
                dislikes_raw = self._apply_dislike_merge(dislikes_raw, members, canonical)
            for member in display_members:
                if isinstance(member, str) and member != canonical:
                    rename_map[member] = canonical
                    if op["scope"] == "likes":
                        keyword_interest_rename_map[member] = canonical
            report.merges.append(
                {
                    "scope": op["scope"],
                    "members": display_members,
                    "canonical": canonical,
                    "reason": str(op.get("reason", "")),
                }
            )

        interests, archived_raw = self._apply_inventory_target(interests, archived_raw, report)

        report.likes_after = len(interests)
        report.dislikes_after = len(dislikes_raw)

        changed = bool(
            rule_merges or valid_ops or report.archived_interests or lifecycle_transitions
        )
        if dry_run:
            _log_run_summary(report, changed=changed)
            return report

        # Embedding + LLM judgement can take tens of seconds. Preference
        # analysis may legitimately publish new evidence in that window; never
        # overwrite it with a consolidation result computed from the old
        # snapshot. A due tick will retry immediately because no state digest
        # or timestamp is advanced on this optimistic-write conflict.
        latest_preference_data = preference_layer.data
        if _preference_revision(latest_preference_data) != source_revision:
            report.applied = False
            report.write_conflict = True
            report.retry_pending = True
            report.errors.append(
                "profile changed during consolidation; apply skipped and will retry"
            )
            report.rule_merges.clear()
            report.merges.clear()
            report.rejected_clusters.clear()
            report.archived_interests.clear()
            report.inventory_reason = ""
            current_interests = latest_preference_data.get("interests", [])
            current_dislikes = latest_preference_data.get("disliked_topics", [])
            report.likes_before = report.likes_after = len(
                [item for item in current_interests if isinstance(item, dict)]
            )
            report.dislikes_before = report.dislikes_after = len(
                [item for item in current_dislikes if str(item).strip()]
            )
            _log_run_summary(report, changed=False)
            return report

        if changed:
            # Ledger write point D5 #6: 12h profile consolidation (compress /
            # archive). ``revert`` records a separate compensating row below.
            with self._profile_ledger().action(
                write_point="consolidation_apply",
                source="consolidation",
                before={
                    "likes_before": report.likes_before,
                    "dislikes_before": report.dislikes_before,
                },
                source_refs=(
                    [f"merge:{op.get('cluster_id', '')}" for op in valid_ops]
                    + [f"archived:{name}" for name in report.archived_interests]
                )
                or ["consolidation"],
            ) as _entry:
                latest_preference_data["interests"] = interests
                latest_preference_data["archived_interests"] = archived_raw
                latest_preference_data["disliked_topics"] = dislikes_raw
                preference_layer.save()
                _entry.after = {
                    "likes_after": report.likes_after,
                    "dislikes_after": report.dislikes_after,
                }
            overrides_before = self._remap_overrides(rename_map)
            # Sync the rebuilt tree only after override labels follow the
            # canonical rename map, otherwise soul_profile.{json,md} is
            # rendered against stale overrides until some later profile write.
            self._rebuild_profile_tree(preference_layer.data)
            keyword_label_rows = self._preview_keyword_interest_label_migration(
                keyword_interest_rename_map
            )
            self._migrate_keyword_interest_labels(keyword_interest_rename_map)
            self._write_run_record(
                report,
                before_snapshot,
                rename_map,
                overrides_before,
                soul_before=soul_before,
                keyword_interest_rename_map=keyword_interest_rename_map,
                keyword_interest_label_rows=keyword_label_rows,
            )
            self._append_changelog(report, current)
            report.applied = True

        # Record judged-distinct pairs so future runs skip them, and
        # advance run bookkeeping even on no-op runs.
        for cluster in judged_clusters:
            if cluster.member_categories is not None:
                if not any(op.get("cluster_id") == cluster.cluster_id for op in valid_ops):
                    keys = cluster.member_keys
                    for i, a in enumerate(keys):
                        for b in keys[i + 1 :]:
                            no_merge.add(_pair_key(a, b))
                continue
            survivors = self._cluster_survivors(cluster, valid_ops)
            for i, a in enumerate(survivors):
                for b in survivors[i + 1 :]:
                    no_merge.add(_pair_key(a, b))
        ordered_protected = sorted(protected_no_merge)
        remaining_no_merge = sorted(no_merge - protected_no_merge)
        state["protected_no_merge_pairs"] = ordered_protected[:_NO_MERGE_PAIRS_CAP]
        state["no_merge_pairs"] = [*ordered_protected, *remaining_no_merge][:_NO_MERGE_PAIRS_CAP]
        state["policy_version"] = _CONSOLIDATION_POLICY_VERSION
        state["last_run_at"] = current.isoformat()
        if report.retry_pending:
            # Do not call an unresolved input "clean". The next due tick must
            # retry even when the profile itself has not changed (e.g. a
            # temporary provider cooldown or malformed partial response).
            state.pop("last_input_digest", None)
        else:
            state["last_input_digest"] = self._input_digest()
        if changed:
            state["last_applied_run_id"] = report.run_id
        self._save_state(state)
        _log_run_summary(report, changed=changed)
        return report

    # -- Topic lifecycle (Phase 4) ---------------------------------------------

    def _scan_topic_lifecycle(
        self,
        interests: list[dict[str, Any]],
        now: datetime,
        *,
        record: bool,
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        """Apply the 12h lifecycle scan and record transitions/proposals.

        Returns the (possibly mutated) interests and the list of transitions.
        Ledger writes are suppressed on dry runs (``record=False``). Best-effort
        — a failure leaves the interests untouched.
        """
        try:
            from openbiliclaw.soul.topic_lifecycle import propose_subdivisions, scan_lifecycle

            scanned, transitions = scan_lifecycle(interests, now=now)
            proposals = propose_subdivisions(scanned)
            if record:
                ledger = self._profile_ledger()
                for tr in transitions:
                    ledger.record(
                        write_point="topic_lifecycle",
                        source="consolidation",
                        before={"topic": tr.name, "state": tr.from_state},
                        after={"topic": tr.name, "state": tr.to_state},
                        source_refs=[f"reason:{tr.reason}"],
                    )
                for proposal in proposals:
                    # Shadow only: a subdivision proposal is recorded, never
                    # executed (tree restructuring is out of scope this version).
                    ledger.record(
                        write_point="topic_subdivision_proposal",
                        source="consolidation",
                        before={"parent": proposal.parent},
                        after={"child": proposal.child, "ratio": proposal.ratio},
                        source_refs=[f"child:{proposal.child}", f"parent:{proposal.parent}"],
                    )
            return scanned, transitions
        except Exception:
            logger.debug("topic lifecycle scan failed", exc_info=True)
            return interests, []

    # -- Stage 0: rule merges ---------------------------------------------------

    def _is_like_inventory_over_target(self) -> bool:
        preference_layer = self._memory.get_layer("preference")
        interests = [
            item
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        return len(interests) > self._like_target_upper

    def _apply_inventory_target(
        self,
        interests: list[dict[str, Any]],
        archived: list[dict[str, Any]],
        report: ConsolidationReport,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Archive low-value active likes until the active inventory is under target."""
        report.likes_target_upper = self._like_target_upper
        report.likes_target_soft = min(self._like_target_soft, self._like_target_upper)
        if not self._archive_enabled:
            if len(interests) > self._like_target_upper:
                report.inventory_reason = "archive_disabled"
            return interests, archived
        if len(interests) <= self._like_target_upper:
            return interests, archived

        protected_keys = self._protected_like_keys()
        protected_names = [
            str(item.get("name", "")).strip()
            for item in interests
            if _normalize_name(str(item.get("name", ""))) in protected_keys
        ]
        report.protected_interests = list(dict.fromkeys(name for name in protected_names if name))

        target = report.likes_target_soft
        protected_count = len(report.protected_interests)
        if protected_count > self._like_target_upper:
            report.inventory_reason = "protected_inventory_exceeds_target"
            target = protected_count

        archive_count = max(0, len(interests) - target)
        if archive_count <= 0:
            return interests, archived

        candidates = [
            item
            for item in interests
            if _normalize_name(str(item.get("name", ""))) not in protected_keys
        ]
        candidates.sort(key=_archive_rank_key)
        to_archive = candidates[:archive_count]
        if len(to_archive) < archive_count and not report.inventory_reason:
            report.inventory_reason = "no_archive_candidates"

        archive_keys = {_interest_member_key(item) for item in to_archive}
        active = [item for item in interests if _interest_member_key(item) not in archive_keys]
        new_archived = [dict(item) for item in to_archive]
        report.archived_interests = [str(item.get("name", "")) for item in new_archived]
        return active, [*new_archived, *archived]

    def _protected_like_keys(self) -> set[str]:
        loader = getattr(self._memory, "load_profile_overrides", None)
        if not callable(loader):
            return set()
        try:
            overrides = loader()
            interest_edits = getattr(overrides, "interest_edits", {})
            likes = interest_edits.get("likes") if isinstance(interest_edits, dict) else None
            if likes is None:
                return set()
            names: list[str] = []
            names.extend(
                str(add.domain)
                for add in getattr(likes, "add_domains", [])
                if str(getattr(add, "domain", "")).strip()
            )
            names.extend(str(name) for name in getattr(likes, "weight_pins", {}) if str(name))
            names.extend(str(name) for name in getattr(likes, "specific_edits", {}) if str(name))
            return {_normalize_name(name) for name in names if _normalize_name(name)}
        except Exception:
            logger.debug("Failed to load profile overrides for archive protection", exc_info=True)
            return set()

    def _effective_like_similarity_threshold(self, active_like_count: int) -> float:
        if active_like_count <= self._like_target_upper:
            return round(self._similarity_threshold, 4)
        target_span = max(
            self._like_target_upper - min(self._like_target_soft, self._like_target_upper),
            1,
        )
        pressure = min(1.0, (active_like_count - self._like_target_upper) / target_span)
        floor = min(self._similarity_threshold, _OVER_TARGET_SIMILARITY_FLOOR)
        threshold = self._similarity_threshold - (_OVER_TARGET_SIMILARITY_MAX_DROP * pressure)
        return round(max(floor, threshold), 4)

    def _rule_merge_exact_names(
        self, interests: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str], list[list[dict[str, Any]]]]:
        """Merge same normalized name within the same category only."""
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        merges: list[str] = []
        for item in interests:
            category = str(item.get("category", "")).strip()
            key = (_normalize_name(str(item["name"])), category)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = item
                order.append(key)
                continue
            winner, loser = (
                (item, existing)
                if _coerce_float(item.get("weight")) > _coerce_float(existing.get("weight"))
                else (existing, item)
            )
            merged = dict(winner)
            merged["weight"] = max(
                _coerce_float(winner.get("weight")), _coerce_float(loser.get("weight"))
            )
            merged["first_seen"] = _earliest(winner.get("first_seen"), loser.get("first_seen"))
            merged["last_seen"] = _latest(winner.get("last_seen"), loser.get("last_seen"))
            by_key[key] = merged
            merges.append(f"同名同类合并: {winner.get('name')} ({category})")

        result = [by_key[key] for key in order]
        homonym_by_name: dict[str, list[dict[str, Any]]] = {}
        for item in result:
            homonym_by_name.setdefault(_normalize_name(str(item.get("name", ""))), []).append(item)
        homonym_groups = [group for group in homonym_by_name.values() if len(group) >= 2]
        return result, merges, homonym_groups

    # -- Stage 1: clustering ------------------------------------------------------

    async def _cluster(
        self,
        names: list[str],
        *,
        scope: str,
        similarity_threshold: float | None = None,
        category_by_name: dict[str, str] | None = None,
        no_merge: set[str] | None = None,
    ) -> list[_Cluster]:
        unique_names = list(dict.fromkeys(name for name in names if name))
        if len(unique_names) < 2:
            return []
        prefix = "L" if scope == "likes" else "D"
        threshold = (
            self._similarity_threshold if similarity_threshold is None else similarity_threshold
        )

        known_distinct = no_merge or set()
        parent = {name: name for name in unique_names}

        def find(name: str) -> str:
            root = name
            while parent[root] != root:
                root = parent[root]
            while parent[name] != name:
                next_name = parent[name]
                parent[name] = root
                name = next_name
            return root

        def union(first: str, second: str) -> None:
            first_root = find(first)
            second_root = find(second)
            if first_root != second_root:
                parent[second_root] = first_root

        def pair_is_known_distinct(first: str, second: str) -> bool:
            return _pair_key(first, second) in known_distinct

        # Likes get a lexical recall path even when embedding is available.
        # This catches obvious wording families whose vector score is model-
        # sensitive (生活日常/生活记录, 社会时事/时事新闻). Dislikes keep the
        # older, stricter containment-only fallback because false broadening
        # there can suppress valid recommendations.
        for index, name in enumerate(unique_names):
            for other in unique_names[index + 1 :]:
                if pair_is_known_distinct(name, other):
                    continue
                left = _normalize_name(name)
                right = _normalize_name(other)
                if scope == "likes":
                    same_category = False
                    if category_by_name is not None:
                        category = category_by_name.get(name, "")
                        same_category = bool(
                            category and category == category_by_name.get(other, "")
                        )
                    # A shared two-character suffix across unrelated categories
                    # (游戏资讯/科技资讯) is not enough evidence and can bridge
                    # otherwise separate components. Cross-category lexical
                    # recall therefore requires containment; same-category
                    # secondary interests retain the broader overlap gate.
                    lexical_match = (
                        _lexically_related(name, other)
                        if category_by_name is None or same_category
                        else bool(left and right and (left in right or right in left))
                    )
                else:
                    lexical_match = bool(left and right and (left in right or right in left))
                if lexical_match:
                    union(name, other)

        if self._embedding_service is not None:
            vectors: dict[str, list[float]] = {}
            for name in unique_names:
                try:
                    vec = await self._embedding_service.embed(name)
                except Exception:
                    vec = []
                if vec:
                    vectors[name] = vec
            embeddable = [n for n in unique_names if n in vectors]
            for i, name in enumerate(embeddable):
                for other in embeddable[i + 1 :]:
                    if pair_is_known_distinct(name, other):
                        continue
                    pair_threshold = threshold
                    if scope == "likes" and category_by_name is not None:
                        category = category_by_name.get(name, "")
                        if category and category == category_by_name.get(other, ""):
                            pair_threshold = max(
                                _OVER_TARGET_SIMILARITY_FLOOR,
                                threshold - _SAME_CATEGORY_SIMILARITY_MARGIN,
                            )
                    if _cosine(vectors[name], vectors[other]) >= pair_threshold:
                        union(name, other)

        grouped: dict[str, list[str]] = {}
        for name in unique_names:
            grouped.setdefault(find(name), []).append(name)
        groups = [group for group in grouped.values() if len(group) >= 2]

        return [
            _Cluster(
                cluster_id=f"{prefix}{idx + 1}",
                scope=scope,
                members=group,
                known_distinct_pairs=self._known_distinct_pairs_for_keys(group, known_distinct),
            )
            for idx, group in enumerate(groups)
        ]

    @staticmethod
    def _known_distinct_pairs(cluster: _Cluster, no_merge: set[str]) -> list[list[str]]:
        return ProfileConsolidator._known_distinct_pairs_for_keys(cluster.member_keys, no_merge)

    @staticmethod
    def _known_distinct_pairs_for_keys(keys: list[str], no_merge: set[str]) -> list[list[str]]:
        return [
            [first, second]
            for index, first in enumerate(keys)
            for second in keys[index + 1 :]
            if _pair_key(first, second) in no_merge
        ]

    @staticmethod
    def _has_unjudged_pair(cluster: _Cluster, no_merge: set[str]) -> bool:
        keys = cluster.member_keys
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                if _pair_key(a, b) not in no_merge:
                    return True
        return False

    # -- Stage 2: LLM judgement ----------------------------------------------------

    async def _judge(self, clusters: list[_Cluster]) -> dict[str, list[dict[str, Any]]]:
        """Judge clusters in batches of ``_JUDGE_CLUSTER_BATCH`` per LLM call.

        A failed batch only drops its own clusters (they re-cluster next
        run); the call raises only when *every* batch failed, so the
        caller's error reporting still fires on total LLM outage.
        """
        if self._llm_service is None:
            return {}
        ops_by_cluster: dict[str, list[dict[str, Any]]] = {}
        batches = [
            clusters[i : i + _JUDGE_CLUSTER_BATCH]
            for i in range(0, len(clusters), _JUDGE_CLUSTER_BATCH)
        ]
        last_error: Exception | None = None
        succeeded = 0
        for batch in batches:
            try:
                ops_by_cluster.update(await self._judge_batch(batch))
                succeeded += 1
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "profile consolidation judge batch failed (%d clusters): %s",
                    len(batch),
                    exc,
                )
        if batches and succeeded == 0 and last_error is not None:
            raise last_error
        return ops_by_cluster

    async def _judge_batch(self, clusters: list[_Cluster]) -> dict[str, list[dict[str, Any]]]:
        if self._llm_service is None:
            return {}
        preference_layer = self._memory.get_layer("preference")
        weight_by_name = {
            str(item.get("name", "")): _coerce_float(item.get("weight"))
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict)
        }
        weight_by_key: dict[str, float] = {}
        category_by_name: dict[str, str] = {}
        best_weight_by_name: dict[str, float] = {}
        for item in preference_layer.data.get("interests", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            weight = _coerce_float(item.get("weight"))
            key = _interest_member_key(item)
            weight_by_key[key] = max(weight_by_key.get(key, 0.0), weight)
            if name not in best_weight_by_name or weight > best_weight_by_name[name]:
                best_weight_by_name[name] = weight
                category_by_name[name] = str(item.get("category", ""))

        ops_by_cluster: dict[str, list[dict[str, Any]]] = {}
        likes_payload: list[dict[str, object]] = [
            {
                "cluster_id": c.cluster_id,
                "known_distinct_pairs": c.known_distinct_pairs,
                "members": [
                    {
                        "name": name,
                        "weight": round(
                            weight_by_key.get(
                                _qualified_member_key(
                                    name,
                                    c.member_categories[idx]
                                    if c.member_categories is not None
                                    else category_by_name.get(name, ""),
                                ),
                                weight_by_name.get(name, 0.0),
                            ),
                            3,
                        ),
                        "category": (
                            c.member_categories[idx]
                            if c.member_categories is not None
                            else category_by_name.get(name, "")
                        ),
                    }
                    for idx, name in enumerate(c.members)
                ],
            }
            for c in clusters
            if c.scope == "likes"
        ]
        dislikes_payload: list[dict[str, object]] = [
            {
                "cluster_id": c.cluster_id,
                "known_distinct_pairs": c.known_distinct_pairs,
                "members": list(c.members),
            }
            for c in clusters
            if c.scope == "dislikes"
        ]
        messages = build_profile_consolidation_prompt(
            likes_clusters=likes_payload,
            dislikes_clusters=dislikes_payload,
        )
        # Cluster merge/keep decisions are judged purely from the interest-label
        # payload in the user prompt (see ``build_profile_consolidation_prompt``);
        # the user's portrait/core memory is irrelevant to whether two labels denote
        # the same interest. Opt out of the default core-memory injection.
        response = await self._llm_service.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            temperature=0.2,
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.consolidation",
            **without_core_memory_kwargs(self._llm_service.complete_structured_task),
        )
        parsed = parse_llm_json_tolerant(response.content)
        if not isinstance(parsed, dict):
            raise ValueError("consolidation response is not a JSON object")
        for scope_key in ("likes", "dislikes"):
            entries = parsed.get(scope_key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                cluster_id = str(entry.get("cluster_id", ""))
                if cluster_id:
                    ops_by_cluster.setdefault(cluster_id, []).append(entry)
        return ops_by_cluster

    def _validate_cluster_ops(self, cluster: _Cluster, ops: list[dict[str, Any]]) -> str:
        """Return a rejection reason, or '' when the cluster's ops are valid."""
        if not ops:
            return "no ops returned"
        records = [
            {
                "name": name,
                "category": (
                    cluster.member_categories[idx] if cluster.member_categories is not None else ""
                ),
                "key": (
                    _qualified_member_key(name, cluster.member_categories[idx])
                    if cluster.member_categories is not None
                    else name
                ),
            }
            for idx, name in enumerate(cluster.members)
        ]
        record_keys = {record["key"] for record in records}
        known_distinct_keys = {
            _pair_key(pair[0], pair[1]) for pair in cluster.known_distinct_pairs if len(pair) == 2
        }
        covered: list[str] = []

        def consume(ref: object) -> tuple[str, str] | None:
            name = _member_name(ref)
            if not name:
                return None
            if isinstance(ref, dict):
                key = _member_ref_key(ref) if cluster.member_categories is not None else name
                if key in record_keys and key not in covered:
                    return key, name
                return None
            if cluster.member_categories is not None:
                for record in records:
                    if record["name"] == name and record["key"] not in covered:
                        return record["key"], name
                return None
            if name in record_keys and name not in covered:
                return name, name
            return None

        for op in ops:
            kind = str(op.get("op", ""))
            if kind == "keep":
                ref = op.get("member", op.get("name", ""))
                consumed = consume(ref)
                if consumed is None:
                    return f"keep references unknown member: {ref!r}"
                key, _name = consumed
                covered.append(key)
                op["_member_keys"] = [key]
            elif kind == "merge":
                raw_members = op.get("members", [])
                member_refs = raw_members if isinstance(raw_members, list) else []
                members: list[str] = []
                member_keys: list[str] = []
                for member_ref in member_refs:
                    consumed = consume(member_ref)
                    if consumed is None:
                        return f"merge references unknown member: {member_ref!r}"
                    key, name = consumed
                    member_keys.append(key)
                    members.append(name)
                    covered.append(key)
                if len(members) < 2:
                    return "merge with fewer than 2 members"
                for index, first in enumerate(member_keys):
                    for second in member_keys[index + 1 :]:
                        if _pair_key(first, second) in known_distinct_keys:
                            return f"merge violates known-distinct pair: {first!r}, {second!r}"
                canonical = str(op.get("canonical", "")).strip()
                problem = self._validate_canonical(canonical, members, scope=cluster.scope)
                if problem:
                    return problem
                op["_member_keys"] = member_keys
            else:
                return f"unknown op kind: {kind!r}"
        if sorted(covered) != sorted(record_keys):
            return "ops do not cover each member exactly once"
        return ""

    @staticmethod
    def _validate_canonical(canonical: str, members: list[str], *, scope: str) -> str:
        if not canonical:
            return "merge without canonical"
        if _normalize_name(canonical) in {_normalize_name(b) for b in _BANNED_GENERIC_CANONICALS}:
            return f"canonical is a banned umbrella term: {canonical!r}"
        shortest = min(len(m) for m in members)
        member_norms = {_normalize_name(member) for member in members}
        # A canonical dramatically shorter than every member is the
        # signature of upward generalization ("低质内容" <- long specific
        # avoid-patterns). Members themselves are exempt (picking the
        # shortest member as canonical is fine for likes).
        if _normalize_name(canonical) not in member_norms and len(canonical) < shortest * 0.5:
            return f"canonical looks over-generalized for {scope}: {canonical!r}"
        return ""

    @staticmethod
    def _cluster_survivors(cluster: _Cluster, valid_ops: list[dict[str, object]]) -> list[str]:
        """Names that remain distinct after this cluster's ops (keeps + canonicals)."""
        merged_away: set[str] = set()
        canonicals: list[str] = []
        for op in valid_ops:
            if op.get("cluster_id") != cluster.cluster_id:
                continue
            members = _as_str_list(op.get("_member_keys"))
            if not members:
                raw_members = op.get("members", [])
                member_refs = raw_members if isinstance(raw_members, list) else []
                members = [_member_name(member) for member in member_refs if member]
            canonical = str(op.get("canonical", ""))
            canonicals.append(canonical)
            merged_away.update(m for m in members if m != canonical)
        kept = [key for key in cluster.member_keys if key not in merged_away]
        return list(dict.fromkeys([*kept, *canonicals]))

    # -- Stage 3: apply --------------------------------------------------------------

    @staticmethod
    def _apply_like_merge(
        interests: list[dict[str, Any]],
        members: list[str],
        canonical: str,
        *,
        member_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        member_set = set(member_keys or members)
        has_qualified_keys = any("::" in key for key in member_set)

        def is_member(item: dict[str, Any]) -> bool:
            name = str(item.get("name", "")).strip()
            key = _interest_member_key(item)
            return key in member_set or (not has_qualified_keys and name in member_set)

        member_items = [item for item in interests if is_member(item)]
        if not member_items:
            return interests
        base = max(member_items, key=lambda item: _coerce_float(item.get("weight")))
        canonical_category = str(base.get("category", "")).strip()

        def is_existing_canonical(item: dict[str, Any]) -> bool:
            if str(item.get("name", "")).strip() != canonical:
                return False
            if not has_qualified_keys:
                return True
            return str(item.get("category", "")).strip() == canonical_category

        # An existing entry already named `canonical` folds into the merge
        # too, otherwise a rename would create a duplicate. For homonym
        # clusters, only fold the canonical in the merged category; the
        # same surface name in another category is a distinct interest.
        involved = [item for item in interests if is_member(item) or is_existing_canonical(item)]
        base = max(involved, key=lambda item: _coerce_float(item.get("weight")))
        merged = dict(base)
        merged["name"] = canonical
        merged["weight"] = max(_coerce_float(item.get("weight")) for item in involved)
        merged["first_seen"] = _earliest(*(item.get("first_seen") for item in involved))
        merged["last_seen"] = _latest(*(item.get("last_seen") for item in involved))
        aliases = _merged_aliases(involved, canonical)
        if aliases:
            merged["aliases"] = aliases
        else:
            merged.pop("aliases", None)

        result: list[dict[str, Any]] = []
        inserted = False
        for item in interests:
            if is_member(item) or is_existing_canonical(item):
                if not inserted:
                    result.append(merged)
                    inserted = True
                continue
            result.append(item)
        return result

    @staticmethod
    def _apply_dislike_merge(dislikes: list[str], members: list[str], canonical: str) -> list[str]:
        member_set = set(members)
        result: list[str] = []
        inserted = False
        for topic in dislikes:
            if topic in member_set or topic == canonical:
                if not inserted:
                    # Keep the front-most (most recent) member's position
                    # so recency ordering survives consolidation.
                    result.append(canonical)
                    inserted = True
                continue
            result.append(topic)
        if not inserted and members:
            result.append(canonical)
        return result

    def _rebuild_profile_tree(self, preference_data: dict[str, object]) -> None:
        """Rebuild the Onion interest tree from the consolidated flat preference."""
        rebuild_profile_tree(self._memory, preference_data)

    # -- Overrides passthrough + revert ------------------------------------------------

    def _remap_overrides(self, rename_map: dict[str, str]) -> dict[str, object] | None:
        """Apply the merge rename map to user profile overrides.

        Overrides match by exact string (e.g. a removed disliked topic), so
        a raw-store rename would silently un-match the user's edit and let
        a removed avoid-topic resurrect under its canonical name. Returns
        the pre-remap overrides dict (for revert) when anything changed.
        """
        if not rename_map:
            return None
        loader = getattr(self._memory, "load_profile_overrides", None)
        saver = getattr(self._memory, "save_profile_overrides", None)
        if not callable(loader) or not callable(saver):
            return None
        try:
            from openbiliclaw.soul.overrides import ProfileOverrides

            overrides = loader()
            raw: dict[str, object] = dict(overrides.to_dict())
            remapped = _remap_strings(raw, rename_map)
            if json.dumps(raw, ensure_ascii=False, sort_keys=True) == json.dumps(
                remapped, ensure_ascii=False, sort_keys=True
            ):
                return None
            saver(ProfileOverrides.from_dict(remapped))
            return raw
        except Exception:
            logger.exception("Failed to remap profile overrides after consolidation")
            return None

    def _preview_keyword_interest_label_migration(
        self,
        rename_map: dict[str, str],
    ) -> list[dict[str, object]]:
        db = self._database
        if db is None or not rename_map:
            return []
        try:
            from openbiliclaw.discovery.inspiration import _normalize_match_text

            normalized = {
                _normalize_match_text(old): str(new).strip()
                for old, new in rename_map.items()
                if _normalize_match_text(old) and str(new).strip()
            }
            rows = db.conn.execute(
                """
                SELECT id, source_interest
                FROM discovery_keywords
                WHERE COALESCE(source_interest, '') != ''
                """
            ).fetchall()
            result: list[dict[str, object]] = []
            for row in rows:
                old_label = str(row["source_interest"] or "").strip()
                new_label = normalized.get(_normalize_match_text(old_label), "")
                if new_label and old_label != new_label:
                    result.append({"id": int(row["id"]), "old": old_label, "new": new_label})
            return result
        except Exception:
            logger.exception("Failed to preview keyword interest label migration")
            return []

    def _migrate_keyword_interest_labels(self, rename_map: dict[str, str]) -> int:
        db = self._database
        if db is None or not rename_map:
            return 0
        migrate = getattr(db, "migrate_keyword_interest_labels", None)
        if not callable(migrate):
            return 0
        try:
            return int(migrate(rename_map))
        except Exception:
            logger.exception("Failed to migrate keyword interest labels after consolidation")
            return 0

    def _restore_keyword_interest_label_rows(self, rows: object) -> None:
        db = self._database
        if db is None or not isinstance(rows, list) or not rows:
            return
        updates: list[tuple[str, int]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            old_label = str(row.get("old") or "").strip()
            try:
                row_id = int(row.get("id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            if old_label and row_id > 0:
                updates.append((old_label, row_id))
        if not updates:
            return
        try:
            db.conn.executemany(
                "UPDATE discovery_keywords SET source_interest = ? WHERE id = ?",
                updates,
            )
            db.conn.commit()
        except Exception:
            logger.exception("Failed to restore keyword interest labels for consolidation revert")

    def revert(self, run_id: str) -> bool:
        """Restore the preference store (and overrides) from a run record.

        The reverted merges' member pairs are added to the no-merge memory
        so the next scheduled run does not simply redo the same merge the
        user just rolled back.
        """
        if self._data_dir is None:
            return False
        record_path = self._data_dir / _RUNS_DIRNAME / f"{run_id}.json"
        if not record_path.exists():
            return False
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read consolidation run record %s", run_id)
            return False
        before = record.get("before")
        if not isinstance(before, dict):
            return False

        preference_layer = self._memory.get_layer("preference")
        # Ledger write point D5 #6: consolidation revert (compensating row).
        with self._profile_ledger().action(
            write_point="consolidation_revert",
            source="consolidation",
            before={"interests": len(preference_layer.data.get("interests", []))},
            source_refs=[f"run_id:{run_id}"],
        ) as _entry:
            preference_layer.data["interests"] = [
                dict(item) for item in before.get("interests", []) if isinstance(item, dict)
            ]
            preference_layer.data["archived_interests"] = [
                dict(item)
                for item in before.get("archived_interests", [])
                if isinstance(item, dict)
            ]
            preference_layer.data["disliked_topics"] = _as_str_list(before.get("disliked_topics"))
            preference_layer.save()
            _entry.after = {"interests": len(preference_layer.data.get("interests", []))}
        overrides_before = record.get("overrides_before")
        if isinstance(overrides_before, dict):
            saver = getattr(self._memory, "save_profile_overrides", None)
            if callable(saver):
                try:
                    from openbiliclaw.soul.overrides import ProfileOverrides

                    saver(ProfileOverrides.from_dict(overrides_before))
                except Exception:
                    logger.exception("Failed to restore profile overrides for %s", run_id)

        # New run records retain the exact raw Soul layer. Legacy records fall
        # back to rebuilding from preference, preserving backwards-compatible
        # revert support without pretending that old snapshots were lossless.
        if not self._restore_soul_snapshot(record.get("soul_before")):
            self._rebuild_profile_tree(preference_layer.data)
        self._restore_keyword_interest_label_rows(record.get("keyword_interest_label_rows"))

        # Pin the rolled-back merges as known-distinct so the next run
        # doesn't redo them.
        state = self._load_state()
        protected_no_merge = self._protected_no_merge_pairs(state)
        no_merge = (
            set(str(p) for p in state.get("no_merge_pairs", [])) | protected_no_merge
            if state.get("policy_version") == _CONSOLIDATION_POLICY_VERSION
            else set(protected_no_merge)
        )
        for merge in record.get("merges", []):
            if not isinstance(merge, dict):
                continue
            raw_members = merge.get("members", [])
            member_refs = raw_members if isinstance(raw_members, list) else []
            names = [
                *(_member_ref_key(member) for member in member_refs),
                str(merge.get("canonical", "")),
            ]
            names = [n for n in dict.fromkeys(names) if n]
            for i, a in enumerate(names):
                for b in names[i + 1 :]:
                    pair = _pair_key(a, b)
                    no_merge.add(pair)
                    protected_no_merge.add(pair)
        ordered_protected = sorted(protected_no_merge)
        remaining_no_merge = sorted(no_merge - protected_no_merge)
        state["protected_no_merge_pairs"] = ordered_protected[:_NO_MERGE_PAIRS_CAP]
        state["no_merge_pairs"] = [*ordered_protected, *remaining_no_merge][:_NO_MERGE_PAIRS_CAP]
        state["policy_version"] = _CONSOLIDATION_POLICY_VERSION
        state["last_input_digest"] = ""
        self._save_state(state)

        try:
            with (self._data_dir / _CHANGELOG_FILENAME).open("a", encoding="utf-8") as fh:
                fh.write(f"\n## 画像整理回滚 {run_id}（{datetime.now().isoformat()}）\n")
        except Exception:
            logger.debug("Failed to append revert changelog", exc_info=True)
        return True

    def _restore_soul_snapshot(self, snapshot: object) -> bool:
        """Restore an exact Soul layer snapshot and refresh effective mirrors."""
        if not isinstance(snapshot, dict):
            return False
        try:
            soul_layer = self._memory.get_layer("soul")
            soul_layer.data.clear()
            soul_layer.data.update(deepcopy(snapshot))
            soul_layer.save()
        except Exception:
            logger.exception("Failed to restore Soul snapshot after consolidation revert")
            return False

        # Mirror refresh is best-effort and must not turn a successful raw
        # restore into the lossy legacy fallback. Overrides have already been
        # restored above, so the rendered files represent the same effective
        # profile as before the consolidation run.
        sync = getattr(self._memory, "sync_profile_files", None)
        if snapshot and callable(sync):
            try:
                sync(deepcopy(snapshot))
            except Exception:
                logger.exception("Failed to refresh Soul mirrors after consolidation revert")
        return True

    # -- Persistence -------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        return self._data_dir / _STATE_FILENAME if self._data_dir else None

    def _protected_no_merge_pairs(self, state: dict[str, Any]) -> set[str]:
        """Return user-reverted pairs, reconstructing pre-v2 state when needed."""
        if "protected_no_merge_pairs" in state:
            return {str(pair) for pair in state.get("protected_no_merge_pairs", [])}
        if self._data_dir is None:
            return set()

        # Policy v1 stored LLM keeps and explicit user reverts in one list.
        # Recover the latter from the append-only audit + run snapshots before
        # invalidating old judge decisions, so a policy upgrade never undoes a
        # user's explicit rollback.
        changelog_path = self._data_dir / _CHANGELOG_FILENAME
        try:
            changelog = changelog_path.read_text(encoding="utf-8")
        except OSError:
            return set()
        run_ids = set(re.findall(r"^## 画像整理回滚 (\d{8}-\d{6})", changelog, re.MULTILINE))
        protected: set[str] = set()
        for run_id in run_ids:
            record_path = self._data_dir / _RUNS_DIRNAME / f"{run_id}.json"
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            merges = record.get("merges", []) if isinstance(record, dict) else []
            for merge in merges:
                if not isinstance(merge, dict):
                    continue
                raw_members = merge.get("members", [])
                member_refs = raw_members if isinstance(raw_members, list) else []
                keys = [_member_ref_key(member) for member in member_refs]
                keys = [key for key in dict.fromkeys(keys) if key]
                for index, first in enumerate(keys):
                    for second in keys[index + 1 :]:
                        protected.add(_pair_key(first, second))
        return protected

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if path is None or not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("Failed to save consolidation state", exc_info=True)

    def _input_digest(self) -> str:
        import hashlib

        preference_layer = self._memory.get_layer("preference")
        interests = [
            (
                str(item.get("name", "")),
                str(item.get("category", "")),
                round(_coerce_float(item.get("weight")), 3),
            )
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict)
        ]
        ranked = sorted(interests, key=lambda item: item[2], reverse=True)
        boundary_items = sorted(
            (name, category) for name, category, _ in ranked[: self._likes_boundary]
        )
        dislikes = sorted(str(item) for item in preference_layer.data.get("disliked_topics", []))
        payload = json.dumps([boundary_items, dislikes], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _write_run_record(
        self,
        report: ConsolidationReport,
        before_snapshot: dict[str, object],
        rename_map: dict[str, str],
        overrides_before: dict[str, object] | None = None,
        *,
        soul_before: dict[str, object] | None = None,
        keyword_interest_rename_map: dict[str, str] | None = None,
        keyword_interest_label_rows: list[dict[str, object]] | None = None,
    ) -> None:
        if self._data_dir is None:
            return
        runs_dir = self._data_dir / _RUNS_DIRNAME
        try:
            runs_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "run_id": report.run_id,
                "kind": "consolidation",
                "before": before_snapshot,
                "soul_before": soul_before,
                "like_similarity_threshold": report.like_similarity_threshold,
                "rule_merges": report.rule_merges,
                "merges": report.merges,
                "rename_map": rename_map,
                "keyword_interest_rename_map": keyword_interest_rename_map or {},
                "keyword_interest_label_rows": keyword_interest_label_rows or [],
                "rejected_clusters": report.rejected_clusters,
                "overrides_before": overrides_before,
            }
            (runs_dir / f"{report.run_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.debug("Failed to write consolidation run record", exc_info=True)

    def _append_changelog(self, report: ConsolidationReport, now: datetime) -> None:
        if self._data_dir is None:
            return
        lines = [
            f"\n## 画像整理 {report.run_id}（{now.strftime('%Y-%m-%d %H:%M')}）\n",
            f"- 兴趣 {report.likes_before} → {report.likes_after}，"
            f"避雷 {report.dislikes_before} → {report.dislikes_after}\n",
        ]
        if report.archived_interests:
            lines.append(f"- [归档] {len(report.archived_interests)} 个低权重长尾兴趣\n")
        if report.inventory_reason:
            lines.append(f"- [库存] {report.inventory_reason}\n")
        for merge in report.merges:
            members = " / ".join(_as_str_list(merge.get("members")))
            lines.append(f"- [{merge.get('scope')}] {members} → {merge.get('canonical')}\n")
        for rule_merge in report.rule_merges:
            lines.append(f"- [规则] {rule_merge}\n")
        try:
            with (self._data_dir / _CHANGELOG_FILENAME).open("a", encoding="utf-8") as fh:
                fh.writelines(lines)
        except Exception:
            logger.debug("Failed to append consolidation changelog", exc_info=True)


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _archive_rank_key(item: dict[str, Any]) -> tuple[float, str, str, str]:
    return (
        _coerce_float(item.get("weight")),
        str(item.get("last_seen", "")),
        str(item.get("first_seen", "")),
        str(item.get("name", "")),
    )


def _merged_aliases(items: list[dict[str, Any]], canonical: str) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    canonical_norm = _normalize_name(canonical)
    for item in items:
        raw_terms: list[object] = [item.get("name", "")]
        existing_aliases = item.get("aliases", [])
        if isinstance(existing_aliases, list):
            raw_terms.extend(existing_aliases)
        for raw in raw_terms:
            alias = str(raw).strip()
            alias_norm = _normalize_name(alias)
            if not alias or not alias_norm or alias_norm == canonical_norm or alias_norm in seen:
                continue
            aliases.append(alias)
            seen.add(alias_norm)
    return aliases


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _earliest(*values: object) -> str:
    candidates = [str(v) for v in values if v]
    return min(candidates) if candidates else ""


def _latest(*values: object) -> str:
    candidates = [str(v) for v in values if v]
    return max(candidates) if candidates else ""


def _remap_strings(value: object, rename_map: dict[str, str]) -> Any:
    """Recursively replace exact string matches per ``rename_map``.

    Only whole-string equality is rewritten (never substrings), covering
    list entries, dict string values, and dict keys. Colliding renamed
    keys keep the first occurrence.
    """
    if isinstance(value, str):
        return rename_map.get(value, value)
    if isinstance(value, list):
        seen: set[str] = set()
        result: list[Any] = []
        for item in value:
            remapped = _remap_strings(item, rename_map)
            if isinstance(remapped, str):
                if remapped in seen:
                    continue
                seen.add(remapped)
            result.append(remapped)
        return result
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = rename_map.get(key, key) if isinstance(key, str) else key
            if new_key in out:
                continue
            out[new_key] = _remap_strings(item, rename_map)
        return out
    return value
