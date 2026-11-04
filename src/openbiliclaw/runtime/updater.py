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
import locale
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import httpx

import openbiliclaw
from openbiliclaw.docker_runtime import is_running_in_container
from openbiliclaw.proc import no_window_kwargs

logger = logging.getLogger(__name__)

_GITHUB_TAGS = "https://api.github.com/repos/whiteguo233/OpenBiliClaw/tags"
_GITHUB_TAGS_ATOM = "https://github.com/whiteguo233/OpenBiliClaw/tags.atom"
_BACKEND_TAG_PREFIX = "backend-v"
_DESKTOP_TAG_PREFIX = "desktop-v"
_MAX_TAG_PAGES = 5
_TAGS_PER_PAGE = 100
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_VERSION_RE = re.compile(
    r"^(?P<version>\d+(?:\.\d+)*)(?P<prerelease>-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
DEFAULT_ALLOWED_REMOTES = (
    "https://github.com/whiteguo233/OpenBiliClaw.git",
    "git@github.com:whiteguo233/OpenBiliClaw.git",
)
_OBSERVE_VIA = "runtime-stream"
_TLS_VERIFICATION_GUIDANCE = (
    "TLS 证书校验失败;请修复系统证书链,或显式设置 SSL_CERT_FILE 指向可信 CA 证书包后重试"
)
_PROCESS_APPLY_LOCK = asyncio.Lock()
_PROCESS_APPLY_TASK: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class _BackendTagCandidate:
    version: tuple[int, ...]
    version_text: str
    tag: str
    canonical: bool
    prerelease: bool
    # SemVer §11 identifier ordering for the prerelease suffix; empty for
    # stable releases. Only used as a selection tie-breaker (audit N8) — the
    # applied/skip decision still compares numeric ``version`` tuples.
    prerelease_key: tuple[tuple[int, int, str], ...] = ()

    @property
    def sort_key(self) -> tuple[object, ...]:
        # A stable release outranks every same-numbered prerelease; among
        # prereleases, SemVer identifier order applies (rc.10 > rc.9 > rc1).
        return (self.version, 0 if self.prerelease else 1, self.prerelease_key)


def _prerelease_sort_key(suffix: str) -> tuple[tuple[int, int, str], ...]:
    """Map ``-rc.10`` style suffixes to a SemVer §11 comparable tuple."""
    identifiers = suffix.lstrip("-").split(".")
    key: list[tuple[int, int, str]] = []
    for ident in identifiers:
        if ident.isdigit():
            key.append((0, int(ident), ""))
        else:
            key.append((1, 0, ident))
    return tuple(key)


@dataclass(frozen=True)
class _BackendTagSelection:
    tag: str = ""
    version: tuple[int, ...] = (0,)
    version_text: str = ""
    ignored_prerelease_version: tuple[int, ...] | None = None
    error_reason: str = ""
    error_detail: str = ""


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


def detect_install_mode() -> str:
    """Best-effort install-mode detection for update-status surfaces.

    - ``frozen``: PyInstaller desktop bundle. There is no git checkout to
      fast-forward, so backend self-update is structurally unsupported —
      users update by installing a newer desktop package.
    - ``docker``: running inside a container. The code is baked into the
      image, so in-place self-update is impossible — users upgrade by
      pulling a newer image. Checked before ``git`` on purpose: even if a
      checkout is mounted into the container, fast-forwarding it would not
      change the code the running image executes.
    - ``git``: the project root is a git checkout (one-line installer,
      agent install, or dev checkout) — the auto-update flow can apply.
    - ``unsupported``: anything else (e.g. a bare pip install).
    """
    if getattr(sys, "frozen", False):
        return "frozen"
    if is_running_in_container():
        return "docker"
    if (_project_root() / ".git").exists():
        return "git"
    return "unsupported"


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
    suffix = match.group("prerelease") or ""
    return _BackendTagCandidate(
        version=version,
        version_text=match.group("version") + suffix,
        tag=raw,
        canonical=canonical,
        prerelease=prerelease,
        prerelease_key=_prerelease_sort_key(suffix) if prerelease else (),
    )


def _parse_backend_version(tag: str) -> tuple[int, ...] | None:
    """Parse stable backend release tags and ignore extension/prerelease tags."""
    candidate = _parse_backend_candidate(tag)
    return candidate.version if candidate is not None else None


def _parse_desktop_candidate(
    tag: str,
    *,
    include_prerelease: bool = False,
) -> _BackendTagCandidate | None:
    """Parse desktop installer release tags — ``desktop-vX.Y.Z`` only.

    Frozen bundles update by downloading a newer installer, and installers are
    published under ``desktop-v*`` tags (which do not always ship in lockstep
    with ``backend-v*`` source tags). Legacy ``v*`` / bare semver tags were
    backend source releases, so they never count as installer candidates.
    """
    raw = tag.strip()
    if not raw.startswith(_DESKTOP_TAG_PREFIX):
        return None
    match = _VERSION_RE.match(raw.removeprefix(_DESKTOP_TAG_PREFIX))
    if match is None:
        return None
    prerelease = bool(match.group("prerelease"))
    if prerelease and not include_prerelease:
        return None
    suffix = match.group("prerelease") or ""
    return _BackendTagCandidate(
        version=tuple(int(part) for part in match.group("version").split(".")),
        version_text=match.group("version") + suffix,
        tag=raw,
        canonical=True,
        prerelease=prerelease,
        prerelease_key=_prerelease_sort_key(suffix) if prerelease else (),
    )


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


def _redact_remote_url(remote_url: str) -> str:
    """Strip any embedded ``user[:pass]@`` credentials for safe display.

    The refusal detail is shown in the UI's 最近错误 card, so a remote spelled
    ``https://ghp_xxx@github.com/owner/repo.git`` must not leak the token.
    """
    parsed = urlparse(remote_url)
    if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        rebuilt = parsed._replace(netloc=host)
        return urlunparse(rebuilt)
    return remote_url


def _canonicalize_remote_url(url: str) -> str:
    """Reduce equivalent git remote spellings to one comparable form.

    ``https://github.com/owner/repo``, ``https://github.com/owner/repo.git``,
    ``git@github.com:owner/repo.git`` and ``ssh://git@github.com/owner/repo``
    all canonicalize to ``github.com/owner/repo`` (lowercase — GitHub treats
    owner/repo case-insensitively). Exact-string matching used to reject
    every clone made without the ``.git`` suffix, permanently blocking
    auto-update on otherwise-legitimate installs.

    Mirror/proxy wrappers (``https://mirror.example/https://github.com/...``)
    intentionally do NOT canonicalize to the wrapped GitHub URL — trusting
    them automatically would let any host that embeds the official path into
    its URL serve tags. Mirror users allowlist their mirror URL explicitly in
    ``[scheduler] auto_update_allowed_remotes``.
    """
    text = url.strip().lower().rstrip("/")
    if not text:
        return ""
    if "://" in text:
        scheme, _, rest = text.partition("://")
        if scheme in {"http", "https", "ssh", "git", "git+ssh"}:
            head, slash, tail = rest.partition("/")
            host = head.rsplit("@", 1)[-1]
            if scheme in {"ssh", "git+ssh"} and host in {
                "ssh.github.com",
                "ssh.github.com:443",
            }:
                host = "github.com"
            host_path = host + slash + tail
        else:
            host_path = text
    elif ":" in text.split("/", 1)[0]:
        # scp-like syntax: [user@]host:path
        head, _, tail = text.partition(":")
        host = head.rsplit("@", 1)[-1]
        if host == "ssh.github.com":
            host = "github.com"
        host_path = f"{host}/{tail.lstrip('/')}"
    else:
        host_path = text
    if host_path.endswith(".git"):
        host_path = host_path[: -len(".git")]
    return host_path.rstrip("/")


def _is_tls_verification_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.TransportError):
        return False
    text = repr(exc).lower()
    return "certificate" in text or "cert_verify" in text or "tls" in text or "ssl" in text


def _is_github_rate_limited_response(resp: httpx.Response) -> bool:
    remaining = str(resp.headers.get("x-ratelimit-remaining", "")).strip()
    if remaining == "0":
        return True
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    try:
        payload = resp.json()
    except Exception:
        return False
    if not isinstance(payload, Mapping):
        return False
    message = _string_from_mapping_field(payload, "message").lower()
    return "rate limit" in message


def _tag_from_release_url(url: str) -> str:
    path = urlparse(url).path
    marker = "/releases/tag/"
    if marker not in path:
        return ""
    return unquote(path.rsplit(marker, maxsplit=1)[1]).strip()


def _tag_names_from_atom(text: str) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    tags: list[str] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        for link in entry.findall(f"{_ATOM_NS}link"):
            tag = _tag_from_release_url(link.attrib.get("href", ""))
            if tag:
                tags.append(tag)
                break
    return tags


def _select_latest_candidate_from_tag_names(
    tag_names: Sequence[str],
    *,
    allow_prerelease: bool,
    channel: str,
) -> _BackendTagSelection:
    parse = _parse_desktop_candidate if channel == "desktop" else _parse_backend_candidate
    canonical: list[_BackendTagCandidate] = []
    legacy: list[_BackendTagCandidate] = []
    ignored_prereleases: list[_BackendTagCandidate] = []
    for tag in tag_names:
        candidate = parse(tag, include_prerelease=True)
        if candidate is None:
            continue
        if candidate.prerelease and not allow_prerelease:
            ignored_prereleases.append(candidate)
            continue
        if candidate.canonical:
            canonical.append(candidate)
        else:
            legacy.append(candidate)

    candidates = canonical or legacy
    ignored = max(
        ignored_prereleases,
        key=lambda item: item.sort_key,
        default=None,
    )
    if candidates:
        latest = max(candidates, key=lambda item: item.sort_key)
        return _BackendTagSelection(
            tag=latest.tag,
            version=latest.version,
            version_text=latest.version_text,
            ignored_prerelease_version=ignored.version if ignored else None,
        )
    return _BackendTagSelection(
        ignored_prerelease_version=ignored.version if ignored else None,
    )


def _decode_process_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    encoding = locale.getpreferredencoding(False)
    return data.decode(encoding, errors="replace")


def _describe_exc(exc: BaseException) -> str:
    """Return a non-empty exception description suitable for logs and status."""
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def _process_failure_detail(
    operation: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    output = result.stderr.strip() or result.stdout.strip()
    if output:
        return f"{operation}: {output}"
    return f"{operation}: process exited with code {result.returncode}"


def _custom_subprocess_proxy() -> str | None:
    """Return the explicit custom proxy without changing direct/system behavior."""
    from openbiliclaw.network import outbound_proxy_mode, outbound_proxy_url

    if outbound_proxy_mode() != "custom":
        return None
    return outbound_proxy_url()


def _quote_command_path(path: Path) -> str:
    """Quote a repository path for copy-pasteable POSIX and Windows git commands."""
    escaped = str(path).replace('"', '\\"')
    return f'"{escaped}"'


def _is_dependency_command(command: Sequence[str]) -> bool:
    if not command:
        return False
    if Path(command[0]).stem.lower() == "uv":
        return True
    return len(command) >= 3 and list(command[1:3]) == ["-m", "pip"]


def _dirty_paths_besides_uv_lock(porcelain: str) -> list[str]:
    """Return dirty paths from ``git status --porcelain``, ignoring uv.lock.

    ``uv sync`` rewrites uv.lock whenever a release tag ships a stale lock
    (the version bump forgot ``uv lock``), so virtually every install has a
    modified uv.lock from day one. That alone must not block updates — the
    apply flow resets uv.lock before merging and re-syncs afterwards.
    """
    dirty: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        if line.startswith(("??", "!!")):
            continue
        index_status = line[0] if len(line) > 0 else " "
        worktree_status = line[1] if len(line) > 1 else " "
        if index_status != " " and worktree_status == " ":
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", maxsplit=1)[1].strip().strip('"')
        if path == "uv.lock":
            continue
        if path == "ollama-models" or path.startswith("ollama-models/"):
            continue
        dirty.append(path)
    return dirty


def _staged_paths_besides_uv_lock(porcelain: str) -> list[str]:
    """Return staged paths while preserving untracked and uv.lock exemptions."""
    staged: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip() or line.startswith(("??", "!!")):
            continue
        index_status = line[0] if line else " "
        if index_status == " ":
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", maxsplit=1)[1].strip().strip('"')
        if path == "uv.lock":
            continue
        if path == "ollama-models" or path.startswith("ollama-models/"):
            continue
        staged.append(path)
    return staged


def _apply_tag_rejection(tag: str, *, allow_prerelease: bool) -> tuple[str, str]:
    candidate = _parse_backend_candidate(tag, include_prerelease=True)
    if candidate is None:
        return (
            "invalid_target_tag",
            f"目标 tag {tag!r} 不是允许的 backend 发布 tag;请先重新检查更新",
        )
    if candidate.prerelease and not allow_prerelease:
        return (
            "prerelease_not_allowed",
            f"目标 tag {tag!r} 是预发布版本;请先在配置中启用 allow_prerelease",
        )
    return "", ""


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
    # Human-readable detail for the most recent apply-guard refusal (includes
    # the actual, credential-redacted remote URL + fix command). Surfaced to
    # the status card's 最近错误 so blocked users can self-diagnose without
    # digging through backend logs — the generic ``reason`` code alone doesn't
    # tell them *which* remote was rejected.
    _guard_detail: str = field(default="", repr=False)
    _apply_task: asyncio.Task[None] | None = field(default=None, repr=False)

    # --- public API -----------------------------------------------------------

    async def check_and_update_if_due(self) -> dict[str, object]:
        """Run the update check only when the configured interval has elapsed."""
        mode = detect_install_mode()
        if mode in ("frozen", "docker"):
            # Desktop bundles and docker containers can't self-apply (the
            # binary / image IS the code; there is no git checkout of their
            # own to fast-forward), but they still poll for new releases —
            # regardless of the auto-update toggle, which only governs
            # auto-apply — so the settings page and runtime stream can nudge
            # the user to download the next installer / pull the next image.
            # ``request_apply`` refuses non-git installs separately, so this
            # can never mutate a co-located git checkout.
            if not self._is_due():
                return {"checked": False, "reason": "not_due"}
            backend = await self.check_now()
            return {
                "checked": True,
                "updated": False,
                "reason": (
                    "docker_install_mode" if mode == "docker" else "unsupported_install_mode"
                ),
                "current_version": backend["current_version"],
                "remote_version": backend["latest_tag"],
            }
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
        mode = detect_install_mode()
        if mode != "git":
            # Non-git installs surface the update but never attempt to apply —
            # request_apply would refuse anyway; returning here keeps the
            # freshly-set update_available state from being clobbered by an
            # apply attempt's unsupported/blocked state.
            return {
                "checked": True,
                "updated": False,
                "reason": (
                    "docker_install_mode" if mode == "docker" else "unsupported_install_mode"
                ),
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
        # Frozen bundles update by downloading a newer installer, so they track
        # ``desktop-v*`` installer tags; git checkouts track ``backend-v*``
        # source tags. The two are not always released in lockstep.
        channel = "desktop" if detect_install_mode() == "frozen" else "backend"
        selection = await self._fetch_latest_candidate(channel=channel)

        if selection.error_reason:
            self._state = "error"
            self._reason = selection.error_reason
            self._update_error = selection.error_detail or selection.error_reason
            logger.warning("Auto-update version check failed: %s", self._update_error)
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
        global _PROCESS_APPLY_TASK

        async with _PROCESS_APPLY_LOCK:
            if _PROCESS_APPLY_TASK is not None and not _PROCESS_APPLY_TASK.done():
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
                self._update_error = "missing_target_tag"
                return 409, self._apply_response(
                    state="blocked",
                    reason="missing_target_tag",
                    accepted=False,
                )

            tag_reason, tag_detail = _apply_tag_rejection(
                target_tag,
                allow_prerelease=self.allow_prerelease,
            )
            if tag_reason:
                self._state = "blocked"
                self._reason = tag_reason
                self._update_error = tag_detail
                return 409, self._apply_response(
                    state="blocked",
                    reason=tag_reason,
                    accepted=False,
                )

            try:
                guard_reason = await self._check_apply_guards(target_tag)
            except FileNotFoundError as exc:
                guard_reason = "git_unavailable"
                self._guard_detail = _describe_exc(exc)
                logger.error("Auto-update git guard failed: %s", self._guard_detail)
            except subprocess.TimeoutExpired as exc:
                guard_reason = "git_command_timeout"
                self._guard_detail = _describe_exc(exc)
                logger.error("Auto-update git guard failed: %s", self._guard_detail)
            except Exception as exc:
                guard_reason = "git_guard_failed"
                self._guard_detail = _describe_exc(exc)
                logger.exception("Auto-update git guard failed: %s", self._guard_detail)
            if guard_reason:
                self._state = (
                    "unsupported"
                    if guard_reason in ("unsupported_install_mode", "docker_install_mode")
                    else "error"
                    if guard_reason
                    in {
                        "github_unreachable",
                        "git_unavailable",
                        "git_command_timeout",
                        "git_guard_failed",
                    }
                    else "blocked"
                )
                self._reason = guard_reason
                # Surface the refusal in ``last_error`` too — the guard path
                # used to be fully silent, leaving the status card's 最近错误
                # empty while the apply kept failing. Prefer the detailed
                # message (actual redacted remote URL + fix command) when the
                # guard produced one; fall back to the bare reason code.
                self._update_error = self._guard_detail or guard_reason
                return 409, self._apply_response(
                    state=self._state,
                    reason=guard_reason,
                    accepted=False,
                )

            self._state = "applying"
            self._reason = "none"
            self._update_error = ""
            self._apply_task = asyncio.create_task(self._apply_update_to_tag(target_tag))
            _PROCESS_APPLY_TASK = self._apply_task
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
            "install_mode": detect_install_mode(),
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
            "install_mode": detect_install_mode(),
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

    def adopt_status_from(self, other: AutoUpdateService) -> None:
        """Carry the last check result across a hot-reload rebuild.

        A config save rebuilds every swappable component, including this
        service. Without this, a freshly-fetched ``update_available`` /
        ``up_to_date`` status would drop back to "尚未检查更新" on the settings
        page until the next scheduled check (up to ``check_interval_hours``).
        Only settled status is adopted — a transient ``checking`` / ``applying``
        state is left to re-derive so an in-flight apply on the previous
        instance is not misrepresented by the fresh one.
        """
        self._last_check_at = other._last_check_at
        self._latest_remote_version = other._latest_remote_version
        self._latest_tag = other._latest_tag
        if other._state in {"", "checking", "applying"}:
            return
        self._state = other._state
        self._reason = other._reason
        self._update_error = other._update_error

    def _background_loop_enabled(self) -> bool:
        """The loop runs when auto-update is on — or always on frozen/docker.

        Frozen desktop installs and docker containers run a check-only loop
        (the toggle governs auto-apply, which neither can ever do) so users
        get a "new installer / image available" reminder without opting in
        to anything.
        """
        return self.enabled or detect_install_mode() in ("frozen", "docker")

    async def run_forever(self) -> None:
        """Background loop: periodically check (and on git installs apply) updates."""
        if not self._background_loop_enabled():
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

    async def _fetch_latest_candidate(
        self,
        *,
        channel: str = "backend",
    ) -> _BackendTagSelection:
        """Query GitHub tags and select the newest allowed tag for *channel*.

        ``backend`` (default) tracks ``backend-v*`` source tags with legacy
        ``v*`` / bare-semver fallback; ``desktop`` tracks ``desktop-v*``
        installer tags only.
        """
        return await self._fetch_latest_candidate_once(channel=channel)

    async def _fetch_latest_candidate_once(
        self,
        *,
        channel: str,
    ) -> _BackendTagSelection:
        # GitHub is overseas for many users → honor [network].proxy while
        # keeping certificate verification mandatory.
        from openbiliclaw.network import outbound_httpx_kwargs

        async with httpx.AsyncClient(timeout=30, verify=True, **outbound_httpx_kwargs()) as client:
            tag_names: list[str] = []
            for page in range(1, _MAX_TAG_PAGES + 1):
                try:
                    resp = await client.get(
                        _GITHUB_TAGS,
                        headers={"Accept": "application/vnd.github.v3+json"},
                        params={"per_page": _TAGS_PER_PAGE, "page": page},
                    )
                except Exception as exc:
                    if _is_tls_verification_error(exc):
                        return _BackendTagSelection(
                            error_reason="tls_verification_failed",
                            error_detail=f"{_describe_exc(exc)};{_TLS_VERIFICATION_GUIDANCE}",
                        )
                    detail = _describe_exc(exc)
                    logger.warning("Auto-update tag check failed: %s", detail)
                    atom_selection = await self._fetch_latest_candidate_from_atom(
                        client,
                        channel=channel,
                    )
                    if not atom_selection.error_reason:
                        logger.info("Auto-update tag check recovered via GitHub tags Atom feed")
                        return atom_selection
                    return _BackendTagSelection(
                        error_reason="github_unreachable",
                        error_detail=detail,
                    )
                if resp.status_code != 200:
                    reason = (
                        "github_rate_limited"
                        if _is_github_rate_limited_response(resp)
                        else "github_unreachable"
                    )
                    logger.warning(
                        "Auto-update tag check failed: HTTP %s (%s)",
                        resp.status_code,
                        reason,
                    )
                    if reason == "github_rate_limited":
                        atom_selection = await self._fetch_latest_candidate_from_atom(
                            client,
                            channel=channel,
                        )
                        if not atom_selection.error_reason:
                            logger.info("Auto-update tag check recovered via GitHub tags Atom feed")
                            return atom_selection
                    return _BackendTagSelection(error_reason=reason)
                try:
                    tags = resp.json()
                except Exception as exc:
                    detail = _describe_exc(exc)
                    logger.warning("Auto-update tag check returned invalid JSON: %s", detail)
                    return _BackendTagSelection(
                        error_reason="invalid_github_response",
                        error_detail=detail,
                    )
                if not tags:
                    break
                if not isinstance(tags, list):
                    logger.warning("Auto-update tag check failed: unexpected tags payload")
                    return _BackendTagSelection(
                        error_reason="invalid_github_response",
                        error_detail=f"unexpected tags payload: {type(tags).__name__}",
                    )
                for tag_payload in tags:
                    if not isinstance(tag_payload, Mapping):
                        continue
                    tag = _string_from_mapping_field(tag_payload, "name")
                    if tag:
                        tag_names.append(tag)
        return _select_latest_candidate_from_tag_names(
            tag_names,
            allow_prerelease=self.allow_prerelease,
            channel=channel,
        )

    async def _fetch_latest_candidate_from_atom(
        self,
        client: httpx.AsyncClient,
        *,
        channel: str,
    ) -> _BackendTagSelection:
        try:
            resp = await client.get(
                _GITHUB_TAGS_ATOM,
                headers={"Accept": "application/atom+xml"},
            )
        except Exception as exc:
            detail = _describe_exc(exc)
            logger.warning("Auto-update Atom tag fallback failed: %s", detail)
            return _BackendTagSelection(
                error_reason="github_unreachable",
                error_detail=detail,
            )
        if resp.status_code != 200:
            logger.warning("Auto-update Atom tag fallback failed: HTTP %s", resp.status_code)
            return _BackendTagSelection(error_reason="github_unreachable")
        tag_names = _tag_names_from_atom(resp.text)
        if not tag_names:
            logger.warning("Auto-update Atom tag fallback failed: unexpected tags payload")
            return _BackendTagSelection(
                error_reason="invalid_github_response",
                error_detail="unexpected Atom tags payload",
            )
        return _select_latest_candidate_from_tag_names(
            tag_names,
            allow_prerelease=self.allow_prerelease,
            channel=channel,
        )

    async def _fetch_latest_version(self) -> str:
        """Query GitHub tags for the newest backend version tag."""
        return (await self._fetch_latest_candidate()).tag

    async def _check_apply_guards(self, tag: str) -> str:
        """Return a stable reason when local state makes apply unsafe."""
        self._guard_detail = ""
        # A PyInstaller desktop bundle reports ``frozen`` even when it shares a
        # data root with a co-located git checkout (entry.py points
        # OPENBILICLAW_PROJECT_ROOT at ~/OpenBiliClaw, the same dir an AI /
        # one-line install uses). Fast-forwarding that checkout would mutate
        # someone else's source + venv while the packaged binary keeps running
        # its bundled old code on restart — an endless update loop. Refuse
        # structurally, regardless of the on-disk ``.git``.
        mode = detect_install_mode()
        if mode != "git":
            logger.warning(
                "Auto-update apply refused: install mode %r cannot self-update "
                "(frozen bundles ship a new installer, docker ships a new image)",
                mode,
            )
            return "docker_install_mode" if mode == "docker" else "unsupported_install_mode"
        root = _project_root()
        if not (root / ".git").exists():
            inside = await self._run_git(["rev-parse", "--is-inside-work-tree"], root)
            if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
                logger.warning("Auto-update apply refused: %s is not a git work tree", root)
                return "unsupported_install_mode"

        configured = await self._run_git(["config", "--get", "remote.origin.url"], root)
        effective = await self._run_git(["ls-remote", "--get-url", "origin"], root)
        all_effective = await self._run_git(["remote", "get-url", "--all", "origin"], root)
        effective_urls = effective.stdout.splitlines() if effective.returncode == 0 else []
        all_effective_urls = (
            all_effective.stdout.splitlines() if all_effective.returncode == 0 else []
        )
        remote_urls = (
            [url.strip() for url in [*effective_urls, *all_effective_urls] if url.strip()]
            if effective_urls and all_effective_urls
            else []
        )
        if not effective_urls and not all_effective_urls:
            # Older injected runners (and old git wrappers) may acknowledge the
            # effective-URL probes without returning output. Preserve their
            # readable single-URL result, but never use it when either effective
            # probe returned a real value or failed: the allowlist must govern
            # what git will actually use.
            configured_urls = configured.stdout.splitlines() if configured.returncode == 0 else []
            if effective.returncode == 0 and all_effective.returncode == 0:
                remote_urls = [url.strip() for url in configured_urls if url.strip()]
        if not remote_urls or effective.returncode != 0 or all_effective.returncode != 0:
            return await self._refuse_unreadable_origin(root, configured)

        allowed = {_canonicalize_remote_url(item) for item in self.allowed_remotes}
        for remote_url in remote_urls:
            redacted = _redact_remote_url(remote_url)
            if _remote_has_credentials(remote_url):
                logger.warning("Auto-update apply refused: remote URL embeds credentials")
                self._guard_detail = (
                    f"origin 远端内嵌了凭证({redacted});"
                    f"改用不带 token 的地址:git remote set-url origin "
                    f"{DEFAULT_ALLOWED_REMOTES[0]}"
                )
                return "untrusted_remote"
            if _canonicalize_remote_url(remote_url) not in allowed:
                logger.warning(
                    "Auto-update apply refused: remote %r is not in the allowlist %s "
                    "(compared after canonicalization; add it to [scheduler] "
                    "auto_update_allowed_remotes or run: git remote set-url origin %s)",
                    remote_url,
                    list(self.allowed_remotes),
                    DEFAULT_ALLOWED_REMOTES[0],
                )
                self._guard_detail = (
                    f"origin 远端 {redacted} 不在允许列表;"
                    f"指回官方:git remote set-url origin {DEFAULT_ALLOWED_REMOTES[0]}"
                    f",或把该地址加入 config.toml 的 [scheduler] "
                    "auto_update_allowed_remotes"
                )
                return "untrusted_remote"

        status = await self._run_git(["status", "--porcelain"], root)
        if status.returncode != 0:
            logger.warning(
                "Auto-update apply refused: git status failed in %s: %s",
                root,
                status.stderr.strip(),
            )
            return "unsupported_install_mode"
        dirty = _dirty_paths_besides_uv_lock(status.stdout)
        for path in _staged_paths_besides_uv_lock(status.stdout):
            if path not in dirty:
                dirty.append(path)
        if dirty:
            logger.warning(
                "Auto-update apply refused: worktree has uncommitted changes: %s",
                ", ".join(dirty[:10]) + (" …" if len(dirty) > 10 else ""),
            )
            return "dirty_worktree"

        git_dir = await self._run_git(["rev-parse", "--git-dir"], root)
        if git_dir.returncode != 0:
            logger.warning(
                "Auto-update apply refused: git rev-parse --git-dir failed in %s: %s",
                root,
                git_dir.stderr.strip(),
            )
            return "unsupported_install_mode"
        if _merge_or_rebase_in_progress(root, git_dir.stdout.strip()):
            logger.warning("Auto-update apply refused: merge or rebase in progress in %s", root)
            return "merge_or_rebase_in_progress"

        fetch = await self._run_git(["fetch", "--force", "--tags", "origin"], root, timeout=120)
        if fetch.returncode != 0:
            logger.warning(
                "Auto-update apply refused: git fetch failed: %s",
                fetch.stderr.strip(),
            )
            return "github_unreachable"

        target = await self._run_git(["rev-parse", "--verify", f"{tag}^{{commit}}"], root)
        if target.returncode != 0:
            logger.warning("Auto-update apply refused: target tag %r not found after fetch", tag)
            return "missing_target_tag"

        ff = await self._run_git(["merge-base", "--is-ancestor", "HEAD", tag], root)
        if ff.returncode != 0:
            logger.warning(
                "Auto-update apply refused: HEAD is not an ancestor of %r "
                "(local branch diverged from the release line)",
                tag,
            )
            return "branch_not_fast_forwardable"
        return ""

    async def _refuse_unreadable_origin(
        self,
        root: Path,
        config_result: subprocess.CompletedProcess[str],
    ) -> str:
        """Classify an empty ``remote.origin.url`` read and emit the right fix.

        ``git config --get remote.origin.url`` comes back empty for several very
        different reasons, and the old blanket "运行:git remote add origin"
        advice was actively wrong for most of them: an install whose origin
        already exists — just url-less, holding multiple urls, or blocked by
        Windows *dubious ownership* — hits "error: remote origin already exists"
        when it follows that hint. That is the exact contradiction users report:
        the status card says 未配置 origin 远端, yet ``git remote add origin``
        answers "already exists". Probe ``git remote get-url origin`` to tell the
        cases apart and write the remediation that actually applies. Always
        reported as ``origin_remote_unusable`` — distinct from
        ``untrusted_remote``, which is a *readable* remote rejected by the
        allowlist / credential check.
        """
        fix_url = DEFAULT_ALLOWED_REMOTES[0]
        root_arg = _quote_command_path(root)
        probe = await self._run_git(["remote", "get-url", "origin"], root)
        config_err = config_result.stderr.strip()
        probe_err = probe.stderr.strip()

        if "dubious ownership" in f"{config_err}\n{probe_err}".lower():
            # Windows secondary drives / service accounts: the repo dir is owned
            # by a different account than the backend process, so git refuses to
            # open it at all. The origin remote itself is fine — safe.directory
            # is the fix, and it must run as whatever account launches the
            # backend (the user's own shell reads the repo without complaint,
            # which is why manual `git remote add` there says "already exists").
            self._guard_detail = (
                f"git 拒绝读取仓库({root}):检测到 dubious ownership"
                f"(仓库属主与运行后端的账户不一致,Windows 副盘/服务账户常见);"
                f"以运行后端的账户执行:git config --global --add safe.directory {root_arg}"
            )
        elif probe.returncode != 0 and "no such remote" in probe_err.lower():
            # Genuinely no origin remote — the only case where "remote add" fits.
            self._guard_detail = (
                f"未配置 origin 远端({root});运行:git -C {root_arg} remote add origin {fix_url}"
            )
        elif probe.returncode != 0 and (config_err or probe_err):
            # Any other git failure reading the repo (lock, corrupt config, git
            # missing on the backend's PATH, …): surface the real error instead
            # of a misleading "remote add" hint the user can't act on.
            git_err = (config_err or probe_err).splitlines()[0].strip()
            self._guard_detail = (
                f"git 无法读取 origin 远端({root}):{git_err};请查看后端日志的完整 git 报错后修复"
            )
        else:
            # origin exists but its url is blank / can't be resolved (the
            # "already exists" trap): set-url overwrites it, remote add is
            # refused. This is what reproduces the reported card contradiction.
            self._guard_detail = (
                f"origin 远端已存在但地址无法解析({root});"
                f"运行:git -C {root_arg} remote set-url origin {fix_url}"
            )

        logger.warning(
            "Auto-update apply refused: remote.origin.url unreadable in %s "
            "(config rc=%s stderr=%s; get-url rc=%s stderr=%s)",
            root,
            config_result.returncode,
            config_err or "<empty>",
            probe.returncode,
            probe_err or "<empty>",
        )
        return "origin_remote_unusable"

    async def _apply_update_to_tag(self, tag: str) -> None:
        """Fast-forward to *tag*, reinstall dependencies, and restart."""
        root = _project_root()
        try:
            # Drop the local uv.lock rewrite (stale-lock releases make uv sync
            # dirty it on install) so --ff-only is not refused for "local
            # changes would be overwritten"; the post-merge sync regenerates it.
            checkout = await self._run_git(["checkout", "--", "uv.lock"], root)
            if checkout.returncode != 0:
                await self._mark_apply_failed(
                    "worktree_prepare_failed",
                    detail=_process_failure_detail("git checkout uv.lock failed", checkout),
                )
                return

            merge = await self._run_git(["merge", "--ff-only", tag], root, timeout=120)
            if merge.returncode != 0:
                detail = _process_failure_detail("git merge --ff-only failed", merge)
                reason = "git_worktree_locked" if "index.lock" in detail.lower() else "merge_failed"
                await self._mark_apply_failed(reason, detail=detail)
                return

            install_cmd = self._detect_install_command(root)
            logger.info("Auto-update syncing dependencies with: %s", install_cmd)
            try:
                install = await self._run_command(install_cmd, root, timeout=300)
            except FileNotFoundError as exc:
                missing = Path(str(exc.filename or "dependency tool")).name
                logger.exception("Auto-update dependency tool is unavailable: %s", missing)
                await self._mark_apply_failed(
                    "dependency_sync_failed",
                    detail=f"依赖同步工具不可用({missing});请重新运行安装器修复运行环境",
                )
                return
            except subprocess.TimeoutExpired:
                logger.exception("Auto-update dependency sync timed out after 300 seconds")
                await self._mark_apply_failed(
                    "dependency_sync_failed",
                    detail=(
                        f"依赖同步超时({self._install_tool_name(install_cmd)},300 秒);"
                        "请检查网络后重试"
                    ),
                )
                return
            if install.returncode != 0:
                output = install.stderr.strip() or install.stdout.strip() or "<no output>"
                logger.error(
                    "Auto-update dependency sync failed (exit=%s, command=%s): %s",
                    install.returncode,
                    install_cmd,
                    output,
                )
                await self._mark_apply_failed(
                    "dependency_sync_failed",
                    detail=(
                        f"依赖同步失败({self._install_tool_name(install_cmd)},"
                        f"退出码 {install.returncode});请查看后端日志中的完整错误"
                    ),
                )
                return

            from openbiliclaw.sponsored_runtime_state import current_sponsored_settings

            if (
                current_sponsored_settings().enabled
                and not current_sponsored_settings().runtime_path
            ):
                reconcile = await self._run_command(
                    [sys.executable, "-m", "openbiliclaw.runtime.sponsored_bundle"],
                    root,
                    timeout=300,
                )
                if reconcile.returncode != 0:
                    await self._mark_apply_failed(
                        "sponsored_runtime_update_failed",
                        detail="免费模型组件更新失败，原组件已保留；请重试更新",
                    )
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
                detail = _describe_exc(exc)
                logger.error("Auto-update restart failed: %s", detail)
                await self._mark_apply_failed("restart_failed", detail=detail)
        except FileNotFoundError as exc:
            detail = _describe_exc(exc)
            logger.exception("Auto-update git executable is unavailable: %s", detail)
            await self._mark_apply_failed("git_unavailable", detail=detail)
        except subprocess.TimeoutExpired as exc:
            detail = _describe_exc(exc)
            logger.exception("Auto-update git command timed out: %s", detail)
            await self._mark_apply_failed("git_command_timeout", detail=detail)
        except Exception as exc:
            detail = _describe_exc(exc)
            logger.exception("Auto-update apply failed: %s", detail)
            await self._mark_apply_failed("apply_failed", detail=detail)

    async def _mark_apply_failed(self, reason: str, *, detail: str = "") -> None:
        self._state = "error"
        self._reason = reason
        self._update_error = detail or reason
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
        proxy = _custom_subprocess_proxy()
        command = ["git", *args]
        if proxy:
            command[1:1] = ["-c", f"http.proxy={proxy}"]
        return await self._run_command(command, root, timeout=timeout)

    async def _run_command(
        self,
        command: Sequence[str],
        root: Path,
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        args = list(command)
        proxy = _custom_subprocess_proxy()
        if proxy and _is_dependency_command(args):
            env = os.environ.copy()
            env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy})
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                **no_window_kwargs(),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **no_window_kwargs(),
            )
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            stdout_data, stderr_data = await proc.communicate()
            raise subprocess.TimeoutExpired(
                args,
                timeout,
                output=_decode_process_output(stdout_data),
                stderr=_decode_process_output(stderr_data),
            ) from exc
        returncode = proc.returncode if proc.returncode is not None else -1
        return subprocess.CompletedProcess(
            args,
            returncode,
            _decode_process_output(stdout_data),
            _decode_process_output(stderr_data),
        )

    @staticmethod
    def _detect_install_command(root: Path) -> list[str]:
        """Choose a dependency-only sync command available to this daemon.

        A repository always contains ``uv.lock``, including installs created by
        the supported pip/venv fallback.  Treating the lockfile itself as proof
        that the daemon can execute ``uv`` made those installs fast-forward the
        source and then fail with ``FileNotFoundError('uv')``.  Probe PATH
        explicitly and otherwise use the pip belonging to the running Python.

        Neither command reinstalls the editable OpenBiliClaw project.  The
        running console entry point can be locked on Windows, while the source
        checkout is already live through the editable install and will be
        re-imported after restart.  ``--inexact`` keeps that editable project
        (and any user-selected extras) while uv synchronizes runtime deps.
        """
        if (root / "uv.lock").exists() and shutil.which("uv") is not None:
            return ["uv", "sync", "--no-install-project", "--inexact"]

        dependencies = AutoUpdateService._read_runtime_dependencies(root)
        if dependencies:
            return [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                *dependencies,
            ]

        # Defensive compatibility for an unusual legacy checkout whose
        # pyproject metadata cannot be read.  Normal releases always take one
        # of the dependency-only paths above.
        return [sys.executable, "-m", "pip", "install", "-e", "."]

    @staticmethod
    def _read_runtime_dependencies(root: Path) -> list[str]:
        """Read PEP 508 runtime requirements from ``pyproject.toml``."""
        try:
            with (root / "pyproject.toml").open("rb") as file:
                payload = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Auto-update could not read pyproject dependencies: %s", exc)
            return []
        project = payload.get("project")
        if not isinstance(project, dict):
            return []
        dependencies = project.get("dependencies")
        if not isinstance(dependencies, list):
            return []
        return [item.strip() for item in dependencies if isinstance(item, str) and item.strip()]

    @staticmethod
    def _install_tool_name(command: Sequence[str]) -> str:
        if len(command) >= 3 and command[1:3] == ["-m", "pip"]:
            return "pip"
        return Path(command[0]).name if command else "unknown"

    @staticmethod
    def _restart_process() -> None:
        """Restart the git-install CLI through a cross-platform module entry."""
        argv = [sys.executable, "-m", "openbiliclaw.cli", *sys.argv[1:]]
        logger.info("Restarting process: %s", argv)
        os.execv(sys.executable, argv)
