"""Native HTTP, budget, privacy and lifecycle tests without paid service calls."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from openbiliclaw.decision import jev_provider
from openbiliclaw.decision.jev_provider import JevDecisionProvider
from openbiliclaw.decision.models import ChoiceQuestion, DecisionRequest, NoulQuestion


def _request(**changes: Any) -> DecisionRequest:
    fields: dict[str, Any] = {
        "request_id": "request-1",
        "purpose": "FILTER",
        "question_schema_version": "filter-v1",
        "state": {"candidate": "私有内容"},
        "questions": {"fit": NoulQuestion(instructions="Is the candidate relevant?")},
    }
    fields.update(changes)
    return DecisionRequest(**fields)


def _payload() -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {"fit": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


async def test_native_wire_attribution_borrowed_auth_and_lifecycle() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), auth=("unrelated", "password"), trust_env=False
    ) as client:
        provider = JevDecisionProvider(api_key="test-secret", model="jev-latest", client=client)
        request = _request()
        result = await provider.evaluate(request)
        assert result.status == "OK"
        assert result.request_id == request.request_id
        assert result.purpose == "FILTER"
        assert result.question_schema_version == "filter-v1"
        assert result.input_digest == request.input_digest
        assert result.requested_model == "jev-latest"
        assert result.actual_model == "jev-1.13.0"
        assert result.attempts == 1
        assert result.latency_ms >= 0
        assert result.http_status == 200
        sent = seen[0]
        assert str(sent.url) == "https://api.typesafe.ai/v1/systemone"
        assert sent.method == "POST"
        assert sent.headers["authorization"] == "Bearer test-secret"
        assert sent.headers["content-type"] == "application/json"
        assert set(json.loads(sent.content)) == {"model", "state", "questions"}
        assert "criteria" not in json.loads(sent.content)["questions"]["fit"]
        await provider.aclose()
        assert not client.is_closed
        closed = await provider.evaluate(request)
        assert closed.reason_code == "provider_closed" and closed.attempts == 0


async def test_canonical_digest_matches_canonical_wire_for_reordered_maps() -> None:
    bodies: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "pair": {
                        "type": "choice",
                        "choice": "ab",
                        "probabilities": {"ab": 0.8, "cd": 0.2},
                        "confidence": 0.5,
                    }
                },
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    first = _request(
        state={"b": 2, "a": 1},
        questions={
            "pair": ChoiceQuestion(instructions="Choose", criteria={"ab": "AB", "cd": "CD"})
        },
    )
    second = _request(
        request_id="request-2",
        state={"a": 1, "b": 2},
        questions={
            "pair": ChoiceQuestion(instructions="Choose", criteria={"cd": "CD", "ab": "AB"})
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = JevDecisionProvider(api_key="secret", client=client)
        left = await provider.evaluate(first)
        right = await provider.evaluate(second)
    assert left.status == right.status == "OK"
    assert left.input_digest == right.input_digest
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("status", [429, 500, 503, 529])
async def test_retryable_statuses_retry_within_shared_budget(status: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(status, text="private error")
            if calls == 1
            else httpx.Response(200, json=_payload())
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(_request())
    assert result.status == "OK" and result.attempts == calls == 2


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (422, "http_error"),
        (302, "redirect_blocked"),
        (400, "http_error"),
        (408, "http_error"),
    ],
)
async def test_other_http_failures_never_retry_or_follow_redirects(
    status: int, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            status,
            headers={"Location": "https://credential-thief.invalid/"},
            text="test-secret 私有内容 provider-private-error",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as client:
        result = await JevDecisionProvider(api_key="test-secret", client=client).evaluate(
            _request()
        )
    assert result.status == "UNAVAILABLE" and result.reason_code == reason
    assert len(calls) == result.attempts == 1
    assert result.answers == {}
    for private in ("test-secret", "私有内容", "provider-private-error"):
        assert private not in result.model_dump_json() + caplog.text


@pytest.mark.parametrize(
    "status,reason", [(429, "rate_limited"), (529, "overloaded"), (503, "server_error")]
)
async def test_retry_exhaustion_preserves_technical_cause(status: int, reason: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private"))
    ) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(_request())
    assert result.status == "UNAVAILABLE" and result.reason_code == reason
    assert result.attempts == 2


@pytest.mark.parametrize("retry_after", ["10", "http-date"])
async def test_total_deadline_includes_backoff_and_retry_after(retry_after: str) -> None:
    if retry_after == "http-date":
        retry_after = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": retry_after})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        started = time.monotonic()
        result = await JevDecisionProvider(
            api_key="secret", client=client, timeout_seconds=0.03, max_attempts=5
        ).evaluate(_request())
    assert result.reason_code == "deadline_exceeded" and calls == 1
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("deadline", [0.0, -1.0])
async def test_expired_caller_deadline_makes_no_http_call(deadline: float) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("expired decision must not call HTTP")

    absolute = 0.0 if deadline == 0 else time.monotonic() - 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(
            _request(deadline_monotonic=absolute)
        )
    assert result.reason_code == "deadline_exceeded" and result.attempts == 0


async def test_total_deadline_cancels_slow_http() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(
            _request(deadline_monotonic=time.monotonic() + 0.03)
        )
    assert result.reason_code == "deadline_exceeded" and result.attempts == 1


async def test_caller_cancellation_propagates() -> None:
    entered = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(
            JevDecisionProvider(api_key="secret", client=client).evaluate(_request())
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_errors_are_sanitized_and_not_retried(
    error: type[httpx.RequestError],
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise error("private payload and secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(_request())
    assert result.status == "UNAVAILABLE" and calls == 1
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "body", [b"bad-json", b'{"model":"x","model":"y"}', b'{"model":NaN}', b"[]"]
)
async def test_invalid_json_and_duplicate_keys_never_retry(body: bytes) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    ) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(_request())
    assert result.status == "INVALID" and result.reason_code == "invalid_response"
    assert result.attempts == 1 and result.answers == {}


async def test_inflight_input_mutation_does_not_change_evidence_binding() -> None:
    request = _request()
    original_digest = request.input_digest

    def handle(sent: httpx.Request) -> httpx.Response:
        assert isinstance(request.state, dict)
        request.state["candidate"] = "changed after dispatch"
        request.questions.clear()
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevDecisionProvider(api_key="secret", client=client).evaluate(request)
    assert result.status == "OK" and result.input_digest == original_digest


@pytest.mark.parametrize("requested_model", ["jev-1.12.0", "jev-latest", "jev-preview"])
async def test_fixed_model_mismatch_rejects_evidence_but_alias_records_actual(
    requested_model: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_payload()))
    ) as client:
        result = await JevDecisionProvider(
            api_key="secret", model=requested_model, client=client
        ).evaluate(_request())
    assert result.actual_model == "jev-1.13.0"
    assert result.requested_model == requested_model
    if requested_model == "jev-1.12.0":
        assert result.status == "INVALID" and result.reason_code == "model_mismatch"
        assert result.answers == {}
    else:
        assert result.status == "OK"


@pytest.mark.parametrize("mutation", ["invalid_question", "nan"])
async def test_mutated_invalid_request_is_sanitized_without_serialization_warning(
    mutation: str, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    request = _request()
    if mutation == "invalid_question":
        request.questions["fit"] = "private-secret-data"  # type: ignore[assignment]
    else:
        assert isinstance(request.state, dict)
        request.state["private-secret-data"] = float("nan")

    def handle(sent: httpx.Request) -> httpx.Response:
        pytest.fail("invalid request must never leave the process")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="^invalid decision request$") as error:
            await JevDecisionProvider(api_key="secret", client=client).evaluate(request)
    captured = capsys.readouterr()
    assert "private-secret-data" not in str(error.value) + captured.err + captured.out + caplog.text


async def test_owned_client_uses_network_policy_and_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []
    kwargs_seen: dict[str, Any] = {}

    def construct(**kwargs: Any) -> httpx.AsyncClient:
        kwargs_seen.update(kwargs)
        client = actual_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_payload()))
        )
        created.append(client)
        return client

    monkeypatch.setattr(
        jev_provider,
        "outbound_httpx_kwargs",
        lambda: {"trust_env": False, "proxy": "http://127.0.0.1:8123"},
    )
    monkeypatch.setattr(jev_provider.httpx, "AsyncClient", construct)
    provider = JevDecisionProvider(api_key="secret")
    assert kwargs_seen["proxy"] == "http://127.0.0.1:8123"
    assert kwargs_seen["trust_env"] is False
    assert kwargs_seen["follow_redirects"] is False
    await provider.aclose()
    await provider.aclose()
    assert created[0].is_closed


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": ""},
        {"api_key": "bad\nkey"},
        {"model": " bad "},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "2"},
        {"max_attempts": 0},
        {"max_attempts": True},
    ],
)
def test_invalid_constructor_settings_fail_before_creating_client(changes: dict[str, Any]) -> None:
    kwargs: dict[str, Any] = {"api_key": "test-secret"}
    kwargs.update(changes)
    with pytest.raises(ValueError):
        JevDecisionProvider(**kwargs)
