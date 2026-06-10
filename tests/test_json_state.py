from __future__ import annotations

import json
from typing import TYPE_CHECKING

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
