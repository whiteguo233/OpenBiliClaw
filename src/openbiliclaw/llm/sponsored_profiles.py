"""Reviewed free-model capabilities shared by settings, policy and routing.

Model selection resolves to a signed task ID; it never grants arbitrary model,
endpoint or credential overrides. Legacy pilot contracts remain test-compatible.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .sponsored_contracts import SponsoredContractRegistry
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

DEFAULT_CHAT_MODEL = "Qwen/Qwen3-8B"
ALTERNATE_CHAT_MODEL = "Qwen/Qwen3.5-4B"
CHAT_MODELS = (DEFAULT_CHAT_MODEL, ALTERNATE_CHAT_MODEL)
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIMENSIONS = 1024
ALTERNATE_TASK_SUFFIX = ".qwen35_4b"
# SiliconFlow model-centre contextLen, verified 2026-10-27. These are hosted
# windows, not Qwen3's 32K native default. Recheck when changing model/provider.
# https://www.siliconflow.cn/models?q=Qwen%2FQwen3-8B
CONTEXT_WINDOWS = {DEFAULT_CHAT_MODEL: 131072, ALTERNATE_CHAT_MODEL: 262144}


def approved_registry() -> SponsoredContractRegistry:
    """Bind both selectable text models and text-only BGE into one signed policy."""
    registry = SponsoredContractRegistry()
    for original in DEFAULT_SPONSORED_REGISTRY:
        if original.operation == "embedding_image":
            continue
        model = (
            EMBEDDING_MODEL
            if original.operation == "embedding_text"
            else ALTERNATE_CHAT_MODEL
            if original.operation == "vision"
            else DEFAULT_CHAT_MODEL
        )
        # These project prompts include root JSON arrays (e.g. 30-item copy).
        # SiliconFlow's json_object constraint turns those into one object.
        # Keep JSON instructions and Core validation, with unconstrained wire output.
        contract = replace(
            original,
            model=model,
            response_format="text"
            if original.operation in {"structured", "vision"}
            else original.response_format,
        )
        registry.register(contract)
        if contract.operation in {"structured", "text", "tools", "consolidation"}:
            registry.register(
                replace(
                    contract,
                    task_id=contract.task_id + ALTERNATE_TASK_SUFFIX,
                    caller=contract.caller + ALTERNATE_TASK_SUFFIX,
                    model=ALTERNATE_CHAT_MODEL,
                )
            )
    return registry


def context_input_bound(payload: Any, *, vision: bool = False) -> int:
    """Conservative text token bound, not a claim to have Qwen's tokenizer.

    UTF-8 byte count bounds byte-level text tokens, with 1024 reserved for framing.
    Image data is not text; reserve a conservative 16K per optional image instead.
    Requests are never silently clipped. Provider-side limits remain authoritative.
    """
    if not vision:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 1024
    images = 0

    def without_images(value: Any) -> Any:
        nonlocal images
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                images += 1
                return {"type": "image_url"}
            return {key: without_images(item) for key, item in value.items()}
        if isinstance(value, list):
            return [without_images(item) for item in value]
        return value

    text = without_images(payload)
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) + 1024 + images * 16384
