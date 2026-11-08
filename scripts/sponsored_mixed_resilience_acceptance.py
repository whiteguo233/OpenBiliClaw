#!/usr/bin/env python3
"""Interrupt encrypted upstream sockets while three real Rust workers are busy."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from sponsored_concurrency_acceptance import Run, Trace
from sponsored_resilience_acceptance import EncryptedRelay

from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle


async def run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    verify_bundle(args.bundle.resolve())
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    os.environ["OPENBILICLAW_SPONSORED_BUNDLE"] = str(args.bundle.resolve())
    relay = EncryptedRelay()
    server = await asyncio.start_server(relay.accept, "127.0.0.1", 0)
    proxy = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        os.environ[key] = proxy
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = ""

    class BurstTrace(Trace):
        injected = False
        fault_task: asyncio.Task[None] | None = None

        async def write_frame(self, writer: Any, frame: dict[str, Any]) -> None:
            await super().write_frame(writer, frame)
            if len(self.frames) == 3 and not self.injected:
                self.injected = True

                async def drop() -> None:
                    await asyncio.sleep(0.15)
                    relay.disconnect()

                self.fault_task = asyncio.create_task(drop())

    trace = BurstTrace()
    run = Run(root, args.model, await asyncio.to_thread(ensure_runtime), trace, free_fallback=True)
    trace.start()
    try:
        stage = await run.stage(
            "fault-mixed", ["evaluation", "evaluation", "chat", "embedding", "chat", "embedding"]
        )
        if trace.fault_task:
            await trace.fault_task
        run.report["injected_network_faults"] = relay.drops
        run.report["encrypted_connections"] = relay.connections
        assert trace.injected and relay.drops == 1
        assert stage["passed"] == stage["submitted"] and all(stage["checks"].values())
    finally:
        await close_shared_provider()
        trace.stop()
        server.close()
        await server.wait_closed()
        await relay.close()
        run.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=CHAT_MODELS, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(parser.parse_args()))
