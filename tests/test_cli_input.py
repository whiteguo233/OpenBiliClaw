"""Tests for openbiliclaw.cli_input multiline chat helpers."""

import io

from openbiliclaw.cli_input import (
    MULTILINE_HINT,
    build_multiline_key_bindings,
    build_multiline_session,
    is_chat_exit_command,
    supports_multiline_prompt,
)


class _FakeTTY(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_supports_multiline_prompt_requires_both_ttys() -> None:
    assert supports_multiline_prompt(_FakeTTY(True), _FakeTTY(True)) is True
    assert supports_multiline_prompt(_FakeTTY(False), _FakeTTY(True)) is False
    assert supports_multiline_prompt(_FakeTTY(True), _FakeTTY(False)) is False
    assert supports_multiline_prompt(io.StringIO(), io.StringIO()) is False


def test_is_chat_exit_command_accepts_empty_and_exit_words() -> None:
    assert is_chat_exit_command("") is True
    assert is_chat_exit_command("   \n  ") is True
    assert is_chat_exit_command("exit") is True
    assert is_chat_exit_command("EXIT") is True
    assert is_chat_exit_command(" quit ") is True


def test_is_chat_exit_command_keeps_normal_and_multiline_messages() -> None:
    assert is_chat_exit_command("exit now") is False
    assert is_chat_exit_command("你好") is False
    assert is_chat_exit_command("第一行\n第二行") is False


def test_build_multiline_session_uses_multiline_buffer() -> None:
    session = build_multiline_session()
    assert session.default_buffer.multiline() is True


def test_build_multiline_key_bindings_registers_enter_and_escape_enter() -> None:
    from prompt_toolkit.keys import Keys

    bindings = build_multiline_key_bindings()
    keys = [tuple(b.keys) for b in bindings.bindings]
    assert (Keys.Enter,) in keys
    assert (Keys.Escape, Keys.Enter) in keys


def test_multiline_hint_mentions_shortcuts() -> None:
    assert "Esc+Enter" in MULTILINE_HINT
    assert "Alt+Enter" in MULTILINE_HINT
