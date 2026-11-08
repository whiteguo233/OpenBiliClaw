"""Bounded proposals with exact completion checks for disjoint topic quotas.

This deliberately supports a small constraint family. Adding overlapping
author/platform/style constraints requires a new feasibility algorithm and
policy version, not another check on the current pair alone.
"""

from __future__ import annotations

from collections import Counter

from .models import CompositionCard, CompositionRequest, PairAlternative, ScreenConstraints

# Engineering work/output caps on an already bounded 40-card shortlist, not
# calibrated recommendation thresholds. The full completion witness is always
# included before this exploratory proposal budget is spent.
_MAX_ALTERNATIVES = 16
_MAX_PAIR_PROBES = 256


def eligible_cards(request: CompositionRequest) -> tuple[CompositionCard, ...]:
    """Apply supplied hard exclusions and use a stable utility/ref ordering."""
    excluded = set(request.constraints.excluded_item_keys)
    return tuple(
        sorted(
            (card for card in request.cards if card.eligible and card.item_key not in excluded),
            key=lambda card: (-card.baseline_score, card.ref),
        )
    )


def _can_complete(
    cards: tuple[CompositionCard, ...],
    constraints: ScreenConstraints,
    prefix: tuple[str, ...],
    target_count: int,
) -> bool:
    if not 0 <= len(prefix) <= target_count or len(set(prefix)) != len(prefix):
        return False
    by_ref = {card.ref: card for card in cards}
    if any(ref not in by_ref for ref in prefix):
        return False
    chosen = set(prefix)
    used = Counter(by_ref[ref].topic_group for ref in prefix)
    available = Counter(card.topic_group for card in cards if card.ref not in chosen)
    quotas = {quota.topic_group: quota for quota in constraints.topic_quotas}
    required = 0
    capacity = 0
    for topic in used.keys() | available.keys() | quotas.keys():
        quota = quotas.get(topic)
        lower = quota.minimum if quota else 0
        upper = quota.maximum if quota else target_count
        if used[topic] > upper:
            return False
        deficit = max(0, lower - used[topic])
        room = min(available[topic], upper - used[topic])
        if deficit > room:
            return False
        required += deficit
        capacity += room
    # Exact for a partition: each card belongs to exactly one topic group, so
    # per-group deficits and remaining capacities cannot double-count supply.
    return required <= target_count - len(prefix) <= capacity


def feasible_completion(
    cards: tuple[CompositionCard, ...],
    constraints: ScreenConstraints,
    prefix: tuple[str, ...],
    target_count: int,
) -> tuple[str, ...] | None:
    """Return a deterministic full completion witness, preserving the prefix."""
    if not _can_complete(cards, constraints, prefix, target_count):
        return None
    result = prefix
    ordered = sorted(cards, key=lambda card: (-card.baseline_score, card.ref))
    while len(result) < target_count:
        for card in ordered:
            if card.ref not in result and _can_complete(
                cards, constraints, (*result, card.ref), target_count
            ):
                result = (*result, card.ref)
                break
        else:  # The exact feasibility proof above makes this an internal error.
            raise RuntimeError("feasible completion witness could not be constructed")
    return result


def largest_feasible_count(request: CompositionRequest) -> int | None:
    """Largest legal count up to target; never relax a hard minimum for shortage."""
    cards = eligible_cards(request)
    for count in range(request.target_count, -1, -1):
        if _can_complete(cards, request.constraints, (), count):
            return count
    return None


def _topic_round_robin(cards: tuple[CompositionCard, ...]) -> tuple[CompositionCard, ...]:
    groups: dict[str, list[CompositionCard]] = {}
    for card in cards:
        groups.setdefault(card.topic_group, []).append(card)
    return tuple(
        group[index]
        for index in range(max((len(group) for group in groups.values()), default=0))
        for group in groups.values()
        if index < len(group)
    )


def propose_pairs(
    request: CompositionRequest, prefix: tuple[str, ...], target_count: int
) -> tuple[PairAlternative, ...]:
    """Offer a baseline witness plus bounded topic-stratified feasible alternatives.

    Enumeration never touches the full pool. A bounded exploratory scan may
    omit alternatives, but cannot turn a feasible screen into a shortage:
    the first option always comes from a complete deterministic witness.
    """
    cards = eligible_cards(request)
    witness = feasible_completion(cards, request.constraints, prefix, target_count)
    if witness is None or len(prefix) >= target_count:
        return ()
    width = min(2, target_count - len(prefix))
    options = [witness[len(prefix) : len(prefix) + width]]
    remaining = _topic_round_robin(tuple(card for card in cards if card.ref not in prefix))

    def add(refs: tuple[str, ...]) -> None:
        if refs not in options and _can_complete(
            cards, request.constraints, (*prefix, *refs), target_count
        ):
            options.append(refs)

    if width == 1:
        for card in remaining:
            add((card.ref,))
            if len(options) >= _MAX_ALTERNATIVES:
                break
    else:
        probes = 0
        for offset in range(1, len(remaining)):
            for index, left in enumerate(remaining):
                right = remaining[(index + offset) % len(remaining)]
                add((left.ref, right.ref))
                probes += 1
                if len(options) >= _MAX_ALTERNATIVES or probes >= _MAX_PAIR_PROBES:
                    break
            if len(options) >= _MAX_ALTERNATIVES or probes >= _MAX_PAIR_PROBES:
                break
    return tuple(
        PairAlternative(option_id=f"p{index}", card_refs=refs) for index, refs in enumerate(options)
    )


def validate_screen(request: CompositionRequest, refs: tuple[str, ...], target_count: int) -> bool:
    """Validate the whole screen against the exact supplied snapshot, not live state."""
    return len(refs) == target_count and _can_complete(
        eligible_cards(request), request.constraints, refs, target_count
    )
