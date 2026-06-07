"""Desktop application entry point for OpenBiliClaw.

This module bootstraps the local backend server as a standalone
desktop application packaged via PyInstaller.

The layout differs between onedir and macOS ``.app`` bundle outputs:

* **onedir** (``dist/OpenBiliClaw/OpenBiliClaw``) — the executable and its
  bundled resources (``config.example.toml``, ``ollama``) live in the install
  directory, which Setup overwrites on upgrade and may remove on uninstall.
  User data therefore lives in a per-user root
  (``%LOCALAPPDATA%\\OpenBiliClaw`` on Windows); any data an older build left
  next to the executable is migrated there on first launch.
* **macOS .app** (``OpenBiliClaw.app/Contents/MacOS/OpenBiliClaw``) —
  the bundle itself is treated as read-only.  User data must live
  outside the bundle, by macOS convention in
  ``~/Library/Application Support/OpenBiliClaw``.  The bundled default
  template ``config.example.toml`` is placed under ``Contents/Resources``
  by PyInstaller and seeded into the user's data dir on first launch.

In both packaged layouts the read-only bundle provides the template config +
``ollama`` while user data lives under :func:`_user_data_root`.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


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

    Uses each OS's conventional per-user location, independent of the install
    directory:

    * Windows — ``%LOCALAPPDATA%\\OpenBiliClaw``
    * macOS   — ``~/Library/Application Support/OpenBiliClaw``
    * other   — ``$XDG_DATA_HOME/OpenBiliClaw`` (``~/.local/share`` fallback)
    """
    if os_name == "nt":
        base = environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / "OpenBiliClaw"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "OpenBiliClaw"
    base = environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / "OpenBiliClaw"


def _user_data_root() -> Path:
    """Return the per-user, writable data root for the packaged app.

    Kept independent of the (upgrade-overwritten, uninstall-removed) install
    directory. See :func:`_user_data_root_for` for the per-OS mapping.
    """
    return _user_data_root_for(os.name, sys.platform, Path.home(), os.environ)


# Names older builds wrote next to the executable under ``{app}``; relocated to
# the per-user data root on the first launch of a relocation-aware build.
_LEGACY_DATA_ENTRIES = ("config.toml", "data", "logs")


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


def _resolve_runtime_paths() -> tuple[Path, Path]:
    """Return ``(project_root, bundled_resources)`` based on launch mode.

    ``project_root`` is where ``config.toml`` / ``data/`` / ``logs/`` live.
    ``bundled_resources`` is the read-only directory holding the default
    ``config.example.toml`` (and bundled ``ollama``) shipped with the package.
    """
    if not getattr(sys, "frozen", False):
        # Development fallback
        repo_root = Path(__file__).resolve().parent.parent
        return repo_root, repo_root

    exe_dir = Path(sys.executable).resolve().parent
    if _is_macos_app_bundle(exe_dir):
        project_root = _user_data_root()
        bundled_resources = exe_dir.parent / "Resources"
        return project_root, bundled_resources

    # onedir layout (Windows): the install dir is overwritten on upgrade and may
    # be removed on uninstall, so user data lives in a per-user root instead of
    # next to the executable. The install dir still provides the bundled template
    # config + ollama as read-only resources.
    project_root = _user_data_root()
    bundled_resources = exe_dir
    return project_root, bundled_resources


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
    """Blank a preset ``[llm.ollama] model`` so local Ollama is embedding-only.

    config.example.toml ships ``[llm.ollama] model = "qwen2.5:7b"``. Per
    ``registry._ollama_is_chat_capable``, a non-empty model marks Ollama
    chat-capable, so the chat chain probes qwen2.5:7b — which the packaged user
    hasn't pulled — flooding the console with ``ollama request failed: 404``.
    The packaged app defaults chat to a cloud provider and only wants Ollama for
    bge-m3 embedding, so clear the chat model. Skipped when the user actually
    defaulted chat to ollama; the wizard sets the model back if they pick it.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
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
        if not _ollama_start_serve_background():
            print("[OpenBiliClaw] Ollama 未能自动拉起；embedding 可能降级。")
    except Exception as exc:  # noqa: BLE001 — preflight must never crash startup
        print(f"[OpenBiliClaw] Ollama preflight 跳过: {exc}")


def _ensure_embedding_model_async() -> None:
    """Pull the local embedding model in the background if it's missing.

    Honours the "bundle the runtime, fetch the 568MB weights once" approach: a
    fresh machine reaches embedding-ready on its own without the user running
    ``setup-embedding``. Runs in a daemon thread so the 568MB download never
    blocks the API; reuses the battle-tested ``cli`` pull helpers (already in the
    bundle). No-op when embedding isn't Ollama or the model is already present.
    """

    def _worker() -> None:
        try:
            from openbiliclaw.config import load_config

            emb = load_config().llm.embedding
            if str(emb.provider).strip().lower() != "ollama":
                return
            model = str(emb.model).strip() or "bge-m3"
            from openbiliclaw.cli import _ollama_has_model, _ollama_pull_model

            if _ollama_has_model(model):
                return
            print(f"[OpenBiliClaw] 后台拉取本地 embedding 模型 {model}(约 568MB,仅首次)…")
            if _ollama_pull_model(model):
                print(f"[OpenBiliClaw] 本地 embedding 模型 {model} 就绪")
        except Exception as exc:  # noqa: BLE001 — background best-effort only
            print(f"[OpenBiliClaw] 模型自动拉取跳过: {exc}")

    threading.Thread(target=_worker, name="obc-embed-pull", daemon=True).start()


def main() -> None:
    project_root, bundled_resources = _resolve_runtime_paths()
    # Windows onedir upgrades: relocate any user data older builds left in the
    # install dir into the per-user root, BEFORE we create fresh data/ + logs/
    # (a whole-dir move must not land inside a freshly-made empty dir).
    _migrate_legacy_install_dir_data(bundled_resources, project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(project_root)

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

    host = os.environ.get("OPENBILICLAW_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENBILICLAW_PORT", "8420"))

    from openbiliclaw.api.app import create_app

    # Self-test mode: assemble the backend to prove every bundled
    # dependency imports and the app builds, then exit WITHOUT binding a
    # port. Lets CI / a local check smoke-test the packaged build even
    # when a real serve-api already owns the port.
    if os.environ.get("OPENBILICLAW_SELFTEST"):
        create_app()
        print("[OpenBiliClaw] selftest OK — 依赖与后端装配正常")
        return

    # Packaged entry bypasses ``openbiliclaw start``, so run the same loopback
    # Ollama preflight here to bring up the (bundled) daemon when needed.
    _packaged_ollama_preflight()

    # With a bundled ollama, fetch the embedding weights once in the background
    # so a fresh install becomes embedding-ready without manual setup.
    if has_bundled_ollama:
        _ensure_embedding_model_async()

    print(f"[OpenBiliClaw] 数据目录: {project_root}")
    print(f"[OpenBiliClaw] 正在启动后端服务 http://{host}:{port} ...")

    # First launch → open the step-by-step setup wizard; afterwards → the app
    # itself. When bound to all interfaces, loopback is the address a local
    # browser can actually hit.
    landing = "/setup/" if seeded else "/web/"
    browser_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
    with suppress(Exception):
        webbrowser.open(f"http://{browser_host}:{port}{landing}")

    # Start the server
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
