"""Discovery-specific prompt optimizer configuration.

Defines the parameter registry, modifiable files whitelist, and
field-to-param mapping for the content discovery pipeline.
Reuses the core PromptOptimizer from optimizer.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openbiliclaw.eval.discovery_evaluator import (
    DISCOVERY_FIELD_TO_PARAM,
    DimensionScore,
)
from openbiliclaw.eval.optimizer import (
    ContinuousParam,
    PromptOptimizer,
    PromptParam,
)

if TYPE_CHECKING:
    from pathlib import Path

_SRC = "src/openbiliclaw"

# Files the discovery optimizer is allowed to modify.
DISCOVERY_MODIFIABLE_FILES: list[str] = [
    f"{_SRC}/llm/prompts.py",
    f"{_SRC}/discovery/strategies/search.py",
    f"{_SRC}/discovery/strategies/trending.py",
    f"{_SRC}/discovery/strategies/related_chain.py",
    f"{_SRC}/discovery/strategies/explore.py",
    f"{_SRC}/discovery/engine.py",
]

DISCOVERY_PROMPT_PARAMS: list[PromptParam] = [
    PromptParam(
        name="search_queries_prompt",
        file_path=f"{_SRC}/llm/prompts.py",
        function_name="build_search_queries_prompt",
    ),
    PromptParam(
        name="content_evaluation_prompt",
        file_path=f"{_SRC}/llm/prompts.py",
        function_name="build_content_evaluation_prompt",
    ),
    PromptParam(
        name="explore_domains_prompt",
        file_path=f"{_SRC}/llm/prompts.py",
        function_name="build_explore_domains_prompt",
    ),
    PromptParam(
        name="recommendation_expression_prompt",
        file_path=f"{_SRC}/llm/prompts.py",
        function_name="build_recommendation_expression_prompt",
    ),
]

# No continuous params — thresholds and strategy code are not auto-optimized.
# Only prompt templates in prompts.py are subject to automatic optimization.
DISCOVERY_CONTINUOUS_PARAMS: list[ContinuousParam] = []


def create_discovery_optimizer(
    *,
    project_root: Path | None = None,
    llm: Any = None,
    use_agent_sdk: bool = True,
) -> PromptOptimizer:
    """Create a PromptOptimizer configured for the discovery pipeline."""
    return PromptOptimizer(
        llm=llm,
        use_agent_sdk=use_agent_sdk,
        project_root=project_root,
        continuous_params=list(DISCOVERY_CONTINUOUS_PARAMS),
        prompt_params=list(DISCOVERY_PROMPT_PARAMS),
        modifiable_files=list(DISCOVERY_MODIFIABLE_FILES),
        field_to_param=dict(DISCOVERY_FIELD_TO_PARAM),
    )


def dimension_scores_to_field_scores(
    worst_dims: list[DimensionScore],
) -> list[Any]:
    """Convert DimensionScore list to FieldScore-compatible objects for the optimizer.

    The PromptOptimizer.exploit() expects objects with .layer, .field, .score,
    .deviation attributes. We bridge DimensionScore (dimension="strategy.dim")
    into that shape.
    """
    from openbiliclaw.eval.evaluator import FieldScore

    results: list[FieldScore] = []
    for dim in worst_dims:
        parts = dim.dimension.split(".", 1)
        layer = parts[0] if len(parts) > 1 else ""
        field_name = parts[1] if len(parts) > 1 else parts[0]
        results.append(
            FieldScore(
                layer=layer,
                field=field_name,
                score=dim.score,
                expected=None,
                predicted=None,
                deviation=dim.details or f"score={dim.score:.2f}",
                severity=dim.severity,
            )
        )
    return results
