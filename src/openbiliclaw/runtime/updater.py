"""Auto-update service — periodically check for and apply new versions."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

import openbiliclaw

logger = logging.getLogger(__name__)

_GITHUB_API_LATEST = (
    "https://api.github.com/repos/whiteguo233/OpenBiliClaw/releases/latest"
)
_GITHUB_TAGS = (
    "https://api.github.com/repos/whiteguo233/OpenBiliClaw/tags"
)


def _project_root() -> Path:
    """Return the git root of the project (best-effort)."""
    env_root = os.environ.get("OPENBILICLAW_PROJECT_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    # Walk up from package location
    pkg_dir = Path(openbiliclaw.__file__).resolve().parent
    for parent in [pkg_dir, *pkg_dir.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like 'v0.2.1' or '0.2.1' into a comparable tuple."""
    v = v.strip().lstrip("vV")
    parts: list[int] = []
    for seg in v.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) or (0,)


@dataclass
class AutoUpdateService:
    """Periodically check GitHub for a newer version and auto-apply updates."""

    enabled: bool = True
    check_interval_hours: int = 6
    check_interval_seconds: int = 600  # loop sleep between due-checks
    _last_check_at: datetime | None = field(default=None, repr=False)
    _latest_remote_version: str = field(default="", repr=False)
    _update_error: str = field(default="", repr=False)

    # --- public API -----------------------------------------------------------

    async def check_and_update_if_due(self) -> dict[str, object]:
        """Run the update check only when the configured interval has elapsed."""
        if not self.enabled:
            return {"checked": False, "reason": "disabled"}
        if not self._is_due():
            return {"checked": False, "reason": "not_due"}
        return await self.check_and_update_now()

    async def check_and_update_now(self) -> dict[str, object]:
        """Check for a new version and apply it immediately if available."""
        self._last_check_at = datetime.now(tz=UTC)
        current = openbiliclaw.__version__
        try:
            remote_version = await self._fetch_latest_version()
        except Exception as exc:
            self._update_error = str(exc)
            logger.warning("Auto-update version check failed: %s", exc)
            return {"checked": True, "updated": False, "error": str(exc)}

        self._latest_remote_version = remote_version
        if not remote_version:
            return {"checked": True, "updated": False, "reason": "no_remote_version"}

        if _parse_version(remote_version) <= _parse_version(current):
            logger.info(
                "Already up-to-date: current=%s, remote=%s", current, remote_version
            )
            return {
                "checked": True,
                "updated": False,
                "current_version": current,
                "remote_version": remote_version,
            }

        logger.info(
            "New version available: %s -> %s, applying update …",
            current,
            remote_version,
        )
        try:
            await self._apply_update()
        except Exception as exc:
            self._update_error = str(exc)
            logger.error("Auto-update apply failed: %s", exc)
            return {
                "checked": True,
                "updated": False,
                "current_version": current,
                "remote_version": remote_version,
                "error": str(exc),
            }

        self._update_error = ""
        logger.info("Update applied successfully, restarting process …")
        self._restart_process()
        # If restart fails (shouldn't normally reach here), still report success
        return {
            "checked": True,
            "updated": True,
            "current_version": current,
            "remote_version": remote_version,
        }

    def get_runtime_status(self) -> dict[str, object]:
        """Expose update status for the runtime-status API."""
        return {
            "auto_update_enabled": self.enabled,
            "current_version": openbiliclaw.__version__,
            "latest_remote_version": self._latest_remote_version,
            "last_update_check_at": (
                self._last_check_at.isoformat() if self._last_check_at else ""
            ),
            "last_update_error": self._update_error,
        }

    async def run_forever(self) -> None:
        """Background loop: periodically check and apply updates."""
        if not self.enabled:
            return
        # Small initial delay to let the main app finish startup
        await asyncio.sleep(10)
        while True:
            try:
                await self.check_and_update_if_due()
            except Exception:
                logger.exception("Unexpected error in auto-update loop")
            await asyncio.sleep(self.check_interval_seconds)

    # --- internals ------------------------------------------------------------

    def _is_due(self) -> bool:
        if self._last_check_at is None:
            return True
        elapsed = datetime.now(tz=UTC) - self._last_check_at
        return elapsed >= timedelta(hours=self.check_interval_hours)

    async def _fetch_latest_version(self) -> str:
        """Query GitHub API for the latest release or tag version."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Try releases/latest first
            try:
                resp = await client.get(
                    _GITHUB_API_LATEST,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tag = data.get("tag_name", "")
                    if tag:
                        return tag
            except Exception:
                pass

            # Fallback: fetch tags
            resp = await client.get(
                _GITHUB_TAGS,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                tags = resp.json()
                if tags and isinstance(tags, list):
                    # Tags are returned newest first
                    return tags[0].get("name", "")
        return ""

    async def _apply_update(self) -> None:
        """Pull latest code and reinstall dependencies."""
        root = _project_root()
        loop = asyncio.get_running_loop()

        # git pull
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            ),
        )

        # Reinstall dependencies
        install_cmd = self._detect_install_command(root)
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                install_cmd,
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            ),
        )

    @staticmethod
    def _detect_install_command(root: Path) -> list[str]:
        """Detect the best install command based on the project environment."""
        # Prefer uv if uv.lock exists
        if (root / "uv.lock").exists():
            return ["uv", "sync"]
        # Fallback to pip
        return [sys.executable, "-m", "pip", "install", "-e", "."]

    @staticmethod
    def _restart_process() -> None:
        """Restart the current process with the same arguments."""
        logger.info("Restarting process: %s %s", sys.executable, sys.argv)
        os.execv(sys.executable, [sys.executable, *sys.argv])
