from __future__ import annotations

import base64
import json
import time
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.codex_auth import (
    CodexAuthError,
    CodexCredentials,
    delete_codex_credentials,
    get_valid_codex_token,
    import_codex_credentials,
    load_codex_credentials,
    refresh_codex_token,
    save_codex_credentials,
)

if TYPE_CHECKING:
    from pathlib import Path


def _jwt_with_payload(payload: dict[str, object]) -> str:
    def part(data: dict[str, object]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return ".".join([part({"alg": "none"}), part(payload), "sig"])


def test_credentials_expiry_uses_safety_window() -> None:
    now = time.time()

    assert CodexCredentials("access", "refresh", now + 600).is_expired() is False
    assert CodexCredentials("access", "refresh", now + 120).is_expired() is True
    assert CodexCredentials("access", "refresh", now - 1).is_expired() is True


def test_save_load_and_delete_credentials_round_trip(tmp_path: Path) -> None:
    token_path = tmp_path / "codex_auth.json"
    creds = CodexCredentials(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=1234567890.0,
        account_id="acct_123",
    )

    save_codex_credentials(creds, token_path=token_path)

    assert load_codex_credentials(token_path=token_path) == creds
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"

    assert delete_codex_credentials(token_path=token_path) is True
    assert load_codex_credentials(token_path=token_path) is None
    assert delete_codex_credentials(token_path=token_path) is False


def test_import_codex_credentials_reads_nested_codex_cli_shape(tmp_path: Path) -> None:
    expires_at = int(time.time()) + 3600
    source = tmp_path / "auth.json"
    destination = tmp_path / "openbiliclaw" / "codex_auth.json"
    source.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": _jwt_with_payload(
                        {
                            "exp": expires_at,
                            "https://api.openai.com/auth": {"chatgpt_account_id": "acct_nested"},
                        }
                    ),
                    "refresh_token": "refresh-token",
                }
            }
        ),
        encoding="utf-8",
    )

    creds = import_codex_credentials(source=source, destination=destination)

    assert creds.refresh_token == "refresh-token"
    assert creds.expires_at == float(expires_at)
    assert creds.account_id == "acct_nested"
    assert load_codex_credentials(token_path=destination) == creds


def test_import_codex_credentials_rejects_missing_refresh_token(tmp_path: Path) -> None:
    source = tmp_path / "auth.json"
    source.write_text(json.dumps({"access_token": "access"}), encoding="utf-8")

    with pytest.raises(CodexAuthError, match="refresh_token"):
        import_codex_credentials(source=source, destination=tmp_path / "out.json")


@pytest.mark.asyncio
async def test_refresh_codex_token_updates_stored_credentials(tmp_path: Path) -> None:
    token_path = tmp_path / "codex_auth.json"
    old = CodexCredentials(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=time.time() - 10,
        account_id="acct_old",
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "account_id": "acct_new",
            }

    class FakeClient:
        def __init__(self) -> None:
            self.data: dict[str, object] | None = None

        async def post(self, _url: str, *, data: dict[str, object], timeout: float) -> FakeResponse:
            self.data = data
            assert timeout == 30.0
            return FakeResponse()

    client = FakeClient()

    refreshed = await refresh_codex_token(old, token_path=token_path, client=client)

    assert client.data is not None
    assert client.data["grant_type"] == "refresh_token"
    assert client.data["refresh_token"] == "old-refresh"
    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "new-refresh"
    assert load_codex_credentials(token_path=token_path) == refreshed


@pytest.mark.asyncio
async def test_get_valid_codex_token_refreshes_expired_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "codex_auth.json"
    save_codex_credentials(
        CodexCredentials("old-access", "old-refresh", time.time() - 10),
        token_path=token_path,
    )
    refreshed = CodexCredentials("new-access", "new-refresh", time.time() + 3600)

    async def fake_refresh(credentials: CodexCredentials, *, token_path=None, client=None):
        assert credentials.access_token == "old-access"
        assert token_path == token_path_value
        return refreshed

    token_path_value = token_path
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.refresh_codex_token",
        fake_refresh,
    )

    token = await get_valid_codex_token(token_path=token_path)

    assert token == "new-access"
