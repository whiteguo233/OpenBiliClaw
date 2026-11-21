#!/usr/bin/env python3
"""Build the bundled LLM price catalog from public datasets.

Inputs (download first; the script never fetches by itself):

- ``models.dev``  ``https://models.dev/api.json``
- ``openrouter``  ``https://openrouter.ai/api/v1/models``

Usage:

    curl -sS https://models.dev/api.json -o /tmp/modelsdev.json
    curl -sS https://openrouter.ai/api/v1/models -o /tmp/openrouter.json
    python scripts/build_llm_price_catalog.py \
        --models-dev /tmp/modelsdev.json \
        --openrouter /tmp/openrouter.json \
        --out src/openbiliclaw/llm/pricing_catalog.json

The output is a compact, deterministic JSON snapshot (see ``docs/plans/
2026-11-21-llm-price-catalog-and-peak-scheduling.md``). It contains:

- provider/model cards for a curated provider allowlist (USD per 1M tokens);
- cross-provider offer statistics per model (for unknown-relay fallback);
- time-of-day tariffs parsed from OpenRouter's ``pricing.overrides``.

Zero or missing prices are never emitted as free: they are excluded from the
offer statistics, and models with no priced offer are omitted. Explicitly free
providers (``ollama``, ``sponsored``) are handled in the runtime pricing table,
not here.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.llm.rate_card import normalize_model_id  # noqa: E402

# Providers whose model IDs the app can route to directly. Kept small on
# purpose: every extra provider grows the bundled snapshot.
PROVIDER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "deepseek",
        "openai",
        "anthropic",
        "google",
        "openrouter",
        "zai",
        "zhipuai",
        "moonshotai",
        "minimax",
        "alibaba",
        "xai",
        "mistral",
        "meta",
        "deepinfra",
        "groq",
        "together",
        "fireworks",
        "sensenova",
        "siliconflow",
    }
)

# Route models that share a tariff schedule but not an exact ID.
TARIFF_ALIASES: dict[str, str] = {
    "deepseek-v4.1-flash": "deepseek-v4-flash",
    "deepseek-v4.1-flash-1017": "deepseek-v4-flash",
    "deepseek-flash": "deepseek-v4-flash",
}


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    return round(number, 8)


def _cost_tuple(cost: Any) -> tuple[float, float, float | None, float | None] | None:
    if not isinstance(cost, dict):
        return None
    input_rate = _positive(cost.get("input"))
    output_rate = _positive(cost.get("output"))
    if input_rate is None or output_rate is None:
        # Some catalogs report ``reasoning`` instead of ``output``.
        output_rate = _positive(cost.get("reasoning")) or output_rate
    if input_rate is None or output_rate is None:
        return None
    return (
        input_rate,
        output_rate,
        _positive(cost.get("cache_read")),
        _positive(cost.get("cache_write")),
    )


def _rate_record(
    rates: tuple[float, float, float | None, float | None],
    *,
    source: str,
) -> dict[str, Any]:
    input_rate, output_rate, cache_read, cache_write = rates
    return {
        "input": input_rate,
        "output": output_rate,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "currency": "USD",
        "source": source,
    }


def _openrouter_rates(
    record: dict[str, Any],
) -> tuple[float, float, float | None, float | None] | None:
    """Convert OpenRouter per-token USD prices to per-million USD."""

    rates = _cost_tuple(
        {
            "input": record.get("prompt"),
            "output": record.get("completion"),
            "cache_read": record.get("input_cache_read"),
            "cache_write": record.get("input_cache_write"),
        }
    )
    if rates is None:
        return None
    return tuple(
        None if value is None else round(value * 1_000_000, 8)  # type: ignore[arg-type]
        for value in rates
    )  # type: ignore[return-value]


def build_provider_cards(models_dev: dict[str, Any]) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for provider_id in sorted(PROVIDER_ALLOWLIST):
        provider = models_dev.get(provider_id)
        if not isinstance(provider, dict):
            continue
        models: dict[str, Any] = {}
        for model_id, model in sorted((provider.get("models") or {}).items()):
            if not isinstance(model, dict):
                continue
            rates = _cost_tuple(model.get("cost"))
            if rates is None:
                continue
            models[str(model_id).lower()] = _rate_record(rates, source="models.dev")
        if models:
            providers[provider_id] = {"models": models}
    return providers


def build_offers(models_dev: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Aggregate positive prices per model across every provider.

    ``0`` / missing prices are excluded. The runtime uses ``max`` as the
    conservative upper bound for a relay whose upstream is unknown.
    """

    buckets: dict[str, dict[str, list[float]]] = {}
    for provider in models_dev.values():
        if not isinstance(provider, dict):
            continue
        for model_id, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            rates = _cost_tuple(model.get("cost"))
            if rates is None:
                continue
            normalized = normalize_model_id(str(model_id))
            if not normalized:
                continue
            bucket = buckets.setdefault(
                normalized,
                {"input": [], "output": [], "cache_read": []},
            )
            bucket["input"].append(rates[0])
            bucket["output"].append(rates[1])
            if rates[2] is not None:
                bucket["cache_read"].append(rates[2])

    offers: dict[str, dict[str, Any]] = {}
    for model_id, bucket in sorted(buckets.items()):
        outputs = bucket["output"]
        if not outputs:
            continue
        offers[model_id] = {
            "count": len(outputs),
            "input_max": round(max(bucket["input"]), 8),
            "input_median": round(statistics.median(bucket["input"]), 8),
            "output_max": round(max(outputs), 8),
            "output_median": round(statistics.median(outputs), 8),
            "cache_read_max": (
                round(max(bucket["cache_read"]), 8) if bucket["cache_read"] else None
            ),
        }
    return offers


def _minute_of_day(raw: Any) -> int | None:
    """OpenRouter ``utc_start`` / ``utc_end`` are hundredths of an hour."""

    try:
        hundredths = int(raw)
    except (TypeError, ValueError):
        return None
    if hundredths < 0 or hundredths > 2400:
        return None
    return hundredths * 6 // 10  # 100 hundredths = 60 minutes


def build_tiers(openrouter: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Extract long-context price tiers (``min_prompt_tokens`` overrides)."""

    tiers: dict[str, list[dict[str, Any]]] = {}
    rows = openrouter.get("data") if isinstance(openrouter, dict) else None
    if not isinstance(rows, list):
        return tiers
    for row in rows:
        if not isinstance(row, dict):
            continue
        pricing = row.get("pricing")
        if not isinstance(pricing, dict):
            continue
        overrides = pricing.get("overrides")
        if not isinstance(overrides, list):
            continue
        model_id = normalize_model_id(str(row.get("id") or ""))
        model_id = TARIFF_ALIASES.get(model_id, model_id)
        if not model_id:
            continue
        model_tiers: list[dict[str, Any]] = []
        for override in overrides:
            try:
                threshold = int(override.get("min_prompt_tokens"))
            except (TypeError, ValueError):
                continue
            if threshold <= 0:
                continue
            rates = _openrouter_rates(override)
            if rates is None:
                continue
            model_tiers.append(
                {
                    "min_prompt_tokens": threshold,
                    "input": rates[0],
                    "output": rates[1],
                    "cache_read": rates[2],
                    "cache_write": rates[3],
                }
            )
        if model_tiers:
            tiers[model_id] = sorted(model_tiers, key=lambda item: item["min_prompt_tokens"])
    return tiers


def build_tariffs(openrouter: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    tariffs: dict[str, list[dict[str, Any]]] = {}
    rows = openrouter.get("data") if isinstance(openrouter, dict) else None
    if not isinstance(rows, list):
        return tariffs
    for row in rows:
        if not isinstance(row, dict):
            continue
        pricing = row.get("pricing")
        if not isinstance(pricing, dict):
            continue
        overrides = pricing.get("overrides")
        if not isinstance(overrides, list):
            continue
        base = _openrouter_rates(pricing)
        if base is None:
            continue
        model_id = normalize_model_id(str(row.get("id") or ""))
        model_id = TARIFF_ALIASES.get(model_id, model_id)
        if not model_id:
            continue
        windows: list[dict[str, Any]] = []
        for override in overrides:
            start = _minute_of_day(override.get("utc_start"))
            end = _minute_of_day(override.get("utc_end"))
            if start is None or end is None or start == end:
                continue
            rates = _openrouter_rates(override)
            if rates is None:
                continue
            day_names_raw = override.get("utc_days")
            if isinstance(day_names_raw, list) and day_names_raw:
                days = sorted(
                    day_names.index(str(day).lower())
                    for day in day_names_raw
                    if str(day).lower() in day_names
                )
            else:
                days = list(range(7))
            if not days:
                continue
            multiplier = rates[1] / base[1] if base[1] else 1.0
            if abs(multiplier - 1.0) < 0.02:
                # A window that bills the same as base is noise; the runtime
                # treats "no matching window" as base price.
                continue
            windows.append(
                {
                    "days": days,
                    "start": start,
                    "end": end,
                    "input": rates[0],
                    "output": rates[1],
                    "cache_read": rates[2],
                    "cache_write": rates[3],
                    "label": "peak" if multiplier > 1.02 else "discount",
                    "multiplier": round(multiplier, 4),
                }
            )
        if windows:
            # De-duplicate identical windows emitted for several route variants.
            unique: dict[tuple[Any, ...], dict[str, Any]] = {}
            for window in windows:
                key = (
                    tuple(window["days"]),
                    window["start"],
                    window["end"],
                    window["label"],
                )
                unique[key] = window
            tariffs[model_id] = sorted(
                unique.values(),
                key=lambda item: (item["days"], item["start"], item["end"]),
            )
    return tariffs


def build_catalog(
    models_dev: dict[str, Any],
    openrouter: dict[str, Any],
    *,
    usd_cny_rate: float = 7.2,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    moment = generated_at or datetime.now(UTC)
    providers = build_provider_cards(models_dev)
    offers = build_offers(models_dev)
    tariffs = build_tariffs(openrouter)
    tiers = build_tiers(openrouter)
    return {
        "version": 1,
        "generated_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fx": {"usd_cny": round(float(usd_cny_rate), 4)},
        "sources": {
            "models_dev": {
                "url": "https://models.dev/api.json",
                "providers": len(models_dev),
                "models": sum(
                    len(provider.get("models") or {})
                    for provider in models_dev.values()
                    if isinstance(provider, dict)
                ),
                "priced_models": len(offers),
            },
            "openrouter": {
                "url": "https://openrouter.ai/api/v1/models",
                "models": len(openrouter.get("data") or []),
                "with_tariffs": len(tariffs),
                "with_tiers": len(tiers),
            },
        },
        "providers": providers,
        "aliases": dict(TARIFF_ALIASES),
        "offers": offers,
        "tariffs": tariffs,
        "tiers": tiers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dev", required=True, type=Path)
    parser.add_argument("--openrouter", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--usd-cny", type=float, default=7.2)
    args = parser.parse_args()

    models_dev = json.loads(args.models_dev.read_text(encoding="utf-8"))
    openrouter = json.loads(args.openrouter.read_text(encoding="utf-8"))
    catalog = build_catalog(models_dev, openrouter, usd_cny_rate=args.usd_cny)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    size_kb = args.out.stat().st_size / 1024
    print(
        f"wrote {args.out} ({size_kb:.0f} KB): "
        f"{len(catalog['providers'])} providers, "
        f"{len(catalog['offers'])} models with offers, "
        f"{len(catalog['tariffs'])} models with tariffs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
