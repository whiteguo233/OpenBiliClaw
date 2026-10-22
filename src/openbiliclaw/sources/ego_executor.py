"""Ego Lite's native Node/CDP transport and durable application-owned task spaces.

Origin/method and public-DNS checks occur at each paused request. Ego owns socket
resolution; unlike the legacy Chromium launcher this adapter does not DNS-pin.
No executable page or host program is accepted from workflow/model data.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openbiliclaw.proc import no_window_kwargs
from openbiliclaw.sources.browser_workflow import (
    BrowserWorkflowItem,
    BrowserWorkflowPlan,
    BrowserWorkflowResult,
    BrowserWorkflowValidationError,
    WorkflowParameters,
    compile_browser_workflow,
    workflow_origin,
    workflow_url,
)
from openbiliclaw.sources.ego_setup import EgoSetupManager
from openbiliclaw.sources.web_public import _is_global_address, _system_resolver

_LOCKS: dict[str, asyncio.Lock] = {}
_READY: dict[str, tuple[int, str]] = {}


def _error(code: str, detail: str = "") -> Exception:
    from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

    descriptions = {
        "ego_navigation_failed": "Ego Lite could not load the approved website",
        "ego_navigation_timeout": "Ego Lite navigation exceeded its time budget",
        "ego_protocol_timeout": "Ego Lite did not answer a browser command in time",
        "ego_protocol_error": "The installed Ego Lite did not accept a required browser command",
        "ego_dom_error": (
            "Ego Lite could not read this page; the workflow selectors need verification"
        ),
        "ego_connection_closed": "The Ego Lite native host connection closed",
        "ego_session_identity_changed": "The saved Ego space is missing or its ownership changed",
        "resource_budget_exceeded": "The website exceeded this run's network resource budget",
        "EGO_PROFILE_NOT_FOUND": "The configured Ego profile is unavailable",
        "EGO_TASK_SPACE_USER_IN_CONTROL": (
            "Return the owned Ego space to agent control before retrying"
        ),
        "login_required": "Website login needs user attention",
    }
    return BrowserWorkflowExecutionError(
        code, detail or descriptions.get(code, "Ego Lite could not complete the browser operation")
    )


def _host_pid(server_name: str) -> int | None:
    """Read only matching Ego app process metadata; never launch the application."""
    if sys.platform != "darwin":
        return None
    try:
        rows = subprocess.run(
            ["pgrep", "-f", r"/ego lite.app/Contents/MacOS/ego lite"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            **no_window_kwargs(),
        ).stdout.splitlines()
        for value in rows:
            if not value.isdigit():
                continue
            command = subprocess.run(
                ["ps", "-p", value, "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                **no_window_kwargs(),
            ).stdout
            match = re.search(r"(?:^|\s)--ego-server-name=([\w.-]+)(?:\s|$)", command)
            if (match.group(1) if match else "") == server_name:
                return int(value)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class EgoRequestPolicy:
    """Exact scopes and fresh public DNS preflight, without claiming socket pinning."""

    async def authorize(
        self, plan: BrowserWorkflowPlan, request: dict[str, Any], *, interactive: bool
    ) -> bool:
        try:
            url = workflow_url(str(request["url"]))
            origin = workflow_origin(url)
            origins = plan.allowed_origins
            if not request.get("document"):
                origins += plan.resource_origins
            if interactive:
                origins += plan.auth.login_origins
            if origin not in origins:
                return False
            method = str(request.get("method", ""))
            if method not in (
                {"GET", "HEAD", "POST", "OPTIONS"} if interactive else {"GET", "HEAD"}
            ):
                return False
            parts = urlsplit(url)
            async with asyncio.timeout(5):
                addresses = await _system_resolver(
                    str(parts.hostname), parts.port or (443 if parts.scheme == "https" else 80)
                )
            return (
                bool(addresses)
                and len(addresses) <= 32
                and all(_is_global_address(ip) for ip in addresses)
            )
        except (KeyError, OSError, TimeoutError, ValueError, BrowserWorkflowValidationError):
            return False


class EgoLiteWorkflowSession:
    """A native Ego task space lease; no personal tabs are selected or claimed."""

    def __init__(
        self,
        executor: EgoLiteWorkflowExecutor,
        plan: BrowserWorkflowPlan,
        *,
        interactive: bool,
        lock: asyncio.Lock,
        observation_only: bool = False,
    ) -> None:
        self.executor, self.plan, self.interactive, self.lock = executor, plan, interactive, lock
        self.observation_only = observation_only
        self._stopped_observation: dict[str, Any] | None = None
        self.pages_visited = 0
        self._closed = False
        self._serial = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._guards: set[asyncio.Task[None]] = set()
        self._process: asyncio.subprocess.Process | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    async def start(self, state_file: Path, state: dict[str, Any]) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="obc-ego-", dir="/tmp")
        directory = Path(self._temporary.name)
        directory.chmod(0o700)
        current_pid = (
            self.executor._setup._pid()
            if self.executor.managed
            else _host_pid(self.executor.server_name)
        )
        previous_pid = state.get("host_pid")
        config = {
            "socket": str(directory / "host.sock"),
            "ready": str(directory / "ready.json"),
            "plan": self.plan.to_dict(),
            "interactive": self.interactive,
            "recover_missing_task": bool(
                self.executor.managed
                and current_pid
                and isinstance(previous_pid, int)
                and current_pid != previous_pid
            ),
            **{key: state[key] for key in ("task_id", "task_name", "profile")},
        }
        module = Path(__file__).with_name("ego_host.mjs").as_uri()
        config_file = directory / "config.json"
        config_file.write_text(json.dumps(config))
        config_file.chmod(0o600)
        script = (
            f"const m=await import({json.dumps(module)});"
            f"await m.startFile({json.dumps(str(config_file))});"
        )
        command = [self.executor.executable]
        if self.executor.server_name:
            command.append(f"--ego-server-name={self.executor.server_name}")
        command += ["nodejs", "-e", script]
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        deadline = time.monotonic() + 15
        ready_file = directory / "ready.json"
        while not ready_file.exists():
            if self._process.returncode is not None:
                raise _error(
                    "ego_not_ready",
                    "Complete Ego Lite first-run setup without importing browser data",
                )
            if time.monotonic() >= deadline:
                raise _error(
                    "ego_connection_timeout",
                    "Ego Lite host did not establish its native connection",
                )
            await asyncio.sleep(0.05)
        ready = json.loads(ready_file.read_text())
        if ready.get("task_id") is not None:
            state.update(task_id=ready["task_id"], host_pid=current_pid)
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            temporary.replace(state_file)
        if ready.get("error"):
            raise _error(str(ready["error"]))
        pid = _host_pid(self.executor.server_name)
        if pid:
            _READY[self.executor.host_key] = (pid, str(ready.get("version", "")))
        reader, self._writer = await asyncio.open_unix_connection(
            config["socket"], limit=8 * 1024 * 1024
        )
        self._reader_task = asyncio.create_task(self._read(reader))
        await self._open_entry()

    async def _open_entry(self) -> None:
        """Preserve denied-origin facts for a user-approved login scope update."""
        try:
            await self.navigate(self.plan.entry_url)
        except Exception:
            if not self.observation_only:
                raise
            diagnostics = await self.command("diagnostics")
            blocked = diagnostics.get("blocked_navigation_origins", [])
            if not any(origin not in self.plan.allowed_origins for origin in blocked):
                raise
            parts = urlsplit(self.plan.entry_url)
            self._stopped_observation = {
                "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
                "title": "",
                "elements": [],
                **diagnostics,
            }

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                message = json.loads(line)
                if message.get("event") == "authorize":
                    task = asyncio.create_task(self._authorize(message))
                    self._guards.add(task)
                    task.add_done_callback(self._guards.discard)
                elif (future := self._pending.pop(message.get("id"), None)) is not None:
                    if message.get("error"):
                        future.set_exception(_error(str(message["error"])))
                    else:
                        future.set_result(message.get("result"))
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(_error("ego_connection_closed"))
            self._pending.clear()

    async def _authorize(self, message: dict[str, Any]) -> None:
        approved = await self.executor.policy.authorize(
            self.plan, message, interactive=self.interactive
        )
        if self._writer is not None and not self._writer.is_closing():
            self._writer.write(
                json.dumps({"id": message["id"], "authorized": approved}).encode() + b"\n"
            )
            await self._writer.drain()

    async def command(self, operation: str, **arguments: Any) -> Any:
        if self._writer is None:
            raise _error("ego_connection_closed")
        self._serial += 1
        serial = self._serial
        future = asyncio.get_running_loop().create_future()
        self._pending[serial] = future
        self._writer.write(
            json.dumps({"id": serial, "operation": operation, "arguments": arguments}).encode()
            + b"\n"
        )
        await self._writer.drain()
        try:
            async with asyncio.timeout(32):
                return await future
        finally:
            self._pending.pop(serial, None)

    async def navigate(self, url: str) -> None:
        await self.command("navigate", url=url)
        self.pages_visited += 1

    async def snapshot(self) -> dict[str, Any]:
        if self._stopped_observation is not None:
            return dict(self._stopped_observation)
        value = await self.command("snapshot")
        return dict(value) if isinstance(value, dict) else {}

    async def screenshot(self) -> bytes:
        return base64.b64decode(await self.command("screenshot"), validate=True)

    async def act(self, action: Any) -> dict[str, Any]:
        if not self.interactive:
            raise _error("manual_only", "Manual control requires login takeover")
        if not isinstance(action, dict) or action.get("action") not in {"click", "fill", "key"}:
            raise _error("invalid_action")
        selector = action.get("selector")
        if selector is not None and (not isinstance(selector, str) or len(selector) > 300):
            raise _error("invalid_action")
        if action["action"] == "fill" and (
            not isinstance(action.get("value"), str) or len(action["value"]) > 4096
        ):
            raise _error("invalid_action")
        result = await self.command(
            str(action["action"]), **{k: v for k, v in action.items() if k != "action"}
        )
        if action["action"] == "fill" and result is not True:
            raise _error("invalid_action", "The manual input selector did not match one input")
        return await self.snapshot()

    async def login_required(self) -> bool:
        observation = await self.snapshot()
        if observation.get("login_wall"):
            return True
        auth = self.plan.auth
        if auth.login_selector:
            row = await self.command("query", selector=auth.login_selector)
            if row and row["visible"]:
                return True
        if auth.required:
            row = await self.command("query", selector=auth.ready_selector)
            return not (row and row["visible"])
        return False

    async def execute(self, parameters: WorkflowParameters) -> BrowserWorkflowResult:
        if self.interactive:
            raise _error("manual_active")
        for step in self.plan.steps:
            if await self.login_required():
                return BrowserWorkflowResult(
                    "login_required",
                    detail="Website login needs user attention",
                    error_code="login_required",
                    scope_complete=False,
                )
            action = step["action"]
            if action == "goto":
                await self.navigate(parameters.render(step["url"], url=True))
            elif action == "fill_search":
                if not await self.command("safe_search", selector=step["selector"]):
                    raise _error("not_search_input")
                await self.command(
                    "fill", selector=step["selector"], value=parameters.render(step["value"])
                )
            elif action == "submit_search":
                form = await self.command("form", selector=step["selector"])
                if not isinstance(form, dict):
                    raise _error("not_get_search")
                parts = urlsplit(form["action"])
                await self.navigate(
                    urlunsplit(
                        (
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode([*parse_qsl(parts.query), *form["fields"]]),
                            "",
                        )
                    )
                )
            elif action == "click_link":
                row = await self.command("query", selector=step["selector"])
                if not row or not row["href"]:
                    raise _error("not_link")
                await self.navigate(str(row["href"]))
            elif action == "wait_for":
                async with asyncio.timeout(10):
                    while not (
                        (row := await self.command("query", selector=step["selector"]))
                        and row["visible"]
                    ):
                        await asyncio.sleep(0.1)
            elif action == "collect":
                return await self._collect(step)
        raise _error("invalid_plan")

    async def _collect(self, step: dict[str, str]) -> BrowserWorkflowResult:
        items: list[BrowserWorkflowItem] = []
        seen: set[str] = set()
        pages: set[str] = set()
        for index in range(self.plan.budgets.max_pages):
            if await self.login_required():
                return BrowserWorkflowResult(
                    "login_required",
                    tuple(items),
                    "Website login needs user attention",
                    "login_required",
                    False,
                    self.pages_visited,
                )
            data = await self.command("collect", step=step, limit=self.plan.budgets.max_items)
            invalid = False
            for raw in data["items"]:
                try:
                    url = workflow_url(raw["url"])
                    if (
                        workflow_origin(url) not in self.plan.allowed_origins
                        or not raw["title"].strip()
                    ):
                        invalid = True
                        continue
                except (BrowserWorkflowValidationError, ValueError):
                    invalid = True
                    continue
                if url in seen:
                    continue
                seen.add(url)
                items.append(
                    BrowserWorkflowItem(
                        raw["title"],
                        url,
                        raw["author_name"],
                        raw["published_at"],
                        raw["description"],
                    )
                )
                if len(items) >= self.plan.budgets.max_items:
                    return BrowserWorkflowResult(
                        "ok", tuple(items), scope_complete=False, pages_visited=self.pages_visited
                    )
            if invalid or (not data["items"] and not data["empty"]):
                return BrowserWorkflowResult(
                    "degraded" if items else "failed",
                    tuple(items),
                    "Collection selectors need verification",
                    "extraction_unverified",
                    False,
                    self.pages_visited,
                    await self.snapshot(),
                )
            if not data["next"]:
                return BrowserWorkflowResult(
                    "ok" if items else "empty", tuple(items), pages_visited=self.pages_visited
                )
            if data["next"] in pages:
                return BrowserWorkflowResult(
                    "degraded",
                    tuple(items),
                    "Pagination repeated a page",
                    "pagination_loop",
                    False,
                    self.pages_visited,
                )
            pages.add(data["next"])
            if index + 1 < self.plan.budgets.max_pages:
                await self.navigate(data["next"])
        return BrowserWorkflowResult(
            "ok" if items else "empty",
            tuple(items),
            scope_complete=False,
            pages_visited=self.pages_visited,
        )

    async def close(self) -> None:
        """Always disconnect the owned host socket, even if its close RPC fails."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._writer:
                try:
                    async with asyncio.timeout(3):
                        await self.command("close")
                except Exception:
                    pass
                finally:
                    self._writer.close()
            for task in self._guards:
                task.cancel()
            if self._reader_task:
                self._reader_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._reader_task
            if self._process and self._process.returncode is None:
                with suppress(TimeoutError):
                    async with asyncio.timeout(2):
                        await self._process.wait()
                if self._process.returncode is None:
                    with suppress(ProcessLookupError):
                        self._process.terminate()
                    with suppress(TimeoutError):
                        async with asyncio.timeout(2):
                            await self._process.wait()
                    if self._process.returncode is None:
                        with suppress(ProcessLookupError):
                            self._process.kill()
                        await self._process.wait()
        finally:
            if self._temporary:
                self._temporary.cleanup()
            if self.lock.locked():
                self.lock.release()


class EgoLiteWorkflowExecutor:
    """Connect the installed Ego host using its official native command channel."""

    def __init__(self, profile_root: Path) -> None:
        self.profile_root = profile_root / "ego"
        self._setup = EgoSetupManager(profile_root.parent)
        managed = self._setup.connection()
        self.executable = managed.get("executable") or (
            os.environ.get("OPENBILICLAW_EGO_BROWSER") or shutil.which("ego-browser") or ""
        )
        self.server_name = managed.get(
            "server_name", os.environ.get("OPENBILICLAW_EGO_SERVER_NAME", "")
        )
        self.profile = managed.get("profile", os.environ.get("OPENBILICLAW_EGO_PROFILE", "Default"))
        self.managed = bool(managed)
        self.host_key = f"{self.executable}:{self.server_name}"
        self.policy = EgoRequestPolicy()

    def availability(self) -> dict[str, Any]:
        """Side-effect-free status: do not run CLI commands which can open onboarding."""
        if self.managed:
            return {
                **self._setup.status(),
                "executor": "ego",
                "network_guarantee": "origin_and_public_dns_preflight",
                "dns_pinned": False,
            }
        installed = bool(self.executable and os.path.isfile(self.executable))
        supported = sys.platform == "darwin"
        pid = _host_pid(self.server_name) if installed and supported else None
        ready = _READY.get(self.host_key)
        available = bool(pid and ready and ready[0] == pid)
        detail = (
            "Ego Lite native connection is ready"
            if available
            else (
                "This host is not supported by the Ego Lite native adapter"
                if not supported
                else "Install Ego Lite and complete first-run setup"
                if not installed
                else "Open Ego Lite on this host"
                if not pid
                else "Ego Lite is installed; its native connection has not been verified"
            )
        )
        return {
            "executor": "ego",
            "installed": installed,
            "available": available,
            "platform_supported": supported,
            "connection_state": "ready" if available else "unverified" if pid else "offline",
            "detail": detail,
            "network_guarantee": "origin_and_public_dns_preflight",
            "dns_pinned": False,
        }

    async def open_session(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        interactive: bool = False,
        observation_only: bool = False,
    ) -> EgoLiteWorkflowSession:
        plan = compile_browser_workflow(plan.to_dict())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
            raise _error("invalid_session")
        if self.server_name and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.server_name):
            raise _error("invalid_host")
        if self.managed and not self._setup.status()["available"]:
            await self._setup.ensure_running()
        status = self.availability()
        if (
            not status["installed"]
            or not status["platform_supported"]
            or status["connection_state"] == "offline"
            or (self.managed and not status["available"])
        ):
            raise _error("executor_unavailable", str(status["detail"]))
        if self.profile_root.is_symlink():
            raise _error("invalid_session")
        self.profile_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile_root.chmod(0o700)
        path = self.profile_root / f"{session_id}.json"
        if path.is_symlink():
            raise _error("invalid_session")
        lock = _LOCKS.setdefault(str(path.resolve()), asyncio.Lock())
        if lock.locked():
            raise _error("session_busy")
        await lock.acquire()
        session = EgoLiteWorkflowSession(
            self, plan, interactive=interactive, lock=lock, observation_only=observation_only
        )
        try:
            state = (
                json.loads(path.read_text())
                if path.exists()
                else {
                    "task_id": None,
                    "task_name": f"OpenBiliClaw-{uuid.uuid4().hex}",
                    "profile": self.profile,
                    "host": self.host_key,
                }
            )
            if state["host"] != self.host_key:
                raise _error(
                    "ego_host_changed", "This login session belongs to a different Ego host"
                )
            await session.start(path, state)
            return session
        except BaseException:
            await session.close()
            raise

    async def run(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        parameters: WorkflowParameters | None = None,
    ) -> BrowserWorkflowResult:
        from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError

        session: EgoLiteWorkflowSession | None = None
        try:
            async with asyncio.timeout(plan.budgets.timeout_seconds):
                session = await self.open_session(plan, session_id=session_id)
                return await session.execute(parameters or WorkflowParameters())
        except BrowserWorkflowExecutionError as exc:
            return BrowserWorkflowResult(
                "login_required" if exc.code == "login_required" else "failed",
                detail=exc.detail,
                error_code=exc.code,
                scope_complete=False,
            )
        except TimeoutError:
            return BrowserWorkflowResult(
                "failed",
                detail="Ego workflow exceeded its total time budget",
                error_code="timeout",
                scope_complete=False,
            )
        except Exception:
            return BrowserWorkflowResult(
                "failed",
                detail="Ego Lite workflow failed",
                error_code="ego_error",
                scope_complete=False,
            )
        finally:
            if session:
                await session.close()
