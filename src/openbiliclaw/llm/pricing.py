"""Per-provider / per-model token pricing in CNY.

Rates are quoted as **CNY per 1K tokens**, listed as ``(input, output)``.
USD-priced models (OpenAI, Claude, etc.) are pre-multiplied by an
approximate exchange rate so the entire pricing surface is a single
currency — keeps daily-spend reports straightforward and avoids burying
fx conversions inside hot paths. Rates drift, so treat results as
estimates (typically within ±20% of actual billing).

Source notes (last refreshed 2026-05):

- DeepSeek: official platform rates (https://platform.deepseek.com/api-docs/pricing)
- OpenAI: API price page (https://openai.com/api/pricing) × USD/CNY ≈ 7.2
- Anthropic Claude: console pricing × USD/CNY ≈ 7.2
- Gemini: AI Studio pricing × USD/CNY ≈ 7.2
- OpenRouter: variable per-route; the ``default`` rate is a midrange
  placeholder. For accurate per-route tracking, override at call site.
- Ollama: local inference, treated as free.

**Prompt-cache discount** (v0.3.28+): when a portion of input tokens is
served from provider-side prompt cache, the cached portion is billed at
a deep discount. ``CACHE_HIT_DISCOUNT`` per provider expresses the
**multiplier** applied to the cached-portion's input rate:

- DeepSeek: 0.10 (90% off — official)
- OpenAI: 0.50 (~50% off, family-wide as of 2026; some models 0.25)
- Claude: 0.10 (90% off reads — Anthropic prompt-caching)
- Gemini: 0.25 (~75% off cached_content_token_count via Context Caching API)
- Others: assume 0.50 conservatively when an unknown provider reports
  cached tokens

``estimate_cost(..., cached_tokens=N)`` applies it: the cached portion
of ``prompt_tokens`` is billed at ``input_rate * discount``, the
non-cached portion at the full ``input_rate``, output unchanged.
"""

from __future__ import annotations

# Per-provider cache-hit discount multipliers. 0.1 means cached tokens
# are billed at 10% of the full input rate (i.e. 90% off). When a
# provider isn't in this map we use 0.5 (conservative — half off).
CACHE_HIT_DISCOUNT: dict[str, float] = {
    "deepseek": 0.10,
    "openai": 0.50,
    "claude": 0.10,
    "gemini": 0.25,
    "openrouter": 0.50,
    "ollama": 0.0,  # local; cached or not, cost is 0
}

# (input_rate, output_rate) — CNY per 1,000 tokens.
PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "deepseek": {
        # ``deepseek-v4-flash`` is the project default and the current
        # main-line model. ``deepseek-v4-pro`` is the higher-tier V4
        # variant. The legacy V3 ``deepseek-chat`` and R1
        # ``deepseek-reasoner`` rows stay so existing configs keep
        # producing accurate estimates until those models reach the
        # 2026/07/24 deprecation date.
        "deepseek-v4-flash": (0.001, 0.002),
        "deepseek-v4-pro": (0.004, 0.012),
        "deepseek-chat": (0.0007, 0.0014),
        "deepseek-reasoner": (0.004, 0.016),
        "default": (0.001, 0.002),
    },
    "openai": {
        # USD × ~7.2 (USD/CNY post-2024). GPT-5 family is current as of
        # 2026-05. gpt-4o family is retired from ChatGPT but API works.
        "gpt-5.5": (0.036, 0.216),  # $5/$30 per M
        "gpt-5.5-pro": (0.216, 1.296),  # $30/$180 per M
        "gpt-5.4-mini": (0.0054, 0.0324),  # $0.75/$4.5 per M
        "gpt-5.4-nano": (0.00144, 0.009),  # $0.20/$1.25 per M
        "gpt-5-nano": (0.00036, 0.00288),  # $0.05/$0.4 per M (cheapest)
        "gpt-4o": (0.018, 0.072),
        "gpt-4o-mini": (0.0011, 0.0043),
        "gpt-4-turbo": (0.072, 0.216),
        "text-embedding-3-small": (0.000144, 0.0),
        "text-embedding-3-large": (0.00094, 0.0),
        # OpenAI-compatible relay services (Kimi / MiniMax / Qwen / GLM /
        # Yi) all write provider="openai" in config — list a few common
        # model names here so the cost report remains useful for them.
        "kimi-k2.6": (0.001, 0.004),
        "kimi-k2.5": (0.001, 0.004),
        "MiniMax-M3": (0.00432, 0.01728),  # $0.60/$2.40 per M
        "MiniMax-M2.7": (0.00216, 0.00864),  # $0.30/$1.20 per M
        "MiniMax-M2.5": (0.00216, 0.00864),
        "qwen-flash": (0.0003, 0.0009),
        "qwen-plus": (0.0008, 0.002),
        "qwen-max": (0.0024, 0.0096),
        "glm-4.7-flash": (0.0, 0.0),  # free tier
        "glm-5": (0.005, 0.020),
        "yi-spark": (0.0001, 0.0001),
        "yi-medium": (0.0025, 0.0025),
        "yi-large": (0.02, 0.02),
        "default": (0.018, 0.072),
    },
    "claude": {
        # USD × 7.2; matches platform.claude.com 2026-05 pricing.
        "claude-opus-4-7": (0.108, 0.540),  # $15/$75 per M (Opus tier)
        "claude-opus-4-6": (0.036, 0.180),  # $5/$25 per M
        "claude-sonnet-4-6": (0.0216, 0.108),  # $3/$15 per M
        "claude-sonnet-4-5": (0.0216, 0.108),
        "claude-haiku-4-5": (0.0054, 0.027),  # cheap tier
        "claude-sonnet-4-20250514": (0.022, 0.108),
        "claude-3-5-sonnet": (0.022, 0.108),
        "claude-3-haiku": (0.0018, 0.009),
        "default": (0.0216, 0.108),
    },
    "gemini": {
        # 2.5 series stable; 3.x preview-tier still in flux 2026-05.
        # 3.1 Pro is currently Public Preview only — its real model id
        # on the Google API is "gemini-3.1-pro-preview". We list both so
        # estimate_cost matches whichever spelling lands in usage logs.
        "gemini-3.1-pro": (0.014, 0.056),
        "gemini-3.1-pro-preview": (0.014, 0.056),
        "gemini-3-pro-preview": (0.014, 0.056),
        "gemini-3-flash-preview": (0.0014, 0.0058),
        "gemini-3-flash": (0.0014, 0.0058),
        "gemini-3.1-flash-lite-preview": (0.00072, 0.0029),
        "gemini-2.5-flash": (0.0011, 0.0029),
        "gemini-2.5-pro": (0.009, 0.072),
        "gemini-embedding-001": (0.000108, 0.0),
        "default": (0.0011, 0.0029),
    },
    "openrouter": {
        # OpenRouter routes vary widely (anywhere from "free" relay of
        # local Ollama to GPT-4o-class). Without knowing the route, use
        # a midrange estimate and let users override per-call.
        "default": (0.005, 0.015),
    },
    "ollama": {
        "default": (0.0, 0.0),
    },
}


def estimate_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Estimate the CNY cost of a single LLM call.

    Falls back to the provider-level ``default`` rate when the exact
    model isn't in the table, then to a generic fallback if the
    provider itself is unknown — so unknown models still produce a
    nonzero number rather than a silent zero.

    ``cached_tokens`` (v0.3.28+) is the portion of ``prompt_tokens``
    served from provider-side prompt cache; that portion is billed at
    ``input_rate * CACHE_HIT_DISCOUNT[provider]`` (typically 10-50% of
    the full rate). Pass 0 (default) for cache-miss / unknown.

    >>> estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    0.011
    >>> estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000, cached_tokens=4000)
    0.0074
    >>> estimate_cost("ollama", "llama3", 10000, 5000)
    0.0
    """
    provider_rates = PRICING.get(provider, {})
    rates = provider_rates.get(model)
    if rates is None:
        rates = provider_rates.get("default")
    if rates is None:
        # Unknown provider — pick a midrange rate so the user notices
        # the unexpected provider in the bill rather than seeing 0.
        rates = (0.001, 0.003)

    input_rate, output_rate = rates
    prompt_tokens = max(0, prompt_tokens)
    completion_tokens = max(0, completion_tokens)
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))
    non_cached = prompt_tokens - cached_tokens

    discount = CACHE_HIT_DISCOUNT.get(provider, 0.5)
    return round(
        (non_cached / 1000.0) * input_rate
        + (cached_tokens / 1000.0) * input_rate * discount
        + (completion_tokens / 1000.0) * output_rate,
        6,
    )
