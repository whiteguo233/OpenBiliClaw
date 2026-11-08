"""ProfileEvaluator — per-layer, per-field scoring of predicted vs expected profiles.

Shared evaluation core used by both human-in-the-loop (Mode 1) and
fully automated (Mode 2) self-iteration loops.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)

# Layer weights for overall score computation
_LAYER_WEIGHTS: dict[str, float] = {
    "core": 0.25,
    "values": 0.15,
    "interest": 0.30,
    "role": 0.10,
    "surface": 0.10,
    "portrait": 0.10,
}

# Field → param attribution mapping (used by optimizer)
FIELD_TO_PARAM: dict[str, str] = {
    "core.core_traits": "soul_profile_prompt",
    "core.deep_needs": "soul_profile_prompt",
    "core.mbti.type": "soul_profile_prompt",
    "core.mbti.dimensions": "soul_profile_prompt",
    "values.values": "soul_profile_prompt",
    "values.motivational_drivers": "soul_profile_prompt",
    "interest.likes": "preference_analysis_prompt",
    "interest.dislikes": "preference_analysis_prompt",
    "interest.favorite_up_users": "preference_analysis_prompt",
    "role.life_stage": "soul_profile_prompt",
    "role.current_phase": "soul_profile_prompt",
    "surface.cognitive_style": "soul_profile_prompt",
    "surface.depth_preference": "preference_analysis_prompt",
    "surface.exploration_openness": "preference_analysis_prompt",
    "portrait": "soul_profile_prompt",
}

# Field → pipeline code attribution (used by expanded optimizer)
FIELD_TO_PIPELINE: dict[str, str] = {
    "interest.dislikes": "soul/layer_updaters.py:_update_interest",
    "interest.favorite_up_users": "soul/layer_updaters.py:_update_interest",
    "surface.cognitive_style": "soul/layer_updaters.py:_update_surface",
    "surface.exploration_openness": "soul/layer_updaters.py:_update_surface",
    "role.life_stage": "soul/layer_updaters.py:_update_role",
    "role.current_phase": "soul/layer_updaters.py:_update_role",
    "values.values": "soul/layer_updaters.py:_update_values",
    "values.motivational_drivers": "soul/layer_updaters.py:_update_values",
    "core.core_traits": "soul/layer_updaters.py:_update_core",
    "core.mbti.type": "soul/layer_updaters.py:_update_core",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FieldScore:
    """Score for a single field comparison."""

    layer: str
    field: str
    score: float  # 0.0-1.0
    expected: object = ""
    predicted: object = ""
    deviation: str = ""
    severity: str = "correct"  # correct / partial / wrong / missing


@dataclass
class LayerScore:
    """Aggregate score for one onion layer."""

    layer: str
    score: float
    field_scores: list[FieldScore] = field(default_factory=list)


@dataclass
class EvalReport:
    """Complete evaluation report for one predicted profile."""

    layer_scores: list[LayerScore] = field(default_factory=list)
    overall_score: float = 0.0
    worst_fields: list[FieldScore] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    persona_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "overall_score": self.overall_score,
            "layer_scores": [
                {
                    "layer": ls.layer,
                    "score": ls.score,
                    "field_scores": [
                        {
                            "layer": fs.layer,
                            "field": fs.field,
                            "score": fs.score,
                            "deviation": fs.deviation,
                            "severity": fs.severity,
                        }
                        for fs in ls.field_scores
                    ],
                }
                for ls in self.layer_scores
            ],
            "worst_fields": [
                {"layer": f.layer, "field": f.field, "score": f.score, "deviation": f.deviation}
                for f in self.worst_fields
            ],
            "attributions": self.attributions,
            "persona_id": self.persona_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Scoring functions (per field type)
# ---------------------------------------------------------------------------

# Cache for LLM scoring results within one evaluation run
_llm_score_cache: dict[str, tuple[float, str]] = {}


async def _llm_semantic_score(
    field_name: str,
    expected: object,
    predicted: object,
    instruction: str,
) -> tuple[float, str]:
    """Use Claude Agent SDK to score semantic similarity between two values."""
    cache_key = f"{field_name}:{expected}:{predicted}"
    if cache_key in _llm_score_cache:
        return _llm_score_cache[cache_key]

    try:
        from openbiliclaw.eval.agents import _collect_text, _extract_json

        from .sponsored_agents import agent_options

        text = await _collect_text(
            prompt=(
                f"请对比以下两个值的语义相似度，返回 JSON。\n\n"
                f"字段: {field_name}\n"
                f"期望值: {expected}\n"
                f"预测值: {predicted}\n\n"
                f"{instruction}\n\n"
                '返回: {"score": 0.0-1.0, "deviation": "偏差描述"}'
            ),
            options=agent_options(
                system_prompt=(
                    "你是语义相似度评分器。对比两个值的语义含义（不是字面文本），"
                    "给出 0-1 分。完全相同概念=1.0，部分重叠=0.5-0.8，完全不同=0-0.3。"
                    "只返回 JSON。"
                ),
                max_turns=1,
            ),
        )
        data = _extract_json(text)
        score = max(0.0, min(1.0, float(data.get("score", 0.5))))
        deviation = str(data.get("deviation", ""))
        result = (score, deviation)
    except Exception:
        logger.warning("LLM scoring failed for %s, falling back", field_name)
        result = _score_string_list_fallback(
            expected if isinstance(expected, list) else [str(expected)],
            predicted if isinstance(predicted, list) else [str(predicted)],
        )

    _llm_score_cache[cache_key] = result
    return result


def _score_string_list_fallback(
    expected: list[str],
    predicted: list[str],
) -> tuple[float, str]:
    """Fallback: score two string lists using F1 of set overlap."""
    if not expected and not predicted:
        return 1.0, ""
    if not expected:
        return 0.0, f"预测了 {len(predicted)} 项，但期望为空（无中生有）"
    if not predicted:
        return 0.0, f"期望 {len(expected)} 项，但预测为空"

    exp_set = set(expected)
    pred_set = set(predicted)
    intersection = exp_set & pred_set
    precision = len(intersection) / len(pred_set) if pred_set else 0
    recall = len(intersection) / len(exp_set) if exp_set else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    missing = exp_set - pred_set
    extra = pred_set - exp_set
    parts: list[str] = []
    if missing:
        parts.append(f"缺少: {', '.join(sorted(missing))}")
    if extra:
        parts.append(f"多余: {', '.join(sorted(extra))}")
    return f1, "; ".join(parts)


def _score_string_fallback(
    expected: str,
    predicted: str,
) -> tuple[float, str]:
    """Fallback: score two strings without LLM."""
    if not expected and not predicted:
        return 1.0, ""
    if not expected or not predicted:
        return 0.0, f"期望='{expected}' 预测='{predicted}'"
    if expected.strip() == predicted.strip():
        return 1.0, ""
    if expected in predicted or predicted in expected:
        return 0.7, "部分匹配"
    return 0.3, "不匹配"


def _score_float(expected: float, predicted: float) -> tuple[float, str]:
    """Score two floats. Score = 1 - abs(diff), clamped to [0, 1]."""
    diff = abs(expected - predicted)
    score = max(0.0, 1.0 - diff)
    if diff < 0.05:
        return score, ""
    return score, f"{predicted:.2f} vs 期望 {expected:.2f} (差 {diff:.2f})"


def _score_mbti_type(expected: str, predicted: str) -> tuple[float, str]:
    """Score MBTI type by letter match ratio."""
    if not expected and not predicted:
        return 1.0, ""
    if not expected or not predicted:
        return 0.0, f"期望='{expected}' 预测='{predicted}'"
    expected = expected.upper()[:4]
    predicted = predicted.upper()[:4]
    if len(expected) != 4 or len(predicted) != 4:
        return 0.0, "MBTI 格式不完整"
    matches = sum(1 for a, b in zip(expected, predicted, strict=True) if a == b)
    score = matches / 4.0
    if matches == 4:
        return 1.0, ""
    mismatches = [f"{expected[i]}→{predicted[i]}" for i in range(4) if expected[i] != predicted[i]]
    return score, f"不匹配: {', '.join(mismatches)}"


def _score_mbti_dimensions(
    expected: dict[str, Any],
    predicted: dict[str, Any],
) -> tuple[float, str]:
    """Score MBTI dimensions by per-dimension MAE."""
    if not expected and not predicted:
        return 1.0, ""
    if not expected or not predicted:
        return 0.0, "缺少维度数据"

    dim_keys = ["E_I", "S_N", "T_F", "J_P"]
    total_diff = 0.0
    count = 0
    deviations: list[str] = []
    for key in dim_keys:
        exp_dim = expected.get(key, {})
        pred_dim = predicted.get(key, {})
        if isinstance(exp_dim, dict) and isinstance(pred_dim, dict):
            exp_s = float(exp_dim.get("strength", 0.5) or 0.5)
            pred_s = float(pred_dim.get("strength", 0.5) or 0.5)
            diff = abs(exp_s - pred_s)
            total_diff += diff
            count += 1
            if diff > 0.15:
                deviations.append(f"{key}: {pred_s:.2f} vs 期望 {exp_s:.2f}")

    if count == 0:
        return 0.5, "无可比较的维度"
    score = max(0.0, 1.0 - total_diff / count)
    return score, "; ".join(deviations)


def _interest_tree(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _score_interest_tree(
    expected: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
) -> tuple[float, str]:
    """Score interest tree by domain recall, specifics recall, and weight MAE."""
    if not expected and not predicted:
        return 1.0, ""
    if not expected:
        return 0.0, f"预测了 {len(predicted)} 个领域，但期望为空（无中生有）"
    if not predicted:
        return 0.0, f"期望 {len(expected)} 个领域，但预测为空"

    # Domain-level recall
    exp_domains = {d.get("domain", ""): d for d in expected if isinstance(d, dict)}
    pred_domains = {d.get("domain", ""): d for d in predicted if isinstance(d, dict)}

    matched_domains = set(exp_domains.keys()) & set(pred_domains.keys())
    domain_recall = len(matched_domains) / len(exp_domains) if exp_domains else 1.0

    # Specifics recall within matched domains
    specifics_scores: list[float] = []
    weight_diffs: list[float] = []
    missing_parts: list[str] = []

    for domain_name in exp_domains:
        if domain_name not in pred_domains:
            missing_parts.append(f"缺少领域: {domain_name}")
            continue
        exp_d = exp_domains[domain_name]
        pred_d = pred_domains[domain_name]

        # Weight comparison
        exp_w = float(exp_d.get("weight", 0.5) or 0.5)
        pred_w = float(pred_d.get("weight", 0.5) or 0.5)
        weight_diffs.append(abs(exp_w - pred_w))

        # Specifics comparison
        exp_specs = {
            s.get("name", ""): s for s in (exp_d.get("specifics") or []) if isinstance(s, dict)
        }
        pred_specs = {
            s.get("name", ""): s for s in (pred_d.get("specifics") or []) if isinstance(s, dict)
        }
        if exp_specs:
            spec_recall = len(set(exp_specs) & set(pred_specs)) / len(exp_specs)
            specifics_scores.append(spec_recall)
            missing_specs = set(exp_specs) - set(pred_specs)
            if missing_specs:
                missing_parts.append(f"{domain_name} 缺少: {', '.join(sorted(missing_specs))}")

    weight_score = max(0.0, 1.0 - (sum(weight_diffs) / len(weight_diffs))) if weight_diffs else 1.0
    specifics_score = sum(specifics_scores) / len(specifics_scores) if specifics_scores else 1.0

    # Weighted combination: domain recall 40%, specifics 40%, weight accuracy 20%
    score = domain_recall * 0.4 + specifics_score * 0.4 + weight_score * 0.2
    return score, "; ".join(missing_parts)


def _severity(score: float) -> str:
    if score >= 0.9:
        return "correct"
    if score >= 0.5:
        return "partial"
    if score > 0.0:
        return "wrong"
    return "missing"


# ---------------------------------------------------------------------------
# ProfileEvaluator
# ---------------------------------------------------------------------------


class ProfileEvaluator:
    """Evaluate predicted OnionProfile against expected ground truth."""

    def __init__(
        self,
        *,
        llm: Any = None,
        layer_weights: dict[str, float] | None = None,
    ) -> None:
        self._llm = llm  # For semantic similarity scoring (portrait, strings)
        self._layer_weights = layer_weights or dict(_LAYER_WEIGHTS)

    async def evaluate(
        self,
        expected: OnionProfile,
        predicted: OnionProfile,
    ) -> EvalReport:
        """Full evaluation: compare all layers and fields."""
        # Clear LLM score cache for each evaluation run
        _llm_score_cache.clear()

        layer_scores: list[LayerScore] = []

        # Core layer
        core_fields = await self._eval_core(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="core",
                score=_mean_score(core_fields),
                field_scores=core_fields,
            )
        )

        # Values layer
        values_fields = await self._eval_values(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="values",
                score=_mean_score(values_fields),
                field_scores=values_fields,
            )
        )

        # Interest layer
        interest_fields = self._eval_interest(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="interest",
                score=_mean_score(interest_fields),
                field_scores=interest_fields,
            )
        )

        # Role layer
        role_fields = await self._eval_role(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="role",
                score=_mean_score(role_fields),
                field_scores=role_fields,
            )
        )

        # Surface layer
        surface_fields = await self._eval_surface(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="surface",
                score=_mean_score(surface_fields),
                field_scores=surface_fields,
            )
        )

        # Portrait
        portrait_fields = await self._eval_portrait(expected, predicted)
        layer_scores.append(
            LayerScore(
                layer="portrait",
                score=_mean_score(portrait_fields),
                field_scores=portrait_fields,
            )
        )

        # Weighted overall score
        overall = sum(ls.score * self._layer_weights.get(ls.layer, 0.1) for ls in layer_scores)

        # Worst fields (lowest scores)
        all_fields = [f for ls in layer_scores for f in ls.field_scores]
        worst = sorted(all_fields, key=lambda f: f.score)[:5]

        # Attributions
        attributions = [
            f"{f.layer}.{f.field} (score={f.score:.2f}): {f.deviation}"
            f" → {FIELD_TO_PARAM.get(f'{f.layer}.{f.field}', 'unknown')}"
            for f in worst
            if f.score < 0.8
        ]

        return EvalReport(
            layer_scores=layer_scores,
            overall_score=round(overall, 4),
            worst_fields=worst,
            attributions=attributions,
            persona_id="",
            timestamp=datetime.now().isoformat(),
        )

    async def evaluate_with_human(
        self,
        predicted: OnionProfile,
        human_feedback: dict[str, object],
    ) -> EvalReport:
        """Build EvalReport from human per-field feedback.

        human_feedback format:
        {
            "core.core_traits": {"score": 0.7, "actual": ["理性", "好奇"], "note": "不太挑剔"},
            "core.mbti.type": {"score": 0.0, "actual": "INTP"},
            ...
        }
        """
        all_fields: list[FieldScore] = []
        for key, fb in human_feedback.items():
            if not isinstance(fb, dict):
                continue
            parts = key.split(".", 1)
            layer = parts[0]
            field_name = parts[1] if len(parts) > 1 else layer
            score = float(fb.get("score", 0.5) or 0.5)
            all_fields.append(
                FieldScore(
                    layer=layer,
                    field=field_name,
                    score=score,
                    expected=fb.get("actual", ""),
                    predicted=fb.get("predicted", ""),
                    deviation=str(fb.get("note", "")),
                    severity=_severity(score),
                )
            )

        # Group by layer
        layer_map: dict[str, list[FieldScore]] = {}
        for f in all_fields:
            layer_map.setdefault(f.layer, []).append(f)

        layer_scores = [
            LayerScore(layer=layer, score=_mean_score(fields), field_scores=fields)
            for layer, fields in layer_map.items()
        ]

        overall = sum(ls.score * self._layer_weights.get(ls.layer, 0.1) for ls in layer_scores)
        worst = sorted(all_fields, key=lambda f: f.score)[:5]
        attributions = [
            f"{f.layer}.{f.field}: {f.deviation}"
            f" → {FIELD_TO_PARAM.get(f'{f.layer}.{f.field}', 'unknown')}"
            for f in worst
            if f.score < 0.8
        ]

        return EvalReport(
            layer_scores=layer_scores,
            overall_score=round(overall, 4),
            worst_fields=worst,
            attributions=attributions,
            timestamp=datetime.now().isoformat(),
        )

    # -- Per-layer evaluation methods -----------------------------------------

    async def _eval_core(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        fields: list[FieldScore] = []

        # core_traits — LLM semantic matching
        s, d = await _llm_semantic_score(
            "core_traits",
            exp.core.core_traits,
            pred.core.core_traits,
            "这是人格特质列表。判断预测的特质是否在语义上覆盖了期望的特质。"
            "'底层逻辑导向'和'分析型思维'算部分重叠(0.6-0.7)。"
            "'理性'和'感性'才算完全不匹配(0-0.2)。",
        )
        fields.append(
            FieldScore(
                "core",
                "core_traits",
                s,
                exp.core.core_traits,
                pred.core.core_traits,
                d,
                _severity(s),
            )
        )

        # deep_needs — LLM semantic matching
        s, d = await _llm_semantic_score(
            "deep_needs",
            exp.core.deep_needs,
            pred.core.deep_needs,
            "这是深层心理需求。判断预测的需求是否在本质上与期望需求一致。"
            "'掌控感'和'认知穿透力'有语义关联(0.5-0.7)。",
        )
        fields.append(
            FieldScore(
                "core", "deep_needs", s, exp.core.deep_needs, pred.core.deep_needs, d, _severity(s)
            )
        )

        # mbti.type — exact match (no LLM needed)
        s, d = _score_mbti_type(exp.core.mbti.type, pred.core.mbti.type)
        fields.append(
            FieldScore(
                "core", "mbti.type", s, exp.core.mbti.type, pred.core.mbti.type, d, _severity(s)
            )
        )

        # mbti.dimensions — numeric (no LLM needed)
        from openbiliclaw.soul.profile import _mbti_to_dict

        exp_dims_raw = _mbti_to_dict(exp.core.mbti).get("dimensions", {})
        pred_dims_raw = _mbti_to_dict(pred.core.mbti).get("dimensions", {})
        exp_dims = exp_dims_raw if isinstance(exp_dims_raw, dict) else {}
        pred_dims = pred_dims_raw if isinstance(pred_dims_raw, dict) else {}
        s, d = _score_mbti_dimensions(exp_dims, pred_dims)
        fields.append(
            FieldScore("core", "mbti.dimensions", s, exp_dims, pred_dims, d, _severity(s))
        )

        return fields

    async def _eval_values(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        fields: list[FieldScore] = []

        s, d = await _llm_semantic_score(
            "values",
            exp.values_layer.values,
            pred.values_layer.values,
            "这是核心价值观。'真实性'和'追求真相'算高度重叠(0.8+)。"
            "'独立性'和'自主'也算重叠(0.7+)。",
        )
        fields.append(
            FieldScore(
                "values",
                "values",
                s,
                exp.values_layer.values,
                pred.values_layer.values,
                d,
                _severity(s),
            )
        )

        s, d = await _llm_semantic_score(
            "motivational_drivers",
            exp.values_layer.motivational_drivers,
            pred.values_layer.motivational_drivers,
            "这是内在动机驱动力。判断预测的驱动力是否和期望的本质相同。",
        )
        fields.append(
            FieldScore(
                "values",
                "motivational_drivers",
                s,
                exp.values_layer.motivational_drivers,
                pred.values_layer.motivational_drivers,
                d,
                _severity(s),
            )
        )

        return fields

    def _eval_interest(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        fields: list[FieldScore] = []

        from openbiliclaw.soul.profile import _interest_layer_to_dict

        exp_dict = _interest_layer_to_dict(exp.interest)
        pred_dict = _interest_layer_to_dict(pred.interest)

        # likes tree — structural matching (no LLM needed)
        s, d = _score_interest_tree(
            _interest_tree(exp_dict.get("likes")),
            _interest_tree(pred_dict.get("likes")),
        )
        fields.append(FieldScore("interest", "likes", s, None, None, d, _severity(s)))

        # dislikes tree
        s, d = _score_interest_tree(
            _interest_tree(exp_dict.get("dislikes")),
            _interest_tree(pred_dict.get("dislikes")),
        )
        fields.append(FieldScore("interest", "dislikes", s, None, None, d, _severity(s)))

        # favorite_up_users — exact match is fine for UP主 names
        s, d = _score_string_list_fallback(
            exp.interest.favorite_up_users,
            pred.interest.favorite_up_users,
        )
        fields.append(
            FieldScore(
                "interest",
                "favorite_up_users",
                s,
                exp.interest.favorite_up_users,
                pred.interest.favorite_up_users,
                d,
                _severity(s),
            )
        )

        return fields

    async def _eval_role(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        fields: list[FieldScore] = []

        s, d = await _llm_semantic_score(
            "life_stage",
            exp.role.life_stage,
            pred.role.life_stage,
            "这是生活阶段描述。'学生阶段'和'大学在读'算高度匹配(0.9)。"
            "'职场初期'和'学生'算部分匹配(0.3-0.5)。",
        )
        fields.append(
            FieldScore(
                "role", "life_stage", s, exp.role.life_stage, pred.role.life_stage, d, _severity(s)
            )
        )

        s, d = await _llm_semantic_score(
            "current_phase",
            exp.role.current_phase,
            pred.role.current_phase,
            "这是当前状态描述。判断两者描述的是否是同一种生活状态。",
        )
        fields.append(
            FieldScore(
                "role",
                "current_phase",
                s,
                exp.role.current_phase,
                pred.role.current_phase,
                d,
                _severity(s),
            )
        )

        return fields

    async def _eval_surface(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        fields: list[FieldScore] = []

        # cognitive_style — LLM semantic matching
        s, d = await _llm_semantic_score(
            "cognitive_style",
            exp.surface.cognitive_style,
            pred.surface.cognitive_style,
            "这是认知风格列表。判断预测的认知风格是否在语义上覆盖了期望的认知风格。"
            "'具象思维优先'和'偏好直观形象'算高度重叠(0.7-0.9)。"
            "'发散联想型'和'创意发散'算部分重叠(0.5-0.7)。"
            "'逻辑严密'和'直觉驱动'才算完全不匹配(0-0.2)。",
        )
        fields.append(
            FieldScore(
                "surface",
                "cognitive_style",
                s,
                exp.surface.cognitive_style,
                pred.surface.cognitive_style,
                d,
                _severity(s),
            )
        )

        s, d = _score_float(
            exp.surface.style.depth_preference,
            pred.surface.style.depth_preference,
        )
        fields.append(
            FieldScore(
                "surface",
                "depth_preference",
                s,
                exp.surface.style.depth_preference,
                pred.surface.style.depth_preference,
                d,
                _severity(s),
            )
        )

        s, d = _score_float(exp.surface.exploration_openness, pred.surface.exploration_openness)
        fields.append(
            FieldScore(
                "surface",
                "exploration_openness",
                s,
                exp.surface.exploration_openness,
                pred.surface.exploration_openness,
                d,
                _severity(s),
            )
        )

        return fields

    async def _eval_portrait(self, exp: OnionProfile, pred: OnionProfile) -> list[FieldScore]:
        """Evaluate portrait using LLM semantic comparison."""
        s, d = await _llm_semantic_score(
            "personality_portrait",
            exp.personality_portrait[:500],
            pred.personality_portrait[:500],
            "这是综合人格叙事。判断预测的画像是否抓住了期望画像的核心洞察。"
            "不要求措辞相同，但核心人格特征、认知模式和生活状态应该一致。"
            "如果核心结论一致但表达不同，给 0.7-0.9。",
        )
        return [
            FieldScore(
                "portrait",
                "personality_portrait",
                s,
                exp.personality_portrait[:100],
                pred.personality_portrait[:100],
                d,
                _severity(s),
            )
        ]


def _mean_score(fields: list[FieldScore]) -> float:
    if not fields:
        return 1.0
    return round(sum(f.score for f in fields) / len(fields), 4)
