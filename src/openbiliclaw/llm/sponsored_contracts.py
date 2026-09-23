"""Public contract registry for the SiliconFlow Sponsored Runtime.

The open-source core owns task IDs, prompts, request schemas, and limits. The
closed-source runtime only verifies that a request matches a contract that was
signed into the Sponsored Policy; it never embeds prompt text.

This module is deliberately dependency-light and deterministic: the unsigned
policy produced from a registry must be byte-stable so release CI can diff it,
sign it, and verify that every shipped task still matches the shipped prompts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .prompt_contracts import ensure_json_mode_contract

_TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_ALLOWED_RESPONSE_FORMATS = frozenset({"json_object"})
_ALLOWED_PRIORITIES = frozenset({"core", "optional"})
_REQUIRED_MESSAGE_TOPOLOGY = ("system", "user")


class SponsoredContractError(ValueError):
    """Raised when a sponsored task contract is malformed or duplicated."""


@dataclass(frozen=True)
class SponsoredTaskContract:
    """One allowed Sponsored task.

    ``system_prompt`` must already be the canonical bytes produced by
    :func:`openbiliclaw.llm.prompt_contracts.ensure_json_mode_contract`.
    Storing raw prompt text would make the policy hash diverge from what the
    provider actually sends.
    """

    task_id: str
    contract_version: int
    caller: str
    system_prompt: str
    request_schema: Mapping[str, Any]
    max_input_bytes: int
    max_output_tokens: int
    model: str
    temperature: float = 0.2
    response_format: str = "json_object"
    priority: str = "core"
    daily_requests: int | None = None
    daily_tokens: int | None = None

    def __post_init__(self) -> None:
        if not _TASK_ID_RE.match(self.task_id):
            raise SponsoredContractError(f"invalid task_id: {self.task_id!r}")
        if self.contract_version < 1:
            raise SponsoredContractError("contract_version must be >= 1")
        if not self.caller.strip():
            raise SponsoredContractError("caller must not be empty")
        if self.caller != self.caller.strip().lower():
            raise SponsoredContractError("caller must be lowercase without whitespace")
        if self.system_prompt != ensure_json_mode_contract(self.system_prompt):
            raise SponsoredContractError(
                f"system_prompt for {self.task_id!r} is not canonical; "
                "run ensure_json_mode_contract() before registering"
            )
        if not isinstance(self.request_schema, Mapping):
            raise SponsoredContractError("request_schema must be a JSON Schema object")
        schema_type = self.request_schema.get("type")
        if schema_type == "object":
            if self.request_schema.get("additionalProperties") is not False:
                raise SponsoredContractError(
                    "object request_schema must set additionalProperties=false"
                )
        elif schema_type == "string":
            if self.request_schema.get("minLength", 1) < 1:
                raise SponsoredContractError("string request_schema must require content")
        else:
            raise SponsoredContractError(
                "request_schema must describe a JSON object or string"
            )
        if self.max_input_bytes <= 0:
            raise SponsoredContractError("max_input_bytes must be positive")
        if self.max_output_tokens <= 0:
            raise SponsoredContractError("max_output_tokens must be positive")
        if not self.model.strip():
            raise SponsoredContractError("model must not be empty")
        if not 0.0 <= self.temperature <= 2.0:
            raise SponsoredContractError("temperature must be within [0, 2]")
        if self.response_format not in _ALLOWED_RESPONSE_FORMATS:
            raise SponsoredContractError(f"unsupported response_format: {self.response_format!r}")
        if self.priority not in _ALLOWED_PRIORITIES:
            raise SponsoredContractError(f"unsupported priority: {self.priority!r}")
        if self.daily_requests is not None and self.daily_requests <= 0:
            raise SponsoredContractError("daily_requests must be positive or None")
        if self.daily_tokens is not None and self.daily_tokens <= 0:
            raise SponsoredContractError("daily_tokens must be positive or None")

    @property
    def message_topology(self) -> tuple[str, str]:
        """The only topology Sponsored v1 accepts."""

        return _REQUIRED_MESSAGE_TOPOLOGY

    @property
    def policy_key(self) -> str:
        return f"{self.task_id}.v{self.contract_version}"

    @property
    def system_prompt_sha256(self) -> str:
        digest = hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def to_policy_task(self) -> dict[str, Any]:
        """Return the signed-policy representation of this contract."""

        policy_task: dict[str, Any] = {
            "system_prompt_sha256": self.system_prompt_sha256,
            "message_topology": list(self.message_topology),
            "input_schema": dict(self.request_schema),
            "max_input_bytes": self.max_input_bytes,
            "max_output_tokens": self.max_output_tokens,
            "model": self.model,
            "temperature": self.temperature,
            "response_format": self.response_format,
            "priority": self.priority,
        }
        limits: dict[str, int] = {}
        if self.daily_requests is not None:
            limits["daily_requests"] = self.daily_requests
        if self.daily_tokens is not None:
            limits["daily_tokens"] = self.daily_tokens
        if limits:
            policy_task["limits"] = limits
        return policy_task


class SponsoredContractRegistry:
    """Validated, duplicate-free collection of sponsored task contracts."""

    def __init__(self) -> None:
        self._by_task: dict[str, SponsoredTaskContract] = {}
        self._by_caller: dict[str, SponsoredTaskContract] = {}

    def register(self, contract: SponsoredTaskContract) -> None:
        if contract.task_id in self._by_task:
            raise SponsoredContractError(f"duplicate task_id: {contract.task_id!r}")
        existing = self._by_caller.get(contract.caller)
        if existing is not None:
            raise SponsoredContractError(
                f"caller {contract.caller!r} already maps to {existing.task_id!r}"
            )
        self._by_task[contract.task_id] = contract
        self._by_caller[contract.caller] = contract

    def get(self, task_id: str) -> SponsoredTaskContract | None:
        return self._by_task.get(task_id)

    def resolve_caller(self, caller: str) -> SponsoredTaskContract | None:
        return self._by_caller.get(caller.strip().lower())

    @property
    def tasks(self) -> Mapping[str, SponsoredTaskContract]:
        return MappingProxyType(self._by_task)

    def __iter__(self) -> Iterator[SponsoredTaskContract]:
        return iter(self._by_task.values())

    def __len__(self) -> int:
        return len(self._by_task)


def build_unsigned_policy(
    registry: SponsoredContractRegistry,
    *,
    policy_version: int,
    generated_at: str,
    expires_at: str,
    core_min: str = "",
    core_max: str = "",
) -> dict[str, Any]:
    """Build the deterministic, unsigned policy document.

    Key material and the signing key id are injected by release CI; this
    function must never see or emit them.
    """

    if policy_version < 1:
        raise SponsoredContractError("policy_version must be >= 1")
    if not generated_at:
        raise SponsoredContractError("generated_at must not be empty")
    if not expires_at:
        raise SponsoredContractError("expires_at must not be empty")

    tasks: dict[str, Any] = {}
    for contract in sorted(registry, key=lambda item: item.task_id):
        tasks[contract.policy_key] = contract.to_policy_task()

    return {
        "policy_version": policy_version,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "core": {"min": core_min, "max": core_max},
        "keys": [],
        "tasks": tasks,
    }
