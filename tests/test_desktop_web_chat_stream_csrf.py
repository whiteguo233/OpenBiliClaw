"""桌面 Web 聊天流 CSRF 契约（PR #351）。

streamAgentChatTurn（/chat/agent/stream）与 streamChatTurn（/chat/stream）
请求必须带同源凭证与 X-OBC-Auth 头，
否则开启访问密码时被 csrf_ok 拒绝（403），turn 永远 pending。静态契约测试。
"""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        source,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} not found"
    return match.group("body")


def test_agent_stream_fetch_sends_csrf_header_and_credentials() -> None:
    body = _function_body(_read(APP_JS), "streamAgentChatTurn")
    match = re.search(
        r"fetch\(`\$\{base\}\$\{ENDPOINTS\.chatAgentStream\}`,\s*\{(?P<opts>.*?)\n      \}\)",
        body,
        flags=re.S,
    )
    assert match is not None, "agent stream fetch not found"
    opts = match.group("opts")
    assert 'credentials: "same-origin"' in opts
    assert re.search(r"headers:\s*withBearer\(\{[^}]*\"X-OBC-Auth\":\s*\"1\"", opts)


def test_agent_stream_endpoint_only_fetched_in_stream_function() -> None:
    source = _read(APP_JS)
    assert source.count("ENDPOINTS.chatAgentStream") == 1


def test_chat_stream_fetch_sends_csrf_header_and_credentials() -> None:
    body = _function_body(_read(APP_JS), "streamChatTurn")
    match = re.search(
        r"fetch\(`\$\{base\}\$\{ENDPOINTS\.chatStream\}`,\s*\{(?P<opts>.*?)\n      \}\)",
        body,
        flags=re.S,
    )
    assert match is not None, "chat stream fetch not found"
    opts = match.group("opts")
    assert 'credentials: "same-origin"' in opts
    assert re.search(r"headers:\s*withBearer\(\{[^}]*\"X-OBC-Auth\":\s*\"1\"", opts)
