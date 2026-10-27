"""Pure Jev evaluator contract tests through a native typed provider seam."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import JevEvaluationConfig
from openbiliclaw.decision.context_budget import JevContextLimits, resolve_jev_context
from openbiliclaw.decision.models import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionEvidence,
    DecisionRequest,
    DecisionUsage,
    ScoreAnswer,
    ScoreQuestion,
)
from openbiliclaw.discovery import jev_evaluator as evaluator_module
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator

if TYPE_CHECKING:
    from collections.abc import Callable

MODEL = "jev-1.13.0"
ITEM: dict[str, object] = {
    "content_id": "example:one",
    "title": "理解计算机的基础原理",
    "description": "讲清楚概念。适合对编程感兴趣的人。",
    "body_text": "通用算法与数据结构，不依赖某个工具版本。",
    "source_platform": "example",
    "content_type": "article",
}


def native_evidence(
    request: DecisionRequest,
    *,
    choices: dict[str, str] | None = None,
    levels: dict[str, float] | None = None,
) -> DecisionEvidence:
    defaults = {
        "reason": "interest",
        "topic": "t1",
        "franchise": "u",
        "style": "deep_focus",
        "temporal_class": "evergreen",
        "validity": "none",
        "evidence": "u",
        "deadline": "u",
    }
    answers: dict[str, DecisionAnswer] = {}
    for key, question in request.questions.items():
        _, field = key.split(".")
        if isinstance(question, ChoiceQuestion):
            selected = (choices or {}).get(key, (choices or {}).get(field, defaults[field]))
            answers[key] = ChoiceAnswer(
                choice=selected,
                probabilities={option: float(option == selected) for option in question.criteria},
                confidence=0.0,  # Deliberately not a relevance/temporal-confidence score.
            )
        else:
            assert isinstance(question, ScoreQuestion)
            value = (levels or {}).get(key, 6.75 if field == "score" else 3.0)
            low = int(value)
            remainder = value - low
            probabilities = {str(i): 0.0 for i in range(len(question.criteria))}
            probabilities[str(low)] = 1.0 - remainder
            if remainder:
                probabilities[str(low + 1)] = remainder
            answers[key] = ScoreAnswer(
                score=value,
                legend={str(i): text for i, text in enumerate(question.criteria)},
                probabilities=probabilities,
                confidence=0.0,
            )
    return DecisionEvidence(
        status="OK",
        reason_code="ok",
        request_id=request.request_id,
        purpose=request.purpose,
        question_schema_version=request.question_schema_version,
        input_digest=request.input_digest,
        requested_model=MODEL,
        actual_model=MODEL,
        answers=answers,
        usage=DecisionUsage(input_tokens=123, output_tokens=10),
        latency_ms=1.0,
        attempts=1,
    )


class NativeProvider:
    def __init__(
        self,
        respond: Callable[[DecisionRequest], DecisionEvidence] = native_evidence,
    ) -> None:
        self.requests: list[DecisionRequest] = []
        self.respond = respond

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request)
        return self.respond(request)

    async def aclose(self) -> None:
        pass


def evaluator(
    provider: NativeProvider,
    **config_overrides: Any,
) -> JevContentEvaluator:
    if (
        "max_request_bytes" in config_overrides
        and "max_state_question_bytes" not in config_overrides
    ):
        config_overrides["max_state_question_bytes"] = min(
            24000, config_overrides["max_request_bytes"]
        )
    return JevContentEvaluator(
        provider,
        model=MODEL,
        config=replace(JevEvaluationConfig(), **config_overrides),
    )


def wire_sizes(request: DecisionRequest) -> tuple[int, int]:
    questions = {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in request.questions.items()
    }

    def size(selected: dict[str, Any]) -> int:
        return len(
            json.dumps(
                {"model": MODEL, "state": request.state, "questions": selected},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    return max(size({key: value}) for key, value in questions.items()), size(questions)


async def test_complete_typed_result_uses_rubric_not_distribution_confidence() -> None:
    provider = NativeProvider()
    result = await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM])
    assert len(provider.requests) == 1
    assert len(provider.requests[0].questions) == 10
    assert provider.requests[0].purpose == "EVALUATE"
    row = result[0]
    assert row is not None
    assert row["score"] == 0.75
    assert row["temporal_confidence"] == 0.75
    assert row["topic_group"] == "编程"
    assert row["style_key"] == "deep_focus"
    assert row["franchise_key"] == ""
    assert row["_jev_franchise_status"] == "unknown"
    assert row["temporal_class"] == "evergreen"
    assert row["temporal_validity_mode"] == "none"
    assert row["temporal_evidence"] == row["temporal_valid_until"] == ""
    assert "IP未知" in row["reason"]
    assert len(row["reason"]) <= 30


async def test_question_count_guard_moves_whole_candidates_without_repeat_evaluation() -> None:
    provider = NativeProvider()
    rows = await evaluator(provider, max_questions_per_request=10).evaluate(
        profile_summary={}, content_items=[ITEM] * 5
    )
    assert all(row is not None for row in rows)
    assert [len(request.questions) for request in provider.requests] == [10] * 5
    keys = [key for request in provider.requests for key in request.questions]
    assert len(keys) == len(set(keys)) == 50


async def test_candidate_text_is_shared_once_instead_of_repeated_in_evidence_question() -> None:
    provider = NativeProvider()
    sentence = "正文唯一证据：这里讨论红黑树旋转的通用算法。"
    item = {**ITEM, "body_text": sentence}
    result = await evaluator(provider).evaluate(profile_summary={}, content_items=[item])
    assert result[0] is not None
    request = provider.requests[0]
    wire = json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
    assert wire.count(sentence) == 1
    assert sentence not in json.dumps(
        request.questions["c0.evidence"].model_dump(mode="json"), ensure_ascii=False
    )


async def test_long_source_adapts_locally_preserving_conditions_and_original_input() -> None:
    provider = NativeProvider()
    condition = "但这里只讨论原理，不承诺投资收益，除非完成独立验证否则不要照做。"
    paragraphs = [f"背景材料第{i}段：讲解概念之间的关系。" for i in range(400)]
    paragraphs[201] = condition
    original = {**ITEM, "body_text": "\n".join(paragraphs)}
    before = dict(original)
    result = await evaluator(provider).evaluate(profile_summary={}, content_items=[original])
    assert result[0] is not None
    assert original == before
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert len(request.questions) == 10
    content = request.state["untrusted_candidates"][0]["content"]
    assert content["text_coverage"]["is_partial"] is True
    visible = [span["text"] for span in content["text_spans"].values()]
    assert condition in visible
    assert paragraphs[200] in visible and paragraphs[202] in visible
    assert len(visible) < 404


@pytest.mark.parametrize("guard", [{"max_candidates_per_request": 1}, {"max_question_bytes": 256}])
async def test_packing_guards_do_not_split_or_silently_shorten_a_question(
    guard: dict[str, int],
) -> None:
    provider = NativeProvider()
    rows = await evaluator(provider, **guard).evaluate(
        profile_summary={}, content_items=[ITEM, ITEM]
    )
    if "max_question_bytes" in guard:
        assert rows == [None, None]
        assert not provider.requests
    else:
        assert all(row is not None for row in rows)
        assert [len(request.questions) for request in provider.requests] == [10, 10]


async def test_more_than_choice_limit_of_explicit_conditions_stays_pending() -> None:
    provider = NativeProvider()
    result = await evaluator(provider).evaluate(
        profile_summary={},
        content_items=[
            {"body_text": "\n".join(f"条件{i}：除非验证否则不要使用。" for i in range(255))}
        ],
    )
    assert result == [None]
    assert not provider.requests


async def test_compressed_evidence_does_not_change_with_candidate_reference_width() -> None:
    target = {**ITEM, "body_text": "\n".join(f"算法背景第{i}段。" for i in range(300))}
    skipped = {"body_text": "不可拆分的单句" * 5000}
    views = []
    for prefix in ([], [skipped] * 10):
        provider = NativeProvider()
        result = await evaluator(provider).evaluate(
            profile_summary={}, content_items=[*prefix, target]
        )
        assert result[-1] is not None
        assert len(provider.requests) == 1
        views.append(provider.requests[0].state["untrusted_candidates"][0]["content"])
    assert views[0] == views[1]


async def test_deduplicated_text_keeps_actual_source_neighbors_of_conditions() -> None:
    paragraphs = [f"背景片段{i}：描述沿途风景。" for i in range(400)]
    repeated_neighbor = "独特的普通段落用于占位。"
    paragraphs[150] = paragraphs[301] = repeated_neighbor
    paragraphs[300] = "如果证据缺失，则停止结论。"
    provider = NativeProvider()
    result = await evaluator(provider).evaluate(
        profile_summary={}, content_items=[{**ITEM, "body_text": "\n".join(paragraphs)}]
    )
    assert result[0] is not None
    content = provider.requests[0].state["untrusted_candidates"][0]["content"]
    assert repeated_neighbor in [span["text"] for span in content["text_spans"].values()]


async def test_success_observability_logs_bytes_and_native_usage_without_private_text(
    caplog: Any,
) -> None:
    caplog.set_level(logging.INFO, logger="openbiliclaw.discovery.jev_evaluator")
    provider = NativeProvider()
    assert (
        await evaluator(provider, franchise_labels=["私有IP选项勿记录"]).evaluate(
            profile_summary={"dislikes": ["私有画像勿记录"]},
            content_items=[{**ITEM, "title": "私有候选标题勿记录"}],
            negative_examples=[{"title": "私有负反馈勿记录"}],
        )
    )[0] is not None
    state_question_bytes, request_bytes = wire_sizes(provider.requests[0])
    messages = [
        record.getMessage()
        for record in caplog.records
        if "Jev full evaluator context usage:" in record.getMessage()
    ]
    assert len(messages) == 1
    assert f"model={MODEL} candidates=1" in messages[0]
    assert f"request_bytes={request_bytes}" in messages[0]
    assert f"max_state_question_bytes={state_question_bytes}" in messages[0]
    assert "actual_input_tokens=123 actual_output_tokens=10" in messages[0]
    assert "byte_guard=engineering_not_token_estimate" in messages[0]
    for private_text in (
        "私有IP选项勿记录",
        "私有画像勿记录",
        "私有候选标题勿记录",
        "私有负反馈勿记录",
    ):
        assert private_text not in caplog.text


async def test_invalid_evidence_never_records_usage_as_a_valid_observation(caplog: Any) -> None:
    caplog.set_level(logging.INFO, logger="openbiliclaw.discovery.jev_evaluator")
    provider = NativeProvider(
        lambda request: native_evidence(request).model_copy(update={"input_digest": "wrong"})
    )
    assert await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM]) == [None]
    assert "Jev full evaluator context usage:" not in caplog.text


async def test_controlled_vocabulary_is_canonical_and_never_expanded_by_candidate() -> None:
    provider = NativeProvider(
        lambda request: native_evidence(request, choices={"franchise": "f0", "topic": "t0"})
    )
    items = [{**ITEM, "franchise_key": "injected-new-name", "title": "Genshin 原理探讨"}]
    row = (
        await evaluator(provider, topic_labels=["游戏"], franchise_labels=["原神"]).evaluate(
            profile_summary={}, content_items=items
        )
    )[0]
    assert row is not None and row["franchise_key"] == "原神"
    assert row["topic_group"] == "游戏"
    question = provider.requests[0].questions["c0.franchise"]
    assert isinstance(question, ChoiceQuestion)
    assert question.criteria == {"f0": "原神", "u": "No identifiable configured franchise."}


async def test_explicit_unknown_is_not_a_provider_failure_or_shared_franchise() -> None:
    provider = NativeProvider(
        lambda request: native_evidence(
            request,
            choices={"topic": "u", "style": "u", "temporal_class": "unknown"},
        )
    )
    row = (await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM]))[0]
    assert row is not None
    assert row["topic_group"] == ""
    assert row["franchise_key"] == row["style_key"] == ""
    assert row["temporal_confidence"] == 0.0
    assert row["temporal_reason"] == ""
    assert row["_jev_topic_status"] == "unknown"
    assert len(row["reason"]) <= 30


async def test_low_relevance_reason_is_empty_but_metadata_is_complete() -> None:
    provider = NativeProvider(lambda request: native_evidence(request, levels={"c0.score": 2.0}))
    row = (await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM]))[0]
    assert row is not None and row["score"] == pytest.approx(2 / 9)
    assert row["reason"] == ""
    assert row["temporal_class"] == "evergreen"


async def test_full_profile_negative_evidence_and_candidate_text_are_preserved() -> None:
    provider = NativeProvider()
    profile = {"disliked_topics": [f"明确负偏好{i}" for i in range(131)], "needs": ["休息"]}
    item = {**ITEM, "body_text": "完整正文" * 100, "tags": [f"标签{i}" for i in range(18)]}
    negatives: list[dict[str, object]] = [{"title": "不喜欢的话术" * 40, "reason": "quick_exit"}]
    result = await evaluator(provider).evaluate(
        profile_summary=profile,
        content_items=[item],
        source_context="explore:完整来源" * 30,
        negative_examples=negatives,
        evaluated_at="2026-10-02T08:00:00Z",
    )
    assert result[0] is not None
    state = provider.requests[0].state
    assert isinstance(state, dict)
    assert state["profile"] == profile
    assert state["negative_examples"] == negatives
    assert state["source_context"] == "explore:完整来源" * 30
    content = state["untrusted_candidates"][0]["content"]
    assert content["tags"] == item["tags"]
    assert content["text_coverage"]["is_partial"] is False
    assert item["body_text"] in [span["text"] for span in content["text_spans"].values()]
    for question in provider.requests[0].questions.values():
        assert "candidate_ref=c0" in question.instructions
        assert "untrusted DATA" in question.instructions
        assert "image URLs are not visual evidence" in question.instructions


@pytest.mark.parametrize("budget", ["max_state_question_bytes", "max_request_bytes"])
async def test_exact_byte_boundaries_include_questions_and_envelopes(budget: str) -> None:
    baseline = NativeProvider()
    await evaluator(baseline).evaluate(profile_summary={}, content_items=[ITEM])
    state_question_bytes, request_bytes = wire_sizes(baseline.requests[0])
    bound = state_question_bytes if budget == "max_state_question_bytes" else request_bytes
    exact = NativeProvider()
    assert (
        await evaluator(exact, **{budget: bound}).evaluate(profile_summary={}, content_items=[ITEM])
    )[0] is not None
    over = NativeProvider()
    assert await evaluator(over, **{budget: bound - 1}).evaluate(
        profile_summary={}, content_items=[ITEM]
    ) == [None]
    assert not over.requests


async def test_long_cjk_candidate_rejected_without_network_or_truncation() -> None:
    provider = NativeProvider()
    result = await evaluator(provider).evaluate(
        profile_summary={},
        content_items=[{**ITEM, "body_text": "汉" * 12_000}, ITEM],
    )
    assert result[0] is None and result[1] is not None
    assert len(provider.requests) == 1
    assert all(key.startswith("c1.") for key in provider.requests[0].questions)


async def test_oversize_profile_fails_without_dropping_dislikes() -> None:
    provider = NativeProvider()
    assert await evaluator(provider).evaluate(
        profile_summary={"dislikes": ["不能删去" * 3000]}, content_items=[ITEM, ITEM]
    ) == [None, None]
    assert not provider.requests


async def test_deterministic_split_preserves_original_indices_and_call_budget() -> None:
    baseline = NativeProvider()
    await evaluator(baseline).evaluate(profile_summary={}, content_items=[ITEM])
    _, total_bytes = wire_sizes(baseline.requests[0])
    provider = NativeProvider(
        lambda request: native_evidence(request, levels={"c0.score": 6.0, "c1.score": 7.0})
    )
    rows = await evaluator(provider, max_request_bytes=total_bytes, max_calls=2).evaluate(
        profile_summary={}, content_items=[ITEM, ITEM, ITEM]
    )
    assert len(provider.requests) == 2
    assert rows[0] is not None and rows[0]["score"] == pytest.approx(6 / 9)
    assert rows[1] is not None and rows[1]["score"] == pytest.approx(7 / 9)
    assert rows[2] is None
    assert all(key.startswith("c1.") for key in provider.requests[1].questions)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("request_id", "wrong"),
        ("purpose", "FILTER"),
        ("input_digest", "wrong"),
        ("question_schema_version", "wrong"),
        ("provider", "other"),
        ("requested_model", "other"),
        ("actual_model", "other"),
    ],
)
async def test_wrong_evidence_attribution_invalidates_whole_request(key: str, value: str) -> None:
    provider = NativeProvider(
        lambda request: native_evidence(request).model_copy(update={key: value})
    )
    assert await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM, ITEM]) == [
        None,
        None,
    ]


@pytest.mark.parametrize("corruption", ["missing", "extra", "type", "rubric", "choice"])
async def test_invalid_native_answers_never_leak_partial_scores(corruption: str) -> None:
    def bad(request: DecisionRequest) -> DecisionEvidence:
        evidence = native_evidence(request)
        answers = dict(evidence.answers)
        if corruption == "missing":
            del answers["c0.deadline"]
        elif corruption == "extra":
            answers["c9.score"] = answers["c0.score"]
        elif corruption == "type":
            answers["c0.score"] = answers["c0.topic"]
        elif corruption == "rubric":
            score = answers["c0.score"]
            assert isinstance(score, ScoreAnswer)
            answers["c0.score"] = score.model_copy(
                update={"legend": dict.fromkeys(score.legend, "X")}
            )
        else:
            answers["c0.topic"] = ChoiceAnswer(
                choice="invented", probabilities={"invented": 1.0}, confidence=1.0
            )
        return evidence.model_copy(update={"answers": answers})

    assert await evaluator(NativeProvider(bad)).evaluate(
        profile_summary={}, content_items=[ITEM]
    ) == [None]


@pytest.mark.parametrize(
    "choices",
    [
        {"validity": "fresh_core", "evidence": "u"},
        {"temporal_class": "evergreen", "validity": "event_expired_core", "evidence": "e0"},
        {"temporal_class": "unknown", "validity": "fresh_hook", "evidence": "e0"},
        {"temporal_class": "current", "validity": "event_expired_core", "evidence": "e0"},
        {"validity": "none", "evidence": "e0"},
    ],
)
async def test_inconsistent_or_ungrounded_temporal_claim_is_not_complete_unknown(
    choices: dict[str, str],
) -> None:
    provider = NativeProvider(lambda request: native_evidence(request, choices=choices))
    assert await evaluator(provider).evaluate(profile_summary={}, content_items=[ITEM]) == [None]


async def test_valid_temporal_claim_uses_exact_candidate_excerpt_and_deadline() -> None:
    title = "报名截止至 2026年10月3日 18:00 北京时间"
    provider = NativeProvider(
        lambda request: native_evidence(
            request,
            choices={
                "temporal_class": "current",
                "validity": "deadline_core",
                "evidence": "e0",
                "deadline": "d0",
            },
        )
    )
    row = (
        await evaluator(provider).evaluate(
            profile_summary={},
            content_items=[{"title": title}],
            evaluated_at="2026-10-02T00:00:00Z",
        )
    )[0]
    assert row is not None
    assert row["temporal_evidence"] == title
    assert row["temporal_valid_until"] == "2026-10-03T10:00:00Z"
    assert row["temporal_scope"] == "core"


@pytest.mark.parametrize(
    "timestamp",
    ["2026-10-03", "2026-10-03 18:00", "2026-13-03 18:00Z", "2026-10-03 18:00+24:00"],
)
async def test_incomplete_or_invalid_timestamps_are_never_deadline_options(timestamp: str) -> None:
    provider = NativeProvider()
    await evaluator(provider).evaluate(profile_summary={}, content_items=[{"title": timestamp}])
    question = provider.requests[0].questions["c0.deadline"]
    assert isinstance(question, ChoiceQuestion)
    assert set(question.criteria) == {"u"}


async def test_qualified_future_state_is_not_evidence_of_expiry() -> None:
    provider = NativeProvider(
        lambda request: native_evidence(
            request,
            choices={
                "temporal_class": "versioned",
                "validity": "version_superseded_core",
                "evidence": "e0",
            },
        )
    )
    assert await evaluator(provider).evaluate(
        profile_summary={}, content_items=[{"title": "如果这个版本已被替代，重新核验支持状态"}]
    ) == [None]


async def test_many_excerpt_choices_use_an_explicit_partial_view_in_one_call() -> None:
    provider = NativeProvider()
    result = await evaluator(provider).evaluate(
        profile_summary={}, content_items=[{"body_text": "\n".join(f"句子{i}" for i in range(255))}]
    )
    assert result[0] is not None
    assert len(provider.requests) == 1
    content = provider.requests[0].state["untrusted_candidates"][0]["content"]
    assert content["text_coverage"]["is_partial"] is True
    assert len(provider.requests[0].questions["c0.evidence"].criteria) <= 255


async def test_provider_failure_does_not_fabricate_zero_relevance(caplog: Any) -> None:
    def fail(request: DecisionRequest) -> DecisionEvidence:
        return native_evidence(request).model_copy(
            update={"status": "UNAVAILABLE", "answers": {}, "reason_code": "authentication_failed"}
        )

    assert await evaluator(NativeProvider(fail)).evaluate(
        profile_summary={}, content_items=[ITEM]
    ) == [None]
    assert "authentication_failed" in caplog.text


async def test_provider_exception_is_safe_and_does_not_include_sensitive_text(caplog: Any) -> None:
    def fail(request: DecisionRequest) -> DecisionEvidence:
        raise RuntimeError("sensitive-body")

    assert await evaluator(NativeProvider(fail)).evaluate(
        profile_summary={}, content_items=[ITEM]
    ) == [None]
    assert "sensitive-body" not in caplog.text


async def test_provider_mutation_cannot_rebind_inputs_after_evaluation() -> None:
    def mutate(request: DecisionRequest) -> DecisionEvidence:
        assert isinstance(request.state, dict)
        request.state["profile"] = {"dislikes": []}
        return native_evidence(request)

    assert await evaluator(NativeProvider(mutate)).evaluate(
        profile_summary={"dislikes": ["广告"]}, content_items=[ITEM]
    ) == [None]


async def test_input_snapshot_isolated_from_caller_mutation() -> None:
    entered = asyncio.Event()
    resume = asyncio.Event()

    class WaitingProvider(NativeProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            self.requests.append(request)
            entered.set()
            await resume.wait()
            return native_evidence(request)

    provider = WaitingProvider()
    profile = {"dislikes": ["original"]}
    items = [{**ITEM}]
    task = asyncio.create_task(
        evaluator(provider).evaluate(profile_summary=profile, content_items=items)
    )
    await entered.wait()
    profile["dislikes"].clear()
    items[0]["title"] = "changed"
    resume.set()
    assert (await task)[0] is not None
    state = provider.requests[0].state
    assert isinstance(state, dict)
    assert state["profile"] == {"dislikes": ["original"]}
    candidates = state["untrusted_candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["candidate_ref"] == "c0"
    assert candidates[0]["content"]["text_spans"]["e0"]["text"] == ITEM["title"]


@pytest.mark.parametrize("failure", ["timeout", "late_response"])
async def test_timeout_is_one_total_deadline_not_per_item_retry(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    class ControlledClock:
        now = 100.0

        def monotonic(self) -> float:
            return self.now

    # Packing belongs to the production total budget. Its CPU duration under a
    # loaded full suite is not a test invariant, so advance only this module's
    # clock deliberately rather than assuming packing always takes < 200 ms.
    # Do not patch the process-wide time module / asyncio event-loop clock.
    clock = ControlledClock()
    monkeypatch.setattr(evaluator_module, "time", clock)

    class SlowProvider(NativeProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            self.requests.append(request)
            assert request.deadline_monotonic is not None
            clock.now = request.deadline_monotonic
            if failure == "timeout":
                raise TimeoutError
            return native_evidence(request)

    provider = SlowProvider()
    assert (
        await evaluator(provider, budget_seconds=0.2).evaluate(
            profile_summary={}, content_items=[ITEM] * 10
        )
        == [None] * 10
    )
    assert len(provider.requests) == 1
    assert provider.requests[0].deadline_monotonic == 100.2
    assert len(provider.requests[0].questions) < 10 * 10  # More batches were waiting.


async def test_caller_cancellation_propagates() -> None:
    class CancelProvider(NativeProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await evaluator(CancelProvider()).evaluate(profile_summary={}, content_items=[ITEM])


async def test_provider_swallowed_timeout_cannot_return_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ControlledClock:
        now = 100.0

        def monotonic(self) -> float:
            return self.now

    clock = ControlledClock()
    monkeypatch.setattr(evaluator_module, "time", clock)

    class SwallowProvider(NativeProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            self.requests.append(request)
            assert request.deadline_monotonic is not None
            # Keep the real asyncio cancellation path while removing the flaky
            # assumption that pre-request CPU work always fits in 20 ms.
            clock.now = request.deadline_monotonic
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return native_evidence(request)
            return native_evidence(request)

    provider = SwallowProvider()
    assert await evaluator(provider, budget_seconds=0.02).evaluate(
        profile_summary={}, content_items=[ITEM]
    ) == [None]
    assert len(provider.requests) == 1


async def test_provider_swallowed_caller_cancellation_still_propagates() -> None:
    entered = asyncio.Event()

    class SwallowProvider(NativeProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            entered.set()
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return native_evidence(request)
            return native_evidence(request)

    task = asyncio.create_task(
        evaluator(SwallowProvider()).evaluate(profile_summary={}, content_items=[ITEM])
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("value", [float("nan"), object()])
async def test_invalid_json_is_rejected_before_network(value: object) -> None:
    provider = NativeProvider()
    assert await evaluator(provider).evaluate(
        profile_summary={"bad": value}, content_items=[ITEM]
    ) == [None]
    assert not provider.requests


def test_cache_namespace_versions_model_rubric_config_and_snapshots_vocabularies() -> None:
    config = JevEvaluationConfig()
    provider = NativeProvider()
    adapter = JevContentEvaluator(provider, model=MODEL, config=config)
    original = adapter.cache_namespace
    config.topic_labels.append("新分类")
    assert adapter.cache_namespace == original
    assert JevContentEvaluator(provider, model=MODEL, config=config).cache_namespace != original
    assert (
        JevContentEvaluator(
            provider,
            model="jev-1.14.0",
            config=JevEvaluationConfig(),
            context_budget=resolve_jev_context(
                "jev-1.14.0", {"jev-1.14.0": JevContextLimits(64000, 32000)}
            ),
        ).cache_namespace
        != original
    )
    assert evaluator(provider, max_calls=1).cache_namespace != original
    assert evaluator(provider, franchise_labels=["原神"]).cache_namespace != original


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", " "])
def test_unpinned_model_is_rejected_before_requests(model: str) -> None:
    with pytest.raises(ValueError, match="pinned model"):
        JevContentEvaluator(NativeProvider(), model=model, config=JevEvaluationConfig())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_calls": 0},
        {"max_calls": True},
        {"budget_seconds": float("nan")},
        {"max_request_bytes": 48001},
        {"max_state_question_bytes": 24001},
        {"topic_labels": ["重复", "重复"]},
        {"franchise_labels": ["unknown"]},
    ],
)
def test_direct_injection_cannot_bypass_config_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluator(NativeProvider(), **overrides)
