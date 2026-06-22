"""Release consistency checks.

The release flow bumps ``pyproject.toml`` and ``openbiliclaw.__version__``;
``uv.lock`` records the package's own version from lock time. If the bump
commit forgets to run ``uv lock`` / ``uv sync``, every fresh install's first
``uv sync`` rewrites uv.lock — the worktree turns dirty from day one and the
auto-update apply guard blocks every update with ``dirty_worktree``
(see ``openbiliclaw.runtime.updater``). Keep all three in lockstep.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import openbiliclaw

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _uv_lock_self_entry() -> dict[str, Any]:
    lock = tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    for package in lock.get("package", []):
        if package.get("name") == "openbiliclaw":
            return package
    raise AssertionError("uv.lock has no [[package]] entry for openbiliclaw itself")


def test_uv_lock_self_version_matches_package_version() -> None:
    """A stale uv.lock bricks auto-update on every install — re-lock on bump."""
    assert _uv_lock_self_entry()["version"] == openbiliclaw.__version__, (
        "uv.lock records a different openbiliclaw version than "
        "openbiliclaw.__version__ — run `uv lock` (or `uv sync`) and commit "
        "uv.lock together with the version bump"
    )


def test_pyproject_version_matches_package_version() -> None:
    pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == openbiliclaw.__version__
