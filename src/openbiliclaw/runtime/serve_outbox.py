"""Non-blocking outbox for recommendation serve writes.

Phase 2: the API process should not await SQLite writes on the
recommendation hot path. Instead it appends the small "shown/history"
payload to a local JSONL outbox and returns immediately. A separate worker
process drains the outbox with its own database connection.

This keeps the API process read-mostly while still giving the user a fast
response. The outbox is intentionally simple and local-first; no external
queues are required.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ServeOutbox:
    """Append/drain recommendation serve writes as JSONL batches."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def append(
        self,
        recommendation_rows: list[dict[str, Any]],
        ranked_bvids: list[str],
    ) -> None:
        """Append one serve batch to the outbox (called by API process)."""
        record = {
            "created_at": time.time(),
            "recommendation_rows": recommendation_rows,
            "ranked_bvids": ranked_bvids,
        }
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        """Return all complete JSONL records currently buffered."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                logger.warning("Skipping malformed serve outbox line")
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def clear(self) -> None:
        """Remove the outbox after a successful drain."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
