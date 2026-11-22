"""Build the bundled LLM price catalog from public datasets.

Pure builders (no network) shared by ``scripts/build_llm_price_catalog.py``
and the opt-in ``openbiliclaw price --sync`` path. The catalog is a compact
snapshot of:

- models.dev provider/model prices (USD per 1M tokens);
- OpenRouter ``pricing.overrides`` time-of-day tariffs and long-context tiers;
- cross-provider offer statistics used as a conservative relay bound.

Zero or missing prices are never emitted as free. Explicitly free providers
(``ollama`` / ``sponsored``) are handled by the runtime pricing table.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.rate_card import normalize_model_id

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

DEFAULT_MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

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
_OPENROUTER_PROVIDER_ALIASES = {
    "z-ai": "zai",
    "zhipuai": "zai",
    "anthropic": "anthropic",
    "google": "google",
    "openai": "openai",
    "deepseek": "deepseek",
}

_MAX_CATALOG_RATE = 1_000_000.0

TARIFF_ALIASES: dict[str, str] = {
    "deepseek-v4.1-flash": "deepseek-v4-flash",
    "deepseek-v4.1-flash-1017": "deepseek-v4-flash",
    "deepseek-flash": "deepseek-v4-flash",
}


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    # Keep the raw per-token precision: rounding at 8 decimals would turn
    # OpenRouter's $0.003/M cache rate (3e-9 per token) into zero before the
    # ×1e6 conversion in ``_openrouter_rates``.
    return float(number)


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
    input_rate, output_rate, cache_read, cache_write = rates
    converted = (
        round(input_rate * 1_000_000, 8),
        round(output_rate * 1_000_000, 8),
        None if cache_read is None else round(cache_read * 1_000_000, 8),
        None if cache_write is None else round(cache_write * 1_000_000, 8),
    )
    if converted[0] > _MAX_CATALOG_RATE or converted[1] > _MAX_CATALOG_RATE:
        return None
    if any(value is not None and value > _MAX_CATALOG_RATE for value in converted[2:]):
        return None
    return converted


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
            if rates is None or not _plausible_rates(rates):
                continue
            models[str(model_id).lower()] = _rate_record(rates, source="models.dev")
        if models:
            providers[provider_id] = {"models": models}
    return providers


def _plausible_rates(rates: tuple[float, float, float | None, float | None]) -> bool:
    if rates[0] > _MAX_CATALOG_RATE or rates[1] > _MAX_CATALOG_RATE:
        return False
    return all(value is None or value <= _MAX_CATALOG_RATE for value in rates[2:])


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
            if rates[0] <= _MAX_CATALOG_RATE:
                bucket["input"].append(rates[0])
            if rates[1] <= _MAX_CATALOG_RATE:
                bucket["output"].append(rates[1])
            if rates[2] is not None and rates[2] <= _MAX_CATALOG_RATE:
                bucket["cache_read"].append(rates[2])

    offers: dict[str, dict[str, Any]] = {}
    for model_id, bucket in sorted(buckets.items()):
        outputs = bucket["output"]
        if not outputs or not bucket["input"]:
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
    except (TypeError, ValueError, OverflowError):
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
        route_id = str(row.get("id") or "")
        if ":batch" in route_id.lower():
            # Batch routes share the normalized model id but are roughly half
            # price; mixing them into the standard tier misprices ordinary
            # prompts (OpenRouter exposes them as ``model:batch``).
            continue
        author = route_id.split("/", 1)[0].strip().lower()
        provider = _OPENROUTER_PROVIDER_ALIASES.get(author, author)
        model_id = normalize_model_id(route_id)
        model_id = TARIFF_ALIASES.get(model_id, model_id)
        if not model_id:
            continue
        model_tiers: list[dict[str, Any]] = []
        for override in overrides:
            if not isinstance(override, dict):
                continue
            threshold_raw = override.get("min_prompt_tokens")
            if threshold_raw is None:
                continue
            try:
                threshold = int(threshold_raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if threshold <= 0:
                continue
            rates = _openrouter_rates(override)
            if rates is None:
                continue
            model_tiers.append(
                {
                    "provider": provider,
                    "min_prompt_tokens": threshold,
                    "input": rates[0],
                    "output": rates[1],
                    "cache_read": rates[2],
                    "cache_write": rates[3],
                }
            )
        if model_tiers:
            # Merge across routes that normalize to the same model id instead
            # of letting the last row overwrite earlier providers' tiers.
            tiers.setdefault(model_id, []).extend(model_tiers)
    for model_id, records in tiers.items():
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in records:
            key = (
                record["provider"],
                record["min_prompt_tokens"],
                record["input"],
                record["output"],
            )
            unique[key] = record
        tiers[model_id] = sorted(
            unique.values(),
            key=lambda item: (item["provider"], item["min_prompt_tokens"]),
        )
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
        route_id = str(row.get("id") or "")
        if ":batch" in route_id.lower():
            # Batch routes share the normalized model id but are roughly half
            # price; mixing them into the standard tier misprices ordinary
            # prompts (OpenRouter exposes them as ``model:batch``).
            continue
        author = route_id.split("/", 1)[0].strip().lower()
        provider = _OPENROUTER_PROVIDER_ALIASES.get(author, author)
        model_id = normalize_model_id(route_id)
        model_id = TARIFF_ALIASES.get(model_id, model_id)
        if not model_id:
            continue
        windows: list[dict[str, Any]] = []
        for override in overrides:
            if not isinstance(override, dict):
                continue
            start = _minute_of_day(override.get("utc_start"))
            end = _minute_of_day(override.get("utc_end"))
            if start is None or end is None or start == end:
                continue
            rates = _openrouter_rates(override)
            if rates is None:
                continue
            if "utc_days" not in override:
                # OpenRouter omits ``utc_days`` for a daily window; that is
                # different from a present-but-null/malformed value.
                days = list(range(7))
            else:
                day_names_raw = override.get("utc_days")
                if not isinstance(day_names_raw, list) or not day_names_raw:
                    continue
                days = sorted(
                    day_names.index(str(day).lower())
                    for day in day_names_raw
                    if str(day).lower() in day_names
                )
            if not days:
                continue
            multiplier = rates[1] / base[1] if base[1] else 1.0
            if abs(multiplier - 1.0) < 0.02:
                # A window that bills the same as base is noise; the runtime
                # treats "no matching window" as base price.
                continue
            windows.append(
                {
                    "provider": provider,
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
            # Merge across routes that normalize to the same model id; the
            # provider field keeps each route's schedule separate.
            tariffs.setdefault(model_id, []).extend(windows)
    for model_id, records in tariffs.items():
        unique_tariffs: dict[tuple[Any, ...], dict[str, Any]] = {}
        for window in records:
            key = (
                window["provider"],
                tuple(window["days"]),
                window["start"],
                window["end"],
                window["label"],
            )
            unique_tariffs[key] = window
        tariffs[model_id] = sorted(
            unique_tariffs.values(),
            key=lambda item: (
                item["provider"],
                item["days"],
                item["start"],
                item["end"],
            ),
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


def _default_fetcher(url: str, *, timeout: float) -> Mapping[str, Any]:
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def sync_price_catalog(
    out_path: str | Path,
    *,
    fetcher: Callable[[str], Mapping[str, Any]] | None = None,
    usd_cny_rate: float = 7.2,
    timeout: float = 60.0,
    models_dev_url: str = DEFAULT_MODELS_DEV_URL,
    openrouter_url: str = DEFAULT_OPENROUTER_URL,
) -> dict[str, Any]:
    """Fetch both public datasets and atomically replace ``out_path``.

    The caller owns the destination (CLI writes ``<data_dir>/pricing_catalog.json``
    so ``catalog_for_runtime`` picks it up without a config edit). A failed
    fetch or parse leaves the existing file untouched.
    """

    fetch = fetcher or (lambda url: _default_fetcher(url, timeout=timeout))
    models_dev = dict(fetch(models_dev_url))
    openrouter = dict(fetch(openrouter_url))
    catalog = build_catalog(models_dev, openrouter, usd_cny_rate=usd_cny_rate)
    if not (catalog.get("providers") or catalog.get("offers")):
        raise ValueError("refusing to replace the price catalog with an empty snapshot")
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".pricing_catalog-",
        dir=destination.parent,
        suffix=".json",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    catalog,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "providers": len(catalog["providers"]),
        "offers": len(catalog["offers"]),
        "tariffs": len(catalog["tariffs"]),
        "tiers": len(catalog["tiers"]),
        "generated_at": catalog["generated_at"],
    }
