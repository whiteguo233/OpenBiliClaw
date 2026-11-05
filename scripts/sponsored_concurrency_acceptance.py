#!/usr/bin/env python3
"""Measure live signed-SDK concurrency without replacing provider responses.

Uses isolated data, real Rust IPC and real SiliconFlow requests. The frame
wrappers only observe timestamps/IDs, never modify frames or credentials.
Default scenarios exercise the application's actual service gate and worker
pool. --scenario process-worker allows two OS processes to share one ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import shlex
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from openbiliclaw.llm import sponsored_provider as ipc
from openbiliclaw.llm.base import LLMRegistry
from openbiliclaw.llm.service import LLMService
from openbiliclaw.llm.sponsored_pool import SponsoredProviderPool
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS, approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
)

JOB: ContextVar[str] = ContextVar("concurrency_job", default="")


class Trace:
    """Observe real execute/result frames; preserve transport behavior."""

    def __init__(self) -> None:
        self.origin = time.monotonic()
        self.frames: dict[str, dict[str, Any]] = {}
        self.providers: list[SponsoredProvider] = []
        self.write = ipc.write_frame_async
        self.read = ipc.read_frame_async

    def now(self) -> float:
        return round(time.monotonic() - self.origin, 6)

    async def write_frame(self, writer: Any, frame: dict[str, Any]) -> None:
        if frame.get("type") in {"execute", "execute_stream"}:
            children: list[SponsoredProvider] = []
            for provider in self.providers:
                children.extend(
                    provider._children
                    if isinstance(provider, SponsoredProviderPool)
                    else [provider]
                )
            owner = next(
                (p._process for p in children if p._process and p._process.stdin is writer), None
            )
            self.frames[frame["id"]] = {
                "id": frame["id"],
                "job": JOB.get(),
                "pid": owner.pid if owner else None,
                "task": frame["task_id"],
                "sent": self.now(),
                "first_delta": None,
                "finished": None,
                "delta_count": 0,
            }
        await self.write(writer, frame)

    async def read_frame(self, reader: Any) -> dict[str, Any]:
        frame = await self.read(reader)
        record = self.frames.get(frame.get("id", ""))
        if record is not None:
            if frame.get("type") == "delta":
                record["delta_count"] += 1
                if record["first_delta"] is None and str(frame.get("content", "")).strip():
                    record["first_delta"] = self.now()
            else:
                record.update(finished=self.now(), ok=frame.get("ok"))
                if frame.get("error"):
                    record["error_code"] = frame["error"].get("code")
        return frame

    def start(self) -> None:
        ipc.write_frame_async = self.write_frame
        ipc.read_frame_async = self.read_frame

    def stop(self) -> None:
        ipc.write_frame_async = self.write
        ipc.read_frame_async = self.read

    def finish_job(self, name: str) -> None:
        for frame in self.frames.values():
            if frame["job"] == name and frame["finished"] is None:
                frame.update(finished=self.now(), interrupted=True)


def peak_overlap(frames: list[dict[str, Any]]) -> int:
    events = [(r["sent"], 1) for r in frames] + [(r["finished"], -1) for r in frames]
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    return peak


class Run:
    def __init__(self, root: Path, model: str, command: str, trace: Trace) -> None:
        self.root, self.model, self.trace = root, model, trace
        self.report: dict[str, Any] = {"model": model, "stages": [], "requests": []}
        install_sponsored_settings(
            SponsoredRuntimeSettings(
                enabled=True,
                runtime_path=command,
                model=model,
                fallback_to_other_free_model=False,
                fallback_to_user_provider=False,
            ),
            data_path=root / "data",
        )
        self.pool = shared_sponsored_provider()
        assert isinstance(self.pool, SponsoredProviderPool)
        trace.providers.append(self.pool)
        self.service = LLMService(registry=LLMRegistry(), memory=None)

    def save(self) -> None:
        self.report["frames"] = list(self.trace.frames.values())
        (self.root / "concurrency.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2) + "\n"
        )

    async def job(
        self, name: str, kind: str, *, single: SponsoredProvider | None = None
    ) -> dict[str, Any]:
        marker = "CC-" + hashlib.sha256(name.encode()).hexdigest()[:12]
        record: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "marker": marker,
            "submitted": self.trace.now(),
        }
        token = JOB.set(name)
        try:
            async with asyncio.timeout(420):
                if kind == "embedding":
                    vector = await self.pool.embedding(f"{marker} Rust并发与个性化推荐向量测试")
                    assert len(vector) == 1024 and any(vector) and all(map(math.isfinite, vector))
                    record.update(dimensions=len(vector), status="pass")
                else:
                    structured = kind == "evaluation"
                    if structured:
                        candidates = [
                            {"id": f"{marker}-{i}", "title": title}
                            for i, title in enumerate(
                                ["Rust异步并发", "Python数据分析", "阳台番茄种植", "天文摄影入门"]
                            )
                        ]
                        system = (
                            "根据给定兴趣评估候选，只返回JSON对象：marker原样返回，items数组每项"
                            "含id、score（0到1）、reason（简短理由）。逐项处理，不增删或改写id。"
                        )
                        user = json.dumps(
                            {
                                "marker": marker,
                                "interests": ["编程", "园艺"],
                                "candidates": candidates,
                            },
                            ensure_ascii=False,
                        )
                    else:
                        system = "按用户要求简短回答，并原样保留本次请求标识。"
                        user = f"先原样写 {marker}，再用两句话解释Rust并发请求为何需要队列。"
                        if kind == "cancel_stream":
                            user = f"先原样写 {marker}，再写一篇关于Rust并发的2000字教程。"
                    kwargs = {
                        "temperature": 0,
                        "max_tokens": 4096 if structured or kind == "cancel_stream" else 512,
                        "caller": "discovery.evaluate_batch" if structured else "agent.chat",
                    }
                    if single is not None:
                        response = await single.complete(
                            [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            **kwargs,
                        )
                    elif structured:
                        response = await self.service.complete_with_core_memory(
                            system_instruction=system,
                            user_input=user,
                            json_mode=True,
                            inject_core_memory=False,
                            **kwargs,
                        )
                    else:
                        stream = self.service.stream_complete_with_core_memory(
                            system_instruction=system,
                            user_input=user,
                            inject_core_memory=False,
                            **kwargs,
                        )
                        response = None
                        deltas: list[str] = []
                        try:
                            async for chunk in stream:
                                if chunk.delta:
                                    record.setdefault("first_content", self.trace.now())
                                    deltas.append(chunk.delta)
                                    if kind == "cancel_stream":
                                        record.update(status="cancelled_after_real_delta")
                                        break
                                if chunk.response is not None:
                                    response = chunk.response
                        finally:
                            await stream.aclose()
                        if kind == "cancel_stream":
                            assert deltas, "No real streamed content arrived before cancellation"
                            return record
                        assert response is not None and deltas, "Missing delta or terminal response"
                        assert "".join(deltas) == response.content, (
                            "Stream/terminal content mismatch"
                        )
                    assert response.provider == "sponsored" and response.model == self.model
                    assert marker in response.content, "Request identity missing or crossed"
                    if structured:
                        result = json.loads(response.content)
                        assert result["marker"] == marker
                        assert [v["id"] for v in result["items"]] == [v["id"] for v in candidates]
                        assert all(0 <= v["score"] <= 1 and v["reason"] for v in result["items"])
                    record.update(status="pass", model=response.model, usage=response.usage)
        except Exception as exc:
            record.update(status="fail", error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            record["finished"] = self.trace.now()
            record["elapsed_seconds"] = round(record["finished"] - record["submitted"], 3)
            self.trace.finish_job(name)
            frames = [v for v in self.trace.frames.values() if v["job"] == name]
            record["queue_seconds"] = (
                round(frames[0]["sent"] - record["submitted"], 3) if frames else None
            )
            if record.get("first_content") is not None:
                record["first_content_seconds"] = round(
                    record["first_content"] - record["submitted"], 3
                )
            self.report["requests"].append(record)
            self.save()
            print(json.dumps(record, ensure_ascii=False), flush=True)
            JOB.reset(token)
        return record

    async def stage(self, name: str, kinds: list[str], **kwargs: Any) -> dict[str, Any]:
        started = self.trace.now()
        results = await asyncio.gather(
            *(self.job(f"{name}-{i}", kind, **kwargs) for i, kind in enumerate(kinds))
        )
        frames = [r for r in self.trace.frames.values() if r["job"].startswith(name + "-")]
        stage = {
            "name": name,
            "submitted": len(kinds),
            "passed": sum(r["status"] == "pass" for r in results),
            "elapsed_seconds": round(self.trace.now() - started, 3),
            "peak_ipc_inflight": peak_overlap(frames),
            "worker_pids": sorted({r["pid"] for r in frames}),
            "max_queue_seconds": max((r["queue_seconds"] or 0) for r in results),
        }
        expected_peak = 1 if name in {"single-child-3", "chat-only-3"} else 3
        stage["checks"] = {
            "expected_peak_observed": stage["peak_ipc_inflight"] == expected_peak,
            "each_child_serial": all(
                peak_overlap([r for r in frames if r["pid"] == pid]) == 1
                for pid in stage["worker_pids"]
            ),
            "worker_identity_observed": all(r["pid"] is not None for r in frames),
        }
        self.report["stages"].append(stage)
        self.save()
        print(json.dumps({"stage": stage}), flush=True)
        return stage


async def main_async(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    if args.bundle:
        verify_bundle(args.bundle.resolve())
        os.environ["OPENBILICLAW_SPONSORED_BUNDLE"] = str(args.bundle.resolve())
    command = await asyncio.to_thread(ensure_runtime)
    parts = shlex.split(command)
    if args.ledger:
        parts[parts.index("--data-dir") + 1] = str(args.ledger.resolve())
        command = shlex.join(parts)
    bundle = Path(parts[0]).parent
    verify_bundle(bundle)
    trace = Trace()
    run = Run(root, args.model, command, trace)
    run.report.update(
        runtime_manifest_sha256=hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest(),
        concurrency=run.service.concurrency,
        private_runtime_response_replacements=False,
        wall_clock_started=time.time(),
    )
    trace.start()
    try:
        if args.scenario == "process-worker":
            await run.stage("process-mixed", ["evaluation", "evaluation", "chat", "embedding"])
        else:
            single = SponsoredProvider(
                parts,
                registry=approved_registry(),
                model=args.model,
                fallback_to_other_free_model=False,
            )
            trace.providers.append(single)
            try:
                await run.stage("single-child-3", ["chat"] * 3, single=single)
            finally:
                await single.aclose()
            await run.stage("mixed-3", ["evaluation", "evaluation", "chat"])
            await run.stage("chat-only-3", ["chat"] * 3)
            await run.stage("mixed-6", ["evaluation", "chat", "embedding"] * 2)
            await run.stage("mixed-12", ["evaluation", "chat", "embedding"] * 4)
            await run.job("cancel-real-stream", "cancel_stream")
            await run.stage("after-cancel", ["chat", "embedding", "evaluation"])
    finally:
        await close_shared_provider()
        trace.stop()
        run.report["wall_clock_finished"] = time.time()
        run.save()
    if any(r["status"] == "fail" for r in run.report["requests"]) or any(
        not all(stage["checks"].values()) for stage in run.report["stages"]
    ):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=CHAT_MODELS, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--scenario", choices=("all", "process-worker"), default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
