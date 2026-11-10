"""Install and initialize an application-owned Ego host without global settings.

Only an explicit prepare action downloads/initializes. Status is read-only, and
ordinary work may restart an already initialized managed host without debugging.
External OPENBILICLAW_EGO_* hosts are never installed, stopped, or reconfigured.
"""

from __future__ import annotations

import asyncio
import atexit
import errno
import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import signal
import socket
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from openbiliclaw.proc import no_window_kwargs
from openbiliclaw.sources.ego_onboarding import EgoOnboardingError, complete_onboarding

logger = logging.getLogger(__name__)

_EXTERNAL_ENV = (
    "OPENBILICLAW_EGO_BROWSER",
    "OPENBILICLAW_EGO_SERVER_NAME",
    "OPENBILICLAW_EGO_PROFILE",
)
# Official installer URLs, not a third-party mirror. A downloaded app must also
# pass Apple signature/Gatekeeper checks for Citro Labs' exact signing identity.
_DOWNLOAD = "https://cdn.ego.app/setup/macos/{arch}/egolite-fHoqgZ74bOEM.dmg"
_MAX_DOWNLOAD = 512 * 1024 * 1024
_RETRY_DELAYS = (1.0, 3.0)
# Real cold-install evidence: a slow official download consumed about 16 minutes.
# Reserve initialization time separately so completed installation cannot starve
# the final native handshake; each phase still has a hard cancellation budget.
_INSTALL_TIMEOUT = 1200
_CONNECT_TIMEOUT = 300
# A real cold native host stayed alive and became ready after about 70s.
# Do not repeatedly kill that startup after 20s; the enclosing connection
# deadline still bounds retries and lock waiting together.
_HANDSHAKE_TIMEOUT = 120
_IDLE_STOP_SECONDS = 120.0
_IDLE_TASKS: dict[str, asyncio.Task[None]] = {}
_EXIT_REAPERS: set[str] = set()
_DETAILS = {
    "idle": "Prepare an isolated Ego Lite browser for OpenBiliClaw",
    "download": "Downloading the official Ego Lite application",
    "verify": "Verifying the official Ego Lite application signature",
    "install": "Installing Ego Lite in the OpenBiliClaw data directory",
    "onboarding": "Initializing an isolated Ego Lite profile without importing browser data",
    "restart": "Restarting the isolated Ego Lite host without a debugging port",
    "handshake": "Verifying the Ego Lite native connection",
    "ready": "The managed Ego Lite native connection is ready",
    "offline": "Ego Lite is prepared and will start when a website task needs it",
    "unsupported": "Managed Ego Lite setup currently requires macOS",
    "external": "An explicitly configured external Ego Lite host is managed separately",
}


class EgoSetupError(RuntimeError):
    """Safe diagnostic that never embeds process output or browser data."""


async def _command(*args: str, timeout: float = 30) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await process.communicate()
    except BaseException as exc:
        if isinstance(exc, TimeoutError):
            logger.warning("Ego setup command %s exceeded %.1fs", Path(args[0]).name, timeout)
        with suppress(ProcessLookupError):
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise EgoSetupError("ego_setup_command_failed")
    if len(stdout) + len(stderr) > 1024 * 1024:
        raise EgoSetupError("ego_setup_output_limit")
    return (stdout + stderr).decode("utf-8", errors="replace")


def _external() -> bool:
    return any(os.environ.get(name) for name in _EXTERNAL_ENV)


_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def _direct_hosts(value: Any) -> list[str]:
    """Validate exact DNS names, never Chromium bypass patterns or proxy URLs."""
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("ego_setup_invalid_direct_hosts")
    hosts = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("ego_setup_invalid_direct_hosts")
        host = raw.strip().lower().removesuffix(".")
        if len(host) > 253 or not re.fullmatch(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})+", host):
            raise ValueError("ego_setup_invalid_direct_hosts")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            hosts.add(host)
        else:
            raise ValueError("ego_setup_invalid_direct_hosts")
    return sorted(hosts)


def _idle_stop_seconds() -> float:
    raw = os.environ.get("OPENBILICLAW_EGO_IDLE_STOP_SECONDS", "")
    try:
        value = float(raw) if raw.strip() else _IDLE_STOP_SECONDS
    except ValueError:
        value = _IDLE_STOP_SECONDS
    return min(max(value, 0.0), 86400.0)


async def _retry_command(*args: str, timeout: float = 30) -> str:
    """Retry only timed-out, read-only or idempotent setup commands.

    Callers must not use this for mounting, browser actions or other operations
    whose outcome needs reconciliation. An actual OS refusal is never retried.
    """
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await _command(*args, timeout=timeout * (attempt + 1))
        except TimeoutError:
            if attempt == len(_RETRY_DELAYS):
                raise
            logger.info("Retrying timed-out Ego setup command %s", Path(args[0]).name)
            await asyncio.sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


class EgoSetupManager:
    """Own one signed app, profile, named host and setup lock per data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve() / "ego-managed"
        self.app = self.root / "ego lite.app"
        self.profile_dir = self.root / "profile"
        self.server_name = "obc-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:24]
        self.executable = self.app / (
            "Contents/Frameworks/ego Framework.framework/Versions/Current/Helpers/ego-browser"
        )
        self._state_path = self.root / "setup.json"

    def network_settings(self) -> dict[str, Any]:
        """Read explicit per-host routes without launching or probing the browser."""
        path = self.root / "network.json"
        if self.root.is_symlink() or path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_network_routes")
        if not path.exists():
            return {"direct_hosts": []}
        try:
            if path.stat().st_size > 32768:
                raise ValueError
            value = json.loads(path.read_text())
            if value.get("version") != 1:
                raise ValueError
            return {"direct_hosts": _direct_hosts(value["direct_hosts"])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EgoSetupError("ego_setup_invalid_network_routes") from exc

    async def configure_direct_hosts(self, hosts: list[str]) -> dict[str, Any]:
        """Persist an explicit choice after safely stopping only an idle managed host."""
        normalized = _direct_hosts(hosts)
        if _external() or sys.platform != "darwin":
            raise EgoSetupError("ego_setup_network_routes_unmanaged")
        if self.root.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        import fcntl

        lock_path = self.root / "setup.lock"
        if lock_path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        with lock_path.open("a") as lock:
            lock_path.chmod(0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EgoSetupError("ego_setup_busy") from exc
            lease = None
            try:
                lease = self.try_exclusive_session_lease()
                if lease is None:
                    raise EgoSetupError("ego_setup_sessions_busy")
                if self._pid():
                    await self._stop()
                path = self.root / "network.json"
                if path.is_symlink():
                    raise EgoSetupError("ego_setup_invalid_network_routes")
                with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False) as out:
                    temporary = Path(out.name)
                    try:
                        json.dump({"version": 1, "direct_hosts": normalized}, out)
                        out.flush()
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
            finally:
                if lease is not None:
                    self.release_session_lease(lease)
                fcntl.flock(lock, fcntl.LOCK_UN)
        return {"direct_hosts": normalized}

    async def _proxy_arguments(self) -> list[str]:
        hosts = self.network_settings()["direct_hosts"]
        if not hosts:
            return []
        # A bypass flag alone is ignored with macOS system discovery. Snapshot
        # the same manual system rules on each cold start, then add exact hosts.
        # PAC/WPAD cannot be safely flattened; never silently make them direct.
        raw = await _command("/usr/sbin/scutil", "--proxy", timeout=3)
        fields = dict(re.findall(r"^\s*([A-Za-z]\w*)\s*:\s*(.*?)\s*$", raw, re.M))
        if any(
            fields.get(key, "0") != "0"
            for key in ("ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable")
        ):
            raise EgoSetupError("ego_setup_proxy_config_unsupported")
        rules = []
        for prefix, scheme, transport in (
            ("HTTP", "http", "http"),
            ("HTTPS", "https", "http"),
            ("SOCKS", "socks", "socks5"),
        ):
            enabled = fields.get(prefix + "Enable", "0")
            if enabled == "0":
                continue
            host, port = fields.get(prefix + "Proxy", ""), fields.get(prefix + "Port", "")
            try:
                if enabled != "1" or not port.isdigit() or not 1 <= int(port) <= 65535:
                    raise ValueError
                try:
                    address = ipaddress.ip_address(host)
                    host = f"[{address}]" if address.version == 6 else str(address)
                except ValueError:
                    if not re.fullmatch(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})*", host.lower()):
                        raise ValueError from None
                rules.append(f"{scheme}={transport}://{host}:{port}")
            except ValueError as exc:
                raise EgoSetupError("ego_setup_proxy_config_unsupported") from exc
        if not rules:
            return []  # The system is already direct for every site.
        exceptions = re.findall(r"^\s*\d+\s*:\s*(.*?)\s*$", raw, re.M)
        if len(exceptions) > 128 or any(
            len(rule) > 253 or not re.fullmatch(r"[A-Za-z0-9*._:/\[\]-]+|<local>|<-loopback>", rule)
            for rule in exceptions
        ):
            raise EgoSetupError("ego_setup_proxy_config_unsupported")
        if fields.get("ExcludeSimpleHostnames") == "1":
            exceptions.append("<local>")
        bypass = list(dict.fromkeys([*exceptions, *hosts]))
        return ["--proxy-server=" + ";".join(rules), "--proxy-bypass-list=" + ";".join(bypass)]

    @property
    def session_lock_path(self) -> Path:
        """Cross-process lease file: shared while any session is live."""
        return self.root / "sessions.lock"

    def _open_session_lock(self) -> BinaryIO:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        path = self.session_lock_path
        if path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        handle = path.open("a+b")
        path.chmod(0o600)
        return handle

    def acquire_session_lease(self) -> BinaryIO:
        """Hold one shared lease for the lifetime of a workflow session.

        The lease is advisory and cross-process: an idle reaper may stop the
        managed host only after every session process has released it.
        """
        import fcntl

        handle = self._open_session_lock()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        except BaseException:
            handle.close()
            raise
        return handle

    def try_exclusive_session_lease(self) -> BinaryIO | None:
        """Return an exclusive lease only when no live session holds one."""
        import fcntl

        handle = self._open_session_lock()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        except BaseException:
            handle.close()
            raise
        return handle

    @staticmethod
    def release_session_lease(handle: BinaryIO) -> None:
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _state(self) -> dict[str, Any]:
        if self.root.is_symlink() or self._state_path.is_symlink():
            return {}
        try:
            if self._state_path.stat().st_size > 16384:
                return {}
            value = json.loads(self._state_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, **changes: Any) -> None:
        state = {**self._state(), **changes, "updated_at": time.time()}
        path = self._state_path.with_suffix(".tmp")
        if path.is_symlink() or self._state_path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        path.replace(self._state_path)

    def _pid(self) -> int | None:
        # Inspect only our own recorded PID and exact managed command identity.
        from subprocess import TimeoutExpired, run

        pid = self._state().get("pid")
        if not isinstance(pid, int) or pid <= 1 or sys.platform != "darwin":
            return None
        try:
            text = run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                **no_window_kwargs(),
            ).stdout.strip()
        except (OSError, TimeoutExpired):
            return None
        binary = str(self.app / "Contents/MacOS/ego lite")
        if not text.startswith(binary + " "):
            return None
        if f"--ego-server-name={self.server_name}" not in text.split():
            return None
        # The path can contain spaces; require the complete value followed by
        # the next known flag boundary rather than splitting it on whitespace.
        if f"--user-data-dir={self.profile_dir} --" not in text:
            return None
        return pid

    def connection(self) -> dict[str, str]:
        """Return a stable bundled CLI connection, without starting any process."""
        if _external():
            return {}
        return {
            "executable": str(self.executable),
            "server_name": self.server_name,
            "profile": "Default",
        }

    def status(self) -> dict[str, Any]:
        """Read installation and prior handshake facts; never launch/probe the CLI."""
        supported = sys.platform == "darwin"
        external = _external()
        state = self._state() if not external else {}
        installed = self.executable.is_file() if not external else False
        pid = self._pid() if installed and supported and not external else None
        available = bool(pid and state.get("ready_pid") == pid and state.get("initialized"))
        setup_state = str(state.get("setup_state", "idle"))
        if not supported:
            setup_state = "unsupported"
        phase = str(state.get("phase", "idle"))
        detail = (
            _DETAILS["external"]
            if external
            else _DETAILS["unsupported"]
            if not supported
            else (str(state.get("detail", _DETAILS["idle"])))
        )
        if setup_state == "ready":
            detail = _DETAILS["ready" if available else "offline"]
        if supported and not external and setup_state == "preparing":
            # A killed API worker must not leave a permanent progress spinner.
            # Opening an existing lock read-only does not start or initialize anything.
            import fcntl

            lock_path = self.root / "setup.lock"
            try:
                if lock_path.is_symlink():
                    raise OSError
                with lock_path.open("r") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        setup_state, detail = "failed", "ego_setup_interrupted"
            except OSError:
                setup_state, detail = "failed", "ego_setup_interrupted"
        return {
            "managed": not external,
            "setup_state": setup_state,
            "phase": phase,
            "detail": detail,
            "installed": installed,
            "available": available,
            "platform_supported": supported,
            "connection_state": "ready" if available else "unverified" if pid else "offline",
        }

    def _phase(self, phase: str) -> None:
        self._write(setup_state="preparing", phase=phase, detail=_DETAILS[phase])

    def _check_bundle_links(self, app: Path) -> None:
        # Never let recursive metadata normalization follow an installer symlink
        # into another app or any user-owned file outside this staged bundle.
        root = app.resolve()
        for path in app.rglob("*"):
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                raise EgoSetupError("ego_setup_unsafe_bundle_link")

    async def _verify(self, app: Path) -> None:
        try:
            await _retry_command("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
        except EgoSetupError as exc:
            raise EgoSetupError("ego_setup_signature_invalid") from exc
        identity = await _retry_command("/usr/bin/codesign", "-dv", "--verbose=4", str(app))
        if not re.search(r"(?m)^TeamIdentifier=JGQLC6YQYJ$", identity) or not re.search(
            r"(?m)^Identifier=com\.citrolabs\.ego\.lite$", identity
        ):
            raise EgoSetupError("ego_setup_signature_mismatch")
        # Never strip quarantine or override an actual OS trust decision.
        try:
            await _retry_command(
                "/usr/sbin/spctl", "--assess", "--type", "execute", str(app), timeout=90
            )
        except EgoSetupError as exc:
            raise EgoSetupError("ego_setup_system_trust_required") from exc

    async def _download(self, path: Path) -> None:
        arch = {"arm64": "arm64", "x86_64": "x64"}.get(platform.machine())
        if not arch:
            raise EgoSetupError("ego_setup_unsupported_architecture")
        async with asyncio.timeout(900):
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                url = _DOWNLOAD.format(arch=arch)
                headers = {"Accept-Encoding": "identity"}
                metadata = await client.head(url, headers=headers)
                if metadata.status_code not in {405, 501}:
                    metadata.raise_for_status()
                    length = int(metadata.headers.get("content-length", "0"))
                    if length > _MAX_DOWNLOAD:
                        raise EgoSetupError("ego_setup_download_too_large")
                    etag = metadata.headers.get("etag", "")
                    if (
                        length >= 4 * 1024 * 1024
                        and metadata.headers.get("accept-ranges") == "bytes"
                        and etag.startswith('"')
                        and etag.endswith('"')
                    ):
                        try:
                            await self._download_ranges(client, url, path, length, etag)
                            return
                        except EgoSetupError as exc:
                            if str(exc) != "ego_setup_ranges_unsupported":
                                raise
                            path.unlink(missing_ok=True)
                # No redirects to arbitrary hosts and no external installer script execution.
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    size = 0
                    with path.open("xb") as target:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > _MAX_DOWNLOAD:
                                raise EgoSetupError("ego_setup_download_too_large")
                            target.write(chunk)

    async def _download_ranges(
        self, client: httpx.AsyncClient, url: str, path: Path, length: int, etag: str
    ) -> None:
        """Fetch four disjoint ranges of one immutable official image.

        Each stream has its own file descriptor. A failed stream cancels and
        joins the others before the caller can discard any partial file.
        """
        with path.open("xb") as target:
            target.truncate(length)

        async def transfer(start: int, end: int) -> None:
            headers = {
                "Range": f"bytes={start}-{end}",
                "If-Match": etag,
                "Accept-Encoding": "identity",
            }
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                if response.status_code == 200:
                    raise EgoSetupError("ego_setup_ranges_unsupported")
                if (
                    response.status_code != 206
                    or response.headers.get("content-range") != f"bytes {start}-{end}/{length}"
                    or response.headers.get("etag") != etag
                ):
                    raise EgoSetupError("ego_setup_invalid_download_range")
                received = 0
                with path.open("r+b") as target:
                    target.seek(start)
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > end - start + 1:
                            raise EgoSetupError("ego_setup_invalid_download_range")
                        target.write(chunk)
                if received != end - start + 1:
                    raise EgoSetupError("ego_setup_incomplete_download_range")

        tasks = [
            asyncio.create_task(transfer(length * i // 4, length * (i + 1) // 4 - 1))
            for i in range(4)
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _install(self) -> None:
        if self.app.exists():
            self._phase("verify")
            await self._verify(self.app)
            if not self.executable.is_file():
                raise EgoSetupError("ego_setup_cli_missing")
            return
        with tempfile.TemporaryDirectory(prefix="install-", dir=self.root) as directory:
            temporary = Path(directory)
            temporary.chmod(0o700)
            dmg = await self._cached_installer(temporary)
            mount = temporary / "mount"
            mount.mkdir()
            self._phase("install")
            attached = False
            try:
                try:
                    await _command(
                        "/usr/bin/hdiutil",
                        "attach",
                        str(dmg),
                        "-nobrowse",
                        "-readonly",
                        "-mountpoint",
                        str(mount),
                        timeout=90,
                    )
                except EgoSetupError as exc:
                    # hdiutil's nonzero status can be transient; do not guess
                    # that the cached image is corrupt or silently redownload.
                    raise EgoSetupError("ego_setup_mount_failed") from exc
                attached = True
                source = mount / "ego lite.app"
                if source.is_symlink() or not source.is_dir():
                    raise EgoSetupError("ego_setup_bundle_missing")
                self._phase("install")
                staged = temporary / "ego lite.app"
                await _retry_command("/usr/bin/ditto", str(source), str(staged), timeout=120)
                self._check_bundle_links(staged)
                # Official 0.6.0.16 DMGs contain Finder metadata which strict
                # codesign rejects. Apple QA1940 documents this condition. Remove
                # only these non-code attributes from our new staged copy; preserve
                # quarantine/provenance and still require full strict verification.
                # https://developer.apple.com/library/archive/qa/qa1940/_index.html
                for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
                    await _retry_command("/usr/bin/xattr", "-dr", attribute, str(staged))
                self._phase("verify")
                await self._verify(staged)
                if not (staged / self.executable.relative_to(self.app)).is_file():
                    raise EgoSetupError("ego_setup_cli_missing")
                staged.rename(self.app)
            except EgoSetupError as exc:
                # Only our regular cache is disposable. The next explicit
                # prepare downloads again; trust refusals, exhausted timeout
                # retries and cancellation keep the image.
                if (
                    str(exc)
                    in {
                        "ego_setup_bundle_missing",
                        "ego_setup_unsafe_bundle_link",
                        "ego_setup_signature_invalid",
                        "ego_setup_signature_mismatch",
                        "ego_setup_cli_missing",
                    }
                    and dmg == self.root / "installer.dmg"
                    and not dmg.is_symlink()
                    and dmg.is_file()
                ):
                    dmg.unlink()
                raise
            finally:
                if attached or mount.is_mount():
                    await _command("/usr/bin/hdiutil", "detach", str(mount), timeout=30)

    async def _cached_installer(self, temporary: Path) -> Path:
        """Cache complete official downloads atomically; never cache a partial body.

        A cache hit only skips downloading. Every installation still mounts the
        image read-only and strictly verifies the staged app and Apple trust.
        """
        cache = self.root / "installer.dmg"
        if cache.is_symlink() or (cache.exists() and not cache.is_file()):
            raise EgoSetupError("ego_setup_invalid_installer_cache")
        if not cache.exists():
            self._phase("download")
            downloaded = temporary / "installer.dmg"
            for attempt in range(len(_RETRY_DELAYS) + 1):
                try:
                    await self._download(downloaded)
                    break
                except (httpx.HTTPError, TimeoutError) as exc:
                    retryable = isinstance(exc, (httpx.TransportError, TimeoutError)) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and (
                            exc.response.status_code in {408, 429}
                            or exc.response.status_code >= 500
                        )
                    )
                    if not retryable or attempt == len(_RETRY_DELAYS):
                        raise
                    # Only our uncommitted partial file is removed. A cached
                    # complete installer is never discarded for a timeout.
                    downloaded.unlink(missing_ok=True)
                    logger.info("Retrying interrupted official Ego download")
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
            if not downloaded.is_file() or not 0 < downloaded.stat().st_size <= _MAX_DOWNLOAD:
                raise EgoSetupError("ego_setup_invalid_download")
            downloaded.chmod(0o600)
            if cache.is_symlink():
                raise EgoSetupError("ego_setup_invalid_installer_cache")
            downloaded.replace(cache)
        if not 0 < cache.stat().st_size <= _MAX_DOWNLOAD:
            raise EgoSetupError("ego_setup_invalid_installer_cache")
        return cache

    async def _launch(self, *, debugging: bool) -> int:
        if self._pid():
            raise EgoSetupError("ego_setup_host_already_running")
        self.profile_dir.mkdir(mode=0o700, exist_ok=True)
        self.profile_dir.chmod(0o700)
        args = [
            str(self.app / "Contents/MacOS/ego lite"),
            f"--ego-server-name={self.server_name}",
            f"--user-data-dir={self.profile_dir}",
            "--headless=new",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
        ]
        args += await self._proxy_arguments()
        port = 0
        if debugging:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = int(listener.getsockname()[1])
            args += ["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"]
        # Use the signed Ego application's own headless mode for every managed
        # launch. Minimizing after createTab is too late to prevent a flash.
        # First-run UI and manual login remain operable through CDP/screenshots.
        args.append("about:blank" if debugging else "--no-startup-window")
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            **no_window_kwargs(),
        )
        self._write(pid=process.pid, ready_pid=None, debug_port=port)
        for _ in range(80):
            if process.returncode is not None:
                raise EgoSetupError("ego_setup_host_exited")
            if self._pid():
                if not debugging:
                    return 0
                try:
                    listeners = await _command(
                        "/usr/sbin/lsof",
                        "-nP",
                        "-a",
                        "-p",
                        str(process.pid),
                        f"-iTCP:{port}",
                        "-sTCP:LISTEN",
                        "-Fn",
                        timeout=3,
                    )
                    addresses = [
                        line[1:] for line in listeners.splitlines() if line.startswith("n")
                    ]
                    if addresses and all(value == f"127.0.0.1:{port}" for value in addresses):
                        return port
                    if addresses:
                        raise EgoSetupError("ego_setup_debug_not_loopback")
                except TimeoutError:
                    # A slow OS ownership probe is inconclusive, not a failed
                    # browser startup. Retry within the existing bounded loop;
                    # only a later verified loopback listener permits onboarding.
                    logger.info("Waiting for the Ego loopback listener ownership check")
                except EgoSetupError as exc:
                    if str(exc) != "ego_setup_command_failed":
                        raise
            await asyncio.sleep(0.25)
        raise EgoSetupError("ego_setup_host_timeout")

    async def _stop(self, *, exit_budget: float = 6.0) -> None:
        pid = self._pid()
        debug_port = self._state().get("debug_port", 0)
        has_debug_port = isinstance(debug_port, int) and bool(debug_port)
        if not pid and not has_debug_port:
            return
        if pid:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

        def settled() -> bool:
            if pid and self._pid() == pid:
                return False
            if not has_debug_port:
                return True
            with socket.socket() as probe:
                probe.settimeout(0.2)
                return probe.connect_ex(("127.0.0.1", debug_port)) == errno.ECONNREFUSED

        async def wait_settled(budget: float) -> bool:
            deadline = time.monotonic() + budget
            while not settled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                await asyncio.sleep(min(0.1, remaining))
            return True

        if await wait_settled(max(exit_budget, 0.0)):
            self._write(pid=None, ready_pid=None, debug_port=0)
            return
        # A graceful shutdown can hang behind a crashed renderer or a stuck
        # profile lock. Escalate only against the exact process identity that
        # was verified above, so a recycled PID is never killed.
        if pid and self._pid() == pid:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            if await wait_settled(3.0):
                self._write(pid=None, ready_pid=None, debug_port=0)
                return
        # Do not destroy a profile or kill an unverified/reused PID to recover.
        if pid and self._pid() == pid:
            raise EgoSetupError("ego_setup_host_did_not_exit")
        raise EgoSetupError("ego_setup_debug_port_still_open")

    def stop_sync(self, *, wait: float = 2.0) -> None:
        """Best-effort stop for interpreter shutdown; never touches external hosts."""
        if _external() or sys.platform != "darwin":
            return
        pid = self._pid()
        if not pid:
            return
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + max(wait, 0.0)
        while time.monotonic() < deadline:
            if self._pid() != pid:
                break
            time.sleep(0.1)
        if self._pid() == pid:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with suppress(Exception):
            self._write(pid=None, ready_pid=None, debug_port=0)

    async def _handshake(self) -> None:
        pid = self._pid()
        if not pid or self._state().get("debug_port"):
            raise EgoSetupError("ego_setup_host_not_ready")
        expression = (
            "const v=await globalThis.ego.getBrowserVersion();"
            "const p=await globalThis.ego.listProfiles();"
            "console.log('OBC_EGO_READY:'+JSON.stringify({"
            "version:v.currentVersion,profile:p.profiles.some(x=>x.id==='Default')}));"
        )
        output = await _command(
            str(self.executable),
            f"--ego-server-name={self.server_name}",
            "nodejs",
            "-e",
            expression,
            timeout=_HANDSHAKE_TIMEOUT,
        )
        records = [
            line.removeprefix("OBC_EGO_READY:")
            for line in output.splitlines()
            if line.startswith("OBC_EGO_READY:")
        ]
        if len(records) != 1:
            raise EgoSetupError("ego_setup_native_handshake_failed")
        try:
            record = json.loads(records[0])
            version = record["version"]
            if record.get("profile") is not True or not re.fullmatch(r"[0-9.]{3,40}", version):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise EgoSetupError("ego_setup_native_handshake_failed") from exc
        if self._pid() != pid:
            raise EgoSetupError("ego_setup_host_identity_changed")
        self._write(
            initialized=True,
            ready_pid=pid,
            version=version,
            setup_state="ready",
            phase="ready",
            detail=_DETAILS["ready"],
            debug_port=0,
        )

    async def _connect_host(self) -> None:
        """Resume known setup pages after a verified stop; never replay a lost click."""
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                if not self._state().get("initialized"):
                    self._phase("onboarding")
                    # Recover only our own incomplete setup, never an external host.
                    await self._stop()
                    port = await self._launch(debugging=True)
                    try:
                        try:
                            await complete_onboarding(port)
                        except EgoOnboardingError as exc:
                            # A crash after clicking Finish can leave a completed
                            # profile without our handshake marker. Its absent setup
                            # page is not success; verify via the no-debug CLI below.
                            if str(exc) != "ego_setup_page_unavailable":
                                raise
                    finally:
                        self._phase("restart")
                        await self._stop()
                self._phase("handshake")
                if not self._pid():
                    await self._launch(debugging=False)
                await self._handshake()
                return
            except (TimeoutError, EgoSetupError, EgoOnboardingError) as exc:
                retryable = isinstance(exc, TimeoutError) or str(exc) in {
                    "ego_setup_host_timeout",
                    "ego_setup_transition_timeout",
                    "ego_setup_action_timeout",
                }
                if not retryable or attempt == len(_RETRY_DELAYS):
                    raise
                # A fresh connection observes the current UI step. It does not
                # resend the previous action after an uncertain receipt.
                await self._stop()
                logger.info("Recovering a transient Ego initialization timeout")
                await asyncio.sleep(_RETRY_DELAYS[attempt])

    async def _operate(self, *, initialize: bool, wait_for_owner: bool = False) -> dict[str, Any]:
        if _external() or sys.platform != "darwin":
            return self.status()
        if self.root.is_symlink() or self.profile_dir.is_symlink() or self.app.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        if not initialize and not self._state().get("initialized"):
            return self.status()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        import fcntl

        lock_path = self.root / "setup.lock"
        if lock_path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        with lock_path.open("a") as lock:
            lock_path.chmod(0o600)
            wait_budget = _CONNECT_TIMEOUT
            if initialize and wait_for_owner:
                wait_budget += _INSTALL_TIMEOUT
            deadline = time.monotonic() + wait_budget
            waited = False
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    # Status-oriented preparation returns progress immediately.
                    # Authoring and actual work wait for the current owner.
                    # Cancel only this waiter, never the process owning setup.
                    if (initialize and not wait_for_owner) or time.monotonic() >= deadline:
                        return self.status()
                    waited = True
                    await asyncio.sleep(0.1)
            try:
                if not initialize or waited:
                    status = self.status()
                    if status["available"] or (waited and status["setup_state"] == "failed"):
                        return status
                if initialize:
                    async with asyncio.timeout(_INSTALL_TIMEOUT):
                        await self._install()
                connect_budget = (
                    _CONNECT_TIMEOUT if initialize else max(0.0, deadline - time.monotonic())
                )
                async with asyncio.timeout(connect_budget):
                    await self._connect_host()
            except asyncio.CancelledError:
                with suppress(Exception):
                    await self._stop()
                self._write(setup_state="failed", detail="ego_setup_cancelled")
                raise
            except Exception as exc:
                with suppress(Exception):
                    await self._stop()
                code = (
                    str(exc)
                    if isinstance(exc, (EgoSetupError, EgoOnboardingError))
                    else (
                        "ego_setup_timeout" if isinstance(exc, TimeoutError) else "ego_setup_failed"
                    )
                )
                self._write(setup_state="failed", detail=code)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return self.status()

    async def prepare(self, *, wait: bool = False) -> dict[str, Any]:
        """Install/initialize; authoring may wait for an existing setup owner's result.

        The default returns progress when busy. ``wait=True`` shares the result
        within the installation plus connection budget, without duplicating work
        or cancelling the owner when this caller is cancelled.
        """
        result = await self._operate(initialize=True, wait_for_owner=wait)
        if result.get("available") is True:
            self.schedule_idle_stop()
        return result

    def register_exit_cleanup(self) -> None:
        """Reclaim this managed host on interpreter exit when no session is live."""
        if _external() or sys.platform != "darwin":
            return
        key = str(self.root)
        if key in _EXIT_REAPERS:
            return
        _EXIT_REAPERS.add(key)

        def reap() -> None:
            try:
                lease = self.try_exclusive_session_lease()
            except Exception:
                return
            if lease is None:
                return
            try:
                self.stop_sync()
            except Exception:
                logger.info("Managed Ego host was not stopped at interpreter exit")
            finally:
                self.release_session_lease(lease)

        atexit.register(reap)

    def schedule_idle_stop(self, *, grace: float | None = None) -> None:
        """Reclaim a prepared or used host after all processes release session leases."""
        if _external() or sys.platform != "darwin":
            return
        self.register_exit_cleanup()
        key = str(self.root)
        delay = _idle_stop_seconds() if grace is None else max(grace, 0.0)
        previous = _IDLE_TASKS.pop(key, None)
        if previous is not None and not previous.done():
            previous.cancel()
        _IDLE_TASKS[key] = asyncio.create_task(self._stop_when_idle(key, delay))

    async def _stop_when_idle(self, key: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            lease = self.try_exclusive_session_lease()
            if lease is None:
                logger.info("Keeping the managed Ego host: a session is still active")
                return
            try:
                await self.stop()
                logger.info("Stopped the idle managed Ego host")
            finally:
                self.release_session_lease(lease)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Idle stop of the managed Ego host failed", exc_info=False)
        finally:
            if _IDLE_TASKS.get(key) is asyncio.current_task():
                _IDLE_TASKS.pop(key, None)

    async def ensure_running(self) -> dict[str, Any]:
        """Start an initialized host, waiting boundedly for another startup owner."""
        return await self._operate(initialize=False)

    async def stop(self, *, exit_budget: float = 6.0) -> dict[str, Any]:
        """Stop our managed host after work ends; external hosts stay untouched.

        Serialized with prepare/ensure_running through the setup lock, so an
        idle stop can never race a launch. Callers decide *when* stopping is
        safe (for example only while holding the exclusive session lease).
        """
        if _external() or sys.platform != "darwin":
            return self.status()
        if not self._state().get("initialized") and not self._pid():
            return self.status()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        import fcntl

        lock_path = self.root / "setup.lock"
        if lock_path.is_symlink():
            raise EgoSetupError("ego_setup_invalid_directory")
        with lock_path.open("a") as lock:
            lock_path.chmod(0o600)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("Ego host stop deferred: setup is busy")
                return self.status()
            try:
                await self._stop(exit_budget=exit_budget)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return self.status()
