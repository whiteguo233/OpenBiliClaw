from __future__ import annotations

from pathlib import Path

from openbiliclaw.runtime.serve_outbox import ServeOutbox


def test_serve_outbox_appends_and_reads_batches(tmp_path: Path) -> None:
    outbox = ServeOutbox(tmp_path / "serve_outbox.jsonl")

    outbox.append(
        [{"bvid": "BV1", "expression": "hello"}],
        ["BV1"],
    )
    outbox.append(
        [{"bvid": "BV2", "expression": "world"}],
        ["BV2"],
    )

    records = outbox.read_all()
    assert len(records) == 2
    assert records[0]["ranked_bvids"] == ["BV1"]
    assert records[1]["recommendation_rows"][0]["bvid"] == "BV2"


def test_serve_outbox_clear_removes_records(tmp_path: Path) -> None:
    outbox = ServeOutbox(tmp_path / "serve_outbox.jsonl")
    outbox.append([{"bvid": "BV1"}], ["BV1"])

    outbox.clear()

    assert outbox.read_all() == []
    assert not (tmp_path / "serve_outbox.jsonl").exists()


def test_serve_outbox_missing_file_reads_empty(tmp_path: Path) -> None:
    outbox = ServeOutbox(tmp_path / "missing.jsonl")
    assert outbox.read_all() == []
