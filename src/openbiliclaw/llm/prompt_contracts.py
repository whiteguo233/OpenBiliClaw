"""Canonical prompt-contract helpers shared by every LLM execution path.

The Sponsored Runtime validates a task's system instruction against a signed
SHA-256 whitelist. That only works if the bytes sent to the provider are
produced by exactly one canonicalization function: this module is that
function's home.

Historically this normalization lived in ``LLMService`` as a private static
method. Keep behavior identical; the move exists so the public policy
generator can hash the same bytes the runtime will later verify.
"""

from __future__ import annotations


def ensure_json_mode_contract(system_instruction: str) -> str:
    """Return the canonical system instruction for a JSON-mode task.

    Some OpenAI-compatible endpoints reject ``response_format=json_object``
    unless a message contains the literal lowercase token. Preserve an
    existing instruction's meaning by normalizing its uppercase ``JSON``
    spelling first; only append the minimal contract token when no such
    spelling exists.

    The function is intentionally pure and byte-stable: callers must not add
    whitespace, prefixes, or suffixes of their own on the way to a provider.
    """

    instruction = system_instruction.strip()
    if "json" in instruction:
        return instruction
    normalized = instruction.replace("JSON", "json")
    if "json" in normalized:
        return normalized
    return f"{normalized}\n\njson" if normalized else "json"
