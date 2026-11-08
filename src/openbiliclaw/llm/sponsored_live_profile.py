"""Explicit signed test profiles for the three account-granted models.

This selects policy metadata for a paired test bundle, never per-request HTTP
parameters or production defaults. BGE-M3 is text-only; missing operations fail
locally rather than spend another model's budget.
"""

from dataclasses import replace

from .sponsored_contracts import SponsoredContractRegistry
from .sponsored_operations import sdk_contracts
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

GRANTED_CHAT_MODELS = ("Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B")
BGE_M3_MODEL = "BAAI/bge-m3"


def granted_test_registry(chat_model: str) -> SponsoredContractRegistry:
    """Bind only granted capabilities into a deterministic signed test policy."""
    if chat_model not in GRANTED_CHAT_MODELS:
        raise ValueError("chat model is not in the reviewed test grant")
    registry = SponsoredContractRegistry()
    for contract in sdk_contracts():
        if contract.operation == "embedding_image":
            continue
        if contract.operation == "vision" and chat_model != "Qwen/Qwen3.5-4B":
            continue
        registry.register(
            replace(
                contract,
                model=BGE_M3_MODEL if contract.operation == "embedding_text" else chat_model,
                max_output_tokens=min(contract.max_output_tokens, 2048),
                daily_requests=30,
                daily_tokens=100_000,
            )
        )
    legacy = DEFAULT_SPONSORED_REGISTRY.get("soul.consolidation")
    assert legacy is not None
    registry.register(replace(legacy, model=chat_model, max_output_tokens=2048))
    return registry
