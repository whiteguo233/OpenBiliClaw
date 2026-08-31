"""End-to-end acceptance coverage for the sparse-JSON production landing."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.run_profile_diet_ab import _DeterministicLLMService

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import (
    _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
    ContentDiscoveryEngine,
    DiscoveredContent,
)
from openbiliclaw.discovery.eval_payload import decode_sparse_evaluation_json
from openbiliclaw.discovery.multimodal import PreparedCoverImage
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, SoulProfile
from openbiliclaw.storage.database import Database

_V6_PRODUCTION_SYSTEM_SHA256 = "fb8c8660ad15bf5459c84d3e5bb09034851fc536f141aa2f3fcd5097d3040e88"
_V6_PRODUCTION_USER_SHA256 = "48944e86483632062b18f1d6b26655d2ef64ac4855c8f34d92da99d76a3d619f"


def _profile(*, private: bool = False) -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(
            name="SECRET_PROFILE_INTEREST" if private else "rollback interest",
            category="test",
            weight=0.9,
        )
    ]
    return profile


def _landing_contents(*, changed_runtime_urls: bool = False) -> list[DiscoveredContent]:
    url_generation = "warm" if changed_runtime_urls else "cold"
    return [
        DiscoveredContent(
            content_id=f"SECRET_GLOBAL_{suffix}",
            content_url=f"https://secret.invalid/{url_generation}/{suffix.lower()}",
            cover_url=f"https://secret.invalid/{url_generation}/cover-{suffix.lower()}",
            title=f"SECRET_TITLE_{suffix}",
            author_name=f"author {suffix}",
            source_platform="twitter",
            content_type="thread",
            source_strategy="feed",
            body_text=f"SECRET_BODY_{suffix}",
            published_at=f"2026-08-0{index + 3}T08:00:00Z",
            view_count=100 + index,
            like_count=10 + index,
        )
        for index, suffix in enumerate(("A", "B"))
    ]


def _rollback_contents() -> list[DiscoveredContent]:
    return [
        DiscoveredContent(
            content_id="ROLLBACK-GLOBAL-A",
            content_url="https://rollback.invalid/a",
            title="Rollback alpha",
            author_name="Author A",
            source_platform="twitter",
            content_type="thread",
            source_strategy="feed",
            body_text="alpha body\nline2",
            view_count=123,
            like_count=7,
        ),
        DiscoveredContent(
            content_id="ROLLBACK-GLOBAL-B",
            content_url="https://rollback.invalid/b",
            title="Rollback beta",
            author_name="Author B",
            source_platform="twitter",
            content_type="thread",
            source_strategy="feed",
            body_text="beta body",
            view_count=456,
            like_count=8,
        ),
    ]


def _candidate_block(user_input: str) -> str:
    open_frame = "<content_batch>\n\n"
    close_frame = "\n\n</content_batch>"
    before, separator, remainder = user_input.partition(open_frame)
    assert separator and before
    payload, separator, _after = remainder.partition(close_frame)
    assert separator
    return payload


def _response(
    results: list[dict[str, object]],
    *,
    prompt_tokens: int = 100,
) -> LLMResponse:
    return LLMResponse(
        content=json.dumps({"results": results}, ensure_ascii=False),
        provider="test-provider",
        instance_id="test-instance",
        model="test-model",
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 20,
            "total_tokens": prompt_tokens + 20,
        },
    )


class _SparseColdWarmService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        batch = decode_sparse_evaluation_json(_candidate_block(str(kwargs["user_input"])))
        results = [
            {
                "id": local_id,
                "score": 0.71 + int(local_id) / 100,
                "reason": f"reason-{local_id}",
                "topic_group": f"topic-{local_id}",
                "style_key": "deep_focus",
                "franchise_key": "",
            }
            for local_id in reversed(batch.local_ids)
        ]
        return _response(results)


@pytest.mark.asyncio
async def test_default_engine_uses_sparse_local_ids_on_cold_and_warm_prompt_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.prompts.content_evaluation_clock",
        lambda: ("2026-08-05T12:34:56Z", "2026-08-05T12:00:00Z"),
    )
    llm = _SparseColdWarmService()
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    profile = _profile(private=True)

    cold = _landing_contents()
    cold_scores = await engine.evaluate_content_batch(
        cold,
        profile,
        source_context="mixed",
        batch_size=30,
    )
    warm = _landing_contents(changed_runtime_urls=True)
    warm_scores = await engine.evaluate_content_batch(
        warm,
        profile,
        source_context="mixed",
        batch_size=30,
    )

    assert _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT == "sparse-json"
    assert engine.evaluation_candidate_transport == "sparse-json"
    assert cold_scores == pytest.approx([0.71, 0.72])
    assert warm_scores == cold_scores
    assert [item.relevance_reason for item in warm] == ["reason-0", "reason-1"]
    assert len(llm.calls) == 1, "the second evaluation must be a warm member-cache hit"

    candidate_block = _candidate_block(str(llm.calls[0]["user_input"]))
    envelope = decode_sparse_evaluation_json(candidate_block)
    assert envelope.defaults == {
        "content_type": "thread",
        "mode": "normal",
        "source_platform": "twitter",
    }
    assert envelope.local_ids == ("0", "1")
    assert [item["published_at"] for item in envelope.items] == [
        "2026-08-03T08:00:00Z",
        "2026-08-04T08:00:00Z",
    ]
    user_input = str(llm.calls[0]["user_input"])
    evaluation_context = user_input.split("<evaluation_context>\n\n", 1)[1].split(
        "\n\n</evaluation_context>", 1
    )[0]
    assert json.loads(evaluation_context) == {"evaluated_at": "2026-08-05T12:34:56Z"}
    assert candidate_block.startswith('{"defaults":')
    assert not candidate_block.startswith("ROW-WIRE-V1")
    for forbidden in (
        "SECRET_GLOBAL_",
        "https://",
        "content_url",
        "cover_url",
        "bvid",
        "item_key",
    ):
        assert forbidden not in candidate_block

    cache_keys = list(engine._eval_cache_store())  # noqa: SLF001
    assert cache_keys
    assert all(key.startswith("content-eval-v6:batch:") for key in cache_keys)
    assert all(key.endswith(":transport:sparse-json") for key in cache_keys)


@pytest.mark.asyncio
async def test_default_sparse_mixed_content_types_stay_per_item_and_bypass_cache() -> None:
    llm = _SparseColdWarmService()
    engine = ContentDiscoveryEngine(llm_service=llm, eval_prefilter_mode="off")
    contents = [
        DiscoveredContent(
            content_id="MIXED-VIDEO",
            title="mixed video",
            source_platform="bilibili",
            content_type="video",
            source_strategy="search",
        ),
        DiscoveredContent(
            content_id="MIXED-THREAD",
            title="mixed thread",
            source_platform="bilibili",
            content_type="thread",
            source_strategy="search",
        ),
    ]

    first = await engine.evaluate_content_batch(
        contents,
        _profile(),
        source_context="mixed",
        batch_size=30,
    )
    second = await engine.evaluate_content_batch(
        contents,
        _profile(),
        source_context="mixed",
        batch_size=30,
    )

    assert first == pytest.approx([0.71, 0.72])
    assert second == first
    assert len(llm.calls) == 2, "mixed content types must bypass member caching"
    for call in llm.calls:
        envelope = decode_sparse_evaluation_json(_candidate_block(str(call["user_input"])))
        assert "content_type" not in envelope.defaults
        assert [item["content_type"] for item in envelope.items] == [
            "video",
            "thread",
        ]


class _SparseMultimodalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_multimodal_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        batch = decode_sparse_evaluation_json(_candidate_block(str(kwargs["user_input"])))
        return _response(
            [
                {
                    "id": local_id,
                    "score": 0.75,
                    "reason": "vision",
                    "topic_group": "visual",
                    "style_key": "aesthetic_browse",
                    "franchise_key": "",
                }
                for local_id in batch.local_ids
            ]
        )


@pytest.mark.asyncio
async def test_default_sparse_multimodal_uses_local_anchors_without_reordering_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents = [
        DiscoveredContent(
            content_id=f"SECRET_IMAGE_GLOBAL_{suffix}",
            content_url=f"https://secret.invalid/item-{suffix.lower()}",
            cover_url=f"https://secret.invalid/cover-{suffix.lower()}",
            title=f"image-{suffix}",
            author_name="author",
            source_platform="twitter",
            content_type="thread",
            source_strategy="feed",
        )
        for suffix in ("A", "B", "C")
    ]
    prepared = [
        PreparedCoverImage(
            content_id="SECRET_IMAGE_GLOBAL_C",
            data_url="data:image/png;base64,IMAGE-C",
            mime_type="image/png",
        ),
        PreparedCoverImage(
            content_id="SECRET_IMAGE_GLOBAL_A",
            data_url="data:image/jpeg;base64,IMAGE-A",
            mime_type="image/jpeg",
        ),
    ]

    async def prepare_images(*args: object, **kwargs: object) -> list[PreparedCoverImage]:
        del args, kwargs
        return prepared

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_image_inputs",
        prepare_images,
    )
    llm = _SparseMultimodalService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        multimodal_evaluation_enabled=True,
        multimodal_vision_supported=True,
    )

    scores = await engine.evaluate_content_batch(
        contents,
        _profile(),
        source_context="mixed",
        batch_size=30,
    )

    assert scores == pytest.approx([0.75, 0.75, 0.75])
    assert engine.evaluation_candidate_transport == "sparse-json"
    assert len(llm.calls) == 1
    image_inputs = llm.calls[0]["image_inputs"]
    assert image_inputs == [
        {
            "content_id": "2",
            "data_url": "data:image/png;base64,IMAGE-C",
            "mime_type": "image/png",
        },
        {
            "content_id": "0",
            "data_url": "data:image/jpeg;base64,IMAGE-A",
            "mime_type": "image/jpeg",
        },
    ]
    candidate_block = _candidate_block(str(llm.calls[0]["user_input"]))
    batch = decode_sparse_evaluation_json(candidate_block)
    assert [item.get("cover_image_ref") for item in batch.items] == [
        "cover:0",
        None,
        "cover:2",
    ]
    assert "SECRET_IMAGE_GLOBAL_" not in candidate_block
    assert "https://" not in candidate_block
    assert "SECRET_IMAGE_GLOBAL_" not in json.dumps(image_inputs)


class _ProductionRollbackService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return _response(
            [
                {
                    "content_id": "ROLLBACK-GLOBAL-B",
                    "score": 0.82,
                    "reason": "beta",
                    "topic_group": "topic-b",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                },
                {
                    "content_id": "ROLLBACK-GLOBAL-A",
                    "score": 0.81,
                    "reason": "alpha",
                    "topic_group": "topic-a",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                },
            ]
        )


@pytest.mark.asyncio
async def test_explicit_production_rollback_matches_v6_prompt_golden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.prompts.content_evaluation_clock",
        lambda: ("2026-08-05T12:34:56Z", "2026-08-05T12:00:00Z"),
    )
    llm = _ProductionRollbackService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        evaluation_candidate_transport="production",
    )

    scores = await engine.evaluate_content_batch(
        _rollback_contents(),
        _profile(),
        source_context="mixed",
        batch_size=30,
    )

    assert scores == pytest.approx([0.81, 0.82])
    assert engine.evaluation_candidate_transport == "production"
    assert len(llm.calls) == 1
    system_instruction = str(llm.calls[0]["system_instruction"])
    user_input = str(llm.calls[0]["user_input"])
    assert hashlib.sha256(system_instruction.encode("utf-8")).hexdigest() == (
        _V6_PRODUCTION_SYSTEM_SHA256
    )
    assert hashlib.sha256(user_input.encode("utf-8")).hexdigest() == (_V6_PRODUCTION_USER_SHA256)

    candidate_block = _candidate_block(user_input)
    production_items = json.loads(candidate_block)
    assert isinstance(production_items, list)
    assert [item["content_id"] for item in production_items] == [
        "ROLLBACK-GLOBAL-A",
        "ROLLBACK-GLOBAL-B",
    ]
    assert candidate_block.startswith("[\n  {")
    assert "https://rollback.invalid/a" in candidate_block
    assert "ROW-WIRE-V1" not in candidate_block


class _RepairingSparseService:
    def __init__(self) -> None:
        self.candidate_blocks: list[str] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        candidate_block = _candidate_block(str(kwargs["user_input"]))
        batch = decode_sparse_evaluation_json(candidate_block)
        self.candidate_blocks.append(candidate_block)
        assert batch.local_ids == tuple(str(index) for index in range(len(batch.items)))
        if len(batch.items) == 2:
            return _response(
                [
                    {
                        "id": "0",
                        "score": 0.64,
                        "reason": "root",
                        "topic_group": "root-topic",
                        "style_key": "deep_focus",
                        "franchise_key": "",
                    }
                ],
                prompt_tokens=120,
            )
        assert len(batch.items) == 1
        return _response(
            [
                {
                    "id": "0",
                    "score": 0.86,
                    "reason": "repaired",
                    "topic_group": "repair-topic",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                }
            ],
            prompt_tokens=80,
        )


@pytest.mark.asyncio
async def test_default_sparse_member_repair_keeps_privacy_metadata_local_and_salted() -> None:
    inner = _RepairingSparseService()
    audited = _DeterministicLLMService(
        inner,
        service="landing-default",
        expected_candidate_transport="sparse-json",
        candidate_transport_audit_enabled=True,
    )
    engine = ContentDiscoveryEngine(llm_service=audited, eval_prefilter_mode="off")

    scores = await engine.evaluate_content_batch(
        _landing_contents(),
        _profile(private=True),
        source_context="mixed",
        batch_size=30,
    )

    assert scores == pytest.approx([0.64, 0.86])
    assert len(inner.candidate_blocks) == 2
    root_batch = decode_sparse_evaluation_json(inner.candidate_blocks[0])
    repair_batch = decode_sparse_evaluation_json(inner.candidate_blocks[1])
    assert root_batch.local_ids == ("0", "1")
    assert repair_batch.local_ids == ("0",)
    assert repair_batch.items[0]["title"] == "SECRET_TITLE_B"
    for block in inner.candidate_blocks:
        assert "SECRET_GLOBAL_" not in block
        assert "https://" not in block

    assert [call["candidate_item_count"] for call in audited.calls] == [2, 1]
    assert audited.calls[0]["result_missing_local_id_count"] == 1
    assert audited.calls[1]["result_missing_local_id_count"] == 0
    for call in audited.calls:
        assert call["candidate_transport"] == "sparse-json"
        assert call["candidate_decode_valid"] is True
        assert call["candidate_local_id_coverage_complete"] is True
        assert call["candidate_global_identity_field_count"] == 0
        assert call["candidate_url_field_count"] == 0
        assert call["result_identity_contract"] == "local-id"
        assert call["result_local_id_binding_safe"] is True
        assert len(str(call["candidate_canonical_digest"])) == 64
        assert len(str(call["user_context_digest"])) == 64

    privacy_artifact = json.dumps(audited.calls, ensure_ascii=False, sort_keys=True)
    for private_value in (
        "SECRET_GLOBAL_",
        "SECRET_TITLE_",
        "SECRET_BODY_",
        "SECRET_PROFILE_INTEREST",
        "https://secret.invalid",
    ):
        assert private_value not in privacy_artifact


class _PipelineSparseService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        batch = decode_sparse_evaluation_json(_candidate_block(str(kwargs["user_input"])))
        score_by_title = {
            "Landing candidate 0": 0.82,
            "Landing candidate 1": 0.71,
            "Landing candidate 2": 0.40,
        }
        return _response(
            [
                {
                    "id": str(item["id"]),
                    "score": score_by_title[str(item["title"])],
                    "reason": "fit" if score_by_title[str(item["title"])] >= 0.5 else "drop",
                    "topic_group": "landing",
                    "style_key": "deep_focus",
                    "franchise_key": "",
                }
                for item in batch.items
            ]
        )


@pytest.mark.asyncio
async def test_default_sparse_candidate_pipeline_claims_evaluates_admits_and_reuses_cache(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "sparse-landing-pipeline.db")
    database.initialize()
    database.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVLAND{index}",
                source_platform="bilibili",
                source_strategy="search",
                bvid=f"BVLAND{index}",
                content_id=f"BVLAND{index}",
                title=f"Landing candidate {index}",
            )
            for index in range(3)
        ]
    )
    llm = _PipelineSparseService()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        database=database,
        eval_prefilter_mode="off",
    )
    pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=engine,
        pool_target_count=30,
    )
    profile = _profile()

    claim = pipeline.claim_batch(limit=3)
    assert claim is not None
    outcome = await pipeline.evaluate_claim(claim, profile)
    completed = await pipeline.complete_claim(outcome, admission_limit=30)

    assert completed == {"evaluated": 3, "cached": 2, "rejected": 1, "stale": 0}
    assert engine.evaluation_candidate_transport == "sparse-json"
    assert len(llm.calls) == 1
    candidate_block = _candidate_block(str(llm.calls[0]["user_input"]))
    batch = decode_sparse_evaluation_json(candidate_block)
    assert batch.local_ids == ("0", "1", "2")
    assert "BVLAND" not in candidate_block
    statuses = database.conn.execute(
        "SELECT status FROM discovery_candidates ORDER BY content_id"
    ).fetchall()
    assert [row["status"] for row in statuses] == [
        "cached",
        "cached",
        "rejected_low_score",
    ]
    assert database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 2

    replay = [
        DiscoveredContent(
            bvid=f"BVLAND{index}",
            content_id=f"BVLAND{index}",
            title=f"Landing candidate {index}",
            source_platform="bilibili",
            source_strategy="search",
        )
        for index in range(3)
    ]
    assert await engine.evaluate_content_batch(
        replay,
        profile,
        source_context="mixed",
        batch_size=3,
    ) == pytest.approx([0.82, 0.71, 0.40])
    assert len(llm.calls) == 1
    assert replay[2].relevance_reason == ""


def test_all_production_engine_constructors_inherit_sparse_and_never_select_row() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source_files = [
        project_root / "src/openbiliclaw/cli.py",
        project_root / "src/openbiliclaw/api/runtime_context.py",
        project_root / "src/openbiliclaw/integrations/openclaw/bootstrap.py",
        project_root / "src/openbiliclaw/discovery/engine.py",
        *sorted((project_root / "src/openbiliclaw/discovery/strategies").glob("*.py")),
    ]
    constructor_calls: list[tuple[Path, ast.Call]] = []
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callable_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if callable_name == "ContentDiscoveryEngine":
                constructor_calls.append((source_file, node))

    # Three application composition roots plus the standalone-strategy
    # fallback. Registered strategies reuse their owning engine instead of
    # constructing eight configuration-blind evaluators.
    assert len(constructor_calls) == 4
    explicit_transport_calls = [
        f"{source_file.relative_to(project_root)}:{node.lineno}"
        for source_file, node in constructor_calls
        if any(keyword.arg == "evaluation_candidate_transport" for keyword in node.keywords)
    ]
    assert explicit_transport_calls == []
    assert _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT == "sparse-json"
    assert ContentDiscoveryEngine().evaluation_candidate_transport == "sparse-json"
    assert (
        ContentDiscoveryEngine(
            evaluation_candidate_transport="row-wire-v1"
        ).evaluation_candidate_transport
        == "row-wire-v1"
    )
