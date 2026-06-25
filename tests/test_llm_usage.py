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


def test_estimate_cost_applies_deepseek_cache_discount() -> None:
    """v0.3.28+: cached portion of input is billed at 10% (DeepSeek).

    5K prompt, 3K completion, 4K of those prompt tokens cached.
    - Non-cached input: 1K × ¥0.001 = ¥0.001
    - Cached input: 4K × ¥0.001 × 0.1 = ¥0.0004
    - Output: 3K × ¥0.002 = ¥0.006
    Total: ¥0.0074 (vs ¥0.011 without cache discount = 33% saved)
    """
    cost_no_cache = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    cost_with_cache = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000, cached_tokens=4000)
    assert cost_no_cache == pytest.approx(0.011, rel=1e-9)
    assert cost_with_cache == pytest.approx(0.0074, rel=1e-9)
    # Save ratio sanity check
    assert cost_with_cache < cost_no_cache * 0.7


def test_estimate_cost_applies_openai_cache_discount() -> None:
    """OpenAI cache is 50% off (vs DeepSeek's 90%)."""
    cost = estimate_cost("openai", "gpt-5-nano", 10000, 1000, cached_tokens=8000)
    # Non-cached: 2K × $0.05/M × 7.2 = 2K × ¥0.00036 = ¥0.00072
    # Cached: 8K × $0.05/M × 7.2 × 0.5 = 8K × ¥0.00018 = ¥0.00144
    # Output: 1K × $0.4/M × 7.2 = 1K × ¥0.00288 = ¥0.00288
    expected = 0.00072 + 0.00144 + 0.00288
    assert cost == pytest.approx(expected, rel=1e-3)


def test_estimate_cost_clamps_cached_to_prompt_tokens() -> None:
    """Defensive: if cached_tokens > prompt_tokens (provider bug), clamp.

    Without clamping, the math goes negative on the non-cached portion.
    """
    cost = estimate_cost("deepseek", "deepseek-v4-flash", 1000, 0, cached_tokens=9999)
    # Should equal: full 1K cached at 10% rate = 1K × ¥0.001 × 0.1 = ¥0.0001
    assert cost == pytest.approx(0.0001, rel=1e-9)
    assert cost > 0  # not negative


def test_estimate_cost_unknown_provider_cache_uses_50pct_default() -> None:
    """Unknown provider with cached tokens → 50% conservative discount."""
    no_cache = estimate_cost("mystery-co", "model-x", 1000, 0)
    with_cache = estimate_cost("mystery-co", "model-x", 1000, 0, cached_tokens=1000)
    # All 1000 tokens cached → cost = 1000 × rate × 0.5 = no_cache × 0.5
    assert with_cache == pytest.approx(no_cache * 0.5, rel=1e-9)


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


# ---------------------------------------------------------------------------
# v0.3.28+: cache field extraction & by-caller cache hit rate


def test_usage_recorder_persists_cached_input_tokens(tmp_path: Path) -> None:
    """When the response carries cached_input_tokens, it must flow
    into the DB row + apply the cache discount in the cost estimate."""
    db = Database(tmp_path / "usage.db")
    db.initialize()
    recorder = UsageRecorder(sink=db)

    class _CachedResponse:
        provider = "deepseek"
        model = "deepseek-v4-flash"
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 1000,
            "cached_input_tokens": 4000,
        }

    recorder.record(_CachedResponse(), caller="discovery.evaluate_batch")

    # Cost should reflect 90% off on the 4000 cached tokens.
    # Manual:
    #   non-cached input: 1000 × 0.001 = 0.001
    #   cached input:     4000 × 0.001 × 0.1 = 0.0004
    #   output:           1000 × 0.002 = 0.002
    #   total: 0.0034 (vs 0.007 without cache discount)
    by_caller = db.query_llm_usage_by_caller(days=7)
    assert len(by_caller) == 1
    row = by_caller[0]
    assert row["caller"] == "discovery.evaluate_batch"
    assert row["prompt_tokens"] == 5000
    assert row["cached_input_tokens"] == 4000
    assert row["cost_cny"] == pytest.approx(0.0034, rel=1e-6)


def test_query_llm_usage_by_caller_returns_cache_field(tmp_path: Path) -> None:
    """Schema migration backfills cached_input_tokens; query exposes it."""
    db = Database(tmp_path / "usage.db")
    db.initialize()
    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=2000,
        completion_tokens=500,
        cached_input_tokens=1500,
        estimated_cost_cny=0.0015,
        caller="discovery.evaluate_batch",
    )
    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=1000,
        completion_tokens=300,
        cached_input_tokens=0,  # cache miss
        estimated_cost_cny=0.0016,
        caller="recommendation.write_expression",
    )

    rows = db.query_llm_usage_by_caller(days=7)
    by_caller = {r["caller"]: r for r in rows}

    assert by_caller["discovery.evaluate_batch"]["cached_input_tokens"] == 1500
    assert by_caller["discovery.evaluate_batch"]["prompt_tokens"] == 2000
    # 75% hit rate on discovery.evaluate_batch
    hit_rate = (
        by_caller["discovery.evaluate_batch"]["cached_input_tokens"]
        / by_caller["discovery.evaluate_batch"]["prompt_tokens"]
    )
    assert hit_rate == pytest.approx(0.75, rel=1e-9)
    # 0% on recommendation
    assert by_caller["recommendation.write_expression"]["cached_input_tokens"] == 0
