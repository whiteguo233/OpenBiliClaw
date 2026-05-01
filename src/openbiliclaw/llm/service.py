"""Shared service facade for prompt assembly and LLM execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.soul.profile import SoulProfile, preference_layer_from_dict
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile

from .base import LLMProviderError
from .prompts import build_socratic_dialogue_prompt

if TYPE_CHECKING:
    from openbiliclaw.memory.manager import MemoryManager

    from .base import LLMResponse


class SupportsComplete(Protocol):
    """Protocol for providers or registries with a complete method."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse: ...


class LLMServiceError(Exception):
    """Base exception for service-layer LLM errors."""


class LLMResponseContentError(LLMServiceError):
    """Raised when an LLM call returns empty content."""


class LLMProviderExecutionError(LLMServiceError):
    """Raised when the underlying provider or registry call fails."""


@dataclass
class LLMService:
    """Facade that assembles prompts and delegates calls to the registry."""

    registry: SupportsComplete
    memory: MemoryManager
    # v0.3.26+: optional usage ledger sink. When supplied, every
    # successful LLM response is written to the ``llm_usage`` table so
    # ``openbiliclaw cost`` can report daily spend. Default None
    # preserves prior behaviour for tests / standalone callers that
    # don't care about cost tracking.
    usage_recorder: object | None = None

    async def complete_with_core_memory(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        caller: str = "",
    ) -> LLMResponse:
        """Execute a task with automatically injected core memory context.

        ``caller`` is an optional free-form tag (e.g. ``"soul.preference"``,
        ``"discovery.eval"``) attached to the usage row so the ``cost``
        report can break spend down by module.
        """
        core_memory_block = ""
        if self.memory is not None:
            try:
                core_memory_block = self.memory.render_core_memory_prompt()
            except Exception:
                pass
        parts = [system_instruction.strip()]
        if core_memory_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(core_memory_block)
        system_content = "\n\n".join(parts)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        try:
            response = await self.registry.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        # Best-effort usage ledger write. The recorder swallows its own
        # exceptions so a billing-table hiccup never affects the LLM
        # response that just succeeded.
        recorder = self.usage_recorder
        if recorder is not None:
            record_fn = getattr(recorder, "record", None)
            if callable(record_fn):
                try:
                    record_fn(response, caller=caller)
                except Exception:
                    pass
        return response

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        """Execute a JSON-mode task with core memory injection."""
        return await self.complete_with_core_memory(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            caller=caller,
        )

    async def complete_with_tools(
        self,
        *,
        system_instruction: str,
        user_input: str,
        tools: list[dict[str, object]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Execute a completion that may include tool/function calls.

        The LLM is given a set of tool definitions.  If it decides to call
        a tool, the response will have ``tool_calls`` populated.  Otherwise
        ``content`` will contain the text reply.

        This method uses JSON mode under the hood: the tools are serialised
        into the system prompt and the model is asked to return a JSON
        wrapper with either ``reply`` or ``tool_call`` keys.
        """
        tools_desc = "\n".join(f"- {t['name']}: {t.get('description', '')}" for t in tools)
        tool_names = [t["name"] for t in tools]
        augmented_system = (
            system_instruction + "\n\n"
            "<available_tools>\n" + tools_desc + "\n"
            "</available_tools>\n\n"
            "<tool_call_format>\n"
            "如果你需要调用工具，请返回如下 JSON（不要附带任何其他文字）：\n"
            '{"tool_call": {"name": "工具名", "arguments": {参数}}}\n'
            "如果不需要调用工具，正常回复用户即可（不要输出 JSON）。\n"
            "</tool_call_format>"
        )
        response = await self.complete_with_core_memory(
            system_instruction=augmented_system,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

        # Try to parse tool calls from the response
        import json

        content = (response.content or "").strip()
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "tool_call" in parsed:
                    call = parsed["tool_call"]
                    if isinstance(call, dict) and call.get("name") in tool_names:
                        response.tool_calls = [call]
                        response.content = ""
            except (json.JSONDecodeError, TypeError):
                pass  # Not valid JSON — treat as normal text reply

        return response

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
    ) -> LLMResponse:
        """Generate a Socratic dialogue reply using core memory context."""
        tone_profile = self._build_dialogue_tone_profile()
        preference_raw = self.memory.get_layer("preference").data
        source_mix = preference_layer_from_dict(preference_raw).source_platform_mix
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            core_memory_text="",
            tone_profile=tone_profile,
            history=[],
            source_platform_mix=source_mix or None,
        )
        return await self.complete_with_core_memory(
            system_instruction=prompt_messages[0]["content"],
            user_input=user_message,
            history=history,
        )

    def _build_dialogue_tone_profile(self) -> ToneProfile:
        """Infer tone profile for dialogue from persisted memory."""
        soul_raw = self.memory.get_layer("soul").data
        preference_raw = self.memory.get_layer("preference").data
        profile = None
        if soul_raw:
            profile = SoulProfile.from_dict(soul_raw)
            profile.preferences = preference_layer_from_dict(preference_raw)
        return build_tone_profile(
            profile=profile,
            preference_summary=self.memory.get_core_memory().get("preference_summary", {}),
            recent_feedback=[],
        )
