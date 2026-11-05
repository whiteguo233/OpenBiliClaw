#!/usr/bin/env python3
"""Live acceptance for application paths beyond the free-plan baseline.

Run after sponsored_system_acceptance.py in its disposable directory. Real
HTTP, production business modules, verified Rust runtime and provider requests
are used. Fixtures are synthetic; no paid provider or private account is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
from openbiliclaw.llm.sponsored_errors import SponsoredRequestTooLargeError
from openbiliclaw.llm.sponsored_profiles import CONTEXT_WINDOWS
from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider


async def run_extended(root: Path, report_name: str, only: set[str] | None = None) -> None:
    assert (root / "acceptance.json").is_file(), "Run the baseline in a new directory first"
    assert not (root / report_name).exists(), "Preserve previous acceptance evidence"
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    cfg = load_config(consult_environment=False)
    assert Path(cfg.data_dir).resolve() == root / "data"
    assert cfg.llm.sponsored.enabled
    assert not cfg.llm.sponsored.fallback_to_user_provider
    assert not cfg.llm.sponsored.fallback_to_other_free_model
    run = Run(root, cfg.llm.sponsored.model, report_name=report_name)
    original_case = run.case

    async def selected_case(name: str, call: Any, **kwargs: Any) -> Any:
        if only and name not in only:
            return None
        return await original_case(name, call, **kwargs)

    run.case = selected_case
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=480
        ) as client:

            async def read_surfaces() -> dict[str, Any]:
                result = {}
                for path in (
                    "/api/health",
                    "/api/profile-summary",
                    "/api/profile/edit-state",
                    "/api/recommendations?limit=3",
                    "/api/chat/sessions",
                    "/api/chat/tasks",
                    "/api/runtime-status",
                    "/api/config",
                    "/api/notifications/pending",
                    "/api/chat/approvals",
                    "/api/chat/pending-confirmations",
                    "/web/",
                    "/m/",
                    "/setup/",
                ):
                    response = await client.get(path, follow_redirects=True)
                    response.raise_for_status()
                    result[path] = {"status": response.status_code, "bytes": len(response.content)}
                return result

            await run.case("http_user_surfaces_and_state_endpoints", read_surfaces)

            async def edits() -> dict[str, Any]:
                added = "陶艺拉坯验收兴趣"
                before = await ctx.soul_engine.get_profile()
                assert added not in [d.domain for d in before.interest.likes]
                for _ in range(2):
                    response = await client.post(
                        "/api/profile/edit", json={"target": "likes", "op": "add", "value": added}
                    )
                    response.raise_for_status()
                profile = await ctx.soul_engine.get_profile()
                assert [d.domain for d in profile.interest.likes].count(added) == 1
                bad = await client.post(
                    "/api/profile/edit", json={"target": "unknown", "op": "add", "value": added}
                )
                assert bad.status_code == 422
                response = await client.post(
                    "/api/profile/edit", json={"target": "likes", "op": "remove", "value": added}
                )
                response.raise_for_status()
                assert added not in [
                    d.domain for d in (await ctx.soul_engine.get_profile()).interest.likes
                ]
                return {
                    "duplicate_edit_deduplicated": True,
                    "invalid_edit_rejected": True,
                    "removed": True,
                }

            await run.case("profile_manual_edit_dedup_and_invalid_input", edits)

            async def durable() -> dict[str, Any]:
                session = await client.post("/api/chat/sessions", json={"title": "完整验收会话"})
                session.raise_for_status()
                session_id = session.json()["session_id"]
                turn_id = f"comprehensive-{time.time_ns()}"
                body = {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "session": "desktop",
                    "message": "只回复本次验收代号：银杏6029。",
                    "streaming": True,
                }
                for _ in range(2):
                    response = await client.post("/api/chat/turns", json=body)
                    response.raise_for_status()
                events: list[dict[str, Any]] = []
                async with client.stream("POST", "/api/chat/agent/stream", json=body) as response:
                    response.raise_for_status()
                    event = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            events.append({"event": event, "data": json.loads(line[6:])})
                errors = [e for e in events if e["event"] == "error"]
                assert not errors, errors
                completed = (await client.get(f"/api/chat/turns/{turn_id}")).json()
                assert completed["status"] == "completed" and "银杏6029" in completed["reply"], (
                    completed
                )
                replay = await client.post("/api/chat/turns", json=body)
                replay.raise_for_status()
                assert replay.json()["reply"] == completed["reply"]
                conflict = await client.post(
                    "/api/chat/turns", json={**body, "message": "不同内容"}
                )
                assert conflict.status_code == 409, conflict.text
                detail = (await client.get(f"/api/chat/sessions/{session_id}")).json()
                assert detail["total"] == 1, detail
                for patch in ({"title": "已重命名验收"}, {"archived": True}, {"archived": False}):
                    response = await client.patch(f"/api/chat/sessions/{session_id}", json=patch)
                    response.raise_for_status()
                    assert all(response.json()[key] == value for key, value in patch.items())
                return {
                    "turn_id": turn_id,
                    "reply": completed["reply"],
                    "stored_turns": 1,
                    "conflict_status": 409,
                    "rename_archive_restore": True,
                }

            await run.case("durable_turn_idempotency_conflict_and_session_lifecycle", durable)

            async def background_task() -> dict[str, Any]:
                response = await client.post(
                    "/api/chat/tasks",
                    json={
                        "title": "只读画像任务",
                        "prompt": "必须用 get_profile 工具读取画像，最后概括两个兴趣，不要修改数据。",
                    },
                )
                response.raise_for_status()
                task_id = response.json()["task_id"]
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    row = (await client.get(f"/api/chat/tasks/{task_id}")).json()
                    if row["status"] in {"completed", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.5)
                assert row["status"] == "completed", row
                assert row.get("steps"), row
                assert "get_profile" in json.dumps(row["steps"]), row
                return row

            await run.case("background_agent_task_real_tool_and_persistence", background_task)

            async def extraction() -> dict[str, Any]:
                from openbiliclaw.saved_sync.identity import make_item_key
                from openbiliclaw.sources.llm_extractor import extract_content_from_page

                rows = await extract_content_from_page(
                    "文章列表\n标题：Rust借用检查实例\n作者：小林\n链接：https://example.com/post/rust-17\n"
                    "摘要：用三个可运行的例子解释引用的作用域。\n"
                    "标题：番茄支架搭建记录\n作者：阿青\n链接：https://example.com/post/garden-29\n"
                    "摘要：记录阳台番茄支架安装过程以及本周的浇水安排。\n页脚：联系我们",
                    source_platform="web",
                    llm_service=ctx.llm_service,
                )
                assert len(rows) == 2, rows
                expected_urls = {
                    "https://example.com/post/rust-17",
                    "https://example.com/post/garden-29",
                }
                assert {r.content_url for r in rows} == expected_urls, rows
                assert {r.item_key for r in rows} == {
                    make_item_key("web", "", url) for url in expected_urls
                }, rows
                return {"items": [asdict(r) for r in rows]}

            await run.case("web_content_extraction_preserves_items_and_urls", extraction)

            async def merged_keywords() -> dict[str, Any]:
                from openbiliclaw.discovery.keyword_digest import profile_kw_digest
                from openbiliclaw.runtime.keyword_planner import KeywordPlanner

                profile = await ctx.soul_engine.get_profile()
                planner = KeywordPlanner(
                    llm_service=ctx.llm_service,
                    database=ctx.database,
                    config=ctx.config,
                    soul_engine=ctx.soul_engine,
                )
                platforms = ["bilibili", "xiaohongshu", "douyin", "youtube", "github"]
                before = ctx.database.max_llm_usage_id()
                with _background_admission_bypass():
                    generated = await planner._generate_for(
                        platforms,
                        profile=profile,
                        digest=profile_kw_digest(profile),
                        hints_by_platform={},
                    )
                calls = [
                    dict(row)
                    for row in ctx.database.conn.execute(
                        "SELECT caller,success FROM llm_usage WHERE id>?", (before,)
                    )
                ]
                assert any(r["success"] and "keyword" in r["caller"] for r in calls), calls
                assert generated and sum(generated.values()) > 0, generated
                assert set(generated) <= set(platforms), generated
                return {"generated": generated, "calls": calls}

            await run.case("five_platform_keyword_generation_and_cache", merged_keywords)

            async def director() -> dict[str, Any]:
                from sponsored_system_acceptance import CANDIDATES

                from openbiliclaw.recommendation.director.models import (
                    CompositionCard,
                    CompositionRequest,
                    CompositionSession,
                    ScreenConstraints,
                )
                from openbiliclaw.recommendation.director.planner import HorizonPlanner

                request = CompositionRequest(
                    request_id="live-director",
                    session=CompositionSession(
                        session_ref="live-session",
                        stream_ref="live-stream",
                        state_revision=1,
                        feedback_cursor="none",
                        configuration_revision=1,
                        explicit_intent="推荐可运行代码的编程实践教程，六条都可以浏览。",
                    ),
                    candidate_set_ref="live-candidates",
                    candidate_revision=1,
                    feature_schema_version="live.v1",
                    direction_ref="programming",
                    direction="编程实践教程",
                    target_count=3,
                    cards=tuple(
                        CompositionCard(
                            ref=f"c{i}",
                            item_key=f"fixture:{i}",
                            title=title,
                            summary=description,
                            topic_group=tag,
                            baseline_score=0.8,
                        )
                        for i, (title, description, tag) in enumerate(CANDIDATES[:6])
                    ),
                    constraints=ScreenConstraints(version="live-v1"),
                )
                result = await HorizonPlanner(ctx.llm_service).plan(
                    request,
                    horizon_screens=2,
                    budget_seconds=ctx.config.director.planning_budget_seconds,
                )
                assert result.plan is not None, result
                selected = [
                    ref for s in result.plan.screens for pair in s.pairs for ref in pair.card_refs
                ]
                assert len(selected) == len(set(selected)) == 6, result
                return result.model_dump(mode="json")

            await run.case("director_two_screens_with_default_planning_budget", director)

            async def speculations() -> dict[str, Any]:
                from openbiliclaw.soul.speculator import InterestSpeculator

                # A running app can already have all probe slots filled.
                # Exercise real generation/persistence in a fresh fixture,
                # without deleting the application's existing speculations.
                directory = root / (Path(report_name).stem + "-speculations")
                directory.mkdir(exist_ok=False)
                speculator = InterestSpeculator(llm_service=ctx.llm_service, data_dir=directory)
                before = ctx.database.max_llm_usage_id()
                with _background_admission_bypass():
                    result = await speculator.force_tick(await ctx.soul_engine.get_profile())
                calls = [
                    dict(row)
                    for row in ctx.database.conn.execute(
                        "SELECT caller,success FROM llm_usage WHERE id>?", (before,)
                    )
                ]
                assert any(r["caller"] == "soul.speculate" and r["success"] for r in calls), calls
                active = InterestSpeculator(
                    llm_service=ctx.llm_service, data_dir=directory
                ).get_active_speculations()
                assert active, asdict(result)
                return {"tick": asdict(result), "active": [asdict(s) for s in active]}

            await run.case("interest_speculation_generation_and_persistence", speculations)

            async def posture() -> dict[str, Any]:
                from openbiliclaw.soul.posture_gate import PostureGate

                gate = PostureGate(mode="enforce", registry=ctx.llm_service)
                with _background_admission_bypass():
                    decision = await gate.evaluate(
                        write_point="portrait_rebuild",
                        change={"before": {}, "after": {"occupation": "职业程序员"}},
                        ledger_digest=[
                            {"id": "fixture-1", "kind": "view", "title": "Rust 入门教程"}
                        ],
                        source_refs=["fixture-1"],
                    )
                assert decision.enforced and not decision.is_error, decision
                assert decision.verdict != "accept", decision
                return asdict(decision)

            await run.case("deep_write_gate_rejects_unsupported_occupation", posture)

            async def dislike_judge() -> dict[str, Any]:
                from openbiliclaw.soul.pool_purge import _embedding_recall, _llm_judge

                rows = [
                    {
                        "bvid": "fixture-bad",
                        "title": "赌博下注攻略：赌场稳赢技巧",
                        "topic_key": "赌博",
                        "topic_group": "博彩",
                    },
                    {
                        "bvid": "fixture-good",
                        "title": "Rust所有权教程与借用检查",
                        "topic_key": "Rust",
                        "topic_group": "编程",
                    },
                ]
                embedding = ctx.recommendation_engine._embedding_service
                recalled = await _embedding_recall(
                    candidates=rows,
                    topics=["赌博下注攻略"],
                    embedding_service=embedding,
                    threshold=0.55,
                )
                assert any(c[0]["bvid"] == "fixture-bad" for c in recalled), recalled
                with _background_admission_bypass():
                    purged = await _llm_judge(
                        recalled=recalled,
                        new_topics=["赌博下注攻略"],
                        all_topics=["赌博下注攻略"],
                        llm_service=ctx.llm_service,
                    )
                assert "fixture-bad" in purged and "fixture-good" not in purged, purged
                return {
                    "recalled": [(c[0]["bvid"], c[1]) for c in recalled],
                    "purge_decisions": purged,
                }

            await run.case("dislike_embedding_recall_and_precise_llm_judgement", dislike_judge)

            async def context() -> dict[str, Any]:
                provider = shared_sponsored_provider()
                assert provider
                lines = [f"record_{i:04d}=普通验收记录{i:04d}" for i in range(1200)]
                lines[731] = "record_0731=翠竹59427"
                source = "\n".join(lines)
                response = await provider.complete(
                    [
                        {"role": "system", "content": "只按提供的记录检索，不猜测。"},
                        {"role": "user", "content": source + "\n只回复record_0731的值。"},
                    ],
                    max_tokens=128,
                    reasoning_effort="",
                    caller="acceptance.long_context",
                )
                assert "翠竹59427" in response.content, response.content
                assert response.model == run.model
                try:
                    await provider.complete(
                        [{"role": "user", "content": "x" * CONTEXT_WINDOWS[run.model]}],
                        max_tokens=128,
                        caller="acceptance.over_context",
                    )
                except SponsoredRequestTooLargeError:
                    rejected = True
                else:
                    raise AssertionError("Oversized request was not rejected")
                return {
                    "input_utf8_bytes": len(source.encode()),
                    "records": len(lines),
                    "answer": response.content,
                    "model": response.model,
                    "over_limit_rejected": rejected,
                }

            await run.case("long_context_middle_record_and_model_specific_bound", context)

            async def thinking() -> dict[str, Any]:
                provider = shared_sponsored_provider()
                assert provider
                chunks = []
                terminal = None
                start = time.monotonic()
                first = None
                async for chunk in provider.stream_complete(
                    [
                        {
                            "role": "user",
                            "content": "有17盒彩笔，每盒12支，送出39支，还剩多少支？仅给算式与答案。",
                        }
                    ],
                    max_tokens=512,
                    reasoning_effort="low",
                    caller="soul.dialogue",
                ):
                    if chunk.delta:
                        first = first if first is not None else time.monotonic() - start
                        chunks.append(chunk.delta)
                    if chunk.response:
                        terminal = chunk.response
                answer = "".join(chunks)
                assert terminal and terminal.model == run.model and "165" in answer, answer
                return {
                    "answer": answer,
                    "first_text_seconds": first,
                    "deltas": len(chunks),
                    "selected_model": terminal.model,
                }

            await run.case("explicit_thinking_real_stream_and_correct_arithmetic", thinking)

            async def drain() -> dict[str, Any]:
                await ctx.dialogue_settlement_queue.pause_and_drain(timeout=360)
                ctx.dialogue_settlement_queue.resume()
                await ctx.soul_engine.wait_for_pending_edits()
                return {"learning_drained": True, "pending_edits_finished": True}

            await run.case("all_submitted_learning_and_edits_finish", drain)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 45)
        finally:
            await close_shared_provider()
            run.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report-name", default="extended-acceptance.json")
    parser.add_argument("--only", action="append", help="Run one named case; repeat for a subset")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_extended(args.root.resolve(), args.report_name, set(args.only or ())))
