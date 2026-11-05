"""Sponsored execution for the evaluation harness, including read-only code tools."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.sponsored_router import sponsored_enabled, try_sponsored_completion


def agent_options(**kwargs: Any) -> Any:
    """Avoid requiring the Claude SDK for a sponsor-only evaluation install."""
    if sponsored_enabled():
        return SimpleNamespace(**kwargs)
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(**kwargs)


def _read_tool(name: str, arguments: dict[str, Any], root: Path) -> str:
    from .discovery_optimizer import DISCOVERY_MODIFIABLE_FILES
    from .optimizer import MODIFIABLE_FILES

    allowed = sorted(set(MODIFIABLE_FILES) | set(DISCOVERY_MODIFIABLE_FILES))
    if name == "Glob":
        if set(arguments) != {"pattern"}:
            raise ValueError("invalid Glob arguments")
        pattern = str(arguments.get("pattern", "*"))[:256]
        return json.dumps([p for p in allowed if fnmatch.fnmatch(p, pattern)], ensure_ascii=False)
    if name == "Grep":
        if set(arguments) != {"pattern"}:
            raise ValueError("invalid Grep arguments")
        pattern = str(arguments.get("pattern", ""))[:256]
        if not pattern:
            raise ValueError("Grep needs a literal pattern")
        result = []
        for name_ in allowed:
            path = (root / name_).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            for index, line in enumerate(path.read_text()[:1_000_000].splitlines(), 1):
                if pattern in line:
                    result.append(f"{name_}:{index}:{line[:400]}")
                if len(result) >= 80:
                    return "\n".join(result)
        return "\n".join(result)
    if name != "Read" or set(arguments) - {"file_path", "offset", "limit"}:
        raise ValueError("unsupported evaluation tool/fields")
    raw = Path(str(arguments.get("file_path", "")))
    path = (root / raw).resolve()
    if not path.is_relative_to(root) or path.relative_to(root).as_posix() not in allowed:
        raise ValueError("file is outside the evaluation source allowlist")
    offset = max(0, int(arguments.get("offset", 1)) - 1)
    limit = max(1, min(400, int(arguments.get("limit", 200))))
    return "\n".join(path.read_text().splitlines()[offset : offset + limit])[:48000]


async def sponsored_agent_text(prompt: str, options: Any) -> tuple[str, str] | None:
    """Keep model calls in the SDK; execute only the harness's existing read tools locally."""
    if not sponsored_enabled():
        return None
    tools = getattr(options, "allowed_tools", None) or []
    if set(tools) - {"Read", "Grep", "Glob"}:
        raise ValueError("unsupported sponsored evaluation tool")
    system = str(getattr(options, "system_prompt", "") or "")
    if tools:
        system += (
            '\n\nRead-only source tools: Read({"file_path":relative path,"offset":1,"limit":200}), '
            'Grep({"pattern":literal text}), Glob({"pattern":glob}). '
            "Only the evaluation source allowlist is readable. "
            'To use one return {"tool_call":{"name":"Read|Grep|Glob","arguments":{...}}}. '
            "After results, return the task final answer. Never request writes or shell commands."
        )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    all_text = []
    root = Path(getattr(options, "cwd", ".") or ".").resolve()
    for _ in range(max(1, min(10, int(getattr(options, "max_turns", 1) or 1)))):
        response = await try_sponsored_completion(
            messages,
            caller="eval.agent.tools" if tools else "eval.agent",
            temperature=0.7,
            max_tokens=16384,
            json_mode=bool(getattr(options, "output_format", None)) and not tools,
        )
        if response is None:
            return None
        text = response.content
        all_text.append(text)
        try:
            parsed = parse_llm_json_tolerant(text)
        except (ValueError, TypeError):
            parsed = None
        call = parsed.get("tool_call") if isinstance(parsed, dict) else None
        if not tools or not isinstance(call, dict):
            return "\n".join(all_text), text
        name, arguments = call.get("name"), call.get("arguments")
        if not isinstance(name, str) or name not in tools or not isinstance(arguments, dict):
            raise ValueError("invalid evaluation tool call")
        try:
            result = _read_tool(name, arguments, root)
        except (ValueError, OSError, TypeError) as exc:
            result = f"tool rejected: {type(exc).__name__}"
        messages.extend(
            [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"[Tool result] {result}"},
            ]
        )
    raise ValueError("evaluation agent exhausted its bounded tool turns")
