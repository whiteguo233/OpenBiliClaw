"""Initialization must observe successful prompt-sized work, not just 200-row groups."""

import asyncio
import json
from typing import Any

import pytest

from openbiliclaw.cli import _await_with_progress_deadline, _InitProgressMarker
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.prompts import build_preference_analysis_prompt
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer


@pytest.mark.asyncio
async def test_prompt_splits_keep_guided_init_alive_and_report_actual_batches() -> None:
    completed = 0

    class RespondingService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            nonlocal completed
            await asyncio.sleep(0.02)
            completed += 1
            return LLMResponse(
                content=json.dumps(
                    {"interests": [{"name": "编程", "category": "科技", "weight": 0.7}]}
                ),
                provider="siliconflow_free",
            )

    base = build_preference_analysis_prompt(events=[], existing_preference={})
    analyzer = PreferenceAnalyzer(
        RespondingService(), max_prompt_chars=sum(len(m["content"]) for m in base) + 1800
    )
    events = [
        {"event_type": "view", "title": f"编程 {i}", "context": "示例上下文" * 180}
        for i in range(16)
    ]
    marker = _InitProgressMarker()
    progress: list[tuple[int, int]] = []

    async def report(done: int, total: int) -> None:
        progress.append((done, total))
        marker.touch()

    result = await _await_with_progress_deadline(
        analyzer.analyze_events(
            events=events,
            existing_preference={},
            event_chunk_size=16,
            progress_callback=report,
            llm_concurrency=1,
        ),
        marker=marker,
        idle_seconds=0.12,
        absolute_seconds=3,
        poll_seconds=0.01,
    )
    assert result["interests"]
    assert completed >= 8
    assert len(progress) == completed
    assert progress == [(i, completed) for i in range(1, completed + 1)]
    assert analyzer.planned_batch_count(events, event_chunk_size=16) == completed


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [0, 2])
async def test_free_context_guard_splits_at_the_preference_call_site(chunk_size: int) -> None:
    from openbiliclaw.llm.sponsored_adapter import SponsoredChatAdapter
    from openbiliclaw.llm.sponsored_errors import SponsoredRequestTooLargeError

    calls: list[str] = []

    class GuardedService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            text = kwargs["user_input"]
            calls.append(text)
            if "EVENT_A" in text and "EVENT_B" in text:
                SponsoredChatAdapter._raise(
                    SponsoredRequestTooLargeError("Qwen/Qwen3-8B context budget exceeded")
                )
            return LLMResponse(content='{"interests": []}', provider="siliconflow_free")

    await PreferenceAnalyzer(GuardedService()).analyze_events(
        events=[{"title": "EVENT_A"}, {"title": "EVENT_B"}],
        existing_preference={},
        event_chunk_size=chunk_size,
    )
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["timeout", "tls_disconnect"])
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("chunk_size", [0, 2])
async def test_free_timeout_recovers_once_with_smaller_read_only_batches(
    persistent: bool, chunk_size: int, failure: str
) -> None:
    from openbiliclaw.llm.sponsored_adapter import SponsoredChatAdapter
    from openbiliclaw.llm.sponsored_errors import SponsoredUnavailableError, SponsoredUpstreamError
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalysisError

    calls: list[str] = []

    class TimingOutService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            text = kwargs["user_input"]
            calls.append(text)
            if persistent or ("EVENT_A" in text and "EVENT_B" in text):
                SponsoredChatAdapter._raise(
                    SponsoredUnavailableError("sponsored runtime timed out")
                    if failure == "timeout"
                    else SponsoredUpstreamError(
                        "transport error: Network Error: Error encountered in the status line: "
                        "peer closed connection without sending TLS close_notify"
                    )
                )
            return LLMResponse(content='{"interests": []}', provider="siliconflow_free")

    work = PreferenceAnalyzer(TimingOutService()).analyze_events(
        events=[{"title": "EVENT_A"}, {"title": "EVENT_B"}],
        existing_preference={},
        event_chunk_size=chunk_size,
    )
    if persistent:
        with pytest.raises(PreferenceAnalysisError):
            await work
        assert len(calls) == 2
    else:
        await work
        assert len(calls) == 3


def test_initialization_deadline_scales_with_actual_prompt_batches() -> None:
    from openbiliclaw.cli import _profile_analysis_deadlines

    _, original = _profile_analysis_deadlines(event_count=200, requested=None, concurrency=1)
    _, actual = _profile_analysis_deadlines(
        event_count=200, requested=None, concurrency=1, batch_count=40
    )
    assert actual > original
    assert _profile_analysis_deadlines(event_count=200, requested=7, batch_count=40) == (None, 7)


@pytest.mark.parametrize("chunk_size", [0, 2])
@pytest.mark.parametrize("code", ["AUTH_FAILED", "QUOTA_EXCEEDED", "POLICY_INVALID"])
async def test_free_permanent_error_is_not_retried_even_with_timeout_text(
    chunk_size: int, code: str
) -> None:
    from openbiliclaw.llm.sponsored_adapter import SponsoredChatAdapter
    from openbiliclaw.llm.sponsored_errors import _ERROR_TYPES
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalysisError

    calls = 0

    class RejectedService:
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            nonlocal calls
            calls += 1
            SponsoredChatAdapter._raise(_ERROR_TYPES[code]("operation timed out"))
            raise AssertionError("unreachable")

    with pytest.raises(PreferenceAnalysisError):
        await PreferenceAnalyzer(RejectedService()).analyze_events(
            events=[{"title": "EVENT_A"}, {"title": "EVENT_B"}],
            existing_preference={},
            event_chunk_size=chunk_size,
        )
    assert calls == 1


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
async def test_free_preference_wire_disables_thinking_and_bounds_output(model: str) -> None:
    from openbiliclaw.llm.base import LLMRegistry
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.llm.sponsored_profiles import approved_registry
    from openbiliclaw.llm.sponsored_provider import SponsoredProvider

    captured: list[dict[str, Any]] = []

    class Capture(SponsoredProvider):
        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            captured.append(kwargs["user_payload"])
            return LLMResponse(content='{"interests": []}', provider="sponsored", model=model)

    sdk = Capture(["unused"], registry=approved_registry(), model=model)
    service = LLMService(registry=LLMRegistry(), memory=None, sponsored_provider=sdk)
    analyzer = PreferenceAnalyzer(service)
    await analyzer.analyze_events(events=[{"title": "Rust 入门"}], existing_preference={})
    assert captured[0]["reasoning_effort"] == ""
    assert captured[0]["max_tokens"] <= 4096


@pytest.mark.parametrize(
    "message",
    [
        "transport error: certificate verify failed",
        "completion was truncated or rejected",
        "completion model mismatch",
        "invalid JSON response",
    ],
)
def test_free_recovery_does_not_hide_permanent_or_invalid_upstream_responses(message):
    from openbiliclaw.llm.sponsored_errors import SponsoredUpstreamError

    assert not PreferenceAnalyzer._is_free_transient_error(SponsoredUpstreamError(message))
