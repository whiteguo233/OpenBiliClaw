"""Skill-related meta tools for the agent loop (M4).

``suggest_skill`` is registered into every skill's tool subset: it lets the
agent *propose* a skill switch without performing one. The actual switch is
client-driven — the frontend renders the ``suggest_skill`` tool call as a
switch card and, once the user confirms, sends the next turn with the new
``skill`` field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import Tool

if TYPE_CHECKING:
    from collections.abc import Iterable

SUGGEST_SKILL_TOOL_NAME = "suggest_skill"


def build_suggest_skill_tool(skill_names: Iterable[str]) -> Tool:
    """Build the ``suggest_skill`` meta tool bound to the available skills.

    The handler only validates and records the suggestion — switching is
    decided by the user and executed by the client on the next turn.
    """
    names = [name.strip() for name in skill_names if name.strip()]

    def _handler(arguments: dict[str, Any]) -> str:
        target = str(arguments.get("skill") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if target not in names:
            return f"切换建议无效：不存在名为 {target!r} 的 skill。可选：{', '.join(names)}"
        suffix = f"，理由：{reason}" if reason else ""
        return (
            f"已生成切换到 skill「{target}」的建议{suffix}。"
            "请用自然语言向用户说明为什么建议切换，并等待用户确认；"
            "用户确认后，前端会以新 skill 发起下一回合，你不需要也不能自行切换。"
        )

    return Tool(
        name=SUGGEST_SKILL_TOOL_NAME,
        description=(
            "建议切换到另一个更合适的 skill（角色）。仅在用户诉求明显超出当前 "
            "skill 职责时使用；这只生成建议，是否切换由用户决定。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "enum": names,
                    "description": "建议切换到的 skill 名称",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么建议切换（会展示给用户）",
                },
            },
            "required": ["skill"],
            "additionalProperties": False,
        },
        handler=_handler,
        permission_level="read",
    )
