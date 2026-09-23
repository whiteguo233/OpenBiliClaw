"""Production registrations for the Sponsored Runtime pilot tasks.

Only tasks listed here may access the SiliconFlow Sponsored endpoint. The
closed-source runtime checks the signed policy built from this registry; it
does not import this module.

Pilot: ``soul.consolidation``
- system prompt is static and cache-friendly;
- the task already opts out of core-memory injection;
- the payload is small, deterministic JSON, which makes it a good schema
  validation target before broader rollout.

Prompt or schema changes here require regenerating and re-signing the
sponsored policy; the golden-hash test in ``tests/test_sponsored_contracts.py``
is intentionally allowed to fail when that happens.
"""

from __future__ import annotations

from typing import Any

from .prompt_contracts import ensure_json_mode_contract
from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

SPONSORED_MODEL = "XingChenAGI/Xing4.0-29B"
SPONSORED_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"

CONSOLIDATION_MAX_INPUT_BYTES = 256 * 1024
# Match the legacy consolidation budget (`DEFAULT_STRUCTURED_MAX_TOKENS`).
# 4096 was too small for Xing4.0's thinking: the model spent the whole budget
# on reasoning and returned empty content, which the runtime maps to
# UPSTREAM_ERROR.
CONSOLIDATION_MAX_OUTPUT_TOKENS = 16384
# Per-install fairness budget derived from *normal user usage*, per the
# product decision: total account capacity must never be used to tighten a
# normal user's quota. Increase accounts/budget or degrade gracefully instead.
# These values are placeholders until real per-task usage is measured; abuse is
# bounded by the account balance + account-level rate limits, not by making a
# legitimate user's budget tiny.
CONSOLIDATION_DAILY_REQUESTS = 200
CONSOLIDATION_DAILY_TOKENS = 500_000


def _pair_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"type": "string", "maxLength": 256},
    }


def _known_distinct_pairs_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 500,
        "items": _pair_schema(),
    }


SOUL_CONSOLIDATION_REQUEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenBiliClaw sponsored request: soul.consolidation.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["likes_clusters", "dislikes_clusters"],
    "properties": {
        "likes_clusters": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cluster_id", "members"],
                "properties": {
                    "cluster_id": {"type": "string", "maxLength": 128},
                    "known_distinct_pairs": _known_distinct_pairs_schema(),
                    "members": {
                        "type": "array",
                        "maxItems": 500,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "weight", "category"],
                            "properties": {
                                "name": {"type": "string", "maxLength": 512},
                                "weight": {
                                    "type": "number",
                                    "minimum": -1_000_000,
                                    "maximum": 1_000_000,
                                },
                                "category": {"type": "string", "maxLength": 128},
                            },
                        },
                    },
                },
            },
        },
        "dislikes_clusters": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cluster_id", "members"],
                "properties": {
                    "cluster_id": {"type": "string", "maxLength": 128},
                    "known_distinct_pairs": _known_distinct_pairs_schema(),
                    "members": {
                        "type": "array",
                        "maxItems": 500,
                        "items": {"type": "string", "maxLength": 512},
                    },
                },
            },
        },
    },
}


# Sponsored sends a JSON payload, while the legacy consolidation prompt talks
# about tag-wrapped text. Reusing the legacy prompt made Xing4.0-29B echo the
# output-schema examples and degenerate into repetition loops (verified against
# the real API). Keep a JSON-native prompt for the sponsored contract only;
# BYOK/Ollama continue to use the legacy builder and prompt bytes.
SOUL_CONSOLIDATION_SPONSORED_SYSTEM_PROMPT = (
    "你是用户画像整理器。\n"
    "user 消息是一个 JSON object：\n"
    '{"likes_clusters":[{"cluster_id":"L1","members":[{"name":"搞笑","weight":5.0,'
    '"category":"娱乐"}],"known_distinct_pairs":[]}],\n'
    ' "dislikes_clusters":[{"cluster_id":"D1","members":["标题党"],'
    '"known_distinct_pairs":[]}]}\n'
    "\n"
    "对每个 cluster 输出一个 op：\n"
    "- merge：组内标签表达同一推荐意图、继续并存只会重复占位（如「搞笑」/「娱乐搞笑」）。"
    "members 必须逐字引用输入成员；canonical 选一个具体原名或同等具体的组合名，"
    "不要向上泛化成大类。\n"
    "- keep：会带来不同推荐结果，尤其是父子兴趣（如「篮球」/「NBA」）。\n"
    "补充：\n"
    "- 同名但 category 不同的成员分别 keep。\n"
    "- known_distinct_pairs 里的成员对绝对不能 merge。\n"
    "- dislikes 只合并语义几乎相同的近义词，严禁向上泛化；拿不准就 keep。\n"
    "- 每个 cluster 至少输出一个 op。\n"
    "\n"
    '输出必须是 JSON object，key 只能是 "likes" 和 "dislikes"：\n'
    '{"likes":[{"cluster_id":"L1","op":"merge","members":["搞笑","娱乐搞笑"],'
    '"canonical":"搞笑"}],\n'
    ' "dislikes":[{"cluster_id":"D1","op":"keep","member":"标题党",'
    '"reason":"不合并"}]}\n'
    "不要用 likes_clusters / dislikes_clusters 作为输出 key；"
    "cluster_id 必须来自本次输入。只输出 JSON。"
)


def build_soul_consolidation_system_prompt() -> str:
    """Return the exact canonical system prompt the provider must send.

    The sponsored variant is JSON-native; the legacy tag-wrapped prompt lives
    in ``build_profile_consolidation_prompt`` and remains the BYOK fallback.
    The JSON mode normalization must match
    ``LLMService.complete_structured_task``.
    """

    return ensure_json_mode_contract(SOUL_CONSOLIDATION_SPONSORED_SYSTEM_PROMPT)


def build_sponsored_registry() -> SponsoredContractRegistry:
    registry = SponsoredContractRegistry()
    registry.register(
        SponsoredTaskContract(
            task_id="soul.consolidation",
            contract_version=1,
            caller="soul.consolidation",
            system_prompt=build_soul_consolidation_system_prompt(),
            request_schema=SOUL_CONSOLIDATION_REQUEST_SCHEMA,
            max_input_bytes=CONSOLIDATION_MAX_INPUT_BYTES,
            max_output_tokens=CONSOLIDATION_MAX_OUTPUT_TOKENS,
            model=SPONSORED_MODEL,
            temperature=0.2,
            priority="core",
            daily_requests=CONSOLIDATION_DAILY_REQUESTS,
            daily_tokens=CONSOLIDATION_DAILY_TOKENS,
        )
    )
    return registry


DEFAULT_SPONSORED_REGISTRY = build_sponsored_registry()
