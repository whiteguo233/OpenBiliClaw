"""Staged B站 cookie while guided init owns the runtime.

``POST /api/bilibili/cookie`` must not validate (~30s probe) or swap the
runtime client mid-init, so historically a *changed* cookie pushed during
guided init was answered 409 and dropped — the extension's retry cadence
then decided whether the fix ever landed. This module persists such a push
to ``data/runtime/staged_bilibili_cookie.json`` instead; the API layer
applies it through the normal validate+persist path once guided init
completes, and exposes the pending state on ``/api/runtime-status`` so the
desktop can show it rather than only the extension's service-worker console.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_STAGED_COOKIE_FILENAME = "staged_bilibili_cookie.json"


def staged_cookie_path(data_dir: Path) -> Path:
    """Sit beside ``worker_status.json`` so every process resolves one path."""
    return data_dir / "runtime" / _STAGED_COOKIE_FILENAME


def stage_bilibili_cookie(data_dir: Path, *, cookie: str, source: str) -> None:
    """Persist *cookie* for application once guided init releases the runtime."""
    path = staged_cookie_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookie": cookie,
        "source": source,
        "staged_at": datetime.now(UTC).isoformat(),
    }
    # Unique tmp name per write (same race as the feedback state store: two
    # writers sharing one tmp name lose the rename to each other).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        with suppress(OSError):
            tmp.unlink()


def peek_staged_bilibili_cookie(data_dir: Path) -> dict[str, str] | None:
    """Read the staged cookie without consuming it; None when absent/invalid."""
    path = staged_cookie_path(data_dir)
    try:
        with open(path, encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    cookie = str(loaded.get("cookie") or "").strip()
    if not cookie:
        return None
    return {
        "cookie": cookie,
        "source": str(loaded.get("source") or "").strip() or "extension",
        "staged_at": str(loaded.get("staged_at") or ""),
    }


def clear_staged_bilibili_cookie(data_dir: Path) -> None:
    """Drop the staged cookie (applied, superseded, or definitively refused)."""
    with suppress(OSError):
        staged_cookie_path(data_dir).unlink()
