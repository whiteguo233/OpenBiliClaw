"""Read-only learned-vs-LLM shadow calibration gate.

Never initializes or migrates the database, never calls an LLM or embedding
provider, and never changes ``eval_scorer``. It snapshots a maximum audit id
inside one read transaction so the evaluated cohort is stable while the daemon
appends newer learned-vs-LLM rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.discovery.eval_scorer_audit import evaluate_learned_scorer_gate  # noqa: E402

_TABLE = "evaluator_learned_scorer_shadow_audit"


def _read_frozen_rows(
    db_path: Path,
    *,
    after_id: int,
    through_id: int | None,
) -> tuple[list[dict[str, Any]], int, str]:
    """Read one stable retained cohort without mutating the database."""
    if not db_path.is_file():
        return [], 0, "database_missing"
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_TABLE,),
        ).fetchone()
        if exists is None:
            connection.commit()
            return [], 0, "audit_table_missing"
        current_max = int(
            connection.execute(
                f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {_TABLE}"  # noqa: S608
            ).fetchone()["max_id"]
            or 0
        )
        requested_max = max(0, through_id) if through_id is not None else current_max
        frozen_max = min(current_max, requested_max)
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT decision_id, candidate_hash, platform_class, context_class,
                       learned_score, llm_score, admission_threshold, admission_result,
                       features_digest
                FROM {_TABLE}
                WHERE id > ? AND id <= ?
                ORDER BY id ASC
                """,  # noqa: S608
                (max(0, after_id), max(0, frozen_max)),
            ).fetchall()
        ]
        connection.commit()
        return rows, frozen_max, "ok"
    finally:
        connection.close()


def _repository_metadata() -> dict[str, object]:
    """Return privacy-safe local revision metadata for the gate artifact."""
    commit = "unknown"
    dirty = True
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        )
        commit = commit_result.stdout.strip() or "unknown"
        status_result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        )
        dirty = bool(status_result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"commit": commit, "dirty": dirty}


def _cohort_digest(rows: list[dict[str, Any]]) -> str:
    decision_ids = "\n".join(str(row.get("decision_id") or "") for row in rows)
    return hashlib.sha256(decision_ids.encode()).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the read-only learned-vs-LLM shadow quality gate.",
    )
    parser.add_argument("--db", type=Path, required=True, help="Path to openbiliclaw.db")
    parser.add_argument(
        "--after-id", type=int, default=0,
        help="Exclude rows at or below this audit id.",
    )
    parser.add_argument(
        "--through-id", type=int, default=None,
        help="Optional inclusive frozen upper audit id; defaults to current maximum.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Optional path for the sanitized aggregate JSON artifact.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows, frozen_max, source_status = _read_frozen_rows(
        args.db, after_id=args.after_id, through_id=args.through_id,
    )
    report = evaluate_learned_scorer_gate(rows).to_dict()
    report["cohort"] = {
        "after_id": max(0, int(args.after_id)),
        "through_id": frozen_max,
        "source_status": source_status,
        "digest": _cohort_digest(rows),
    }
    report["repository"] = _repository_metadata()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if bool(report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
