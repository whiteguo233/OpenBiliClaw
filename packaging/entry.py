"""Desktop application entry point for OpenBiliClaw.

This module bootstraps the local backend server as a standalone
desktop application packaged via PyInstaller.

The layout differs between onedir and macOS ``.app`` bundle outputs:

* **onedir** (``dist/OpenBiliClaw/OpenBiliClaw``) — the executable and its
  bundled resources (``config.example.toml``, ``ollama``) live in the install
  directory, which Setup overwrites on upgrade and may remove on uninstall.
  User data therefore lives in the same per-user root used by the one-line /
  AI installers (``%USERPROFILE%\\OpenBiliClaw`` on Windows,
  ``~/OpenBiliClaw`` on macOS/Linux). Any data an older build left next to the
  executable or in the older packaged-app system data root is migrated on first
  launch.
* **macOS .app** (``OpenBiliClaw.app/Contents/MacOS/OpenBiliClaw``) —
  the bundle itself is treated as read-only. The bundled default template
  ``config.example.toml`` is placed under ``Contents/Resources`` by PyInstaller
  and seeded into the user's data dir on first launch.

In both packaged layouts the read-only bundle provides the template config +
``ollama`` while user data lives under :func:`_user_data_root`.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from openbiliclaw.proc import ChildProcessSupervisor, ManagedChild
from openbiliclaw.recommendation_runtime import ensure_recommendation_transport_env

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any


def _is_macos_app_bundle(exe_dir: Path) -> bool:
    """True when the executable sits inside ``.app/Contents/MacOS``."""
    return exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents"


def _macos_app_bundle_root(exe_dir: Path) -> Path:
    """Return the ``.app`` directory when running from a macOS bundle."""
    return exe_dir.parent.parent


def _user_data_root_for(
    os_name: str, platform: str, home: Path, environ: Mapping[str, str]
) -> Path:
    """Pure resolver for the per-user data root (params injected for testing).

    Packaged builds intentionally share the same root as script / AI installs:
    ``~/OpenBiliClaw`` on Unix-like systems and ``%USERPROFILE%\\OpenBiliClaw``
    on Windows. This lets users switch between install channels without
    retyping keys or losing profile data.
    """
    _ = os_name, platform, environ
    return home / "OpenBiliClaw"


def _user_data_root() -> Path:
    """Return the per-user, writable data root for the packaged app.

    Kept independent of the (upgrade-overwritten, uninstall-removed) install
    directory. See :func:`_user_data_root_for` for the per-OS mapping.
    """
    return _user_data_root_for(os.name, sys.platform, Path.home(), os.environ)


def _legacy_packaged_user_data_root_for(
    os_name: str, platform: str, home: Path, environ: Mapping[str, str]
) -> Path:
    """Return the user-data root used by older packaged desktop builds."""
    if os_name == "nt":
        base = environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / "OpenBiliClaw"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "OpenBiliClaw"
    base = environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / "OpenBiliClaw"


def _legacy_packaged_user_data_root() -> Path:
    """Return the legacy packaged-app root for the current machine."""
    return _legacy_packaged_user_data_root_for(
        os.name,
        sys.platform,
        Path.home(),
        os.environ,
    )


# Names older builds wrote next to the executable under ``{app}``; relocated to
# the per-user data root on the first launch of a relocation-aware build.
_LEGACY_DATA_ENTRIES = ("config.toml", "config.local.toml", "data", "logs")
_CONFIG_FILES = ("config.toml", "config.local.toml")


class _ConfigRepairResult(NamedTuple):
    repaired: bool
    regenerated_default: bool


def _migrate_legacy_install_dir_data(install_dir: Path, project_root: Path) -> None:
    """Relocate pre-relocation user data out of the install directory.

    Builds before this change kept ``config.toml`` / ``data/`` / ``logs/`` next
    to the executable under ``{app}``. That entangled user data with the install
    dir — it got locked during upgrades and risked deletion on uninstall. User
    data now lives under :func:`_user_data_root`; on the first launch of a new
    build, move anything an old build left behind in the install dir.

    Best-effort and idempotent: skips when the new root already holds a config
    or database (already migrated / fresh new-layout install), never clobbers an
    existing destination, and never raises (a failed move just falls back to a
    fresh data dir rather than crashing startup). Must run BEFORE the new
    ``data/`` / ``logs/`` dirs are created, so a whole-directory move lands
    cleanly instead of nesting inside a freshly-made empty dir.
    """
    if install_dir == project_root:
        return  # dev fallback / any same-dir layout: nothing to relocate
    already_migrated = (project_root / "config.toml").exists() or (
        project_root / "data" / "openbiliclaw.db"
    ).exists()
    if already_migrated:
        return
    legacy = [name for name in _LEGACY_DATA_ENTRIES if (install_dir / name).exists()]
    if not legacy:
        return
    project_root.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for name in legacy:
        destination = project_root / name
        if destination.exists():
            continue  # never overwrite something already in the new root
        try:
            shutil.move(str(install_dir / name), str(destination))
            moved.append(name)
        except Exception as exc:  # noqa: BLE001 — best-effort; fall back to fresh
            print(f"[OpenBiliClaw] 历史数据迁移跳过 {name}: {exc}")
    if moved:
        print(f"[OpenBiliClaw] 已将历史数据迁移到 {project_root}: {', '.join(moved)}")


def _copy_legacy_packaged_user_data(legacy_root: Path, project_root: Path) -> None:
    """Copy old packaged-app user data into the unified script/AI data root.

    The desktop package used to store user data in OS-specific app-data
    locations. New builds use ``~/OpenBiliClaw`` / ``%USERPROFILE%\\OpenBiliClaw``
    so all install channels share one profile. This migration is copy-only,
    best-effort and non-clobbering: if the unified root already has a given
    entry, it wins.
    """
    if legacy_root == project_root or not legacy_root.exists():
        return
    legacy = [name for name in _LEGACY_DATA_ENTRIES if (legacy_root / name).exists()]
    if not legacy:
        return
    project_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in legacy:
        source = legacy_root / name
        destination = project_root / name
        if destination.exists():
            continue
        try:
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            copied.append(name)
        except Exception as exc:  # noqa: BLE001 — best-effort compatibility
            print(f"[OpenBiliClaw] 历史安装包数据拷贝跳过 {name}: {exc}")
    if copied:
        print(f"[OpenBiliClaw] 已从历史安装包数据目录拷贝到 {project_root}: {', '.join(copied)}")


def _resolve_runtime_paths() -> tuple[Path, Path]:
    """Return ``(project_root, bundled_resources)`` based on launch mode.

    ``project_root`` is where ``config.toml`` / ``data/`` / ``logs/`` live.
    ``bundled_resources`` is the read-only directory holding the default
    ``config.example.toml`` (and bundled ``ollama``) shipped with the package.

    An explicit ``OPENBILICLAW_PROJECT_ROOT`` env var overrides ``project_root``
    (portable installs, multiple profiles, isolated tests); bundled resources are
    still resolved from the executable. ``main`` re-exports the resolved root, so
    a pre-set value flows through to the backend unchanged.
    """
    override = os.environ.get("OPENBILICLAW_PROJECT_ROOT", "").strip()

    if not getattr(sys, "frozen", False):
        # Development fallback
        repo_root = Path(__file__).resolve().parent.parent
        return (Path(override) if override else repo_root), repo_root

    exe_dir = Path(sys.executable).resolve().parent
    if _is_macos_app_bundle(exe_dir):
        bundled_resources = exe_dir.parent / "Resources"
        return (Path(override) if override else _user_data_root()), bundled_resources

    # onedir layout (Windows): the install dir is overwritten on upgrade and may
    # be removed on uninstall, so user data lives in a per-user root instead of
    # next to the executable. The install dir still provides the bundled template
    # config + ollama as read-only resources.
    bundled_resources = exe_dir
    return (Path(override) if override else _user_data_root()), bundled_resources


def _seed_default_config(project_root: Path, bundled_resources: Path) -> bool:
    """Copy the bundled ``config.example.toml`` into ``project_root`` on first run.

    Returns ``True`` only when a fresh ``config.toml`` was just created, so the
    caller can apply packaged-only first-run defaults (e.g. enabling the bundled
    Ollama embedding) without ever overriding a config the user already has.
    """
    config_path = project_root / "config.toml"
    if config_path.exists():
        return False
    example_candidates = [
        project_root / "config.example.toml",
        bundled_resources / "config.example.toml",
    ]
    for example in example_candidates:
        if example.exists():
            shutil.copyfile(example, config_path)
            print(f"[OpenBiliClaw] 已生成默认配置: {config_path}")
            return True
    return False


def _next_invalid_config_path(path: Path) -> Path:
    """Return a non-clobbering backup path for an unloadable config file."""
    candidate = path.with_name(f"{path.name}.invalid")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.invalid.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _quarantine_config_file(path: Path, reason: object) -> bool:
    """Move an unloadable config aside so startup can regenerate defaults."""
    destination = _next_invalid_config_path(path)
    try:
        path.rename(destination)
    except OSError as exc:
        print(f"[OpenBiliClaw] 配置文件无法隔离 {path}: {exc}")
        return False
    reason_text = str(reason).splitlines()[0]
    print(
        f"[OpenBiliClaw] 配置文件异常,已备份为 {destination.name};"
        f" 将使用默认配置重新初始化。原因: {reason_text}"
    )
    return True


def _toml_syntax_error(path: Path) -> BaseException | None:
    """Return the TOML/encoding parse error for a config file, if any."""
    try:
        with path.open("rb") as file:
            tomllib.load(file)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return exc
    except OSError as exc:
        print(f"[OpenBiliClaw] 配置文件读取失败 {path}: {exc}")
    return None


def _runtime_config_error(project_root: Path) -> BaseException | None:
    """Return the error raised by the real runtime config builder, if any."""
    previous_root = os.environ.get("OPENBILICLAW_PROJECT_ROOT")
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(project_root)
    try:
        from openbiliclaw.config import load_config

        load_config()
    except Exception as exc:  # noqa: BLE001 — desktop startup recovery boundary
        return exc
    finally:
        if previous_root is None:
            os.environ.pop("OPENBILICLAW_PROJECT_ROOT", None)
        else:
            os.environ["OPENBILICLAW_PROJECT_ROOT"] = previous_root
    return None


def _repair_unloadable_config(project_root: Path, bundled_resources: Path) -> _ConfigRepairResult:
    """Quarantine broken config files and regenerate a default desktop config.

    This only touches ``config.toml`` / ``config.local.toml``. User data under
    ``data/`` remains intact; the setup wizard can re-collect missing settings.
    """
    repaired = False
    regenerated_default = False

    for name in _CONFIG_FILES:
        path = project_root / name
        if not path.exists():
            continue
        error = _toml_syntax_error(path)
        if error is not None and _quarantine_config_file(path, error):
            repaired = True

    if not (project_root / "config.toml").exists():
        regenerated_default = _seed_default_config(project_root, bundled_resources)

    error = _runtime_config_error(project_root)
    if error is not None:
        print(
            "[OpenBiliClaw] 配置结构无法加载,将隔离配置并重新初始化。"
            f"原因: {str(error).splitlines()[0]}"
        )
        for name in _CONFIG_FILES:
            path = project_root / name
            if path.exists() and _quarantine_config_file(path, error):
                repaired = True
        if not (project_root / "config.toml").exists():
            regenerated_default = _seed_default_config(project_root, bundled_resources)

    return _ConfigRepairResult(repaired=repaired, regenerated_default=regenerated_default)


def _bundled_ollama_path(bundled_resources: Path) -> Path | None:
    """Return the packaged ``ollama`` executable shipped beside the app, if any."""
    name = "ollama.exe" if os.name == "nt" else "ollama"
    candidate = bundled_resources / name
    return candidate if candidate.exists() else None


def _inject_bundled_ollama_on_path(bundled_resources: Path) -> bool:
    """Prepend the bundled ollama's directory to ``PATH``.

    The runtime talks to Ollama purely over HTTP and locates the binary via
    ``shutil.which("ollama")`` (in ``runtime/ollama_supervisor.py``). Putting the
    bundled binary first on ``PATH`` lets every existing code path — health
    probe, ``ollama serve`` preflight, model pull — find it with **zero** changes
    to the reviewed runtime code, while still honouring a user-installed ollama
    if they prefer one (we only prepend, never replace).
    """
    ollama = _bundled_ollama_path(bundled_resources)
    if ollama is None:
        return False
    if os.name != "nt":
        with suppress(Exception):
            os.chmod(ollama, 0o755)  # zip/copy roundtrips can drop the +x bit
    bin_dir = str(ollama.parent)
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current
    return True


def _enable_ollama_embedding_default(config_path: Path) -> None:
    """Flip ``[llm.embedding].provider`` to ``ollama`` in a freshly seeded config.

    Only touches the single ``provider = ""`` line inside the ``[llm.embedding]``
    block so the heavily-commented template stays intact (a ``save_config``
    round-trip would strip every comment). No-op if the user already picked a
    provider. This is what makes the bundled Ollama actually drive embedding
    out of the box; without it the shipped binary would sit idle.
    """
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    in_block = False
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_block = stripped == "[llm.embedding]"
            continue
        if in_block and not changed and re.match(r'\s*provider\s*=\s*""', line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f'{indent}provider = "ollama"\n'
            changed = True
            break
    if changed:
        config_path.write_text("".join(lines), encoding="utf-8")
        print("[OpenBiliClaw] 已默认启用本地 Ollama embedding (bge-m3)")


def _default_ollama_to_embedding_only(config_path: Path) -> None:
    """Blank the legacy preset ``[llm.ollama] model`` for packaged upgrades.

    The v2 template marks its Ollama chat instance disabled, so it needs no
    mutation. This path remains only for an older copied config where a
    non-empty ``[llm.ollama].model`` implicitly made Ollama chat-capable.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return
    if "[llm.instances." in text:
        return
    provider = re.search(r'(?m)^\s*default_provider\s*=\s*"([^"]*)"', text)
    if provider and provider.group(1).strip().lower() == "ollama":
        return
    lines = text.splitlines(keepends=True)
    in_block = False
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_block = stripped == "[llm.ollama]"
            continue
        if in_block:
            match = re.match(r'(\s*model\s*=\s*)"[^"]*"(.*)$', line)
            if match:
                lines[i] = f'{match.group(1)}""{match.group(2)}\n'
                changed = True
                break
    if changed:
        config_path.write_text("".join(lines), encoding="utf-8")
        print("[OpenBiliClaw] 本地 Ollama 默认仅用于 embedding(已清空预设 chat 模型)")


def _packaged_ollama_preflight() -> None:
    """Ensure a loopback ``ollama serve`` is up when chat/embedding needs it.

    Mirrors ``cli._preflight_loopback_ollama`` (loopback-only guard included) via
    the shared supervisor, because the packaged entry calls ``create_app()``
    directly and never goes through ``openbiliclaw start`` where the preflight
    normally runs. Failures only warn — they must never block the server.
    """
    try:
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.ollama_supervisor import (
            _is_default_ollama_endpoint,
            _ollama_is_running,
            _ollama_start_serve_background,
            effective_ollama_endpoint,
            is_loopback,
            ollama_required,
        )

        cfg = load_config()
        if not ollama_required(cfg) or not cfg.autostart.manage_ollama:
            return
        endpoint = effective_ollama_endpoint(cfg)
        if not is_loopback(endpoint) or _ollama_is_running(host=endpoint):
            return
        if not _is_default_ollama_endpoint(endpoint):
            return
        if not _ollama_start_serve_background():
            print("[OpenBiliClaw] Ollama 未能自动拉起；embedding 可能降级。")
    except Exception as exc:  # noqa: BLE001 — preflight must never crash startup
        print(f"[OpenBiliClaw] Ollama preflight 跳过: {exc}")


def _ensure_embedding_model_async() -> None:
    """Pull the local embedding model in the background if it's missing.

    Honours the "bundle the runtime, fetch the 1.1GB weights once" approach: a
    fresh machine reaches embedding-ready on its own without the user running
    ``setup-embedding``. Runs in a daemon thread so the 1.1GB download never
    blocks the API; reuses the battle-tested ``cli`` pull helpers (already in the
    bundle). No-op when embedding isn't Ollama or the model is already present.
    """

    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        emb = cfg.llm.embedding
        if str(emb.provider).strip().lower() != "ollama":
            return
        model = str(emb.model).strip() or "bge-m3"
        base_url = (
            str(emb.base_url).strip()
            or str(cfg.llm.ollama.base_url).strip()
            or "http://localhost:11434/v1"
        )
        from openbiliclaw.runtime import embedding_progress

        # Publish the in-flight state before starting the worker. The API can
        # bind and serve its first /api/init-status request before a newly
        # created thread gets scheduled; without this early mark the GUI sees
        # one idle snapshot and stops polling, so the user must click the init
        # CTA to discover the download.
        embedding_progress.mark_pull_running(model)

        def _worker() -> None:
            try:
                from openbiliclaw.cli import _ollama_has_model
                from openbiliclaw.llm.ollama_diagnostics import (
                    native_root,
                    pull_ollama_model,
                )

                if _ollama_has_model(model, native_root(base_url)):
                    embedding_progress.mark_pull_done(True, "")
                    return
                print(f"[OpenBiliClaw] 后台拉取本地 embedding 模型 {model}(约 1.1GB,仅首次)…")
                ok = False
                error = ""
                try:
                    ok, error = asyncio.run(
                        pull_ollama_model(
                            base_url,
                            model,
                            on_progress=embedding_progress.report_pull,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — background best-effort only
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    embedding_progress.mark_pull_done(ok, error)
                if ok:
                    print(f"[OpenBiliClaw] 本地 embedding 模型 {model} 就绪")
                elif error:
                    print(f"[OpenBiliClaw] 本地 embedding 模型 {model} 拉取失败: {error}")
            except Exception as exc:  # noqa: BLE001 — background best-effort only
                error = f"{type(exc).__name__}: {exc}"
                embedding_progress.mark_pull_done(False, error)
                print(f"[OpenBiliClaw] 模型自动拉取跳过: {exc}")

        threading.Thread(target=_worker, name="obc-embed-pull", daemon=True).start()
    except Exception as exc:  # noqa: BLE001 — startup must remain best-effort
        print(f"[OpenBiliClaw] 模型自动拉取跳过: {exc}")


# Dedicated port for the ``with-embedding`` variant's PRIVATE Ollama daemon, so
# the baked model is served from our own ASCII/user-writable dir independently
# of any external/official Ollama the user runs on the default 11434.
_PRIVATE_OLLAMA_PORT = 11435


def _set_embedding_field(config_path: Path, field: str, value: str) -> None:
    """Set a single ``[llm.embedding].<field>`` line, editing only that line so
    the comment-heavy template survives (mirrors ``_enable_ollama_embedding_default``)."""
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    in_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_block = stripped == "[llm.embedding]"
            continue
        if in_block and re.match(rf"\s*{re.escape(field)}\s*=", line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f'{indent}{field} = "{value}"\n'
            config_path.write_text("".join(lines), encoding="utf-8")
            return


def _seed_bundled_embedding_model(bundled_resources: Path, config_path: Path) -> bool:
    """``with-embedding`` variant: seed the baked bge-m3 into an ASCII,
    user-writable dir and serve it from a PRIVATE Ollama on a dedicated port,
    BEFORE any other Ollama start.

    Returns True when the private embedding daemon is up (the caller then skips
    the default preflight + network pull). Returns False — a no-op — for the
    lean variant (no seed dir), when the user picked a remote embedding
    provider, or when anything can't complete (=> fall back to the normal
    preflight + online pull). Never crashes startup.
    """
    seed_dir = bundled_resources / "bge-m3-seed"
    if not seed_dir.is_dir():
        return False  # lean variant — nothing bundled
    try:
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.embedding_seed import (
            effective_embedding_models_dir,
            seed_embedding_model,
        )
        from openbiliclaw.runtime.ollama_supervisor import start_managed_ollama_at

        cfg = load_config()
        provider = str(cfg.llm.embedding.provider).strip().lower()
        if provider not in ("", "ollama"):
            return False  # user chose a remote embedding provider — respect it

        target = effective_embedding_models_dir(
            user_ollama_models=os.environ.get("OLLAMA_MODELS"),
            platform=sys.platform,
            home=Path.home(),
            programdata=os.environ.get("PROGRAMDATA"),
        )
        if target is None:
            return False

        result = seed_embedding_model(seed_dir, target)
        if not result.ok:
            print(f"[OpenBiliClaw] 内置向量模型播种失败,改为在线拉取: {result.detail}")
            return False

        host = f"127.0.0.1:{_PRIVATE_OLLAMA_PORT}"
        base_url = f"http://{host}/v1"
        # Provider + base_url + model all point at the private daemon, which
        # only serves the bundled bge-m3. Forcing the model guards against a
        # stale/other embedding model name 404-ing against the private daemon.
        _enable_ollama_embedding_default(config_path)
        _set_embedding_field(config_path, "base_url", base_url)
        _set_embedding_field(config_path, "model", "bge-m3")

        if not start_managed_ollama_at(str(target), host):
            print("[OpenBiliClaw] 私有 Ollama 未能启动,内置向量模型交回默认流程。")
            return False
        print(f"[OpenBiliClaw] 内置向量模型 bge-m3 已就绪(私有 Ollama :{_PRIVATE_OLLAMA_PORT})")
        return True
    except Exception as exc:  # noqa: BLE001 — must never crash startup
        print(f"[OpenBiliClaw] 内置向量模型初始化跳过: {exc}")
        return False


def _redirect_output_to_logfile(project_root: Path) -> Path | None:
    """Point stdout/stderr at a log file for the windowed (no-console) build.

    A PyInstaller windowed app has no console: ``sys.stdout`` / ``sys.stderr``
    are ``None``, so stray ``print`` / traceback writes raise. Send them to
    ``logs/desktop.log`` so nothing crashes and the tray's "view logs" entry has
    live output to show. No-op when not frozen (dev keeps its console).
    """
    if not getattr(sys, "frozen", False):
        return None
    log_dir = project_root / "logs"
    with suppress(OSError):
        log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "desktop.log"
    try:
        # Windows log viewers (Notepad, many tail tools) on a zh-CN locale guess
        # GBK for a BOM-less UTF-8 file → Chinese turns to mojibake. Detect a
        # fresh/empty file so we can prepend a UTF-8 BOM below, making the
        # encoding unambiguous; appends afterwards add no extra BOM.
        fresh = not log_path.exists() or log_path.stat().st_size == 0
        # Long-lived on purpose: this stream IS stdout/stderr for the whole
        # process, so it must stay open (no context manager).
        stream = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115
        if fresh:
            stream.write("\ufeff")  # BOM → UTF-8 bytes EF BB BF
            stream.flush()
    except OSError:
        return None
    sys.stdout = stream
    sys.stderr = stream
    return log_path


def _close_splash() -> None:
    """Close the PyInstaller boot splash, if this build has one.

    ``pyi_splash`` only exists inside a frozen build that bundled a Splash
    target (Windows). Everywhere else (dev, macOS) the import fails and this is a
    no-op. Best-effort: a failure here must never block startup.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]  # only in splash builds
    except Exception:  # noqa: BLE001 — absent on dev / macOS / non-splash builds
        return
    with suppress(Exception):
        pyi_splash.close()


def _notify_starting() -> None:
    """Show a one-shot 'starting' OS notification (macOS only, frozen only).

    Windows gets the PyInstaller boot splash; macOS can't (PyInstaller splash is
    unsupported there), so a menu-bar agent — which shows no Dock bounce — uses a
    notification instead so the launch isn't silent. Best-effort and fire-and-
    forget; never blocks or raises.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return
    with suppress(Exception):
        subprocess.Popen(  # noqa: S603
            [
                "osascript",  # noqa: S607
                "-e",
                'display notification "正在启动,请稍候…" with title "OpenBiliClaw"',
            ]
        )


def _open_in_default_app(path: Path) -> None:
    """Open a file / folder with the OS default handler (best-effort)."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
    except Exception as exc:  # noqa: BLE001 — best-effort UX, never crash the tray
        print(f"[OpenBiliClaw] 打开失败 {path}: {exc}")


def _view_runtime_logs(log_path: Path) -> None:
    """Show the running log in a live-tailing console/terminal (parity across
    Windows + macOS); fall back to opening the log file in the default app."""
    try:
        if os.name == "nt":
            _open_in_default_app(log_path)
            return
        if sys.platform == "darwin":
            # Live-tail in Terminal by opening a tiny .command as a *document*
            # (`open -a Terminal file.command`). The previous approach —
            # `osascript … tell application "Terminal"` — needs Apple-Events
            # automation permission, which an unsigned packaged .app is silently
            # denied, so the menu item did nothing (and Popen never saw the
            # error). A document open needs no such permission; Terminal just
            # runs the script. Paths are double-quoted to survive the space in
            # "Application Support". Fall back to the default app if Terminal
            # can't be launched (return code checked, unlike the old Popen).
            helper = log_path.parent / "view-logs.command"
            helper.write_text(f'#!/bin/bash\ntail -n 200 -f "{log_path}"\n', encoding="utf-8")
            helper.chmod(0o755)
            opened = subprocess.run(  # noqa: S603
                ["open", "-a", "Terminal", str(helper)],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
            )
            if opened.returncode == 0:
                return
            print(f"[OpenBiliClaw] 用 Terminal 打开日志失败,改用默认应用: {opened.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001 — fall back to opening the file
        print(f"[OpenBiliClaw] 打开实时日志失败: {exc}")
    _open_in_default_app(log_path)


def _try_single_instance_lock(project_root: Path) -> tuple[str, Any]:
    """Attempt the per-data-dir single-instance lock.

    Returns ``(status, handle)``:

    * ``("acquired", handle)`` — we own it; the caller MUST keep ``handle`` open
      for the process lifetime. The OS releases the lock automatically when the
      process exits (even on a crash), so there are no stale locks.
    * ``("busy", None)`` — another instance already holds it (e.g. the user
      double-clicked the icon); this launch should not start a second backend.
    * ``("error", None)`` — couldn't use a lock file at all; the caller should
      fail open (start anyway) rather than block on a lock-file problem.

    The lock lives in ``project_root``, so separate data dirs (portable installs)
    can run side by side; the same install double-launched is what gets blocked.
    """
    lock_path = project_root / "openbiliclaw.lock"
    try:
        handle = open(lock_path, "a+")  # noqa: SIM115 — held for the process lifetime
    except OSError:
        return "error", None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return "busy", None
    return "acquired", handle


# Named mutex held by every packaged OpenBiliClaw process (the tray parent and
# each --openbiliclaw-worker child — all are the frozen exe whose files
# Setup/Uninstall overwrite or delete). packaging/openbiliclaw.iss lists this
# name in [Setup] AppMutex so both Setup and Uninstall open with the standard
# "application is running" dialog instead of proceeding into locked files:
# Inno's CloseApplications/Restart Manager is install-only, and the
# uninstaller treats locked-file delete errors as non-fatal — it used to
# strand the locked files, delete itself, and leave the user no way to retry
# the uninstall. Session-local on purpose (no Global\ prefix): the install is
# per-user, and another user's running instance must not block this user's
# Setup/Uninstall.
_INSTALLER_MUTEX_NAME = "OpenBiliClaw-B4F3D2A1-7C6E-4A8B-9D1F-0E2A6C5B3D14"


def _acquire_installer_mutex() -> Any:
    """Create the named mutex Inno Setup's AppMutex check looks for.

    Best-effort and fail-open: any problem (non-Windows, dev run, ctypes
    failure) returns ``None`` and startup proceeds unaffected. The handle is
    intentionally never closed — Windows releases it automatically when the
    process exits (including crashes), which is exactly the lifetime the
    installer/uninstaller coordination needs. Acquiring an already-existing
    mutex is fine: parent and worker children share one name, and the mutex
    merely needs to exist while any packaged process runs.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32.CreateMutexW(None, False, _INSTALLER_MUTEX_NAME)
    except Exception:  # noqa: BLE001 — coordination only, never block startup
        return None


def _tray_icon_image() -> Any:
    """Load the canonical app icon for the Windows tray / macOS menu bar."""
    from PIL import Image

    from openbiliclaw import __file__ as package_init

    icon_path = Path(package_init).resolve().parent / "web" / "icon-192.png"
    with Image.open(icon_path) as source:
        return source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)


def _should_use_tray() -> bool:
    """Tray mode = a frozen Windows or macOS build with pystray + Pillow.

    Windows → system tray (bottom-right); macOS → menu-bar status item. Both run
    uvicorn in the background and the tray in the foreground. Other platforms
    (and dev runs) keep the simple foreground server.
    """
    if not getattr(sys, "frozen", False):
        return False
    if os.name != "nt" and sys.platform != "darwin":
        return False
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure → foreground fallback
        return False
    return True


def _run_server_in_tray(
    server: Any,
    host: str,
    port: int,
    project_root: Path,
    listener_sockets: list[Any] | None = None,
) -> None:
    """Run uvicorn in a background thread and a system-tray icon in the
    foreground (Windows). There is no console / window, so the only way to stop
    the backend is the tray menu's quit — which sets ``server.should_exit`` so
    uvicorn unwinds cleanly. The menu also opens the Web UI and the live logs.
    """
    import pystray

    browser_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
    web_url = f"http://{browser_host}:{port}/web/"
    log_path = project_root / "logs" / "desktop.log"

    def _serve() -> None:
        # A daemon thread dying silently leaves the tray up with a dead backend
        # ("后端已退出" and no clue) — the __main__ crash handler only sees the
        # main thread. Persist the traceback so the failure is diagnosable.
        try:
            if listener_sockets:
                server.run(sockets=listener_sockets)
            else:
                server.run()
        except Exception:
            import traceback

            with suppress(Exception):
                (project_root / "logs" / "crash.log").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
            print("[OpenBiliClaw] 后端服务线程异常退出,详见 logs/crash.log")
            traceback.print_exc()

    server_thread = threading.Thread(target=_serve, name="obc-uvicorn", daemon=True)
    server_thread.start()

    def _open_web(icon: Any, item: Any) -> None:
        with suppress(Exception):
            webbrowser.open(web_url)

    def _open_logs(icon: Any, item: Any) -> None:
        _view_runtime_logs(log_path)

    def _quit(icon: Any, item: Any) -> None:
        server.should_exit = True
        with suppress(Exception):
            icon.visible = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开 Web 界面", _open_web, default=True),
        pystray.MenuItem("查看运行日志", _open_logs),
        pystray.MenuItem("退出 OpenBiliClaw", _quit),
    )
    icon = pystray.Icon("OpenBiliClaw", _tray_icon_image(), "OpenBiliClaw", menu)
    # Backend is assembled and the tray icon is about to appear → drop the boot
    # splash now, handing visual feedback over to the tray with no perceptible gap.
    _close_splash()
    try:
        icon.run()  # blocks on the main thread until _quit calls icon.stop()
    finally:
        server.should_exit = True
        # Stop the ollama daemon we started so it (and its model runner) don't
        # linger as orphans after quit; a user-managed ollama is left untouched.
        with suppress(Exception):
            from openbiliclaw.runtime.ollama_supervisor import stop_managed_ollama

            stop_managed_ollama()
        with suppress(Exception):
            server_thread.join(timeout=5)


_LANDING_HEALTH_TIMEOUT_SECONDS = 30.0
_LANDING_HEALTH_POLL_SECONDS = 0.5

# Loopback probes must never detour through a system proxy: users with
# HTTP(S)_PROXY set but no 127.0.0.1 NO_PROXY entry would otherwise have the
# health wait answered (or hung) by the proxy instead of the local backend.
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _wait_for_backend_ready(
    base_url: str, timeout_seconds: float = _LANDING_HEALTH_TIMEOUT_SECONDS
) -> bool:
    """Poll ``/api/health`` until the backend answers.

    The static ``/setup/`` / ``/web/`` shells cannot self-retry a refused
    first GET, so the browser must only be pointed at a live server.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with (
            suppress(Exception),
            _LOOPBACK_OPENER.open(f"{base_url}/api/health", timeout=2) as response,  # noqa: S310
        ):
            if 200 <= response.status < 300:
                return True
        time.sleep(_LANDING_HEALTH_POLL_SECONDS)
    return False


def _fetch_backend_initialized(base_url: str) -> bool | None:
    """Read ``initialized`` from ``/api/init-status``; ``None`` when unknown.

    An UNinitialized backend answers this read only after running its real
    prereq probes (LLM / bilibili, gather-bounded by the slowest), which can
    take well over 5s on a cold start — keep the timeout generous or the
    /setup/ routing silently degrades to the /web fallback.
    """
    try:
        with _LOOPBACK_OPENER.open(f"{base_url}/api/init-status", timeout=15) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    value = payload.get("initialized") if isinstance(payload, dict) else None
    return value if isinstance(value, bool) else None


def _decide_landing_path(seeded: bool, repaired: bool, initialized: bool | None) -> str:
    """Pick the page the launcher opens.

    Fresh or repaired config → setup wizard. A configured relaunch whose init
    never completed must land on the wizard too — the old config-existence-only
    rule sent those users to ``/web/`` and relied entirely on the SPA's
    onboarding gate. Unknown init state (probe failed) keeps the ``/web/``
    fallback: the SPA gate is the safety net, not a wall.
    """
    if seeded or repaired:
        return "/setup/"
    if initialized is False:
        return "/setup/"
    return "/web/"


def _open_landing_page_when_ready(base_url: str, *, seeded: bool, repaired: bool) -> None:
    """Open the landing page once the backend is reachable (best effort).

    On health timeout the browser still opens — matching the old immediate-open
    behaviour rather than silently never showing a page.
    """
    healthy = _wait_for_backend_ready(base_url)
    initialized = _fetch_backend_initialized(base_url) if healthy else None
    landing = _decide_landing_path(seeded, repaired, initialized)
    with suppress(Exception):
        webbrowser.open(base_url + landing)


def _reconcile_packaged_autostart(runtime_config: Any) -> None:
    """Best-effort login-item reconciliation for the frozen desktop entry."""
    try:
        from openbiliclaw.runtime import autostart

        warning = autostart.reconcile(runtime_config)
    except Exception as exc:  # noqa: BLE001 - desktop startup must continue
        warning = f"开机自启动对账失败：{exc}"
    if warning:
        print(f"[OpenBiliClaw] {warning}")


def _packaged_tailnet_event_callback(event: dict[str, object]) -> None:
    """Surface the embedded tailnet helper lifecycle in desktop logs/browser."""

    def _open_login(url: str) -> None:
        with suppress(Exception):
            webbrowser.open(url)

    event_name = str(event.get("event", "")).strip()
    if event_name == "needs_login":
        login_url = str(event.get("auth_url", "")).strip()
        if not login_url:
            return
        print("[OpenBiliClaw] Tailnet 需要登录，正在打开一次性登录页面。")
        threading.Thread(
            target=_open_login,
            args=(login_url,),
            name="obc-tailnet-login",
            daemon=True,
        ).start()
        return
    if event_name == "ready":
        dns_name = str(event.get("dns_name", "")).strip()
        raw_ips = event.get("ips", [])
        ips = ",".join(str(value) for value in raw_ips) if isinstance(raw_ips, list) else ""
        port = event.get("port", 8420)
        print(f"[OpenBiliClaw] Tailnet 已就绪: dns={dns_name or '-'} ips={ips or '-'} port={port}")
        return
    if event_name == "error":
        print(f"[OpenBiliClaw] Tailnet 远程入口不可用: {event.get('message', 'unknown')}")


def _start_packaged_tailnet(runtime_config: Any, host: str, port: int) -> Any | None:
    """Start the optional bundled helper; a failure must not break local use."""
    if runtime_config is None or not bool(
        getattr(getattr(runtime_config, "tailnet", None), "enabled", False)
    ):
        return None
    if host.strip().lower() not in {"0.0.0.0", "127.0.0.1", "localhost"}:
        print(
            f"[OpenBiliClaw] Tailnet 未启动: API 绑定 {host}，"
            "helper 只会连接 127.0.0.1；本机服务继续。"
        )
        return None
    if not bool(getattr(getattr(runtime_config.api, "auth", None), "enabled", False)):
        print(
            "[OpenBiliClaw] 提醒: Tailnet 已开启但应用密码未开启;"
            "建议在本机 Web 的设置页开启局域网访问密码。"
        )
    from openbiliclaw.runtime.tailnet_supervisor import start_tailnet_if_enabled

    try:
        return start_tailnet_if_enabled(
            runtime_config,
            port,
            event_callback=_packaged_tailnet_event_callback,
        )
    except Exception as exc:  # noqa: BLE001 - local desktop startup must continue
        print(f"[OpenBiliClaw] Tailnet 未启动（本机服务继续）: {exc}")
        return None


def _worker_mode_requested() -> bool:
    """Return whether the four-process background-worker mode is enabled.

    The packaged desktop app now defaults to the same four-process backend
    layout as ``openbiliclaw start``: full worker, discovery worker,
    recommendation server and image proxy.  ``OPENBILICLAW_WORKER=0`` (or
    ``false`` / ``no`` / ``off``) disables the first three and keeps the image
    proxy child for compatibility with the CLI fallback mode.
    """
    value = os.environ.get("OPENBILICLAW_WORKER", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _spawn_backend_child(module_name: str, *, env: dict[str, str] | None) -> subprocess.Popen:
    """Spawn a Python backend child in source or frozen desktop mode."""
    from openbiliclaw.proc import no_window_kwargs

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--openbiliclaw-worker", module_name]
    else:
        cmd = [sys.executable, "-m", module_name]
    return subprocess.Popen(
        cmd,
        cwd=os.getcwd(),
        env=env,
        **no_window_kwargs(),
    )


def main() -> None:
    project_root, bundled_resources = _resolve_runtime_paths()
    # Windowed (no-console) build: route output to a log file FIRST, before any
    # print() runs (a windowed app's stdout is None and would raise). Creating
    # logs/ here also means legacy migration won't relocate the old logs/ dir
    # (disposable) — config.toml + data/ still migrate.
    with suppress(OSError):
        (project_root / "logs").mkdir(parents=True, exist_ok=True)
    _redirect_output_to_logfile(project_root)
    # Tell the user the launch registered. Windows shows the PyInstaller boot
    # splash (closed once the tray appears); macOS — where a menu-bar agent gives
    # no Dock bounce — gets a one-shot notification here instead.
    _notify_starting()
    project_root.mkdir(parents=True, exist_ok=True)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(project_root)

    # Crash recovery must run before any startup helper seeds config.toml or
    # creates data/. Either target may intentionally be absent halfway through
    # a journaled rename; recreating it here would occupy the restore target and
    # turn a recoverable crash into a fail-closed startup.
    migration_guard = None
    if not os.environ.get("OPENBILICLAW_SELFTEST"):
        from openbiliclaw.storage.migration import (
            acquire_migration_runtime_guard,
            apply_pending_migration,
            migration_recovery_data_dir,
        )

        recovery_data_dir = migration_recovery_data_dir(project_root=project_root)
        if recovery_data_dir is not None:
            migration_guard = acquire_migration_runtime_guard(
                project_root,
                recovery_data_dir,
            )
            if migration_guard is None:
                _close_splash()
                print("[OpenBiliClaw] 迁移目标数据目录已有后端在运行；本次不启动新实例。")
                return
            atexit.register(migration_guard.release)
            try:
                migration_result = apply_pending_migration(
                    project_root=project_root,
                    locked_data_dir=recovery_data_dir,
                )
            except Exception:
                migration_guard.release()
                raise
            if migration_result is not None:
                print(f"[OpenBiliClaw] {migration_result.message}")

    # New packaged builds share the same data root as one-line / AI installs.
    # If a previous packaged build used the old OS-specific app-data root, copy
    # its data into the unified root before seeding any defaults.
    _copy_legacy_packaged_user_data(_legacy_packaged_user_data_root(), project_root)
    # Very old Windows onedir builds: relocate any user data left in the install
    # dir into the per-user root, BEFORE we create fresh data/ (a whole-dir move
    # must not land inside a freshly-made empty dir). Source launches also use
    # the repository root as ``bundled_resources``; never treat its ignored
    # config/data as legacy packaged data when a selftest overrides the profile.
    if getattr(sys, "frozen", False):
        _migrate_legacy_install_dir_data(bundled_resources, project_root)

    # Ensure data & log directories exist
    (project_root / "data").mkdir(exist_ok=True)
    (project_root / "logs").mkdir(exist_ok=True)

    # Make a bundled ollama (if this build shipped one) discoverable before any
    # config is read, so the existing supervisor finds it via shutil.which.
    has_bundled_ollama = _inject_bundled_ollama_on_path(bundled_resources)

    # Seed a default config.toml if the user hasn't created one yet. On a fresh
    # packaged install with a bundled ollama, default embedding to it so local
    # semantic features work out of the box.
    seeded = _seed_default_config(project_root, bundled_resources)
    if seeded and has_bundled_ollama:
        _enable_ollama_embedding_default(project_root / "config.toml")
        _default_ollama_to_embedding_only(project_root / "config.toml")
    repair_result = _repair_unloadable_config(project_root, bundled_resources)
    if repair_result.regenerated_default and has_bundled_ollama:
        _enable_ollama_embedding_default(project_root / "config.toml")
        _default_ollama_to_embedding_only(project_root / "config.toml")

    # The packaged app bypasses `openbiliclaw start`, so set up the same
    # structured, rotated, UTF-8 `openbiliclaw.log` the CLI gets — otherwise the
    # only record is raw stdout in desktop.log, which is harder to triage and can
    # mojibake. Best-effort: the stdout→desktop.log redirect remains the fallback.
    # sweep_unmanaged=False so it won't truncate the live desktop.log mid-run.
    runtime_config = None
    with suppress(Exception):
        from openbiliclaw.config import load_config
        from openbiliclaw.logging_setup import configure_logging

        runtime_config = load_config()
        configure_logging(runtime_config, sweep_unmanaged=False)
        _reconcile_packaged_autostart(runtime_config)

    # Recovery initially locks the journal's historical target. Environment or
    # disk config may now select a different runtime data_dir; keep the first
    # lock and atomically add the effective one before any backend component can
    # open it.
    if migration_guard is not None and runtime_config is None:
        migration_guard.release()
        raise RuntimeError("迁移恢复后无法加载运行配置；后端拒绝在未锁定的数据目录上启动。")
    if migration_guard is not None and runtime_config is not None:
        try:
            runtime_data_locked = migration_guard.acquire_data_dir(runtime_config.data_path)
        except Exception:
            migration_guard.release()
            raise
        if not runtime_data_locked:
            migration_guard.release()
            _close_splash()
            print("[OpenBiliClaw] 迁移恢复后的数据目录已被另一后端使用；本次拒绝启动。")
            return

    default_host = "127.0.0.1"
    default_port = 8420
    if runtime_config is not None:
        default_host = str(
            getattr(runtime_config.api, "host", default_host) or default_host
        ).strip()
        default_port = int(getattr(runtime_config.api, "port", default_port) or default_port)
    host = os.environ.get("OPENBILICLAW_HOST", "").strip() or default_host
    port = int(os.environ.get("OPENBILICLAW_PORT", "").strip() or str(default_port))

    from openbiliclaw.api.app import create_app

    # Self-test mode: assemble the backend to prove every bundled
    # dependency imports and the app builds, then exit WITHOUT binding a
    # port. Lets CI / a local check smoke-test the packaged build even
    # when a real serve-api already owns the port.
    if os.environ.get("OPENBILICLAW_SELFTEST"):
        create_app()
        _close_splash()
        print("[OpenBiliClaw] selftest OK — 依赖与后端装配正常")
        return

    # Single-instance guard (packaged app only): a second launch — e.g. the user
    # double-clicking the icon — must not spin up a second backend + tray. Hold
    # the lock for the whole run (``lock_handle`` stays referenced until main()
    # returns); the OS frees it on exit. A busy lock means another instance owns
    # the port already, so just surface its Web UI and quit this launch.
    lock_handle = None
    if getattr(sys, "frozen", False):
        status, lock_handle = _try_single_instance_lock(project_root)
        if status == "busy":
            existing_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
            _close_splash()
            print("[OpenBiliClaw] 已有实例在运行;打开 Web 界面,本次不启动新后端。")
            # The other instance already owns the port, so it is safe to ask it
            # whether init ever completed and route to the wizard when not.
            existing_base = f"http://{existing_host}:{port}"
            landing = _decide_landing_path(False, False, _fetch_backend_initialized(existing_base))
            with suppress(Exception):
                webbrowser.open(existing_base + landing)
            return
    _ = lock_handle  # keep a reference so the lock is held for the process lifetime

    # The package lock above only covers frozen desktop builds.  This shared
    # guard also fences CLI/Docker servers that use the same project root and is
    # the proof required before a staged migration may replace SQLite + memory.
    from openbiliclaw.storage.migration import (
        acquire_migration_runtime_guard,
        apply_pending_migration,
    )

    if migration_guard is None:
        migration_data_dir = (
            runtime_config.data_path if runtime_config is not None else project_root / "data"
        )
        migration_guard = acquire_migration_runtime_guard(project_root, migration_data_dir)
        if migration_guard is None:
            existing_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
            _close_splash()
            print("[OpenBiliClaw] 当前数据目录已有后端在运行；本次不启动新实例。")
            with suppress(Exception):
                webbrowser.open(f"http://{existing_host}:{port}/web/")
            return
        atexit.register(migration_guard.release)
        try:
            migration_result = apply_pending_migration(
                project_root=project_root,
                locked_data_dir=migration_data_dir,
            )
        except Exception:
            migration_guard.release()
            raise
        if migration_result is not None:
            print(f"[OpenBiliClaw] {migration_result.message}")

    # A successful apply writes a portable config while preserving this
    # machine's listener/TLS/autostart fields. Re-read it before any runtime
    # services or browser URL are constructed.
    with suppress(Exception):
        from openbiliclaw.config import load_config
        from openbiliclaw.logging_setup import configure_logging

        runtime_config = load_config()
        configure_logging(runtime_config, sweep_unmanaged=False)
        _reconcile_packaged_autostart(runtime_config)
        default_host = str(getattr(runtime_config.api, "host", "127.0.0.1") or "127.0.0.1")
        default_port = int(getattr(runtime_config.api, "port", 8420) or 8420)
        host = os.environ.get("OPENBILICLAW_HOST", "").strip() or default_host
        port = int(os.environ.get("OPENBILICLAW_PORT", "").strip() or str(default_port))

    # with-embedding variant: seed the baked bge-m3 and bring up a private
    # Ollama for it BEFORE any other Ollama start. On success the default
    # preflight + online pull are superseded (embedding is already ready).
    if not _seed_bundled_embedding_model(bundled_resources, project_root / "config.toml"):
        # Packaged entry bypasses ``openbiliclaw start``, so run the same loopback
        # Ollama preflight here to bring up the (bundled) daemon when needed.
        _packaged_ollama_preflight()

        # With a bundled ollama, fetch the embedding weights once in the background
        # so a fresh install becomes embedding-ready without manual setup.
        if has_bundled_ollama:
            _ensure_embedding_model_async()

    print(f"[OpenBiliClaw] 数据目录: {project_root}")
    print(f"[OpenBiliClaw] 正在启动后端服务 http://{host}:{port} ...")

    # Landing page opens from a background thread only after /api/health
    # answers: opening before uvicorn binds showed ERR_CONNECTION_REFUSED, and
    # the static shells cannot retry a refused first GET. The thread also asks
    # /api/init-status so a configured-but-never-initialized relaunch lands on
    # the wizard instead of relying on /web's onboarding gate alone. When bound
    # to all interfaces, loopback is the address a local browser can hit.
    browser_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
    threading.Thread(
        target=_open_landing_page_when_ready,
        args=(f"http://{browser_host}:{port}",),
        kwargs={"seeded": seeded, "repaired": repair_result.repaired},
        name="obc-open-landing",
        daemon=True,
    ).start()

    # Start the server.  The desktop package now defaults to the four-process
    # backend layout: full worker + discovery worker + recommendation server +
    # image proxy, matching `openbiliclaw start` with OPENBILICLAW_WORKER=1.
    # Set the flag before create_app() so the API process skips its own
    # periodic/event-processing loops and delegates them to the children.
    worker_requested = _worker_mode_requested()
    if worker_requested:
        os.environ["OPENBILICLAW_FULL_WORKER"] = "1"
        data_path = (
            runtime_config.data_path if runtime_config is not None else project_root / "data"
        )
        ensure_recommendation_transport_env(data_path)

    import uvicorn

    app = create_app()
    use_tray = _should_use_tray()
    config_kwargs = {"access_log": False} if use_tray else {}
    config = uvicorn.Config(app, host=host, port=port, log_level="info", **config_kwargs)
    server = uvicorn.Server(config)
    from openbiliclaw.runtime.api_server import (
        close_listener_sockets,
        create_wildcard_listener_sockets,
    )

    listener_sockets = create_wildcard_listener_sockets(host, port)
    tailnet_supervisor = _start_packaged_tailnet(runtime_config, host, port)

    children: list[ManagedChild] = []
    if worker_requested:
        children.extend(
            [
                ManagedChild(
                    "full-worker",
                    lambda: _spawn_backend_child(
                        "openbiliclaw.worker",
                        env={**os.environ, "OPENBILICLAW_FULL_WORKER": "1"},
                    ),
                ),
                ManagedChild(
                    "discovery-worker",
                    lambda: _spawn_backend_child(
                        "openbiliclaw.discovery_worker",
                        env={
                            **os.environ,
                            "OPENBILICLAW_DISCOVERY_WORKER": "1",
                            "OPENBILICLAW_FULL_WORKER": "1",
                        },
                    ),
                ),
                ManagedChild(
                    "recommendation-server",
                    lambda: _spawn_backend_child(
                        "openbiliclaw.recommendation_server",
                        env={
                            **os.environ,
                            "OPENBILICLAW_RECOMMENDATION_ONLY": "1",
                            "OPENBILICLAW_FULL_WORKER": "1",
                        },
                    ),
                ),
            ]
        )

    # The image proxy is always a dedicated child, even in the legacy
    # single-API-process fallback, so image work cannot stall API serving.
    children.append(
        ManagedChild(
            "image-service",
            lambda: _spawn_backend_child("openbiliclaw.image_service", env={**os.environ}),
        )
    )
    child_supervisor = ChildProcessSupervisor(children)

    try:
        child_supervisor.start()
        image_service_port = os.environ.get("OPENBILICLAW_IMAGE_SERVICE_PORT", "8421")
        os.environ["OPENBILICLAW_IMAGE_SERVICE_URL"] = f"http://127.0.0.1:{image_service_port}"

        if use_tray:
            # Windowed build: uvicorn runs in the background and a tray icon owns the
            # foreground (Windows system tray / macOS menu bar). No console window
            # appears; closing nothing stops it — only the tray menu's "退出" quits.
            where = "系统托盘（右下角）" if os.name == "nt" else "菜单栏（右上角）"
            print(f"[OpenBiliClaw] 已最小化到{where}；右键托盘图标可查看日志或退出。")
            _run_server_in_tray(server, host, port, project_root, listener_sockets)
        else:
            # Dev / non-Windows / tray unavailable: run in the foreground (console).
            _close_splash()
            if listener_sockets:
                server.run(sockets=listener_sockets)
            else:
                server.run()
    finally:
        child_supervisor.stop()
        if tailnet_supervisor is not None:
            tailnet_supervisor.stop()
        close_listener_sockets(listener_sockets)
        migration_guard.release()


if __name__ == "__main__":
    # Installer coordination: hold the AppMutex for the whole process lifetime
    # (parent and --openbiliclaw-worker children alike — both are the packaged
    # exe whose files Setup/Uninstall overwrite/delete). Never closed; Windows
    # releases it automatically on process exit. See _acquire_installer_mutex.
    _installer_mutex = _acquire_installer_mutex()

    # Frozen desktop child processes are re-executions of the same packaged
    # executable.  Route them to the requested backend module before the normal
    # desktop main() (tray/splash/migration) runs.
    if (
        getattr(sys, "frozen", False)
        and len(sys.argv) >= 3
        and sys.argv[1] == "--openbiliclaw-worker"
    ):
        import runpy

        # Child re-executions of the same frozen EXE have no console too, so
        # send their logs to the same desktop.log as the parent.  Also close
        # the Windows boot splash as early as possible in each child.
        child_root = Path(
            os.environ.get("OPENBILICLAW_PROJECT_ROOT") or _resolve_runtime_paths()[0]
        )
        _redirect_output_to_logfile(child_root)
        _close_splash()
        try:
            runpy.run_module(sys.argv[2], run_name="__main__")
        except Exception:
            # Windowed builds turn any uncaught exception in a child into a
            # PyInstaller bootloader error dialog. Log the traceback to
            # desktop.log and exit with a plain SystemExit (which the
            # bootloader handles silently) so a crashed child stays
            # diagnosable instead of blocking startup with error boxes; the
            # parent-side supervisor respawns it with backoff.
            import traceback

            print(
                f"[OpenBiliClaw] backend child {sys.argv[2]} crashed; see logs/desktop.log",
                file=sys.stderr,
            )
            traceback.print_exc()
            raise SystemExit(1) from None
        raise SystemExit(0)

    try:
        main()
    except Exception:
        # Startup failed — drop the splash so it doesn't hang on screen forever,
        # then persist the crash (windowed builds have no console to print to).
        _close_splash()
        with suppress(Exception):
            import traceback

            crash_dir = _user_data_root() / "logs"
            crash_dir.mkdir(parents=True, exist_ok=True)
            (crash_dir / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
