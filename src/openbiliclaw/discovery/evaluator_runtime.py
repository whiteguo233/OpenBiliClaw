"""Application factories for the opt-in full Jev evaluator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.config import validate_director_routes
from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.decision.runtime import LoopLocalDecisionProvider
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator

if TYPE_CHECKING:
    from openbiliclaw.config import Config


def build_cli_jev_evaluator(config: Config) -> JevContentEvaluator | None:
    """Construct only the selected route; each CLI call closes its own transport."""
    if getattr(getattr(config, "discovery", None), "eval_scorer", "llm") != "jev":
        return None
    validate_director_routes(config)
    return JevContentEvaluator(
        LoopLocalDecisionProvider(config.decision),
        model=config.decision.providers.jev.model,
        config=config.discovery.jev_evaluation,
        context_budget=resolve_jev_context(
            config.decision.providers.jev.model,
            config.decision.providers.jev.context_limits,
        ),
    )
