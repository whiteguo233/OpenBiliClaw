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

    async def complete_with_core_memory(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Execute a task with automatically injected core memory context."""
        system_content = "\n\n".join(
            [
                system_instruction.strip(),
                "以下是当前用户的 core memory，请作为理解背景：",
                self.memory.render_core_memory_prompt(),
            ]
        )
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
        return response

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Execute a JSON-mode task with core memory injection."""
        return await self.complete_with_core_memory(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
    ) -> LLMResponse:
        """Generate a Socratic dialogue reply using core memory context."""
        tone_profile = self._build_dialogue_tone_profile()
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            core_memory_text="",
            tone_profile=tone_profile,
            history=[],
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
