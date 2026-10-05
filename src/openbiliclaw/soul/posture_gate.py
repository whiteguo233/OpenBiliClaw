"""Posture gate — deep-write consistency judgement (Phase 3).

Deep profile writes (goal / value / core-state candidates, VALUES/CORE layer
updates, full soul rebuilds) are stable and expensive; a couple of noisy
behaviours should not rewrite them. The posture gate judges each such write
against the existing understanding and returns one of three verdicts:

- ``accept``    — write proceeds unchanged.
- ``downgrade`` — the direction may hold but evidence is thin / in tension with
  the profile: don't write the deep layer, demote to a *hypothesis* instead.
  (A conflict is a new hypothesis, not an error.)
- ``reject``    — noise / self-contradiction: drop the write.

Three modes (spec §invariant 3):

- ``off``     — full bypass, **zero LLM calls**, byte-identical to the pre-gate
  pipeline (the replay guard).
- ``shadow``  — the write proceeds immediately (zero delay); an **async
  side-channel** task consumes an *immutable commit-boundary snapshot* and
  records the judgement to the ledger as ``shadow_accept`` / ``shadow_downgrade``
  / ``shadow_reject`` (LLM failure → ``shadow_error``). The judgement task never
  re-reads live state, so later writes can't pollute the judged input.
- ``enforce`` — synchronous judgement; the caller applies the verdict. An LLM /
  parse failure conservatively downgrades (fail-closed-to-hypothesis) + WARNING.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.base import is_reasoning_budget_exhausted
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.prompts import build_posture_gate_prompt

if TYPE_CHECKING:
    from openbiliclaw.soul.ledger import ProfileLedger

logger = logging.getLogger(__name__)

ACCEPT = "accept"
DOWNGRADE = "downgrade"
REJECT = "reject"
_VALID_VERDICTS = frozenset({ACCEPT, DOWNGRADE, REJECT})

# Judged-change token budget. Deep-write diffs are small (a handful of traits /
# values / a candidate line); 512 output tokens comfortably holds a verdict +
# one-sentence reason. Revisit after a provider swap (pitfall #3).
# ``LLMService.complete_structured_task()`` floors every structured call at
# ``MIN_STRUCTURED_MAX_TOKENS`` (4096) for reasoning-first models, so the first
# production attempt starts at the floor; this constant stays the *logical*
# verdict budget and the retry threshold below still escalates to 8192.
_POSTURE_GATE_MAX_TOKENS = 512
# Reasoning-model fallback budget. Reasoning providers (e.g. sensenova
# deepseek-v4-flash) burn the whole output budget on invisible thinking and
# return "reasoning but no final content (finish_reason=length)" — a
# deterministic failure at 512 tokens that would poison the shadow observation
# window with shadow_error rows. Mirrors the preference-analyzer precedent:
# on that exact signature, retry ONCE with this raised budget (8k covers any
# realistic thinking trace for a 3-way verdict; calibrate after provider swap).
_POSTURE_GATE_REASONING_FALLBACK_MAX_TOKENS = 8192
_GATE_CALLER = "soul.posture_gate"


class _GateOutputError(RuntimeError):
    """The provider returned content but no valid gate judgement."""


@dataclass
class GateDecision:
    """Outcome of a gate evaluation.

    ``verdict`` is always one of the three whitelist values. ``enforced`` is
    True only when a synchronous judgement actually gated the write (enforce
    mode); shadow/off return ``accept`` with ``enforced=False`` so the write
    proceeds unchanged.

    ``is_error`` distinguishes a *genuine* conservative downgrade (the LLM
    judged the change thin / in tension) from a downgrade forced by an
    LLM/parse **exception** (F7). Both behave identically for the write
    (fail-closed to a hypothesis), but a rebuild caller reads ``is_error`` to
    decide whether to CLEAR its ``rebuild_pending`` (real verdict — the batch
    was judged) or KEEP it for a bounded retry (transient error — not yet
    judged). It mirrors the shadow path's ``shadow_error`` vs
    ``shadow_downgrade`` distinction. Access point ① callers ignore this field.
    """

    verdict: str = ACCEPT
    reason: str = ""
    enforced: bool = False
    is_error: bool = False

    @property
    def blocks(self) -> bool:
        """Whether the caller must NOT apply the deep write as-is."""
        return self.enforced and self.verdict in {DOWNGRADE, REJECT}

    @property
    def downgraded(self) -> bool:
        return self.enforced and self.verdict == DOWNGRADE


@dataclass(frozen=True)
class _GateSnapshot:
    """Immutable commit-boundary snapshot consumed by the shadow task."""

    gate_id: str
    write_point: str
    change: dict[str, Any]
    core_memory: dict[str, Any]
    ledger_digest: list[dict[str, Any]]
    source_refs: tuple[str, ...] = field(default_factory=tuple)


class PostureGate:
    """LLM-backed deep-write gate with off / shadow / enforce modes.

    ``registry`` is any object exposing an ``async complete_structured_task``
    (the engine passes its :class:`LLMService`). ``ledger`` records shadow
    judgements (best-effort). ``background_tasks`` (optional) is a set the gate
    adds shadow tasks to so their lifecycle is observable — they are ordinary
    async side tasks (distinct from the dialogue-learn queue worker's self-owned
    lifecycle).
    """

    def __init__(
        self,
        *,
        mode: str,
        registry: Any | None,
        ledger: ProfileLedger | None = None,
        background_tasks: set[asyncio.Task[Any]] | None = None,
    ) -> None:
        normalized = str(mode or "shadow").strip().lower()
        self._mode = normalized if normalized in {"off", "shadow", "enforce"} else "shadow"
        self._registry = registry
        self._ledger = ledger
        self._background_tasks = background_tasks if background_tasks is not None else set()
        # Retained for deterministic draining in tests.
        self._shadow_tasks: set[asyncio.Task[Any]] = set()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def enabled(self) -> bool:
        return self._mode != "off"

    async def evaluate(
        self,
        *,
        write_point: str,
        change: dict[str, Any],
        core_memory: dict[str, Any] | None = None,
        ledger_digest: list[dict[str, Any]] | None = None,
        source_refs: list[str] | None = None,
    ) -> GateDecision:
        """Judge a deep write.

        - ``off``: returns ``accept`` immediately with **no LLM call**.
        - ``shadow``: returns ``accept`` immediately and schedules an async
          judgement over an immutable snapshot (zero delay for the caller).
        - ``enforce``: awaits the judgement and returns the verdict; LLM / parse
          failure downgrades conservatively.
        """
        if self._mode == "off":
            return GateDecision(verdict=ACCEPT, enforced=False)

        snapshot = _GateSnapshot(
            gate_id=uuid.uuid4().hex[:12],
            write_point=str(write_point),
            change=copy.deepcopy(change),
            core_memory=copy.deepcopy(core_memory or {}),
            ledger_digest=copy.deepcopy(ledger_digest or []),
            source_refs=tuple(str(r) for r in (source_refs or [])),
        )

        if self._mode == "shadow":
            self._spawn_shadow(snapshot)
            return GateDecision(verdict=ACCEPT, enforced=False)

        # enforce — a provider failure fails closed to a hypothesis (downgrade).
        # ``is_error`` is set ONLY on the exception path so a rebuild caller can
        # keep its pending marker for a bounded retry (F7); a real downgrade
        # verdict from ``_judge`` leaves it False so the caller clears pending.
        try:
            verdict, reason = await self._judge(snapshot)
        except Exception as exc:
            logger.warning(
                "posture gate enforce judgement errored; downgrading conservatively: %s",
                exc,
            )
            return GateDecision(
                verdict=DOWNGRADE,
                reason="judgement error",
                enforced=True,
                is_error=True,
            )
        return GateDecision(verdict=verdict, reason=reason, enforced=True)

    # -- shadow side-channel ---------------------------------------------------

    def _spawn_shadow(self, snapshot: _GateSnapshot) -> None:
        try:
            task = asyncio.ensure_future(self._run_shadow(snapshot))
        except RuntimeError:
            # No running loop (e.g. a sync test path) — run inline best-effort.
            logger.debug("posture gate: no event loop for shadow task; skipping")
            return
        self._shadow_tasks.add(task)
        self._background_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._shadow_tasks.discard(t)
            self._background_tasks.discard(t)

        task.add_done_callback(_done)

    async def _run_shadow(self, snapshot: _GateSnapshot) -> None:
        try:
            verdict, reason = await self._judge(snapshot)
            self._record(snapshot, gate_verdict=f"shadow_{verdict}", reason=reason)
        except Exception as exc:  # provider / unexpected failure
            logger.debug("posture gate shadow judgement failed", exc_info=True)
            self._record(snapshot, gate_verdict="shadow_error", reason=str(exc), error=str(exc))

    async def drain_shadow(self) -> None:
        """Await all in-flight shadow judgement tasks (tests / shutdown)."""
        pending = list(self._shadow_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # -- core judgement --------------------------------------------------------

    async def _judge(self, snapshot: _GateSnapshot) -> tuple[str, str]:
        """Run the gate LLM over the snapshot; return ``(verdict, reason)``.

        Only ever consumes the immutable snapshot — never live state. Provider
        failures, missing/invalid JSON and non-whitelisted verdicts all raise:
        shadow records ``shadow_error`` and enforce returns a conservative
        downgrade with ``is_error=True``. Only an explicit valid LLM verdict is
        a genuine non-error judgement.
        """
        if self._registry is None:
            raise RuntimeError("posture gate has no LLM registry")
        messages = build_posture_gate_prompt(
            change=snapshot.change,
            core_memory=snapshot.core_memory,
            ledger_digest=snapshot.ledger_digest,
        )
        max_tokens = _POSTURE_GATE_MAX_TOKENS
        reasoning_budget_retried = False
        while True:
            try:
                response = await self._registry.complete_structured_task(
                    system_instruction=messages[0]["content"],
                    user_input=messages[1]["content"],
                    max_tokens=max_tokens,
                    caller=_GATE_CALLER,
                    reasoning_effort=None,
                    inject_core_memory=False,
                )
                break
            except Exception as exc:
                # Reasoning providers can exhaust the small verdict budget on
                # invisible thinking (deterministic, would land as shadow_error
                # every time). Retry once with the raised fallback budget —
                # same pattern as soul/preference_analyzer.py's chunk path.
                if (
                    is_reasoning_budget_exhausted(exc)
                    and not reasoning_budget_retried
                    and max_tokens < _POSTURE_GATE_REASONING_FALLBACK_MAX_TOKENS
                ):
                    reasoning_budget_retried = True
                    max_tokens = _POSTURE_GATE_REASONING_FALLBACK_MAX_TOKENS
                    logger.warning(
                        "posture gate reasoning exhausted %d-token budget; "
                        "retrying once with %d tokens",
                        _POSTURE_GATE_MAX_TOKENS,
                        max_tokens,
                    )
                    continue
                raise
        parsed = parse_llm_json_tolerant(getattr(response, "content", "") or "")
        if not isinstance(parsed, dict):
            logger.warning("posture gate returned non-dict JSON")
            raise _GateOutputError("unparseable gate response")
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in _VALID_VERDICTS:
            logger.warning("posture gate verdict %r not whitelisted", verdict)
            raise _GateOutputError(f"invalid gate verdict: {verdict!r}")
        return verdict, str(parsed.get("reason", ""))

    def _record(
        self,
        snapshot: _GateSnapshot,
        *,
        gate_verdict: str,
        reason: str = "",
        error: str = "",
    ) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.record(
                write_point=snapshot.write_point,
                source="posture_gate",
                before={"gate_id": snapshot.gate_id},
                after={"reason": reason, "change": snapshot.change},
                source_refs=list(snapshot.source_refs),
                gate_verdict=gate_verdict,
                error=error,
            )
        except Exception:  # pragma: no cover - ledger is best-effort
            logger.debug("posture gate ledger record failed", exc_info=True)
