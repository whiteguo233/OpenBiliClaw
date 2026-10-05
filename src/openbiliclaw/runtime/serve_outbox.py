"""Compatibility queue for serve batches left by older worker installations.

Interactive recommendations now commit directly before returning. Queued legacy
batches are still drained safely; each new queue file is immutable and is only
acknowledged after its own database commit.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ServeOutbox:
    """Persist and acknowledge independent serve batches without truncating writers."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.spool = self.path.with_name(self.path.name + ".batches")

    def append(
        self,
        recommendation_rows: list[dict[str, Any]],
        ranked_bvids: list[str],
    ) -> None:
        """Atomically publish one batch; in-progress files are never drained."""
        self.spool.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {
            "created_at": time.time(),
            "recommendation_rows": recommendation_rows,
            "ranked_bvids": ranked_bvids,
        }
        fd, temporary = tempfile.mkstemp(dir=self.spool, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.spool / f"{time.time_ns()}-{uuid.uuid4().hex}.jsonl")
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        # A malformed batch must remain available for diagnosis, never be ACKed
        # as an apparently successful empty batch.
        records = [json.loads(line) for line in lines if line.strip()]
        if not all(isinstance(record, dict) for record in records):
            raise ValueError("Invalid serve outbox batch")
        return records

    def read_all(self) -> list[dict[str, Any]]:
        """Read published records, including the pre-upgrade JSONL file."""
        return [
            record
            for path in [self.path, *sorted(self.spool.glob("*.jsonl"))]
            for record in self._read(path)
        ]

    def claim_batches(self) -> list[tuple[Path, list[dict[str, Any]]]]:
        """Freeze legacy input and return independently acknowledgeable batches.

        New writers only publish to the spool, so renaming the legacy file is
        safe after upgrading/restarting the old API and worker together.
        """
        if self.path.exists():
            self.spool.mkdir(mode=0o700, parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                self.path.rename(self.spool / f"legacy-{uuid.uuid4().hex}.jsonl")
        return [(path, self._read(path)) for path in sorted(self.spool.glob("*.jsonl"))]

    def acknowledge(self, batch: Path) -> None:
        """Delete only the successfully committed immutable batch."""
        if batch.parent != self.spool or batch.suffix != ".jsonl":
            raise ValueError("Invalid serve outbox acknowledgement")
        with contextlib.suppress(FileNotFoundError):
            batch.unlink()

    def count(self) -> int:
        """Return the number of buffered serve records."""
        return len(self.read_all())

    def clear(self) -> None:
        """Explicitly discard a snapshot of queued batches (not used by drain)."""
        for batch, _records in self.claim_batches():
            self.acknowledge(batch)
