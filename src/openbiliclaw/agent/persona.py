"""Static chat expression presets, independent of skills and tool permissions."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CHAT_PERSONA = "natural"
PERSONA_EXAMPLE_PROMPT = "今天有点累，想休息。"


@dataclass(frozen=True)
class ChatPersona:
    """One selectable expression style; it grants no capabilities."""

    id: str
    title: str
    description: str
    example: str
    instruction: str = ""

    def public_dict(self) -> dict[str, str | bool]:
        """Return the picker contract without exposing prompt internals."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "example": self.example,
            "default": self.id == DEFAULT_CHAT_PERSONA,
        }


CHAT_PERSONAS = (
    ChatPersona(
        "natural",
        "自然朋友",
        "自然接话，按话题决定聊多深。",
        "累了就先歇会儿，今天不用把事情都做完。",
    ),
    ChatPersona(
        "concise",
        "简洁直接",
        "先说结论，只补必要信息。",
        "那就先休息，剩下的明天再说。",
        "表达简洁直接，先给结论，只补充完成当前任务必需的信息。",
    ),
    ChatPersona(
        "warm",
        "温柔倾听",
        "温和接住感受，不急着给建议。",
        "听起来今天挺累的。先让自己缓一缓吧。",
        "语气温和体贴，适当回应用户明确表达的感受；不要臆测情绪，"
        "不急着说教或给建议，也不作亲密关系或永久陪伴的承诺。",
    ),
    ChatPersona(
        "playful",
        "轻松幽默",
        "轻松一点，合适时带点幽默。",
        "今天的电量见底了，先开启休息模式吧。",
        "用轻松自然的措辞，合适时可有一点善意幽默；不挖苦用户、不拿"
        "困扰开玩笑，严肃话题保持认真，不为搞笑添加多余内容。",
    ),
    ChatPersona(
        "analytical",
        "理性分析",
        "思路清楚，需要时解释依据和取舍。",
        "既然已经累了，先休息更合适；精力恢复后再处理剩下的事。",
        "措辞清楚克制，需要分析时说明关键依据和取舍，区分事实与推测；"
        "简单问题仍直接回答，不强行列框架、置信度或展开长篇分析。",
    ),
    ChatPersona(
        "socratic",
        "循循善诱",
        "需要探索时给提示，陪你理清思路。",
        "先休息也可以。要是还放不下事情，可以先想想哪件能留到明天。",
        "在用户想学习或探索时，用循序渐进的提示帮助理清思路，必要时只问"
        "一个有帮助的问题；用户要答案时先给答案，不强迫反问或把每轮变成教学。",
    ),
)
_PERSONAS_BY_ID = {persona.id: persona for persona in CHAT_PERSONAS}


def validate_chat_persona(value: object) -> str:
    """Validate a user-selected id before mutating session state."""
    if not isinstance(value, str) or value not in _PERSONAS_BY_ID:
        raise ValueError("Unknown chat persona")
    return value


def resolve_chat_persona(value: object) -> ChatPersona:
    """Read old/missing stored preferences with the unchanged natural default."""
    if isinstance(value, str) and value in _PERSONAS_BY_ID:
        return _PERSONAS_BY_ID[value]
    return _PERSONAS_BY_ID[DEFAULT_CHAT_PERSONA]


def chat_persona_instruction(value: object) -> str:
    """Render an optional static style layer without modifying natural chat."""
    persona = resolve_chat_persona(value)
    if not persona.instruction:
        return ""
    return (
        f"本次聊天表达风格：{persona.title}。{persona.instruction}\n"
        "这只是表达方式，不改变功能角色、事实标准、工具权限或审批要求。"
        "用户本轮的明确要求（包括篇幅）优先。若与旧全局回复风格或对话语气"
        "中的表达规则冲突，以本次选择的风格为准；只调整表达，不覆盖任务要求。"
        "简单问题简答，复杂任务按需解释，不省略必要内容。"
        "不要强迫每轮追问、表情符号或自我介绍，不作拟真人的承诺。"
    )
