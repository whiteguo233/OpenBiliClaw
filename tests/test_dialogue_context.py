"""Baseline snapshots for the Phase 1 dialogue window + regurgitation work.

Committed BEFORE the window/regurgitation/builder changes (plan Task 3, step
"基线先行"). These lock the ≤-window prompt bytes and the current
dialogue-insight extraction prompt so the later refactor is provably
behaviour-equivalent for in-window sessions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.api.models import ChatTurnOut
from openbiliclaw.soul.dialogue import (
    DIALOGUE_WINDOW_TURNS,
    DialogueLearningMode,
    DialogueTurn,
    SocraticDialogue,
)
from openbiliclaw.soul.dialogue_turn_context import (
    BindingMode,
    DialogueBindingError,
    DialogueTurnBinding,
    DialogueTurnContext,
    filter_evidence_labels,
)
from openbiliclaw.soul.identity import build_hash8_map, insight_hash8
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


_FIXED_TURN_TIMESTAMP = "2026-07-22T09:30:00+08:00"
_UTC_PLUS_8 = timezone(timedelta(hours=8))


def test_dialogue_context_digest_is_canonical_and_evidence_is_readable_only() -> None:
    evidence = filter_evidence_labels(
        [
            "42",
            "e3617163",
            "550e8400-e29b-41d4-a716-446655440000",
            "BV1abcdefghij",
            "av123",
            "event-7",
            "turn:opaque",
            "完整看完三条长视频",
            "完整看完三条长视频",
            "https://example.test/context",
        ]
    )
    assert evidence == ("完整看完三条长视频", "https://example.test/context")
    context = DialogueTurnContext(
        reply_to_turn_id="turn-a",
        source_type="card",
        kind="hypothesis",
        ref="ref-a",
        generation=7,
        anchor_origin_turn_id="turn-a",
        title="A 的标题",
        evidence_labels=evidence,
        captured_at="2026-08-01T12:00:00+08:00",
    )
    retry_context = DialogueTurnContext.from_mapping(
        {**context.to_mapping(), "captured_at": "2026-08-01T12:05:00+08:00"}
    )
    assert context.context_digest == retry_context.context_digest
    assert len(context.context_digest) == 64
    binding = DialogueTurnBinding.from_context(context)
    assert binding.mode is BindingMode.BOUND
    assert "ref-a" not in binding.render_user_prompt("这句话")
    assert "A 的标题" in binding.render_user_prompt("这句话")


def test_dialogue_context_rejects_unknown_or_mismatched_facts() -> None:
    with pytest.raises(DialogueBindingError):
        DialogueTurnContext.from_mapping(
            {
                "reply_to_turn_id": "turn-a",
                "source_type": "card",
                "kind": "confusion",
                "ref": "ref-a",
                "generation": 7,
                "anchor_origin_turn_id": "turn-a",
                "title": "错配",
            }
        )
    with pytest.raises(DialogueBindingError):
        DialogueTurnBinding.from_mapping(
            {
                "mode": "detached",
                "context_digest": "forged",
                "context": {},
                "inventory_settles_allowed": False,
            }
        )
    with pytest.raises(DialogueBindingError):
        DialogueTurnBinding.from_mapping({"mode": "detached", "inventory_settles_allowed": "false"})
    with pytest.raises(DialogueBindingError):
        DialogueTurnContext.from_mapping(
            {
                "version": True,
                "reply_to_turn_id": "turn-a",
                "source_type": "card",
                "kind": "hypothesis",
                "ref": "ref-a",
                "generation": 1,
                "anchor_origin_turn_id": "turn-a",
                "title": "标题",
            }
        )


def _dialogue_with_history(exchanges: int) -> SocraticDialogue:
    dialogue = SocraticDialogue(  # type: ignore[arg-type]
        llm=None,
        soul_engine=object(),
        session="popup",
        local_timezone=_UTC_PLUS_8,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    for i in range(exchanges):
        dialogue._history.append(
            DialogueTurn(
                role="user",
                content=f"用户消息{i}",
                timestamp=_FIXED_TURN_TIMESTAMP,
            )
        )
        dialogue._history.append(
            DialogueTurn(
                role="agent",
                content=f"助手回复{i}",
                timestamp=_FIXED_TURN_TIMESTAMP,
            )
        )
    # An in-flight user turn is appended by respond() before _history_to_messages.
    dialogue._history.append(DialogueTurn(role="user", content="当前用户消息"))
    return dialogue


def test_history_to_messages_within_window_is_unchanged() -> None:
    # 20 exchanges = 40 prior messages, all retained (window == 20 exchanges).
    dialogue = _dialogue_with_history(20)
    messages = dialogue._history_to_messages()
    assert len(messages) == 40
    assert messages[0] == {"role": "user", "content": "[07-22 09:30] 用户消息0"}
    assert messages[1] == {"role": "assistant", "content": "[07-22 09:30] 助手回复0"}
    assert messages[-1] == {"role": "assistant", "content": "[07-22 09:30] 助手回复19"}


def test_history_to_messages_maps_agent_role_to_assistant() -> None:
    dialogue = _dialogue_with_history(1)
    messages = dialogue._history_to_messages()
    assert messages == [
        {"role": "user", "content": "[07-22 09:30] 用户消息0"},
        {"role": "assistant", "content": "[07-22 09:30] 助手回复0"},
    ]


def test_history_truncated_to_window_when_over_limit() -> None:
    # 25 exchanges → only the last DIALOGUE_WINDOW_TURNS (20) are kept.
    dialogue = _dialogue_with_history(25)
    messages = dialogue._history_to_messages()
    assert len(messages) == DIALOGUE_WINDOW_TURNS * 2
    # Oldest retained exchange is #5 (25 - 20).
    assert messages[0] == {"role": "user", "content": "[07-22 09:30] 用户消息5"}
    assert messages[-1] == {"role": "assistant", "content": "[07-22 09:30] 助手回复24"}


def test_agent_transcript_keeps_window_roles_and_reply_context_without_mutation() -> None:
    dialogue = _dialogue_with_history(25)
    dialogue._history[-3].relation_prefix = "[回复卡片「喜欢安静的科普」]"
    legacy_before = dialogue._history_to_messages()

    messages = dialogue._history_to_messages(as_transcript=True)

    assert len(messages) == 1 and messages[0]["role"] == "user"
    records = json.loads(messages[0]["content"].split("\n", 1)[1])
    assert len(records) == DIALOGUE_WINDOW_TURNS * 2
    assert records[0] == {"role": "user", "content": "用户消息5", "local_time": "07-22 09:30"}
    assert records[-1] == {
        "role": "assistant",
        "content": "助手回复24",
        "local_time": "07-22 09:30",
    }
    assert records[-2]["reply_context"] == "[回复卡片「喜欢安静的科普」]"
    assert "当前用户消息" not in messages[0]["content"]
    assert dialogue._history_to_messages() == legacy_before


def test_history_rendering_has_no_now_parameter() -> None:
    assert "now" not in inspect.signature(SocraticDialogue._history_to_messages).parameters


def test_regurgitated_utc_timestamp_uses_injected_local_timezone(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(turn_id="utc-turn", message="用户", session="popup", scope="chat")
    db.complete_chat_turn("utc-turn", reply="助手")
    db.conn.execute(
        "UPDATE chat_turns SET created_at = '2026-07-22 01:30:00' WHERE turn_id = 'utc-turn'"
    )
    db.conn.commit()
    dialogue = SocraticDialogue(  # type: ignore[arg-type]
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,
        local_timezone=_UTC_PLUS_8,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._ensure_history_loaded()
    dialogue._history.append(DialogueTurn(role="user", content="当前轮"))

    assert dialogue._history_to_messages() == [
        {"role": "user", "content": "[07-22 09:30] 用户"},
        {"role": "assistant", "content": "[07-22 09:30] 助手"},
    ]


async def test_current_time_is_only_appended_to_user_prompt_tail() -> None:
    fixed_now = datetime(2026, 7, 22, 10, 45, tzinfo=_UTC_PLUS_8)

    class FakeService:
        def __init__(self) -> None:
            self.user_message = ""
            self.history: list[dict[str, str]] = []

        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str,
        ) -> object:
            self.user_message = user_message
            self.history = history
            return type("Response", (), {"content": "收到"})()

    service = FakeService()
    dialogue = SocraticDialogue(  # type: ignore[arg-type]
        llm=None,
        soul_engine=object(),
        llm_service=service,
        local_timezone=_UTC_PLUS_8,
        now_provider=lambda: fixed_now,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._history.extend(
        [
            DialogueTurn(role="user", content="上一问", timestamp=_FIXED_TURN_TIMESTAMP),
            DialogueTurn(role="agent", content="上一答", timestamp=_FIXED_TURN_TIMESTAMP),
        ]
    )

    await dialogue.respond("现在几点？")

    assert service.user_message.endswith("\n\n当前时间:2026-07-22 10:45 +08:00")
    assert service.user_message.startswith("现在几点？")
    assert all("当前时间:" not in message["content"] for message in service.history)
    assert dialogue.history[-2].content == "现在几点？"


def test_chat_turn_out_exposes_structured_payload() -> None:
    turn = ChatTurnOut(
        turn_id="card-1",
        payload={"type": "card", "state": "pending"},
    )

    assert turn.model_dump()["payload"] == {"type": "card", "state": "pending"}


# ---------------------------------------------------------------------------
# Regurgitation (all UI sessions + completed chat/hypothesis/confusion)
# ---------------------------------------------------------------------------


def _db_with_turns(tmp_path: Path) -> Database:
    db = Database(tmp_path / "chat.db")
    db.initialize()
    return db


def test_regurgitation_loads_completed_popup_chat_turns(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(turn_id="t1", message="用户一", session="popup", scope="chat")
    db.complete_chat_turn("t1", reply="回复一")
    db.create_chat_turn(turn_id="t2", message="用户二", session="popup", scope="chat")
    db.complete_chat_turn("t2", reply="回复二")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._ensure_history_loaded()
    contents = [(t.role, t.content) for t in dialogue.history]
    assert contents == [
        ("user", "用户一"),
        ("agent", "回复一"),
        ("user", "用户二"),
        ("agent", "回复二"),
    ]


def test_regurgitation_excludes_pending_probe_and_cli(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    # Pending (not completed) — excluded.
    db.create_chat_turn(turn_id="p1", message="待处理", session="popup", scope="chat")
    # Probe scope — excluded.
    db.create_chat_turn(turn_id="pr1", message="探针", session="popup", scope="probe")
    db.complete_chat_turn("pr1", reply="探针回复")
    # Completed chat — included.
    db.create_chat_turn(turn_id="c1", message="正常", session="popup", scope="chat")
    db.complete_chat_turn("c1", reply="正常回复")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._ensure_history_loaded()
    assert [(t.role, t.content) for t in dialogue.history] == [
        ("user", "正常"),
        ("agent", "正常回复"),
    ]


def test_regurgitation_skipped_for_cli_session(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(turn_id="c1", message="正常", session="popup", scope="chat")
    db.complete_chat_turn("c1", reply="正常回复")
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="cli",
        database=db,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._ensure_history_loaded()
    assert dialogue.history == []


def test_regurgitation_is_one_history_across_sessions_and_confirmation_scopes(
    tmp_path: Path,
) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(
        turn_id="popup-chat",
        message="普通问题",
        session="popup",
        scope="chat",
    )
    db.complete_chat_turn("popup-chat", reply="普通回答")
    db.create_chat_turn(
        turn_id="web-card",
        message="阿b 的猜测",
        session="webui",
        scope="hypothesis",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": "abc12345",
            "title": "用户偏爱深度内容",
            "state": "pending",
        },
    )
    db.complete_chat_turn("web-card", reply="")
    db.create_chat_confirmation_turn(
        turn_id="popup-question",
        session="popup",
        scope="confusion",
        ref="7",
        title="收藏后马上退出",
        message="",
        reply="这次收藏和停留时长相反，你愿意说说吗？",
        payload={
            "type": "question",
            "kind": "confusion",
            "ref": "7",
            "title": "收藏后马上退出",
            "state": "clarifying",
        },
    )
    db.create_chat_turn(
        turn_id="web-confusion",
        message="我只是把它当背景音",
        session="webui",
        scope="confusion",
    )
    db.complete_chat_turn("web-confusion", reply="明白了，这不代表稳定兴趣。")
    db.create_chat_turn(
        turn_id="excluded-probe",
        message="探针",
        session="popup",
        scope="probe",
    )
    db.complete_chat_turn("excluded-probe", reply="不应进入认知历史")
    db.conn.execute(
        """
        UPDATE chat_turns
        SET created_at = CASE turn_id
            WHEN 'popup-chat' THEN '2026-07-22 01:00:00'
            WHEN 'web-card' THEN '2026-07-22 01:01:00'
            WHEN 'popup-question' THEN '2026-07-22 01:01:30'
            WHEN 'web-confusion' THEN '2026-07-22 01:02:00'
            ELSE '2026-07-22 01:03:00'
        END
        """
    )
    db.conn.commit()

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    dialogue._ensure_history_loaded()

    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "普通问题"),
        ("agent", "普通回答"),
        ("agent", "用户偏爱深度内容"),
        ("agent", "这次收藏和停留时长相反，你愿意说说吗？"),
        ("user", "我只是把它当背景音"),
        ("agent", "明白了，这不代表稳定兴趣。"),
    ]


async def test_durable_request_session_overrides_dialogue_default_for_learning() -> None:
    learned: list[dict[str, object]] = []

    class FakeSoul:
        async def learn_from_dialogue(self, **payload: object) -> None:
            learned.append(dict(payload))

    class FakeService:
        async def complete_socratic_dialogue(self, **_kwargs: object) -> object:
            return type("Response", (), {"content": "收到"})()

    dialogue = SocraticDialogue(  # type: ignore[arg-type]
        llm=None,
        soul_engine=FakeSoul(),
        llm_service=FakeService(),
        session="popup",
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )

    await dialogue.respond("来自桌面端", session="webui", turn_id="turn-web")
    for _ in range(20):
        if learned:
            break
        await asyncio.sleep(0)

    assert learned[0]["session"] == "webui"


# ---------------------------------------------------------------------------
# hash8 identity
# ---------------------------------------------------------------------------


def test_insight_hash8_is_stable_and_canonicalizes_whitespace() -> None:
    a = insight_hash8("用户 通过 深度内容 获得掌控感")
    b = insight_hash8("  用户   通过 深度内容\n获得掌控感  ")
    assert a == b  # NFC + strip + whitespace-collapse → same key
    assert len(a) == 8
    assert insight_hash8("完全不同的假设") != a


def test_build_hash8_map_distinct_texts_use_hex8() -> None:
    mapping = build_hash8_map(["假设甲", "假设乙"])
    assert len(mapping) == 2
    assert all(len(k) == 8 for k in mapping)
    assert set(mapping.values()) == {"假设甲", "假设乙"}
