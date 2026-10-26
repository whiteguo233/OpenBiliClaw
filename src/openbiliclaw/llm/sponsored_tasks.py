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

from .consolidation_prompt import CONSOLIDATION_SYSTEM_PROMPT
from .json_utils import DEFAULT_STRUCTURED_MAX_TOKENS
from .prompt_contracts import ensure_json_mode_contract
from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

# Free-only sponsorship constraint: the policy must use a model that costs
# nothing on SiliconFlow. Measured against the unchanged legacy consolidation
# prompt: DeepSeek-R1-0528-Qwen3-8B is the strongest free candidate when the
# backend is available, but it currently returns 400 "Model does not exist"
# for real consolidation payloads; Qwen/Qwen3-8B, GLM-Z1-9B-0414 and
# Qwen2.5-7B-Instruct misattribute cluster members; Qwen3.5-4B returns 503;
# Xing4.0-29B degenerates into repetition. GLM-4-9B-0414 is the only free model
# that stays structurally usable, so it is the pilot default until the free
# backend picture changes.
SPONSORED_MODEL = "THUDM/GLM-4-9B-0414"
SPONSORED_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"

CONSOLIDATION_MAX_INPUT_BYTES = 256 * 1024
# Keep the sponsored output budget identical to the legacy consolidation flow
# (`DEFAULT_STRUCTURED_MAX_TOKENS`). If real runs show output truncation, raise
# this shared value/policy; do not compensate with a different prompt.
CONSOLIDATION_MAX_OUTPUT_TOKENS = DEFAULT_STRUCTURED_MAX_TOKENS
# Per-install fairness budget derived from *normal user usage*, per the
# product decision: total account capacity must never be used to tighten a
# normal user's quota. Increase accounts/budget or degrade gracefully instead.
# These values are placeholders until real per-task usage is measured; abuse is
# bounded by the account balance + account-level rate limits, not by making a
# legitimate user's budget tiny.
CONSOLIDATION_DAILY_REQUESTS = 200
CONSOLIDATION_DAILY_TOKENS = 500_000


# Closed field sets: the runtime validates the same fixed structure independently.
_PAIR = {
    "type": "array",
    "minItems": 2,
    "maxItems": 2,
    "items": {"type": "string", "minLength": 1, "maxLength": 512},
}
_MEMBER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "weight", "category"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 256},
        "weight": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
        "category": {"type": "string", "maxLength": 128},
    },
}


def _cluster_schema(member: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["cluster_id", "known_distinct_pairs", "members"],
        "properties": {
            "cluster_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "known_distinct_pairs": {"type": "array", "maxItems": 128, "items": _PAIR},
            "members": {"type": "array", "minItems": 2, "maxItems": 128, "items": member},
        },
    }


SOUL_CONSOLIDATION_REQUEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenBiliClaw fixed consolidation v2",
    "type": "object",
    "additionalProperties": False,
    "required": ["likes_clusters", "dislikes_clusters"],
    "properties": {
        "likes_clusters": {"type": "array", "maxItems": 32, "items": _cluster_schema(_MEMBER)},
        "dislikes_clusters": {
            "type": "array",
            "maxItems": 32,
            "items": _cluster_schema({"type": "string", "minLength": 1, "maxLength": 256}),
        },
    },
}


def validate_task_input(value: Any, schema: dict[str, Any] | None = None) -> None:
    """Check the fixed schema before IPC; the private runtime repeats this check."""
    import math

    if schema is None:
        validate_task_input(value, SOUL_CONSOLIDATION_REQUEST_SCHEMA)
        if len(value["likes_clusters"]) + len(value["dislikes_clusters"]) > 32:
            raise ValueError("at most 32 clusters are allowed")
        return
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError("unknown or missing task input fields")
        for key, child in schema["properties"].items():
            validate_task_input(value[key], child)
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get(
            "maxItems", 32
        ):
            raise ValueError("invalid task array length")
        for item in value:
            validate_task_input(item, schema["items"])
    elif kind == "string":
        if (
            not isinstance(value, str)
            or not schema.get("minLength", 0) <= len(value) <= schema["maxLength"]
        ):
            raise ValueError("invalid task text length")
        if schema.get("minLength") and not value.strip():
            raise ValueError("empty task field")
    elif kind == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not schema["minimum"] <= value <= schema["maximum"]
        ):
            raise ValueError("invalid task number")


# The sponsored contract intentionally reuses the legacy consolidation system
# prompt and legacy tag-wrapped user message unchanged. The runtime uses
# the signed system text and fixed tag/JSON renderer;
# shared golden vectors prove byte-identical BYOK and Sponsored prompts.
def build_soul_consolidation_system_prompt() -> str:
    """Return the exact canonical system prompt the provider must send.

    The static text is shared with the business builder through the pure
    ``consolidation_prompt`` module. JSON mode normalization must match
    ``LLMService.complete_structured_task``.
    """

    return ensure_json_mode_contract(CONSOLIDATION_SYSTEM_PROMPT)


def build_sponsored_registry() -> SponsoredContractRegistry:
    registry = SponsoredContractRegistry()
    registry.register(
        SponsoredTaskContract(
            task_id="soul.consolidation",
            contract_version=2,
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
