"""Rate-card resolution tests for the bundled price catalog (issue #353)."""

from __future__ import annotations

import types
from datetime import UTC, datetime

import pytest

from openbiliclaw.llm.rate_card import (
    RateCatalog,
    RateTier,
    TokenRates,
    catalog_for_runtime,
    normalize_model_id,
)

_CATALOG = {
    "version": 1,
    "generated_at": "2026-11-21T00:00:00Z",
    "fx": {"usd_cny": 7.2},
    "providers": {
        "deepseek": {
            "models": {
                "deepseek-v4-flash": {
                    "input": 0.15,
                    "output": 0.6,
                    "cache_read": 0.003,
                    "cache_write": None,
                    "currency": "USD",
                    "source": "models.dev",
                }
            }
        },
        "openai": {
            "models": {
                "gpt-tiered": {
                    "input": 1.0,
                    "output": 2.0,
                    "currency": "USD",
                    "source": "models.dev",
                }
            }
        },
    },
    "aliases": {"deepseek-v4.1-flash": "deepseek-v4-flash"},
    "offers": {
        "deepseek-v4-flash": {
            "count": 3,
            "input_max": 0.3,
            "input_median": 0.2,
            "output_max": 1.2,
            "output_median": 0.6,
            "cache_read_max": 0.01,
        },
        "glm-5.3-flash": {
            "count": 45,
            "input_max": 0.45,
            "output_max": 1.785,
            "cache_read_max": 0.09,
        },
    },
    "tariffs": {
        "deepseek-v4-flash": [
            {
                "days": [0, 1, 2, 3, 4],
                "start": 60,
                "end": 240,
                "input": 0.3,
                "output": 1.2,
                "cache_read": 0.01,
                "label": "peak",
                "multiplier": 2.0,
            },
            {
                "days": [0, 1, 2, 3, 4],
                "start": 360,
                "end": 600,
                "input": 0.3,
                "output": 1.2,
                "cache_read": 0.01,
                "label": "peak",
                "multiplier": 2.0,
            },
        ]
    },
    "tiers": {
        "gpt-tiered": [
            {"min_prompt_tokens": 1000, "input": 2.0, "output": 4.0},
            {"min_prompt_tokens": 5000, "input": 4.0, "output": 8.0},
        ]
    },
    "sources": {
        "models_dev": {"url": "https://models.dev/api.json", "providers": 324},
        "openrouter": {"url": "https://openrouter.ai/api/v1/models", "with_tariffs": 4},
    },
}


def _catalog() -> RateCatalog:
    return RateCatalog.from_dict(_CATALOG)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


def test_exact_card_off_peak_cost_matches_models_dev() -> None:
    effective = _catalog().resolve(
        "deepseek",
        "deepseek-v4-flash",
        now=_utc(2026, 11, 22, 0, 0),  # Sunday: no peak window
        prompt_tokens=5000,
    )
    assert effective is not None
    assert effective.tariff_label == "base"
    assert effective.confidence == "exact"
    # 5k input × $0.15/M + 3k output × $0.60/M = $0.00255 → ×7.2 = ¥0.01836
    assert effective.cost(prompt_tokens=5000, completion_tokens=3000, usd_cny=7.2) == pytest.approx(
        0.01836
    )


def test_peak_window_boundaries_are_utc_and_weekday_scoped() -> None:
    catalog = _catalog()

    def label(moment: datetime) -> str:
        effective = catalog.resolve("deepseek", "deepseek-v4-flash", now=moment)
        assert effective is not None
        return effective.tariff_label

    assert label(_utc(2026, 11, 23, 0, 59)) == "base"  # Monday 00:59 UTC
    assert label(_utc(2026, 11, 23, 1, 0)) == "peak"  # 01:00 inclusive
    assert label(_utc(2026, 11, 23, 3, 59)) == "peak"
    assert label(_utc(2026, 11, 23, 4, 0)) == "base"  # 04:00 exclusive
    assert label(_utc(2026, 11, 28, 2, 0)) == "base"  # Saturday
    assert label(_utc(2026, 11, 23, 7, 0)) == "peak"  # second window


def test_peak_doubles_the_cost() -> None:
    catalog = _catalog()
    off = catalog.resolve("deepseek", "deepseek-v4-flash", now=_utc(2026, 11, 22, 0, 0))
    peak = catalog.resolve("deepseek", "deepseek-v4-flash", now=_utc(2026, 11, 23, 2, 0))
    assert off is not None and peak is not None
    assert peak.tariff_multiplier == 2.0
    assert peak.cost(prompt_tokens=1000, completion_tokens=1000) == pytest.approx(
        off.cost(prompt_tokens=1000, completion_tokens=1000) * 2
    )


def test_next_off_peak_returns_the_window_end() -> None:
    assert _catalog().next_off_peak(
        "deepseek", "deepseek-v4-flash", now=_utc(2026, 11, 23, 2, 0)
    ) == _utc(2026, 11, 23, 4, 0)


def test_alias_maps_route_ids_to_the_canonical_model() -> None:
    catalog = _catalog()
    effective = catalog.resolve("deepseek", "deepseek-v4.1-flash", now=_utc(2026, 11, 22, 0, 0))
    assert effective is not None
    assert effective.confidence == "exact"
    assert effective.rates.input_per_mtok == 0.15


def test_unknown_relay_uses_the_model_offer_maximum() -> None:
    effective = _catalog().resolve(
        "openai_compatible", "glm-5.3-flash", now=_utc(2026, 11, 22, 0, 0)
    )
    assert effective is not None
    assert effective.confidence == "derived_relay"
    assert effective.rates.input_per_mtok == 0.45
    assert effective.rates.output_per_mtok == 1.785


def test_unknown_model_returns_none_not_zero() -> None:
    assert _catalog().resolve("openai_compatible", "definitely-not-a-model") is None
    assert _catalog().resolve("ollama", "llama3") is None


def test_long_context_tier_replaces_rates_at_the_threshold() -> None:
    catalog = _catalog()
    below = catalog.resolve("openai", "gpt-tiered", prompt_tokens=999)
    at_first = catalog.resolve("openai", "gpt-tiered", prompt_tokens=1000)
    at_second = catalog.resolve("openai", "gpt-tiered", prompt_tokens=5000)
    assert below is not None and at_first is not None and at_second is not None
    assert below.rates.input_per_mtok == 1.0
    assert at_first.rates.input_per_mtok == 2.0
    assert at_second.rates.input_per_mtok == 4.0
    assert at_second.tier_label == "5000+"


def test_cache_read_price_is_used_when_present() -> None:
    effective = _catalog().resolve(
        "deepseek",
        "deepseek-v4-flash",
        now=_utc(2026, 11, 22, 0, 0),
    )
    assert effective is not None
    # 4k cached × $0.003/M + 1k input × $0.15/M + 0 output = $0.000162
    assert effective.cost(
        prompt_tokens=5000,
        completion_tokens=0,
        cached_tokens=4000,
        usd_cny=7.2,
    ) == pytest.approx(0.000162 * 7.2)


def test_user_override_wins_over_the_catalog() -> None:
    override = types.SimpleNamespace(
        provider="openai_compatible",
        model="glm-5.3-flash",
        input=0.1,
        output=0.2,
        cache_read=None,
        cache_write=None,
        currency="USD",
    )
    catalog = RateCatalog.from_dict(_CATALOG, overrides=[override])
    effective = catalog.resolve("openai_compatible", "glm-5.3-flash")
    assert effective is not None
    assert effective.confidence == "user"
    assert effective.rates.input_per_mtok == 0.1
    assert effective.rates.output_per_mtok == 0.2


def test_cny_card_is_not_multiplied_by_fx() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "relay": {
                    "models": {
                        "m": {
                            "input": 1.0,
                            "output": 2.0,
                            "currency": "CNY",
                            "source": "user",
                        }
                    }
                }
            }
        }
    )
    effective = catalog.resolve("relay", "m")
    assert effective is not None
    assert effective.cost(prompt_tokens=1_000_000, completion_tokens=0) == pytest.approx(1.0)


def test_missing_catalog_file_degrades_to_empty_catalog(tmp_path) -> None:
    catalog = RateCatalog.load(tmp_path / "missing.json")
    assert catalog.model_count == 0
    assert catalog.resolve("deepseek", "deepseek-v4-flash") is None


def test_catalog_for_runtime_reads_pricing_config_and_overrides() -> None:
    config = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            pricing=types.SimpleNamespace(
                catalog_path="",
                usd_cny_rate=6.9,
                overrides=[
                    types.SimpleNamespace(
                        provider="openai_compatible",
                        model="glm-5.3-flash",
                        input=0.1,
                        output=0.2,
                        cache_read=None,
                        cache_write=None,
                        currency="USD",
                    )
                ],
            )
        )
    )
    catalog = catalog_for_runtime(config)
    assert catalog.usd_cny == pytest.approx(6.9)
    effective = catalog.resolve("openai_compatible", "glm-5.3-flash")
    assert effective is not None
    assert effective.confidence == "user"


def test_model_normalization_strips_vendor_variants_and_dates() -> None:
    assert normalize_model_id("deepseek/deepseek-v4.1-flash-1017") == "deepseek-v4.1-flash"
    assert normalize_model_id("GPT-6.2-Astra:batch") == "gpt-6.2-astra"
    assert normalize_model_id("  zai/glm-5.3-flash  ") == "glm-5.3-flash"


def test_rate_tier_and_rates_are_plain_values() -> None:
    tier = RateTier(min_prompt_tokens=100, rates=TokenRates(1.0, 2.0))
    assert tier.min_prompt_tokens == 100
    assert tier.rates.output_per_mtok == 2.0


def test_pricing_config_round_trips(tmp_path) -> None:
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text(
        "[llm.pricing]\n"
        "usd_cny_rate = 6.9\n"
        "[[llm.pricing.overrides]]\n"
        'provider = "openai_compatible"\n'
        'model = "glm-5.3-flash"\n'
        "input = 0.15\n"
        "output = 0.5\n"
        "cache_read = 0.03\n"
        'currency = "USD"\n',
        encoding="utf-8",
    )

    config = load_config(path, consult_environment=False)
    pricing = config.llm.pricing
    assert pricing.usd_cny_rate == pytest.approx(6.9)
    assert len(pricing.overrides) == 1
    override = pricing.overrides[0]
    assert override.provider == "openai_compatible"
    assert override.model == "glm-5.3-flash"
    assert override.input == pytest.approx(0.15)
    assert override.output == pytest.approx(0.5)
    assert override.cache_read == pytest.approx(0.03)

    save_config(config, path)
    reloaded = load_config(path, consult_environment=False)
    assert reloaded.llm.pricing.usd_cny_rate == pytest.approx(6.9)
    assert reloaded.llm.pricing.overrides[0].output == pytest.approx(0.5)


def test_invalid_pricing_overrides_are_ignored(tmp_path) -> None:
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "[llm.pricing]\n"
        "usd_cny_rate = 0\n"
        "[[llm.pricing.overrides]]\n"
        'provider = "relay"\n'
        'model = "m"\n'
        "input = -1\n"
        "output = 2\n",
        encoding="utf-8",
    )

    config = load_config(path, consult_environment=False)
    assert config.llm.pricing.usd_cny_rate == pytest.approx(7.2)
    assert config.llm.pricing.overrides == []


def test_bundled_catalog_loads_and_covers_deepseek() -> None:
    catalog = RateCatalog.load()
    assert catalog.provider_count > 0
    assert catalog.model_count > 0
    effective = catalog.resolve("deepseek", "deepseek-v4-flash")
    assert effective is not None
    assert effective.confidence == "exact"
    assert effective.rates.input_per_mtok == pytest.approx(0.15)
    assert effective.rates.output_per_mtok == pytest.approx(0.6)
