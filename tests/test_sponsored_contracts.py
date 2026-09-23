"""Tests for the public Sponsored Runtime contract registry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from openbiliclaw.llm.json_utils import DEFAULT_STRUCTURED_MAX_TOKENS
from openbiliclaw.llm.prompt_contracts import ensure_json_mode_contract
from openbiliclaw.llm.sponsored_contracts import (
    SponsoredContractError,
    SponsoredContractRegistry,
    SponsoredTaskContract,
    build_unsigned_policy,
)
from openbiliclaw.llm.sponsored_tasks import (
    CONSOLIDATION_MAX_OUTPUT_TOKENS,
    DEFAULT_SPONSORED_REGISTRY,
    SOUL_CONSOLIDATION_REQUEST_SCHEMA,
    build_soul_consolidation_system_prompt,
)

# Golden contract hash. Updating it is the deliberate "you changed a shipped
# prompt, now re-sign the policy" gate described in the design doc.
SOUL_CONSOLIDATION_SYSTEM_PROMPT_SHA256 = (
    "sha256:4f1ca729a26ce89fa3e41e4f0160b6e2c98aa109cb7bb8c5e42bd1622d0ca4f5"
)


def _contract(**overrides: Any) -> SponsoredTaskContract:
    values: dict[str, Any] = {
        "task_id": "soul.consolidation",
        "contract_version": 1,
        "caller": "soul.consolidation",
        "system_prompt": "Return json.",
        "request_schema": deepcopy(SOUL_CONSOLIDATION_REQUEST_SCHEMA),
        "max_input_bytes": 4096,
        "max_output_tokens": 512,
        "model": "test/model",
    }
    values.update(overrides)
    return SponsoredTaskContract(**values)


def test_pilot_contract_prompt_matches_golden_hash() -> None:
    contract = DEFAULT_SPONSORED_REGISTRY.get("soul.consolidation")
    assert contract is not None
    assert build_soul_consolidation_system_prompt() == contract.system_prompt
    assert contract.system_prompt_sha256 == SOUL_CONSOLIDATION_SYSTEM_PROMPT_SHA256
    # Sponsored intentionally reuses the legacy prompt bytes; the runtime only
    # pins the hash. Removing <output_schema> would change the old flow.
    assert "known_distinct_pairs" in contract.system_prompt
    assert "<output_schema>" in contract.system_prompt


def test_pilot_contract_resolves_from_caller() -> None:
    contract = DEFAULT_SPONSORED_REGISTRY.resolve_caller("soul.consolidation")
    assert contract is not None
    assert contract.task_id == "soul.consolidation"
    assert contract.policy_key == "soul.consolidation.v1"
    assert DEFAULT_SPONSORED_REGISTRY.resolve_caller("general.chat") is None


def test_pilot_schema_accepts_legacy_text_payload() -> None:
    assert SOUL_CONSOLIDATION_REQUEST_SCHEMA["type"] == "string"
    assert SOUL_CONSOLIDATION_REQUEST_SCHEMA["minLength"] == 1


def test_pilot_output_budget_matches_legacy_flow() -> None:
    assert CONSOLIDATION_MAX_OUTPUT_TOKENS == DEFAULT_STRUCTURED_MAX_TOKENS


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"task_id": "BadTask"}, "invalid task_id"),
        ({"contract_version": 0}, "contract_version"),
        ({"caller": "Soul.Consolidation"}, "lowercase"),
        ({"system_prompt": "  Return JSON.  "}, "not canonical"),
        ({"request_schema": {"type": "array"}}, "object or string"),
        ({"request_schema": {"type": "string", "minLength": 0}}, "require content"),
        (
            {
                "request_schema": {
                    "type": "object",
                    "additionalProperties": True,
                }
            },
            "additionalProperties=false",
        ),
        ({"max_input_bytes": 0}, "max_input_bytes"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"model": "  "}, "model"),
        ({"temperature": 3.0}, "temperature"),
        ({"response_format": "text"}, "response_format"),
        ({"priority": "optionalish"}, "priority"),
        ({"daily_requests": 0}, "daily_requests"),
    ],
)
def test_contract_rejects_invalid_values(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(SponsoredContractError, match=message):
        _contract(**overrides)


def test_registry_rejects_duplicate_task_and_caller() -> None:
    registry = SponsoredContractRegistry()
    registry.register(_contract())
    with pytest.raises(SponsoredContractError, match="duplicate task_id"):
        registry.register(_contract())
    with pytest.raises(SponsoredContractError, match="already maps"):
        registry.register(_contract(task_id="soul.other"))


def test_canonicalization_contract_is_explicit() -> None:
    raw = "Return JSON."
    canonical = ensure_json_mode_contract(raw)
    with pytest.raises(SponsoredContractError, match="not canonical"):
        _contract(system_prompt=raw)
    assert _contract(system_prompt=canonical).system_prompt == canonical


def test_unsigned_policy_is_deterministic_and_complete() -> None:
    policy = build_unsigned_policy(
        DEFAULT_SPONSORED_REGISTRY,
        policy_version=7,
        generated_at="2026-09-22T00:00:00Z",
        expires_at="2026-12-21T00:00:00Z",
        core_min="0.4.0",
        core_max="0.4.*",
    )
    assert policy["policy_version"] == 7
    assert policy["keys"] == []
    assert policy["core"] == {"min": "0.4.0", "max": "0.4.*"}

    task = policy["tasks"]["soul.consolidation.v1"]
    assert task["system_prompt_sha256"] == SOUL_CONSOLIDATION_SYSTEM_PROMPT_SHA256
    assert task["message_topology"] == ["system", "user"]
    assert task["model"] == "deepseek-ai/DeepSeek-V3.2"
    assert task["response_format"] == "json_object"
    assert task["limits"] == {"daily_requests": 200, "daily_tokens": 500_000}
    assert task["input_schema"]["type"] == "string"
    assert task["input_schema"]["minLength"] == 1

    again = build_unsigned_policy(
        DEFAULT_SPONSORED_REGISTRY,
        policy_version=7,
        generated_at="2026-09-22T00:00:00Z",
        expires_at="2026-12-21T00:00:00Z",
        core_min="0.4.0",
        core_max="0.4.*",
    )
    assert again == policy
