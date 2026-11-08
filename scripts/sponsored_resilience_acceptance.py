#!/usr/bin/env python3
"""Fault-test the signed Rust bundle against real SiliconFlow requests.

A loopback CONNECT relay forwards encrypted TLS bytes without decrypting or
recording credentials/content. It can close or pause only its own sockets.
No fake model responses, global network changes, or paid fallback are used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shlex
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from sponsored_concurrency_acceptance import JOB, Trace

from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS, approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle


class EncryptedRelay:
    """Forward only CONNECT api.siliconflow.cn:443; never terminate TLS."""

    def __init__(self) -> None:
        self.writers: set[asyncio.StreamWriter] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.resume = asyncio.Event()
        self.resume.set()
        self.connections = 0
        self.bytes_forwarded = 0
        self.drops = 0

    async def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        upstream_writer = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            assert header.split(b"\r\n", 1)[0] == b"CONNECT api.siliconflow.cn:443 HTTP/1.1"
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "api.siliconflow.cn", 443
            )
            self.connections += 1
            self.writers.update((writer, upstream_writer))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                while data := await source.read(65536):
                    await self.resume.wait()
                    target.write(data)
                    await target.drain()
                    self.bytes_forwarded += len(data)

            pumps = [
                asyncio.create_task(pipe(reader, upstream_writer)),
                asyncio.create_task(pipe(upstream_reader, writer)),
            ]
            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, AssertionError):
            pass
        finally:
            for stream in (writer, upstream_writer):
                if stream:
                    self.writers.discard(stream)
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            self.tasks.discard(task)

    def disconnect(self) -> None:
        self.drops += 1
        for writer in list(self.writers):
            writer.transport.abort()

    async def close(self) -> None:
        self.resume.set()
        self.disconnect()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)


class FaultTrace(Trace):
    def __init__(self, relay: EncryptedRelay) -> None:
        super().__init__()
        self.relay = relay
        self.mode = ""
        self.attempt = 0
        self.injected = False
        self.pending: list[asyncio.Task[None]] = []

    async def write_frame(self, writer: Any, frame: dict[str, Any]) -> None:
        execute = frame.get("type") in {"execute", "execute_stream"}
        if execute:
            self.attempt += 1
            if self.mode == "silence":
                if self.attempt == 1:
                    self.relay.resume.clear()
                    self.injected = True
                else:
                    self.relay.resume.set()
        await super().write_frame(writer, frame)
        if execute and self.attempt == 1 and self.mode in {"drop_before", "crash"}:

            async def interrupt() -> None:
                await asyncio.sleep(0.1)
                self.injected = True
                if self.mode == "crash":
                    for provider in self.providers:
                        process = provider._process
                        if process and process.stdin is writer and process.returncode is None:
                            process.kill()
                else:
                    self.relay.disconnect()

            self.pending.append(asyncio.create_task(interrupt()))

    async def read_frame(self, reader: Any) -> dict[str, Any]:
        frame = await super().read_frame(reader)
        if self.mode == "drop_after" and frame.get("type") == "delta" and not self.injected:
            self.injected = True
            self.relay.disconnect()
        return frame


async def run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    bundle = args.bundle.resolve()
    verify_bundle(bundle)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    os.environ["OPENBILICLAW_SPONSORED_BUNDLE"] = str(bundle)
    command = shlex.split(await asyncio.to_thread(ensure_runtime))
    relay = EncryptedRelay()
    server = await asyncio.start_server(relay.accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    proxy = f"http://127.0.0.1:{port}"
    provider = SponsoredProvider(
        command,
        registry=approved_registry(),
        model=args.model,
        fallback_to_other_free_model=True,
        env={
            "HTTPS_PROXY": proxy,
            "HTTP_PROXY": proxy,
            "https_proxy": proxy,
            "http_proxy": proxy,
            "NO_PROXY": "",
            "no_proxy": "",
        },
    )
    trace = FaultTrace(relay)
    trace.providers.append(provider)
    results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "model": args.model,
        "runtime_manifest_sha256": hashlib.sha256(
            (bundle / "manifest.json").read_bytes()
        ).hexdigest(),
        "real_upstream": True,
        "fault_scope": "isolated encrypted CONNECT sockets or owned Rust child",
        "requests": results,
    }

    def save() -> None:
        report.update(
            frames=list(trace.frames.values()),
            connections=relay.connections,
            encrypted_bytes=relay.bytes_forwarded,
        )
        (root / "resilience.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    async def job(name: str, mode: str = "", kind: str = "stream") -> None:
        trace.mode, trace.attempt, trace.injected = mode, 0, False
        token = JOB.set(name)
        start = time.monotonic()
        connections_before = relay.connections
        row: dict[str, Any] = {"name": name, "fault": mode, "kind": kind}
        deltas: list[str] = []
        response = None
        marker = f"RES-{args.model.rsplit('/', 1)[-1]}-{name}"
        try:
            async with asyncio.timeout(220):
                if kind == "embedding":
                    vector = await provider.embedding(marker + " 并发稳定性测试")
                    assert len(vector) == 1024 and any(vector)
                    row["dimensions"] = len(vector)
                else:
                    prompt = f"先原样写 {marker}，再用一句话说明断线重试。"
                    if mode == "drop_after":
                        prompt = f"先原样写 {marker}，然后写2000字的Rust并发教程。"
                    stream = provider.stream_complete(
                        [{"role": "user", "content": prompt}],
                        caller="agent.chat",
                        max_tokens=3000 if mode == "drop_after" else 200,
                        temperature=0,
                    )
                    try:
                        async for chunk in stream:
                            if chunk.delta:
                                row.setdefault(
                                    "first_delta_seconds", round(time.monotonic() - start, 3)
                                )
                                deltas.append(chunk.delta)
                            if chunk.response:
                                response = chunk.response
                    finally:
                        await stream.aclose()
                    assert response is not None and marker in response.content
                    assert "".join(deltas) == response.content
                    row.update(
                        actual_model=response.model,
                        free_fallback=response.raw.get("free_model_fallback"),
                    )
                    if mode == "silence":
                        assert response.model != args.model and trace.attempt == 2
                    if mode == "drop_after":
                        raise AssertionError("interrupted stream was incorrectly accepted")
                assert relay.connections > 0 and relay.bytes_forwarded > 0
                if mode:
                    assert trace.injected
                row["status"] = "pass"

        except Exception as exc:
            row.update(
                status="fail", error_type=type(exc).__name__, error_code=getattr(exc, "code", "")
            )
            if (
                mode == "drop_after"
                and trace.injected
                and deltas
                and response is None
                and trace.attempt == 1
            ):
                row["status"] = "pass_expected_interruption"
        finally:
            relay.resume.set()
            await asyncio.gather(*trace.pending, return_exceptions=True)
            trace.pending.clear()
            trace.finish_job(name)
            row.update(
                seconds=round(time.monotonic() - start, 3),
                ipc_attempts=trace.attempt,
                injected=trace.injected,
                delta_chunks=len(deltas),
                new_tls_connections=relay.connections - connections_before,
            )
            results.append(row)
            save()
            print(json.dumps(row, ensure_ascii=False), flush=True)
            JOB.reset(token)

    trace.start()
    try:
        await job("warm", kind="embedding")
        await job("socket-before", "drop_before")
        await job("silent-primary", "silence")
        await job("child-crash", "crash")
        await job("socket-after", "drop_after")
        await job("recovered-chat")
        await job("recovered-embedding", kind="embedding")
        await job("embedding-drop", "drop_before", kind="embedding")
        await job("final-chat")
    finally:
        await provider.aclose()
        trace.stop()
        server.close()
        await server.wait_closed()
        await relay.close()
        save()
    if any(row["status"] == "fail" for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=CHAT_MODELS, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(parser.parse_args()))
