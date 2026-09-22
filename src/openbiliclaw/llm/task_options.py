"""Helpers for optional structured LLM task parameters."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def call_accepts_keyword(fn: Any, name: str) -> bool:
    """Return whether a callable accepts a keyword argument."""

    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters


def without_core_memory_kwargs(fn: Any) -> dict[str, Any]:
    """Return kwargs that disable extra core-memory injection when supported."""

    if call_accepts_keyword(fn, "inject_core_memory"):
        return {"inject_core_memory": False}
    return {}


async def execute_sponsored_or_structured(
    llm: Any,
    *,
    caller: str,
    payload: Mapping[str, Any],
    fallback_system_instruction: str,
    fallback_user_input: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    inject_core_memory: bool = False,
) -> Any:
    """Run a sponsored-eligible structured task when the service supports it.

    ``LLMService`` owns the actual decision (enabled? runtime available?
    fallback allowed?) and keeps the legacy prompt path for BYOK / Ollama.
    Test doubles that only implement ``complete_structured_task`` keep using
    the legacy path, so this helper never requires the Sponsored Runtime to
    exist.
    """

    execute_sponsored = getattr(llm, "execute_sponsored_task", None)
    if callable(execute_sponsored):
        return await execute_sponsored(
            caller=caller,
            payload=payload,
            fallback_system_instruction=fallback_system_instruction,
            fallback_user_input=fallback_user_input,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            inject_core_memory=inject_core_memory,
        )

    legacy_kwargs: dict[str, Any] = {}
    if reasoning_effort is not None and call_accepts_keyword(
        llm.complete_structured_task, "reasoning_effort"
    ):
        legacy_kwargs["reasoning_effort"] = reasoning_effort
    if not inject_core_memory:
        legacy_kwargs.update(without_core_memory_kwargs(llm.complete_structured_task))
    return await llm.complete_structured_task(
        system_instruction=fallback_system_instruction,
        user_input=fallback_user_input,
        temperature=temperature,
        max_tokens=max_tokens,
        caller=caller,
        **legacy_kwargs,
    )
