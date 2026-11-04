#!/usr/bin/env python3
"""Continue live acceptance on synthetic data with a real HTTP/SSE server.

Run sponsored_system_acceptance.py first. This starts the production ASGI
lifespan and exercises durable chat, cognition, settings switching and restart
persistence in its isolated directory. No provider or business mocks are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from sponsored_system_acceptance import Run

from openbiliclaw.api.app import create_app
from openbiliclaw.config import load_config
from openbiliclaw.llm.service import _background_admission_bypass
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS
from openbiliclaw.llm.sponsored_router import close_shared_provider


async def run_http(root: Path, port: int, keep_seconds: int) -> None:
    assert (root / "config.toml").is_file(), "Use an existing isolated acceptance directory"
    assert (root / "acceptance.json").is_file(), "Run the synthetic business acceptance first"
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    cfg = load_config(consult_environment=False)
    assert Path(cfg.data_dir).resolve() == root / "data", "Refusing non-isolated application data"
    assert cfg.llm.sponsored.enabled
    assert not cfg.llm.sponsored.fallback_to_user_provider
    assert not cfg.llm.sponsored.fallback_to_other_free_model
    model = cfg.llm.sponsored.model
    run = Run(root, model, report_name="http-acceptance.json")
    app = create_app()
    run.ctx = app.state.runtime_context
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        if serving.done():
            await serving
            raise RuntimeError("HTTP server stopped before startup")
        await asyncio.sleep(0.1)
    assert server.started
    print(json.dumps({"server": f"http://127.0.0.1:{port}", "model": model}), flush=True)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=480
        ) as client:

            async def pages() -> dict[str, Any]:
                result = {}
                for path in ["/api/health", "/api/profile-summary", "/web/", "/m/"]:
                    response = await client.get(path, follow_redirects=True)
                    response.raise_for_status()
                    result[path] = {"status": response.status_code, "bytes": len(response.content)}
                assert (await client.get("/api/health")).json()["profile_ready"]
                return result

            await run.case("real_server_startup_and_user_surfaces", pages)

            async def chat() -> dict[str, Any]:
                session = await client.post("/api/chat/sessions", json={"title": "免费模型验收"})
                session.raise_for_status()
                session_id = session.json()["session_id"]
                outputs = []
                for i, message in enumerate(
                    [
                        "本次测试代号是蓝色番茄731。请用两句话解释 Rust 借用的作用，记住代号供下一条核对。",
                        "我上一条给你的完整测试代号是什么？只回复代号。",
                    ]
                ):
                    turn_id = f"free-http-{int(time.time())}-{i}"
                    body = {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "session": "desktop",
                        "message": message,
                        "streaming": True,
                    }
                    created = await client.post("/api/chat/turns", json=body)
                    created.raise_for_status()
                    events = []
                    event_name = ""
                    start = time.monotonic()
                    first_content = None
                    async with client.stream(
                        "POST", "/api/chat/agent/stream", json=body
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.startswith("event: "):
                                event_name = line[7:]
                            elif line.startswith("data: "):
                                data = json.loads(line[6:])
                                events.append({"event": event_name, "data": data})
                                if event_name == "delta" and first_content is None:
                                    first_content = round(time.monotonic() - start, 3)
                    assert not [e for e in events if e["event"] == "error"], events
                    done = [e["data"] for e in events if e["event"] == "done"]
                    assert done and done[-1].get("reply"), events
                    assert first_content is not None, "No incremental content delta arrived"
                    turn = await client.get(f"/api/chat/turns/{turn_id}")
                    turn.raise_for_status()
                    assert turn.json()["status"] == "completed", turn.json()
                    assert turn.json()["reply"] == done[-1]["reply"]
                    if i == 1:
                        assert "蓝色番茄731" in done[-1]["reply"], done[-1]
                    outputs.append(
                        {
                            "first_content_seconds": first_content,
                            "elapsed_seconds": round(time.monotonic() - start, 3),
                            "reply": done[-1]["reply"],
                            "event_counts": {
                                name: sum(e["event"] == name for e in events)
                                for name in sorted({e["event"] for e in events})
                            },
                            "persisted": True,
                        }
                    )
                return {"turns": outputs}

            await run.case("real_sse_two_turn_chat_and_durable_history", chat)

            async def cognition() -> dict[str, Any]:
                with _background_admission_bypass():
                    # Advance only the isolated fixture's throttle clock; the
                    # model, prompts, ledger writes and embeddings remain real.
                    result = await run.ctx.soul_engine._cognition_cycle.run_if_due(
                        now=datetime.now() + timedelta(hours=13)
                    )
                assert not result.errors, result.errors
                assert result.ran and result.total_awareness_after > 0, result
                return asdict(result)

            await run.case("awareness_and_insight_cognition_cycle", cognition)

            async def learning_drain() -> dict[str, Any]:
                queue = run.ctx.dialogue_settlement_queue
                try:
                    await queue.pause_and_drain(timeout=360)
                finally:
                    queue.resume()
                return {
                    "drained": True,
                    "dialogue_events": len(
                        [
                            e
                            for e in run.ctx.memory_manager.query_events(limit=100)
                            if e["event_type"] == "dialogue"
                        ]
                    ),
                }

            await run.case("post_chat_learning_queue_drained", learning_drain)

            alternate = next(m for m in CHAT_MODELS if m != model)
            for selected in (alternate, model):

                async def switch(selected: str = selected) -> dict[str, Any]:
                    apply_start = time.monotonic()
                    response = await client.put(
                        "/api/config",
                        json={"llm": {"sponsored": {"enabled": True, "model": selected}}},
                    )
                    response.raise_for_status()
                    for _ in range(300):
                        status = (await client.get("/api/config/apply-status")).json()
                        assert status.get("state") != "failed", status
                        if (
                            status.get("state") in {"idle", "applied"}
                            and run.ctx.config.llm.sponsored.model == selected
                        ):
                            break
                        await asyncio.sleep(0.1)
                    assert status.get("state") in {"idle", "applied"}, status
                    assert run.ctx.config.llm.sponsored.model == selected
                    apply_seconds = round(time.monotonic() - apply_start, 3)
                    probe = await client.post(
                        "/api/config/probe-service", json={"kind": "sponsored"}
                    )
                    probe.raise_for_status()
                    result = probe.json()
                    result["apply_seconds"] = apply_seconds
                    assert result["ok"] and result["model"] == selected, result
                    assert load_config(consult_environment=False).llm.sponsored.model == selected
                    return result

                # A background call admitted before the reload may finish using
                # the previous model; the live probe must use the selected one.
                await run.case(
                    "save_apply_live_probe_" + selected.split("/")[-1],
                    switch,
                    allowed_models=tuple(CHAT_MODELS),
                )

            if keep_seconds:
                print(
                    json.dumps({"ready_for_browser_seconds": keep_seconds, "port": port}),
                    flush=True,
                )
                await asyncio.sleep(keep_seconds)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 45)
        finally:
            await close_shared_provider()
            run.finish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--keep-seconds", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_http(args.root.resolve(), args.port, args.keep_seconds))


if __name__ == "__main__":
    main()
