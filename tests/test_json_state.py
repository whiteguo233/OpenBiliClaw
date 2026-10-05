from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.memory.json_state import update_json_state


class _CounterState:
    def __init__(self, count: int = 0) -> None:
        self.count = count

    @classmethod
    def from_dict(cls, raw: object) -> _CounterState:
        if not isinstance(raw, dict):
            return cls()
        return cls(count=int(raw.get("count", 0)))

    def to_dict(self) -> dict[str, int]:
        return {"count": self.count}


def test_update_json_state_reads_latest_on_each_update(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    update_json_state(
        path,
        default_factory=lambda: {"items": []},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"items": []},
        serialize=lambda state: state,
        mutate=lambda state: state["items"].append("a"),
    )
    update_json_state(
        path,
        default_factory=lambda: {"items": []},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"items": []},
        serialize=lambda state: state,
        mutate=lambda state: state["items"].append("b"),
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {"items": ["a", "b"]}


def test_update_json_state_recovers_from_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    state = update_json_state(
        path,
        default_factory=lambda: {"count": 0},
        normalize=lambda raw: raw if isinstance(raw, dict) else {"count": 0},
        serialize=lambda state: state,
        mutate=lambda state: state.update({"count": state["count"] + 1}),
    )

    assert state == {"count": 1}
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 1}


def test_update_json_state_serializes_typed_state_without_re_normalizing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "typed.json"

    first = update_json_state(
        path,
        default_factory=_CounterState,
        normalize=_CounterState.from_dict,
        serialize=lambda state: state.to_dict(),
        mutate=lambda state: setattr(state, "count", state.count + 1),
    )
    second = update_json_state(
        path,
        default_factory=_CounterState,
        normalize=_CounterState.from_dict,
        serialize=lambda state: state.to_dict(),
        mutate=lambda state: setattr(state, "count", state.count + 1),
    )

    assert first.count == 1
    assert second.count == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 2}


def test_atomic_write_json_retries_transient_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from openbiliclaw.memory import json_state
    from openbiliclaw.memory.json_state import _atomic_write_json

    path = tmp_path / "state.json"
    real_replace = os.replace
    attempts = 0
    sleeps: list[float] = []

    def flaky_replace(src: str, dst: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(json_state.os, "replace", flaky_replace)
    monkeypatch.setattr(json_state.time, "sleep", sleeps.append)

    _atomic_write_json(path, {"ok": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert attempts == 3
    assert len(sleeps) == 2


def test_atomic_write_json_raises_after_retries_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.memory import json_state
    from openbiliclaw.memory.json_state import _atomic_write_json

    path = tmp_path / "state.json"
    sleeps: list[float] = []

    def always_denied(src: str, dst: str) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(json_state.os, "replace", always_denied)
    monkeypatch.setattr(json_state.time, "sleep", sleeps.append)

    with pytest.raises(PermissionError):
        _atomic_write_json(path, {"ok": True})

    assert len(sleeps) == json_state._ATOMIC_WRITE_MAX_ATTEMPTS - 1
    assert list(tmp_path.glob("*.tmp")) == []
