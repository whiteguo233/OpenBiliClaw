"""A real IPC-shaped local RPM rejection must survive the embedding burst."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.llm import sponsored_provider as module
from openbiliclaw.llm.sponsored_errors import SponsoredRateLimitedError, SponsoredUnavailableError
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider


@pytest.fixture
def ipc(monkeypatch):
    provider = SponsoredProvider(
        ["unused"],
        registry=approved_registry(),
        request_timeout=120,
        fallback_to_other_free_model=False,
    )
    provider._process = SimpleNamespace(stdin=object(), stdout=object(), returncode=None)
    provider._streaming = True
    provider._request_timeouts = True
    monkeypatch.setattr(provider, "_ensure_started", AsyncMock())
    monkeypatch.setattr(provider, "_stop_process", AsyncMock())
    monkeypatch.setattr(provider, "_cancel_in_flight", lambda *_: None)
    state = {"writes": [], "failures": 1, "message": "local rate limit reached", "delta": False}

    async def write(_pipe, frame):
        state["writes"].append(frame)

    async def read(_pipe):
        frame = state["writes"][-1]
        if state["delta"]:
            state["delta"] = False
            return {"type": "delta", "id": frame["id"], "content": "already visible"}
        if len(state["writes"]) <= state["failures"]:
            return {
                "type": "result",
                "id": frame["id"],
                "ok": False,
                "error": {"code": "RATE_LIMITED", "message": state["message"]},
            }
        return {"type": "result", "id": frame["id"], "ok": True, "content": "ok"}

    monkeypatch.setattr(module, "write_frame_async", write)
    monkeypatch.setattr(module, "read_frame_async", read)
    return provider, state


async def invoke(provider, mode):
    if mode == "stream":
        return [c async for c in provider.stream_complete([{"role": "user", "content": "hello"}])]
    if mode == "embedding":
        return await provider.execute_task(
            task_id="llm.embedding_text", user_payload={"text": "hello"}
        )
    return await provider.complete([{"role": "user", "content": "hello"}])


@pytest.mark.parametrize("mode", ["plain", "stream", "embedding"])
async def test_local_rpm_waits_once_then_reuses_the_same_task(ipc, monkeypatch, mode):
    provider, state = ipc
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    await invoke(provider, mode)
    assert len(state["writes"]) == 2
    assert state["writes"][0]["task_id"] == state["writes"][1]["task_id"]
    sleep.assert_awaited_once_with(60.0)


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_persistent_local_rpm_is_bounded(ipc, monkeypatch, mode):
    provider, state = ipc
    state["failures"] = 100
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(SponsoredRateLimitedError):
        await invoke(provider, mode)
    assert len(state["writes"]) == 2
    sleep.assert_awaited_once_with(60.0)


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_remote_429_does_not_get_local_window_retry(ipc, monkeypatch, mode):
    provider, state = ipc
    state["message"] = "upstream rate limit reached"
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(SponsoredRateLimitedError):
        await invoke(provider, mode)
    assert len(state["writes"]) == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_local_wait_remains_inside_original_request_deadline(ipc, mode):
    provider, state = ipc
    provider._request_timeout = 0.02
    with pytest.raises(SponsoredUnavailableError):
        await invoke(provider, mode)
    assert len(state["writes"]) == 1
    provider._stop_process.assert_awaited()


async def test_visible_stream_is_never_replayed_for_local_rpm(ipc, monkeypatch):
    provider, state = ipc
    state["delta"] = True
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(SponsoredRateLimitedError):
        await invoke(provider, "stream")
    assert len(state["writes"]) == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_persistent_local_window_never_switches_shared_free_model(ipc, monkeypatch, mode):
    provider, state = ipc
    provider._free_fallback = True
    state["failures"] = 100
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    with pytest.raises(SponsoredRateLimitedError):
        await invoke(provider, mode)
    assert len(state["writes"]) == 2
    assert len({frame["task_id"] for frame in state["writes"]}) == 1


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_retry_transmits_only_remaining_timeout(ipc, monkeypatch, mode):
    provider, state = ipc
    sleep = module.asyncio.sleep

    async def short_wait(_seconds):
        await sleep(0.01)

    monkeypatch.setattr(module.asyncio, "sleep", short_wait)
    await invoke(provider, mode)
    assert state["writes"][1]["timeout_ms"] < state["writes"][0]["timeout_ms"]


@pytest.mark.parametrize("mode", ["plain", "stream"])
async def test_cancellation_during_local_wait_never_replays(ipc, monkeypatch, mode):
    provider, state = ipc
    waiting = module.asyncio.Event()

    async def blocked_wait(_seconds):
        waiting.set()
        await module.asyncio.Event().wait()

    monkeypatch.setattr(module.asyncio, "sleep", blocked_wait)
    monkeypatch.setattr(provider, "_abort_process_sync", lambda *_: None)
    task = module.asyncio.create_task(invoke(provider, mode))
    await waiting.wait()
    task.cancel()
    with pytest.raises(module.asyncio.CancelledError):
        await task
    assert len(state["writes"]) == 1
    provider._stop_process.assert_awaited()
