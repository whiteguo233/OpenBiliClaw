"""Selected full evaluator routing, cache isolation and real SQLite admission."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.storage.database import Database

from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from pathlib import Path


class BombLLM:
    async def complete_structured_task(self, **kwargs: Any) -> Any:
        raise AssertionError("Pure Jev evaluation must never call the LLM")


class RecordingEvaluator:
    def __init__(self, *, missing: bool = False) -> None:
        self.cache_namespace = "jev-test-model-schema-v1"
        self.calls: list[dict[str, Any]] = []
        self.missing = missing

    async def evaluate(self, **kwargs: Any) -> list[dict[str, Any] | None]:
        self.calls.append(deepcopy(kwargs))
        return [
            None
            if self.missing
            else {
                "score": 0.9,
                "reason": "受控评估命中兴趣",
                "topic_group": "人工智能",
                "style_key": "deep_dive",
                "franchise_key": "",
                "temporal_class": "evergreen",
                "temporal_confidence": 0.9,
                "temporal_reason": "不依赖当前时间",
                "temporal_validity_mode": "none",
                "temporal_valid_until": "",
                "temporal_scope": "none",
                "temporal_evidence": "",
                "temporal_state": "unknown",
            }
            for _item in kwargs["content_items"]
        ]


def make_engine(adapter: Any, **kwargs: Any) -> ContentDiscoveryEngine:
    return ContentDiscoveryEngine(eval_scorer="jev", jev_evaluator=adapter, **kwargs)


def item(index: int = 1) -> DiscoveredContent:
    return DiscoveredContent(
        bvid=f"BVJEV{index}", title=f"神经网络原理 {index}", source_strategy="search"
    )


async def test_single_without_llm_and_batch_use_same_evaluator() -> None:
    adapter = RecordingEvaluator()
    engine = make_engine(adapter)
    content = item()
    assert await engine.evaluate_content(content, _build_profile()) == 0.9
    assert content.topic_group == "人工智能"
    assert content.temporal_evidence_complete
    assert engine._learned_scorer is None
    assert len(adapter.calls) == 1
    assert "profile_summary" in adapter.calls[0]
    assert await engine.evaluate_content_batch([item()], _build_profile()) == [0.9]
    assert len(adapter.calls) == 1  # same-mode exact cache hit


async def test_failed_batch_is_not_retried_by_llm_or_cached() -> None:
    adapter = RecordingEvaluator(missing=True)
    engine = make_engine(adapter, llm_service=BombLLM())
    candidates = [item(1), item(2)]
    assert await engine.evaluate_content_batch(candidates, _build_profile()) == [0.0, 0.0]
    assert len(adapter.calls) == 1  # adapter owns all splitting/deadlines
    assert not engine._eval_cache
    assert all(c.relevance_reason == "evaluation_response_missing" for c in candidates)
    assert await engine.evaluate_content(candidates[0], _build_profile()) == 0.0
    assert len(adapter.calls) == 2


async def test_model_schema_or_vocabulary_namespace_invalidates_cache() -> None:
    adapter = RecordingEvaluator()
    engine = make_engine(adapter)
    profile = _build_profile()
    assert await engine.evaluate_content_batch([item()], profile) == [0.9]
    adapter.cache_namespace = "jev-new-model-or-vocabulary-v2"
    assert await engine.evaluate_content_batch([item()], profile) == [0.9]
    assert len(adapter.calls) == 2
    keys = list(engine._eval_cache)
    assert len(keys) == 2
    assert all(":evaluator:jev-" in key for key in keys)


@pytest.mark.parametrize("field", ["published_label", "content_url", "cover_url", "body_text"])
async def test_cache_hashes_exact_jev_input_not_llm_sparse_projection(field: str) -> None:
    adapter = RecordingEvaluator()
    engine = make_engine(adapter)
    content = item()
    await engine.evaluate_content_batch([content], _build_profile())
    setattr(content, field, "different prompt-visible evidence")
    await engine.evaluate_content_batch([content], _build_profile())
    assert len(adapter.calls) == 2


def test_unconfigured_and_multimodal_jev_are_rejected() -> None:
    with pytest.raises(ValueError, match="configured evaluator"):
        ContentDiscoveryEngine(eval_scorer="jev")
    with pytest.raises(ValueError, match="text-only"):
        make_engine(RecordingEvaluator(), multimodal_evaluation_enabled=True)


async def test_unknown_topic_is_not_filled_by_embedding() -> None:
    class BombEmbedding:
        async def embed(self, text: str) -> list[float]:
            raise AssertionError("must preserve controlled unknown")

    engine = make_engine(RecordingEvaluator(), embedding_service=BombEmbedding())
    content = item()
    content.topic_key = "raw keyword"
    await engine._normalize_topic_groups([content])
    assert content.topic_group == ""


async def test_legacy_recovery_uses_selected_jev_without_llm(tmp_path: Path) -> None:
    db = Database(tmp_path / "legacy.db")
    db.initialize()
    adapter = RecordingEvaluator()
    evaluator = make_engine(adapter, database=db)
    recommendation = RecommendationEngine(llm=BombLLM(), database=db, content_evaluator=evaluator)
    content = item()
    await recommendation._classify_batch([content], _build_profile())
    assert content.relevance_score == 0.9
    assert content.topic_group == "人工智能"
    assert len(adapter.calls) == 1
    db.close()


@pytest.mark.parametrize("injected", [False, True])
async def test_startup_configure_attaches_jev_with_director_and_filter_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injected: bool
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config
    from openbiliclaw.recommendation.director import runtime as director_module

    from .test_jev_full_evaluator import NativeProvider

    config = Config()
    config.discovery.eval_scorer = "jev"
    config.decision.providers.jev.enabled = True
    provider = NativeProvider()
    monkeypatch.setattr(director_module, "build_decision_provider", lambda _config: provider)
    db = Database(tmp_path / "startup.db")
    db.initialize()
    recommendation = RecommendationEngine(llm=BombLLM(), database=db)
    controller_discovery = ContentDiscoveryEngine(llm_service=BombLLM(), database=db)
    context = RuntimeContext(
        config=config,
        database=db,
        llm_service=BombLLM(),
        recommendation_engine=recommendation,
        discovery_engine=(ContentDiscoveryEngine(llm_service=BombLLM()) if injected else None),
        runtime_controller=SimpleNamespace(discovery_engine=controller_discovery),
    )
    context.configure_director()
    assert context.director_runtime is not None
    assert not context.director_runtime.enabled
    assert context.discovery_engine._eval_scorer == "jev"
    assert controller_discovery._eval_scorer == "jev"
    assert recommendation._content_evaluator is context.discovery_engine
    assert await controller_discovery.evaluate_content(item(), _build_profile()) == 0.75
    await recommendation._classify_batch([item(2)], _build_profile())
    assert len(provider.requests) == 2
    await context.director_runtime.aclose()
    db.close()


@pytest.mark.parametrize("missing", [False, True])
async def test_candidate_claim_evaluation_admission_real_sqlite(
    tmp_path: Path, missing: bool
) -> None:
    db = Database(tmp_path / "candidates.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVJEV1",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVJEV1",
                title="神经网络原理",
            )
        ]
    )
    adapter = RecordingEvaluator(missing=missing)
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=make_engine(adapter, llm_service=BombLLM(), database=db),
        pool_target_count=30,
    )
    claim = pipeline.claim_batch(limit=1)
    assert claim is not None
    outcome = await pipeline.evaluate_claim(claim, _build_profile())
    result = await pipeline.complete_claim(outcome, admission_limit=10)
    if missing:
        assert result["cached"] == 0
        row = db.conn.execute("SELECT status, eval_error FROM discovery_candidates").fetchone()
        assert row["status"] == "pending_eval"
        assert row["eval_error"] == "evaluation_response_missing"
        assert not pipeline.discovery_engine._eval_cache
    else:
        assert result["cached"] == 1
        row = db.conn.execute(
            "SELECT relevance_score, topic_group, temporal_evidence_complete FROM content_cache"
        ).fetchone()
        assert row["relevance_score"] == 0.9
        assert row["topic_group"] == "人工智能"
        assert row["temporal_evidence_complete"]
    assert len(adapter.calls) == 1
    db.close()
