"""LLM-judged consolidation of like / dislike topics at the prompt-cap boundary.

Interest tags and disliked topics accumulate wording variants forever:
the merge path only collapses exact ``(name, category)`` matches, and
weight decay never removes a variant that keeps getting reinforced. On
real profiles this leaves the weight-sorted top-64 (the slice that
actually reaches LLM prompts) half-occupied by duplicates of the same
concept, crowding genuinely distinct interests out of the boundary.

The consolidator runs a staged, mostly-free pipeline:

1. **Rule layer** — identical names with different categories merge in
   code (no LLM).
2. **Clustering** — embedding cosine similarity (or substring fallback)
   groups suspect duplicates. Only multi-member clusters proceed.
3. **No-merge memory** — pairs an earlier run already judged "distinct"
   are not re-asked; a cluster with no unjudged pair is skipped, so
   steady-state runs make zero LLM calls.
4. **LLM judgement** — one batched call returns merge/keep *operations*,
   never a rewritten list.
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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_profile_consolidation_prompt

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

# Consolidation works the boundary region of the 64-entry prompt caps:
# top-128 likes by weight (2x the display cap) and the full dislike
# store (<= 128 by _DISLIKED_TOPICS_STORE_CAP). The goal is not to
# shrink the store but to make the truncated top-64 hold 64 *distinct*
# concepts; the tail below the boundary is left to weight decay.
_LIKES_BOUNDARY = 128
_SIMILARITY_THRESHOLD = 0.85
_DEFAULT_MIN_INTERVAL_SECONDS = 12 * 3600
_STATE_FILENAME = "consolidation_state.json"
_RUNS_DIRNAME = "consolidation_runs"
_CHANGELOG_FILENAME = "soul_changelog.md"
# Known-distinct pair memory is FIFO-capped so the state file stays
# bounded even after months of 12h runs.
_NO_MERGE_PAIRS_CAP = 4000
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


@dataclass
class ConsolidationReport:
    """Outcome of one consolidation pass."""

    ran: bool = False
    throttled: bool = False
    skipped_clean: bool = False
    dry_run: bool = False
    run_id: str = ""
    rule_merges: list[str] = field(default_factory=list)
    clusters_sent: int = 0
    merges: list[dict[str, object]] = field(default_factory=list)
    rejected_clusters: list[str] = field(default_factory=list)
    likes_before: int = 0
    likes_after: int = 0
    dislikes_before: int = 0
    dislikes_after: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class _Cluster:
    cluster_id: str
    scope: str  # "likes" | "dislikes"
    members: list[str]


def _pair_key(a: str, b: str) -> str:
    return "||".join(sorted((a, b)))


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


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
    ) -> None:
        self._memory = memory
        self._llm_service = llm_service
        self._embedding_service = embedding_service
        resolved_dir = data_dir or getattr(memory, "_data_dir", None)
        self._data_dir = Path(resolved_dir) if resolved_dir else None
        self._min_interval_seconds = int(min_interval_seconds)
        self._likes_boundary = int(likes_boundary)
        self._similarity_threshold = float(similarity_threshold)

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
        if digest and digest == state.get("last_input_digest"):
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
        )

        preference_layer = self._memory.get_layer("preference")
        interests_raw = [
            dict(item)
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        dislikes_raw = [
            str(item).strip()
            for item in preference_layer.data.get("disliked_topics", [])
            if str(item).strip()
        ]
        report.likes_before = len(interests_raw)
        report.dislikes_before = len(dislikes_raw)

        before_snapshot = {
            "interests": [dict(item) for item in interests_raw],
            "disliked_topics": list(dislikes_raw),
        }

        # ── Stage 0: rule layer — same name, different category ────────────
        interests, rule_merges = self._rule_merge_exact_names(interests_raw)
        report.rule_merges = rule_merges

        # ── Boundary slice ─────────────────────────────────────────────────
        ranked = sorted(interests, key=lambda item: _coerce_float(item.get("weight")), reverse=True)
        like_slice_names = [str(item["name"]) for item in ranked[: self._likes_boundary]]

        # ── Stage 1: clustering ────────────────────────────────────────────
        state = self._load_state()
        no_merge: set[str] = set(str(p) for p in state.get("no_merge_pairs", []))
        like_clusters = await self._cluster(like_slice_names, scope="likes")
        dislike_clusters = await self._cluster(dislikes_raw, scope="dislikes")
        clusters = [
            cluster
            for cluster in (*like_clusters, *dislike_clusters)
            if self._has_unjudged_pair(cluster, no_merge)
        ]
        report.clusters_sent = len(clusters)

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

        # ── Stage 3: apply ─────────────────────────────────────────────────
        rename_map: dict[str, str] = {}
        for op in valid_ops:
            members = _as_str_list(op.get("members"))
            canonical = str(op.get("canonical", ""))
            if op["scope"] == "likes":
                interests = self._apply_like_merge(interests, members, canonical)
            else:
                dislikes_raw = self._apply_dislike_merge(dislikes_raw, members, canonical)
            for member in members:
                if member != canonical:
                    rename_map[member] = canonical
            report.merges.append(
                {
                    "scope": op["scope"],
                    "members": members,
                    "canonical": canonical,
                    "reason": str(op.get("reason", "")),
                }
            )

        report.likes_after = len(interests)
        report.dislikes_after = len(dislikes_raw)

        if dry_run:
            return report

        changed = bool(rule_merges or valid_ops)
        if changed:
            preference_layer.data["interests"] = interests
            preference_layer.data["disliked_topics"] = dislikes_raw
            preference_layer.save()
            self._rebuild_profile_tree(preference_layer.data)
            overrides_before = self._remap_overrides(rename_map)
            self._write_run_record(report, before_snapshot, rename_map, overrides_before)
            self._append_changelog(report, current)

        # Record judged-distinct pairs so future runs skip them, and
        # advance run bookkeeping even on no-op runs.
        for cluster in judged_clusters:
            survivors = self._cluster_survivors(cluster, valid_ops)
            for i, a in enumerate(survivors):
                for b in survivors[i + 1 :]:
                    no_merge.add(_pair_key(a, b))
        state["no_merge_pairs"] = sorted(no_merge)[:_NO_MERGE_PAIRS_CAP]
        state["last_run_at"] = current.isoformat()
        state["last_input_digest"] = self._input_digest()
        if changed:
            state["last_applied_run_id"] = report.run_id
        self._save_state(state)
        return report

    # -- Stage 0: rule merges ---------------------------------------------------

    def _rule_merge_exact_names(
        self, interests: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Merge entries whose normalized names are identical (category differs)."""
        by_name: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        merges: list[str] = []
        for item in interests:
            key = _normalize_name(str(item["name"]))
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = item
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
            by_name[key] = merged
            merges.append(
                f"同名合并: {winner.get('name')} "
                f"({winner.get('category')} ∪ {loser.get('category')})"
            )
        return [by_name[key] for key in order], merges

    # -- Stage 1: clustering ------------------------------------------------------

    async def _cluster(self, names: list[str], *, scope: str) -> list[_Cluster]:
        unique_names = list(dict.fromkeys(name for name in names if name))
        if len(unique_names) < 2:
            return []
        prefix = "L" if scope == "likes" else "D"

        groups: list[list[str]] = []
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
            assigned: set[str] = set()
            for i, name in enumerate(embeddable):
                if name in assigned:
                    continue
                group = [name]
                assigned.add(name)
                for other in embeddable[i + 1 :]:
                    if other in assigned:
                        continue
                    if _cosine(vectors[name], vectors[other]) >= self._similarity_threshold:
                        group.append(other)
                        assigned.add(other)
                if len(group) >= 2:
                    groups.append(group)
        else:
            # Fallback without embeddings: substring containment grouping.
            assigned = set()
            for i, name in enumerate(unique_names):
                if name in assigned:
                    continue
                norm = _normalize_name(name)
                group = [name]
                for other in unique_names[i + 1 :]:
                    if other in assigned:
                        continue
                    other_norm = _normalize_name(other)
                    if norm and other_norm and (norm in other_norm or other_norm in norm):
                        group.append(other)
                        assigned.add(other)
                if len(group) >= 2:
                    assigned.add(name)
                    groups.append(group)

        return [
            _Cluster(cluster_id=f"{prefix}{idx + 1}", scope=scope, members=group)
            for idx, group in enumerate(groups)
        ]

    @staticmethod
    def _has_unjudged_pair(cluster: _Cluster, no_merge: set[str]) -> bool:
        members = cluster.members
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if _pair_key(a, b) not in no_merge:
                    return True
        return False

    # -- Stage 2: LLM judgement ----------------------------------------------------

    async def _judge(self, clusters: list[_Cluster]) -> dict[str, list[dict[str, Any]]]:
        if self._llm_service is None:
            return {}
        preference_layer = self._memory.get_layer("preference")
        weight_by_name = {
            str(item.get("name", "")): _coerce_float(item.get("weight"))
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict)
        }
        likes_payload: list[dict[str, object]] = [
            {
                "cluster_id": c.cluster_id,
                "members": [
                    {"name": name, "weight": round(weight_by_name.get(name, 0.0), 3)}
                    for name in c.members
                ],
            }
            for c in clusters
            if c.scope == "likes"
        ]
        dislikes_payload: list[dict[str, object]] = [
            {"cluster_id": c.cluster_id, "members": list(c.members)}
            for c in clusters
            if c.scope == "dislikes"
        ]
        messages = build_profile_consolidation_prompt(
            likes_clusters=likes_payload,
            dislikes_clusters=dislikes_payload,
        )
        response = await self._llm_service.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            temperature=0.2,
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.consolidation",
        )
        parsed = parse_llm_json_tolerant(response.content)
        if not isinstance(parsed, dict):
            raise ValueError("consolidation response is not a JSON object")
        ops_by_cluster: dict[str, list[dict[str, Any]]] = {}
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
        member_set = set(cluster.members)
        covered: list[str] = []
        for op in ops:
            kind = str(op.get("op", ""))
            if kind == "keep":
                name = str(op.get("name", ""))
                if name not in member_set:
                    return f"keep references unknown member: {name!r}"
                covered.append(name)
            elif kind == "merge":
                members = [str(m) for m in op.get("members", []) if str(m)]
                if len(members) < 2:
                    return "merge with fewer than 2 members"
                unknown = [m for m in members if m not in member_set]
                if unknown:
                    return f"merge references unknown members: {unknown!r}"
                canonical = str(op.get("canonical", "")).strip()
                problem = self._validate_canonical(canonical, members, scope=cluster.scope)
                if problem:
                    return problem
                covered.extend(members)
            else:
                return f"unknown op kind: {kind!r}"
        if sorted(covered) != sorted(cluster.members):
            return "ops do not cover each member exactly once"
        return ""

    @staticmethod
    def _validate_canonical(canonical: str, members: list[str], *, scope: str) -> str:
        if not canonical:
            return "merge without canonical"
        if _normalize_name(canonical) in {_normalize_name(b) for b in _BANNED_GENERIC_CANONICALS}:
            return f"canonical is a banned umbrella term: {canonical!r}"
        shortest = min(len(m) for m in members)
        # A canonical dramatically shorter than every member is the
        # signature of upward generalization ("低质内容" <- long specific
        # avoid-patterns). Members themselves are exempt (picking the
        # shortest member as canonical is fine for likes).
        if canonical not in members and scope == "dislikes" and len(canonical) < shortest * 0.5:
            return f"canonical looks over-generalized for dislikes: {canonical!r}"
        return ""

    @staticmethod
    def _cluster_survivors(cluster: _Cluster, valid_ops: list[dict[str, object]]) -> list[str]:
        """Names that remain distinct after this cluster's ops (keeps + canonicals)."""
        merged_away: set[str] = set()
        canonicals: list[str] = []
        for op in valid_ops:
            if op.get("cluster_id") != cluster.cluster_id:
                continue
            members = _as_str_list(op.get("members"))
            canonical = str(op.get("canonical", ""))
            canonicals.append(canonical)
            merged_away.update(m for m in members if m != canonical)
        kept = [name for name in cluster.members if name not in merged_away]
        return list(dict.fromkeys([*kept, *canonicals]))

    # -- Stage 3: apply --------------------------------------------------------------

    @staticmethod
    def _apply_like_merge(
        interests: list[dict[str, Any]], members: list[str], canonical: str
    ) -> list[dict[str, Any]]:
        member_set = set(members)
        # An existing entry already named `canonical` (outside the
        # cluster) folds into the merge too, whatever its position —
        # otherwise the rename would create a duplicate name.
        involved_names = member_set | {canonical}
        involved = [item for item in interests if str(item.get("name")) in involved_names]
        if not any(str(item.get("name")) in member_set for item in involved):
            return interests
        base = max(involved, key=lambda item: _coerce_float(item.get("weight")))
        merged = dict(base)
        merged["name"] = canonical
        merged["weight"] = max(_coerce_float(item.get("weight")) for item in involved)
        merged["first_seen"] = _earliest(*(item.get("first_seen") for item in involved))
        merged["last_seen"] = _latest(*(item.get("last_seen") for item in involved))

        result: list[dict[str, Any]] = []
        inserted = False
        for item in interests:
            if str(item.get("name")) in involved_names:
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
        from openbiliclaw.soul.profile import OnionProfile

        soul_layer = self._memory.get_layer("soul")
        if not soul_layer.data:
            return
        try:
            profile = OnionProfile.from_dict(dict(soul_layer.data))
            profile.populate_from_flat_preference(preference_data)
            soul_layer.data.clear()
            soul_layer.data.update(profile.to_dict())
            soul_layer.save()
            sync = getattr(self._memory, "sync_profile_files", None)
            if callable(sync):
                sync(profile)
        except Exception:
            logger.exception("Failed to rebuild profile tree after consolidation")

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
        preference_layer.data["interests"] = [
            dict(item) for item in before.get("interests", []) if isinstance(item, dict)
        ]
        preference_layer.data["disliked_topics"] = _as_str_list(before.get("disliked_topics"))
        preference_layer.save()
        self._rebuild_profile_tree(preference_layer.data)

        overrides_before = record.get("overrides_before")
        if isinstance(overrides_before, dict):
            saver = getattr(self._memory, "save_profile_overrides", None)
            if callable(saver):
                try:
                    from openbiliclaw.soul.overrides import ProfileOverrides

                    saver(ProfileOverrides.from_dict(overrides_before))
                except Exception:
                    logger.exception("Failed to restore profile overrides for %s", run_id)

        # Pin the rolled-back merges as known-distinct so the next run
        # doesn't redo them.
        state = self._load_state()
        no_merge = set(str(p) for p in state.get("no_merge_pairs", []))
        for merge in record.get("merges", []):
            if not isinstance(merge, dict):
                continue
            names = [*_as_str_list(merge.get("members")), str(merge.get("canonical", ""))]
            names = [n for n in dict.fromkeys(names) if n]
            for i, a in enumerate(names):
                for b in names[i + 1 :]:
                    no_merge.add(_pair_key(a, b))
        state["no_merge_pairs"] = sorted(no_merge)[:_NO_MERGE_PAIRS_CAP]
        state["last_input_digest"] = ""
        self._save_state(state)

        try:
            with (self._data_dir / _CHANGELOG_FILENAME).open("a", encoding="utf-8") as fh:
                fh.write(f"\n## 画像整理回滚 {run_id}（{datetime.now().isoformat()}）\n")
        except Exception:
            logger.debug("Failed to append revert changelog", exc_info=True)
        return True

    # -- Persistence -------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        return self._data_dir / _STATE_FILENAME if self._data_dir else None

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
            (str(item.get("name", "")), round(_coerce_float(item.get("weight")), 3))
            for item in preference_layer.data.get("interests", [])
            if isinstance(item, dict)
        ]
        ranked = sorted(interests, key=lambda pair: pair[1], reverse=True)
        boundary_names = sorted(name for name, _ in ranked[: self._likes_boundary])
        dislikes = sorted(str(item) for item in preference_layer.data.get("disliked_topics", []))
        payload = json.dumps([boundary_names, dislikes], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _write_run_record(
        self,
        report: ConsolidationReport,
        before_snapshot: dict[str, object],
        rename_map: dict[str, str],
        overrides_before: dict[str, object] | None = None,
    ) -> None:
        if self._data_dir is None:
            return
        runs_dir = self._data_dir / _RUNS_DIRNAME
        try:
            runs_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "run_id": report.run_id,
                "before": before_snapshot,
                "rule_merges": report.rule_merges,
                "merges": report.merges,
                "rename_map": rename_map,
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
