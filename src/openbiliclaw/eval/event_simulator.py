"""EventSimulator — generate simulated behavioral events from a persona.

Given a ground truth OnionProfile, generates realistic B站 behavioral events
(views, searches, likes, dislikes, dialogues) that such a user would produce.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.sponsored_router import complete_project_task

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


def build_event_simulation_prompt(
    persona: OnionProfile,
    event_count: int,
) -> list[dict[str, str]]:
    """Build LLM prompt for generating simulated events from a persona."""
    # Build a compact persona summary for the prompt
    persona_summary = persona.to_llm_context()

    system = f"""<task>
你是一个用户行为模拟器。根据给定的用户画像，生成该用户在 B 站上的行为事件序列。
事件必须符合用户的兴趣、性格和使用习惯。
</task>

<user_profile>
{persona_summary}
</user_profile>

<output_schema>
返回严格 JSON，格式为事件数组：
{{
  "events": [
    {{
      "event_type": "view|search|like|coin|favorite|comment|feedback|dialogue",
      "title": "视频标题或搜索词",
      "metadata": {{
        "bvid": "BV...",
        "duration": 600,
        "progress": 0.95,
        "up_name": "UP主名称",
        "tags": ["标签1", "标签2"]
      }}
    }}
  ]
}}
</output_schema>

<rules>
- 生成 {event_count} 条事件
- 事件类型分布要符合用户特征：
  - 深度用户：更多 view（高完播率）+ search，少 scroll
  - 轻度用户：更多 click + scroll，完播率低
  - 专注型：同一领域连续多条 view
  - 探索型：跨领域切换频繁
- 视频标题要像真实 B 站视频标题（具体、有吸引力）
- 搜索词要符合用户兴趣领域
- feedback 事件用 like/dislike 表示对推荐的反馈
- dialogue 事件是用户在聊天中说的话（自然语言）
- view 事件的 progress 表示完播率 (0.0-1.0)
- 所有内容用中文
- bvid 随机生成格式为 "BV" + 10位字母数字
</rules>"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"请生成 {event_count} 条行为事件。"},
    ]


class EventSimulator:
    """Generate simulated behavioral events from a ground truth persona.

    Supports two backends:
    - Claude Agent SDK (default): uses `run_event_agent()` from `agents.py`
    - Direct LLM: pass an `llm` instance for unit testing
    """

    def __init__(self, llm: Any = None, *, use_agent_sdk: bool = True) -> None:
        self._llm = llm
        self._use_agent_sdk = use_agent_sdk and llm is None

    async def simulate(
        self,
        persona: OnionProfile,
        *,
        event_count: int = 100,
    ) -> list[dict[str, object]]:
        """Generate simulated events matching the persona."""
        if self._use_agent_sdk:
            from openbiliclaw.eval.agents import run_event_agent

            return await run_event_agent(persona, event_count=event_count)

        # Fallback: direct LLM call
        messages = build_event_simulation_prompt(persona, event_count)
        response: LLMResponse = await complete_project_task(
            self._llm,
            messages,
            temperature=0.8,
            max_tokens=8192,
            json_mode=True,
        )
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> list[dict[str, object]]:
        """Parse LLM response into event list."""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()

        data = json.loads(text)

        # Handle both {"events": [...]} and bare [...]
        if isinstance(data, dict):
            events = data.get("events", [])
        elif isinstance(data, list):
            events = data
        else:
            msg = "Event simulation response must be a JSON object or array"
            raise ValueError(msg)

        if not isinstance(events, list):
            return []

        # Normalize and validate events
        result: list[dict[str, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type", ""))
            if not event_type:
                continue
            result.append(
                {
                    "event_type": event_type,
                    "title": str(event.get("title", "")),
                    "url": str(event.get("url", "")),
                    "metadata": event.get("metadata", {}),
                    # v0.3.23+: align eval simulator with the unified
                    # event_format string contract. Was {}.
                    "context": event.get("context", ""),
                }
            )

        return result
