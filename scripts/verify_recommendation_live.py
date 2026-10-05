#!/usr/bin/env python3
"""Opt-in real HTTP/WebSocket/SQLite recommendation acceptance; consumes live stock."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

import httpx
import websockets


def percentile(values: list[float], q: float) -> float:
    return round(sorted(values)[max(0, math.ceil(len(values) * q) - 1)], 1) if values else 0.0


async def run(args: argparse.Namespace) -> dict[str, object]:
    events: list[dict[str, object]] = []
    health: list[float] = []
    samples: list[dict[str, object]] = []
    failures: list[str] = []
    seen: set[str] = set()
    stop = asyncio.Event()
    base = args.base_url.rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/api/runtime-stream"

    async def receive(ws: object) -> None:
        async for message in ws:
            event = json.loads(message)
            if event.get("type") == "refresh.pool_updated":
                events.append(
                    {
                        key: event[key]
                        for key in (
                            "pool_available_count",
                            "platform_available_counts",
                            "pool_status_version",
                        )
                        if key in event
                    }
                )

    async def probe() -> None:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=15) as client:
            while not stop.is_set():
                try:
                    start = time.perf_counter()
                    response = await client.get("/api/health")
                    response.raise_for_status()
                    health.append((time.perf_counter() - start) * 1000)
                except Exception as exc:
                    failures.append("health: " + type(exc).__name__)
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 0.2)

    async with (
        httpx.AsyncClient(base_url=base, trust_env=False, timeout=15) as client,
        websockets.connect(ws_url) as ws,
    ):
        listener = asyncio.create_task(receive(ws))
        probe_task = asyncio.create_task(probe())
        before_response = await client.get("/api/recommendations/platform-availability")
        before_response.raise_for_status()
        before = before_response.json()
        try:
            for index in range(args.requests):
                action = "reshuffle" if index % 2 == 0 else "append"
                scope = args.source_platform or ""
                payload: dict[str, object] = {"excluded_bvids": sorted(seen)}
                if scope:
                    payload["source_platform"] = scope
                start = time.perf_counter()
                stage = "mutation_http"
                try:
                    response = await client.post("/api/recommendations/" + action, json=payload)
                    elapsed = (time.perf_counter() - start) * 1000
                    response.raise_for_status()
                    stage = "committed_inventory"
                    body = response.json()
                    items, inventory = body["items"], body.get("pool_status")
                    assert inventory is not None, "missing committed inventory"
                    assert inventory["pool_available_count"] == sum(
                        inventory["platform_available_counts"].values()
                    ), "total/source mismatch"
                    identities = [str(item["bvid"]) for item in items]
                    assert len(set(identities)) == len(identities), "duplicate within batch"
                    assert not seen.intersection(identities), "duplicate between batches"
                    assert all(item["id"] > 0 for item in items), "non-persisted ID"
                    if scope:
                        assert all(item["source_platform"] == scope for item in items), (
                            "platform scope leak"
                        )
                    if args.database:
                        with sqlite3.connect(
                            Path(args.database).resolve().as_uri() + "?mode=ro",
                            uri=True,
                            timeout=10,
                        ) as db:
                            for item in items:
                                row = db.execute(
                                    "SELECT r.bvid,c.pool_status FROM recommendations r JOIN content_cache c ON c.bvid=r.bvid WHERE r.id=?",
                                    (item["id"],),
                                ).fetchone()
                                assert (
                                    row
                                    and row[0] == item["bvid"]
                                    and row[1] in ("shown", "feedbacked")
                                ), "response preceded commit"
                    seen.update(identities)
                    await asyncio.sleep(0.05)
                    mirrored = any(
                        event.get("pool_status_version") == inventory["pool_status_version"]
                        and event.get("platform_available_counts")
                        == inventory["platform_available_counts"]
                        for event in events
                    )
                    assert mirrored, "main WebSocket did not relay mutation inventory"
                    stage = "followup_availability_http"
                    check = (await client.get("/api/recommendations/platform-availability")).json()
                    assert check["total_available"] == sum(check["by_platform"].values()), (
                        "availability total mismatch"
                    )
                    sample = {
                        "index": index + 1,
                        "action": action,
                        "source": scope or "all",
                        "ms": round(elapsed, 1),
                        "items": len(items),
                        "has_more": body.get("has_more"),
                        "remaining": inventory["pool_available_count"],
                        "sources": inventory["platform_available_counts"],
                        "inventory_matches_followup": inventory["platform_available_counts"]
                        == check["by_platform"],
                        "websocket_matched": mirrored,
                    }
                    samples.append(sample)
                    print(json.dumps(sample, ensure_ascii=False), flush=True)
                    if inventory["pool_available_count"] <= 30:
                        break
                except Exception as exc:
                    failures.append(f"{action}[{index + 1}] {stage}: {type(exc).__name__}: {exc}")
                    print(failures[-1], flush=True)
                    break
        finally:
            stop.set()
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
            await probe_task
        after = (await client.get("/api/recommendations/platform-availability")).json()
    values = [float(sample["ms"]) for sample in samples]
    result = {
        "before": before,
        "after": after,
        "samples": samples,
        "unique_items": len(seen),
        "latency_ms": {
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "max": max(values, default=0),
        },
        "health_ms": {
            "samples": len(health),
            "p95": percentile(health, 0.95),
            "max": round(max(health, default=0), 1),
        },
        "pool_events": len(events),
        "pool_event_snapshots": events,
        "failures": failures,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("samples", "before", "after", "pool_event_snapshots")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8420")
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--source-platform", default="")
    parser.add_argument("--database", help="Optional read-only database commit verification")
    parser.add_argument("--output", required=True)
    result = asyncio.run(run(parser.parse_args()))
    raise SystemExit(bool(result["failures"]))
