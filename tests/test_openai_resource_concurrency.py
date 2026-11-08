"""Cold SDK resource imports must not block unrelated HTTP requests."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from openbiliclaw.llm.openai_provider import OpenAIProvider


@pytest.mark.parametrize("route", ["chat_completions", "responses"])
async def test_cold_sdk_resource_resolution_yields_http_loop(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    resolved = threading.Event()
    loop_thread = threading.get_ident()
    sent: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> dict[str, bool]:
        assert threading.get_ident() == loop_thread, "async transport stays on its owning loop"
        sent.append(kwargs)
        return {"ok": True}

    class LazyClient:
        @property
        def chat(self) -> SimpleNamespace:
            return SimpleNamespace(completions=self._resource())

        @property
        def responses(self) -> SimpleNamespace:
            return self._resource()

        def _resource(self) -> SimpleNamespace:
            started.set()
            # The bounded escape prevents a deadlock on the pre-fix code,
            # where this lazy property executes on the HTTP event loop.
            release.wait(3)
            resolved.set()
            return SimpleNamespace(create=create)

    provider = OpenAIProvider(api_key="test-key", trust_env=False)
    await provider._client.close()
    monkeypatch.setattr(provider, "_client", LazyClient())
    kwargs = {"model": "test-model", "stream": True}
    app = FastAPI()

    @app.get("/complete")
    async def complete() -> Any:
        send = (
            provider._create_response if route == "responses" else provider._create_chat_completion
        )
        return await send(**kwargs)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        request = asyncio.create_task(client.get("/complete"))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert not resolved.is_set(), "SDK resource import blocked the HTTP event loop"
            response = await asyncio.wait_for(client.get("/ping"), timeout=1)
            assert response.status_code == 200
            assert not resolved.is_set(), "ping must complete during the cold resource import"
        finally:
            release.set()
            completion = await request
        assert completion.json() == {"ok": True}
        assert sent == [kwargs]
