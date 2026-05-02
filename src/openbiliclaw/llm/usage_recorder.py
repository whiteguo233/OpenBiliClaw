"""Records per-call LLM usage to the database for cost tracking.

Hooks into ``LLMService`` after every successful provider response. The
service hands us the ``LLMResponse`` (which carries provider-reported
``usage`` fields), we look up the price tier in
``openbiliclaw.llm.pricing`` and append a row to the ``llm_usage``
table. ``openbiliclaw cost`` reads back the table for daily summaries.

Failures are deliberately swallowed inside ``record()`` — billing
should never block a successful LLM response from reaching the
caller.

Two side-channels for real-time observability:

- INFO log on every successful call:
  ``[llm-cost] caller=discovery.evaluate model=deepseek-v4-flash 850→230 tok ≈ ¥0.0010``
  Lets you ``tail -f`` daemon logs and see cost flowing in live.
- WARN log when a *single* call exceeds ``EXPENSIVE_CALL_CNY_THRESHOLD``
  (default ¥0.10). This catches runaway prompts (a 32K-token reasoning
  call costs ~¥0.5 silently otherwise).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.llm.pricing import estimate_cost

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse

logger = logging.getLogger(__name__)

# Single-call threshold above which we WARN. Most legitimate
# OpenBiliClaw calls cost <¥0.01; ¥0.10 is ~10x that, well above
# any expected per-call cost and into "something's wrong" territory.
# Override via env var if a deployment runs higher-quality models on
# purpose (Opus 4.7 alone can exceed this on a long prompt).
_EXPENSIVE_THRESHOLD_DEFAULT = 0.10
EXPENSIVE_CALL_CNY_THRESHOLD = float(
    os.environ.get("OPENBILICLAW_LLM_EXPENSIVE_CNY", _EXPENSIVE_THRESHOLD_DEFAULT)
)


class _UsageSink(Protocol):
    """Minimal contract the recorder needs from a database-like object."""

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int: ...


class UsageRecorder:
    """Append per-call usage rows to the LLM ledger.

    Constructed once per process (typically by ``runtime_context``) and
    passed into ``LLMService``. ``record()`` is called from the service
    on every response — the recorder pulls token counts out of the
    response's ``usage`` dict, estimates cost via ``pricing``, and
    appends one row.
    """

    def __init__(self, sink: _UsageSink | None) -> None:
        self._sink = sink

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def record(
        self,
        response: LLMResponse | None,
        *,
        caller: str = "",
    ) -> None:
        """Persist the usage row for one LLM response.

        ``response`` may be None (degenerate path) — we silently no-op
        rather than raising, since the caller is in a hot path.
        """
        if response is None:
            return

        usage = getattr(response, "usage", None) or {}
        provider = str(getattr(response, "provider", "") or "").strip().lower()
        model = str(getattr(response, "model", "") or "").strip()

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        # Normalized cache field — providers populate when their backend
        # served some input tokens from prompt cache. ``cached_input_tokens``
        # is always <= prompt_tokens. See provider docs in
        # openai_provider.py / claude_provider.py / gemini_provider.py.
        cached_tokens = int(usage.get("cached_input_tokens", 0) or 0)

        try:
            cost = estimate_cost(
                provider,
                model,
                prompt_tokens,
                completion_tokens,
                cached_tokens=cached_tokens,
            )
        except Exception:
            logger.debug("estimate_cost failed", exc_info=True)
            return

        # Real-time INFO log so `journalctl -fu openbiliclaw` /
        # `docker logs -f` shows cost as it accrues. Caller defaults
        # to "?" when untagged so the log reads consistently. Cache hit
        # ratio annotated when present so you can spot a builder that
        # poisoned its prompt prefix.
        caller_tag = caller or "?"
        cache_note = ""
        if cached_tokens > 0 and prompt_tokens > 0:
            hit = cached_tokens / prompt_tokens * 100
            cache_note = f" cache_hit={cached_tokens}/{prompt_tokens} ({hit:.0f}%)"
        logger.info(
            "[llm-cost] caller=%s model=%s tokens=%d→%d ≈ ¥%.4f%s",
            caller_tag,
            model or "(unknown)",
            prompt_tokens,
            completion_tokens,
            cost,
            cache_note,
        )

        # Anomaly WARN — single call over threshold is almost always a
        # runaway prompt (forgotten history truncation, oversized batch,
        # accidentally enabled reasoning budget, etc.). Worth logging
        # loudly so it's noticed before $$ accumulate.
        if cost >= EXPENSIVE_CALL_CNY_THRESHOLD:
            logger.warning(
                "[llm-cost] expensive single call: caller=%s model=%s "
                "%d→%d tokens ≈ ¥%.4f (threshold ¥%.2f, override via "
                "OPENBILICLAW_LLM_EXPENSIVE_CNY)",
                caller_tag,
                model or "(unknown)",
                prompt_tokens,
                completion_tokens,
                cost,
                EXPENSIVE_CALL_CNY_THRESHOLD,
            )

        if self._sink is None:
            return

        try:
            self._sink.insert_llm_usage(
                provider=provider or "unknown",
                model=model or "",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=cached_tokens,
                estimated_cost_cny=cost,
                caller=caller,
                success=True,
            )
        except Exception:
            # Never block the LLM hot path on billing-table writes.
            # Worst case: a partial row is missed; ledger drifts ~0.1%.
            logger.debug("UsageRecorder.record failed", exc_info=True)
