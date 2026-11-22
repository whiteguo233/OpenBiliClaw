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
    """deepseek-v4-flash: ¥0.00108 input + ¥0.00432 output per 1K tokens.

    Rates re-checked 2026-11-21 against models.dev / OpenRouter
    ($0.15 / $0.60 per M × 7.2). The old output rate (¥0.002) was 2.16x low
    and is what issue #353 measured against the real DeepSeek bill.
    """
    cost = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    assert cost == pytest.approx(0.0054 + 0.01296, rel=1e-9)


def test_estimate_cost_api_route_default_model() -> None:
    assert estimate_cost("api_route", "gpt-5.5", 1000, 1000) == pytest.approx(
        0.00216 + 0.01296, rel=1e-9
    )
    assert estimate_cost("api_route", "another-model", 1000, 1000) == pytest.approx(
        0.005 + 0.015, rel=1e-9
    )


def test_estimate_cost_cheaperinference_uses_gateway_default() -> None:
    assert estimate_cost("cheaperinference", "gpt-5.4-mini", 1000, 1000) == pytest.approx(
        0.005 + 0.015, rel=1e-9
    )


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


def test_estimate_cost_sponsored_is_free() -> None:
    """Sponsored traffic is covered by the sponsor account (0 user cost)."""
    assert estimate_cost("sponsored", "THUDM/GLM-4-9B-0414", 100000, 50000) == 0.0


def test_estimate_cost_handles_negative_token_counts() -> None:
    """Defensive: negative token values clamp to 0 instead of producing
    negative cost."""
    assert estimate_cost("deepseek", "deepseek-chat", -10, -5) == 0.0


def test_estimate_cost_applies_deepseek_cache_discount() -> None:
    """v0.3.28+: cached portion of input is billed at 10% (DeepSeek).

    5K prompt, 3K completion, 4K of those prompt tokens cached.
    - Non-cached input: 1K × ¥0.00108 = ¥0.00108
    - Cached input: 4K × ¥0.00108 × 0.1 = ¥0.000432
    - Output: 3K × ¥0.00432 = ¥0.01296
    Total: ¥0.014472 (vs ¥0.01836 without cache discount = 21% saved)
    """
    cost_no_cache = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    cost_with_cache = estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000, cached_tokens=4000)
    assert cost_no_cache == pytest.approx(0.01836, rel=1e-9)
    assert cost_with_cache == pytest.approx(0.014472, rel=1e-9)
    # Save ratio sanity check (output is now the dominant cost, so the
    # cache-only saving is ~21% here, not the old ~33%).
    assert cost_with_cache < cost_no_cache * 0.85


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
    # Full 1K cached at 10% rate = 1K × ¥0.00108 × 0.1 = ¥0.000108
    assert cost == pytest.approx(0.000108, rel=1e-9)
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
    # Cost = 4500/1000 * 0.00108 + 2000/1000 * 0.00432 = 0.00486 + 0.00864
    assert today["cost_cny"] == pytest.approx(0.0135, rel=1e-6)


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
    """A usage-less response is charged a conservative fallback estimate.

    Recording 0 would let a gateway that rejects stream_options bypass the
    daily cap indefinitely (Codex acceptance finding)."""
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
    assert rows[0]["prompt_tokens"] >= 2048
    assert rows[0]["cost_cny"] > 0.0


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
    #   non-cached input: 1000 × 0.00108 = 0.00108
    #   cached input:     4000 × 0.00108 × 0.1 = 0.000432
    #   output:           1000 × 0.00432 = 0.00432
    #   total: 0.005832 (vs 0.00972 without cache discount)
    by_caller = db.query_llm_usage_by_caller(days=7)
    assert len(by_caller) == 1
    row = by_caller[0]
    assert row["caller"] == "discovery.evaluate_batch"
    assert row["prompt_tokens"] == 5000
    assert row["cached_input_tokens"] == 4000
    assert row["cost_cny"] == pytest.approx(0.005832, rel=1e-6)


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


def test_usage_recorder_uses_rate_catalog_and_records_confidence(tmp_path: Path) -> None:
    from openbiliclaw.llm.rate_card import RateCatalog

    db = Database(tmp_path / "usage.db")
    db.initialize()
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {
                    "models": {
                        "deepseek-v4-flash": {
                            "input": 0.15,
                            "output": 0.6,
                            "cache_read": 0.003,
                            "currency": "USD",
                            "source": "models.dev",
                        }
                    }
                }
            },
            "fx": {"usd_cny": 7.2},
        }
    )
    recorder = UsageRecorder(sink=db, catalog=catalog)
    response = _FakeResponse(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    recorder.record(response, caller="soul.preference")

    row = db.conn.execute(
        "SELECT estimated_cost_cny, cost_confidence FROM llm_usage ORDER BY id DESC LIMIT 1"
    ).fetchone()
    # 1k × $0.15/M + 1k × $0.60/M = $0.00075 → ×7.2 = ¥0.0054
    assert float(row["estimated_cost_cny"]) == pytest.approx(0.0054, rel=1e-6)
    assert row["cost_confidence"] == "exact"


def test_today_query_sums_low_confidence_spend(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()
    db.insert_llm_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_tokens=1000,
        completion_tokens=1000,
        estimated_cost_cny=0.01,
        caller="soul.preference",
        cost_confidence="exact",
    )
    db.insert_llm_usage(
        provider="openai_compatible",
        model="glm-5.3-flash",
        prompt_tokens=1000,
        completion_tokens=1000,
        estimated_cost_cny=0.02,
        caller="discovery.evaluate_batch",
        cost_confidence="derived_relay",
    )

    today = db.query_llm_usage_today()
    assert today["cost_cny"] == pytest.approx(0.03)
    assert today["low_confidence_cost_cny"] == pytest.approx(0.02)


def test_usage_recorder_charges_claude_cache_writes(tmp_path: Path) -> None:
    from openbiliclaw.llm.rate_card import RateCatalog

    db = Database(tmp_path / "usage.db")
    db.initialize()
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "anthropic": {
                    "models": {
                        "claude-test": {
                            "input": 15.0,
                            "output": 75.0,
                            "cache_read": 1.5,
                            "cache_write": 18.75,
                            "currency": "USD",
                            "source": "test",
                        }
                    }
                }
            }
        }
    )
    recorder = UsageRecorder(sink=db, catalog=catalog)
    response = _FakeResponse(
        provider="claude",
        model="claude-test",
        prompt_tokens=50,
        completion_tokens=100,
    )
    response.usage["cached_input_tokens"] = 16000
    response.usage["cache_creation_input_tokens"] = 16000
    response.content = "cached reply"

    recorder.record(response, caller="e2e.claude")

    today = db.query_llm_usage_by_day(days=7)[0]
    # 50 uncached @15 + 16k read @1.5 + 16k write @18.75 + 100 out @75
    # = $0.33225 × 7.2 = ¥2.3922
    assert today["cost_cny"] == pytest.approx(2.3922, rel=1e-4)


def test_usage_recorder_estimates_when_stream_usage_is_missing(tmp_path: Path) -> None:
    db = Database(tmp_path / "usage.db")
    db.initialize()
    recorder = UsageRecorder(sink=db)
    response = _FakeResponse(
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=0,
        completion_tokens=0,
    )
    response.usage = {}
    response.content = "a" * 400

    recorder.record(response, caller="e2e.stream")

    today = db.query_llm_usage_by_day(days=7)[0]
    assert today["calls"] == 1
    assert today["cost_cny"] > 0
    assert today["prompt_tokens"] >= 2048
