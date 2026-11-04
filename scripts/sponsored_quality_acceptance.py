#!/usr/bin/env python3
"""Opt-in live regression for free-model evidence boundaries.

Runs production components with published, verified SDK bundles and synthetic
fixtures. No credentials or user account data are required as CLI arguments.
Use PYTHONPATH=src python scripts/sponsored_quality_acceptance.py --root NEW_DIR.
A nonzero exit means a case or background operation failed; read acceptance.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

from sponsored_system_acceptance import Run, fixture_events

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.service import _background_admission_bypass
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime
from openbiliclaw.sources.event_format import build_event
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings


async def run_model(out: Path, model: str, *, rebuild_only: bool = False) -> None:
    out.mkdir()
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(out)
    cfg = Config(data_dir=str(out / "data"))
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=True,
        model=model,
        fallback_to_other_free_model=False,
        fallback_to_user_provider=False,
    )
    cfg.scheduler.auto_update_enabled = False
    save_config(cfg, out / "config.toml")
    installed = await asyncio.to_thread(ensure_runtime)
    print(json.dumps({"installed": str(installed), "model": model}), flush=True)
    ctx = create_app().state.runtime_context
    run = Run(out, model)
    run.ctx = ctx
    profile = None

    def capture_calls(service):
        original_complete = service.complete_structured_task

        async def capture_complete(**kwargs):
            response = await original_complete(**kwargs)
            if kwargs.get("caller") in {"soul.profile_build", "discovery.temporal_repair"}:
                with (out / "captured-responses.jsonl").open("a") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "caller": kwargs["caller"],
                                "input": kwargs["user_input"],
                                "response": response.content,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            return response

        service.complete_structured_task = capture_complete

    capture_calls(ctx.llm_service)
    if ctx.soul_engine._profile_builder.registry is not ctx.llm_service:
        capture_calls(ctx.soul_engine._profile_builder.registry)

    async def portrait():
        nonlocal profile
        history = [
            build_event(
                event_type=x["type"],
                source_platform=x["source_platform"],
                title=x["title"],
                url=x["url"],
                metadata={**x["metadata"], "timestamp": x["timestamp"]},
            )
            for x in fixture_events()
        ]
        with _background_admission_bypass():
            profile = await ctx.soul_engine._profile_builder.build(
                history=history, preference={}, awareness_notes=[], active_insights=[]
            )
        assert profile.life_stage == profile.current_phase == "暂无明确自述"
        assert not profile.core_traits and not profile.deep_needs and not profile.values
        assert profile._raw_mbti["confidence"] <= 0.55
        assert profile._raw_mbti["dimensions"].get("EI", {}).get("strength", 0.5) == 0.5
        assert profile.cognitive_style, (
            "Expected a supported information preference on rich passive history"
        )
        assert (
            "亲手" not in profile.personality_portrait
            and "掌控" not in profile.personality_portrait
        )
        return {"profile": asdict(profile), "mbti": profile._raw_mbti}

    async def dates():
        assert profile is not None
        inputs = [
            ("Rust 借用与所有权原理", "介绍借用、引用和生命周期的基础原理，配有示例。"),
            ("光合作用基本原理", "解释植物如何利用光能合成有机物的基础知识。"),
            (
                "比赛报名入口",
                "本比赛报名截止 2026-11-01T18:00:00+08:00。截止后此报名入口关闭，不再受理。",
            ),
            ("阿波罗13号历史纪录片", "回顾1970年阿波罗13号任务全过程和当时的救援。"),
            (
                "限时免费领取Rust教程",
                "介绍Rust的基础原理并提供教程资料。活动没有提供具体截止时间。",
            ),
            ("Python 3.12 版本指南", "Python 3.12仍是当前受支持版本。本指南介绍其版本特性。"),
        ]
        contents = [
            DiscoveredContent(
                bvid=f"BVfollow{i}",
                title=title,
                description=description,
                source_strategy="search",
                source_platform="bilibili",
                published_at="2026-01-01T00:00:00+08:00",
            )
            for i, (title, description) in enumerate(inputs)
        ]
        with _background_admission_bypass():
            scores = await ctx.discovery_engine._evaluate_batch_once(
                contents,
                profile,
                normal_cache_enabled=False,
                evaluated_at="2026-11-05T01:00:00+08:00",
            )
        rows = [
            {
                key: getattr(c, key)
                for key in (
                    "bvid",
                    "title",
                    "temporal_class",
                    "temporal_confidence",
                    "temporal_validity_mode",
                    "temporal_valid_until",
                    "temporal_scope",
                    "temporal_state",
                    "temporal_evidence",
                    "temporal_evidence_complete",
                )
            }
            for c in contents
        ]
        (out / "temporal-details.json").write_text(
            json.dumps({"scores": scores, "results": rows}, ensure_ascii=False, indent=2)
        )
        assert len(scores) == 6 and all(s is not None for s in scores)
        assert contents[0].temporal_class == contents[1].temporal_class == "evergreen", rows
        assert contents[3].temporal_class == "historical", rows
        assert contents[2].temporal_validity_mode == "explicit_deadline", rows
        assert contents[2].temporal_valid_until == "2026-11-01T10:00:00Z", rows
        assert all(not c.temporal_valid_until for i, c in enumerate(contents) if i != 2), rows
        assert all(c.temporal_evidence_complete for c in contents), rows
        return {"scores": scores, "results": rows}

    async def awareness():
        events = [
            {
                "id": i + 1,
                "event_type": "favorite",
                "title": title,
                "source_platform": "bilibili",
                "metadata": {"timestamp": "2026-11-04T10:00:00+08:00"},
            }
            for i, title in enumerate(["Rust借用机制原理", "Python异步编程示例"])
        ]
        with _background_admission_bypass():
            (
                notes,
                confusions,
            ) = await ctx.soul_engine._awareness_analyzer.analyze_with_confusions(
                events=events,
                preference={},
                soul_profile=asdict(profile),
                source_event_ids=[1, 2],
            )
        assert all(set(n.source_event_ids).issubset({1, 2}) for n in notes)
        return {"notes": [asdict(n) for n in notes], "confusions": confusions}

    async def chat_and_embedding():
        outputs = []
        for _ in range(3):
            started = time.monotonic()
            first = None
            reply = ""
            async for chunk in ctx.llm_service.stream_complete_with_native_tools(
                messages=[{"role": "user", "content": "请只回复四个字：连接正常"}],
                tools=[],
                caller="agent.chat",
            ):
                if chunk.delta:
                    first = first if first is not None else round(time.monotonic() - started, 3)
                    reply += chunk.delta
            assert "连接正常" in reply, reply
            outputs.append(
                {
                    "first_delta_seconds": first,
                    "seconds": round(time.monotonic() - started, 3),
                    "reply": reply,
                }
            )
        vector = await ctx.recommendation_engine._embedding_service.embed("Rust 借用机制与所有权")
        assert len(vector) == 1024
        return {"chat": outputs, "embedding_dimensions": len(vector)}

    async def self_description_survives_rebuild():
        await ctx.memory_manager.propagate_event(
            {
                "event_type": "dialogue",
                "title": "用户补充职业",
                "metadata": {"user_message": "我是一名中学数学老师，不是程序员。"},
            }
        )
        history = ctx.soul_engine._profile_rebuild_history()
        with _background_admission_bypass():
            rebuilt = await ctx.soul_engine._profile_builder.build(
                history=history,
                preference={},
                awareness_notes=[],
                active_insights=[
                    {
                        "hypothesis": "用户是一名中学数学老师",
                        "user_verdict": "confirmed",
                        "validated": True,
                        "confidence": 0.95,
                    }
                ],
            )
        assert any(word in rebuilt.life_stage for word in ("老师", "教师")), rebuilt.life_stage
        return {"source_event_count": len(history), "profile": asdict(rebuilt)}

    try:
        if not rebuild_only:
            await run.case("passive_portrait_is_grounded", portrait)
            await run.case("six_temporal_semantics_and_no_cross_item_date", dates)
            await run.case("awareness_has_no_foreign_event_citations", awareness)
            await run.case("three_streams_and_bge_after_public_install", chat_and_embedding)
        await run.case("self_description_survives_rebuild", self_description_survives_rebuild)
    finally:
        await ctx.task_registry.cancel_all()
        await close_shared_provider()
        await ctx.bilibili_client.close()
        ctx.database.close()
        run.finish()


async def main(root: Path, models: list[str], *, rebuild_only: bool = False) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    failed = False
    for model in models:
        try:
            await run_model(root / model.split("/")[-1], model, rebuild_only=rebuild_only)
        except SystemExit:
            failed = True
    if failed:
        raise SystemExit("Acceptance recorded a failure; inspect per-model reports")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New disposable output directory")
    parser.add_argument("--model", action="append", choices=["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Run only the original-user-evidence rebuild regression",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(
        main(
            args.root,
            args.model or ["Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B"],
            rebuild_only=args.rebuild_only,
        )
    )
