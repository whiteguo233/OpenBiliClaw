"""Helpers for optional structured LLM task parameters."""

from __future__ import annotations

import inspect
from typing import Any


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
