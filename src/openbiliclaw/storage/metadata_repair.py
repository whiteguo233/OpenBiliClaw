"""Recover missing metadata from matching local records without guessing facts."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from openbiliclaw.published_time import normalize_published_time
from openbiliclaw.sources.platforms import normalize_source_platform

FIELDS = ("author_name", "published_at", "description", "body_text", "tags")


def _valid(field: str, value: Any) -> str:
    if field == "tags":
        try:
            tags = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            return ""
        if not isinstance(tags, list):
            return ""
        names = list(
            dict.fromkeys(v.strip()[:128] for v in tags if isinstance(v, str) and v.strip())
        )[:32]
        return json.dumps(names, ensure_ascii=False) if names else ""
    if field == "published_at":
        return normalize_published_time(value).published_at
    if not isinstance(value, str):
        return ""
    limit = 8000 if field in {"description", "body_text"} else 256
    return value.strip()[:limit]


def _identity(row: dict[str, Any]) -> tuple[str, str] | None:
    platform = normalize_source_platform(row.get("source_platform", ""), default="unknown")
    if platform == "unknown":
        return None
    content_id = str(row.get("content_id") or row.get("bvid") or "").strip()
    # Never correlate by title, author, cover or partial URL.
    return (platform, content_id) if content_id else None


def repair_metadata(
    database: Path, *, apply: bool = False, backup: Path | None = None
) -> dict[str, Any]:
    """Preview/apply additive repairs with a consistent backup and race-safe writes.

    Only matching canonical source + content ID records can supply missing fields.
    Conflicting witnesses are left unresolved. Scores, statuses and user actions
    are never changed, and no upstream or model request is made.
    """
    if not database.is_file():
        raise ValueError("database does not exist")
    if apply and (backup is None or backup.exists() or backup.resolve() == database.resolve()):
        raise ValueError("apply requires a new backup path")
    db = sqlite3.connect(f"{database.resolve().as_uri()}?mode={'rw' if apply else 'ro'}", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if apply:
            assert backup is not None
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Reserve path exclusively; never overwrite a prior backup.
            with backup.open("xb"):
                pass
            backup.chmod(0o600)
            destination = sqlite3.connect(backup)
            try:
                db.backup(destination)
            finally:
                destination.close()
        records: list[tuple[str, dict[str, Any]]] = []
        available: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for table in ("discovery_candidates", "content_cache"):
            columns = {str(v[1]) for v in db.execute(f"PRAGMA table_info({table})")}
            if not {"bvid", "source_platform"} <= columns:
                continue
            selected = sorted(
                ({"rowid", "content_id", "bvid", "source_platform", "raw_payload"} | set(FIELDS))
                & (columns | {"rowid"})
            )
            projection = ",".join(
                "rowid AS repair_rowid" if col == "rowid" else col for col in selected
            )
            for result in db.execute(f"SELECT {projection} FROM {table}"):
                row = dict(result)
                identity = _identity(row)
                if identity is None:
                    continue
                records.append((table, row))
                # Only existing canonical fields, not arbitrary nested raw APIs.
                try:
                    raw = json.loads(row.get("raw_payload") or "{}")
                except (ValueError, TypeError):
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                for field in FIELDS:
                    for value in (row.get(field), raw.get(field)):
                        if valid := _valid(field, value):
                            available[identity][field].add(valid)
        planned: list[tuple[str, dict[str, Any], str, str]] = []
        counts: Counter[str] = Counter()
        unknown: Counter[str] = Counter()
        conflicts: Counter[str] = Counter()
        for table, row in records:
            identity = _identity(row)
            assert identity is not None
            for field in FIELDS:
                if field not in row or _valid(field, row.get(field)):
                    continue
                values = available[identity][field]
                label = f"{identity[0]}.{field}"
                if len(values) == 1:
                    planned.append((table, row, field, next(iter(values))))
                    counts[label] += 1
                elif len(values) > 1:
                    conflicts[label] += 1
                else:
                    unknown[label] += 1
        applied = 0
        if apply:
            with db:
                for table, row, field, value in planned:
                    # IS also handles NULL; a concurrent new value wins.
                    applied += db.execute(
                        f"UPDATE {table} SET {field}=? WHERE rowid=? AND {field} IS ?",
                        (value, row["repair_rowid"], row.get(field)),
                    ).rowcount
        return {
            "records": len(records),
            "planned_fields": len(planned),
            "applied_fields": applied,
            "recoverable": dict(counts),
            "unknown": dict(unknown),
            "conflicts": dict(conflicts),
            "network_requests": 0,
            "model_requests": 0,
        }
    finally:
        db.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            repair_metadata(args.database, apply=args.apply, backup=args.backup),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
