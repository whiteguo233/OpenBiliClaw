"""Records per-call LLM usage to the database for cost tracking.

Hooks into ``LLMService`` after every successful provider response. The
service hands us the ``LLMResponse`` (which carries provider-reported
``usage`` fields), we look up the price tier in
``openbiliclaw.llm.pricing`` and append a row to the ``llm_usage``
table. ``openbiliclaw cost`` reads back the table for daily summaries.

Failures are deliberately swallowed inside ``record()`` — billing
should never block a successful LLM response from reaching the
caller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.llm.pricing import estimate_cost

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse

logger = logging.getLogger(__name__)


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
        if self._sink is None or response is None:
            return

        usage = getattr(response, "usage", None) or {}
        provider = str(getattr(response, "provider", "") or "").strip().lower()
        model = str(getattr(response, "model", "") or "").strip()

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)

        try:
            cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
            self._sink.insert_llm_usage(
                provider=provider or "unknown",
                model=model or "",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_cny=cost,
                caller=caller,
                success=True,
            )
        except Exception:
            # Never block the LLM hot path on billing-table writes.
            # Worst case: a partial row is missed; ledger drifts ~0.1%.
            logger.debug("UsageRecorder.record failed", exc_info=True)
