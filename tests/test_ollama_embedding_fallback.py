"""Replay native runner crashes through production and readiness call sites."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from openbiliclaw.llm.embedding import EmbeddingService
from openbiliclaw.llm.ollama_diagnostics import (
    DIAG_MODEL_BROKEN,
    DIAG_OK,
    diagnose_ollama_embedding,
)
from openbiliclaw.llm.ollama_provider import OllamaProvider

CRASH = (
    "llama-server process has terminated: exit status 0xc0000409: "
    "The system detected an overrun of a stack-based buffer in this application."
)
BASE = "http://127.0.0.1:11435/v1"


def install_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client(**kwargs):
        kwargs.setdefault("transport", transport)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return transport


@pytest.mark.parametrize(
    "crash",
    [
        CRASH,
        "exit status 0xc0000005",
        "CUDA error: out of memory",
        "Vulkan error: VK_ERROR_DEVICE_LOST",
    ],
)
async def test_runner_crash_falls_back_and_diagnosis_shares_cpu_mode(monkeypatch, crash):
    requests = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})
        body = json.loads(request.content)
        requests.append(body)
        if body.get("options", {}).get("num_gpu") != 0:
            return httpx.Response(500, json={"error": crash})
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    transport = install_transport(monkeypatch, handler)
    provider = OllamaProvider(model="bge-m3", base_url=BASE)
    assert await provider.embed("hello") == [0.1, 0.2, 0.3]
    # A fresh provider and config-only readiness must use the same selection.
    assert await OllamaProvider(model="bge-m3", base_url=BASE).embed("again")
    code, _ = await diagnose_ollama_embedding(
        "http://localhost:11435/v1", "bge-m3:latest", transport=transport
    )
    assert code == DIAG_OK
    assert [r.get("options") for r in requests] == [None] + [{"num_gpu": 0}] * 3
    assert [r["prompt"] for r in requests] == ["hello", "hello", "again", "ping"]


async def test_diagnosis_can_trigger_fallback_before_production(monkeypatch):
    requests = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})
        body = json.loads(request.content)
        requests.append(body)
        if "options" not in body:
            return httpx.Response(500, json={"error": CRASH})
        return httpx.Response(200, json={"embedding": [1.0]})

    install_transport(monkeypatch, handler)
    assert (await diagnose_ollama_embedding(BASE, "bge-m3"))[0] == DIAG_OK
    assert await OllamaProvider(model="bge-m3", base_url=BASE).embed("hello") == [1.0]
    assert [r.get("options") for r in requests] == [None, {"num_gpu": 0}, {"num_gpu": 0}]


async def test_cpu_failure_is_not_reported_ready_or_retried_on_gpu(monkeypatch):
    requests = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(500, json={"error": CRASH})

    install_transport(monkeypatch, handler)
    code, detail = await diagnose_ollama_embedding(BASE, "bge-m3")
    assert code == DIAG_MODEL_BROKEN
    assert "CPU" in detail
    assert "0xc0000409" in detail
    assert "可能下载不完整或内存不足" not in detail
    assert await OllamaProvider(model="bge-m3", base_url=BASE).embed("hello") == []
    assert sum("options" not in r for r in requests) == 1


@pytest.mark.parametrize("base", [BASE, "https://ollama.example/v1"])
async def test_healthy_default_keeps_acceleration_and_remote_is_untouched(monkeypatch, base):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"embedding": [0.5]})

    install_transport(monkeypatch, handler)
    provider = OllamaProvider(model="bge-m3", base_url=base)
    assert await provider.embed("hello") == [0.5]
    assert all("options" not in b for b in bodies)


@pytest.mark.parametrize(
    ("status", "error", "base"),
    [
        (404, "model not found", BASE),
        (401, "unauthorized", BASE),
        (500, "invalid model path", BASE),
        (500, CRASH, "https://ollama.example/v1"),
    ],
)
async def test_unrelated_or_remote_errors_do_not_switch_mode(monkeypatch, status, error, base):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(status, json={"error": error})

    install_transport(monkeypatch, handler)
    assert await OllamaProvider(model="bge-m3", base_url=base).embed("hello") == []
    assert len(bodies) == 2  # Existing transient retry budget is unchanged.
    assert all("options" not in b for b in bodies)


async def test_concurrent_requests_recover_and_later_calls_stay_on_cpu(monkeypatch):
    bodies = []

    async def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        await asyncio.sleep(0)
        if "options" not in body:
            return httpx.Response(500, json={"error": CRASH})
        return httpx.Response(200, json={"embedding": [1.0]})

    install_transport(monkeypatch, handler)
    provider = OllamaProvider(model="bge-m3", base_url=BASE)
    assert await asyncio.gather(provider.embed("a"), provider.embed("b")) == [[1.0], [1.0]]
    auto_count = sum("options" not in b for b in bodies)
    assert await provider.embed("c") == [1.0]
    assert sum("options" not in b for b in bodies) == auto_count


@pytest.mark.parametrize("vector", [[], [True], [0.1, "bad"], [float("inf")], None])
async def test_invalid_cpu_vector_never_becomes_ready_or_cached(monkeypatch, vector):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})
        body = json.loads(request.content)
        if "options" not in body:
            return httpx.Response(500, json={"error": CRASH})
        # json.dumps intentionally permits Infinity to replay malformed servers.
        return httpx.Response(200, content=json.dumps({"embedding": vector}))

    install_transport(monkeypatch, handler)
    service = EmbeddingService(OllamaProvider(model="bge-m3", base_url=BASE), model="bge-m3")
    assert await service.probe() is False
    assert await service.embed("hello") == []
    assert (await diagnose_ollama_embedding(BASE, "bge-m3"))[0] == DIAG_MODEL_BROKEN


async def test_cpu_failure_can_recover_without_revisiting_gpu(monkeypatch):
    cpu_working = False
    automatic_calls = 0

    def handler(request):
        nonlocal automatic_calls
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})
        body = json.loads(request.content)
        if "options" not in body:
            automatic_calls += 1
        elif cpu_working:
            return httpx.Response(200, json={"embedding": [0.5]})
        return httpx.Response(500, json={"error": CRASH})

    install_transport(monkeypatch, handler)
    assert (await diagnose_ollama_embedding(BASE, "bge-m3"))[0] == DIAG_MODEL_BROKEN
    cpu_working = True
    assert (await diagnose_ollama_embedding(BASE, "bge-m3"))[0] == DIAG_OK
    assert automatic_calls == 1


async def test_mode_is_isolated_by_model_endpoint_and_app_restart(monkeypatch):
    from openbiliclaw.llm import ollama_embedding_runtime as runtime

    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if request.url.port == 11435 and body["model"] == "bge-m3" and "options" not in body:
            return httpx.Response(500, json={"error": CRASH})
        return httpx.Response(200, json={"embedding": [0.5]})

    install_transport(monkeypatch, handler)
    provider = OllamaProvider(model="bge-m3", base_url=BASE)
    assert await provider.embed("hello")
    assert await provider.embed("another model", model="nomic-embed-text")
    assert "options" not in bodies[-1]
    assert await OllamaProvider(model="bge-m3", base_url="http://localhost:11434/v1").embed("hi")
    assert "options" not in bodies[-1]
    # Process-local state starts empty on an app restart.
    monkeypatch.setattr(runtime, "_cpu_models", set())
    assert await provider.embed("restart")
    assert "options" not in bodies[-2]


async def test_cancellation_during_cpu_load_retains_mode(monkeypatch):
    from openbiliclaw.llm.ollama_embedding_runtime import cpu_fallback_active

    cpu_started = asyncio.Event()

    async def handler(request):
        if "options" not in json.loads(request.content):
            return httpx.Response(500, json={"error": CRASH})
        cpu_started.set()
        await asyncio.Event().wait()

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(OllamaProvider(model="bge-m3", base_url=BASE).embed("hello"))
    await asyncio.wait_for(cpu_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cpu_fallback_active(BASE, "bge-m3")


async def test_cold_load_timeout_does_not_switch_mode(monkeypatch):
    from openbiliclaw.llm.ollama_embedding_runtime import cpu_fallback_active

    def handler(request):
        raise httpx.ReadTimeout("cold load", request=request)

    install_transport(monkeypatch, handler)
    assert await OllamaProvider(model="bge-m3", base_url=BASE).embed("hello") == []
    assert not cpu_fallback_active(BASE, "bge-m3")


@pytest.mark.parametrize("bad_vector", [[], [0.1, "bad"], [float("nan")]])
async def test_invalid_automatic_vector_retries_on_cpu(monkeypatch, bad_vector):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        vector = [0.5] if "options" in body else bad_vector
        return httpx.Response(200, content=json.dumps({"embedding": vector}))

    install_transport(monkeypatch, handler)
    assert await OllamaProvider(model="bge-m3", base_url=BASE).embed("hello") == [0.5]
    assert [b.get("options") for b in bodies] == [None, {"num_gpu": 0}]
