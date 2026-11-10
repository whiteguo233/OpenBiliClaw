"""Run one real-provider cognition pass and report the prompt payload (issue #342).

The cognition prompt used to serialize ``preference.archived_interests`` — an
append-only lifecycle ledger — on every call, so prompt cost grew with account
age instead of with current understanding. This driver runs the exact
production path (``MemoryManager`` → ``LLMService`` → ``AwarenessAnalyzer`` →
``CognitionCycle``) against a prepared data directory, injects a synthetic
ledger *into the in-memory preference layer* (persisted storage is never
touched), captures the outbound request plus the provider-reported usage, and
writes a JSON report. Point ``PYTHONPATH`` at the build under test to compare
arms with an identical command.

Preparation — clone the real install, never mutate it:

```bash
D=/tmp/obc-342-e2e/fixed
mkdir -p "$D/data/memory"
sed 's#^data_dir = .*#data_dir = "'"$D"'/data"#' config.toml > "$D/config.toml"
cp -c data/openbiliclaw.db "$D/data/openbiliclaw.db"
for f in preference soul soul_profile profile_overrides awareness insight feedback_state; do
  cp -c "data/memory/$f.json" "$D/data/memory/$f.json" 2>/dev/null || true
done
```

Then, from the checkout whose build you want to exercise:

```bash
PYTHONPATH=src python scripts/cognition_prompt_payload_e2e.py \
    --config "$D/config.toml" --ledger-rows 4752 --events 6 --out "$D/report.json"
```
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_SENTINEL = "ZZ-ARCHIVE-LEDGER-SENTINEL"
_SENTINEL_CATEGORY = "生活"
# Old-but-valid timestamp so the throttle sees the cycle as due without
# perturbing the prompt cache prefix (the timestamp is not prompt input).
_OLD_ISO = "2020-01-01T00:00:00"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="config.toml pointing at the clone")
    parser.add_argument("--out", required=True, help="where to write the JSON report")
    parser.add_argument(
        "--ledger-rows",
        type=int,
        default=4752,
        help="synthetic archived_interests rows injected in memory (0 disables)",
    )
    parser.add_argument(
        "--events",
        type=int,
        default=6,
        help="how many newest events the awareness pass should consume",
    )
    parser.add_argument(
        "--arm",
        default="",
        help="label for this arm (e.g. fixed / baseline); recorded in the report",
    )
    return parser.parse_args()


def _max_event_id(db_path: Path) -> int:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT MAX(id) FROM events").fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def _force_due(data_dir: Path, *, events: int, max_id: int) -> dict[str, Any]:
    """Rewrite the cycle state so awareness is due and insight/cleanup are not."""
    now_iso = datetime.now().isoformat()
    state = {
        "last_awareness_event_id": max(0, max_id - max(1, events)),
        "last_awareness_at": _OLD_ISO,
        "last_insight_awareness_index": 0,
        "last_insight_at": now_iso,
        "last_cleanup_at": now_iso,
    }
    path = data_dir / "memory" / "cognition_cycle_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _inject_ledger(memory: Any, rows: int) -> dict[str, Any]:
    """Insert synthetic archive rows into the in-memory preference layer."""
    layer = memory.get_layer("preference")
    data = layer.data
    active = [item for item in data.get("interests") or [] if isinstance(item, dict)]
    template = dict(active[0]) if active else {"category": _SENTINEL_CATEGORY, "weight": 0.2}
    ledger: list[dict[str, Any]] = []
    for index in range(rows):
        entry = dict(template)
        base_name = str(template.get("name") or "归档兴趣")
        entry["name"] = f"{base_name}-长尾-{index:04d}"
        entry["category"] = _SENTINEL_CATEGORY
        entry["weight"] = round(0.012 + (index % 7) * 0.001, 4)
        entry["archived_at"] = "2026-03-01T12:00:00"
        entry["archived_reason"] = "inventory_archive"
        entry["has_archive_sentinel"] = index == 0
        if index == 0:
            entry["name"] = f"{_SENTINEL}-0000"
        ledger.append(entry)
    data["archived_interests"] = ledger
    encoded = json.dumps(ledger, ensure_ascii=False)
    return {
        "rows": len(ledger),
        "serialized_bytes": len(encoded.encode("utf-8")),
        "avg_row_bytes": round(len(encoded.encode("utf-8")) / len(ledger), 1) if ledger else 0,
        "sentinel": ledger[0]["name"] if ledger else "",
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.llm.usage_recorder import UsageRecorder
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.cognition_cycle import CognitionCycle
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer
    from openbiliclaw.storage.database import Database

    config = load_config(args.config)
    data_dir = Path(config.data_path)
    db_path = data_dir / "openbiliclaw.db"
    database = Database(db_path)
    database.initialize()
    memory = MemoryManager(data_dir, database=database)
    memory.initialize()

    ledger = _inject_ledger(memory, args.ledger_rows)
    preference = memory.get_layer("preference").data
    state = _force_due(data_dir, events=args.events, max_id=_max_event_id(db_path))

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        last_usage_id = int(
            connection.execute("SELECT COALESCE(MAX(id), 0) FROM llm_usage").fetchone()[0]
        )
    finally:
        connection.close()

    registry = build_llm_registry(config)
    service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=UsageRecorder(sink=database),
        module_overrides=module_overrides_from_config(config),
        concurrency=config.llm.concurrency,
    )

    calls: list[dict[str, Any]] = []
    original = service.complete_structured_task

    async def _capturing(**kwargs: Any) -> Any:
        user_input = str(kwargs.get("user_input") or "")
        system_instruction = str(kwargs.get("system_instruction") or "")
        record: dict[str, Any] = {
            "caller": kwargs.get("caller", ""),
            "system_bytes": len(system_instruction.encode("utf-8")),
            "user_bytes": len(user_input.encode("utf-8")),
            "user_sha256": hashlib.sha256(user_input.encode("utf-8")).hexdigest(),
            "user_has_archived_key": "archived_interests" in user_input,
            "user_has_ledger_sentinel": _SENTINEL in user_input,
            "user_preview": user_input[:280],
        }
        started = time.monotonic()
        try:
            response = await original(**kwargs)
        except Exception as exc:  # noqa: BLE001 - the report needs the real failure
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["elapsed_seconds"] = round(time.monotonic() - started, 2)
            calls.append(record)
            raise
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        record["model"] = response.model
        record["provider"] = response.provider
        record["instance_id"] = response.instance_id
        record["usage"] = response.usage
        calls.append(record)
        return response

    service.complete_structured_task = _capturing  # type: ignore[method-assign]

    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=AwarenessAnalyzer(
            service,
            plain_prompt_view="legacy",
            confusions_prompt_view="compact-v1",
        ),
        insight_analyzer=InsightAnalyzer(service, cognition_prompt_view="legacy"),
        min_interval_seconds=0,
        awareness_event_batch_size=max(1, args.events),
    )

    started = time.monotonic()
    error = ""
    result: Any = None
    try:
        result = await cycle.run_if_due()
    except Exception as exc:  # noqa: BLE001 - record, do not hide
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 2)

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        usage_rows = [
            {
                "caller": row[0],
                "provider": row[1],
                "model": row[2],
                "prompt_tokens": row[3],
                "completion_tokens": row[4],
                "total_tokens": row[5],
                "estimated_cost_cny": row[6],
                "success": bool(row[7]),
            }
            for row in connection.execute(
                "SELECT caller, provider, model, prompt_tokens, completion_tokens, "
                "total_tokens, estimated_cost_cny, success FROM llm_usage "
                "WHERE id > ? ORDER BY id",
                (last_usage_id,),
            )
        ]
    finally:
        connection.close()

    return {
        "arm": args.arm,
        "config": str(Path(args.config)),
        "data_dir": str(data_dir),
        "events_consumed": args.events,
        "cycle_state": state,
        "ledger": ledger,
        "preference_interests": len(preference.get("interests") or []),
        "awareness_prompt_view": "compact-v1",
        "cycle_result": dataclasses.asdict(result) if result is not None else None,
        "cycle_error": error,
        "cycle_elapsed_seconds": elapsed,
        "calls": calls,
        "usage_rows": usage_rows,
        "python": __import__("sys").version.split()[0],
        "openbiliclaw": __import__("openbiliclaw").__file__,
    }


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run(args))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("arm", "ledger", "calls", "usage_rows")},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
