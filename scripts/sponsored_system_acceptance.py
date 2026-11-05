#!/usr/bin/env python3
"""Opt-in live free-model acceptance using isolated synthetic application data.

Uses the published, verified Rust SDK and production business components. No
provider, IPC, database, or business response is mocked. Public-source fetching
is reported separately from model-dependent processing of synthetic fixtures.
Run with PYTHONPATH=src; --root must point to a new, disposable directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import shlex
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

import httpx

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.strategies.search import SearchStrategy
from openbiliclaw.llm.service import _background_admission_bypass
from openbiliclaw.llm.sponsored_embedding import select_local_embedding
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime, verify_bundle
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings

HISTORY = [
    ("Rust 所有权与借用入门", "编程", "favorite"),
    ("Rust 生命周期：从例子理解引用", "编程", "like"),
    ("用 Python 和 SQLite 写个人知识库", "编程", "favorite"),
    ("SQLite 索引和慢查询优化实践", "编程", "like"),
    ("Python asyncio 并发任务取消与超时", "编程", "favorite"),
    ("Rust 错误处理 Result 与问号操作符", "编程", "like"),
    ("用 pytest 给 HTTP API 写集成测试", "编程", "favorite"),
    ("FastAPI 请求校验与数据库事务", "编程", "like"),
    ("Docker 部署个人 Python 服务", "编程", "favorite"),
    ("Git 分支和 worktree 实战", "编程", "like"),
    ("阳台种番茄：浇水与光照", "园艺", "favorite"),
    ("盆栽番茄整枝与支架", "园艺", "like"),
]

CANDIDATES = [
    ("Rust 所有权实战：修复借用检查错误", "用可运行例子解释借用检查和所有权移动。", "编程"),
    ("SQLite 查询优化：建立复合索引", "用 EXPLAIN QUERY PLAN 比较索引前后查询。", "编程"),
    ("Python asyncio 超时与取消", "演示异步请求取消和 finally 清理资源。", "编程"),
    ("FastAPI 与 SQLite 事务测试", "讲解用 pytest 验证接口和数据库事务回滚。", "编程"),
    ("Docker 多阶段构建 Python 服务", "构建并运行一个可复现的 Python 容器。", "编程"),
    ("Git worktree 同时维护两个分支", "示范独立工作目录中开发、测试和清理。", "编程"),
    ("Rust 的 Result 错误类型设计", "从错误枚举到问号操作符的练习。", "编程"),
    ("给 Python HTTP 客户端写回归测试", "用本地服务验证重试、超时和响应解析。", "编程"),
    ("阳台番茄浇水和光照记录", "盆栽番茄的日常养护记录，不涉及商业种植。", "园艺"),
    ("盆栽番茄搭支架和整枝", "小阳台番茄支架和整枝的操作示范。", "园艺"),
    ("赌场必胜秘诀与下注攻略", "推销赌博下注与稳赢技巧。", "博彩"),
    ("明星绯闻速报", "娱乐明星八卦和绯闻合集。", "娱乐"),
]


def fixture_events() -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    return [
        {
            "type": kind,
            "event_id": f"sponsored-acceptance-{i}",
            "url": f"https://www.bilibili.com/video/BV1SPT{i:06d}",
            "title": title,
            "timestamp": now - (len(HISTORY) - i) * 86_400_000,
            "source_platform": "bilibili",
            "context": {"pageType": "video"},
            "metadata": {
                "bvid": f"BV1SPT{i:06d}",
                "tags": [tag],
                "author": "合成验收样本",
                "description": title + "，包含原理说明与操作例子。",
            },
        }
        for i, (title, tag, kind) in enumerate(HISTORY)
    ]


def normalized_events(ctx: Any) -> list[dict[str, Any]]:
    rows = ctx.memory_manager.query_events(limit=100)
    for row in rows:
        if isinstance(row.get("metadata"), str):
            row["metadata"] = json.loads(row["metadata"])
    return rows


class RuntimeObservations(logging.Handler):
    """Keep background failures visible even when foreground assertions pass."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("openbiliclaw."):
            return
        message = record.getMessage()
        failed = record.levelno >= logging.ERROR or any(
            marker in message.lower()
            for marker in (
                "timed out",
                "llm call failed",
                "evaluation worker failed",
                "generation failed",
            )
        )
        self.records.append({"logger": record.name, "message": message, "failure": failed})


class Run:
    def __init__(self, root: Path, model: str, report_name: str = "acceptance.json") -> None:
        self.root = root
        self.model = model
        self.report_name = report_name
        self.results: list[dict[str, Any]] = []
        self.ctx: Any = None
        self.complete = False
        self.observations = RuntimeObservations()
        logging.getLogger().addHandler(self.observations)

    def save(self) -> None:
        (self.root / self.report_name).write_text(
            json.dumps(
                {
                    "model": self.model,
                    "complete": self.complete,
                    "cases": self.results,
                    "background_observations": self.observations.records,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    def finish(self) -> None:
        self.complete = True
        self.save()
        logging.getLogger().removeHandler(self.observations)
        if any(case["status"] != "pass" for case in self.results) or any(
            row["failure"] for row in self.observations.records
        ):
            raise SystemExit("Acceptance recorded a case or background failure; inspect the report")

    async def case(
        self,
        name: str,
        call: Callable[[], Awaitable[Any]],
        *,
        timeout: int = 480,
        allowed_models: tuple[str, ...] | None = None,
    ) -> Any:
        print(json.dumps({"start": name, "model": self.model}), flush=True)
        start = time.monotonic()
        before = self.ctx.database.max_llm_usage_id() if self.ctx else 0
        record: dict[str, Any] = {"name": name}
        result: Any = None
        try:
            result = await asyncio.wait_for(call(), timeout)
            record.update(status="pass", result=result)
        except Exception as exc:
            record.update(status="fail", error=f"{type(exc).__name__}: {exc}")
            logging.exception("Acceptance case failed: %s", name)
        record["seconds"] = round(time.monotonic() - start, 3)
        if self.ctx:
            record["calls"] = [
                dict(row)
                for row in self.ctx.database.conn.execute(
                    "SELECT caller,provider,model,prompt_tokens,completion_tokens,success "
                    "FROM llm_usage WHERE id>? ORDER BY id",
                    (before,),
                )
            ]
            if any(row["provider"] != "sponsored" for row in record["calls"]):
                record.update(status="fail", error="An application call escaped the free route")
            if any(
                row["model"] not in (allowed_models or (self.model,)) for row in record["calls"]
            ):
                record.update(status="fail", error="An application call used an unselected model")
        self.results.append(record)
        self.save()
        print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
        return result


async def run_model(
    root: Path, model: str, candidate_count: int = 12, embedding_mode: str = "cloud"
) -> None:
    root.mkdir(parents=True, exist_ok=False)
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    cfg = Config(data_dir=str(root / "data"))
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=True,
        model=model,
        embedding_mode=embedding_mode,
        fallback_to_other_free_model=False,
        fallback_to_user_provider=False,
        request_timeout_seconds=180,
    )
    if embedding_mode == "local":
        select_local_embedding(cfg)
    cfg.scheduler.auto_update_enabled = False
    save_config(cfg, root / "config.toml")
    run = Run(root, model)

    async def install() -> dict[str, Any]:
        command = await asyncio.to_thread(ensure_runtime)
        bundle = Path(shlex.split(command)[0]).parent
        verify_bundle(bundle)
        return {"verified": True, "downloaded_production_sdk": True}

    if not await run.case("install_verified_published_sdk", install, timeout=180):
        run.finish()
        return
    app = create_app()
    ctx = app.state.runtime_context
    run.ctx = ctx
    profile: Any = None
    candidates: list[DiscoveredContent] = []
    served: list[Any] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:18420", timeout=480
    ) as client:

        async def startup() -> dict[str, Any]:
            assert not ctx.degraded, ctx.degraded_reason
            response = await client.get("/api/health")
            response.raise_for_status()
            conf = await client.get("/api/config")
            conf.raise_for_status()
            sponsored = conf.json()["llm"]["sponsored"]
            assert sponsored["enabled"] and sponsored["model"] == model
            assert sponsored["embedding_mode"] == embedding_mode
            assert not sponsored["fallback_to_user_provider"]
            assert not sponsored["fallback_to_other_free_model"]
            return {"health": response.json(), "sponsored": sponsored}

        await run.case("application_startup_and_saved_config", startup)

        async def ingest() -> dict[str, Any]:
            events = fixture_events()[:2]
            for event in events:
                event["event_id"] += "-after-init"
            payload = {"events": events}
            response = await client.post("/api/events", json=payload)
            response.raise_for_status()
            first = response.json()
            response = await client.post("/api/events", json=payload)
            response.raise_for_status()
            count = len(normalized_events(ctx))
            assert first["accepted"] == 2, first
            assert response.json()["duplicates"] == 2, response.json()
            assert count == len(HISTORY) + 2, (count, first, response.json())
            return {"response": first, "replay": response.json(), "stored_events": count}

        async def initial_profile() -> dict[str, Any]:
            nonlocal profile
            from openbiliclaw.sources.event_format import build_event

            # Initial account history enters through memory ingestion; the
            # extension endpoint intentionally rejects events until a profile
            # exists. Exercise that endpoint after the real bootstrap below.
            history = [
                build_event(
                    event_type=item["type"],
                    source_platform=item["source_platform"],
                    title=item["title"],
                    url=item["url"],
                    metadata={**item["metadata"], "timestamp": item["timestamp"]},
                )
                for item in fixture_events()
            ]
            await ctx.memory_manager.propagate_events(history)
            events = normalized_events(ctx)
            # Match CLI init's explicit foreground admission. Without this
            # context a direct module invocation is maintenance traffic, which
            # intentionally waits while the first recommendation pool is empty.
            with _background_admission_bypass():
                await ctx.soul_engine.analyze_events(events)
                profile = await ctx.soul_engine.build_initial_profile(events)
            assert ctx.soul_engine.is_profile_ready()
            assert profile.preferences.interests, "no interests persisted"
            response = await client.get("/api/profile-summary")
            response.raise_for_status()
            return {
                "interests": [asdict(item) for item in profile.preferences.interests],
                "portrait": profile.to_dict(),
                "profile_api_status": response.status_code,
            }

        await run.case("behavior_to_preference_to_persisted_profile", initial_profile)
        await run.case("event_ingress_persistence_and_deduplication", ingest)

        embedding = ctx.recommendation_engine._embedding_service

        async def embed() -> dict[str, Any]:
            texts = ["Rust 所有权与借用检查", "Rust 引用和生命周期", "明天天气如何"]
            vectors = [await embedding.embed(text) for text in texts]
            assert all(len(v) == 1024 and all(math.isfinite(x) for x in v) for v in vectors)
            assert all(sum(x * x for x in v) > 0 for v in vectors)
            assert embedding.lookup_cached(texts[0]) == vectors[0]
            repeated = await embedding.embed(texts[0])
            assert repeated == vectors[0]

            def cosine(left: list[float], right: list[float]) -> float:
                return sum(a * b for a, b in zip(left, right, strict=True)) / math.sqrt(
                    sum(a * a for a in left) * sum(b * b for b in right)
                )

            related, unrelated = cosine(vectors[0], vectors[1]), cosine(vectors[0], vectors[2])
            assert related > unrelated
            return {
                "model": embedding.embedding_model,
                "embedding_mode": embedding_mode,
                "dimensions": 1024,
                "cache_hit": True,
                "related_cosine": related,
                "unrelated_cosine": unrelated,
            }

        await run.case("bge_text_embedding_similarity_and_cache", embed)

        async def discovery() -> dict[str, Any]:
            assert profile is not None, "profile prerequisite failed"
            strategy = SearchStrategy(
                llm_service=ctx.llm_service,
                bilibili_client=ctx.bilibili_client,
                queries_per_run=2,
                page_size=3,
                llm_evaluation=False,
            )
            queries = await strategy._generate_queries(profile)
            assert queries and all(isinstance(x, str) and x.strip() for x in queries)
            return {"queries": queries}

        keywords = await run.case("production_discovery_keyword_generation", discovery)

        async def source_fetch() -> dict[str, Any]:
            assert keywords, "query generation prerequisite failed"
            query = keywords["queries"][0]
            rows = await ctx.bilibili_client.search(query, page=1, page_size=3)
            assert rows, "anonymous public search returned no items"
            (root / "public-source.json").write_text(json.dumps(rows, ensure_ascii=False))
            return {"query": query, "items": len(rows), "titles": [r.get("title") for r in rows]}

        await run.case("anonymous_live_bilibili_search", source_fetch, timeout=60)

        async def evaluate() -> dict[str, Any]:
            nonlocal candidates
            assert profile is not None, "profile prerequisite failed"
            candidates = [
                DiscoveredContent(
                    bvid=f"BV1EVL{i:06d}",
                    title=title,
                    description=description,
                    up_name="合成验收样本",
                    duration=600,
                    view_count=1000,
                    tags=[tag],
                    source_platform="bilibili",
                    source_strategy="search",
                    score_threshold=0.6,
                )
                for i, (title, description, tag) in enumerate(
                    [CANDIDATES[i % len(CANDIDATES)] for i in range(candidate_count)]
                )
            ]
            scores = await ctx.discovery_engine.evaluate_content_batch(candidates, profile)
            assert len(scores) == len(candidates), (len(scores), len(candidates))
            assert all(math.isfinite(x.relevance_score) for x in candidates)
            relevant_rows = [c for i, c in enumerate(candidates) if i % len(CANDIDATES) < 8]
            unrelated_rows = [c for i, c in enumerate(candidates) if i % len(CANDIDATES) >= 10]
            relevant = sum(c.relevance_score for c in relevant_rows) / len(relevant_rows)
            unrelated = sum(c.relevance_score for c in unrelated_rows) / len(unrelated_rows)
            assert relevant > unrelated, (relevant, unrelated)
            await ctx.discovery_engine.normalize_evaluated_results(candidates)
            admitted = ctx.discovery_engine.cache_evaluated_results(candidates)
            assert admitted >= 3, admitted
            await ctx.pool_inventory_commit_callback()
            return {
                "input_count": len(candidates),
                "scores": [
                    {"title": c.title, "score": c.relevance_score, "reason": c.relevance_reason}
                    for c in candidates
                ],
                "admitted": admitted,
            }

        await run.case("production_batch_evaluation_and_pool_admission", evaluate)

        async def recommend() -> dict[str, Any]:
            nonlocal served
            assert profile is not None, "profile prerequisite failed"
            copied = await ctx.recommendation_engine.precompute_pool_copy(
                profile=profile, limit=12, delight_limit=12
            )
            served = await ctx.recommendation_engine.serve(profile, limit=3)
            assert len(served) == 3, (len(served), copied)
            assert all(item.expression.strip() for item in served)
            return {"copy_generated": copied, "served": [asdict(item) for item in served]}

        await run.case("batch_copy_ranking_and_recommendation_delivery", recommend)

        async def feedback() -> dict[str, Any]:
            assert len(served) == 3, "recommendation prerequisite failed"
            for i, item in enumerate(served):
                row = ctx.database.get_recommendation_by_identity(bvid=item.content.bvid)
                assert row
                response = await client.post(
                    "/api/feedback",
                    json={
                        "recommendation_id": row["id"],
                        "feedback_type": "like" if i < 2 else "comment",
                        "note": "喜欢这种有可运行代码的原理讲解，请保留编程教程。",
                        "request_id": f"acceptance-feedback-{i}",
                    },
                )
                response.raise_for_status()
            result = await ctx.soul_engine.process_feedback_batch_if_needed()
            rows = ctx.database.conn.execute(
                "SELECT count(*) AS n FROM recommendations WHERE feedback_type != ''"
            ).fetchone()
            assert rows["n"] >= 3
            return {"persisted_feedback": rows["n"], "learning": result}

        await run.case("feedback_persistence_and_profile_learning", feedback)

        async def agent_tools() -> dict[str, Any]:
            events = []
            async for event in ctx.agent_loop.run(
                system_instruction=(
                    "你是验收助手。必须先调用 get_profile 查询用户画像，再调用 read_memory "
                    "读取偏好或记忆，最后用中文概括用户两个主要兴趣。不要写入或修改任何数据。"
                ),
                user_message="请根据实际画像和记忆，概括我的两个主要兴趣。",
            ):
                events.append(event.to_dict())
            tool_results = [e for e in events if e["type"] == "tool_result"]
            finals = [e for e in events if e["type"] == "final"]
            assert tool_results and all(e.get("ok", True) for e in tool_results), events
            assert finals and finals[-1].get("text"), events
            assert {"get_profile", "read_memory"} <= {e.get("tool_name") for e in tool_results}, (
                "The model did not execute both explicitly requested tools"
            )
            return {"events": events}

        await run.case("agent_real_tool_execution_and_final_answer", agent_tools)

        async def dialogue_learning() -> dict[str, Any]:
            result = await ctx.dialogue_settlement_queue.submit_and_wait(
                "learn",
                {
                    "user_message": "我明确喜欢 Rust 编程教程，也喜欢阳台种番茄；请不要给我推荐赌博内容。",
                    "assistant_reply": "记住了：Rust 教程和阳台番茄养护；避开赌博内容。",
                    "session": "desktop",
                    "scope": "chat",
                    "turn_id": "acceptance-explicit-learning",
                },
            )
            assert result.outcome == "completed", result
            profile_after = await ctx.soul_engine.get_profile()
            return {"learning": asdict(result), "profile_after": profile_after.to_dict()}

        await run.case("explicit_dialogue_learning", dialogue_learning)

        async def consolidation() -> dict[str, Any]:
            from openbiliclaw.soul.consolidator import _Cluster

            consolidator = ctx.soul_engine._profile_consolidator
            # The learned fixture can have no duplicate clusters. A no-op dry
            # run must not be reported as proof of a real fixed-task API call.
            clusters = [
                _Cluster(cluster_id=f"live-{i}", scope="likes", members=[name, name + "实践"])
                for i, name in enumerate(
                    ["Rust所有权", "Python异步编程", "SQLite索引优化", "盆栽番茄养护"]
                )
            ]
            with _background_admission_bypass():
                report = await consolidator.run(dry_run=True)
                operations = await consolidator._judge(clusters)
            assert not report.errors, report.errors
            problems = {
                cluster.cluster_id: consolidator._validate_cluster_ops(
                    cluster, operations.get(cluster.cluster_id, [])
                )
                for cluster in clusters
            }
            assert not any(problems.values()), problems
            return {
                **asdict(report),
                "live_judge_clusters": len(clusters),
                "operations": operations,
            }

        await run.case("profile_consolidation_and_embedding_recall", consolidation)

        async def persistence() -> dict[str, Any]:
            from openbiliclaw.api.runtime_context import build_runtime_context
            from openbiliclaw.config import load_config

            restored = build_runtime_context(load_config(consult_environment=False))
            try:
                loaded = await restored.soul_engine.get_profile()
                assert loaded.preferences.interests
                assert len(normalized_events(restored)) >= len(HISTORY)
                assert restored.config.llm.sponsored.model == model
                return {
                    "restored_interests": len(loaded.preferences.interests),
                    "restored_events": len(normalized_events(restored)),
                    "saved_model": restored.config.llm.sponsored.model,
                }
            finally:
                await restored.bilibili_client.close()
                restored.database.close()

        await run.case("database_and_profile_reload", persistence)

    await ctx.soul_engine.wait_for_pending_edits()
    await ctx.task_registry.cancel_all()
    if ctx.dialogue_settlement_queue:
        await ctx.dialogue_settlement_queue.shutdown(timeout=10)
    await close_shared_provider()
    await ctx.bilibili_client.close()
    ctx.database.close()
    run.finish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--model", choices=CHAT_MODELS, required=True)
    parser.add_argument("--candidate-count", type=int, choices=(12, 30), default=12)
    parser.add_argument("--embedding-mode", choices=("cloud", "local"), default="cloud")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(
            run_model(args.root.resolve(), args.model, args.candidate_count, args.embedding_mode)
        )
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
