"""Public Web tabs aggregate custom inventory without changing source quotas."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.storage.database import Database
from tests.test_custom_source_runtime import _cache, _item, _source
from tests.test_keyword_planner import _profile
from tests.test_recommendation_engine import _DummyLLM, _seed_visible


@pytest.fixture
def inventory(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    first = _source(database, "Release news", target_share=1)
    second = _source(database, "Tutorials", target_share=3)
    items = [_item(first, "one"), _item(second, "two")]
    for item in items:
        _cache(database, item)
    # A second provenance for one URL must not add a second inventory credit.
    _cache(database, _item(second, "one"))
    _seed_visible(database, "BVother", title="Other platform", source_platform="bilibili")
    yield database, first, second, items
    database.close()


def test_custom_inventory_reports_canonical_web_and_preserves_instance_quotas(inventory):
    database, first, second, _ = inventory
    availability = database.load_pool_platform_availability()
    assert availability.by_platform == {"web": 2, "bilibili": 1}
    assert availability.total_available == sum(availability.by_platform.values()) == 3
    assert database.count_pool_available_candidates_by_source() == {
        first["source_id"]: 1,
        second["source_id"]: 1,
        "bilibili": 1,
    }


def test_web_scope_includes_custom_sources_without_losing_internal_instance_scope(inventory):
    database, first, second, items = inventory
    rows = database.get_pool_candidates_for_platform("web", limit=10)
    assert {row["source_id"] for row in rows} == {first["source_id"], second["source_id"]}
    snapshot = database.load_pool_serve_snapshot(limit=10, source_platform="web")
    assert {row["source_id"] for row in snapshot.candidate_rows} == {
        first["source_id"],
        second["source_id"],
    }
    assert len(database.get_pool_candidates_for_platform(first["source_id"])) == 1
    engine = RecommendationEngine.__new__(RecommendationEngine)
    assert engine._enforce_platform_scope(items, "web") == items
    assert engine._enforce_platform_scope(items, first["source_id"]) == [items[0]]
    assert engine._enforce_platform_scope(items, "bilibili") == []


def test_public_web_append_serves_real_custom_inventory_with_names(inventory, monkeypatch):
    monkeypatch.setenv("OPENBILICLAW_RECOMMENDATION_ONLY", "1")
    database, first, second, _ = inventory
    llm = _DummyLLM()
    engine = RecommendationEngine(llm=llm, database=database)
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=SimpleNamespace(get_profile=AsyncMock(return_value=_profile())),
        recommendation_engine=engine,
    )
    # Route integration only: boot-time automatic serving would consume this inventory.
    client = TestClient(app)
    try:
        response = client.get("/api/recommendations/platform-availability")
        assert response.status_code == 200
        assert response.json()["by_platform"] == {"web": 2, "bilibili": 1}
        for path in ("append", "reshuffle"):
            assert (
                client.post(
                    f"/api/recommendations/{path}",
                    json={"source_platform": first["source_id"]},
                ).status_code
                == 422
            )
        served = client.post("/api/recommendations/append", json={"source_platform": "web"})
        assert served.status_code == 200
        items = served.json()["items"]
        assert {item["source_id"] for item in items} == {
            first["source_id"],
            second["source_id"],
        }
        assert {item["source_name"] for item in items} == {"Release news", "Tutorials"}
        assert all(item["source_platform"] == "web" for item in items)
        assert all(item["item_key"].startswith("web:") for item in items)
        assert llm.calls == []  # Existing copy is served; no model response is injected.
    finally:
        client.close()
