"""Durable candidate identity survives every proactive delight surface."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.sources.custom_sources import CustomSourceStore
from openbiliclaw.sources.web_public import web_content_id
from openbiliclaw.storage.database import Database
from tests.test_discovery_candidate_pipeline import _build_profile, _ScoringLLM


@pytest.mark.parametrize("custom_source", [True, False])
async def test_admitted_source_identity_reaches_all_delight_surfaces(tmp_path, custom_source):
    database = Database(tmp_path / "delight.db")
    database.initialize()
    try:
        source = (
            CustomSourceStore(database).upsert_source(
                name="Django 官方博客",
                entry_url="https://www.djangoproject.com/weblog/",
                intent="Django maintenance and releases",
            )
            if custom_source
            else {"source_id": "", "name": ""}
        )
        url = "https://www.djangoproject.com/weblog/identity-test/"
        item = DiscoveredContent(
            content_id=web_content_id(url),
            content_url=url,
            title="Django reference guide",
            source_platform="web",
            source_strategy="web-workflow",
            source_id=source["source_id"],
            source_name=source["name"],
            content_type="article",
            published_at=datetime.now(UTC).isoformat(),
        )
        llm = _ScoringLLM(
            [
                {
                    "content_id": item.content_id,
                    "score": 0.95,
                    "reason": "Relevant reference material",
                    "topic_group": "Django",
                    "style_key": "reference",
                    "temporal_class": "evergreen",
                    "temporal_confidence": 0.95,
                    "temporal_reason": "Reference material is not time-sensitive",
                    "temporal_validity_mode": "none",
                    "temporal_valid_until": "",
                    "temporal_scope": "none",
                    "temporal_evidence": "",
                    "temporal_state": "unknown",
                }
            ]
        )
        engine = ContentDiscoveryEngine(llm_service=llm, database=database)
        pipeline = DiscoveryCandidatePipeline(database, engine, pool_target_count=30)
        assert pipeline.enqueue_candidates([item], source_context="web-workflow") == 1
        claim = pipeline.claim_batch(limit=1)
        assert claim is not None
        outcome = await pipeline.evaluate_claim(claim, _build_profile())
        result = await pipeline.complete_claim(outcome, admission_limit=10)
        assert result == {"evaluated": 1, "cached": 1, "rejected": 0, "stale": 0}
        row = dict(database.conn.execute("SELECT * FROM content_cache").fetchone())
        key = row["item_key"]
        assert key == f"web:{item.content_id}"
        assert row["source_id"] == source["source_id"]
        assert row["source_name"] == source["name"]
        database.update_pool_copy(
            row["bvid"], expression="A useful Django guide", topic_label="Django"
        )
        assert database.update_delight_score(
            row["bvid"],
            delight_score=0.95,
            delight_reason="A useful Django guide",
            delight_hook="Django",
        )
        # Pausing discovery keeps already admitted content and its attribution.
        if custom_source:
            CustomSourceStore(database).update_source(source["source_id"], enabled=False)
        runtime_state = {}
        memory = SimpleNamespace(
            load_discovery_runtime_state=lambda: dict(runtime_state),
            update_discovery_runtime_state=lambda update: update(runtime_state),
        )
        soul = SimpleNamespace(get_effective_disliked_topics=lambda: [])
        events = SimpleNamespace(publish=AsyncMock(return_value=True))
        runtime = ContinuousRefreshController(
            memory_manager=memory,
            database=database,
            soul_engine=soul,
            discovery_engine=engine,
            recommendation_engine=object(),
            event_hub=events,
        )
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=soul,
            runtime_controller=runtime,
            runtime_event_hub=events,
        )
        client = TestClient(app)
        batch = client.get("/api/delight/pending-batch")
        single = client.get("/api/delight/pending")
        triggered = client.post("/api/delight/trigger", json={"count": 1})
        assert batch.status_code == single.status_code == triggered.status_code == 200
        await runtime._publish_delight_if_available()
        pushed = [call.args[0] for call in events.publish.await_args_list]
        assert len(pushed) == 2 and all(p["type"] == "delight.candidate" for p in pushed)
        for payload in [batch.json()["items"][0], single.json()["item"], *pushed]:
            assert payload["item_key"] == key
            assert payload["content_id"] == item.content_id
            assert payload["content_url"] == url
            assert payload["source_platform"] == "web"
            assert payload["source_id"] == source["source_id"]
            assert payload["source_name"] == source["name"]
        assert database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 1
    finally:
        database.close()
