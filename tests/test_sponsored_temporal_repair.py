"""A failed free-model date contract is corrected once without changing relevance."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import SoulProfile


def _temporal(**changes: Any) -> dict[str, Any]:
    return {
        "temporal_class": "evergreen",
        "temporal_confidence": 0.9,
        "temporal_reason": "基础原理不依赖具体日期",
        "temporal_validity_mode": "none",
        "temporal_valid_until": "",
        "temporal_scope": "none",
        "temporal_evidence": "",
        "temporal_state": "unknown",
        **changes,
    }


@pytest.mark.parametrize("bad_repair", [False, True])
async def test_temporal_correction_is_bounded_grounded_and_preserves_scores(
    bad_repair: bool,
) -> None:
    class Service:
        calls: list[dict[str, Any]] = []

        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                result = [
                    {
                        "bvid": "BVdate",
                        "score": 0.82,
                        "reason": "符合兴趣",
                        "topic_group": "编程",
                        "style_key": "deep_focus",
                        "franchise_key": "",
                        **_temporal(temporal_state="active"),
                    }
                ]
            elif bad_repair:
                # The foreign deadline isn't in the source; never accept it.
                result = [
                    {
                        "id": "0",
                        "score": 0.0,
                        **_temporal(
                            temporal_class="current",
                            temporal_validity_mode="explicit_deadline",
                            temporal_scope="core",
                            temporal_evidence="报名截止2026-11-01T10:00:00+08:00",
                            temporal_valid_until="2026-11-01T10:00:00+08:00",
                        ),
                    }
                ]
            else:
                result = [{"id": "0", **_temporal()}]
            return LLMResponse(content=json.dumps({"results": result}), provider="sponsored")

    service = Service()
    engine = ContentDiscoveryEngine(llm_service=service)
    item = DiscoveredContent(bvid="BVdate", title="Rust 借用原理", source_strategy="search")
    scores = await engine._evaluate_batch_once([item], SoulProfile(), normal_cache_enabled=False)
    assert scores == [0.82] and item.relevance_reason == "符合兴趣"
    assert len(service.calls) == 2
    assert service.calls[1]["caller"] == "discovery.temporal_repair"
    assert item.temporal_class == ("unknown" if bad_repair else "evergreen")
    assert not item.temporal_valid_until
    assert item.temporal_evidence_complete is not bad_repair


@pytest.mark.parametrize("initial_mode", ["none", "freshness_only"])
async def test_missed_source_deadline_is_repaired_without_expiring_other_items(
    initial_mode: str,
) -> None:
    class Service:
        calls: int = 0

        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                results = [
                    {
                        "bvid": "BVdeadline",
                        "score": 0.81,
                        "reason": "报名相关",
                        **_temporal(
                            temporal_validity_mode=initial_mode,
                            temporal_scope="hook" if initial_mode == "freshness_only" else "none",
                            temporal_evidence="报名入口"
                            if initial_mode == "freshness_only"
                            else "",
                        ),
                    }
                ]
            else:
                results = [
                    {
                        "id": "0",
                        **_temporal(
                            temporal_class="current",
                            temporal_validity_mode="explicit_deadline",
                            temporal_valid_until="2026-11-01T18:00:00+08:00",
                            temporal_scope="core",
                            temporal_state="expired",
                            temporal_evidence="报名截止 2026-11-01T18:00:00+08:00",
                        ),
                    }
                ]
            return LLMResponse(content=json.dumps({"results": results}), provider="sponsored")

    service = Service()
    engine = ContentDiscoveryEngine(llm_service=service)
    item = DiscoveredContent(
        bvid="BVdeadline",
        title="报名入口",
        source_strategy="search",
        description="报名截止 2026-11-01T18:00:00+08:00",
    )
    scores = await engine._evaluate_batch_once([item], SoulProfile(), normal_cache_enabled=False)
    assert scores == [0.81]
    assert service.calls == 2
    assert item.temporal_valid_until == "2026-11-01T10:00:00Z"
    assert item.temporal_evidence_complete
    assert item.temporal_state == "unknown"  # Expiry belongs to the clock, not model state.
