"""Interactive chat input helpers for the CLI.

Wraps prompt_toolkit so `openbiliclaw chat` supports multiline input
(issue #83): Enter submits, Esc+Enter (or Alt+Enter) inserts a newline.
Non-TTY environments (pipes, tests) fall back to the original single-line
``typer.prompt`` path.
"""

from __future__ import annotations

from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys

_CHAT_EXIT_COMMANDS = frozenset({"", "exit", "quit"})

MULTILINE_HINT = "Enter 发送，Esc+Enter（或 Alt+Enter）插入换行"


def supports_multiline_prompt(stdin: TextIO, stdout: TextIO) -> bool:
    """Return True when both streams are TTYs, i.e. an interactive terminal."""
    return stdin.isatty() and stdout.isatty()


def is_chat_exit_command(message: str) -> bool:
    """Return True when the stripped message ends the chat loop (empty / exit / quit)."""
    return message.strip().lower() in _CHAT_EXIT_COMMANDS


def build_multiline_key_bindings() -> KeyBindings:
    """Return key bindings where Enter submits and Esc+Enter / Alt+Enter inserts a newline."""
    bindings = KeyBindings()

    @bindings.add(Keys.Enter)
    def _submit(event: KeyPressEvent) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _insert_newline(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


def build_multiline_session() -> PromptSession[str]:
    """Create a multiline PromptSession with Enter-to-submit key bindings."""
    return PromptSession(multiline=True, key_bindings=build_multiline_key_bindings())
