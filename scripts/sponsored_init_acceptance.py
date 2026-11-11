#!/usr/bin/env python3
"""Opt-in real SDK history-analysis acceptance, with disposable synthetic data.

Run with PYTHONPATH=src. --root must not exist. This exercises memory ingestion,
preference analysis with the guided-init watchdog, and persisted profile creation;
it does not fetch platform history or build the initial recommendation pool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import fields, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sponsored_system_acceptance import fixture_events

from openbiliclaw.api.app import create_app
from openbiliclaw.cli import (
    _await_with_progress_deadline,
    _InitProgressMarker,
    _profile_analysis_deadlines,
)
from openbiliclaw.config import Config, save_config
from openbiliclaw.llm.service import _background_admission_bypass
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, install_archive


async def run(
    root: Path,
    model: str,
    count: int,
    archive: Path | None,
    *,
    inject_first_timeout: bool = False,
    runtime_command: str | None = None,
) -> None:
    """Call only the selected free model; capture metadata, never credentials."""
    root.mkdir(parents=True, exist_ok=False)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    config = Config(data_dir=str(root / "data"))
    config.llm.instance_routing = True
    config.llm.default_chain = [
        "siliconflow-free" if model == CHAT_MODELS[0] else "siliconflow-free-qwen35-4b"
    ]
    config.llm.embedding.provider = "siliconflow_free"
    config.llm.embedding.model = "BAAI/bge-m3"
    config.llm.embedding.output_dimensionality = 1024
    if runtime_command:
        config.llm.sponsored = replace(config.llm.sponsored, runtime_path=runtime_command)
    config.scheduler.auto_update_enabled = False
    config.network.mode = "direct"
    for field in fields(config.sources):
        source = getattr(config.sources, field.name)
        if hasattr(source, "enabled"):
            source.enabled = False
    save_config(config, root / "config.toml")
    if archive:
        install_archive(archive)
    elif not runtime_command:
        ensure_runtime()

    report: dict[str, Any] = {"model": model, "event_count": count, "calls": [], "progress": []}

    def save_report() -> None:
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    original = SponsoredProvider._execute_task_once
    timeout_injected = False

    async def observed(provider: SponsoredProvider, **kwargs: Any) -> Any:
        nonlocal timeout_injected
        payload = kwargs.get("user_payload", {})
        record: dict[str, Any] = {
            "caller": payload.get("caller", kwargs.get("caller")),
            "task_id": kwargs.get("task_id"),
            "input_bytes": len(json.dumps(payload, ensure_ascii=False).encode()),
            "max_tokens": payload.get("max_tokens"),
            "effort": payload.get("reasoning_effort"),
        }
        report["calls"].append(record)
        started = time.monotonic()
        previous_timeout = provider._request_timeout
        inject = (
            inject_first_timeout
            and not timeout_injected
            and str(record["caller"]).startswith("soul.preference")
        )
        if inject:
            timeout_injected = True
            # Exercise actual IPC cancellation/process shutdown, not a fake reply.
            provider._request_timeout = 0.05
            record["injected_timeout_seconds"] = 0.05
        try:
            # Observation only: original production implementation and network.
            result = await original(provider, **kwargs)
            record["ok"] = True
            record["request_timeout_seconds"] = provider._request_timeout
            record["runtime_negotiated_timeouts"] = provider._request_timeouts
            return result
        except BaseException as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            if inject:
                provider._request_timeout = previous_timeout
                record["runtime_stopped_after_timeout"] = not provider.runtime_running
            record["seconds"] = round(time.monotonic() - started, 2)
            save_report()

    started = time.monotonic()
    try:
        with patch.object(SponsoredProvider, "_execute_task_once", observed):
            context = create_app().state.runtime_context
            assert not context.degraded, context.degraded_reason
            templates = fixture_events()
            events = []
            for i in range(count):
                event = dict(templates[i % len(templates)])
                event["event_id"] = f"init-acceptance-{i}"
                event["title"] += f" 第{i + 1}篇"
                event["timestamp"] -= i * 60000
                event["metadata"] = dict(event["metadata"])
                events.append(event)
            await context.memory_manager.propagate_events(events)
            rows = context.memory_manager.query_events(limit=count)
            assert len(rows) == count
            for row in rows:
                if isinstance(row.get("metadata"), str):
                    row["metadata"] = json.loads(row["metadata"])
            started = time.monotonic()
            marker = _InitProgressMarker()

            async def progress(done: int, total: int) -> None:
                marker.touch()
                report["progress"].append(
                    {"done": done, "total": total, "seconds": round(time.monotonic() - started, 2)}
                )
                save_report()
                print(f"{model}: {done}/{total}", flush=True)

            batches = context.soul_engine.preference_analysis_batch_count(
                rows, event_chunk_size=200
            )
            report["planned_batches"] = batches
            idle, absolute = _profile_analysis_deadlines(
                event_count=count,
                requested=None,
                concurrency=3,
                batch_count=batches,
                request_timeout_seconds=float(config.llm.timeout),
            )
            report["deadlines"] = {"idle": idle, "absolute": absolute}
            with _background_admission_bypass():
                await _await_with_progress_deadline(
                    context.soul_engine.analyze_events(
                        rows, event_chunk_size=200, llm_concurrency=3, progress_callback=progress
                    ),
                    marker=marker,
                    idle_seconds=idle,
                    absolute_seconds=absolute,
                )
                report["preference_seconds"] = round(time.monotonic() - started, 2)
                profile = await context.soul_engine.build_initial_profile(rows)
            assert context.soul_engine.is_profile_ready() and profile.preferences.interests
            report["profile_sections"] = {
                name: len(getattr(profile, name))
                for name in (
                    "core_traits",
                    "deep_needs",
                    "values",
                    "motivational_drivers",
                    "cognitive_style",
                    "current_phase",
                )
            }
            assert all(report["profile_sections"].values()), report["profile_sections"]
            assert all(
                "待确认" in item
                for name in ("core_traits", "deep_needs", "values", "motivational_drivers")
                for item in getattr(profile, name)
            )
            assert profile.life_stage == "暂无明确自述" and not profile.core.mbti.type
            assert report["progress"][-1]["done"] == batches
            if inject_first_timeout:
                injected = [c for c in report["calls"] if "injected_timeout_seconds" in c]
                assert len(injected) == 1
                assert injected[0].get("error_type") == "SponsoredUnavailableError"
                assert injected[0]["runtime_stopped_after_timeout"]
            report["ok"] = True
    except BaseException as exc:
        report.update(ok=False, error_type=type(exc).__name__)
        raise
    finally:
        report["total_seconds"] = round(time.monotonic() - started, 2)
        await close_shared_provider()
        save_report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--model", required=True, choices=CHAT_MODELS)
    parser.add_argument("--events", type=int, default=800)
    runtime_source = parser.add_mutually_exclusive_group()
    runtime_source.add_argument("--sdk-archive", type=Path, help="Optional signed release archive")
    runtime_source.add_argument(
        "--runtime-command", help="Local candidate command with signed policy"
    )
    parser.add_argument(
        "--inject-first-timeout", action="store_true", help="Force one real IPC request timeout"
    )
    args = parser.parse_args()
    if args.events < 1:
        parser.error("--events must be positive")
    asyncio.run(
        run(
            args.root.resolve(),
            args.model,
            args.events,
            args.sdk_archive,
            inject_first_timeout=args.inject_first_timeout,
            runtime_command=args.runtime_command,
        )
    )


if __name__ == "__main__":
    main()
