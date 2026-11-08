#!/usr/bin/env python3
"""Live regression checks on a disposable sponsored_system_acceptance profile.

Uses the installed signed SDK, real Agent tools, cognition persistence and HTTP
settings probes. No provider substitutes or alternate-model fallback are allowed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from sponsored_system_acceptance import Run

from openbiliclaw.api.app import create_app
from openbiliclaw.config import load_config
from openbiliclaw.llm.service import _background_admission_bypass


async def run_checks(root: Path, report_name: str) -> None:
    assert (root / "acceptance.json").is_file(), "Use an isolated baseline profile"
    assert not (root / report_name).exists(), "Preserve previous evidence"
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    cfg = load_config(consult_environment=False)
    assert Path(cfg.data_dir).resolve() == root / "data"
    assert cfg.llm.sponsored.enabled
    assert not cfg.llm.sponsored.fallback_to_other_free_model
    assert not cfg.llm.sponsored.fallback_to_user_provider
    run = Run(root, cfg.llm.sponsored.model, report_name)
    app = create_app()
    ctx = run.ctx = app.state.runtime_context
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        assert not serving.done(), "Server exited during startup"
        await asyncio.sleep(0.1)
    assert server.started
    try:

        async def exact_memory() -> dict[str, Any]:
            records = {f"record_{index:04d}": f"普通记录{index:04d}" for index in range(1200)}
            records.update(record_0731="翠竹59427", record_0847="青杉68219")
            layer = ctx.memory_manager.get_layer("event")
            layer.update("release_test_records", records)
            layer.save()
            outputs = []
            for key in ("record_0731", "record_0847"):
                events = [
                    event.to_dict()
                    async for event in ctx.agent_loop.run(
                        system_instruction=(
                            "你是记忆查询助手。必须调用 read_memory 精确读取指定路径的原值。"
                            "不要读取或猜测整层，不修改数据。最终只回复该value原文，不增加空格或解释。"
                        ),
                        user_message=f"请读取event层/release_test_records/{key}的原值。",
                    )
                ]
                tools = [event for event in events if event["type"] == "tool_result"]
                assert tools and all(event.get("ok", False) for event in tools), events
                assert any(
                    event.get("tool_name") == "read_memory"
                    and json.loads(event["text"]).get("value") == records[key]
                    for event in tools
                ), events
                finals = [event["text"] for event in events if event["type"] == "final"]
                assert finals and finals[-1].strip() == records[key], events
                outputs.append({"key": key, "events": events})
            return {"stored_records": len(records), "queries": outputs}

        await run.case("agent_exact_values_in_1200_record_memory", exact_memory)

        async def fresh_awareness() -> dict[str, Any]:
            cycle = ctx.soul_engine._cognition_cycle
            state = cycle._load_state()
            old_max = ctx.database.conn.execute("SELECT max(id) FROM events").fetchone()[0]
            state["last_awareness_event_id"] = old_max
            before = cycle._load_awareness_notes()
            batch = [
                ctx.database.insert_event(
                    "view",
                    source_platform="bilibili",
                    title=f"蓝晒手工验收：{subject}",
                    context=f"用户今天浏览蓝晒手工的{subject}教程；只确认浏览，未知完成度。",
                    metadata={"event_time": time.time(), "time_quality": "source"},
                )
                for subject in ("感光液调配", "棉布涂布", "植物摆放", "日晒显影")
            ]
            assert old_max and min(batch) > old_max
            with _background_admission_bypass():
                added = await cycle._run_awareness(state)
            after = cycle._load_awareness_notes()
            new_notes = after[len(before) :]
            assert added and new_notes, "No new awareness was persisted"
            assert state["last_awareness_event_id"] == max(batch)
            assert cycle._load_state()["last_awareness_event_id"] == max(batch)
            assert all(
                note.source_event_ids and set(note.source_event_ids) <= set(batch)
                for note in new_notes
            ), [asdict(note) for note in new_notes]
            return {
                "historical_max_id": old_max,
                "batch_ids": batch,
                "added": added,
                "notes": [asdict(note) for note in new_notes],
                "watermark": max(batch),
            }

        await run.case("fresh_cognition_with_historical_lookback", fresh_awareness)

        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=180
        ) as client:

            async def provider_probes() -> dict[str, Any]:
                config = await client.get("/api/config")
                config.raise_for_status()
                free = config.json()["llm"]["sponsored"]
                assert free["builtin"] and free["provider_id"] == "siliconflow_free"
                assert len(free["models"]) == 2 and free["runtime_available"]
                assert "api_key" not in free and "runtime_path" not in free
                # Probes bypass embedding caches. Concurrent real probes reproduce
                # the original busy/cold local service path without stopping Ollama.
                responses = await asyncio.gather(
                    *[
                        client.post(
                            "/api/config/probe-service",
                            json={
                                "kind": "sponsored",
                                "config": {
                                    "llm": {
                                        "sponsored": {
                                            "enabled": True,
                                            "model": cfg.llm.sponsored.model,
                                            "embedding_mode": cfg.llm.sponsored.embedding_mode,
                                        }
                                    }
                                },
                            },
                        )
                        for _ in range(3)
                    ]
                )
                for response in responses:
                    response.raise_for_status()
                    assert response.json()["ok"], response.json()
                return {"provider": free, "probes": [response.json() for response in responses]}

            await run.case("builtin_provider_and_three_concurrent_real_probes", provider_probes)
    finally:
        server.should_exit = True
        await serving
    run.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report-name", default="release-acceptance.json")
    args = parser.parse_args()
    asyncio.run(run_checks(args.root.resolve(), args.report_name))
