"""Docker runtime helpers for optional host proxy bootstrap."""

from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

_DEFAULT_PROXY_HOST = "host.docker.internal"
_DEFAULT_PROXY_PORT = 7897
_DEFAULT_PROXY_TIMEOUT = 1.0
_DEFAULT_RUNTIME_ROOT = "/app/runtime"
_DEFAULT_TEMPLATE_PATH = "/app/config.example.toml"
_DEFAULT_NO_PROXY_ENTRIES = ("127.0.0.1", "localhost", "host.docker.internal")
_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def bootstrap_runtime_root(
    *,
    runtime_root: Path,
    template_path: Path,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Create the isolated runtime root with config/data/logs when missing.

    When ``OPENBILICLAW_SEED_OLLAMA_DEFAULTS`` is set in ``env`` (the
    Docker compose file ships it on by default), the freshly-created
    config gets two values pre-filled so the bundled Ollama sidecar
    works out of the box:

      * ``[llm.ollama] base_url`` → ``OPENBILICLAW_OLLAMA_BASE_URL``
        (default ``http://ollama:11434/v1`` — the compose service name)
      * ``[llm.embedding] provider`` → ``ollama``
      * ``[llm.embedding] model`` → ``OPENBILICLAW_EMBEDDING_MODEL``
        (default ``bge-m3``)
      * ``[llm.embedding] base_url`` → ``OPENBILICLAW_OLLAMA_BASE_URL``

    An existing ``config.toml`` is never overwritten — users who already
    set up their own embedding stack keep their choices.
    """
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "data").mkdir(parents=True, exist_ok=True)
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)

    config_path = runtime_root / "config.toml"
    if config_path.exists() or not template_path.exists():
        return

    shutil.copyfile(template_path, config_path)

    resolved_env = env if env is not None else os.environ
    if str(resolved_env.get("OPENBILICLAW_SEED_OLLAMA_DEFAULTS", "")).strip():
        ollama_base = (
            resolved_env.get("OPENBILICLAW_OLLAMA_BASE_URL", "").strip() or "http://ollama:11434/v1"
        )
        embedding_model = resolved_env.get("OPENBILICLAW_EMBEDDING_MODEL", "").strip() or "bge-m3"
        _seed_ollama_defaults(config_path, ollama_base, embedding_model)


def _seed_ollama_defaults(
    config_path: Path,
    ollama_base_url: str,
    embedding_model: str,
) -> None:
    """Patch ``base_url`` under [llm.ollama] and provider/model under
    [llm.embedding] in a freshly-copied template config.

    Line-based editor: the config template only uses single-line string
    values for the fields we touch, so a small in-place edit is enough
    and we avoid pulling in a TOML writer dependency just for this.
    """
    text = config_path.read_text(encoding="utf-8")
    text = _set_toml_string(text, "llm.ollama", "base_url", ollama_base_url)
    text = _set_toml_string(text, "llm.embedding", "provider", "ollama")
    text = _set_toml_string(text, "llm.embedding", "model", embedding_model)
    text = _set_toml_string(text, "llm.embedding", "base_url", ollama_base_url)
    config_path.write_text(text, encoding="utf-8")


def _set_toml_string(content: str, section: str, key: str, value: str) -> str:
    """Replace ``key = "..."`` under ``[section]`` with ``key = "<value>"``.

    Appends both the section header and the key/value pair when missing,
    so the helper is idempotent on partial templates. Ignores commented
    lines and inline tables.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key} = "{escaped}"'
    section_header = f"[{section}]"

    lines = content.splitlines()
    in_section = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section_header
            continue
        if not in_section or stripped.startswith("#") or "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs == key:
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            lines[index] = f"{indent}{new_line}"
            trailing_newline = "\n" if content.endswith("\n") else ""
            return "\n".join(lines) + trailing_newline

    # Section/key didn't exist: append a fresh block at the end.
    suffix: list[str] = []
    if not content.endswith("\n"):
        suffix.append("")
    suffix.append(section_header)
    suffix.append(new_line)
    return content + "\n".join(suffix) + "\n"


def can_connect(host: str, port: int, timeout: float) -> bool:
    """Return whether a TCP endpoint is reachable."""
    with socket.create_connection((host, port), timeout=timeout):
        return True


def resolve_optional_proxy_env(
    env: dict[str, str] | os._Environ[str],
    *,
    can_connect: Callable[[str, int, float], bool] = can_connect,
    proxy_host: str = _DEFAULT_PROXY_HOST,
    proxy_port: int = _DEFAULT_PROXY_PORT,
    timeout: float = _DEFAULT_PROXY_TIMEOUT,
) -> dict[str, str]:
    """Return proxy env updates when a host-side Clash proxy is reachable."""
    if any(str(env.get(key, "")).strip() for key in _PROXY_KEYS):
        return {}

    if not can_connect(proxy_host, proxy_port, timeout):
        return {}

    proxy_url = f"http://{proxy_host}:{proxy_port}"
    no_proxy = _merge_no_proxy(env.get("NO_PROXY", "") or env.get("no_proxy", ""))
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _merge_no_proxy(current: str) -> str:
    """Merge required local bypass hosts into no_proxy."""
    entries = [item.strip() for item in current.split(",") if item.strip()]
    for entry in _DEFAULT_NO_PROXY_ENTRIES:
        if entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def is_running_in_container(env: MutableMapping[str, str] | None = None) -> bool:
    """Return whether this process is running inside a container runtime.

    The host-proxy auto-detection below is only safe inside a container,
    where ``host.docker.internal`` really does point to the host and is
    the only route to the internet.  On a native macOS developer
    machine Docker Desktop still resolves that name — so without this
    gate the bootstrapper routes every outbound request through the
    host's Clash proxy, which breaks Bilibili calls (and anything else
    that doesn't tolerate Clash's routing).
    """
    resolved_env = env if env is not None else os.environ
    if str(resolved_env.get("OPENBILICLAW_IN_CONTAINER", "")).strip():
        return True
    # Docker writes /.dockerenv; Podman writes /run/.containerenv.
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def bootstrap_runtime_environment(
    env: MutableMapping[str, str],
    *,
    can_connect: Callable[[str, int, float], bool] = can_connect,
    in_container: Callable[[MutableMapping[str, str]], bool] = is_running_in_container,
) -> None:
    """Bootstrap the isolated runtime root and optional proxy env in-place."""
    runtime_root = Path(env.get("OPENBILICLAW_PROJECT_ROOT", _DEFAULT_RUNTIME_ROOT))
    template_path = Path(env.get("OPENBILICLAW_CONFIG_TEMPLATE", _DEFAULT_TEMPLATE_PATH))
    bootstrap_runtime_root(
        runtime_root=runtime_root,
        template_path=template_path,
        env=env,
    )
    env.setdefault("OPENBILICLAW_PROJECT_ROOT", str(runtime_root))

    # Proxy auto-detection is ONLY safe inside container runtimes.
    if not in_container(env):
        return

    proxy_host = env.get("OPENBILICLAW_PROXY_HOST", _DEFAULT_PROXY_HOST).strip()
    proxy_port = int(env.get("OPENBILICLAW_PROXY_PORT", str(_DEFAULT_PROXY_PORT)))
    timeout = float(env.get("OPENBILICLAW_PROXY_TIMEOUT", str(_DEFAULT_PROXY_TIMEOUT)))
    env.update(
        resolve_optional_proxy_env(
            dict(env),
            can_connect=can_connect,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            timeout=timeout,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Bootstrap optional proxy settings, then exec the target command."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("usage: python -m openbiliclaw.docker_runtime <command> [args...]")

    bootstrap_runtime_environment(os.environ)
    os.execvpe(args[0], args, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
