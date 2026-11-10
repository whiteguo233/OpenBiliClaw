"""Golden-contract tests for the cognition compact-v1 prompt views."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.prompts import (
    build_awareness_prompt,
    build_awareness_with_confusions_prompt,
    build_insight_prompt,
    build_preference_analysis_prompt,
)
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.event_prompt_views import (
    MALFORMED_METADATA_MAX_CHARS,
    build_cognition_event_view_v1,
    normalize_cognition_input_view,
)
from openbiliclaw.soul.insight_analyzer import InsightAnalyzer
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
from openbiliclaw.soul.profile_views import build_cognition_profile_view_v1

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cognition_prompt_view_v1.json"


def _fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _by_id(events: list[dict[str, object]], event_id: int) -> dict[str, object]:
    return next(event for event in events if event.get("id") == event_id)


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _all_mapping_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            keys.add(str(raw_key))
            keys.update(_all_mapping_keys(item))
    elif isinstance(value, list | tuple):
        for item in value:
            keys.update(_all_mapping_keys(item))
    return keys


def test_cognition_event_view_v1_preserves_cross_source_semantic_evidence() -> None:
    fixture = _fixture()
    source = fixture["events"]
    before = copy.deepcopy(source)

    first = build_cognition_event_view_v1(source)
    second = build_cognition_event_view_v1(source)
    rendered = first.as_list()

    assert source == before
    assert first == second
    assert [event["id"] for event in rendered] == list(range(101, 110))
    assert first.removed_field_count == 18
    assert first.malformed_metadata_count == 1
    assert first.source_characters == 4593
    assert first.rendered_characters == 3351
    assert first.rendered_characters <= first.source_characters * 0.75

    favorite = _by_id(rendered, 101)
    favorite_metadata = _mapping(favorite["metadata"])
    assert favorite["inferred_satisfaction"] == "positive"
    assert favorite_metadata["bvid"] == "BV1ASYNC"
    assert favorite_metadata["up_name"] == "系统实验室"
    assert favorite_metadata["unknown_semantic_label"] == "长期工程项目"

    dislike = _by_id(rendered, 102)
    dislike_metadata = _mapping(dislike["metadata"])
    assert dislike["inferred_satisfaction"] == "negative"
    assert dislike_metadata["feedback_type"] == "dislike"
    assert dislike_metadata["reaction"] == "thumbs_down"
    assert dislike_metadata["feedback_note"] == "标题夸张且没有证据"

    comment = _by_id(rendered, 103)
    comment_metadata = _mapping(comment["metadata"])
    assert comment["inferred_satisfaction"] == "neutral"
    assert comment_metadata["comment_kind"] == "comment"
    assert comment_metadata["comment_text"] == "路线不错,但旁白有点吵"
    assert comment_metadata["unknown_semantic_mood"] == "有保留的认可"

    retraction_metadata = _mapping(_by_id(rendered, 104)["metadata"])
    assert retraction_metadata["retracted"] is True
    assert retraction_metadata["retracted_action"] == "like"
    assert retraction_metadata["signal_strength"] == 0.2

    xhs_metadata = _mapping(_by_id(rendered, 105)["metadata"])
    assert xhs_metadata["source_platform"] == "xiaohongshu"
    assert xhs_metadata["content_id"] == "xhs-note-105"
    assert xhs_metadata["author"] == "豆子老师"

    dwell_metadata = _mapping(_by_id(rendered, 106)["metadata"])
    assert dwell_metadata["source_platform"] == "youtube"
    assert dwell_metadata["author"] == "Kernel Notes"
    assert dwell_metadata["watch_seconds"] == 2520
    assert dwell_metadata["video_duration_seconds"] == 2700
    assert dwell_metadata["completion_ratio"] == 0.9333

    search = _by_id(rendered, 107)
    assert search["search_query"] == "分布式任务幂等重试"
    assert _mapping(search["metadata"])["query"] == "分布式任务幂等重试"

    dialogue = _by_id(rendered, 108)
    assert "长期复用" in str(dialogue["user_message"])
    assert _mapping(dialogue["metadata"])["unknown_semantic_commitment"] == "明确的长期目标"


def test_cognition_event_view_v1_uses_a_narrow_denylist_and_bounds_malformed_metadata() -> None:
    fixture = _fixture()
    rendered = build_cognition_event_view_v1(fixture["events"]).as_list()
    serialized = json.dumps(rendered, ensure_ascii=False, sort_keys=True)

    for internal_key in (
        "browser_context",
        "browser_state",
        "classifier_reason",
        "dom_snapshot",
        "event_namespace",
        "idempotency_key",
        "ingest_idempotency_key",
        "page_html",
        "projection_namespace",
        "projection_owner",
        "raw_dom",
        "raw_html",
        "satisfaction_reason",
    ):
        assert f'"{internal_key}"' not in serialized

    # URLs are redundant beside a title/context or a source identity, but remain
    # as the only useful identity on an otherwise opaque click.
    assert "url" not in _by_id(rendered, 101)
    assert "url" not in _by_id(rendered, 105)
    opaque = _by_id(rendered, 109)
    assert opaque["url"] == "https://example.com/sole-identity/109"
    assert isinstance(opaque["metadata"], str)
    assert len(opaque["metadata"]) == MALFORMED_METADATA_MAX_CHARS
    assert str(opaque["metadata"]).startswith("{broken metadata payload")

    type_only = {
        "type": "view",
        "title": "类型别名",
        "metadata": {"source_platform": "web", "future_signal": "keep-me"},
    }
    reordered = dict(reversed(list(type_only.items())))
    assert build_cognition_event_view_v1([type_only]).as_list() == (
        build_cognition_event_view_v1([reordered]).as_list()
    )
    assert build_cognition_event_view_v1([type_only]).as_list()[0] == {
        "event_type": "view",
        "metadata": {"future_signal": "keep-me", "source_platform": "web"},
        "title": "类型别名",
    }

    without_metadata = build_cognition_event_view_v1(
        [{"event_type": "view", "title": "没有 metadata"}]
    )
    assert without_metadata.as_list() == [{"event_type": "view", "title": "没有 metadata"}]
    assert without_metadata.malformed_metadata_count == 0

    nested = build_cognition_event_view_v1(
        [
            {
                "event_type": "view",
                "metadata": {
                    "future_semantics": {
                        "raw_dom": "internal-only",
                        "meaning": "keep-me",
                    }
                },
            }
        ]
    ).as_list()[0]
    assert nested["metadata"] == {"future_semantics": {"meaning": "keep-me"}}


def test_cognition_profile_view_v1_removes_duplicates_and_bookkeeping_without_mutation() -> None:
    fixture = _fixture()
    soul = fixture["soul_profile"]
    preference = fixture["preference"]
    soul_before = copy.deepcopy(soul)
    preference_before = copy.deepcopy(preference)

    first = build_cognition_profile_view_v1(
        soul_profile=soul,
        preference_summary=preference,
    )
    second = build_cognition_profile_view_v1(
        soul_profile=soul,
        preference_summary=preference,
    )

    assert first == second
    assert soul == soul_before
    assert preference == preference_before
    assert first.stable_soul["personality_portrait"] == soul["personality_portrait"]
    assert "interest" not in first.stable_soul
    assert "recent_awareness" not in first.stable_soul
    assert "active_insights" not in first.stable_soul

    internal_keys = {
        "_init_cognition_context",
        "awareness_candidates",
        "created_at",
        "insight_candidates",
        "profile_ready",
        "updated_at",
        "version",
    }
    assert not (_all_mapping_keys(first.stable_soul) & internal_keys)
    assert not (_all_mapping_keys(first.stable_preference) & internal_keys)
    assert not (_all_mapping_keys(first.volatile_cognition()) & internal_keys)

    interests = first.stable_preference["interests"]
    assert isinstance(interests, list)
    assert [item["name"] for item in interests] == [
        "分布式系统",
        "异步任务",
        "操作系统",
        "长期项目",
        "手冲咖啡",
    ]
    assert interests[0]["state"] == "active"
    assert interests[0]["evidence_count"] == 23
    assert interests[0]["first_seen"] == "2026-06-01"
    assert interests[0]["last_seen"] == "2026-08-01"
    assert interests[-1]["state"] == "trial"
    assert first.stable_preference["disliked_topics"] == preference["disliked_topics"]
    speculative = first.stable_preference["speculative_interests"]
    assert isinstance(speculative, list)
    assert [item["domain"] for item in speculative] == ["可靠性工程"]
    assert speculative[0]["state"] == "active"

    volatile = first.volatile_cognition()
    awareness = volatile["recent_awareness"]
    insights = volatile["active_insights"]
    assert isinstance(awareness, list)
    assert isinstance(insights, list)
    assert len(awareness) == 2
    assert len(insights) == 2
    assert awareness[0]["date"] == "2026-07-31"
    assert awareness[0]["source_event_ids"] == [90, 94]
    assert insights[0]["user_verdict"] == "confirmed"


def test_cognition_profile_view_v1_does_not_cap_active_or_negative_evidence() -> None:
    active = [
        {
            "name": f"interest-{index}",
            "weight": 1 - index / 1000,
            "state": "active",
            "evidence_count": index + 1,
        }
        for index in range(180)
    ]
    negatives = [f"avoid-{index}" for index in range(160)]
    profile = build_cognition_profile_view_v1(
        preference_summary={
            "interests": active,
            "disliked_topics": negatives,
            "speculative_interests": [
                {"domain": "keep", "state": "active"},
                {"domain": "drop", "state": "archived"},
            ],
        }
    )

    assert len(profile.stable_preference["interests"]) == 180
    assert profile.stable_preference["disliked_topics"] == negatives
    assert profile.stable_preference["speculative_interests"] == [
        {"domain": "keep", "state": "active"}
    ]


_ARCHIVED_LEDGER_SENTINEL = "archived-ledger"


def _preference_with_archived_ledger(preference: dict[str, Any], size: int) -> dict[str, Any]:
    """Return a preference payload whose archived ledger holds ``size`` rows."""
    payload = copy.deepcopy(preference)
    payload["archived_interests"] = [
        {
            "name": f"{_ARCHIVED_LEDGER_SENTINEL}-{index}",
            "category": "tech",
            "weight": 0.01,
            "evidence_count": 2,
            "archived_at": "2026-01-01T00:00:00",
        }
        for index in range(size)
    ]
    return payload


def test_cognition_profile_view_v1_drops_the_archived_interest_ledger() -> None:
    fixture = _fixture()
    preference = _preference_with_archived_ledger(fixture["preference"], 4)
    before = copy.deepcopy(preference)

    view = build_cognition_profile_view_v1(preference_summary=preference)

    assert "archived_interests" not in view.stable_preference
    assert "archived_interests" not in _all_mapping_keys(view.volatile_cognition())
    # Active interests still render; only the lifecycle ledger is withheld.
    interests = view.stable_preference["interests"]
    assert isinstance(interests, list)
    assert [item["name"] for item in interests][:2] == ["分布式系统", "异步任务"]
    # Persisted storage is untouched — the analyzer still merges and reactivates
    # archived topics from the stored list.
    assert preference == before


def test_cognition_prompts_never_serialize_the_archived_interest_ledger() -> None:
    fixture = _fixture()
    soul = fixture["soul_profile"]
    events = fixture["events"]
    preference = _preference_with_archived_ledger(fixture["preference"], 2048)
    before = copy.deepcopy(fixture)

    cases: dict[str, Callable[[str], list[dict[str, str]]]] = {
        "preference": lambda view: build_preference_analysis_prompt(
            events=events,
            existing_preference=preference,
            awareness_notes=soul["recent_awareness"],
            active_insights=soul["active_insights"],
            input_view=view,
        ),
        "awareness": lambda view: build_awareness_prompt(
            events=events,
            preference_summary=preference,
            soul_profile=soul,
            input_view=view,
        ),
        "awareness_confusions": lambda view: build_awareness_with_confusions_prompt(
            events=events,
            preference_summary=preference,
            soul_profile=soul,
            input_view=view,
        ),
        "insight": lambda view: build_insight_prompt(
            awareness_notes=soul["recent_awareness"],
            preference_summary=preference,
            soul_profile=soul,
            existing_hypotheses=soul["active_insights"],
            input_view=view,
        ),
    }

    for name, build in cases.items():
        compact = build("compact-v1")
        legacy = build("legacy")
        assert compact[0] == legacy[0], name
        # Both views drop the append-only ledger; only the view-specific
        # shaping differs between them.
        assert _ARCHIVED_LEDGER_SENTINEL not in compact[1]["content"], name
        assert _ARCHIVED_LEDGER_SENTINEL not in legacy[1]["content"], name

    assert fixture == before


def test_compact_cognition_prompt_bytes_are_independent_of_archive_size() -> None:
    fixture = _fixture()
    soul = fixture["soul_profile"]
    events = fixture["events"]
    no_archive = _preference_with_archived_ledger(fixture["preference"], 0)
    huge_archive = _preference_with_archived_ledger(fixture["preference"], 5000)

    small = build_awareness_with_confusions_prompt(
        events=events,
        preference_summary=no_archive,
        soul_profile=soul,
        input_view="compact-v1",
    )
    huge = build_awareness_with_confusions_prompt(
        events=events,
        preference_summary=huge_archive,
        soul_profile=soul,
        input_view="compact-v1",
    )

    # Issue #342: prompt cost must not scale with account age, so a 5,000-row
    # ledger has to render byte-identically to no ledger at all.
    assert huge == small
    assert _ARCHIVED_LEDGER_SENTINEL not in huge[1]["content"]


def test_insight_compact_view_derives_existing_hypotheses_only_when_unspecified() -> None:
    fixture = _fixture()
    soul = fixture["soul_profile"]
    preference = fixture["preference"]

    derived = build_insight_prompt(
        awareness_notes=[],
        preference_summary=preference,
        soul_profile=soul,
        existing_hypotheses=None,
        input_view="compact-v1",
    )[1]["content"]
    suppressed = build_insight_prompt(
        awareness_notes=[],
        preference_summary=preference,
        soul_profile=soul,
        existing_hypotheses=[],
        input_view="compact-v1",
    )[1]["content"]

    assert "用户可能更在意工具能否进入长期项目" in derived
    assert "用户可能更在意工具能否进入长期项目" not in suppressed


def _prompt_cases(
    fixture: dict[str, Any],
) -> list[tuple[str, Callable[..., list[dict[str, str]]], dict[str, object], float]]:
    soul = fixture["soul_profile"]
    preference = fixture["preference"]
    events = fixture["events"]
    return [
        (
            "preference",
            build_preference_analysis_prompt,
            {
                "events": events,
                "existing_preference": preference,
                "awareness_notes": soul["recent_awareness"],
                "active_insights": soul["active_insights"],
            },
            # Lifecycle evidence stays model-visible for quality; this fixture
            # still requires a material user-block reduction, while the 25%
            # token gate is decided by provider replay over real batch sizes.
            0.80,
        ),
        (
            "awareness",
            build_awareness_prompt,
            {
                "events": events,
                "preference_summary": preference,
                "soul_profile": soul,
            },
            0.70,
        ),
        (
            "awareness_confusions",
            build_awareness_with_confusions_prompt,
            {
                "events": events,
                "preference_summary": preference,
                "soul_profile": soul,
            },
            0.70,
        ),
        (
            "insight",
            build_insight_prompt,
            {
                "awareness_notes": soul["recent_awareness"],
                "preference_summary": preference,
                "soul_profile": soul,
                "existing_hypotheses": soul["active_insights"],
            },
            0.70,
        ),
    ]


@pytest.mark.parametrize("case_index", range(4))
def test_compact_prompt_builders_preserve_legacy_and_system_bytes(case_index: int) -> None:
    fixture = _fixture()
    before = copy.deepcopy(fixture)
    _name, builder, kwargs, maximum_ratio = _prompt_cases(fixture)[case_index]

    default = builder(**kwargs)
    explicit_legacy = builder(**kwargs, input_view="legacy")
    compact = builder(**kwargs, input_view="compact-v1")
    compact_again = builder(**kwargs, input_view="compact-v1")

    assert default == explicit_legacy
    assert compact == compact_again
    assert compact[0] == explicit_legacy[0]
    assert len(compact[1]["content"]) <= len(explicit_legacy[1]["content"]) * maximum_ratio
    assert fixture == before


def test_compact_prompt_blocks_are_stable_to_volatile_and_keep_retraction_marker() -> None:
    fixture = _fixture()
    cases = _prompt_cases(fixture)
    expected_tags = {
        "preference": [
            "<existing_preference>",
            "<recent_awareness>",
            "<active_insights>",
            "<event_batch>",
        ],
        "awareness": [
            "<soul_profile>",
            "<preference_summary>",
            "<recent_awareness>",
            "<active_insights>",
            "<recent_events>",
        ],
        "awareness_confusions": [
            "<soul_profile>",
            "<preference_summary>",
            "<recent_awareness>",
            "<active_insights>",
            "<recent_events>",
        ],
        "insight": [
            "<soul_profile>",
            "<preference_summary>",
            "<existing_hypotheses>",
            "<awareness_notes>",
        ],
    }

    for name, builder, kwargs, _maximum_ratio in cases:
        user_prompt = builder(**kwargs, input_view="compact-v1")[1]["content"]
        positions = [user_prompt.index(tag) for tag in expected_tags[name]]
        assert positions == sorted(positions)
        assert "_init_cognition_context" not in user_prompt
        assert '"interest"' not in user_prompt
        if name != "insight":
            assert "raw_dom" not in user_prompt
            assert "(已撤销)" in user_prompt


@pytest.mark.parametrize(
    "value, expected",
    [
        ("legacy", "legacy"),
        (" LEGACY ", "legacy"),
        ("compact-v1", "compact-v1"),
        ("COMPACT-V1", "compact-v1"),
        ("", "legacy"),
    ],
)
def test_normalize_cognition_input_view(value: str, expected: str) -> None:
    assert normalize_cognition_input_view(value) == expected


def test_cognition_prompt_builders_reject_unknown_view() -> None:
    fixture = _fixture()
    for _name, builder, kwargs, _maximum_ratio in _prompt_cases(fixture):
        with pytest.raises(ValueError, match="compact-v1"):
            builder(**kwargs, input_view="compact-v2")


class _CapturingStructuredService:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, str]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "caller": caller,
            }
        )
        return LLMResponse(content=self.content, provider="fixture")


@pytest.mark.asyncio
async def test_cognition_analyzers_thread_compact_view_to_their_builders() -> None:
    event = {
        "id": 1,
        "event_type": "favorite",
        "title": "可靠系统",
        "context": "收藏了可靠系统教程",
        "metadata": {
            "source_platform": "bilibili",
            "bvid": "BV1",
            "raw_dom": "internal-only",
        },
    }
    preference = {
        "interests": [{"name": "可靠系统", "state": "active", "created_at": "old"}],
        "_init_cognition_context": {"awareness_candidates": ["internal-only"]},
    }
    soul = {
        "personality_portrait": "重视证据",
        "interest": {"likes": ["duplicated-interest"]},
        "version": 7,
    }

    awareness_service = _CapturingStructuredService('{"notes": [], "confusions": []}')
    awareness = AwarenessAnalyzer(
        awareness_service,
        confusions_prompt_view="compact-v1",
    )
    await awareness.analyze_with_confusions(
        events=[event],
        preference=preference,
        soul_profile=soul,
    )

    insight_service = _CapturingStructuredService("[]")
    insight = InsightAnalyzer(insight_service, cognition_prompt_view="compact-v1")
    await insight.analyze(awareness_notes=[], preference=preference, soul_profile=soul)

    preference_service = _CapturingStructuredService("{}")
    preference_analyzer = PreferenceAnalyzer(
        preference_service,
        cognition_prompt_view="compact-v1",
    )
    await preference_analyzer.analyze_events(
        events=[event],
        existing_preference=preference,
    )

    for service in (awareness_service, insight_service, preference_service):
        user_input = service.calls[0]["user_input"]
        assert "internal-only" not in user_input
        assert "duplicated-interest" not in user_input
        assert '"version"' not in user_input


@pytest.mark.asyncio
async def test_awareness_compact_rollout_does_not_change_ungated_plain_builder() -> None:
    event = {
        "id": 1,
        "event_type": "view",
        "title": "可靠系统",
        "metadata": {"raw_dom": "plain-awareness-only", "bvid": "BV1"},
    }
    service = _CapturingStructuredService("[]")
    analyzer = AwarenessAnalyzer(
        service,
        plain_prompt_view="legacy",
        confusions_prompt_view="compact-v1",
    )

    await analyzer.analyze(events=[event], preference={}, soul_profile={})
    await analyzer.analyze_with_confusions(events=[event], preference={}, soul_profile={})

    assert service.calls[0]["caller"] == "soul.awareness"
    assert "plain-awareness-only" in service.calls[0]["user_input"]
    assert service.calls[1]["caller"] == "soul.awareness_confusions"
    assert "plain-awareness-only" not in service.calls[1]["user_input"]


def test_cognition_analyzers_reject_unknown_view_at_construction() -> None:
    service = _CapturingStructuredService("[]")
    with pytest.raises(ValueError, match="compact-v1"):
        AwarenessAnalyzer(service, plain_prompt_view="future")
    with pytest.raises(ValueError, match="compact-v1"):
        AwarenessAnalyzer(service, confusions_prompt_view="future")
    with pytest.raises(ValueError, match="compact-v1"):
        InsightAnalyzer(service, cognition_prompt_view="future")
    with pytest.raises(ValueError, match="compact-v1"):
        PreferenceAnalyzer(service, cognition_prompt_view="future")
