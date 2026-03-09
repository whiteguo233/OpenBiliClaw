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
    from openbiliclaw.llm.base import LLMProvider
    from openbiliclaw.llm.service import LLMService
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
        llm: LLMProvider | None,
        soul_engine: SoulEngine,
        llm_service: LLMService | None = None,
        session: str = "cli",
    ) -> None:
        self._llm = llm
        self._soul_engine = soul_engine
        self._llm_service = llm_service
        self._session = session
        self._history: list[DialogueTurn] = []

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
            response = await service.complete_socratic_dialogue(
                user_message=user_message,
                history=self._history_to_messages(),
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
        return LLMService(registry=self._llm, memory=memory)
