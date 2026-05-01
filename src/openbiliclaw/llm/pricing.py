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
"""

from __future__ import annotations

# (input_rate, output_rate) — CNY per 1,000 tokens.
PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "deepseek": {
        # The deepseek-chat (V3) and deepseek-reasoner (R1) lines are
        # the current published series. ``deepseek-v4-flash`` is the
        # config-default in this project; tracked under the V4-flash
        # rate row so users running the default get reasonable
        # estimates even if the model name isn't in DeepSeek's public
        # pricing table at any given moment.
        "deepseek-chat": (0.0007, 0.0014),
        "deepseek-v4-flash": (0.001, 0.002),
        "deepseek-reasoner": (0.004, 0.016),
        "default": (0.001, 0.002),
    },
    "openai": {
        # USD prices × ~7.2 (post-2024 USD/CNY). Leave one decimal of
        # slack since the rate is a moving target and OpenAI's tier
        # discounts complicate the picture.
        "gpt-4o": (0.018, 0.072),
        "gpt-4o-mini": (0.0011, 0.0043),
        "gpt-4-turbo": (0.072, 0.216),
        "text-embedding-3-small": (0.000144, 0.0),
        "text-embedding-3-large": (0.00094, 0.0),
        "default": (0.018, 0.072),
    },
    "claude": {
        "claude-sonnet-4-20250514": (0.022, 0.108),
        "claude-3-5-sonnet": (0.022, 0.108),
        "claude-3-haiku": (0.0018, 0.009),
        "default": (0.022, 0.108),
    },
    "gemini": {
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
) -> float:
    """Estimate the CNY cost of a single LLM call.

    Falls back to the provider-level ``default`` rate when the exact
    model isn't in the table, then to a generic fallback if the
    provider itself is unknown — so unknown models still produce a
    nonzero number rather than a silent zero.

    >>> estimate_cost("deepseek", "deepseek-v4-flash", 5000, 3000)
    0.011
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
    return round(
        (max(0, prompt_tokens) / 1000.0) * input_rate
        + (max(0, completion_tokens) / 1000.0) * output_rate,
        6,
    )
