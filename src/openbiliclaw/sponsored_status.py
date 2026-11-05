"""Credential-free, durable status shared by API and background SDK workers.

Only stable error codes are persisted, never upstream bodies, prompts or keys.
Each capability has one atomic file; a successful embedding request cannot
erase a chat permission failure. Changing the runtime binary invalidates old
evidence, while changing only the chosen chat model does not.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openbiliclaw.config import Config

UPDATE_URL = "https://github.com/whiteguo233/OpenBiliClaw/releases/latest"
_directory: Path | None = None

GUIDANCE = {
    "AUTH_FAILED": (
        "免费方案凭据不可用",
        "内置免费凭据已被服务商拒绝，可能已过期、撤销或权限发生变化。"
        "请更新到最新的 OpenBiliClaw 安装包/版本后重试；也可在模型设置关闭免费方案，"
        "改用自己的 API Key。更换两个免费模型不能更新凭据。",
    ),
    "POLICY_INVALID": (
        "免费组件需要更新",
        "当前免费组件的授权策略已过期或无效。请更新到最新的 OpenBiliClaw 安装包/版本；"
        "也可关闭免费方案，改用自己的 API Key。",
    ),
    "QUOTA_EXCEEDED": (
        "免费方案额度暂不可用",
        "免费方案的可用额度已用完，请等待额度恢复，或在模型设置改用自己的 API Key。"
        "这不是凭据过期，更新版本不保证恢复额度。",
    ),
    "BALANCE_LOW": (
        "免费服务额度不足",
        "免费服务账户当前额度不足，请稍后重试，或在模型设置改用自己的 API Key。",
    ),
}


def configure_status_directory(data_path: Path | None) -> None:
    """Bind the active installation; None keeps standalone/tests memory-free."""
    global _directory
    _directory = data_path / "runtime" / "sponsored-status" if data_path is not None else None


def runtime_identity(command: Sequence[str]) -> str:
    """Non-secret identity for the executable and its invocation."""
    parts = list(command)
    if parts:
        try:
            stat = Path(parts[0]).stat()
            parts.extend([str(stat.st_size), str(stat.st_mtime_ns)])
        except OSError:
            pass
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _read(directory: Path, capability: str) -> dict[str, Any]:
    try:
        path = directory / f"{capability}.json"
        if path.stat().st_size > 4096:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record_sponsored_outcome(command: Sequence[str], *, capability: str, code: str = "") -> None:
    """Record actionable failures/recovery without affecting model execution.

    Transient errors do not imply credential expiry and cannot clear a prior
    revocation. Only a successful request or a different binary clears it.
    """
    if capability not in {"chat", "embedding"} or (code and code not in GUIDANCE):
        return
    directory = _directory
    if directory is None:
        return
    tmp: str | None = None
    try:
        identity = runtime_identity(command)
        previous = _read(directory, capability)
        if previous.get("runtime") == identity and previous.get("code") == code:
            return
        # Healthy traffic creates no files; only retain recovery for an existing alert.
        if not code and not previous:
            return
        value = {"runtime": identity, "code": code, "observed_at": time.time()}
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{capability}-", dir=directory)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(tmp, directory / f"{capability}.json")
        tmp = None
        if code:
            from openbiliclaw.diagnostics_alerts import record_diagnostics_alert

            record_diagnostics_alert(
                category="embedding" if capability == "embedding" else "llm",
                code=f"sponsored_{code.lower()}",
                message=GUIDANCE[code][1],
                source="sponsored",
                severity="error",
            )
    except (OSError, ValueError):
        pass
    finally:
        if tmp is not None:
            with suppress(OSError):
                Path(tmp).unlink(missing_ok=True)


def sponsored_status(config: Config) -> dict[str, Any]:
    """Return current actionable notices without making provider requests."""
    result: dict[str, Any] = {"notices": [], "update_url": UPDATE_URL}
    settings = getattr(getattr(config, "llm", None), "sponsored", None)
    if settings is None or not settings.enabled:
        return result
    from openbiliclaw.runtime.sponsored_bundle import installed_runtime_command

    try:
        command = shlex.split(settings.runtime_path or installed_runtime_command())
    except ValueError:
        return result
    if not command:
        from openbiliclaw.runtime.sponsored_bundle import bundle_status

        if bundle_status().get("code") == "POLICY_INVALID":
            result["notices"] = [
                {
                    "id": "sponsored-policy_invalid",
                    "code": "POLICY_INVALID",
                    "title": GUIDANCE["POLICY_INVALID"][0],
                    "message": GUIDANCE["POLICY_INVALID"][1],
                    "requires_update": True,
                    "capabilities": ["chat"],
                }
            ]
        return result
    identity = runtime_identity(command)
    directory = config.data_path / "runtime" / "sponsored-status"
    capabilities = ["chat", "embedding"] if settings.uses_cloud_embedding else ["chat"]
    by_code: dict[str, dict[str, Any]] = {}
    for capability in capabilities:
        value = _read(directory, capability)
        code = value.get("code")
        if value.get("runtime") != identity or not isinstance(code, str) or code not in GUIDANCE:
            continue
        notice = by_code.setdefault(
            code,
            {
                "id": f"sponsored-{code.lower()}",
                "code": code,
                "title": GUIDANCE[code][0],
                "message": GUIDANCE[code][1],
                "requires_update": code in {"AUTH_FAILED", "POLICY_INVALID"},
                "capabilities": [],
            },
        )
        notice["capabilities"].append(capability)
    result["notices"] = list(by_code.values())
    return result
