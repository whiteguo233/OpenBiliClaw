"""User-scope boot autostart registration."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from openbiliclaw import docker_runtime
from openbiliclaw.runtime.distribution import is_mac_app_store

from .base import AutostartManager, AutostartStatus

if TYPE_CHECKING:
    from openbiliclaw.config import Config


def _unsupported_reason() -> str:
    if is_mac_app_store():
        return "managed_by_store_app"
    if docker_runtime.is_running_in_container():
        return "unsupported_docker_runtime"
    return "unsupported_platform"


def get_manager() -> AutostartManager | None:
    """Return the current platform manager, or ``None`` when unsupported."""
    if is_mac_app_store() or docker_runtime.is_running_in_container():
        return None

    try:
        if sys.platform == "darwin":
            from .macos import MacOSLaunchAgentManager

            return MacOSLaunchAgentManager()
        if sys.platform == "win32":
            from .windows import WindowsRunManager

            return WindowsRunManager()
        if sys.platform.startswith("linux"):
            from .linux import LinuxXdgAutostartManager

            return LinuxXdgAutostartManager()
    except ModuleNotFoundError:
        return None
    return None


def is_supported() -> bool:
    """Return whether the current runtime can manage user autostart."""
    return get_manager() is not None


def register(config: object) -> None:
    """Register the current platform autostart entry."""
    manager = get_manager()
    if manager is None:
        raise RuntimeError(_unsupported_reason())
    manager.register(config)  # type: ignore[arg-type]


def unregister() -> None:
    """Remove the current platform autostart entry."""
    manager = get_manager()
    if manager is None:
        raise RuntimeError(_unsupported_reason())
    manager.unregister()


def status() -> AutostartStatus:
    """Return current platform autostart status."""
    manager = get_manager()
    if manager is None:
        return AutostartStatus(
            supported=False,
            registered=False,
            platform=sys.platform,
            mechanism="none",
            reason=_unsupported_reason(),
        )
    return AutostartStatus(
        supported=True,
        registered=manager.is_registered(),
        platform=sys.platform,
        mechanism=manager.mechanism,
        reason="none",
    )


def reconcile(config: Config) -> str | None:
    """Reconcile configured intent with the current user's OS login item.

    Returns a human-readable warning when a best-effort repair fails. Both the
    CLI daemon and frozen desktop entry call this helper so install modes cannot
    drift.
    """
    try:
        state = status()
    except Exception as exc:  # noqa: BLE001 - startup reconciliation boundary
        return f"开机自启动状态检查失败：{exc}"
    if not state.supported:
        return None

    if not config.autostart.enabled:
        # Always call the idempotent remover. A broken legacy entry may be
        # hidden by is_registered() (missing executable/script) yet still remain
        # in the OS store and resurrect after a reinstall to the same path.
        try:
            unregister()
        except Exception as exc:  # noqa: BLE001 - startup must continue
            return f"开机自启动残留项移除失败：{exc}"
        return None

    if state.registered:
        manager = get_manager()
        refresh = getattr(manager, "refresh_if_needed", None)
        if callable(refresh):
            try:
                refresh(config)
            except Exception as exc:  # noqa: BLE001 - startup must continue
                return f"开机自启动旧注册项升级失败：{exc}"
        return None

    from .guards import active_env_managed_inputs

    managed = active_env_managed_inputs(config)
    if managed:
        return (
            "已开启开机自启动，但检测到环境变量配置，跳过自动补注册："
            f"{', '.join(managed)}。请先写入 config.toml。"
        )
    try:
        register(config)
    except Exception as exc:  # noqa: BLE001 - startup must continue
        return f"开机自启动补注册失败：{exc}"
    return None


__all__ = [
    "AutostartManager",
    "AutostartStatus",
    "get_manager",
    "is_supported",
    "reconcile",
    "register",
    "status",
    "unregister",
]
