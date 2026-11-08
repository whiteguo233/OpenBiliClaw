#!/usr/bin/env python3
"""Live HTTP/SSE burst alongside real evaluation and cloud embedding requests.

Run from the repository root with PYTHONPATH=src. All data is isolated under a
new --root. No provider results are mocked; request middleware only assigns
observation labels. A busy SSE channel may still complete via the durable worker,
so record both transport outcomes and the final persisted turn state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import uvicorn
from sponsored_concurrency_acceptance import JOB, Run, Trace, peak_overlap

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class LabelRequests:
    """Propagate observation labels without modifying HTTP or model payloads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        label = dict(scope.get("headers", [])).get(b"x-concurrency-probe", b"").decode()
        token = JOB.set(label)
        try:
            await self.app(scope, receive, send)
        finally:
            JOB.reset(token)


async def run_http(args: argparse.Namespace) -> None:
    model = args.model
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    bundle = args.bundle.resolve()
    verify_bundle(bundle)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    os.environ["OPENBILICLAW_SPONSORED_BUNDLE"] = str(bundle)
    cfg = Config(data_dir=str(root / "data"))
    cfg.scheduler.enabled = False
    cfg.scheduler.auto_update_enabled = False
    for source in vars(cfg.sources).values():
        if hasattr(source, "enabled"):
            source.enabled = False
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=True,
        model=model,
        fallback_to_other_free_model=args.free_fallback,
        fallback_to_user_provider=False,
    )
    save_config(cfg, root / "config.toml")
    command = await asyncio.to_thread(ensure_runtime)
    app = create_app()
    app.add_middleware(LabelRequests)
    trace = Trace()
    run = Run(root, model, command, trace, free_fallback=args.free_fallback)
    run.service = app.state.runtime_context.llm_service
    trace.start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(300):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(0.1)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=240,
            trust_env=False,
            headers={"X-OBC-Auth": "1"},
        ) as client:

            async def chat(i: int) -> dict[str, Any]:
                name = f"http-chat-{i}"
                marker = f"HTTP-鲸鱼-{i}-3957"
                row: dict[str, Any] = {"name": name, "kind": "http_chat", "submitted": trace.now()}
                try:
                    session = await client.post("/api/chat/sessions", json={"title": name})
                    session.raise_for_status()
                    turn_id = f"concurrency-{time.time_ns()}-{i}"
                    row["turn_id"] = turn_id
                    row["marker"] = marker
                    body = {
                        "turn_id": turn_id,
                        "session_id": session.json()["session_id"],
                        "session": "desktop",
                        "message": f"请先原样写 {marker}，然后用一句话解释异步请求排队。",
                        "streaming": True,
                    }
                    saved = await client.post("/api/chat/turns", json=body)
                    saved.raise_for_status()
                    event = ""
                    events = []
                    async with client.stream(
                        "POST",
                        "/api/chat/agent/stream",
                        json=body,
                        headers={"X-Concurrency-Probe": name},
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.startswith("event: "):
                                event = line[7:]
                            elif line.startswith("data: "):
                                payload = json.loads(line[6:])
                                events.append({"event": event, "data": payload})
                                if event == "delta":
                                    row.setdefault("first_content", trace.now())
                    failures = [e for e in events if e["event"] == "error"]
                    assert not failures, str(failures)[:300]
                    done = [e["data"] for e in events if e["event"] == "done"]
                    assert done and marker in done[-1].get("reply", ""), str(done)[:300]
                    read = await client.get(f"/api/chat/turns/{turn_id}")
                    read.raise_for_status()
                    assert read.json()["status"] == "completed"
                    assert read.json()["reply"] == done[-1]["reply"]
                    row.update(
                        status="pass",
                        durable_reply_matches=True,
                        event_counts={
                            n: sum(e["event"] == n for e in events)
                            for n in {e["event"] for e in events}
                        },
                    )
                except Exception as exc:
                    row.update(status="fail", error_type=type(exc).__name__, error=str(exc)[:500])
                finally:
                    trace.finish_job(name)
                    row["finished"] = trace.now()
                    row["elapsed_seconds"] = round(row["finished"] - row["submitted"], 3)
                    if "first_content" in row:
                        row["first_content_seconds"] = round(
                            row["first_content"] - row["submitted"], 3
                        )
                    run.report["requests"].append(row)
                    run.save()
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                return row

            stopped = asyncio.Event()
            pings = []

            async def ping() -> None:
                while not stopped.is_set():
                    t = time.monotonic()
                    try:
                        response = await client.get("/api/ping", timeout=3)
                        pings.append(
                            {"ok": response.is_success, "seconds": round(time.monotonic() - t, 4)}
                        )
                    except Exception as exc:
                        pings.append({"ok": False, "error_type": type(exc).__name__})
                    await asyncio.sleep(0.5)

            ping_task = asyncio.create_task(ping())
            begin = trace.now()
            results = await asyncio.gather(
                run.job("http-eval-0", "evaluation"),
                run.job("http-eval-1", "evaluation"),
                chat(0),
                chat(1),
                run.job("http-embedding-0", "embedding"),
                run.job("http-embedding-1", "embedding"),
            )
            for row in results:
                if row.get("turn_id"):
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        response = await client.get("/api/chat/turns/" + row["turn_id"])
                        response.raise_for_status()
                        durable = response.json()
                        if durable["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(0.5)
                    row["durable_status"] = durable["status"]
                    row["durable_reply_matches"] = row["marker"] in (durable.get("reply") or "")
                    row["durable_error"] = durable.get("error")
                    row["durable_observed_seconds"] = round(trace.now() - row["submitted"], 3)
                    run.save()
            stopped.set()
            await ping_task
            run.report["http_summary"] = {
                "passed": sum(r["status"] == "pass" for r in results),
                "total": len(results),
                "seconds": round(trace.now() - begin, 3),
                "pings": pings,
                "gate": run.service.concurrency_gate.status_payload(),
            }
            # Report durable job completion above; shutdown owns any follow-up learning.
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 45)
        finally:
            try:
                await close_shared_provider()
            finally:
                for frame in trace.frames.values():
                    if frame["finished"] is None:
                        frame.update(finished=trace.now(), interrupted=True)
                run.report["peak_ipc_inflight"] = peak_overlap(list(trace.frames.values()))
                trace.stop()
                run.save()
    summary = {k: v for k, v in run.report.get("http_summary", {}).items() if k != "pings"}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if any(r["status"] == "fail" for r in run.report["requests"]):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=("Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--free-fallback", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_http(args))


if __name__ == "__main__":
    main()
