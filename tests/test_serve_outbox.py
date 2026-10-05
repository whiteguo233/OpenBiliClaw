from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


async def test_drain_preserves_batches_appended_during_commit(tmp_path: Path) -> None:
    from openbiliclaw.worker.main import _drain_serve_outbox

    outbox = ServeOutbox(tmp_path / "serve_outbox.jsonl")
    outbox.append([{"bvid": "first"}], ["first"])
    persisted = []

    class Database:
        async def persist_pool_serve_async(self, rows, bvids):
            persisted.extend(bvids)
            outbox.append([{"bvid": "second"}], ["second"])

    await _drain_serve_outbox(Database(), tmp_path)
    assert persisted == ["first"]
    assert [r["ranked_bvids"] for r in outbox.read_all()] == [["second"]]


async def test_drain_preserves_failed_batch_for_retry(tmp_path: Path) -> None:
    from openbiliclaw.worker.main import _drain_serve_outbox

    outbox = ServeOutbox(tmp_path / "serve_outbox.jsonl")
    outbox.append([{"bvid": "retry"}], ["retry"])

    class Database:
        async def persist_pool_serve_async(self, rows, bvids):
            raise RuntimeError("writer unavailable")

    await _drain_serve_outbox(Database(), tmp_path)
    assert [r["ranked_bvids"] for r in outbox.read_all()] == [["retry"]]


async def test_drain_recovers_legacy_jsonl_and_preserves_new_spool(tmp_path: Path) -> None:
    import json

    from openbiliclaw.worker.main import _drain_serve_outbox

    path = tmp_path / "serve_outbox.jsonl"
    path.write_text(
        json.dumps({"recommendation_rows": [{"bvid": "legacy"}], "ranked_bvids": ["legacy"]}) + "\n"
    )
    outbox = ServeOutbox(path)
    persisted = []

    class Database:
        async def persist_pool_serve_async(self, rows, bvids):
            persisted.extend(bvids)
            outbox.append([{"bvid": "new"}], ["new"])

    await _drain_serve_outbox(Database(), tmp_path)
    assert persisted == ["legacy"]
    assert not path.exists()
    assert [r["ranked_bvids"] for r in outbox.read_all()] == [["new"]]
