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
from .prompts import build_profile_consolidation_prompt
from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

SPONSORED_MODEL = "XingChenAGI/Xing4.0-29B"
SPONSORED_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"

CONSOLIDATION_MAX_INPUT_BYTES = 256 * 1024
CONSOLIDATION_MAX_OUTPUT_TOKENS = 4096
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


def build_soul_consolidation_system_prompt() -> str:
    """Return the exact canonical system prompt the provider must send.

    ``build_profile_consolidation_prompt`` owns the static system text; the
    empty payload arguments only exist to reach that static message. The JSON
    mode normalization must match ``LLMService.complete_structured_task``.
    """

    messages = build_profile_consolidation_prompt(likes_clusters=[], dislikes_clusters=[])
    return ensure_json_mode_contract(messages[0]["content"])


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
