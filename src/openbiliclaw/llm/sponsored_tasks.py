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

from .json_utils import DEFAULT_STRUCTURED_MAX_TOKENS
from .prompt_contracts import ensure_json_mode_contract
from .prompts import build_profile_consolidation_prompt
from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

# The sponsor account can reach many SiliconFlow models. Xing4.0-29B was
# measured against the legacy consolidation prompt and degenerated into
# repetition loops even at 8 clusters, so the pilot uses DeepSeek-V3.2, which
# followed the unchanged legacy prompt at 17 clusters in one real call
# (valid JSON, 22 like ops + 2 dislike ops, 56s).
SPONSORED_MODEL = "deepseek-ai/DeepSeek-V3.2"
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


# Sponsored v1 for this pilot sends the *legacy* tag-wrapped user message
# unchanged. The runtime cannot re-derive that text without embedding prompt
# logic in Rust, so the contract validates it as a size-bounded string while
# still pinning the canonical system-prompt hash and task whitelist.
SOUL_CONSOLIDATION_REQUEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenBiliClaw sponsored request: soul.consolidation.v1 (legacy text)",
    "type": "string",
    "minLength": 1,
}


# The sponsored contract intentionally reuses the legacy consolidation system
# prompt and the legacy tag-wrapped user message unchanged. The runtime pins
# the system-prompt hash and size but does not rewrite the text; BYOK/Ollama
# and Sponsored therefore send byte-identical prompts for this task.
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
