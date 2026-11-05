"""PersonaJudge — virtual persona evaluates speculative interests.

Uses Claude Agent SDK to role-play as a virtual persona and judge whether
each speculative interest genuinely resonates. Returns per-speculation
resonance scores that feed into the SpeculationEvaluator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResonanceVerdict:
    """A virtual persona's judgment on one speculative interest."""

    domain: str
    would_click: bool = False
    resonance_score: float = 0.0
    reasoning: str = ""


@dataclass(frozen=True)
class PersonaJudgment:
    """Complete judgment from one virtual persona on a batch of speculations."""

    persona_summary: str = ""
    verdicts: tuple[ResonanceVerdict, ...] = ()
    mean_resonance: float = 0.0


async def judge_speculations(
    *,
    persona_context: str,
    speculations: list[dict[str, str]],
    max_retries: int = 2,
) -> PersonaJudgment:
    """Have a virtual persona judge a batch of speculative interests.

    The Claude Agent SDK acts AS the persona (not as an evaluator looking
    at the persona from outside). The persona answers naturally whether
    each direction genuinely appeals to them.

    Args:
        persona_context: Full persona description (to_llm_context output).
        speculations: List of {"domain", "reason"} dicts to judge.
        max_retries: SDK call retry count.

    Returns:
        PersonaJudgment with per-speculation verdicts.
    """
    if not speculations:
        return PersonaJudgment(persona_summary=persona_context[:100])

    spec_lines = "\n".join(
        f"{i}. {s.get('domain', '')} — {s.get('reason', '')}" for i, s in enumerate(speculations, 1)
    )

    system_prompt = (
        "你现在是以下这个人。请完全进入角色，用第一人称回答。\n"
        "不要跳出角色分析，不要说'作为AI'，就是这个人在说话。\n\n"
        f"{persona_context}\n\n"
        "你的任务：对每个推荐方向，诚实回答你会不会感兴趣。\n"
        "回答时基于你的真实性格、认知偏好和当前状态来判断，\n"
        "不要因为觉得'应该拓展视野'就说感兴趣——只有真正会点开看的才算。"
    )

    user_prompt = (
        "有人想给你推荐以下几个新领域的内容，你看看哪些你真的会感兴趣：\n\n"
        f"{spec_lines}\n\n"
        "对每一个方向，请回答：\n"
        "1. would_click: 如果 B 站首页出现这类内容，你会不会点进去看？(true/false)\n"
        "2. resonance_score: 0-1 之间，0=完全不感兴趣 1=非常想看\n"
        "3. reasoning: 用一句话说明为什么（用你自己的话，不要复述推理依据）\n\n"
        '返回 JSON: {"verdicts": [{"domain": "...", "would_click": true, '
        '"resonance_score": 0.7, "reasoning": "..."}]}'
    )

    try:
        from openbiliclaw.eval.agents import collect_json
        from openbiliclaw.llm.sponsored_router import sponsored_enabled

        if sponsored_enabled():
            from .sponsored_agents import agent_options
        else:
            agent_options = import_module("claude_agent_sdk").ClaudeAgentOptions

        result = await collect_json(
            prompt=user_prompt,
            options=agent_options(
                system_prompt=system_prompt,
                max_turns=2,
            ),
            max_retries=max_retries,
            label="persona_judge",
        )

        raw_verdicts = result.get("verdicts", [])
        if not isinstance(raw_verdicts, list):
            raw_verdicts = []

        verdicts = _parse_verdicts(raw_verdicts, speculations)
        mean = sum(v.resonance_score for v in verdicts) / len(verdicts) if verdicts else 0.0

        return PersonaJudgment(
            persona_summary=persona_context[:100],
            verdicts=tuple(verdicts),
            mean_resonance=round(mean, 4),
        )

    except Exception:
        logger.warning("PersonaJudge failed, returning neutral scores", exc_info=True)
        fallback = tuple(
            ResonanceVerdict(
                domain=s.get("domain", ""),
                resonance_score=0.5,
                reasoning="evaluation failed",
            )
            for s in speculations
        )
        return PersonaJudgment(
            persona_summary=persona_context[:100],
            verdicts=fallback,
            mean_resonance=0.5,
        )


def _parse_verdicts(
    raw_verdicts: list[Any],
    speculations: list[dict[str, str]],
) -> list[ResonanceVerdict]:
    """Parse and align verdicts with speculation list."""
    # Build domain lookup for alignment
    spec_domains = [s.get("domain", "") for s in speculations]
    verdict_map: dict[str, ResonanceVerdict] = {}

    for item in raw_verdicts:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "")).strip()
        if not domain:
            continue
        score = item.get("resonance_score", 0.5)
        if isinstance(score, bool):
            score = 1.0 if score else 0.0
        elif not isinstance(score, (int, float)):
            score = 0.5
        verdict_map[domain] = ResonanceVerdict(
            domain=domain,
            would_click=bool(item.get("would_click", False)),
            resonance_score=max(0.0, min(1.0, float(score))),
            reasoning=str(item.get("reasoning", ""))[:200],
        )

    # Align with input order, filling gaps
    result: list[ResonanceVerdict] = []
    for domain in spec_domains:
        if domain in verdict_map:
            result.append(verdict_map[domain])
        else:
            # Try fuzzy match (domain might be slightly rephrased)
            matched = _fuzzy_match_verdict(domain, verdict_map)
            if matched is not None:
                result.append(matched)
            else:
                result.append(
                    ResonanceVerdict(
                        domain=domain,
                        resonance_score=0.5,
                        reasoning="no verdict returned",
                    )
                )
    return result


def _fuzzy_match_verdict(
    target: str,
    verdict_map: dict[str, ResonanceVerdict],
) -> ResonanceVerdict | None:
    """Try to match a domain name against verdict keys with fuzzy logic."""
    target_lower = target.lower()
    for key, verdict in verdict_map.items():
        key_lower = key.lower()
        if target_lower in key_lower or key_lower in target_lower:
            return verdict
    return None
