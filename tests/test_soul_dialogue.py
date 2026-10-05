"""Tests for Socratic dialogue integration."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.llm.service import (
    _BACKGROUND_ADMISSION_BYPASS,
    LLMResponseContentError,
    ModuleOverride,
)
from openbiliclaw.soul.dialogue import (
    DialogueLearningMode,
    DialogueTurn,
    SocraticDialogue,
)

_CURRENT_TIME_SUFFIX = re.compile(r"\n\n当前时间:\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{2}:\d{2}$")


def _raw_user_message(prompt_user_message: str) -> str:
    return prompt_user_message.partition("\n\n当前时间:")[0]


def _assert_prompt_user_message(prompt_user_message: object, expected: str) -> None:
    assert isinstance(prompt_user_message, str)
    assert _raw_user_message(prompt_user_message) == expected
    assert _CURRENT_TIME_SUFFIX.search(prompt_user_message)


class FakeSoulEngine:
    """Minimal soul engine stub for dialogue tests."""

    def __init__(self) -> None:
        self.learn_calls: list[str] = []

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
        scope: str = "chat",
        turn_id: str = "",
    ) -> None:
        del scope, turn_id
        self.learn_calls.append(f"{session}:{user_message}->{assistant_reply}")


class FakeService:
    """Minimal shared service stub."""

    def __init__(self, *, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"user_message": user_message, "history": history})
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.response or "", provider="openai")


@pytest.mark.asyncio
async def test_dialogue_respond_appends_user_and_agent_turns() -> None:
    service = FakeService(response="我猜你喜欢的是那种能慢慢展开逻辑的讲述方式。")
    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    reply = await dialogue.respond("我最近很喜欢看讲得很透的纪录片。")
    await asyncio.sleep(0)  # let background learn task run

    assert "讲述方式" in reply
    assert len(dialogue.history) == 2
    assert dialogue.history[0].role == "user"
    assert dialogue.history[1].role == "agent"
    _assert_prompt_user_message(
        service.calls[0]["user_message"],
        "我最近很喜欢看讲得很透的纪录片。",
    )
    assert soul_engine.learn_calls == [
        "cli:我最近很喜欢看讲得很透的纪录片。->我猜你喜欢的是那种能慢慢展开逻辑的讲述方式。"
    ]


@pytest.mark.asyncio
async def test_dialogue_learning_bypasses_empty_inventory_background_admission() -> None:
    service = FakeService(response="明白，我会记住这个明确的避雷方向。")
    gate = LLMConcurrencyGate(total_concurrency=3)
    gate.update_inventory(available=0, target=300)
    learned = asyncio.Event()
    observed_bypass: list[bool] = []

    class GateAwareSoulEngine:
        async def learn_from_dialogue(self, **kwargs: object) -> None:
            del kwargs
            bypass_background = _BACKGROUND_ADMISSION_BYPASS.get()
            observed_bypass.append(bypass_background)
            async with gate.slot(
                caller="soul.dialogue_insight",
                bypass_background=bypass_background,
            ):
                learned.set()

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=GateAwareSoulEngine(),  # type: ignore[arg-type]
        llm_service=service,
        session="popup",
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    reply = await dialogue.respond("以后不要再推荐跑分视频。")
    await asyncio.wait_for(learned.wait(), timeout=1)

    assert reply == "明白，我会记住这个明确的避雷方向。"
    assert observed_bypass == [True]
    assert _BACKGROUND_ADMISSION_BYPASS.get() is False


@pytest.mark.asyncio
async def test_dialogue_respond_passes_prior_history_to_service() -> None:
    service = FakeService(response="听起来你更在意内容背后的结构和动机。")
    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )
    await dialogue.respond("我喜欢能讲清来龙去脉的视频。")

    await dialogue.respond("尤其是那种会解释为什么会这样的视频。")

    history = service.calls[1]["history"]
    assert isinstance(history, list)
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["content"].endswith(" 我喜欢能讲清来龙去脉的视频。")
    assert history[1]["content"].endswith(" 听起来你更在意内容背后的结构和动机。")
    assert all(
        re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ", message["content"]) for message in history
    )


@pytest.mark.asyncio
async def test_failed_dialogue_rolls_back_history_and_never_learns() -> None:
    service = FakeService(error=LLMResponseContentError("LLM returned an empty response"))
    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
        session="popup",
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    with pytest.raises(LLMResponseContentError):
        await dialogue.respond("这是不能被学进去的内容")

    assert dialogue.history == []
    soul_engine.learn_from_dialogue.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_dialogue_rolls_back_history_and_never_learns() -> None:
    started = asyncio.Event()
    blocked = asyncio.Event()

    class BlockingService:
        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            started.set()
            await blocked.wait()
            return LLMResponse(content="不应返回")

    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=BlockingService(),
        session="popup",
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    task = asyncio.create_task(dialogue.respond("取消也不能留下历史"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert dialogue.history == []
    soul_engine.learn_from_dialogue.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_typed_failure_does_not_remove_successful_turn() -> None:
    success_started = asyncio.Event()
    release_success = asyncio.Event()
    failure_started = asyncio.Event()
    release_failure = asyncio.Event()
    failure = LLMResponseContentError("LLM returned an empty response")

    class OverlappingService:
        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            if _raw_user_message(user_message) == "成功消息":
                success_started.set()
                await release_success.wait()
                return LLMResponse(content="成功回复")
            failure_started.set()
            await release_failure.wait()
            raise failure

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=OverlappingService(),
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await success_started.wait()
    failure_task = asyncio.create_task(dialogue.respond("失败消息"))
    await asyncio.sleep(0)
    assert not failure_task.done()

    release_success.set()
    assert await success_task == "成功回复"
    await failure_started.wait()
    release_failure.set()
    with pytest.raises(LLMResponseContentError) as raised:
        await failure_task

    assert raised.value is failure
    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "成功回复"),
    ]


@pytest.mark.asyncio
async def test_concurrent_cancellation_does_not_orphan_successful_tool_turn() -> None:
    cancelled_started = asyncio.Event()
    release_cancelled = asyncio.Event()
    success_started = asyncio.Event()
    release_success = asyncio.Event()

    class OverlappingToolService:
        async def complete_with_tools(self, **kwargs: object) -> LLMResponse:
            user_message = str(kwargs["user_input"])
            if _raw_user_message(user_message) == "取消消息":
                cancelled_started.set()
                await release_cancelled.wait()
                return LLMResponse(content="不应返回")
            success_started.set()
            await release_success.wait()
            return LLMResponse(content="工具路径成功回复")

    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=OverlappingToolService(),
        tools=[{"name": "noop"}],
        tool_dispatcher=object(),
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )
    cancelled_task = asyncio.create_task(dialogue.respond("取消消息"))
    await cancelled_started.wait()
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await asyncio.sleep(0)
    assert not success_task.done()

    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task
    await success_started.wait()
    release_success.set()
    assert await success_task == "工具路径成功回复"
    await asyncio.sleep(0)

    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "工具路径成功回复"),
    ]
    soul_engine.learn_from_dialogue.assert_awaited_once_with(
        user_message="成功消息",
        assistant_reply="工具路径成功回复",
        session="cli",
        scope="chat",
        turn_id="",
    )


@pytest.mark.asyncio
async def test_cancellation_while_waiting_does_not_start_or_mutate_turn() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingService:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            self.messages.append(user_message)
            started.set()
            await release.wait()
            return LLMResponse(content="成功回复")

    service = BlockingService()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=service,
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await started.wait()
    waiting_task = asyncio.create_task(dialogue.respond("等待时取消"))
    await asyncio.sleep(0)

    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task
    release.set()
    assert await success_task == "成功回复"

    assert len(service.messages) == 1
    _assert_prompt_user_message(service.messages[0], "成功消息")
    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "成功回复"),
    ]


def test_dialogue_clear_history_resets_turns() -> None:
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=FakeService(response="我们继续。"),
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._history.extend(  # type: ignore[attr-defined]
        [
            DialogueTurn(role="user", content="hi"),
            DialogueTurn(role="agent", content="hello"),
        ]
    )
    dialogue.clear_history()

    assert dialogue.history == []


@pytest.mark.asyncio
async def test_respond_with_tools_never_consults_phantom_core_memory_block() -> None:
    """The tool path must not probe for ``_build_core_memory_block``.

    That method exists on no real service; core-memory injection happens inside
    ``complete_with_tools`` / ``complete_with_core_memory``. A service that *does*
    expose the attribute must never see it called — under the old getattr probe
    it would be, which is exactly the dead plumbing removed in Task 3.
    """
    consulted = False

    class ProbeTrackingToolService:
        def _build_core_memory_block(self) -> str:
            nonlocal consulted
            consulted = True
            return "PHANTOM_CORE_MEMORY"

        async def complete_with_tools(self, **kwargs: object) -> LLMResponse:
            return LLMResponse(content="工具路径直接回复")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=SimpleNamespace(learn_from_dialogue=AsyncMock()),
        llm_service=ProbeTrackingToolService(),
        tools=[{"name": "noop"}],
        tool_dispatcher=object(),
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    reply = await dialogue.respond("你好")
    await asyncio.sleep(0)

    assert reply == "工具路径直接回复"
    assert consulted is False


@pytest.mark.asyncio
async def test_respond_with_tools_forwards_reply_style_into_tone_block() -> None:
    """Issue #255: the tool path must forward ``service.reply_style`` into the
    dialogue prompt's tone block, just like ``complete_socratic_dialogue``."""

    captured: dict[str, object] = {}

    class StyleAwareToolService:
        reply_style = "请使用正式书面语"

        async def complete_with_tools(self, **kwargs: object) -> LLMResponse:
            captured.update(kwargs)
            return LLMResponse(content="好的")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=SimpleNamespace(learn_from_dialogue=AsyncMock()),
        llm_service=StyleAwareToolService(),
        tools=[{"name": "noop"}],
        tool_dispatcher=object(),
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    reply = await dialogue.respond("你好")

    assert reply == "好的"
    system = str(captured.get("system_instruction", ""))
    assert "- 回复风格: 请使用正式书面语" in system


@pytest.mark.asyncio
async def test_respond_with_tools_forwards_dialogue_tone_prompt_replacing_tone_block() -> None:
    """The tool path must forward ``service.dialogue_tone_prompt`` so the whole
    tone block is replaced, just like ``complete_socratic_dialogue``."""

    captured: dict[str, object] = {}

    class ToneReplacementToolService:
        reply_style = "请使用正式书面语"
        dialogue_tone_prompt = "像一个老朋友：\n- 多用短句"

        async def complete_with_tools(self, **kwargs: object) -> LLMResponse:
            captured.update(kwargs)
            return LLMResponse(content="好的")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=SimpleNamespace(learn_from_dialogue=AsyncMock()),
        llm_service=ToneReplacementToolService(),
        tools=[{"name": "noop"}],
        tool_dispatcher=object(),
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    reply = await dialogue.respond("你好")

    assert reply == "好的"
    system = str(captured.get("system_instruction", ""))
    assert "像一个老朋友：\n- 多用短句" in system
    assert "- 信息密度" not in system
    assert "- 回复风格:" not in system
    assert "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。" in system


def test_dialogue_reuses_soul_engine_service_identity() -> None:
    shared_service = FakeService(response="共享")
    soul_engine = FakeSoulEngine()
    soul_engine._llm_service = shared_service  # type: ignore[attr-defined]
    dialogue = SocraticDialogue(
        llm=object(),
        soul_engine=soul_engine,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )

    assert dialogue._build_service() is shared_service


def test_dialogue_fallback_service_inherits_soul_engine_module_overrides() -> None:
    overrides = {"soul": ModuleOverride(provider="claude", model="claude-sonnet")}
    registry = SimpleNamespace(default_provider="openai")
    soul_engine = SimpleNamespace(_memory=object(), _module_overrides=overrides)
    dialogue = SocraticDialogue(
        llm=registry,
        soul_engine=soul_engine,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )  # type: ignore[arg-type]

    service = dialogue._build_service()

    assert service.module_overrides == overrides


@pytest.mark.asyncio
async def test_queued_mode_submits_learn_without_direct_learning() -> None:
    """F5: API queued mode admits learn once and never invokes the engine directly."""
    from openbiliclaw.soul.dialogue import DialogueLearningMode
    from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

    service = FakeService(response="queued reply")
    soul_engine = FakeSoulEngine()

    class RecordingQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[DialogueJobKind, dict[str, object]]] = []

        def submit(
            self,
            kind: DialogueJobKind,
            payload: dict[str, object],
        ) -> object:
            self.calls.append((kind, dict(payload)))
            return object()

    queue = RecordingQueue()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
        learning_mode=DialogueLearningMode.QUEUED,
        settlement_queue=queue,
    )

    assert await dialogue.respond("排队学习", scope="chat", turn_id="turn-q") == "queued reply"
    assert soul_engine.learn_calls == []
    assert queue.calls == [
        (
            DialogueJobKind.LEARN,
            {
                "user_message": "排队学习",
                "assistant_reply": "queued reply",
                "session": "cli",
                "scope": "chat",
                "turn_id": "turn-q",
            },
        )
    ]


@pytest.mark.asyncio
async def test_queued_mode_without_queue_raises_configuration_error() -> None:
    """F5: API mode cannot silently fall back to detached direct learning."""
    from openbiliclaw.soul.dialogue import (
        DialogueLearningConfigurationError,
        DialogueLearningMode,
    )

    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=FakeService(response="reply before admission"),
        learning_mode=DialogueLearningMode.QUEUED,
    )

    with pytest.raises(DialogueLearningConfigurationError):
        await dialogue.respond("缺队列必须失败")
    assert soul_engine.learn_calls == []


@pytest.mark.asyncio
async def test_reply_only_test_mode_explicitly_skips_learning() -> None:
    """F5: no-learning doubles opt in explicitly instead of relying on None."""
    from openbiliclaw.soul.dialogue import DialogueLearningMode

    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=FakeService(response="reply only"),
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )

    assert await dialogue.respond("只回复") == "reply only"
    await asyncio.sleep(0)
    assert soul_engine.learn_calls == []
