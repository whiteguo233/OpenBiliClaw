"""Cold-host ID recovery trusts only another current-generation app session."""

import json

from openbiliclaw.sources.ego_executor import _current_task_owners


def test_recycled_id_evidence_excludes_stale_foreign_malformed_and_symlink_states(tmp_path):
    current = {
        "task_id": 1,
        "task_name": "OpenBiliClaw-" + "a" * 32,
        "host": "managed-host",
        "host_pid": 101,
        "profile": "Default",
    }
    (tmp_path / "current.json").write_text(json.dumps(current))
    for index, changes in enumerate(
        [
            {"host_pid": 100},
            {"host": "other-host"},
            {"profile": "Other"},
            {"task_id": 2},
            {"task_name": "User space"},
        ]
    ):
        (tmp_path / f"foreign-{index}.json").write_text(json.dumps({**current, **changes}))
    (tmp_path / "link.json").symlink_to(tmp_path / "current.json")
    (tmp_path / "bad.json").write_text("[]")
    (tmp_path / "large.json").write_text(" " * 17000)
    assert _current_task_owners(
        tmp_path, task_id=1, host="managed-host", pid=101, profile="Default"
    ) == [{key: current[key] for key in ("task_id", "task_name", "profile")}]
    assert (
        _current_task_owners(
            tmp_path, task_id=None, host="managed-host", pid=101, profile="Default"
        )
        == []
    )
