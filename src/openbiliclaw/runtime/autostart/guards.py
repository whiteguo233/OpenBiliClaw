"""Guards shared by autostart API, CLI, and start-time self-heal."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.config import load_config

if TYPE_CHECKING:
    from openbiliclaw.config import Config

_PROJECT_ROOT_ENV = "OPENBILICLAW_PROJECT_ROOT"
_INTERNAL_RUNTIME_ENVS = {_PROJECT_ROOT_ENV, "OPENBILICLAW_SELFTEST"}
_PROVIDER_CREDENTIAL_ENVS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

# ``OPENBILICLAW_*`` keys that existed before this process started mutating its
# own environment. Anything carrying the prefix but missing here was injected by
# the runtime after startup — four-process worker flags, the recommendation
# transport, the image-proxy URL — and every launch rebuilds it, so a login
# session autostart entry cannot lose it.
_startup_env_keys: frozenset[str] | None = None


def snapshot_startup_env() -> None:
    """Record which ``OPENBILICLAW_*`` keys the launching environment supplied.

    Idempotent: the first call wins, so a later caller cannot mistake runtime
    mutations for caller-supplied configuration. Call this before the first
    ``os.environ`` write in ``openbiliclaw start`` and in the frozen desktop
    entry.
    """
    global _startup_env_keys
    if _startup_env_keys is not None:
        return
    _startup_env_keys = frozenset(key for key in os.environ if key.startswith("OPENBILICLAW_"))


def _is_runtime_injected(key: str) -> bool:
    """Whether the runtime, not the launching environment, set ``key``."""
    return _startup_env_keys is not None and key not in _startup_env_keys


def build_restart_env() -> dict[str, str]:
    """Copy the environment without wiring the replacement process rebuilds.

    Keep caller-supplied inputs, including explicit runtime overrides. Without
    a startup snapshot, preserve everything rather than guess their provenance.
    The live environment stays intact if restarting the process fails.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not (key.startswith("OPENBILICLAW_") and _is_runtime_injected(key))
    }


def _env_is_set(key: str) -> bool:
    return bool(os.environ.get(key, "").strip())


def _is_packaged_sponsored_bundle(key: str) -> bool:
    """Recognize only the SDK path reconstructed by every frozen app launch."""
    if key != "OPENBILICLAW_SPONSORED_BUNDLE" or not getattr(sys, "frozen", False):
        return False
    executable_dir = Path(sys.executable).resolve().parent
    resources = executable_dir
    if (
        executable_dir.name == "MacOS"
        and executable_dir.parent.name == "Contents"
        and executable_dir.parent.parent.suffix == ".app"
    ):
        resources = executable_dir.parent / "Resources"
    bundle = resources / "sponsored"
    return bundle.is_dir() and Path(os.environ[key]).resolve() == bundle.resolve()


def active_env_managed_inputs(config: Config) -> list[str]:
    """Return active env keys that a login-session autostart entry would lose."""
    managed: set[str] = set()
    for key in os.environ:
        if (
            key.startswith("OPENBILICLAW_")
            and key not in _INTERNAL_RUNTIME_ENVS
            and _env_is_set(key)
            and not _is_runtime_injected(key)
            and not _is_packaged_sponsored_bundle(key)
        ):
            managed.add(key)

    for key in _PROVIDER_CREDENTIAL_ENVS:
        if _env_is_set(key):
            managed.add(key)

    douyin_cookie_env = str(config.sources.douyin.cookie_env).strip()
    if douyin_cookie_env and _env_is_set(douyin_cookie_env):
        managed.add(douyin_cookie_env)

    return sorted(managed)


def autostart_shadowed(intended: bool) -> bool:
    """Return whether effective config disagrees with a just-written intent."""
    return load_config().autostart.enabled != intended
