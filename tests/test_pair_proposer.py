"""Exact small-pool oracle checks for the offline pair proposal algorithm."""

from __future__ import annotations

import itertools
import random
from collections import Counter

from openbiliclaw.recommendation.director import (
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    ScreenConstraints,
    TopicQuota,
)
from openbiliclaw.recommendation.director.proposer import (
    eligible_cards,
    feasible_completion,
    largest_feasible_count,
    propose_pairs,
    validate_screen,
)


def _request(
    topics: tuple[str, ...] = ("ai", "ai", "ai", "craft", "outdoors", "music"),
    *,
    target: int = 6,
    quotas: tuple[TopicQuota, ...] = (),
    excluded: tuple[str, ...] = (),
) -> CompositionRequest:
    return CompositionRequest(
        request_id="synthetic-request",
        session=CompositionSession(
            session_ref="synthetic-session",
            stream_ref="synthetic-stream",
            state_revision=1,
            feedback_cursor="synthetic-feedback",
            configuration_revision=1,
        ),
        candidate_set_ref="synthetic-candidates",
        candidate_revision=1,
        feature_schema_version="synthetic-features.v1",
        direction_ref="synthetic-direction",
        direction="Explore a few different topics",
        cards=tuple(
            CompositionCard(
                ref=f"c{index}",
                item_key=f"bilibili:{index}",
                title=f"Synthetic item {index}",
                topic_group=topic,
                baseline_score=float(len(topics) - index),
            )
            for index, topic in enumerate(topics)
        ),
        constraints=ScreenConstraints(
            version="synthetic-constraints.v1", topic_quotas=quotas, excluded_item_keys=excluded
        ),
        target_count=target,
    )


def _oracle(
    request: CompositionRequest, prefix: tuple[str, ...], target: int
) -> tuple[str, ...] | None:
    """Independent brute force only in tests, never used in the production module."""
    cards = tuple(
        card
        for card in request.cards
        if card.eligible and card.item_key not in request.constraints.excluded_item_keys
    )
    by_ref = {card.ref: card for card in cards}
    if len(prefix) != len(set(prefix)) or any(ref not in by_ref for ref in prefix):
        return None
    if len(prefix) > target:
        return None
    remaining = [card.ref for card in cards if card.ref not in prefix]
    for suffix in itertools.combinations(remaining, target - len(prefix)):
        refs = prefix + suffix
        counts = Counter(by_ref[ref].topic_group for ref in refs)
        if all(
            quota.minimum <= counts[quota.topic_group] <= quota.maximum
            for quota in request.constraints.topic_quotas
        ):
            return refs
    return None


def test_each_alternative_keeps_rare_topic_supply_feasible() -> None:
    request = _request(
        target=4,
        quotas=(
            TopicQuota(topic_group="craft", minimum=1),
            TopicQuota(topic_group="outdoors", minimum=1),
            TopicQuota(topic_group="music", minimum=1),
        ),
    )
    options = propose_pairs(request, (), 4)
    assert options
    # Top-utility ai+ai is locally legal but leaves only two positions for
    # three required rare topics, so it must not be shown to the model.
    assert all(option.card_refs != ("c0", "c1") for option in options)
    for option in options:
        assert _oracle(request, option.card_refs, 4) is not None
    assert options[0].card_refs == ("c0", "c3")


def test_requested_minimum_never_relaxed_for_shortage() -> None:
    request = _request(("ai", "ai"), target=4, quotas=(TopicQuota(topic_group="music", minimum=1),))
    assert largest_feasible_count(request) is None
    assert propose_pairs(request, (), 4) == ()


def test_shortage_and_empty_inventory_counts() -> None:
    assert largest_feasible_count(_request(("ai",), target=4)) == 1
    assert largest_feasible_count(_request((), target=4)) == 0
    request = _request(
        ("ai", "ai", "ai", "craft"), target=4, quotas=(TopicQuota(topic_group="ai", maximum=1),)
    )
    assert largest_feasible_count(request) == 2


def test_canonical_exclusions_and_eligibility_apply_at_every_check() -> None:
    request = _request(excluded=("bilibili:0",))
    cards = tuple(
        card.model_copy(update={"eligible": False}) if card.ref == "c1" else card
        for card in request.cards
    )
    request = request.model_copy(update={"cards": cards})
    assert len(eligible_cards(request)) == 4
    assert largest_feasible_count(request) == 4
    assert not validate_screen(request, ("c0", "c1"), 2)
    assert all(
        set(option.card_refs).isdisjoint({"c0", "c1"}) for option in propose_pairs(request, (), 4)
    )


def test_baseline_stable_under_input_shuffle_and_utility_ties() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(update={"baseline_score": 1.0}) for card in request.cards
            )
        }
    )
    reordered = request.model_copy(update={"cards": tuple(reversed(request.cards))})
    assert eligible_cards(request) == eligible_cards(reordered)
    assert propose_pairs(request, (), 6) == propose_pairs(reordered, (), 6)


def test_pair_menu_is_bounded_and_includes_tail_topic_representative() -> None:
    request = _request(("head",) * 38 + ("rare-a", "rare-b"), target=6)
    options = propose_pairs(request, (), 6)
    assert len(options) == 16
    assert options[0].card_refs == ("c0", "c1")
    assert {"c38", "c39"} <= {ref for option in options for ref in option.card_refs}
    assert len({option.card_refs for option in options}) == len(options)
    assert all(len(option.card_refs) == 2 for option in options)


def test_prefix_removed_and_only_final_odd_tail_is_singleton() -> None:
    request = _request(target=5)
    prefix: tuple[str, ...] = ()
    for width in (2, 2, 1):
        options = propose_pairs(request, prefix, 5)
        assert all(len(option.card_refs) == width for option in options)
        assert all(not set(prefix) & set(option.card_refs) for option in options)
        prefix += options[0].card_refs
    assert validate_screen(request, prefix, 5)
    assert not validate_screen(request, prefix, 6)
    assert propose_pairs(request, prefix, 5) == ()


def test_invalid_prefixes_rejected_without_relaxing_constraints() -> None:
    request = _request(target=4, quotas=(TopicQuota(topic_group="ai", maximum=1),))
    for prefix in (("missing",), ("c0", "c0"), ("c0", "c1")):
        assert feasible_completion(eligible_cards(request), request.constraints, prefix, 4) is None
        assert not validate_screen(request, prefix, len(prefix))
        assert propose_pairs(request, prefix, 4) == ()


def test_exact_feasibility_matches_brute_force_small_pool_oracle() -> None:
    rng = random.Random(419)
    for _ in range(120):
        topics = tuple(rng.choice(("a", "b", "c")) for _ in range(rng.randrange(9)))
        quotas = tuple(
            TopicQuota(topic_group=topic, minimum=lower, maximum=upper)
            for topic in ("a", "b", "c")
            for lower in (rng.randrange(3),)
            for upper in (rng.randrange(lower, 5),)
        )
        request = _request(topics, target=rng.randrange(1, 9), quotas=quotas)
        expected_size = next(
            (
                count
                for count in range(request.target_count, -1, -1)
                if _oracle(request, (), count) is not None
            ),
            None,
        )
        assert largest_feasible_count(request) == expected_size
        if expected_size is None:
            continue
        prefix: tuple[str, ...] = ()
        while len(prefix) < expected_size:
            options = propose_pairs(request, prefix, expected_size)
            assert options
            for option in options:
                assert _oracle(request, prefix + option.card_refs, expected_size) is not None
            prefix += rng.choice(options).card_refs
        assert validate_screen(request, prefix, expected_size)
