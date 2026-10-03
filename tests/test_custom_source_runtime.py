"""Dynamic discovery sources share canonical content and existing quota machinery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import Config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import candidate_key_for, row_to_discovered_content
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
from openbiliclaw.runtime.source_policy import effective_pool_source_shares, source_enabled_map
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.sources.custom_sources import CustomSourceStore
from openbiliclaw.sources.web_public import web_content_id
from openbiliclaw.sources.workflow_store import WorkflowStore
from openbiliclaw.storage.database import Database
from tests.test_keyword_planner import (
    _discovery_cfg,
    _FakeDeficitSource,
    _FakeLLM,
    _make_planner,
    _pending,
    _profile,
)
from tests.test_refresh_source_deficit import _controller

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "custom.db")
    database.initialize()
    yield database
    database.close()


def _source(db: Database, name: str = "Articles", **kwargs: Any) -> dict[str, Any]:
    return CustomSourceStore(db).upsert_source(
        name=name,
        entry_url="https://www.djangoproject.com/weblog/",
        intent="Django releases",
        **kwargs,
    )


def _item(source: dict[str, Any], slug: str = "one") -> DiscoveredContent:
    url = f"https://www.djangoproject.com/weblog/{slug}/"
    return DiscoveredContent(
        content_id=web_content_id(url),
        content_url=url,
        title=f"Django {slug}",
        source_platform="web",
        source_strategy="web-workflow",
        content_type="article",
        source_id=source["source_id"],
        source_name=source["name"],
        published_at=datetime.now(UTC).isoformat(),
        relevance_score=0.9,
        relevance_reason="useful",
        topic_group=f"topic-{slug}",
        style_key="analysis",
        temporal_class="evergreen",
        temporal_confidence=0.9,
        temporal_reason="reference",
    )


def _cache(db: Database, item: DiscoveredContent) -> None:
    kwargs = item.to_cache_kwargs()
    kwargs.update(pool_expression="Useful article", pool_topic_label="Django")
    db.cache_content(item.bvid or item.content_id, **kwargs)


def _search_workflow(db: Database, source_id: str, query_mode: str = "interest") -> None:
    store = WorkflowStore(db)
    draft = store.save_draft(
        {
            "workflow_id": "",
            "base_version": 0,
            "intent": "Django releases",
            "status": "ready",
            "trial_success": True,
            "executor": "http",
            "query_mode": query_mode,
        }
    )
    store.publish(draft["draft_id"], source_id)


def test_same_origin_sources_have_independent_shares_and_policy(db: Database) -> None:
    first = _source(db, "Release news", target_share=1, session_id="shared-login")
    second = _source(db, "Tutorials", target_share=3, session_id="shared-login")
    config = Config()
    config.sources.bilibili.enabled = False
    config.scheduler.pool_source_shares["custom_unregistered"] = 20
    shares = effective_pool_source_shares(config, database=db)
    assert shares == {first["source_id"]: 1, second["source_id"]: 3}
    assert first["origin"] == second["origin"]
    assert first["source_id"] != second["source_id"]
    controller = _controller(
        database=db,
        pool_target_count=20,
        pool_source_shares_provider=lambda: effective_pool_source_shares(config, database=db),
    )
    assert controller._source_target_counts() == {first["source_id"]: 5, second["source_id"]: 15}
    CustomSourceStore(db).update_source(first["source_id"], enabled=False, name="Paused")
    assert not source_enabled_map(config, database=db)[first["source_id"]]
    assert controller._source_target_counts() == {second["source_id"]: 20}
    assert CustomSourceStore(db).get_source(first["source_id"])["session_id"] == "shared-login"
    assert db._clean_pool_quotas(
        {first["source_id"]: 1, second["source_id"]: 3, "custom_unregistered": 30}
    ) == {second["source_id"]: 3}


@pytest.mark.parametrize(
    "fields",
    [
        {"target_share": 21},
        {"target_share": True},
        {"interval_minutes": 4},
        {"entry_url": "https://other.example.com/"},
        {"session_id": "other-login"},
        {"intent": "Changed target"},
    ],
)
def test_policy_update_rejects_invalid_values_and_identity_mutation(
    db: Database, fields: dict[str, Any]
) -> None:
    source = _source(db)
    with pytest.raises(ValueError):
        CustomSourceStore(db).update_source(source["source_id"], **fields)
    assert CustomSourceStore(db).get_source(source["source_id"]) == source


def test_duplicate_url_keeps_one_candidate_and_inventory_with_all_provenance(db: Database) -> None:
    first, second = _source(db, "A"), _source(db, "B")
    engine = ContentDiscoveryEngine(llm_service=SimpleNamespace(), database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=engine, pool_target_count=20
    )
    a, b = _item(first), _item(second)
    assert candidate_key_for(a) == candidate_key_for(b)
    assert pipeline.enqueue_candidates([a, b], source_context="web-workflow") == 1
    row = dict(db.conn.execute("SELECT * FROM discovery_candidates").fetchone())
    restored = row_to_discovered_content(row)
    assert restored.source_id == first["source_id"]
    assert restored.source_name == "A"
    assert {p["source_id"] for p in restored.source_metadata["source_provenance"]} == {
        first["source_id"],
        second["source_id"],
    }
    _cache(db, a)
    assert pipeline.enqueue_candidates([b], source_context="web-workflow") == 0
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 1
    assert db.count_pool_candidates_by_source() == {first["source_id"]: 1}
    assert db.count_pool_available_candidates_by_source() == {first["source_id"]: 1}
    cached = dict(db.conn.execute("SELECT * FROM content_cache").fetchone())
    assert len(json.loads(cached["source_metadata"])["source_provenance"]) == 2
    assert cached["item_key"] == candidate_key_for(a)
    assert db.get_pool_candidates_for_platform(second["source_id"]) == []
    assert len(db.get_pool_candidates_for_platform(first["source_id"])) == 1
    recommendation = RecommendationEngine.__new__(RecommendationEngine)
    mapped = recommendation._rows_to_discovered([cached])[0]
    assert mapped.source_id == first["source_id"]
    assert mapped.source_platform == "web"
    assert recommendation._enforce_platform_scope([mapped], first["source_id"]) == [mapped]
    _cache(db, b)
    assert db.count_pool_candidates_by_source() == {first["source_id"]: 1}
    rid = db.insert_recommendation(
        a.bvid or a.content_id,
        item_key=candidate_key_for(a),
        confidence=0.9,
        expression="Useful article",
    )
    stored = db.get_recommendations()[0]
    assert stored["id"] == rid and stored["item_key"] == candidate_key_for(a)
    assert stored["source_id"] == first["source_id"]
    saved = SavedItemInput(
        source_platform="web",
        content_id=a.content_id,
        content_url=a.content_url,
        content_type="article",
        title=a.title,
    )
    db.upsert_saved_membership("favorite", saved)
    db.upsert_saved_membership("watch_later", saved)
    CustomSourceStore(db).update_source(first["source_id"], name="Renamed")
    for kind in ("favorite", "watch_later"):
        member = db.list_saved_memberships(kind)[0]
        assert member["item_key"] == candidate_key_for(a)
        assert member["source_id"] == first["source_id"]
        assert member["source_name"] == "Renamed"
    assert db.get_recommendations()[0]["source_name"] == "Renamed"


def test_dynamic_claim_priority_uses_source_instance_and_rejects_unknown(db: Database) -> None:
    first, second = _source(db, "A"), _source(db, "B")
    engine = ContentDiscoveryEngine(llm_service=SimpleNamespace(), database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=engine, pool_target_count=20
    )
    assert (
        pipeline.enqueue_candidates(
            [_item(first, "one"), _item(second, "two")], source_context="web-workflow"
        )
        == 2
    )
    rows = db.claim_discovery_candidates_for_eval(
        limit=1, claim_token="test", preferred_source_platforms=[second["source_id"]]
    )
    assert rows[0]["source_id"] == second["source_id"]
    forged = _item({"source_id": "custom_" + "a" * 32, "name": "Unknown"}, "unknown")
    with pytest.raises(ValueError, match="registered source"):
        pipeline.enqueue_candidates([forged], source_context="web-workflow")
    with pytest.raises(ValueError, match="registered source"):
        _cache(db, forged)


async def test_dynamic_planner_generates_claimable_keywords_and_both_tracks_have_context(
    db: Database,
) -> None:
    first = _source(db, "Django search", session_id="private-session")
    no_query = _source(db, "Django feed")
    disabled = _source(db, "Disabled", enabled=False)
    _search_workflow(db, first["source_id"])
    _search_workflow(db, no_query["source_id"], "none")
    _search_workflow(db, disabled["source_id"])
    cfg = _discovery_cfg()
    llm = _FakeLLM({first["source_id"]: ["Django async ORM"]})
    profile = _profile(("Python", 0.9))
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        discovery=cfg,
        deficit=_FakeDeficitSource(
            deficits={
                first["source_id"]: 5,
                no_query["source_id"]: 5,
                disabled["source_id"]: 5,
                "custom_fake": 5,
            }
        ),
    )
    assert planner._due_platforms(profile_kw_digest(profile)) == [first["source_id"]]
    ledger = await planner.run_once()
    assert ledger[first["source_id"]] > 0
    assert "Django async ORM" in _pending(db, first["source_id"], profile_kw_digest(profile))
    prompt = llm.calls[0]["user"]
    assert "Django search" in prompt and "Django releases" in prompt
    assert "private-session" not in prompt
    guides = planner._inspiration_pipeline._inspiration_platform_guides(
        [first["source_id"]], profile=profile, recent_by_platform={}
    )
    assert guides[0]["source_name"] == "Django search"
    assert guides[0]["source_origin"] == "https://www.djangoproject.com"
    assert guides[0]["source_intent"] == "Django releases"
    coordinator = KeywordFetchCoordinator(database=db, discovery_config=cfg)
    claimed = coordinator.claim(first["source_id"], n=1)
    assert len(claimed) == 1
    assert (
        db.conn.execute(
            "SELECT platform FROM discovery_keywords WHERE id = ?", (claimed[0].id,)
        ).fetchone()[0]
        == first["source_id"]
    )
    assert claimed[0].keyword == "Django async ORM"
    coordinator.mark_used(claimed)
    CustomSourceStore(db).update_source(first["source_id"], enabled=False)
    assert first["source_id"] not in planner._planner_platforms()


async def test_one_shot_refresh_plans_and_runs_custom_source_without_bilibili_plan(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db)
    events: list[object] = []

    class Planner:
        enabled = True

        def bind_deficit_source(self, controller: object) -> None:
            events.append("bound")

        async def run_once(self) -> None:
            events.append("planned")

    class Producer:
        async def produce_if_due(self, *, limit: int) -> dict[str, object]:
            events.append(limit)
            return {"enqueued": 1, "discovered": 1}

    controller = _controller(
        database=db,
        pool_target_count=20,
        keyword_planner=Planner(),
        pool_source_shares_provider=lambda: {source["source_id"]: 1},
        custom_source_producer_provider=lambda _: Producer(),
        one_shot_inline_eval_limit=4,
    )
    monkeypatch.setattr(type(controller), "_build_refresh_plan", lambda *_: [])
    result = await controller.refresh_if_needed()
    assert events == ["bound", "planned", 4]
    assert result["refreshed"] is True
    assert result["custom_source_results"][source["source_id"]]["enqueued"] == 1


async def test_custom_tick_isolates_failures_and_reloads_disabled_sources(db: Database) -> None:
    first, second = _source(db, "A"), _source(db, "B")
    calls: list[str] = []

    class Producer:
        def __init__(self, source_id: str) -> None:
            self.source_id = source_id

        async def produce_if_due(self, *, limit: int) -> dict[str, object]:
            calls.append(self.source_id)
            if self.source_id == first["source_id"]:
                raise RuntimeError("temporary executor failure")
            return {"enqueued": 1}

    controller = _controller(
        database=db,
        pool_target_count=20,
        pool_source_shares_provider=lambda: {
            first["source_id"]: 1,
            second["source_id"]: 3,
            "custom_fake": 99,
        },
        custom_source_producer_provider=Producer,
    )
    results = await controller._tick_custom_source_producers()
    assert results[first["source_id"]]["reason"] == "error"
    assert results[second["source_id"]]["enqueued"] == 1
    assert set(calls) == {first["source_id"], second["source_id"]}
    calls.clear()
    CustomSourceStore(db).update_source(first["source_id"], enabled=False)
    await controller._tick_custom_source_producers()
    assert calls == [second["source_id"]]


def test_two_round_admission_does_not_aggregate_custom_web_sources() -> None:
    from tests.test_candidate_pipeline_admission import _accepted, _FakeAdmissionDB, _pipeline

    first, second = "custom_" + "a" * 32, "custom_" + "b" * 32
    db = _FakeAdmissionDB(available={first: 299}, pool_count=299)
    pipeline = _pipeline(db, share_targets={first: 75, second: 225})
    accepted = _accepted([(1, "web", "web-workflow"), (2, "web", "web-workflow")])
    for (row, item), source_id in zip(accepted, (first, second), strict=True):
        row["source_id"] = source_id
        item.source_id = source_id
    cached, rejected = pipeline._admit_until_full(
        accepted,
        recently_viewed=set(),
        admitted_items=[],
        limit=None,
    )
    assert (cached, rejected) == (1, 0)
    assert db.cached_ids == [2]
    assert pipeline.pool_full_for_source(first)
    assert not pipeline.pool_full_for_source(second)


def test_archived_source_is_hidden_but_history_and_shared_session_remain(db: Database) -> None:
    source = _source(db, session_id="shared")
    store = CustomSourceStore(db)
    item = _item(source)
    _cache(db, item)
    assert store.archive_source(source["source_id"])
    assert not store.archive_source(source["source_id"])
    assert store.list_sources() == []
    archived = store.get_source(source["source_id"])
    assert archived["archived"] and not archived["enabled"]
    assert archived["session_id"] == "shared"
    assert (
        db.get_content_source_provenance(candidate_key_for(item))[0]["source_id"]
        == source["source_id"]
    )
    with pytest.raises(ValueError, match="Archived"):
        store.update_source(source["source_id"], enabled=True)
    assert source["source_id"] not in effective_pool_source_shares(Config(), database=db)


def test_source_create_and_policy_update_can_join_publishing_transaction(db: Database) -> None:
    store = CustomSourceStore(db)
    db.conn.execute("BEGIN IMMEDIATE")
    created = store.upsert_source(
        name="Pending",
        entry_url="https://example.com/",
        intent="Python",
        enabled=False,
        commit=False,
    )
    updated = store.update_source(created["source_id"], enabled=True, commit=False)
    assert updated["enabled"]
    assert db.conn.in_transaction
    other = db.open_connection()
    try:
        assert other.execute("SELECT COUNT(*) FROM custom_sources").fetchone()[0] == 0
    finally:
        other.close()
    db.conn.rollback()
    assert store.get_source(created["source_id"]) is None


async def test_custom_explore_pipeline_persists_separate_claimable_keyword_kind(
    db: Database,
) -> None:
    from openbiliclaw.discovery.inspiration import ExaPreviewItem
    from tests.test_keyword_planner import _FakeInspirationProvider, _SequentialLLM

    source = _source(db, "Django search")
    source_id = source["source_id"]
    _search_workflow(db, source_id)
    profile = _profile(("Python", 0.9))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "Python",
                        "axis_label": "geospatial",
                        "axis_kind": "method",
                        "example_terms": ["GeoDjango"],
                    }
                ],
                "keywords": [
                    {
                        "interest": "Python",
                        "axis_id_or_label": "geospatial",
                        "platform": source_id,
                        "core_concept": "Python GeoDjango tutorial",
                    }
                ],
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Python": [
                ExaPreviewItem(
                    title="Python GeoDjango",
                    url="https://example.com/gis",
                    highlights=("GeoDjango tutorial",),
                )
            ],
        }
    )
    cfg = _discovery_cfg(inspiration_search_enabled=True, inspiration_max_keywords_per_platform=1)
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={source_id: 5}),
        discovery=cfg,
        inspiration_provider=provider,
    )
    ledger = await planner._generate_custom_explore([source_id], profile=profile, digest=digest)
    assert ledger[source_id] == 1
    assert _pending(db, source_id, digest) == []
    assert _pending(db, source_id, digest, keyword_kind="explore") == ["Python GeoDjango tutorial"]
    assert "Django search" in llm.calls[0]["user"]
    coordinator = KeywordFetchCoordinator(database=db, discovery_config=cfg)
    assert coordinator.claim(source_id, n=1) == []
    claimed = coordinator.claim(source_id, n=1, keyword_kind="explore")
    assert [word.keyword for word in claimed] == ["Python GeoDjango tutorial"]
    coordinator.mark_used(claimed)


async def test_openclaw_scopes_by_registered_instance_and_preserves_content_family(
    db: Database,
) -> None:
    from openbiliclaw.integrations.openclaw.errors import AdapterValidationError
    from openbiliclaw.recommendation.engine import Recommendation
    from tests.test_openclaw_adapter import _build_adapter

    first, second = _source(db, "A"), _source(db, "B")
    one, two = _item(first, "one"), _item(second, "two")
    adapter, *_ = _build_adapter()
    adapter.services.database = db
    calls = []

    async def serve(profile, **kwargs):
        calls.append(kwargs)
        return [Recommendation(content=item, confidence=0.9) for item in (one, two)]

    adapter.services.recommendation_engine = SimpleNamespace(serve=serve)
    response = await adapter.recommend(source_platform=first["source_id"])
    assert calls[0]["source_platform"] == first["source_id"]
    assert len(response.items) == 1
    assert response.items[0].source_id == first["source_id"]
    assert response.items[0].source_name == "A"
    assert response.items[0].source_platform == "web"
    with pytest.raises(AdapterValidationError):
        await adapter.recommend(source_platform="custom_" + "c" * 32)
    _cache(db, one)
    db.insert_recommendation(
        one.bvid or one.content_id, confidence=0.9, item_key=candidate_key_for(one)
    )
    history = await adapter.recommend(source_platform=first["source_id"], refresh_if_needed=True)
    assert history.items[0].item_key == candidate_key_for(one)
    assert history.items[0].source_id == first["source_id"]
