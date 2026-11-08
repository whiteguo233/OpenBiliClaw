"""Keep accidental tool envelopes out of conversational text, never execute them."""

from __future__ import annotations

import json
import re
from typing import Any

# A resource bound, not a confidence threshold: hold at most 32 Ki characters
# while deciding whether a trailing paragraph is a tool envelope. Larger data
# is passed through unchanged. Ordinary prose is never buffered as a whole.
_MAX_PENDING_CHARS = 32 * 1024
_ENVELOPE_KEYS = ('"tool_call"', '"tool_calls"')
_EXPLICIT_DATA_REQUEST = re.compile(
    r"json|tool[_ -]?calls?|function[_ -]?calls?|示例|代码|原文|原样|原值|"
    r"example|snippet|verbatim|\bexact(?:ly)?\b|schema",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_JSON_FENCE = re.compile(r"(`{3,}|~{3,})(?:json)?[ \t\r]*", re.IGNORECASE)
_PARTIAL_JSON_FENCE = re.compile(r"(`+|~+)(?:j(?:s(?:o(?:n)?)?)?)?[ \t\r]*", re.IGNORECASE)


def preserves_tool_text(request: str) -> bool:
    """Conservatively retain data/code when the raw request refers to it."""
    return bool(_EXPLICIT_DATA_REQUEST.search(request))


def explicit_tool_requests(request: str, tool_names: set[str]) -> set[str]:
    """Find direct, named call instructions, never infer actions from prose.

    This is an evidence requirement, not a dispatcher or an intent classifier.
    Only imperative clauses naming registered tools count. Quoted examples,
    conditionals and negated calls opt out conservatively; ordinary requests
    such as 'read my memory' remain the model's responsibility.
    """
    if re.search(
        r"示例|例子|引用|原文|如果|必要时|按需|不要.{0,12}调用|不必.{0,12}调用|"
        r"无需.{0,12}调用|别.{0,12}调用|\b(?:example|quote|snippet|if|unless)\b|"
        r"\b(?:do not|don't|never)\b.{0,20}\b(?:call|invoke)\b",
        request,
        re.IGNORECASE,
    ):
        return set()
    # Fences, including unfinished ones, cannot create an execution requirement.
    prose = re.sub(r"(`{3,}|~{3,})[^\n]*\n.*?(?:\1|\Z)", "", request, flags=re.DOTALL)
    prose = re.sub(r"\"[^\"]*\"|'[^']*'|“[^”]*”|‘[^’]*’", "", prose, flags=re.DOTALL)
    prose = re.sub(r"`(?![A-Za-z_][A-Za-z_0-9]*`)[^`\n]*`", "", prose)
    required: set[str] = set()
    for line in prose.splitlines():
        if line.startswith(("    ", "\t", ">")):
            continue
        for clause in re.split(r"[。！？；;.,，]", line):
            match = re.match(
                r"\s*(?:(?:请(?:你)?|麻烦(?:你)?|帮我)\s*)?(?:先|再|然后)?\s*调用\s*"
                r"`?([A-Za-z_][A-Za-z_0-9]*)`?(?![A-Za-z_0-9])|"
                r"\s*(?:please\s+)?(?:(?:first|then)\s+)?(?:call|invoke)\s+"
                r"`?([A-Za-z_][A-Za-z_0-9]*)`?(?![A-Za-z_0-9])",
                clause,
                re.IGNORECASE,
            )
            if match:
                name = match[1] or match[2]
                if name in tool_names:
                    required.add(name)
    return required


class ToolTextGuard:
    """Stream prose; quarantine a standalone tool envelope and its trailing text.

    Complete, known-tool envelopes may be bare or in a JSON fence.
    Explicit data requests, other code, quoted/inline examples, unknown JSON
    and malformed text are preserved. ``finish`` must run before treating the response as complete.
    This class does not produce tool calls or authorize any operation.
    """

    def __init__(self, tool_names: set[str], *, preserve: bool = False) -> None:
        self._tool_names = tool_names
        self._disabled = preserve or not tool_names
        self._pending: str | None = None
        self._paragraph_start = True
        self._line = ""
        self._fence = ""
        self.dropped = False

    def _observe(self, text: str) -> None:
        for char in text:
            if char == "\n":
                match = _FENCE.match(self._line)
                if match:
                    marker, suffix = match.groups()
                    if not self._fence:
                        self._fence = marker
                    elif (
                        marker[0] == self._fence[0]
                        and len(marker) >= len(self._fence)
                        and not suffix.strip()
                    ):
                        self._fence = ""
                if not self._line.strip():
                    self._paragraph_start = True
                self._line = ""
            else:
                self._line += char
                if not char.isspace():
                    self._paragraph_start = False

    def feed(self, text: str) -> str:
        """Return the currently safe display text for a content delta."""
        if self._disabled:
            return text
        if self._pending is not None:
            self._pending += text
            return self._release_unrelated()
        for index, char in enumerate(text):
            if (
                char in "{`~"
                and self._paragraph_start
                and not self._fence
                and len(self._line.expandtabs(4)) < 4
            ):
                self._pending = text[index:]
                return text[:index] + self._release_unrelated()
            self._observe(char)
        return text

    def _release_unrelated(self) -> str:
        pending = self._pending
        assert pending is not None
        parts = self._candidate_parts(pending)
        if len(pending) <= _MAX_PENDING_CHARS and parts is not None:
            body, marker = parts
            key_prefix = body[1:].lstrip()
            possible = (not body or body.startswith("{")) and any(
                key.startswith(key_prefix) or key_prefix.startswith(key) for key in _ENVELOPE_KEYS
            )
        else:
            possible = False
        if possible:
            try:
                value, end = json.JSONDecoder().raw_decode(body)
            except (ValueError, RecursionError):
                return ""  # Potentially incomplete; finish() preserves malformed text.
            tail = body[end:]
            closing = tail.strip()
            possible_close = not closing or (
                marker is not None
                and "\n" in tail[: len(tail) - len(tail.lstrip())]
                and set(closing) == {marker[0]}
            )
            if self._is_envelope(value) and (
                marker is None or possible_close or self._closed_fence_prefix(tail, marker)
            ):
                return ""
        self._pending = None
        self._observe(pending)
        return pending

    @staticmethod
    def _candidate_parts(pending: str) -> tuple[str, str | None] | None:
        if pending.startswith("{"):
            return pending, None
        header, separator, body = pending.partition("\n")
        if not separator:
            return ("", None) if _PARTIAL_JSON_FENCE.fullmatch(header) else None
        match = _JSON_FENCE.fullmatch(header)
        return (body.lstrip(), match[1]) if match else None

    @staticmethod
    def _closed_fence_prefix(tail: str, marker: str) -> bool:
        return bool(
            re.match(
                r"\s*\n {0,3}" + marker[0] + "{" + str(len(marker)) + r",}[ \t]*(?:\r?\n|\Z)",
                tail,
            )
        )

    def _is_envelope(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if set(value) == {"tool_call"}:
            calls = [value["tool_call"]]
        elif set(value) == {"tool_calls"} and isinstance(value["tool_calls"], list):
            calls = value["tool_calls"]
        else:
            return False
        return bool(calls) and all(self._is_call(call) for call in calls)

    def _is_call(self, call: Any) -> bool:
        if not isinstance(call, dict):
            return False
        if "function" in call:
            if set(call) - {"id", "type", "function"} or call.get("type") != "function":
                return False
            call = call["function"]
        if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
            return False
        if not isinstance(call["name"], str) or call["name"] not in self._tool_names:
            return False
        arguments = call["arguments"]
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, RecursionError):
                return False
        return isinstance(arguments, dict)

    def finish(self) -> str:
        """Suppress a known protocol prefix and its unsupported trailing claims."""
        pending, self._pending = self._pending, None
        if pending is None:
            return ""
        parts = self._candidate_parts(pending)
        if parts is None:
            return pending
        body, marker = parts
        try:
            value, end = json.JSONDecoder().raw_decode(body)
        except (ValueError, RecursionError):
            return pending
        tail = body[end:]
        # Claims after an unexecuted tool envelope cannot attest its success.
        # Keep the whole suffix quarantined; an explicit data request opts out.
        complete = marker is None or self._closed_fence_prefix(tail, marker)
        if complete and self._is_envelope(value):
            self.dropped = True
            return ""
        return pending
