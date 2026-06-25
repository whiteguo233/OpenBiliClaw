"""PersonaGenerator — generate diverse simulated user profiles as ground truth.

Uses LLM to create realistic, self-consistent OnionProfile instances
that cover diverse personality types, interest patterns, and life stages.
"""

from __future__ import annotations

import json
import logging
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse

from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)

# Persona diversity dimensions for mini-batch sampling
PERSONA_DIMENSIONS: dict[str, list[str]] = {
    "mbti": ["INTJ", "ENFP", "ISTP", "ESFJ", "INFJ", "ENTP", "ISFP", "ESTJ"],
    "interest_breadth": ["specialist", "generalist"],
    "depth": ["casual", "moderate", "hardcore"],
    "age_group": ["student", "young_pro", "mid_career", "senior"],
    "usage": ["binge", "browse", "search_heavy"],
}


def _sample_constraints(count: int) -> list[dict[str, str]]:
    """Sample diverse constraint combinations for a mini-batch."""
    all_combos: list[dict[str, str]] = []
    for mbti in PERSONA_DIMENSIONS["mbti"]:
        for breadth in PERSONA_DIMENSIONS["interest_breadth"]:
            for depth in PERSONA_DIMENSIONS["depth"]:
                all_combos.append(
                    {
                        "mbti": mbti,
                        "interest_breadth": breadth,
                        "depth": depth,
                        "age_group": random.choice(PERSONA_DIMENSIONS["age_group"]),
                        "usage": random.choice(PERSONA_DIMENSIONS["usage"]),
                    }
                )
    random.shuffle(all_combos)
    return all_combos[:count]


def build_persona_prompt(constraints: dict[str, str]) -> list[dict[str, str]]:
    """Build LLM prompt for generating a simulated user persona."""
    system = """<task>
你是一个用户画像生成器。请根据给定的约束条件，生成一个虚构但合理、自洽的 B 站用户画像。
画像必须像一个真实的人——兴趣之间有关联，性格和行为模式一致，生活阶段和深层需求自洽。
</task>

<output_schema>
返回严格 JSON，结构如下：
{
  "personality_portrait": "200字以上的自然语言画像描述",
  "core": {
    "core_traits": ["3-5个核心性格特质"],
    "deep_needs": ["2-4个深层心理需求"],
    "mbti": {
      "type": "四字母类型",
      "dimensions": {
        "E_I": {"pole": "E或I", "strength": 0.5-1.0},
        "S_N": {"pole": "S或N", "strength": 0.5-1.0},
        "T_F": {"pole": "T或F", "strength": 0.5-1.0},
        "J_P": {"pole": "J或P", "strength": 0.5-1.0}
      },
      "confidence": 0.9
    }
  },
  "values_layer": {
    "values": ["2-4个核心价值观"],
    "motivational_drivers": ["2-3个内在驱动力"]
  },
  "interest": {
    "likes": [
      {
        "domain": "宽域名称",
        "weight": 0.5-1.0,
        "specifics": [{"name": "窄域名称", "weight": 0.5-1.0}]
      }
    ],
    "dislikes": [
      {"domain": "不喜欢的宽域", "weight": 0.7-1.0, "specifics": []}
    ],
    "favorite_up_users": ["1-3个UP主名称"]
  },
  "role": {
    "life_stage": "生活阶段描述",
    "current_phase": "当前状态描述"
  },
  "surface": {
    "cognitive_style": ["2-3个认知风格特征"],
    "exploration_openness": 0.3-0.9
  }
}
</output_schema>

<rules>
- 所有内容用中文
- 画像必须自洽：MBTI、兴趣、性格、生活阶段之间要有逻辑关联
- 兴趣要具体到 B 站上真实存在的内容领域和具体作品/UP主
- likes 至少 3 个宽域，每个宽域至少 2 个窄域
- dislikes 至少 1 个
- personality_portrait 至少 200 字
</rules>"""

    user = f"<constraints>\n{json.dumps(constraints, ensure_ascii=False, indent=2)}\n</constraints>"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class PersonaGenerator:
    """Generate diverse simulated user profiles for evaluation.

    Supports two backends:
    - Claude Agent SDK (default): uses `run_persona_agent()` from `agents.py`
    - Direct LLM: pass an `llm` instance for unit testing or non-SDK environments
    """

    def __init__(self, llm: Any = None, *, use_agent_sdk: bool = True) -> None:
        self._llm = llm
        self._use_agent_sdk = use_agent_sdk and llm is None

    async def generate(
        self,
        *,
        constraints: dict[str, str] | None = None,
    ) -> OnionProfile:
        """Generate a single ground truth OnionProfile."""
        if constraints is None:
            constraints = _sample_constraints(1)[0]

        if self._use_agent_sdk:
            from openbiliclaw.eval.agents import run_persona_agent

            return await run_persona_agent(constraints)

        # Fallback: direct LLM call
        messages = build_persona_prompt(constraints)
        response: LLMResponse = await self._llm.complete(
            messages,
            temperature=0.9,
            max_tokens=4096,
            json_mode=True,
        )
        return self._parse_response(response.content, constraints)

    async def generate_batch(self, count: int) -> list[OnionProfile]:
        """Generate a diverse mini-batch of personas."""
        constraint_list = _sample_constraints(count)
        personas: list[OnionProfile] = []
        for constraints in constraint_list:
            try:
                persona = await self.generate(constraints=constraints)
                personas.append(persona)
            except Exception:
                logger.warning("Failed to generate persona with %s", constraints)
        return personas

    def _parse_response(
        self,
        content: str,
        constraints: dict[str, str],
    ) -> OnionProfile:
        """Parse LLM response into OnionProfile."""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            msg = "Persona generation response must be a JSON object"
            raise ValueError(msg)

        # Set persona_id from constraints
        profile = OnionProfile.from_dict(data)
        return profile
