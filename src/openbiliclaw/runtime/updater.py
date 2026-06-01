"""Auto-update service — periodically check for and apply backend source tags.

Release contract:
- backend source updates are git tags named ``backend-vX.Y.Z``;
- legacy ``vX.Y.Z`` / bare ``X.Y.Z`` tags are tolerated for old installs;
- extension artifacts use ``extension-vX.Y.Z`` and MUST be ignored here;
- GitHub ``/releases/latest`` is not authoritative for backend updates because
  current Releases are extension artifacts. ``_fetch_latest_version`` therefore
  queries ``/tags`` directly and filters for backend tags.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

import openbiliclaw

logger = logging.getLogger(__name__)

_GITHUB_TAGS = "https://api.github.com/repos/whiteguo233/OpenBiliClaw/tags"
_BACKEND_TAG_PREFIX = "backend-v"
_MAX_TAG_PAGES = 5
_TAGS_PER_PAGE = 100
_VERSION_RE = re.compile(
    r"^(?P<version>\d+(?:\.\d+)*)(?P<prerelease>-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
DEFAULT_ALLOWED_REMOTES = (
    "https://github.com/whiteguo233/OpenBiliClaw.git",
    "git@github.com:whiteguo233/OpenBiliClaw.git",
)
_OBSERVE_VIA = "runtime-stream"


@dataclass(frozen=True)
class _BackendTagCandidate:
    version: tuple[int, ...]
    version_text: str
    tag: str
    canonical: bool
    prerelease: bool


@dataclass(frozen=True)
class _BackendTagSelection:
    tag: str = ""
    version: tuple[int, ...] = (0,)
    version_text: str = ""
    ignored_prerelease_version: tuple[int, ...] | None = None
    error_reason: str = ""


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


def _parse_backend_candidate(
    tag: str,
    *,
    include_prerelease: bool = False,
) -> _BackendTagCandidate | None:
    """Parse backend release tags and ignore extension/non-backend tags."""
    raw = tag.strip()
    if not raw:
        return None
    canonical = False
    if raw.startswith(_BACKEND_TAG_PREFIX):
        canonical = True
        version_text = raw.removeprefix(_BACKEND_TAG_PREFIX)
    elif raw[:1] in {"v", "V"} and len(raw) > 1 and raw[1].isdigit():
        version_text = raw[1:]
    elif raw[0].isdigit():
        version_text = raw
    elif raw[0].isalpha():
        return None
    else:
        version_text = raw

    match = _VERSION_RE.match(version_text)
    if match is None:
        return None
    prerelease = bool(match.group("prerelease"))
    if prerelease and not include_prerelease:
        return None
    version = tuple(int(part) for part in match.group("version").split("."))
    return _BackendTagCandidate(
        version=version,
        version_text=match.group("version"),
        tag=raw,
        canonical=canonical,
        prerelease=prerelease,
    )


def _parse_backend_version(tag: str) -> tuple[int, ...] | None:
    """Parse stable backend release tags and ignore extension/prerelease tags."""
    candidate = _parse_backend_candidate(tag)
    return candidate.version if candidate is not None else None


def _string_from_mapping_field(
    payload: Mapping[str, object],
    field: str,
) -> str:
    value = payload.get(field)
    return value.strip() if isinstance(value, str) else ""


def _remote_has_credentials(remote_url: str) -> bool:
    parsed = urlparse(remote_url)
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.username or parsed.password)
    return False


def _merge_or_rebase_in_progress(root: Path, git_dir_text: str) -> bool:
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    return any(
        (git_dir / marker).exists() for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply")
    )


@dataclass
class AutoUpdateService:
    """Periodically check GitHub for a newer version and auto-apply updates."""

    enabled: bool = False
    check_interval_hours: int = 6
    check_interval_seconds: int = 600  # loop sleep between due-checks
    allow_prerelease: bool = False
    allowed_remotes: Sequence[str] = DEFAULT_ALLOWED_REMOTES
    event_publisher: Callable[[dict[str, object]], Awaitable[object]] | None = None
    _last_check_at: datetime | None = field(default=None, repr=False)
    _latest_remote_version: str = field(default="", repr=False)
    _latest_tag: str = field(default="", repr=False)
    _state: str = field(default="", repr=False)
    _reason: str = field(default="none", repr=False)
    _update_error: str = field(default="", repr=False)
    _apply_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _apply_task: asyncio.Task[None] | None = field(default=None, repr=False)

    # --- public API -----------------------------------------------------------

    async def check_and_update_if_due(self) -> dict[str, object]:
        """Run the update check only when the configured interval has elapsed."""
        if not self.enabled:
            return {"checked": False, "reason": "disabled"}
        if not self._is_due():
            return {"checked": False, "reason": "not_due"}
        return await self.check_and_update_now()

    async def check_and_update_now(self) -> dict[str, object]:
        """Check for a new version and auto-apply it when scheduling is enabled."""
        backend = await self.check_now()
        if backend["state"] != "update_available":
            reason = str(backend.get("reason", "none"))
            if reason == "none" and backend["state"] == "error":
                reason = "github_unreachable"
            if reason == "no_backend_tag_yet":
                return {"checked": True, "updated": False, "reason": reason}
            return {
                "checked": True,
                "updated": False,
                "reason": reason,
                "current_version": backend["current_version"],
                "remote_version": backend["latest_tag"],
            }
        if not self.enabled:
            return {
                "checked": True,
                "updated": False,
                "reason": "disabled",
                "current_version": backend["current_version"],
                "remote_version": backend["latest_tag"],
            }
        status_code, payload = await self.request_apply(tag=str(backend["latest_tag"]))
        return {
            "checked": True,
            "updated": status_code == 202,
            "reason": payload.get("reason", "none"),
            "current_version": backend["current_version"],
            "remote_version": backend["latest_tag"],
        }

    async def check_now(self) -> dict[str, object]:
        """Manually refresh backend update status without applying updates."""
        self._state = "checking"
        self._reason = "none"
        self._last_check_at = datetime.now(tz=UTC)
        current = openbiliclaw.__version__
        current_parsed = _parse_version(current)
        selection = await self._fetch_latest_candidate()

        if selection.error_reason:
            self._state = "error"
            self._reason = selection.error_reason
            self._update_error = selection.error_reason
            logger.warning("Auto-update version check failed: %s", selection.error_reason)
            return self.get_update_status()

        self._latest_tag = selection.tag
        self._latest_remote_version = selection.version_text
        ignored_newer_prerelease = (
            selection.ignored_prerelease_version is not None
            and selection.ignored_prerelease_version > current_parsed
        )
        if not selection.tag:
            if ignored_newer_prerelease:
                self._state = "up_to_date"
                self._reason = "prerelease_ignored"
                self._update_error = ""
            else:
                self._state = "error"
                self._reason = "no_backend_tag_yet"
                self._update_error = "no_backend_tag_yet"
                logger.info("Auto-update check found no_backend_tag_yet")
            return self.get_update_status()

        if selection.version <= current_parsed:
            self._state = "up_to_date"
            self._reason = "prerelease_ignored" if ignored_newer_prerelease else "none"
            self._update_error = ""
            logger.info("Already up-to-date: current=%s, remote=%s", current, selection.tag)
            return self.get_update_status()

        self._state = "update_available"
        self._reason = "none"
        self._update_error = ""
        await self._publish_event(
            {
                "type": "backend_update_available",
                "current_version": current,
                "latest_version": selection.version_text,
                "latest_tag": selection.tag,
            }
        )
        return self.get_update_status()

    async def request_apply(self, *, tag: str = "") -> tuple[int, dict[str, object]]:
        """Validate and start a backend apply flow, returning before restart."""
        async with self._apply_lock:
            if self._apply_task is not None and not self._apply_task.done():
                self._state = "applying"
                self._reason = "already_applying"
                return 409, self._apply_response(
                    state="applying",
                    reason="already_applying",
                    accepted=False,
                )

            target_tag = tag.strip() or self._latest_tag
            if not target_tag:
                self._state = "blocked"
                self._reason = "missing_target_tag"
                return 409, self._apply_response(
                    state="blocked",
                    reason="missing_target_tag",
                    accepted=False,
                )

            guard_reason = await self._check_apply_guards(target_tag)
            if guard_reason:
                self._state = (
                    "unsupported"
                    if guard_reason.startswith("unsupported_")
                    else "error"
                    if guard_reason == "github_unreachable"
                    else "blocked"
                )
                self._reason = guard_reason
                return 409, self._apply_response(
                    state=self._state,
                    reason=guard_reason,
                    accepted=False,
                )

            self._state = "applying"
            self._reason = "none"
            self._apply_task = asyncio.create_task(self._apply_update_to_tag(target_tag))
            return 202, self._apply_response(
                state="applying",
                reason="none",
                accepted=True,
            )

    def get_update_status(self) -> dict[str, object]:
        """Expose backend update status for the update-status API."""
        state = self._state or ("disabled" if not self.enabled else "unknown")
        return {
            "state": state,
            "auto_update_enabled": self.enabled,
            "current_version": openbiliclaw.__version__,
            "latest_version": self._latest_remote_version,
            "latest_tag": self._latest_tag,
            "last_check_at": self._last_check_at.isoformat() if self._last_check_at else "",
            "last_error": self._update_error,
            "reason": self._reason or "none",
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
            "backend_update_state": (
                self._state or ("disabled" if not self.enabled else "unknown")
            ),
            "backend_update_reason": self._reason or "none",
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

    async def _fetch_latest_candidate(self) -> _BackendTagSelection:
        """Query GitHub tags and select the newest allowed backend tag."""
        async with httpx.AsyncClient(timeout=30) as client:
            canonical: list[_BackendTagCandidate] = []
            legacy: list[_BackendTagCandidate] = []
            ignored_prereleases: list[_BackendTagCandidate] = []
            for page in range(1, _MAX_TAG_PAGES + 1):
                try:
                    resp = await client.get(
                        _GITHUB_TAGS,
                        headers={"Accept": "application/vnd.github.v3+json"},
                        params={"per_page": _TAGS_PER_PAGE, "page": page},
                    )
                except Exception as exc:
                    logger.warning("Auto-update tag check failed: %s", exc)
                    return _BackendTagSelection(error_reason="github_unreachable")
                if resp.status_code != 200:
                    logger.warning("Auto-update tag check failed: HTTP %s", resp.status_code)
                    return _BackendTagSelection(error_reason="github_unreachable")
                tags = resp.json()
                if not tags:
                    break
                if not isinstance(tags, list):
                    logger.warning("Auto-update tag check failed: unexpected tags payload")
                    return _BackendTagSelection(error_reason="github_unreachable")
                for tag_payload in tags:
                    if not isinstance(tag_payload, Mapping):
                        continue
                    tag = _string_from_mapping_field(tag_payload, "name")
                    candidate = _parse_backend_candidate(tag, include_prerelease=True)
                    if candidate is None:
                        continue
                    if candidate.prerelease and not self.allow_prerelease:
                        ignored_prereleases.append(candidate)
                        continue
                    if candidate.canonical:
                        canonical.append(candidate)
                    else:
                        legacy.append(candidate)
            candidates = canonical or legacy
            ignored = max(
                ignored_prereleases,
                key=lambda item: item.version,
                default=None,
            )
            if candidates:
                latest = max(candidates, key=lambda item: item.version)
                return _BackendTagSelection(
                    tag=latest.tag,
                    version=latest.version,
                    version_text=latest.version_text,
                    ignored_prerelease_version=ignored.version if ignored else None,
                )
        return _BackendTagSelection(
            ignored_prerelease_version=ignored.version if ignored else None,
        )

    async def _fetch_latest_version(self) -> str:
        """Query GitHub tags for the newest backend version tag."""
        return (await self._fetch_latest_candidate()).tag

    async def _check_apply_guards(self, tag: str) -> str:
        """Return a stable reason when local state makes apply unsafe."""
        root = _project_root()
        if not (root / ".git").exists():
            inside = await self._run_git(["rev-parse", "--is-inside-work-tree"], root)
            if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
                return "unsupported_install_mode"

        remote = await self._run_git(["config", "--get", "remote.origin.url"], root)
        remote_url = remote.stdout.strip() if remote.returncode == 0 else ""
        if not remote_url or _remote_has_credentials(remote_url):
            return "untrusted_remote"
        if remote_url not in set(self.allowed_remotes):
            return "untrusted_remote"

        status = await self._run_git(["status", "--porcelain"], root)
        if status.returncode != 0:
            return "unsupported_install_mode"
        if status.stdout.strip():
            return "dirty_worktree"

        git_dir = await self._run_git(["rev-parse", "--git-dir"], root)
        if git_dir.returncode != 0:
            return "unsupported_install_mode"
        if _merge_or_rebase_in_progress(root, git_dir.stdout.strip()):
            return "merge_or_rebase_in_progress"

        fetch = await self._run_git(["fetch", "--tags", "origin"], root, timeout=120)
        if fetch.returncode != 0:
            return "github_unreachable"

        target = await self._run_git(["rev-parse", "--verify", f"{tag}^{{commit}}"], root)
        if target.returncode != 0:
            return "missing_target_tag"

        ff = await self._run_git(["merge-base", "--is-ancestor", "HEAD", tag], root)
        if ff.returncode != 0:
            return "branch_not_fast_forwardable"
        return ""

    async def _apply_update_to_tag(self, tag: str) -> None:
        """Fast-forward to *tag*, reinstall dependencies, and restart."""
        root = _project_root()
        try:
            merge = await self._run_git(["merge", "--ff-only", tag], root, timeout=120)
            if merge.returncode != 0:
                await self._mark_apply_failed("branch_not_fast_forwardable")
                return

            install_cmd = self._detect_install_command(root)
            install = await self._run_command(install_cmd, root, timeout=300)
            if install.returncode != 0:
                await self._mark_apply_failed("dependency_sync_failed")
                return

            self._state = "restart_pending"
            self._reason = "none"
            self._update_error = ""
            await self._publish_event(
                {
                    "type": "backend_restart_pending",
                    "latest_tag": tag,
                }
            )
            try:
                logger.info("Auto-update applied; restarting process for %s", tag)
                self._restart_process()
            except Exception as exc:
                logger.error("Auto-update restart failed: %s", exc)
                await self._mark_apply_failed("restart_failed")
        except Exception:
            logger.exception("Auto-update apply failed")
            await self._mark_apply_failed("dependency_sync_failed")

    async def _mark_apply_failed(self, reason: str) -> None:
        self._state = "error"
        self._reason = reason
        self._update_error = reason
        await self._publish_event({"type": "backend_update_failed", "reason": reason})

    async def _publish_event(self, event: dict[str, object]) -> None:
        if self.event_publisher is None:
            return
        try:
            result = self.event_publisher(dict(event))
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.debug("Runtime update event publish failed", exc_info=True)

    def _apply_response(
        self,
        *,
        state: str,
        reason: str,
        accepted: bool,
    ) -> dict[str, object]:
        return {
            "target": "backend",
            "state": state,
            "reason": reason,
            "accepted": accepted,
            "observe_via": _OBSERVE_VIA,
        }

    async def _run_git(
        self,
        args: Sequence[str],
        root: Path,
        *,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return await self._run_command(["git", *args], root, timeout=timeout)

    async def _run_command(
        self,
        command: Sequence[str],
        root: Path,
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                list(command),
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
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
