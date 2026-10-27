"""Opt-in live TikTok transport and real configured evaluator smoke, isolated storage.

Usage: PYTHONPATH=src python scripts/smoke_tiktok_pipeline.py --config /path/config.toml
Reports counts and provider names, plus public recommendation DTOs in a separate file.
It never writes to the configured data root. This full-pipeline test intentionally
writes candidates/recommendations/scheduling state inside its temporary database;
the projection-free transport smoke is the separate discover-tiktok CLI command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

from openbiliclaw.api.runtime_context import build_tiktok_discovery_producer
from openbiliclaw.config import load_config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.llm.registry import build_embedding_service
from openbiliclaw.llm.service import LLMService, module_overrides_from_config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.network import set_outbound_proxy
from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database


async def run(config_path: Path, output: Path) -> bool:
    cfg = load_config(config_path)
    report: dict[str, object] = {
        "chat_provider": cfg.llm.default_provider,
        "storage": "isolated",
        "profile": "synthetic science interest",
    }
    set_outbound_proxy(cfg.network.proxy, mode=cfg.network.mode)
    with tempfile.TemporaryDirectory(prefix="openbiliclaw-tiktok-pipeline-") as tmp:
        cfg.data_dir = tmp
        cfg.storage.db_path = str(Path(tmp) / "smoke.db")
        cfg.sources.tiktok.enabled = True
        cfg.sources.tiktok.mode = "web"
        # A bounded explicit keyword is injected through the real coordinator;
        # this isolates source acceptance from unrelated keyword generation.
        cfg.discovery.unified_keyword_planner_enabled = True
        db = Database(Path(cfg.storage.db_path))
        db.initialize()
        db.insert_pending_keywords("tiktok", ["science"], "live-smoke")
        registry = build_llm_registry(cfg)
        embedding = build_embedding_service(cfg, registry)
        service = LLMService(
            registry=registry,
            memory=MemoryManager(Path(tmp), database=db),
            module_overrides=module_overrides_from_config(cfg),
        )
        engine = ContentDiscoveryEngine(
            llm_service=service,
            database=db,
            embedding_service=embedding,
            eval_prefilter_mode=cfg.discovery.eval_prefilter_mode,
            eval_scorer=cfg.discovery.eval_scorer,
        )
        pipeline = DiscoveryCandidatePipeline(
            database=db,
            discovery_engine=engine,
            admission_min_score=cfg.discovery.admission_min_score,
        )
        profile = SoulProfile(
            preferences=PreferenceLayer(
                interests=[
                    InterestTag(
                        name="science experiments and physics explanations",
                        category="science",
                        weight=0.95,
                    )
                ]
            )
        )

        async def get_profile():
            return profile

        producer = build_tiktok_discovery_producer(
            config=cfg,
            database=db,
            soul_engine=SimpleNamespace(get_profile=get_profile),
            discovery_engine=engine,
            llm_service=service,
            concurrency=None,
            candidate_pipeline=pipeline,
            keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=cfg.discovery),
            enabled_override=True,
        )
        assert producer is not None
        producer.strategies = ("tiktok_tag",)
        producer.daily_tag_budget = 1
        try:
            result = await producer.produce_if_due(limit=4)
            report["pipeline"] = {
                k: v
                for k, v in result.items()
                if k in {"reason", "discovered", "enqueued", "evaluated", "cached", "rejected"}
            }
            if embedding is not None:
                vector = await embedding.embed("Science experiments and physics explanations")
                report["embedding_dimensions"] = len(vector)
            report["candidate_statuses"] = dict(
                db.conn.execute(
                    "SELECT status, COUNT(*) FROM discovery_candidates GROUP BY status"
                ).fetchall()
            )
            report["admitted"] = len(pipeline.last_admitted_items)
            from unittest.mock import patch

            import httpx

            from openbiliclaw.api.app import create_app
            from openbiliclaw.config import ApiAuthConfig
            from openbiliclaw.recommendation.engine import RecommendationEngine

            recommender = RecommendationEngine(service, db, embedding_service=embedding)
            report["copy_ready"] = await recommender.precompute_pool_copy(
                profile=profile, limit=4, delight_limit=0
            )
            served = await recommender.serve(profile, limit=3, source_platform="tiktok")
            report["served"] = len(served)
            cfg.api.auth = ApiAuthConfig(enabled=False)
            cfg.scheduler.enabled = False
            with patch("openbiliclaw.config.load_config", return_value=cfg):
                app = create_app(
                    database=db,
                    memory_manager=service.memory,
                    soul_engine=SimpleNamespace(get_profile=get_profile, _llm_service=service),
                    recommendation_engine=recommender,
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as http:
                    response = await http.get("/api/recommendations")
                    report["recommendations_http"] = response.status_code
                    if response.status_code == 200:
                        payload = response.json()
                        rows = payload.get("recommendations", payload.get("items", []))
                        report["api_items"] = len(rows)
                        report["api_tiktok_identity_valid"] = bool(rows) and all(
                            row.get("source_platform") == "tiktok"
                            and str(row.get("content_url", "")).startswith(
                                "https://www.tiktok.com/"
                            )
                            for row in rows
                        )
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.with_name("recommendations.json").write_text(
                            json.dumps(payload, ensure_ascii=False)
                        )
            report["item_identity_valid"] = all(
                item.item_key.startswith("tiktok:")
                and item.content_url.startswith("https://www.tiktok.com/")
                for item in pipeline.last_admitted_items
            )
        except Exception as exc:
            report["error"] = str(getattr(exc, "reason", type(exc).__name__))
        finally:
            db.close()
    report["passed"] = bool(
        "error" not in report
        and report.get("admitted", 0)
        and report.get("embedding_dimensions", 0)
        and report.get("copy_ready", 0)
        and report.get("served", 0)
        and report.get("recommendations_http") == 200
        and report.get("api_tiktok_identity_valid")
        and report.get("item_identity_valid")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return bool(report["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("output/tiktok-readiness/live-pipeline.json")
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    raise SystemExit(0 if asyncio.run(run(args.config, args.output)) else 1)
