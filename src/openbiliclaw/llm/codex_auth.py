"""Experimental Codex CLI OAuth credential support.

This module imports the local Codex CLI login state and exposes a bearer
token for the existing OpenAI provider. It is intentionally conservative:
OpenBiliClaw does not run its own OAuth browser flow here, and it never
prints token values.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

_CODEX_AUTH_PATH_ENV = "OPENBILICLAW_CODEX_AUTH_PATH"
_CODEX_CLI_AUTH_PATH_ENV = "OPENBILICLAW_CODEX_CLI_AUTH_PATH"
_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_EXPIRY_SKEW_SECONDS = 300.0
_refresh_lock = asyncio.Lock()


class CodexAuthError(RuntimeError):
    """Raised when Codex credentials cannot be loaded, imported, or refreshed."""


class _AsyncPostClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        data: dict[str, object],
        timeout: float,
    ) -> Any: ...


@dataclass(frozen=True)
class CodexCredentials:
    """Minimal token set needed to call OpenAI with Codex CLI credentials."""

    access_token: str
    refresh_token: str
    expires_at: float
    account_id: str = ""

    def is_expired(self, *, skew_seconds: float = _EXPIRY_SKEW_SECONDS) -> bool:
        """Return whether the token is expired or too close to expiry."""
        return self.expires_at <= time.time() + skew_seconds

    def to_json(self) -> dict[str, object]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
        }


def default_token_path() -> Path:
    """Return OpenBiliClaw's private Codex credential path."""
    env_path = os.environ.get(_CODEX_AUTH_PATH_ENV, "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".openbiliclaw" / "codex_auth.json"


def default_codex_cli_auth_path() -> Path:
    """Return the Codex CLI auth file path."""
    env_path = os.environ.get(_CODEX_CLI_AUTH_PATH_ENV, "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".codex" / "auth.json"


def codex_credentials_exist(*, token_path: Path | None = None) -> bool:
    """Return whether OpenBiliClaw has a local Codex credential file."""
    return (token_path or default_token_path()).expanduser().exists()


def save_codex_credentials(
    credentials: CodexCredentials,
    *,
    token_path: Path | None = None,
) -> Path:
    """Persist credentials with user-only permissions where the OS allows it."""
    path = (token_path or default_token_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path.parent, 0o700)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(credentials.to_json(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _chmod_best_effort(tmp_path, 0o600)
    tmp_path.replace(path)
    _chmod_best_effort(path, 0o600)
    return path


def load_codex_credentials(*, token_path: Path | None = None) -> CodexCredentials | None:
    """Load OpenBiliClaw's local Codex credentials."""
    path = (token_path or default_token_path()).expanduser()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAuthError(f"无法读取 Codex 凭据文件: {path}") from exc
    return _credentials_from_mapping(data, source=path)


def delete_codex_credentials(*, token_path: Path | None = None) -> bool:
    """Delete OpenBiliClaw's local Codex credentials."""
    path = (token_path or default_token_path()).expanduser()
    if not path.exists():
        return False
    path.unlink()
    return True


def import_codex_credentials(
    *,
    source: Path | None = None,
    destination: Path | None = None,
) -> CodexCredentials:
    """Import credentials from Codex CLI auth.json into OpenBiliClaw storage."""
    source_path = (source or default_codex_cli_auth_path()).expanduser()
    if not source_path.exists():
        raise CodexAuthError(f"未找到 Codex CLI 凭据文件: {source_path}")
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAuthError(f"无法读取 Codex CLI 凭据文件: {source_path}") from exc

    credentials = _credentials_from_mapping(data, source=source_path)
    save_codex_credentials(credentials, token_path=destination)
    return credentials


async def refresh_codex_token(
    credentials: CodexCredentials,
    *,
    token_path: Path | None = None,
    client: _AsyncPostClient | None = None,
) -> CodexCredentials:
    """Refresh a Codex OAuth access token and persist the new credentials."""
    data: dict[str, object] = {
        "grant_type": "refresh_token",
        "refresh_token": credentials.refresh_token,
        "client_id": _CODEX_CLIENT_ID,
    }
    if client is None:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(_TOKEN_ENDPOINT, data=data, timeout=30.0)
            refreshed = _credentials_from_refresh_response(response, credentials)
    else:
        response = await client.post(_TOKEN_ENDPOINT, data=data, timeout=30.0)
        refreshed = _credentials_from_refresh_response(response, credentials)
    save_codex_credentials(refreshed, token_path=token_path)
    return refreshed


async def get_valid_codex_token(
    *,
    force_refresh: bool = False,
    token_path: Path | None = None,
) -> str:
    """Return a valid Codex access token, refreshing when needed."""
    path = token_path or default_token_path()
    credentials = load_codex_credentials(token_path=path)
    if credentials is None:
        raise CodexAuthError("未找到 Codex OAuth 凭据，请先运行 `openbiliclaw login codex`。")
    if not force_refresh and not credentials.is_expired():
        return credentials.access_token

    async with _refresh_lock:
        latest = load_codex_credentials(token_path=path)
        if latest is None:
            raise CodexAuthError("未找到 Codex OAuth 凭据，请先运行 `openbiliclaw login codex`。")
        if not force_refresh and not latest.is_expired():
            return latest.access_token
        refreshed = await refresh_codex_token(latest, token_path=path)
        return refreshed.access_token


def run_codex_cli_login(*, command: Sequence[str] | None = None) -> None:
    """Run the official Codex CLI login flow."""
    cmd = list(command or ("codex", "login"))
    executable = shutil.which(cmd[0])
    if executable is None:
        raise CodexAuthError("未找到 `codex` 命令。请先安装/登录 Codex CLI，或使用 --import。")
    cmd[0] = executable
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise CodexAuthError("`codex login` 未成功完成，请重新登录后再试。")


def _credentials_from_mapping(data: object, *, source: Path) -> CodexCredentials:
    if not isinstance(data, dict):
        raise CodexAuthError(f"Codex 凭据格式无效: {source}")
    token_data = _find_token_mapping(data)
    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    if not access_token:
        raise CodexAuthError(f"Codex 凭据缺少 access_token: {source}")
    if not refresh_token:
        raise CodexAuthError(f"Codex 凭据缺少 refresh_token: {source}")

    jwt_payload = _decode_jwt_payload(access_token)
    expires_at = _extract_expires_at(token_data, jwt_payload)
    account_id = _extract_account_id(token_data, data, jwt_payload)
    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
    )


def _find_token_mapping(data: dict[str, object]) -> dict[str, object]:
    for key in ("tokens", "auth", "oauth"):
        nested = data.get(key)
        if isinstance(nested, dict) and ("access_token" in nested or "refresh_token" in nested):
            return nested
    return data


def _extract_expires_at(
    token_data: dict[str, object],
    jwt_payload: dict[str, object],
) -> float:
    for key in ("expires_at", "expiresAt", "expires"):
        raw = token_data.get(key)
        coerced = _coerce_float(raw)
        if coerced is not None:
            return coerced
    jwt_exp = jwt_payload.get("exp")
    if isinstance(jwt_exp, int | float):
        return float(jwt_exp)
    raise CodexAuthError("Codex 凭据缺少 expires_at，且 access_token 中没有 exp。")


def _extract_account_id(
    token_data: dict[str, object],
    root_data: dict[str, object],
    jwt_payload: dict[str, object],
) -> str:
    for source in (token_data, root_data, jwt_payload):
        raw = source.get("account_id") or source.get("chatgpt_account_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    auth_claim = jwt_payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        raw = auth_claim.get("chatgpt_account_id")
        if isinstance(raw, str):
            return raw.strip()
    return ""


def _decode_jwt_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        data = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _credentials_from_refresh_response(
    response: Any,
    previous: CodexCredentials,
) -> CodexCredentials:
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise CodexAuthError(
            "Codex token 刷新失败，请重新运行 `openbiliclaw login codex`。"
        ) from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex token 刷新响应格式无效。")
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise CodexAuthError("Codex token 刷新响应缺少 access_token。")
    refresh_token = str(payload.get("refresh_token") or previous.refresh_token).strip()
    jwt_payload = _decode_jwt_payload(access_token)
    expires_at = _refresh_expires_at(payload, jwt_payload)
    account_id = _extract_account_id(payload, {}, jwt_payload) or previous.account_id
    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
    )


def _refresh_expires_at(
    payload: dict[str, object],
    jwt_payload: dict[str, object],
) -> float:
    raw_expires_at = payload.get("expires_at")
    coerced_expires_at = _coerce_float(raw_expires_at)
    if coerced_expires_at is not None:
        return coerced_expires_at
    raw_expires_in = payload.get("expires_in")
    coerced_expires_in = _coerce_float(raw_expires_in)
    if coerced_expires_in is not None:
        return time.time() + coerced_expires_in
    jwt_exp = jwt_payload.get("exp")
    if isinstance(jwt_exp, int | float):
        return float(jwt_exp)
    raise CodexAuthError("Codex token 刷新响应缺少有效期。")


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return


def _coerce_float(value: object) -> float | None:
    if not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except ValueError:
        return None
