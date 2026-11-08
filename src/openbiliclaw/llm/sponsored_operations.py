"""Closed SDK operations covering every project completion and embedding path.

Prompts are application data for these SDK operations (including free-form chat).
They are not an authentication signal. The runtime owns destinations, models and
hard ceilings; callers cannot supply arbitrary HTTP fields or upstream credentials.
"""

from __future__ import annotations

import base64
import binascii
import math
from typing import Any

CHAT_OPERATIONS = ("structured", "text", "tools", "vision")
EMBEDDING_OPERATIONS = ("embedding_text", "embedding_image")
OPERATIONS = (*CHAT_OPERATIONS, *EMBEDDING_OPERATIONS)
EMBEDDING_MODEL = "Qwen/Qwen3-VL-Embedding-8B"
EMBEDDING_DIMENSIONS = 1024
CHAT_MODEL = "THUDM/GLM-4-9B-0414"
VISION_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"
_REASONING = {None, "", "minimal", "low", "medium", "high", "max", "xhigh"}


def image_data_url(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 8 * 1024 * 1024:
        raise ValueError("invalid image size")
    header, sep, data = value.partition(",")
    if not sep or header not in {
        "data:image/jpeg;base64",
        "data:image/png;base64",
        "data:image/webp;base64",
        "data:image/gif;base64",
    }:
        raise ValueError("only inline image data is accepted")
    try:
        if not base64.b64decode(data, validate=True):
            raise ValueError("empty image")
    except binascii.Error as exc:
        raise ValueError("invalid image encoding") from exc


def validate_operation_input(data: Any, operation: str, *, max_tokens: int = 32768) -> None:
    """Independently validate the closed wire shape before starting a child."""
    if not isinstance(data, dict) or operation not in OPERATIONS:
        raise ValueError("unknown SDK operation/input")
    if operation in EMBEDDING_OPERATIONS:
        key = "text" if operation == "embedding_text" else "image"
        if set(data) != {key}:
            raise ValueError("unexpected embedding field")
        if key == "text":
            if not isinstance(data[key], str) or not data[key].strip() or len(data[key]) > 131072:
                raise ValueError("invalid embedding text")
        else:
            image_data_url(data[key])
        return
    if set(data) != {"messages", "temperature", "max_tokens", "reasoning_effort", "caller"}:
        raise ValueError("unexpected completion field")
    if not isinstance(data["caller"], str) or len(data["caller"]) > 128:
        raise ValueError("invalid caller")
    temperature = data["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (float, int))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise ValueError("invalid temperature")
    cap = data["max_tokens"]
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= max_tokens:
        raise ValueError("output exceeds signed ceiling")
    if data["reasoning_effort"] not in _REASONING:
        raise ValueError("unsupported reasoning effort")
    if data["reasoning_effort"] not in (None, "") and cap < 128:
        raise ValueError("thinking needs a bounded output budget of at least 128")
    messages = data["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise ValueError("invalid conversation length")
    images = 0
    for i, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("invalid message fields")
        role = message["role"]
        if role not in {"system", "user", "assistant"} or (role == "system" and i != 0):
            raise ValueError("invalid message role")
        content = message["content"]
        if isinstance(content, str):
            continue
        if role != "user" or not isinstance(content, list):
            raise ValueError("unexpected multimodal content")
        if not 1 <= len(content) <= 65:
            raise ValueError("invalid content parts")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("invalid content part")
            if (
                part.get("type") == "text"
                and set(part) == {"type", "text"}
                and isinstance(part["text"], str)
            ):
                continue
            if (
                operation != "vision"
                or part.get("type") != "image_url"
                or set(part) != {"type", "image_url"}
            ):
                raise ValueError("unsupported content part")
            image = part["image_url"]
            if not isinstance(image, dict) or set(image) != {"url"}:
                raise ValueError("invalid image fields")
            image_data_url(image["url"])
            images += 1
    if messages[-1]["role"] != "user" or images > 32:
        raise ValueError("invalid conversation tail/image count")
    if operation == "vision" and images == 0:
        raise ValueError("vision needs an image")


def sdk_contracts() -> list[Any]:
    """Produce deterministic signed operation contracts without reading secrets."""
    from .prompt_contracts import ensure_json_mode_contract
    from .sponsored_contracts import SponsoredTaskContract

    result = []
    for operation in OPERATIONS:
        embedding = operation in EMBEDDING_OPERATIONS
        schema: dict[str, Any]
        if embedding:
            key = "text" if operation == "embedding_text" else "image"
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": [key],
                "properties": {key: {"type": "string"}},
            }
        else:
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["messages", "temperature", "max_tokens", "reasoning_effort", "caller"],
                "properties": {
                    "messages": {"type": "array", "minItems": 1, "maxItems": 128},
                    "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                    "max_tokens": {"type": "integer", "minimum": 1, "maximum": 32768},
                    "reasoning_effort": {},
                    "caller": {"type": "string", "maxLength": 128},
                },
            }
        result.append(
            SponsoredTaskContract(
                task_id=f"llm.{operation}",
                caller=f"llm.{operation}",
                contract_version=3,
                system_prompt=ensure_json_mode_contract(
                    f"OpenBiliClaw SDK operation: {operation}."
                ),
                request_schema=schema,
                max_input_bytes=(12 if operation in {"vision", "embedding_image"} else 2)
                * 1024
                * 1024,
                max_output_tokens=1 if embedding else 32768,
                model=EMBEDDING_MODEL
                if embedding
                else VISION_MODEL
                if operation == "vision"
                else CHAT_MODEL,
                response_format="json_object" if operation in {"structured", "vision"} else "text",
                operation=operation,
                embedding_dimensions=EMBEDDING_DIMENSIONS if embedding else 0,
            )
        )
    return result
