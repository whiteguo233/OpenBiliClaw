"""Tests for the v0.3.26+ LLM usage ledger.

Covers:
- ``pricing.estimate_cost`` math + provider/model fallback
- ``Database.insert_llm_usage`` + ``query_llm_usage_*`` round-trip
- ``UsageRecorder`` extracting tokens from a fake ``LLMResponse``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.pricing import PRICING, estimate_cost
from openbiliclaw.llm.usage_recorder import UsageRecorder
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# pricing.estimate_cost


def test_estimate_cost_known_provider_model() -> None:
    """deepseek-v4-flash: ¥0.001 input + ¥0.002 output per 1K tokens."""
    cost = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    assert cost == pytest.approx(0.005 + 0.006, rel=1e-9)


def test_estimate_cost_falls_back_to_provider_default() -> None:
    """Unknown model under known provider → default rate."""
    expected_default = PRICING["deepseek"]["default"]
    cost = estimate_cost("deepseek", "deepseek-v9-quantum", 1000, 500)
    expected = (1000 / 1000) * expected_default[0] + (500 / 1000) * expected_default[1]
    assert cost == pytest.approx(expected, rel=1e-9)


def test_estimate_cost_unknown_provider_uses_generic_fallback() -> None:
    """Truly-unknown provider gets a midrange estimate, not silent zero —
    so unexpected provider names still show up in the bill instead of
    hiding under a 0."""
    cost = estimate_cost("totally-new-co", "model-x", 1000, 500)
    assert cost > 0


def test_estimate_cost_ollama_is_free() -> None:
    """Local Ollama is treated as free (0 cost)."""
    assert estimate_cost("ollama", "llama3", 100000, 50000) == 0.0


def test_estimate_cost_handles_negative_token_counts() -> None:
    """Defensive: negative token values clamp to 0 instead of producing
    negative cost."""
    assert estimate_cost("deepseek", "deepseek-chat", -10, -5) == 0.0


# ---------------------------------------------------------------------------
# Database round-trip


def test_database_insert_and_query_llm_usage_by_day(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()

    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=5000,
        completion_tokens=2000,
        estimated_cost_cny=0.009,
        caller="discovery.eval",
    )
    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=3000,
        completion_tokens=1500,
        estimated_cost_cny=0.0042,
    )

    daily = db.query_llm_usage_by_day(days=7)
    assert len(daily) == 1  # all in same day
    today = daily[0]
    assert today["calls"] == 2
    assert today["prompt_tokens"] == 8000
    assert today["completion_tokens"] == 3500
    assert today["total_tokens"] == 11500
    assert today["cost_cny"] == pytest.approx(0.0132, rel=1e-6)


def test_database_query_llm_usage_by_provider(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()

    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=10000,
        completion_tokens=2000,
        estimated_cost_cny=0.014,
    )
    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=4000,
        completion_tokens=1000,
        estimated_cost_cny=0.0042,
    )
    db.insert_llm_usage(
        provider="ollama",
        model="bge-m3",
        prompt_tokens=500,
        completion_tokens=0,
        estimated_cost_cny=0.0,
    )

    rows = db.query_llm_usage_by_provider(days=7)
    # Sorted by cost_cny DESC; the v4-flash row should win
    assert rows[0]["provider"] == "deepseek"
    assert rows[0]["model"] == "deepseek-v4-flash"
    assert rows[0]["calls"] == 1
    # Ollama has 0 cost — comes last
    assert rows[-1]["provider"] == "ollama"


def test_database_query_llm_usage_total(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()

    for _ in range(5):
        db.insert_llm_usage(
            provider="deepseek",
            model="deepseek-chat",
            prompt_tokens=1000,
            completion_tokens=500,
            estimated_cost_cny=0.0014,
        )

    total = db.query_llm_usage_total(days=7)
    assert total["calls"] == 5
    assert total["prompt_tokens"] == 5000
    assert total["completion_tokens"] == 2500
    assert total["total_tokens"] == 7500
    assert total["cost_cny"] == pytest.approx(0.007, rel=1e-6)


def test_database_query_llm_usage_total_empty_returns_zeros(tmp_path: Path) -> None:
    """When no usage has been recorded, total is all-zeros — the CLI
    relies on this to print a friendly empty-state message."""
    db = Database(tmp_path / "usage.db")
    db.initialize()

    total = db.query_llm_usage_total(days=7)
    assert total["calls"] == 0
    assert total["cost_cny"] == 0.0


# ---------------------------------------------------------------------------
# UsageRecorder integration


class _FakeResponse:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }


def test_usage_recorder_persists_response_tokens(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()
    recorder = UsageRecorder(sink=db)
    assert recorder.enabled

    response = _FakeResponse(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=4500,
        completion_tokens=2000,
    )
    recorder.record(response, caller="soul.preference")

    rows = db.query_llm_usage_by_day(days=7)
    assert len(rows) == 1
    today = rows[0]
    assert today["calls"] == 1
    assert today["prompt_tokens"] == 4500
    assert today["completion_tokens"] == 2000
    # Cost = 4500/1000 * 0.001 + 2000/1000 * 0.002 = 0.0045 + 0.004 = 0.0085
    assert today["cost_cny"] == pytest.approx(0.0085, rel=1e-6)


def test_usage_recorder_no_op_when_sink_missing() -> None:
    """A recorder without a sink shouldn't raise — useful for tests
    and standalone scripts that don't care about cost tracking."""
    recorder = UsageRecorder(sink=None)
    assert not recorder.enabled

    response = _FakeResponse(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=100,
        completion_tokens=50,
    )
    # Should silently no-op, not raise.
    recorder.record(response, caller="test")


def test_usage_recorder_swallows_sink_errors(tmp_path: Path) -> None:
    """Billing should never break the LLM hot path. If the sink
    raises (e.g. DB locked, schema mismatch), record() just logs +
    moves on."""

    class _BrokenSink:
        def insert_llm_usage(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("DB exploded")

    recorder = UsageRecorder(sink=_BrokenSink())
    response = _FakeResponse(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=100,
        completion_tokens=50,
    )
    # Must not raise.
    recorder.record(response, caller="test")


def test_usage_recorder_handles_response_without_usage(tmp_path: Path) -> None:
    """Some providers (e.g. older models, partial failures) may return
    LLMResponse without a usage dict. Record 0 tokens / 0 cost."""
    db = Database(tmp_path / "usage.db")
    db.initialize()
    recorder = UsageRecorder(sink=db)

    class _NoUsageResponse:
        provider = "deepseek"
        model = "deepseek-chat"
        usage = None

    recorder.record(_NoUsageResponse(), caller="edge")
    rows = db.query_llm_usage_by_day(days=7)
    assert len(rows) == 1
    assert rows[0]["calls"] == 1
    assert rows[0]["prompt_tokens"] == 0
    assert rows[0]["cost_cny"] == 0.0
