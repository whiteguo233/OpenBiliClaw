#!/usr/bin/env python3
"""Real HTTP/SSE recovery through the signed SDK and encrypted fault relay."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from sponsored_concurrency_acceptance import JOB
from sponsored_resilience_acceptance import EncryptedRelay, FaultTrace

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings


class HttpFaults(FaultTrace):
    chat_attempts = 0
    streams_at_injection = 0
    label = ""

    async def write_frame(self, writer: Any, frame: dict[str, Any]) -> None:
        if self.mode == "silent_request" and frame.get("type") == "execute_stream":
            # The two free candidates of the first logical request see a
            # black hole. The durable retry gets the real network back.
            if self.chat_attempts < 2:
                self.relay.resume.clear()
                self.injected = True
            else:
                self.relay.resume.set()
        if frame.get("type") == "execute_stream":
            self.chat_attempts += 1
        token = JOB.set(self.label)
        try:
            await super().write_frame(writer, frame)
        finally:
            JOB.reset(token)

    async def read_frame(self, reader: Any) -> dict[str, Any]:
        injected_before = self.injected
        frame = await super().read_frame(reader)
        if self.injected and not injected_before:
            self.streams_at_injection = self.chat_attempts
        return frame


async def run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    verify_bundle(args.bundle.resolve())
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    os.environ["OPENBILICLAW_SPONSORED_BUNDLE"] = str(args.bundle.resolve())
    relay = EncryptedRelay()
    proxy_server = await asyncio.start_server(relay.accept, "127.0.0.1", 0)
    proxy_port = proxy_server.sockets[0].getsockname()[1]
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ[name] = f"http://127.0.0.1:{proxy_port}"
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    cfg = Config(data_dir=str(root / "data"))
    cfg.scheduler.enabled = cfg.scheduler.auto_update_enabled = False
    for source in vars(cfg.sources).values():
        if hasattr(source, "enabled"):
            source.enabled = False
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=True,
        model=args.model,
        fallback_to_other_free_model=True,
        fallback_to_user_provider=False,
    )
    save_config(cfg, root / "config.toml")
    await asyncio.to_thread(ensure_runtime)
    trace = HttpFaults(relay)
    trace.start()
    rows: list[dict[str, Any]] = []

    def save() -> None:
        (root / "http-resilience.json").write_text(
            json.dumps(
                {
                    "model": args.model,
                    "requests": rows,
                    "frames": list(trace.frames.values()),
                    "connections": relay.connections,
                    "encrypted_bytes": relay.bytes_forwarded,
                    "real_upstream": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    async def start() -> tuple[Any, Any, httpx.AsyncClient]:
        app = create_app()
        pool = shared_sponsored_provider()
        assert pool is not None
        trace.providers.append(pool)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        task = asyncio.create_task(server.serve())
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=180,
            trust_env=False,
            headers={"X-OBC-Auth": "1"},
        )
        return server, task, client

    async def stop(server: Any, task: Any, client: httpx.AsyncClient) -> None:
        await client.aclose()
        server.should_exit = True
        await asyncio.wait_for(task, 45)
        await close_shared_provider()

    async def create(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
        session = await client.post("/api/chat/sessions", json={"title": name})
        session.raise_for_status()
        body = {
            "turn_id": f"resilience-{name}",
            "session_id": session.json()["session_id"],
            "session": "desktop",
            "message": f"请原样回复标记 HTTP-RES-{name}，再解释一句断线重试。",
            "streaming": True,
        }
        if name == "partial":
            body["message"] += "然后写一篇1000字教程。"
        response = await client.post("/api/chat/turns", json=body)
        response.raise_for_status()
        return body

    async def stream(
        client: httpx.AsyncClient, body: dict[str, Any], *, disconnect: bool = False
    ) -> list[dict[str, Any]]:
        events = []
        name = ""
        async with client.stream("POST", "/api/chat/agent/stream", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append({"type": name, **data})
                    if disconnect and name == "delta":
                        break
        return events

    async def settled(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
        async with asyncio.timeout(150):
            while True:
                response = await client.get("/api/chat/turns/" + body["turn_id"])
                response.raise_for_status()
                value = response.json()
                if value["status"] != "pending":
                    return value
                await asyncio.sleep(0.25)

    server, task, client = await start()
    try:
        for name, mode in [
            ("durable", "silent_request"),
            ("disconnect", ""),
            ("partial", "drop_after"),
        ]:
            trace.mode, trace.attempt, trace.injected = mode, 0, False
            trace.chat_attempts, trace.label = 0, name
            token = JOB.set(name)
            row: dict[str, Any] = {"name": name}
            begin = time.monotonic()
            try:
                body = await create(client, name)
                events = await stream(client, body, disconnect=name == "disconnect")
                stored = await settled(client, body)
                row.update(
                    durable_status=stored["status"],
                    events=[e["type"] for e in events],
                    retry_events=sum(
                        e.get("type") == "sponsored_retry"
                        for e in stored["payload"].get("agent_events", [])
                    ),
                )
                if name == "partial":
                    assert trace.injected and stored["status"] == "failed"
                    assert trace.chat_attempts == trace.streams_at_injection
                    assert not stored["reply"]
                else:
                    assert stored["status"] == "completed"
                    assert f"HTTP-RES-{name}" in stored["reply"]
                if name == "durable":
                    assert any(e.get("retrying") is True for e in events)
                    assert row["retry_events"] >= 1 and trace.attempt >= 3
                if name == "disconnect":
                    assert any(e["type"] == "delta" for e in events)
                    assert not any(e["type"] == "done" for e in events)
                attempts_before = trace.chat_attempts
                replay = await stream(client, body)
                assert trace.chat_attempts == attempts_before, "reconnecting re-executed the turn"
                assert replay[-1]["type"] == ("error" if name == "partial" else "done")
                row.update(status="pass", replay_without_execution=True)
            except Exception as exc:
                row.update(status="fail", error_type=type(exc).__name__, error=str(exc)[:200])
            finally:
                relay.resume.set()
                row.update(seconds=round(time.monotonic() - begin, 3), attempts=trace.attempt)
                row["chat_attempts"] = trace.chat_attempts
                rows.append(row)
                trace.finish_job(name)
                save()
                print(json.dumps(row, ensure_ascii=False), flush=True)
                JOB.reset(token)
        trace.mode, trace.attempt = "", 0
        body = await create(client, "restart")
        await stop(server, task, client)
        server, task, client = await start()
        begin = time.monotonic()
        row = {"name": "restart_pending"}
        try:
            value = await settled(client, body)
            assert value["status"] == "completed" and "HTTP-RES-restart" in value["reply"]
            row.update(status="pass", durable_status=value["status"])
        except Exception as exc:
            row.update(status="fail", error_type=type(exc).__name__)
        rows.append({**row, "seconds": round(time.monotonic() - begin, 3)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    finally:
        relay.resume.set()
        await stop(server, task, client)
        trace.stop()
        proxy_server.close()
        await proxy_server.wait_closed()
        await relay.close()
        save()
    if any(row["status"] == "fail" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=("Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(parser.parse_args()))
