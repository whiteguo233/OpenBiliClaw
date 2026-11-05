#!/usr/bin/env python3
"""Real backend SIGKILL/restart acceptance on a disposable baseline copy.

Requires a completed sponsored_system_acceptance.py directory and a verified
Rust bundle. Only child processes started by this script are terminated. No
credentials are printed and no user installation or source account is changed.
"""

import argparse
import asyncio
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
from dataclasses import replace
from typing import Any, TextIO

import httpx

from openbiliclaw.config import load_config, save_config

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source-root", type=pathlib.Path, required=True)
parser.add_argument("--root", type=pathlib.Path, required=True)
args = parser.parse_args()
if os.name != "posix":
    parser.error("This SIGKILL/process-group acceptance requires macOS or Linux")
source = args.source_root.resolve()
root = args.root.resolve()
assert (source / "acceptance.json").is_file(), "Use a completed disposable baseline"
assert json.loads((source / "acceptance.json").read_text())["complete"]
assert not root.exists()
root.mkdir()
cfg = load_config(source / "config.toml", consult_environment=False)
assert pathlib.Path(cfg.data_dir).resolve() == source / "data"
shutil.copytree(pathlib.Path(cfg.data_dir), root / "data")
cfg.data_dir = str(root / "data")
cfg.scheduler.enabled = False
cfg.scheduler.auto_update_enabled = False
cfg.llm.sponsored = replace(
    cfg.llm.sponsored,
    embedding_mode="cloud",
    fallback_to_other_free_model=False,
    fallback_to_user_provider=False,
)
save_config(cfg, root / "config.toml")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
env = dict(os.environ, OPENBILICLAW_PROJECT_ROOT=str(root))
records: list[dict[str, Any]] = [{"model": cfg.llm.sponsored.model}]
process: subprocess.Popen[Any] | None = None
stream_task: asyncio.Task[None] | None = None
logs: list[TextIO] = []


def save() -> None:
    (root / "report.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))


def start() -> None:
    global process
    log = (root / f"server-{len(logs)}.log").open("w")
    logs.append(log)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "openbiliclaw.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


async def ready(c: httpx.AsyncClient) -> None:
    for _ in range(150):
        assert process.poll() is None, "owned backend exited"
        try:
            r = await c.get("/api/init-status", timeout=2)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.2)
    raise AssertionError("backend startup timed out")


async def main() -> None:
    global stream_task
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=180
    ) as c:
        start()
        await ready(c)
        sess = (await c.post("/api/chat/sessions", json={"title": "真实进程中断验收"})).json()[
            "session_id"
        ]
        first = {
            "turn_id": "process-interrupted",
            "session_id": sess,
            "session": "desktop",
            "message": "请输出 180 行编号的 Rust 所有权练习清单，每行至少 15 个字。不要调用工具。",
            "streaming": True,
        }
        (await c.post("/api/chat/turns", json=first)).raise_for_status()
        delta = asyncio.Event()
        events = []

        async def consume() -> None:
            event = ""
            try:
                async with c.stream("POST", "/api/chat/agent/stream", json=first) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            events.append(event)
                            if event == "delta":
                                delta.set()
            except httpx.HTTPError:
                return

        stream_task = asyncio.create_task(consume())
        await asyncio.wait_for(delta.wait(), 120)
        second = {
            "turn_id": "process-queued",
            "session_id": sess,
            "session": "desktop",
            "message": "只回复：进程恢复842。",
            "streaming": True,
        }
        (await c.post("/api/chat/turns", json=second)).raise_for_status()
        pre1 = (await c.get("/api/chat/turns/process-interrupted")).json()
        pre2 = (await c.get("/api/chat/turns/process-queued")).json()
        assert pre1["status"] == "pending" and pre2["status"] == "pending", (
            pre1["status"],
            pre2["status"],
        )
        records.append(
            {
                "case": "before_SIGKILL",
                "first_delta_seen": True,
                "first_status": pre1["status"],
                "queued_status": pre2["status"],
                "backend_pid": process.pid,
            }
        )
        save()
        os.killpg(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.wait)
        await asyncio.wait_for(stream_task, 10)
        start()
        await ready(c)
        for _ in range(180):
            a = (await c.get("/api/chat/turns/process-interrupted")).json()
            b = (await c.get("/api/chat/turns/process-queued")).json()
            if a["status"] != "pending" and b["status"] != "pending":
                break
            await asyncio.sleep(1)
        detail = (await c.get(f"/api/chat/sessions/{sess}")).json()
        records.append(
            {
                "case": "after_restart",
                "interrupted": {k: a.get(k) for k in ("status", "reply", "error")},
                "queued": {k: b.get(k) for k in ("status", "reply", "error")},
                "turn_count": detail.get("total"),
                "events_before_crash": events,
            }
        )
        save()
        assert a["status"] == "failed", a
        # Recovery is a transport invariant. Retain exact-copy quality as a
        # separate observation so a model-inserted space is not called a lost
        # or replayed turn (the long-context suite checks exact fidelity).
        exact_copy = "进程恢复842" in b["reply"]
        records.append({"case": "reply_fidelity", "exact_copy": exact_copy, "reply": b["reply"]})
        save()
        assert b["status"] == "completed" and "进程恢复842" in "".join(b["reply"].split()), b
        assert detail["total"] == 2, detail
        records.append(
            {
                "case": "recovery_assertions",
                "status": "pass",
                "partial_work_not_replayed": True,
                "untouched_queued_work_resumed": True,
            }
        )
        save()


try:
    asyncio.run(main())
except BaseException as e:
    records.append({"case": "failure", "error": type(e).__name__ + ": " + str(e)[:2000]})
    save()
    raise
finally:
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    for log in logs:
        log.close()
