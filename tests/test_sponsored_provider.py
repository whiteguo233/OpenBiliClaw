"""Tests for the SponsoredProvider IPC client against the mock runtime."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.sponsored_errors import (
    SponsoredAuthError,
    SponsoredBalanceLowError,
    SponsoredContractMismatchError,
    SponsoredLedgerTamperedError,
    SponsoredQuotaExceededError,
    SponsoredRateLimitedError,
    SponsoredRequestTooLargeError,
    SponsoredUnavailableError,
    SponsoredUnsupportedTaskError,
    SponsoredUpstreamError,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider

MOCK_RUNTIME_COMMAND = (
    sys.executable,
    "-m",
    "openbiliclaw.llm.sponsored_mock_runtime",
)
VALID_PAYLOAD = {"likes_clusters": [], "dislikes_clusters": []}


async def test_execute_task_success() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0)
    try:
        response = await provider.execute_task(
            caller="soul.consolidation",
            user_payload=VALID_PAYLOAD,
        )
    finally:
        await provider.aclose()

    assert isinstance(response, LLMResponse)
    assert response.provider == "sponsored"
    assert response.model == "XingChenAGI/Xing4.0-29B"
    assert response.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }
    assert json.loads(response.content) == {"likes": [], "dislikes": []}


async def test_provider_reuses_one_child_for_multiple_tasks() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0)
    try:
        first = await provider.execute_task(
            caller="soul.consolidation",
            user_payload=VALID_PAYLOAD,
        )
        assert provider.runtime_running
        second = await provider.execute_task(
            caller="soul.consolidation",
            user_payload=VALID_PAYLOAD,
        )
    finally:
        await provider.aclose()

    assert first.content == second.content
    assert provider.runtime_running is False


async def test_local_prompt_mismatch_is_not_fallback_allowed() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND)
    try:
        with pytest.raises(SponsoredContractMismatchError) as excinfo:
            await provider.execute_task(
                caller="soul.consolidation",
                user_payload=VALID_PAYLOAD,
                system_prompt="tampered prompt",
            )
    finally:
        await provider.aclose()

    assert excinfo.value.fallback_allowed is False
    assert provider.runtime_running is False


async def test_unknown_caller_is_unsupported() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND)
    try:
        with pytest.raises(SponsoredUnsupportedTaskError):
            await provider.execute_task(
                caller="general.chat",
                user_payload=VALID_PAYLOAD,
            )
    finally:
        await provider.aclose()


async def test_oversized_payload_is_rejected_locally() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND)
    payload = {
        "likes_clusters": [],
        "dislikes_clusters": [
            {"cluster_id": "c1", "members": ["x" * 300_000]},
        ],
    }
    try:
        with pytest.raises(SponsoredRequestTooLargeError):
            await provider.execute_task(caller="soul.consolidation", user_payload=payload)
    finally:
        await provider.aclose()


async def test_missing_runtime_is_unavailable() -> None:
    provider = SponsoredProvider(["/nonexistent/obc-sponsored-runtime"])
    with pytest.raises(SponsoredUnavailableError):
        await provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD)
    await provider.aclose()


async def test_broken_runtime_handshake_is_unavailable() -> None:
    provider = SponsoredProvider(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        handshake_timeout=5.0,
    )
    with pytest.raises(SponsoredUnavailableError):
        await provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD)
    await provider.aclose()


@pytest.mark.parametrize(
    ("code", "expected_type"),
    [
        ("QUOTA_EXCEEDED", SponsoredQuotaExceededError),
        ("RATE_LIMITED", SponsoredRateLimitedError),
        ("AUTH_FAILED", SponsoredAuthError),
        ("BALANCE_LOW", SponsoredBalanceLowError),
        ("LEDGER_TAMPERED", SponsoredLedgerTamperedError),
        ("UPSTREAM_ERROR", SponsoredUpstreamError),
    ],
)
async def test_runtime_error_codes_map_to_typed_errors(
    code: str,
    expected_type: type[Exception],
) -> None:
    provider = SponsoredProvider(
        MOCK_RUNTIME_COMMAND,
        env={"SPONSORED_MOCK_ERROR_CODE": code},
        request_timeout=15.0,
    )
    try:
        with pytest.raises(expected_type):
            await provider.execute_task(
                caller="soul.consolidation",
                user_payload=VALID_PAYLOAD,
            )
    finally:
        await provider.aclose()


async def test_forced_contract_mismatch_is_not_fallback_allowed() -> None:
    provider = SponsoredProvider(
        MOCK_RUNTIME_COMMAND,
        env={"SPONSORED_MOCK_ERROR_CODE": "CONTRACT_MISMATCH"},
        request_timeout=15.0,
    )
    try:
        with pytest.raises(SponsoredContractMismatchError) as excinfo:
            await provider.execute_task(
                caller="soul.consolidation",
                user_payload=VALID_PAYLOAD,
            )
    finally:
        await provider.aclose()
    assert excinfo.value.fallback_allowed is False


async def test_runtime_timeout_is_unavailable() -> None:
    provider = SponsoredProvider(
        MOCK_RUNTIME_COMMAND,
        env={"SPONSORED_MOCK_DELAY_SECONDS": "2"},
        request_timeout=0.15,
        handshake_timeout=5.0,
    )
    try:
        with pytest.raises(SponsoredUnavailableError):
            await provider.execute_task(
                caller="soul.consolidation",
                user_payload=VALID_PAYLOAD,
            )
    finally:
        await provider.aclose()


async def test_concurrent_requests_are_serialized_per_child() -> None:
    provider = SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0)
    try:
        responses = await asyncio.gather(
            provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD),
            provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD),
            provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD),
        )
    finally:
        await provider.aclose()
    assert [json.loads(response.content) for response in responses] == [
        {"likes": [], "dislikes": []},
        {"likes": [], "dislikes": []},
        {"likes": [], "dislikes": []},
    ]


async def test_async_context_manager_closes_runtime() -> None:
    async with SponsoredProvider(MOCK_RUNTIME_COMMAND, request_timeout=15.0) as provider:
        assert provider.runtime_running
        await provider.execute_task(caller="soul.consolidation", user_payload=VALID_PAYLOAD)
    assert provider.runtime_running is False
