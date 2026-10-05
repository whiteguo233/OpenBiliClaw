"""Thin direct unit tests for :class:`InspirationKeywordPipeline`.

The deep behavioural coverage lives in ``tests/test_keyword_planner.py`` and
``tests/test_discovery_inspiration.py`` (exercised through the planner's
compatibility delegates). These tests drive the extracted pipeline directly
with fakes to pin the seam: happy path, the ④-LLM-failure deterministic
fallback, and preview isolation (persist / bump semantics).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import DiscoveryConfig, derive_inspiration_breadth_params
from openbiliclaw.discovery.inspiration import AxisRow, ExaPreviewItem, SecondaryInterest
from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_BILI = "bilibili"
_BANGUMI = "bangumi"
_GITHUB = "github"
_LINUXDO = "linuxdo"
_WEIBO = "weibo"
_V2EX = "v2ex"
_CLOCK = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


@dataclass
class _FakeLLM:
    payload: dict[str, object] | None
    raises: bool = False
    calls: list[str] = field(default_factory=list)

    async def complete_structured_task(self, *, caller: str = "", **_: object) -> Any:
        self.calls.append(caller)
        if self.raises:
            raise RuntimeError("llm down")
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test-model",
        )


@dataclass
class _FakeProvider:
    previews_by_query: dict[str, list[ExaPreviewItem]]

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        return list(self.previews_by_query.get(query, []))


@dataclass
class _FakeHost:
    """Stand-in for the shared planner infra the pipeline calls back on."""

    profile: SoulProfile
    inserted: list[tuple[str, list[str]]] = field(default_factory=list)
    insert_kinds: list[tuple[str, str]] = field(default_factory=list)

    def _history(self, platform: str) -> list[str]:
        return []

    def _novel_keywords(
        self,
        platform: str,
        words: list[str],
        *,
        keyword_kind: str = "regular",
    ) -> list[str]:
        """Mirror the planner seam; rotation behaviour has dedicated tests."""
        del platform, keyword_kind
        return list(words)

    def _insert(
        self,
        platform: str,
        words: list[str],
        digest: str,
        *,
        keyword_kind: str = "regular",
        metadata_by_keyword: dict[str, dict[str, object]] | None = None,
    ) -> int:
        self.inserted.append((platform, list(words)))
        if words:
            self.insert_kinds.append((platform, keyword_kind))
        return len(words)

    def _avoid_hints(self, profile: SoulProfile | None = None) -> dict[str, dict[str, object]]:
        return {}

    def _supply_hints(
        self, hints_by_platform: dict[str, dict[str, object]]
    ) -> dict[str, list[str]]:
        return {}

    async def _load_profile(self) -> SoulProfile | None:
        return self.profile


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="Switch 独立游戏", category="游戏", weight=0.95)]
        )
    )


def _axis_payload() -> dict[str, object]:
    return {
        "axes": [
            {
                "interest": "Switch 独立游戏",
                "axis_label": "冷门佳作",
                "axis_kind": "community_vocab",
                "example_terms": ["冷门佳作"],
            }
        ],
        "keywords": [
            {
                "interest": "Switch 独立游戏",
                "axis_id_or_label": "冷门佳作",
                "platform": _BILI,
                "core_concept": "Switch 独立游戏 冷门佳作",
                "decoration": "盘点",
            }
        ],
    }


def test_pipeline_carries_platform_partial_evidence_into_grounding_ledger() -> None:
    provider = object()
    ledger = InspirationKeywordPipeline._new_grounding_ledger(provider)

    InspirationKeywordPipeline._merge_provider_grounding_ledger(
        ledger,
        {
            "platforms": {"github": 1},
            "platform_complete": {},
            "platform_affirmative_empty": {},
            "platform_partial": {"github": 1},
            "platform_degraded": {"github": 1},
            "platform_terminal_evidence": {"github": {"incomplete_results": 1}},
            "provider_degraded": {"PlatformSourceInspirationProvider": 1},
        },
        provider,
    )

    assert ledger["platform_partial"] == {"github": 1}
    assert ledger["platform_degraded"] == {"github": 1}
    assert ledger["platform_complete"] == {}
    assert ledger["platform_affirmative_empty"] == {}
    assert ledger["platform_terminal_evidence"] == {"github": {"incomplete_results": 1}}
    assert ledger["provider_degraded"] == {"PlatformSourceInspirationProvider": 1}


def _make_pipeline(
    db: Database,
    *,
    llm: _FakeLLM,
    host: _FakeHost,
    provider: _FakeProvider,
) -> InspirationKeywordPipeline:
    discovery = DiscoveryConfig(inspiration_search_enabled=True)  # type: ignore[call-arg]
    # Cap to one keyword/platform so the coverage-first fill does not add a
    # second deterministic keyword — keeps these seam tests single-output.
    params = dataclasses.replace(
        derive_inspiration_breadth_params("medium"), max_keywords_per_platform=1
    )
    return InspirationKeywordPipeline(
        host=host,
        db=db,
        llm_service=llm,
        inspiration_provider=provider,
        discovery=lambda: discovery,
        inspiration_params=lambda: params,
        clock=lambda: _CLOCK,
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "pipeline.db")
    d.initialize()
    return d


async def test_pipeline_happy_path_persists_keywords_and_upserts_axis(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    ledger = await pipeline._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    assert llm.calls == ["discovery.keyword_inspiration"]
    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏 冷门佳作 盘点"])]
    # Axis was upserted with production bump semantics.
    row = db.conn.execute(
        "SELECT use_count, status FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["status"]) == "active"


async def test_pipeline_materializes_bangumi_axis_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    payload = _axis_payload()
    keywords = payload["keywords"]
    assert isinstance(keywords, list)
    assert isinstance(keywords[0], dict)
    keywords[0]["platform"] = _BANGUMI
    keywords[0]["core_concept"] = "时间循环 独立游戏"
    keywords[0]["decoration"] = ""
    llm = _FakeLLM(payload=payload)
    pipeline = _make_pipeline(
        db,
        llm=llm,
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger = await pipeline._run_inspiration_stage([_BANGUMI], profile=profile, digest="d1")

    assert ledger == {_BANGUMI: 1}
    assert host.inserted == [(_BANGUMI, ["时间循环 独立游戏"])]


async def test_pipeline_materializes_linuxdo_inspiration_axis_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    payload = _axis_payload()
    keywords = payload["keywords"]
    assert isinstance(keywords, list)
    assert isinstance(keywords[0], dict)
    keywords[0]["platform"] = _LINUXDO
    keywords[0]["core_concept"] = "Linux 内核 eBPF 调优"
    keywords[0]["decoration"] = ""
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload=payload),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger = await pipeline._run_inspiration_stage([_LINUXDO], profile=profile, digest="d1")

    assert ledger == {_LINUXDO: 1}
    assert host.inserted == [(_LINUXDO, ["Linux 内核 eBPF 调优"])]


async def test_pipeline_materializes_github_inspiration_axis_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    payload = _axis_payload()
    keywords = payload["keywords"]
    assert isinstance(keywords, list)
    assert isinstance(keywords[0], dict)
    keywords[0]["platform"] = _GITHUB
    keywords[0]["core_concept"] = "local LLM agent framework"
    keywords[0]["decoration"] = "open source"
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload=payload),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger = await pipeline._run_inspiration_stage([_GITHUB], profile=profile, digest="d1")

    assert ledger == {_GITHUB: 1}
    assert host.inserted == [(_GITHUB, ["local LLM agent framework open source"])]


async def test_pipeline_materializes_weibo_axis_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    payload = _axis_payload()
    keywords = payload["keywords"]
    assert isinstance(keywords, list)
    assert isinstance(keywords[0], dict)
    keywords[0]["platform"] = _WEIBO
    keywords[0]["core_concept"] = "AI Agent"
    keywords[0]["decoration"] = "热议"
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload=payload),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger = await pipeline._run_inspiration_stage([_WEIBO], profile=profile, digest="d1")

    assert ledger == {_WEIBO: 1}
    assert host.inserted == [(_WEIBO, ["AI Agent 热议"])]


async def test_pipeline_materializes_v2ex_axis_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    payload = _axis_payload()
    keywords = payload["keywords"]
    assert isinstance(keywords, list)
    assert isinstance(keywords[0], dict)
    keywords[0]["platform"] = _V2EX
    keywords[0]["core_concept"] = "本地 Agent 上下文管理"
    keywords[0]["decoration"] = "经验"
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload=payload),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger = await pipeline._run_inspiration_stage([_V2EX], profile=profile, digest="d1")

    assert ledger == {_V2EX: 1}
    assert host.inserted == [(_V2EX, ["本地 Agent 上下文管理 经验"])]


async def test_pipeline_llm_failure_falls_back_deterministically(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=None, raises=True)
    provider = _FakeProvider(previews_by_query={})
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    ledger = await pipeline._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    # ④ failed but the stage still produced a deterministic interest-only keyword.
    assert llm.calls == ["discovery.keyword_inspiration"]
    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏"])]


async def test_pipeline_preview_does_not_persist_keywords_or_run_backfill(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    report = await pipeline.preview_inspiration_keywords(
        [_BILI], profile=profile, persist_axes=False
    )

    # Preview never writes keyword rows, never persists axes, never ticks backfill.
    assert host.inserted == []
    assert report["platform_keywords"] == {_BILI: ["Switch 独立游戏 冷门佳作 盘点"]}
    assert pipeline.last_axis_backfill == {"ran": False, "staled": 0, "retired": 0, "purged": 0}
    axis_count = db.conn.execute("SELECT COUNT(*) AS n FROM discovery_inspiration_axis").fetchone()
    assert int(axis_count["n"]) == 0


async def test_pipeline_preview_persist_axes_writes_axis_without_bumping_usage(
    db: Database,
) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    await pipeline.preview_inspiration_keywords([_BILI], profile=profile, persist_axes=True)

    assert host.inserted == []  # still no keyword rows
    row = db.conn.execute(
        "SELECT use_count FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchone()
    assert row is not None
    # persist_axes=True writes the row, but preview keeps bump_axis_usage=False.
    assert int(row["use_count"]) == 0


# ── Part E: embedding near-duplicate axis merge ─────────────────────────


@dataclass
class _FakeEmbeddingService:
    """Deterministic fake: maps text → vector; can raise / time out / go blank."""

    vectors: dict[str, list[float]]
    mode: str = "ok"  # "ok" | "raise" | "timeout" | "empty"
    calls: list[str] = field(default_factory=list)

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.mode == "raise":
            raise RuntimeError("embed provider down")
        if self.mode == "timeout":
            import asyncio

            await asyncio.sleep(3600)
        if self.mode == "empty":
            return []
        return list(self.vectors.get(text, []))


def _axis(label: str, *, interest: str = "游戏评价", terms: tuple[str, ...] = ()) -> Any:
    from openbiliclaw.discovery.inspiration import AxisRow

    return AxisRow(
        interest_label=interest,
        axis_label=label,
        axis_kind="subgenre",
        source="external_search",
        example_terms=terms,
    )


async def _pipeline_with_embeddings(
    db: Database, service: _FakeEmbeddingService
) -> InspirationKeywordPipeline:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=None)
    provider = _FakeProvider(previews_by_query={})
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)
    pipeline._embedding_service = service
    return pipeline


async def test_axis_merge_folds_near_duplicate_into_existing_row(db: Database) -> None:
    existing = _axis("维修与DIY", terms=("拆机",))
    new = _axis("故障自修", terms=("自己修",))
    # Near-identical vectors → cosine ~1.0 >= 0.92.
    service = _FakeEmbeddingService(
        vectors={"维修与DIY": [1.0, 0.0, 0.02], "故障自修": [1.0, 0.0, 0.0]}
    )
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    assert len(resolved) == 1
    # Folded onto the existing axis identity (same id/label), evidence unioned.
    assert resolved[0].axis_id == existing.axis_id
    assert resolved[0].axis_label == "维修与DIY"
    assert set(resolved[0].example_terms) == {"拆机", "自己修"}


async def test_axis_merge_below_threshold_keeps_new_row(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("剧情解析")
    # Orthogonal vectors → cosine 0.0 < 0.92.
    service = _FakeEmbeddingService(vectors={"维修与DIY": [1.0, 0.0], "剧情解析": [0.0, 1.0]})
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    assert len(resolved) == 1
    assert resolved[0].axis_id == new.axis_id
    assert resolved[0].axis_label == "剧情解析"


async def test_axis_merge_only_matches_same_interest(db: Database) -> None:
    existing = _axis("维修与DIY", interest="数码")
    new = _axis("故障自修", interest="游戏评价")
    service = _FakeEmbeddingService(vectors={"维修与DIY": [1.0, 0.0], "故障自修": [1.0, 0.0]})
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    # Different interest → never merged even though the labels embed identically.
    assert resolved[0].axis_id == new.axis_id


async def test_axis_merge_timeout_degrades_to_string_behaviour(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("故障自修")
    service = _FakeEmbeddingService(
        vectors={"维修与DIY": [1.0, 0.0], "故障自修": [1.0, 0.0]}, mode="timeout"
    )
    pipeline = await _pipeline_with_embeddings(db, service)
    # Shrink the timeout so the test is fast.
    import openbiliclaw.runtime.inspiration_pipeline as pipeline_module

    original = pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS
    pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS = 0.05
    try:
        resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])
    finally:
        pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS = original

    # Hard requirement (AC9): timeout → axes pass through unchanged + flag.
    assert degraded is True
    assert [a.axis_id for a in resolved] == [new.axis_id]


async def test_axis_merge_provider_error_degrades_and_never_blocks(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("故障自修")
    service = _FakeEmbeddingService(vectors={}, mode="raise")
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is True
    assert [a.axis_id for a in resolved] == [new.axis_id]


async def test_axis_merge_no_service_passes_through_without_degrade(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=None), host=host, provider=_FakeProvider(previews_by_query={})
    )
    # Default: no embedding service injected.
    resolved, degraded = await pipeline._resolve_axis_merges(
        [_axis("故障自修")], [_axis("维修与DIY")]
    )

    assert degraded is False  # absence is not degradation
    assert len(resolved) == 1


async def test_stage_embedding_merge_writes_single_row_and_flags_clean(db: Database) -> None:
    # End-to-end through the production stage: seed an existing axis, then let
    # the LLM propose a near-duplicate; embedding merge folds it into one row.
    from openbiliclaw.discovery.inspiration import AxisRow

    db.upsert_inspiration_axes(
        [
            AxisRow(
                interest_label="Switch 独立游戏",
                axis_label="冷门佳作",
                axis_kind="community_vocab",
                source="external_search",
            )
        ],
        bump_usage=False,
    )
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(
        payload={
            "axes": [
                {
                    "interest": "Switch 独立游戏",
                    "axis_label": "小众神作",
                    "axis_kind": "community_vocab",
                    "example_terms": ["小众神作"],
                }
            ],
            "keywords": [],
        }
    )
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)
    pipeline._embedding_service = _FakeEmbeddingService(
        vectors={"冷门佳作": [1.0, 0.0, 0.01], "小众神作": [1.0, 0.0, 0.0]}
    )

    report = (
        await pipeline._run_inspiration_stage(["bilibili"], profile=profile, digest="d1")
        is not None
    )
    assert report

    rows = db.conn.execute(
        "SELECT axis_label FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchall()
    # The near-duplicate "小众神作" was folded into "冷门佳作" — still one row.
    assert [str(r["axis_label"]) for r in rows] == ["冷门佳作"]


# ── Phase 2.1 Task 2: dynamic max_tokens + provider protection (F2) ─────

_SIX_PLATFORMS = ["bilibili", "xiaohongshu", "douyin", "youtube", "reddit", "zhihu"]


@dataclass
class _MaxTokensLLM:
    """Records the max_tokens of each call and can raise a per-call error."""

    payload: dict[str, object] = field(default_factory=lambda: {"axes": [], "keywords": []})
    errors: list[Exception | None] = field(default_factory=list)
    max_tokens_seen: list[int] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def complete_structured_task(
        self, *, caller: str = "", max_tokens: int = 4096, **_: object
    ) -> Any:
        self.calls.append(caller)
        self.max_tokens_seen.append(int(max_tokens))
        index = len(self.calls) - 1
        err = self.errors[index] if index < len(self.errors) else None
        if err is not None:
            raise err
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test-model",
        )


def _valid_axis_payload() -> dict[str, object]:
    return {
        "axes": [
            {
                "interest": "i0",
                "axis_label": "冷门佳作",
                "axis_kind": "other",
                "example_terms": ["冷门佳作"],
            }
        ],
        "keywords": [],
    }


def _axis_call_pipeline(db: Database, llm: Any) -> InspirationKeywordPipeline:
    return InspirationKeywordPipeline(
        host=_FakeHost(profile=_profile()),
        db=db,
        llm_service=llm,
        inspiration_provider=_FakeProvider(previews_by_query={}),
        discovery=lambda: DiscoveryConfig(inspiration_search_enabled=True),  # type: ignore[call-arg]
        inspiration_params=lambda: derive_inspiration_breadth_params("medium"),
        clock=lambda: _CLOCK,
    )


async def _run_axis_call(
    pipeline: InspirationKeywordPipeline, *, interests: int, platforms: int
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    selected = [{"label": f"i{n}", "weight": 0.5} for n in range(interests)]
    allocation = {"i0": {"platforms": _SIX_PLATFORMS[:platforms], "min_axes": 1}}
    return await pipeline.plan_inspiration_axis_keywords(
        profile_digest={},
        platform_guides={},
        selected_interests=selected,
        existing_axes=[],
        fresh_evidence=[],
        allocation_targets=allocation,
    )


@pytest.mark.parametrize(
    ("interests", "platforms", "expected"),
    [(4, 2, 8192), (4, 6, 11264), (8, 6, 16384)],
)
async def test_axis_keyword_max_tokens_scales_with_slots(
    db: Database, interests: int, platforms: int, expected: int
) -> None:
    llm = _MaxTokensLLM()
    pipeline = _axis_call_pipeline(db, llm)

    _axes, _candidates, telemetry = await _run_axis_call(
        pipeline, interests=interests, platforms=platforms
    )

    # slots = interests * distinct platforms; THRESHOLD=12 → 8 slots stay at the
    # 8192 floor, 24 slots (6 platforms) scale to 8192+(24-12)*256=11264, and 48
    # slots hit the 16384 ceil (8192+36*256=17408, clamped).
    assert telemetry["max_tokens_requested"] == expected
    assert llm.max_tokens_seen == [expected]
    # Exactly one call on the happy path (≤1 successful generation invariant).
    assert llm.calls == ["discovery.keyword_inspiration"]


async def test_axis_keyword_retries_once_at_floor_on_max_tokens_error(db: Database) -> None:
    err = RuntimeError("Requested max_tokens exceeds the maximum allowed for this model")
    llm = _MaxTokensLLM(payload=_valid_axis_payload(), errors=[err])  # 1st raises, 2nd ok
    pipeline = _axis_call_pipeline(db, llm)

    axes, _candidates, telemetry = await _run_axis_call(pipeline, interests=8, platforms=6)

    # Scaled 16384 rejected → bounded single retry at the 8192 floor recovers.
    assert llm.max_tokens_seen == [16384, 8192]
    assert len(llm.calls) == 2
    assert telemetry["max_tokens_retry"] is True
    assert telemetry["max_tokens_requested"] == 8192
    assert telemetry["llm_call_failed"] is False
    assert axes  # the retry's output parsed successfully


async def test_axis_keyword_retry_only_once_then_deterministic_fallback(db: Database) -> None:
    err = RuntimeError("max_tokens value is too large")
    llm = _MaxTokensLLM(errors=[err, err, err])  # both attempts raise
    pipeline = _axis_call_pipeline(db, llm)

    axes, candidates, telemetry = await _run_axis_call(pipeline, interests=8, platforms=6)

    # Retry is bounded to exactly one — never a third attempt.
    assert llm.max_tokens_seen == [16384, 8192]
    assert len(llm.calls) == 2
    assert telemetry["llm_call_failed"] is True
    assert axes == [] and candidates == []


async def test_non_max_tokens_error_does_not_retry(db: Database) -> None:
    llm = _MaxTokensLLM(errors=[RuntimeError("network connection reset by peer")])
    pipeline = _axis_call_pipeline(db, llm)

    axes, candidates, telemetry = await _run_axis_call(pipeline, interests=8, platforms=6)

    # A non-max_tokens error goes straight to fallback — no floor retry.
    assert llm.max_tokens_seen == [16384]
    assert len(llm.calls) == 1
    assert telemetry["max_tokens_retry"] is False
    assert telemetry["llm_call_failed"] is True
    assert axes == [] and candidates == []


async def test_max_tokens_error_at_floor_request_does_not_retry(db: Database) -> None:
    # slots=8 → requested is already the 8192 floor; a max_tokens error there
    # must NOT retry (retrying at the same value is pointless).
    llm = _MaxTokensLLM(errors=[RuntimeError("max_tokens exceeded")])
    pipeline = _axis_call_pipeline(db, llm)

    _axes, _candidates, telemetry = await _run_axis_call(pipeline, interests=4, platforms=2)

    assert llm.max_tokens_seen == [8192]
    assert len(llm.calls) == 1
    assert telemetry["max_tokens_retry"] is False
    assert telemetry["llm_call_failed"] is True


async def test_preview_report_surfaces_core_concept_and_decoration_metadata(db: Database) -> None:
    # F3 (Codex R1 S2): preview returns before insertion, so the pipeline must
    # explicitly write report["metadata_by_platform"] for observation.
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    report = await pipeline.preview_inspiration_keywords(
        [_BILI], profile=profile, persist_axes=False
    )

    keyword = report["platform_keywords"][_BILI][0]
    assert keyword == "Switch 独立游戏 冷门佳作 盘点"
    metadata_by_platform = report["metadata_by_platform"]
    entry = metadata_by_platform[_BILI][keyword]
    assert entry["core_concept"] == "Switch 独立游戏 冷门佳作"
    assert entry["decoration"] == "盘点"


# ── Phase 2.3 Task 1: E0 parameterization of _run_inspiration_axis_pipeline ──


async def _run_production_pipeline(
    pipeline: InspirationKeywordPipeline,
    *,
    profile: SoulProfile,
    **params: Any,
) -> tuple[dict[str, int], dict[str, Any]]:
    return await pipeline._run_inspiration_axis_pipeline(
        [_BILI],
        profile=profile,
        digest="d1",
        query_kind="regular",
        persist_keywords=True,
        persist_grounding=True,
        persist_axes=True,
        bump_axis_usage=True,
        selection_scope="production",
        **params,
    )


async def test_pipeline_defaults_reproduce_regular_output_byte_stable(db: Database) -> None:
    # Hard gate: all four E0 params at their defaults reproduce the pre-refactor
    # regular path — same selected interest, keyword text, and ledger.
    profile = _profile()
    host = _FakeHost(profile=profile)
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=_axis_payload()), host=host, provider=provider
    )

    ledger, report = await _run_production_pipeline(pipeline, profile=profile)

    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏 冷门佳作 盘点"])]
    assert [i["label"] for i in report["selected_secondary_interests"]] == ["Switch 独立游戏"]


async def test_pipeline_uses_explicit_seed_interests(db: Database) -> None:
    # seed_interests overrides _selected_inspiration_interests: the report reflects
    # the injected seed, not the profile-derived like interest.
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload={"axes": [], "keywords": []}),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )
    seed = SecondaryInterest(interest_id="i:cross", label="跨域主题", parent="", weight=0.5)

    _ledger, report = await _run_production_pipeline(
        pipeline, profile=profile, seed_interests=[seed]
    )

    labels = [i["label"] for i in report["selected_secondary_interests"]]
    assert labels == ["跨域主题"]
    assert "Switch 独立游戏" not in labels


async def test_pipeline_axis_source_replaces_interest_listed_axes(db: Database) -> None:
    # axis_source replaces the interest-listed existing_axes: the injected axis
    # surfaces as a deterministic grounding-probe branch (which is built from the
    # existing_axes), whereas the empty db would otherwise yield no axis branch.
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db,
        llm=_FakeLLM(payload={"axes": [], "keywords": []}),
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )
    seed = SecondaryInterest(interest_id="i:cross", label="跨域主题", parent="", weight=0.5)
    axis = AxisRow(
        interest_label="跨域主题",
        axis_label="跨域轴X",
        axis_kind="other",
        source="explore",
        example_terms=("专名Y",),
    )

    _ledger, report = await _run_production_pipeline(
        pipeline, profile=profile, seed_interests=[seed], axis_source=[axis]
    )

    branch_labels = [b["branch_label"] for b in report["brainstorm_branches"]]
    assert "跨域轴X" in branch_labels


async def test_pipeline_disables_deterministic_fallback_on_llm_failure(db: Database) -> None:
    # allow_deterministic_llm_fallback=False → LLM failure yields empty output so
    # an upper layer can degrade; the default True still fills deterministically.
    profile = _profile()

    host_off = _FakeHost(profile=profile)
    pipeline_off = _make_pipeline(
        db, llm=_FakeLLM(payload=None, raises=True), host=host_off, provider=_FakeProvider({})
    )
    ledger_off, _report_off = await _run_production_pipeline(
        pipeline_off, profile=profile, allow_deterministic_llm_fallback=False
    )
    # No deterministic fill → zero keywords produced (the empty insert call is a
    # no-op; nothing lands in the ledger), so an upper layer can degrade.
    assert ledger_off == {}
    assert [word for _platform, words in host_off.inserted for word in words] == []

    host_on = _FakeHost(profile=profile)
    pipeline_on = _make_pipeline(
        db, llm=_FakeLLM(payload=None, raises=True), host=host_on, provider=_FakeProvider({})
    )
    ledger_on, _report_on = await _run_production_pipeline(pipeline_on, profile=profile)
    # Default (True) keeps the Phase-1 deterministic fill → non-empty output.
    assert ledger_on == {_BILI: 1}
    assert host_on.inserted == [(_BILI, ["Switch 独立游戏"])]


async def test_pipeline_accepts_explore_request_param_inertly(db: Database) -> None:
    # explore_request is reserved (threaded into the prompt by a later task); at
    # this stage passing it must not change the default output.
    profile = _profile()
    host = _FakeHost(profile=profile)
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=_axis_payload()), host=host, provider=provider
    )

    ledger, _report = await _run_production_pipeline(
        pipeline, profile=profile, explore_request={"avoid_covered": ["已覆盖话题"]}
    )

    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏 冷门佳作 盘点"])]


# ── Phase 2.3 Task 4: _run_explore_inspiration_stage (E1+E2) ────────────


def _explore_axis_payload() -> dict[str, object]:
    # A cross-domain axis + a specific cross-domain keyword anchored on a real
    # entity (not a restatement of the seed domain), decorated with a marker.
    return {
        "axes": [
            {
                "interest": "天文摄影",
                "axis_label": "深空拍摄",
                "axis_kind": "method",
                "example_terms": ["深空拍摄"],
            }
        ],
        "keywords": [
            {
                "interest": "天文摄影",
                "axis_id_or_label": "深空拍摄",
                "platform": _BILI,
                "core_concept": "詹姆斯韦伯 深空图像",
                "decoration": "盘点",
            }
        ],
    }


def _explore_domains() -> list[dict[str, object]]:
    return [{"domain": "天文摄影", "novelty_level": 0.8, "queries": ["天文摄影 入门 盘点"]}]


async def test_explore_stage_produces_crossdomain_explore_keywords(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_explore_axis_payload())
    pipeline = _make_pipeline(
        db,
        llm=llm,
        host=host,
        provider=_FakeProvider(previews_by_query={}),
    )

    ledger, report = await pipeline._run_explore_inspiration_stage(
        [_BILI],
        profile=profile,
        digest="d1",
        explore_domains=_explore_domains(),
        covered_topic_groups=["游戏", "动漫"],
    )

    keyword = report["platform_keywords"][_BILI][0]
    assert keyword == "詹姆斯韦伯 深空图像 盘点"
    assert ledger == {_BILI: 1}
    # keyword_kind='explore', platform=bilibili
    assert host.insert_kinds == [(_BILI, "explore")]
    entry = report["metadata_by_platform"][_BILI][keyword]
    assert entry["query_kind"] == "explore"
    assert entry["inspiration_backend"] == "axis_keyword"
    # source_interest ∈ current explore_domains (not old domain / like interest)
    assert entry["source_interest"] == "天文摄影"
    # exactly 1 successful LLM call
    assert llm.calls == ["discovery.keyword_inspiration"]
    # axis persisted with source='explore' and the correct axis_id (angle_id)
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    explore_axes = db.list_inspiration_axes_by_source("explore", limit=10, now=now)
    axis_ids = {a.axis_id for a in explore_axes}
    assert entry["angle_id"] in axis_ids
    assert all(a.source == "explore" for a in explore_axes)
    assert report["explore_degraded"] is False
    # restatement_rate ≤ 0.3 for the produced explore keyword
    from openbiliclaw.discovery.inspiration import RealizedKeyword, restatement_rate

    realized = [RealizedKeyword(keyword=keyword, metadata=entry)]
    assert restatement_rate(realized) <= 0.3


async def test_explore_stage_inherits_f2_max_tokens(db: Database) -> None:
    # Two seed domains × 1 platform = 2 slots < threshold 12 → floor 8192.
    profile = _profile()
    host = _FakeHost(profile=profile)
    seen_max_tokens: list[int] = []

    @dataclass
    class _CaptureLLM:
        payload: dict[str, object]

        async def complete_structured_task(
            self, *, caller: str = "", max_tokens: int = 4096, **_: object
        ) -> Any:
            seen_max_tokens.append(int(max_tokens))
            from openbiliclaw.llm.base import LLMResponse

            return LLMResponse(
                content=json.dumps(self.payload, ensure_ascii=False),
                provider="test",
                model="test",
            )

    pipeline = _make_pipeline(
        db, llm=_CaptureLLM(payload=_explore_axis_payload()), host=host, provider=_FakeProvider({})
    )

    await pipeline._run_explore_inspiration_stage(
        [_BILI],
        profile=profile,
        digest="d1",
        explore_domains=[
            {"domain": "天文摄影", "novelty_level": 0.8, "queries": []},
            {"domain": "深海生物", "novelty_level": 0.7, "queries": []},
        ],
        covered_topic_groups=[],
    )

    assert seen_max_tokens == [8192]  # F2 floor for 2 slots


async def test_explore_stage_clamps_interest_to_current_domains(db: Database) -> None:
    # Adversarial (R3): the LLM drifts, returning a keyword whose interest is a
    # stale like-interest (游戏) instead of the current domain — that candidate
    # must be dropped so no output keyword carries the stale source_interest.
    profile = _profile()
    host = _FakeHost(profile=profile)
    drift_payload = {
        "axes": [
            {
                "interest": "天文摄影",
                "axis_label": "深空拍摄",
                "axis_kind": "method",
                "example_terms": ["深空拍摄"],
            }
        ],
        "keywords": [
            {
                "interest": "游戏",  # stale / drifted — NOT a current explore domain
                "axis_id_or_label": "深空拍摄",
                "platform": _BILI,
                "core_concept": "某热门游戏 新作",
                "decoration": "盘点",
            }
        ],
    }
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=drift_payload), host=host, provider=_FakeProvider({})
    )

    ledger, report = await pipeline._run_explore_inspiration_stage(
        [_BILI],
        profile=profile,
        digest="d1",
        explore_domains=_explore_domains(),  # current domain = 天文摄影
        covered_topic_groups=[],
    )

    # The drifted candidate is dropped → no output keyword carries 游戏.
    produced = report["platform_keywords"][_BILI]
    metadata = report["metadata_by_platform"][_BILI]
    assert all(metadata[kw]["source_interest"] == "天文摄影" for kw in produced)
    assert all(metadata[kw]["source_interest"] != "游戏" for kw in produced)
    assert "某热门游戏 新作 盘点" not in produced
    # No live-fill / interest_only 游戏 keyword either (any produced word is clean).
    assert all(word for _p, words in host.inserted for word in words) or ledger == {}


async def test_explore_stage_degraded_on_llm_failure_returns_empty(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=None, raises=True), host=host, provider=_FakeProvider({})
    )

    ledger, report = await pipeline._run_explore_inspiration_stage(
        [_BILI],
        profile=profile,
        digest="d1",
        explore_domains=_explore_domains(),
        covered_topic_groups=[],
    )

    # LLM failure + no deterministic fallback → empty output + degraded marker.
    assert report["explore_degraded"] is True
    assert ledger == {}
    assert [word for _p, words in host.inserted for word in words] == []


async def test_explore_stage_cold_start_no_domains_is_noop(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=_explore_axis_payload()), host=host, provider=_FakeProvider({})
    )

    ledger, report = await pipeline._run_explore_inspiration_stage(
        [_BILI],
        profile=profile,
        digest="d1",
        explore_domains=[],
        covered_topic_groups=[],
    )

    assert ledger == {}
    assert host.inserted == []
    assert report["explore_degraded"] is False
