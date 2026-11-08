"""Short request-local card references at the Agent planner seam only.

Canonical request / stored-plan references remain untouched. Candidate order is
the caller's stable eligible order; c0 means its first card even when a different
card's canonical reference happens to be spelled c0. No title or other untrusted
content participates in lookup keys. This module translates identities, never
repairs a selection, deduplicates cards or judges semantic suitability.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Match the existing production shortlist engineering cap, not an effect or
# model-quality threshold. Revisit together with runtime shortlist changes.
_MAX_CANDIDATES = 40
_CANONICAL_REF = re.compile(r"[a-zA-Z0-9_-]{1,64}")


@dataclass(frozen=True)
class PlannerRefMap:
    """Immutable per-call alias namespace; only known aliases may be restored."""

    _canonical_refs: tuple[str, ...]

    def restore_output(self, raw: object) -> dict[str, Any]:
        """Copy parsed JSON, restoring only selected and omitted card refs.

        Unknown / malformed references and malformed traversed structures raise
        fixed-code ValueError without echoing model text. Missing fields remain
        missing for the existing output-schema validator. Duplicate / overlapping
        refs preserve their exact positions so normal plan validation rejects
        them; evidence refs, notes and other text are never remapped.
        """
        if not isinstance(raw, dict):
            raise ValueError("invalid_planner_ref_structure")
        result: dict[str, Any] = copy.deepcopy(raw)
        aliases = {f"c{index}": ref for index, ref in enumerate(self._canonical_refs)}

        def restore_refs(parent: dict[str, Any], key: str) -> None:
            if key not in parent:
                return
            values = parent[key]
            if not isinstance(values, list):
                raise ValueError("invalid_planner_ref_structure")
            restored: list[str] = []
            for value in values:
                if not isinstance(value, str) or value not in aliases:
                    raise ValueError("unknown_planner_alias")
                restored.append(aliases[value])
            parent[key] = restored

        if "screens" in result:
            screens = result["screens"]
            if not isinstance(screens, list):
                raise ValueError("invalid_planner_ref_structure")
            for screen in screens:
                if not isinstance(screen, dict):
                    raise ValueError("invalid_planner_ref_structure")
                if "pairs" not in screen:
                    continue
                pairs = screen["pairs"]
                if not isinstance(pairs, list):
                    raise ValueError("invalid_planner_ref_structure")
                for pair in pairs:
                    if not isinstance(pair, dict):
                        raise ValueError("invalid_planner_ref_structure")
                    restore_refs(pair, "card_refs")
        if "completion" in result:
            completion = result["completion"]
            if not isinstance(completion, dict):
                raise ValueError("invalid_planner_ref_structure")
            if "omissions" in completion:
                omissions = completion["omissions"]
                if not isinstance(omissions, list):
                    raise ValueError("invalid_planner_ref_structure")
                for group in omissions:
                    if not isinstance(group, dict):
                        raise ValueError("invalid_planner_ref_structure")
                    restore_refs(group, "refs")
        return result


def alias_planner_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], PlannerRefMap]:
    """Deep-copy eligible candidate payloads with c0.. aliases in original order.

    Accept at most the production 40-card shortlist. Canonical refs must match
    the existing CompositionCard identity format and be distinct. These checks
    reject a broken caller contract; they never select/filter/reorder candidates.
    The returned map owns immutable identity strings, not the caller's payloads.
    """
    if len(candidates) > _MAX_CANDIDATES:
        raise ValueError("planner_candidate_limit")
    canonical_refs: list[str] = []
    projected: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        ref = candidate.get("ref")
        if not isinstance(ref, str) or _CANONICAL_REF.fullmatch(ref) is None:
            raise ValueError("invalid_planner_candidate_refs")
        canonical_refs.append(ref)
        payload = copy.deepcopy(dict(candidate))
        payload["ref"] = f"c{index}"
        projected.append(payload)
    if len(set(canonical_refs)) != len(canonical_refs):
        raise ValueError("invalid_planner_candidate_refs")
    return projected, PlannerRefMap(tuple(canonical_refs))
