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
                "provider": "deepseek",
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
                "provider": "deepseek",
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
            {"provider": "openai", "min_prompt_tokens": 1000, "input": 2.0, "output": 4.0},
            {"provider": "openai", "min_prompt_tokens": 5000, "input": 4.0, "output": 8.0},
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


def test_primary_route_reads_v2_chain_and_legacy_provider() -> None:
    from openbiliclaw.llm.rate_card import primary_route

    v2 = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            default_chain=["relay"],
            instances={
                "relay": types.SimpleNamespace(
                    provider_type="openai_compatible", model="glm-5.3-flash"
                )
            },
            default_provider="deepseek",
            deepseek=types.SimpleNamespace(model="deepseek-v4-flash"),
        )
    )
    assert primary_route(v2) == ("openai_compatible", "glm-5.3-flash")

    legacy = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            default_chain=[],
            instances={},
            default_provider="deepseek",
            deepseek=types.SimpleNamespace(model="deepseek-v4-flash"),
        )
    )
    assert primary_route(legacy) == ("deepseek", "deepseek-v4-flash")


def test_peak_predicate_exists_for_a_known_route_and_not_for_unknown() -> None:
    from openbiliclaw.llm.rate_card import peak_predicate_for_config

    known = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            default_chain=["ds"],
            instances={
                "ds": types.SimpleNamespace(provider_type="deepseek", model="deepseek-v4-flash")
            },
            default_provider="",
            instances_data={},
        )
    )
    predicate = peak_predicate_for_config(known, _catalog())
    assert predicate is not None
    assert isinstance(predicate(), bool)

    unknown = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            default_chain=[],
            instances={},
            default_provider="mystery",
            mystery=types.SimpleNamespace(model="no-such-model"),
        )
    )
    assert peak_predicate_for_config(unknown, _catalog()) is None


def test_relay_does_not_inherit_a_provider_peak_schedule() -> None:
    """A relay reselling deepseek-v4-flash is not assumed to follow DeepSeek's
    peak windows; only direct providers (or OpenRouter itself) keep tariffs."""

    catalog = _catalog()
    relay = catalog.resolve(
        "openai_compatible",
        "deepseek-v4-flash",
        now=_utc(2026, 11, 23, 2, 0),
    )
    direct = catalog.resolve(
        "deepseek",
        "deepseek-v4-flash",
        now=_utc(2026, 11, 23, 2, 0),
    )
    assert relay is not None and direct is not None
    assert relay.tariff_label == "base"
    assert direct.tariff_label == "peak"
    assert (
        catalog.is_peak(
            "openai_compatible",
            "deepseek-v4-flash",
            now=_utc(2026, 11, 23, 2, 0),
        )
        is False
    )


def test_runtime_catalog_prefers_a_synced_data_dir_snapshot(tmp_path) -> None:
    import json

    synced = {
        "providers": {
            "deepseek": {
                "models": {
                    "deepseek-v4-flash": {
                        "input": 0.15,
                        "output": 0.6,
                        "currency": "USD",
                        "source": "models.dev",
                    }
                }
            }
        },
        "fx": {"usd_cny": 7.0},
    }
    (tmp_path / "pricing_catalog.json").write_text(json.dumps(synced), encoding="utf-8")
    config = types.SimpleNamespace(
        llm=types.SimpleNamespace(
            pricing=types.SimpleNamespace(
                catalog_path="",
                usd_cny_rate=7.2,
                overrides=[],
            )
        ),
        data_path=tmp_path,
    )
    catalog = catalog_for_runtime(config)
    assert catalog.provider_count == 1
    assert catalog.usd_cny == pytest.approx(7.2)


def test_wrapped_tariff_window_uses_the_start_day_after_midnight() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {
                    "models": {
                        "m": {
                            "input": 1.0,
                            "output": 1.0,
                            "currency": "USD",
                            "source": "test",
                        }
                    }
                }
            },
            "tariffs": {
                "m": [
                    {
                        "provider": "deepseek",
                        "days": [4],  # Friday only
                        "start": 1380,  # 23:00 UTC
                        "end": 60,  # 01:00 UTC
                        "input": 2.0,
                        "output": 2.0,
                        "label": "peak",
                        "multiplier": 2.0,
                    }
                ]
            },
        }
    )
    friday_night = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 27, 23, 30))
    saturday_early = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 28, 0, 30))
    saturday_after = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 28, 1, 30))
    friday_before = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 27, 0, 30))
    assert friday_night is not None and friday_night.tariff_label == "peak"
    assert saturday_early is not None and saturday_early.tariff_label == "peak"
    assert saturday_after is not None and saturday_after.tariff_label == "base"
    assert friday_before is not None and friday_before.tariff_label == "base"


def test_user_override_does_not_inherit_catalog_tariffs_or_tiers() -> None:
    override = types.SimpleNamespace(
        provider="deepseek",
        model="deepseek-v4-flash",
        input=10.0,
        output=20.0,
        cache_read=None,
        cache_write=None,
        currency="CNY",
    )
    catalog = RateCatalog.from_dict(_CATALOG, overrides=[override])
    effective = catalog.resolve(
        "deepseek",
        "deepseek-v4-flash",
        now=_utc(2026, 11, 23, 2, 0),
        prompt_tokens=5000,
    )
    assert effective is not None
    assert effective.confidence == "user"
    assert effective.tariff_label == "base"
    assert effective.rates.input_per_mtok == 10.0
    assert effective.rates.output_per_mtok == 20.0
    assert effective.tier_label == ""


def test_catalog_tariff_is_not_applied_to_a_different_provider() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "zai": {
                    "models": {
                        "deepseek-v4-flash": {
                            "input": 0.2,
                            "output": 0.8,
                            "currency": "USD",
                            "source": "models.dev",
                        }
                    }
                }
            },
            "tariffs": {
                "deepseek-v4-flash": [
                    {
                        "provider": "deepseek",
                        "days": [0, 1, 2, 3, 4],
                        "start": 60,
                        "end": 240,
                        "input": 0.4,
                        "output": 1.6,
                        "label": "peak",
                        "multiplier": 2.0,
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("zai", "deepseek-v4-flash", now=_utc(2026, 11, 23, 2, 0))
    assert effective is not None
    assert effective.tariff_label == "base"
    assert effective.rates.output_per_mtok == 0.8


def test_tiers_are_provider_scoped() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
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
                "zai": {
                    "models": {
                        "gpt-tiered": {
                            "input": 0.5,
                            "output": 1.0,
                            "currency": "USD",
                            "source": "models.dev",
                        }
                    }
                },
            },
            "tiers": {
                "gpt-tiered": [
                    {"provider": "openai", "min_prompt_tokens": 1000, "input": 9.0, "output": 9.0}
                ]
            },
        }
    )
    openai_rate = catalog.resolve("openai", "gpt-tiered", prompt_tokens=2000)
    zai_rate = catalog.resolve("zai", "gpt-tiered", prompt_tokens=2000)
    assert openai_rate is not None and openai_rate.rates.input_per_mtok == 9.0
    assert zai_rate is not None and zai_rate.rates.input_per_mtok == 0.5


def test_negative_offer_and_cache_prices_are_not_billable() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {
                    "models": {
                        "m": {
                            "input": 1.0,
                            "output": 2.0,
                            "cache_read": -1.0,
                            "currency": "USD",
                            "source": "test",
                        }
                    }
                }
            },
            "offers": {
                "bad": {"count": 1, "input_max": -1.0, "output_max": 2.0},
            },
        }
    )
    exact = catalog.resolve("deepseek", "m")
    assert exact is not None
    assert exact.rates.cache_read_per_mtok is None
    assert catalog.resolve("openai_compatible", "bad") is None


def test_nonfinite_offer_prices_are_rejected() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {},
            "offers": {
                "nan-model": {"count": 1, "input_max": float("nan"), "output_max": 2.0},
            },
        }
    )
    assert catalog.resolve("openai_compatible", "nan-model") is None


def test_openrouter_card_does_not_inherit_another_providers_tiers() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "openrouter": {
                    "models": {
                        "gpt-5.4": {
                            "input": 5.0,
                            "output": 22.5,
                            "currency": "USD",
                            "source": "models.dev",
                        }
                    }
                }
            },
            "tiers": {
                "gpt-5.4": [
                    {
                        "provider": "openai",
                        "min_prompt_tokens": 272000,
                        "input": 2.5,
                        "output": 11.25,
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("openrouter", "gpt-5.4", prompt_tokens=300000)
    assert effective is not None
    assert effective.tier_label == ""
    assert effective.rates.input_per_mtok == 5.0


def test_nonfinite_user_override_cache_prices_are_ignored() -> None:
    override = types.SimpleNamespace(
        provider="deepseek",
        model="deepseek-v4-flash",
        input=1.0,
        output=2.0,
        cache_read=float("inf"),
        cache_write=float("-inf"),
        currency="USD",
    )
    catalog = RateCatalog.from_dict({}, overrides=[override])
    effective = catalog.resolve("deepseek", "deepseek-v4-flash", prompt_tokens=1000)
    assert effective is not None
    assert effective.rates.cache_read_per_mtok is None
    assert effective.rates.cache_write_per_mtok is None


def test_malformed_catalog_fields_degrade_to_empty_lookups() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": ["deepseek"],
            "offers": "nope",
            "aliases": 7,
            "tariffs": [],
            "tiers": "nope",
            "fx": [],
        }
    )
    assert catalog.resolve("deepseek", "deepseek-v4-flash") is None
    assert catalog.usd_cny == 7.2
    assert RateCatalog.from_dict({"fx": {"usd_cny": float("inf")}}).usd_cny == 7.2


def test_packaging_spec_bundles_the_pricing_catalog() -> None:
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[1] / "packaging" / "openbiliclaw.spec").read_text(
        encoding="utf-8"
    )
    assert "pricing_catalog.json" in spec
    assert '"openbiliclaw/llm"' in spec


def test_nested_malformed_catalog_entries_do_not_raise() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {
                    "models": {
                        "m": {
                            "input": 1.0,
                            "output": 2.0,
                            "cache_read": [],
                            "currency": "USD",
                        }
                    }
                }
            },
            "tariffs": {
                "m": [
                    {
                        "input": 1.0,
                        "output": 2.0,
                        "start": 0,
                        "end": 60,
                        "days": ["bad"],
                        "multiplier": [],
                    }
                ]
            },
            "tiers": {
                "m": [
                    {
                        "min_prompt_tokens": 1000,
                        "input": 1.0,
                        "output": 2.0,
                        "cache_read": [],
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("deepseek", "m", prompt_tokens=2000)
    assert effective is not None
    assert effective.rates.cache_read_per_mtok is None


def test_overflowing_json_numbers_do_not_raise() -> None:
    huge = float("1e309")
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 1.0, "output": 2.0, "currency": "USD"}}}
            },
            "tariffs": {
                "m": [
                    {
                        "input": 1.0,
                        "output": 2.0,
                        "start": huge,
                        "end": 60,
                        "days": [huge],
                    }
                ]
            },
            "tiers": {"m": [{"min_prompt_tokens": huge, "input": 1.0, "output": 2.0}]},
        }
    )
    effective = catalog.resolve("deepseek", "m", prompt_tokens=1000)
    assert effective is not None
    assert effective.tariff_label == "base"
    assert effective.tier_label == ""


def test_malformed_override_cache_prices_do_not_raise() -> None:
    override = types.SimpleNamespace(
        provider="deepseek",
        model="m",
        input=1.0,
        output=2.0,
        cache_read=[],
        cache_write={},
        currency="USD",
    )
    catalog = RateCatalog.from_dict({}, overrides=[override])
    assert catalog.resolve("deepseek", "m") is not None


def test_fractional_weekdays_are_rejected_not_truncated() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 1.0, "output": 2.0, "currency": "USD"}}}
            },
            "tariffs": {
                "m": [
                    {
                        "provider": "deepseek",
                        "input": 2.0,
                        "output": 4.0,
                        "start": 0,
                        "end": 60,
                        "days": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 29, 0, 30))
    assert effective is not None
    assert effective.tariff_label == "base"


def test_huge_json_integers_do_not_raise() -> None:
    huge = 10**400
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 1.0, "output": 2.0, "currency": "USD"}}}
            },
            "tariffs": {
                "m": [
                    {
                        "provider": "deepseek",
                        "input": 1.0,
                        "output": 2.0,
                        "start": huge,
                        "end": 60,
                        "days": [1],
                    }
                ]
            },
            "tiers": {"m": [{"min_prompt_tokens": huge, "input": 1.0, "output": 2.0}]},
            "fx": {"usd_cny": huge},
        }
    )
    effective = catalog.resolve("deepseek", "m", prompt_tokens=1000)
    assert effective is not None
    assert effective.tariff_label == "base"
    assert effective.tier_label == ""
    assert catalog.usd_cny == 7.2


def test_huge_offer_numbers_are_rejected() -> None:
    huge = 10**400
    catalog = RateCatalog.from_dict(
        {
            "providers": {},
            "offers": {
                "huge": {
                    "count": 1,
                    "input_max": huge,
                    "output_max": huge,
                    "cache_read_max": huge,
                }
            },
        }
    )
    assert catalog.resolve("openai_compatible", "huge") is None


def test_deeply_nested_catalog_file_falls_back(tmp_path) -> None:
    nested = "[" * 20000 + "0" + "]" * 20000
    path = tmp_path / "catalog.json"
    path.write_text('{"providers": {"x": ' + nested + "}}", encoding="utf-8")
    catalog = RateCatalog.load(path)
    assert catalog.usd_cny == 7.2
    assert catalog.resolve("deepseek", "m") is None


def test_non_list_weekdays_are_rejected() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 1.0, "output": 2.0, "currency": "USD"}}}
            },
            "tariffs": {
                "m": [
                    {
                        "provider": "deepseek",
                        "input": 2.0,
                        "output": 4.0,
                        "start": 0,
                        "end": 60,
                        "days": 0.5,
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 29, 0, 30))
    assert effective is not None
    assert effective.tariff_label == "base"


def test_null_weekdays_are_rejected() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {
                "deepseek": {"models": {"m": {"input": 1.0, "output": 2.0, "currency": "USD"}}}
            },
            "tariffs": {
                "m": [
                    {
                        "provider": "deepseek",
                        "input": 2.0,
                        "output": 4.0,
                        "start": 0,
                        "end": 60,
                        "days": None,
                    }
                ]
            },
        }
    )
    effective = catalog.resolve("deepseek", "m", now=_utc(2026, 11, 29, 0, 30))
    assert effective is not None
    assert effective.tariff_label == "base"


def test_absurdly_large_but_finite_rates_are_rejected() -> None:
    catalog = RateCatalog.from_dict(
        {
            "providers": {},
            "offers": {
                "huge-finite": {
                    "count": 1,
                    "input_max": 1e308,
                    "output_max": 1e308,
                }
            },
        }
    )
    assert catalog.resolve("openai_compatible", "huge-finite") is None
