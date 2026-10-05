"""API regressions for the durable, asynchronous chat reply lane."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api import app as app_module
from openbiliclaw.api.app import create_app
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class BlockingDialogue:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self._release: asyncio.Event | None = None

    async def respond(
        self,
        _message: str,
        *,
        scope: str = "chat",
        turn_id: str = "",
    ) -> str:
        del scope, turn_id
        self.calls += 1
        self._release = asyncio.Event()
        self.started.set()
        await self._release.wait()
        return "后台回复完成"

    async def unblock(self) -> None:
        assert self._release is not None
        self._release.set()


class NoProviderDialogue:
    """Dialogue stub whose every respond fails with an empty-chain error.

    Mirrors issue #213: the resolved module route references no registered
    instance, so ``complete_with_core_memory`` fails fast before any HTTP
    attempt with a classified ``no_provider`` failure.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def respond(
        self,
        _message: str,
        *,
        scope: str = "chat",
        turn_id: str = "",
    ) -> str:
        del scope, turn_id
        self.calls += 1
        raise LLMProviderExecutionError("No provider was available to process the request.")


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def test_post_returns_pending_immediately_and_get_dedupes_wake(tmp_path: Path) -> None:
    database = _database(tmp_path)
    dialogue = BlockingDialogue()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue,
    )
    message = "这段用户消息不能出现在运行状态里"

    with TestClient(app) as client:
        started_at = time.perf_counter()
        response = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "async-turn",
                "session": "popup",
                "scope": "chat",
                "message": message,
            },
        )
        elapsed = time.perf_counter() - started_at

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        assert elapsed < 1.0
        assert dialogue.started.wait(timeout=1)

        for _ in range(5):
            pending = client.get("/api/chat/turns/async-turn")
            assert pending.status_code == 200
            assert pending.json()["status"] == "pending"
        time.sleep(0.05)
        assert dialogue.calls == 1

        # Exercise the degraded/fallback runtime-status branch: scheduler
        # fields remain additive and never leak message contents.
        app.state.runtime_context.runtime_controller = object()
        status = client.get("/api/runtime-status")
        assert status.status_code == 200
        assert status.json()["chat_reply_depth"] == 1
        assert status.json()["chat_reply_active"] is True
        assert message not in status.text

        client.portal.call(dialogue.unblock)
        for _ in range(50):
            completed = client.get("/api/chat/turns/async-turn").json()
            if completed["status"] == "completed":
                break
            time.sleep(0.01)

        assert completed["status"] == "completed"
        assert completed["reply"] == "后台回复完成"
        assert dialogue.calls == 1


def test_persistent_no_provider_turn_fails_visibly_instead_of_waiting_forever(
    tmp_path: Path,
) -> None:
    """Issue #213: an unresolvable LLM route must end the turn, not spin forever.

    When every reply attempt fails fast with a classified ``no_provider``
    error (empty resolved module route), the durable turn escalates to a
    terminal failed state with actionable copy after a bounded number of
    attempts — instead of retrying forever on the "正在思考" spinner and
    head-of-line blocking every later turn.
    """
    database = _database(tmp_path)
    dialogue = NoProviderDialogue()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "no-provider-turn",
                "session": "popup",
                "scope": "chat",
                "message": "聊聊我的口味",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending"

        terminal: dict[str, object] = {}
        for _ in range(600):
            current = client.get("/api/chat/turns/no-provider-turn").json()
            if current["status"] == "failed":
                terminal = current
                break
            time.sleep(0.02)

        assert terminal["status"] == "failed"
        error_text = str(terminal.get("error", ""))
        assert "模块路由" in error_text
        assert "重新发送" in error_text
        # Bounded escalation: exactly the configured attempt budget, not an
        # infinite retry loop against a broken route.
        assert dialogue.calls == app_module._CHAT_NO_PROVIDER_TERMINAL_ATTEMPTS


def test_app_startup_recovers_pending_turn_from_prior_process(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_chat_turn(
        turn_id="startup-recovery",
        session="popup",
        scope="chat",
        message="重启后继续",
    )
    calls: list[str] = []

    class Dialogue:
        async def respond(
            self,
            _message: str,
            *,
            scope: str = "chat",
            turn_id: str = "",
        ) -> str:
            del scope
            calls.append(turn_id)
            return "恢复完成"

    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=SimpleNamespace(),
        dialogue=Dialogue(),
    )
    with TestClient(app) as client:
        for _ in range(50):
            recovered = client.get("/api/chat/turns/startup-recovery").json()
            if recovered["status"] == "completed":
                break
            time.sleep(0.01)

    assert recovered["status"] == "completed"
    assert recovered["reply"] == "恢复完成"
    assert calls == ["startup-recovery"]


def test_app_hot_reload_drains_old_reply_then_queued_request_uses_new_dialogue(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    old_dialogue = BlockingDialogue()

    class NewDialogue:
        def __init__(self) -> None:
            self.calls = 0

        async def respond(self, _message: str) -> str:
            self.calls += 1
            return "new-runtime"

    new_dialogue = NewDialogue()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=old_dialogue,
    )

    with TestClient(app) as client:
        context = app.state.runtime_context
        published: list[str] = []

        async def rebuild(_new_config: object) -> None:
            context.dialogue = new_dialogue
            published.append("new")

        async def restart(
            _app: object,
            *,
            run_post_reload_llm_work: bool = True,
        ) -> None:
            del run_post_reload_llm_work

        context.rebuild_from_config = rebuild
        context.restart_background_tasks = restart

        with ThreadPoolExecutor(max_workers=2) as executor:
            active_old = executor.submit(
                client.post,
                "/api/chat",
                json={"message": "old request"},
            )
            assert old_dialogue.started.wait(timeout=1)

            reload_result = client.portal.start_task_soon(
                app.state._rebuild_runtime_with_lane_handoff,
                object(),
            )
            for _ in range(100):
                if app.state.dialogue_execution_coordinator.paused:
                    break
                time.sleep(0.005)
            assert app.state.dialogue_execution_coordinator.paused is True

            queued = executor.submit(
                client.post,
                "/api/chat",
                json={"message": "queued request"},
            )
            time.sleep(0.05)
            assert queued.done() is False
            assert published == []

            client.portal.call(old_dialogue.unblock)
            assert active_old.result(timeout=1).json()["reply"] == "后台回复完成"
            reload_result.result(timeout=1)
            assert queued.result(timeout=1).json()["reply"] == "new-runtime"

        assert published == ["new"]
        assert old_dialogue.calls == 1
        assert new_dialogue.calls == 1


def test_app_hot_reload_timeout_does_not_publish_and_resumes_old_dialogue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_module, "_DIALOGUE_EXECUTION_DRAIN_TIMEOUT_SECONDS", 0.01)
    database = _database(tmp_path)

    class FirstBlockingDialogue:
        def __init__(self) -> None:
            self.calls = 0
            self.started = threading.Event()
            self.release: asyncio.Event | None = None

        async def respond(self, _message: str) -> str:
            self.calls += 1
            if self.calls == 1:
                self.release = asyncio.Event()
                self.started.set()
                await self.release.wait()
            return "old-runtime"

        async def unblock(self) -> None:
            assert self.release is not None
            self.release.set()

    old_dialogue = FirstBlockingDialogue()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=old_dialogue,
    )

    with TestClient(app) as client:
        context = app.state.runtime_context
        rebuild_calls = 0

        async def rebuild(_new_config: object) -> None:
            nonlocal rebuild_calls
            rebuild_calls += 1

        async def restart(
            _app: object,
            *,
            run_post_reload_llm_work: bool = True,
        ) -> None:
            del run_post_reload_llm_work

        context.rebuild_from_config = rebuild
        context.restart_background_tasks = restart

        with ThreadPoolExecutor(max_workers=2) as executor:
            active_old = executor.submit(
                client.post,
                "/api/chat",
                json={"message": "long old request"},
            )
            assert old_dialogue.started.wait(timeout=1)

            reload_result = client.portal.start_task_soon(
                app.state._rebuild_runtime_with_lane_handoff,
                object(),
            )
            with pytest.raises(TimeoutError):
                reload_result.result(timeout=1)

            assert rebuild_calls == 0
            assert app.state.dialogue_execution_coordinator.paused is False
            queued_old = executor.submit(
                client.post,
                "/api/chat",
                json={"message": "queued old request"},
            )
            time.sleep(0.02)
            assert queued_old.done() is False

            client.portal.call(old_dialogue.unblock)
            assert active_old.result(timeout=1).json()["reply"] == "old-runtime"
            assert queued_old.result(timeout=1).json()["reply"] == "old-runtime"

        assert old_dialogue.calls == 2
