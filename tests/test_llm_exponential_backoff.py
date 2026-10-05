from __future__ import annotations

import time

from openbiliclaw.llm.base import LLMRegistry


def test_rate_limit_backoff_grows_exponentially_and_caps() -> None:
    registry = LLMRegistry()

    registry._mark_rate_limited("provider-a")
    first = registry._rate_limited_until["provider-a"] - time.monotonic()
    assert 55 <= first <= 65

    registry._mark_rate_limited("provider-a")
    second = registry._rate_limited_until["provider-a"] - time.monotonic()
    assert 115 <= second <= 125

    registry._mark_rate_limited("provider-a")
    third = registry._rate_limited_until["provider-a"] - time.monotonic()
    assert 235 <= third <= 245

    # Repeated failures should eventually hit the cap, not grow forever.
    for _ in range(10):
        registry._mark_rate_limited("provider-a")
    capped = registry._rate_limited_until["provider-a"] - time.monotonic()
    assert capped <= registry._RATE_LIMIT_MAX_COOLDOWN_SECONDS + 1
