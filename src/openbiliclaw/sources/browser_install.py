"""Install and inspect the browser runtime in Python and frozen desktop builds.

The bundled Playwright Node driver installs its matching browser revision. No
system Python, shell, arbitrary command or npm installation is required.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

from openbiliclaw.proc import no_window_kwargs


def _driver_command() -> tuple[str, str] | None:
    if importlib.util.find_spec("playwright") is None:
        return None
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return str(node), str(cli)


def browser_runtime_status() -> dict[str, Any]:
    """Inspect the matching Chromium executable, including inside a frozen app."""

    driver = _driver_command()
    if driver is None:
        return {
            "installed": False,
            "executable_path": "",
            "detail": "Repair the Python installation with openbiliclaw[browser]",
        }
    node, cli = driver
    try:
        output = subprocess.run(
            [
                node,
                "-e",
                "const {chromium}=require(process.argv[1]);"
                "console.log(JSON.stringify({executable_path:chromium.executablePath()}))",
                str(Path(cli).parent),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            **no_window_kwargs(),
        )
        path = str(json.loads(output.stdout)["executable_path"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return {
            "installed": False,
            "executable_path": "",
            "detail": "Bundled browser driver is unavailable",
        }
    installed = Path(path).is_file()
    return {
        "installed": installed,
        "executable_path": path,
        "detail": "" if installed else "Install Chromium with openbiliclaw browser install",
    }


async def install_browser_runtime(*, with_system_deps: bool = False) -> dict[str, Any]:
    """Download the matching Chromium; Linux admins may explicitly include OS packages."""

    driver = _driver_command()
    if driver is None:
        return browser_runtime_status()
    command = [*driver, "install"]
    if with_system_deps:
        command.append("--with-deps")
    command.append("chromium")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    try:
        async with asyncio.timeout(900):
            returncode = await process.wait()
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if returncode:
        return {
            "installed": False,
            "executable_path": "",
            "detail": "Chromium installation failed; check network access and OS dependencies",
        }
    return await asyncio.to_thread(browser_runtime_status)
