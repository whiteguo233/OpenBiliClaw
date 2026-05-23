"""Socratic dialogue module.

Handles deep, probing conversations with the user to better understand them.
The dialogue style is inspired by the Socratic method:
- Ask "why" to uncover motivations
- Propose hypotheses and test them
- Confirm understanding before adjusting
- Adapt dynamically based on responses
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.llm.service import LLMService, ModuleOverride, SupportsComplete
    from openbiliclaw.soul.engine import SoulEngine

logger = logging.getLogger(__name__)


@dataclass
class DialogueTurn:
    """A single turn in a dialogue."""

    role: str  # "user" | "agent"
    content: str
    timestamp: str = ""
    extracted_insights: list[str] | None = None


class SocraticDialogue:
    """Manages Socratic-style dialogue with the user.

    The dialogue module doesn't just record what the user says — it actively
    probes deeper to understand motivations, validate hypotheses, and refine
    the agent's understanding of who the user really is.

    Dialogue strategies:
    1. 追问 Why — Don't stop at preferences, dig into motivations
    2. 提出假设 — Actively hypothesize based on current understanding
    3. 确认验证 — Use recommendations to test hypotheses
    4. 动态调整 — Refine the soul profile based on dialogue
    """

    def __init__(
        self,
        llm: SupportsComplete | None,
        soul_engine: SoulEngine,
        llm_service: LLMService | None = None,
        session: str = "cli",
        tools: list[dict[str, Any]] | None = None,
        tool_dispatcher: Any | None = None,
        module_overrides: Mapping[str, ModuleOverride] | None = None,
    ) -> None:
        self._llm = llm
        self._soul_engine = soul_engine
        self._llm_service = llm_service
        self._session = session
        self._history: list[DialogueTurn] = []
        self._tools = tools or []
        self._tool_dispatcher = tool_dispatcher
        self._module_overrides = dict(module_overrides) if module_overrides is not None else None

    async def respond(self, user_message: str) -> str:
        """Generate a Socratic response to a user message.

        The response should:
        - Acknowledge what the user said
        - Probe deeper when appropriate ("为什么？")
        - Propose hypotheses ("我猜你可能...")
        - Confirm understanding ("所以你的意思是...")
        - Feel natural and warm, like a friend talking

        Args:
            user_message: The user's message.

        Returns:
            Agent's response.
        """
        from openbiliclaw.llm.service import LLMServiceError

        self._history.append(DialogueTurn(role="user", content=user_message))

        try:
            service = self._llm_service or self._build_service()

            # If tools are configured, try tool-calling path first
            if self._tools and self._tool_dispatcher:
                reply = await self._respond_with_tools(service, user_message)
            else:
                response = await service.complete_socratic_dialogue(
                    user_message=user_message,
                    history=self._history_to_messages(),
                    caller="soul.dialogue",
                )
                reply = response.content
        except (LLMServiceError, RuntimeError):
            logger.exception("Failed to generate Socratic dialogue response.")
            reply = "我刚刚思路断了一下，你可以换个说法再告诉我一次吗？"

        self._history.append(DialogueTurn(role="agent", content=reply))
        learn_from_dialogue = getattr(self._soul_engine, "learn_from_dialogue", None)
        if callable(learn_from_dialogue):
            try:
                await learn_from_dialogue(
                    user_message=user_message,
                    assistant_reply=reply,
                    session=self._session,
                )
            except Exception:
                logger.exception("Failed to learn from dialogue turn.")
        return reply

    async def _respond_with_tools(self, service: Any, user_message: str) -> str:
        """Attempt a tool-calling response, falling back to normal dialogue.

        The flow:
        1. Ask LLM with tool definitions — it may return a tool_call or text.
        2. If tool_call: execute via dispatcher, feed result back, get final reply.
        3. If text: return as-is.
        """
        from openbiliclaw.llm.prompts import build_socratic_dialogue_prompt

        core_memory = ""
        build_block = getattr(service, "_build_core_memory_block", None)
        if callable(build_block):
            core_memory = build_block()
        tone_profile = None
        build_tone = getattr(service, "_build_dialogue_tone_profile", None)
        if callable(build_tone):
            tone_profile = build_tone()
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            history=self._history_to_messages(),
            core_memory_text=core_memory,
            tone_profile=tone_profile,
        )
        system = prompt_messages[0]["content"] if prompt_messages else ""

        response = await service.complete_with_tools(
            system_instruction=system,
            user_input=user_message,
            tools=self._tools,
            history=self._history_to_messages(),
            caller="soul.dialogue.tools",
        )

        # If the LLM returned a tool call, execute and continue
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            logger.info("Dialogue tool call: %s", tool_call.get("name"))
            if self._tool_dispatcher is None:
                return str(response.content)
            tool_result = self._tool_dispatcher.dispatch(tool_call)

            # Feed tool result back to get a natural reply
            followup = await service.complete_socratic_dialogue(
                user_message=f"[工具执行结果] {tool_result}",
                history=self._history_to_messages()
                + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": f"（调用了工具 {tool_call.get('name')}）"},
                ],
                caller="soul.dialogue.tool_followup",
            )
            return str(followup.content)

        return str(response.content)

    async def extract_insights(self, turns: list[DialogueTurn]) -> list[dict[str, Any]]:
        """Extract insights about the user from dialogue turns.

        Args:
            turns: Recent dialogue turns to analyze.

        Returns:
            List of extracted insight dicts.
        """
        # TODO: Use LLM to identify preference signals, motivations,
        #       personality traits from the conversation
        return []

    @property
    def history(self) -> list[DialogueTurn]:
        """The dialogue history."""
        return self._history.copy()

    def clear_history(self) -> None:
        """Clear the dialogue history."""
        self._history.clear()

    def _history_to_messages(self) -> list[dict[str, str]]:
        """Convert prior dialogue turns to chat messages for the LLM."""
        return [
            {
                "role": "assistant" if turn.role == "agent" else turn.role,
                "content": turn.content,
            }
            for turn in self._history[:-1]
        ]

    def _build_service(self) -> LLMService:
        """Create the shared LLM service when one is not injected."""
        from openbiliclaw.llm.service import LLMService

        memory = getattr(self._soul_engine, "_memory", None)
        if self._llm is None or memory is None:
            raise RuntimeError("Dialogue service is not configured.")
        module_overrides = self._module_overrides
        if module_overrides is None:
            module_overrides = getattr(self._soul_engine, "_module_overrides", {})
        return LLMService(
            registry=self._llm,
            memory=memory,
            module_overrides=module_overrides or {},
            concurrency=int(getattr(self._soul_engine, "_llm_concurrency", 3)),
        )
