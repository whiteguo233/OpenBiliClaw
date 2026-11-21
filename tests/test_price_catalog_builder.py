"""Tests for the price-catalog generator (scripts/build_llm_price_catalog.py)."""

from __future__ import annotations

from typing import Any

from openbiliclaw.llm import price_catalog as builder


def _models_dev() -> dict[str, Any]:
    return {
        "deepseek": {
            "models": {
                "deepseek-v4-flash": {"cost": {"input": 0.15, "output": 0.6, "cache_read": 0.003}}
            }
        },
        "zai": {
            "models": {
                "deepseek-v4-flash": {"cost": {"input": 0.5, "output": 2.0, "cache_read": 0.02}},
                "zero-priced": {"cost": {"input": 0, "output": 0}},
            }
        },
    }


def _openrouter() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "deepseek/deepseek-v4.1-flash-1017",
                "pricing": {
                    "prompt": "0.00000015",
                    "completion": "0.0000006",
                    "input_cache_read": "0.000000003",
                    "overrides": [
                        {
                            "utc_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                            "utc_start": 100,
                            "utc_end": 400,
                            "prompt": "0.0000003",
                            "completion": "0.0000012",
                            "input_cache_read": "0.00000001",
                        },
                        {
                            "utc_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                            "utc_start": 400,
                            "utc_end": 600,
                            "prompt": "0.00000015",
                            "completion": "0.0000006",
                            "input_cache_read": "0.000000003",
                        },
                    ],
                },
            },
            {
                "id": "openai/gpt-tiered",
                "pricing": {
                    "prompt": "0.000001",
                    "completion": "0.000002",
                    "overrides": [
                        {
                            "min_prompt_tokens": 200000,
                            "prompt": "0.000002",
                            "completion": "0.000004",
                        }
                    ],
                },
            },
        ]
    }


def test_normalize_model_id_variants() -> None:
    assert builder.normalize_model_id("deepseek/deepseek-v4.1-flash-1017") == "deepseek-v4.1-flash"
    assert builder.normalize_model_id("GPT-6.2-Astra:batch") == "gpt-6.2-astra"


def test_provider_cards_exclude_zero_and_negative_prices() -> None:
    catalog = builder.build_catalog(_models_dev(), {"data": []})
    providers = catalog["providers"]
    assert "deepseek" in providers
    assert providers["deepseek"]["models"]["deepseek-v4-flash"]["input"] == 0.15
    # The reseller card exists, but the zero-priced model is not emitted.
    assert "zero-priced" not in providers["zai"]["models"]


def test_offers_use_positive_prices_and_max_for_conservative_fallback() -> None:
    catalog = builder.build_catalog(_models_dev(), {"data": []})
    offer = catalog["offers"]["deepseek-v4-flash"]
    assert offer["count"] == 2
    assert offer["input_max"] == 0.5
    assert offer["output_max"] == 2.0
    assert "zero-priced" not in catalog["offers"]


def test_time_tariffs_are_per_million_and_skip_same_price_windows() -> None:
    catalog = builder.build_catalog(_models_dev(), _openrouter())
    windows = catalog["tariffs"]["deepseek-v4-flash"]
    # The 400-600 window bills the same as base and is filtered out.
    assert len(windows) == 1
    window = windows[0]
    assert window["start"] == 60
    assert window["end"] == 240
    assert window["input"] == 0.3
    assert window["output"] == 1.2
    assert window["label"] == "peak"
    assert window["multiplier"] == 2.0


def test_long_context_tiers_are_extracted() -> None:
    catalog = builder.build_catalog(_models_dev(), _openrouter())
    tiers = catalog["tiers"]["gpt-tiered"]
    assert tiers == [
        {
            "min_prompt_tokens": 200000,
            "input": 2.0,
            "output": 4.0,
            "cache_read": None,
            "cache_write": None,
        }
    ]


def test_catalog_records_sources_and_aliases() -> None:
    catalog = builder.build_catalog(_models_dev(), _openrouter(), usd_cny_rate=7.3)
    assert catalog["fx"]["usd_cny"] == 7.3
    assert catalog["sources"]["models_dev"]["providers"] == 2
    assert catalog["sources"]["openrouter"]["models"] == 2
    assert catalog["aliases"]["deepseek-v4.1-flash"] == "deepseek-v4-flash"


def test_sync_price_catalog_writes_an_atomic_snapshot(tmp_path) -> None:
    import json

    payloads = {
        builder.DEFAULT_MODELS_DEV_URL: _models_dev(),
        builder.DEFAULT_OPENROUTER_URL: _openrouter(),
    }
    destination = tmp_path / "pricing_catalog.json"

    summary = builder.sync_price_catalog(
        destination,
        fetcher=payloads.__getitem__,
        usd_cny_rate=7.3,
    )

    assert summary["path"] == str(destination)
    assert summary["providers"] == 2
    assert summary["offers"] >= 1
    assert summary["tariffs"] == 1
    catalog = json.loads(destination.read_text(encoding="utf-8"))
    assert catalog["fx"]["usd_cny"] == 7.3
    assert catalog["providers"]["deepseek"]["models"]["deepseek-v4-flash"]["input"] == 0.15


def test_sync_failure_leaves_the_previous_catalog_untouched(tmp_path) -> None:
    destination = tmp_path / "pricing_catalog.json"
    destination.write_text("previous", encoding="utf-8")

    def _boom(_url: str):
        raise RuntimeError("network down")

    try:
        builder.sync_price_catalog(destination, fetcher=_boom)
    except RuntimeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("sync should propagate the fetch failure")

    assert destination.read_text(encoding="utf-8") == "previous"
