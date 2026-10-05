from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMAuthError, LLMResponseError
from openbiliclaw.llm.codex_chatgpt_provider import (
    CodexChatGPTProvider,
    _parse_codex_sse_response,
    codex_account_id_from_token,
    fetch_codex_models,
    normalize_codex_base_url,
)


def _jwt_with_payload(payload: dict[str, object]) -> str:
    import base64

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return ".".join([json.dumps({"alg": "none"}), payload_b64, "sig"])


def test_normalize_codex_base_url_accepts_only_official_chatgpt_paths() -> None:
    assert normalize_codex_base_url("") == "https://chatgpt.com/backend-api/codex"
    assert (
        normalize_codex_base_url("https://chatgpt.com/backend-api")
        == "https://chatgpt.com/backend-api/codex"
    )
    assert (
        normalize_codex_base_url("https://chatgpt.com/backend-api/codex")
        == "https://chatgpt.com/backend-api/codex"
    )
    assert (
        normalize_codex_base_url("https://chatgpt.com/backend-api/codex/responses")
        == "https://chatgpt.com/backend-api/codex"
    )


def test_normalize_codex_base_url_rejects_platform_api_and_third_parties() -> None:
    for bad in (
        "https://api.openai.com/v1",
        "https://openai.example.com/backend-api",
        "https://chatgpt.com.evil.com/backend-api",
        "http://chatgpt.com/backend-api",
        "https://chatgpt.com/backend-api?token=1",
        "https://chatgpt.com/backend-api/codex#frag",
        "https://relay.example.com/v1",
    ):
        with pytest.raises(LLMAuthError):
            normalize_codex_base_url(bad)


def test_codex_account_id_from_token_reads_openai_auth_claim() -> None:
    token = _jwt_with_payload({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}})
    assert codex_account_id_from_token(token) == "acct_123"


def test_codex_account_id_from_token_missing_claim_returns_empty() -> None:
    token = _jwt_with_payload({"sub": "user_1"})
    assert codex_account_id_from_token(token) == ""


def test_build_request_body_uses_responses_contract() -> None:
    provider = CodexChatGPTProvider(
        access_token="token",
        account_id="acct",
        model="gpt-5-nano",
    )
    body = provider._build_request_body(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hi"},
        ],
        effective_model="gpt-5-nano",
        max_tokens=16,
        json_mode=True,
        reasoning_effort="",
    )

    assert body["model"] == "gpt-5-nano"
    assert body["instructions"] == "Be brief."
    assert body["store"] is False
    assert body["stream"] is True
    assert "max_output_tokens" not in body
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    assert body["text"]["verbosity"] == "medium"
    assert body["text"]["format"] == {"type": "json_object"}
    assert "temperature" not in body
    assert "reasoning" not in body


@pytest.mark.asyncio
async def test_fetch_codex_models_filters_hidden_and_sorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.llm.codex_chatgpt_provider as provider_module

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "models": [
                    {"slug": "gpt-5.4", "priority": 16, "visibility": "list"},
                    {"slug": "codex-auto-review", "priority": 43, "visibility": "hide"},
                    {"slug": "gpt-5.6-sol", "priority": 1, "visibility": "list"},
                    {"slug": "gpt-5.3-codex-spark", "priority": 26, "visibility": "list"},
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
            assert url == ("https://chatgpt.com/backend-api/codex/models?client_version=1.0.0")
            assert headers["Authorization"] == "Bearer token"
            assert headers["ChatGPT-Account-Id"] == "acct"
            return FakeResponse()

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)

    models = await fetch_codex_models(access_token="token", account_id="acct")

    assert models == ["gpt-5.6-sol", "gpt-5.4", "gpt-5.3-codex-spark"]


def test_parse_codex_sse_response_extracts_deltas_and_usage() -> None:
    body = "\n".join(
        [
            "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"}),
            "data: " + json.dumps({"type": "response.output_text.delta", "delta": "!"}),
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5-nano",
                        "status": "completed",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 2,
                            "total_tokens": 7,
                            "input_tokens_details": {"cached_tokens": 3},
                        },
                    },
                }
            ),
            "",
        ]
    )
    parsed = _parse_codex_sse_response(body)
    assert parsed.text_content == "OK!"
    assert parsed.model == "gpt-5-nano"
    assert parsed.usage == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
        "cached_input_tokens": 3,
    }


def test_parse_codex_sse_response_raises_on_error_event() -> None:
    body = "data: " + json.dumps({"type": "error", "message": "boom"})
    with pytest.raises(LLMResponseError, match="boom"):
        _parse_codex_sse_response(body)


@pytest.mark.asyncio
async def test_complete_posts_to_codex_responses_and_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexChatGPTProvider(
        access_token="token",
        account_id="acct",
        model="gpt-5-nano",
    )
    captured: dict[str, object] = {}

    async def fake_post_once(
        url: str, headers: dict[str, str], body: dict[str, object]
    ) -> tuple[int, str]:
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return 200, "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"})

    monkeypatch.setattr(provider, "_post_once", fake_post_once)
    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=16,
        reasoning_effort="",
    )

    assert response.content == "OK"
    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["headers"]["chatgpt-account-id"] == "acct"
    assert captured["headers"]["originator"] == "openbiliclaw"
    assert captured["headers"]["OpenAI-Beta"] == "responses=experimental"
    assert captured["body"]["stream"] is True
    assert "temperature" not in captured["body"]


@pytest.mark.asyncio
async def test_complete_retries_without_text_format_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexChatGPTProvider(access_token="token", account_id="acct", model="gpt-5.4")
    calls: list[dict[str, object]] = []

    async def fake_post_once(
        url: str, headers: dict[str, str], body: dict[str, object]
    ) -> tuple[int, str]:
        calls.append(body)
        if len(calls) == 1:
            return 400, '{"detail":"Unsupported parameter: text.format"}'
        return 200, "data: " + json.dumps(
            {"type": "response.output_text.delta", "delta": '{"ok": true}'}
        )

    monkeypatch.setattr(provider, "_post_once", fake_post_once)
    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=16,
        json_mode=True,
        reasoning_effort="",
    )

    assert response.content == '{"ok": true}'
    assert len(calls) == 2
    assert "format" not in str(calls[1].get("text"))


@pytest.mark.asyncio
async def test_post_with_retry_refreshes_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.llm.codex_chatgpt_provider import _CodexHTTPError

    provider = CodexChatGPTProvider(
        access_token="expired-token",
        account_id="acct",
        model="gpt-5-nano",
        token_provider=lambda force_refresh=False: _fake_refresh_token(),
    )
    calls: list[str] = []
    refreshed: list[bool] = []

    async def fake_refresh() -> None:
        refreshed.append(True)
        provider._access_token = "fresh-token"
        provider._account_id = "acct"

    async def fake_post_once(
        url: str, headers: dict[str, str], body: dict[str, object]
    ) -> tuple[int, str]:
        calls.append(headers["Authorization"])
        if len(calls) == 1:
            raise _CodexHTTPError("HTTP 401", status_code=401, body="unauthorized")
        return 200, "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"})

    def _fake_refresh_token() -> str:
        return "fresh-token"

    monkeypatch.setattr(provider, "_post_once", fake_post_once)
    monkeypatch.setattr(provider, "_refresh_access_token", fake_refresh)

    status, _text = await provider._post_with_retry({"model": "gpt-5-nano"})

    assert status == 200
    assert len(calls) == 2
    assert calls[1] == "Bearer fresh-token"
    assert refreshed == [True]
