"""Human feedback collection and optimization application.

Shared by run_init_eval.py and run_update_eval.py to close the
human-in-the-loop optimization cycle:
  display profile → collect per-layer feedback → optimizer → apply → validate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Layer definitions for interactive feedback
EVAL_LAYERS = [
    ("core", "核心层 Core", "core_traits / deep_needs / mbti"),
    ("values_layer", "价值层 Values", "values / motivational_drivers"),
    ("interest", "兴趣层 Interest", "likes tree / dislikes / favorite_up_users"),
    ("role", "角色层 Role", "life_stage / current_phase"),
    ("surface", "表层 Surface", "cognitive_style / depth_preference / exploration_openness"),
    ("portrait", "综合叙事", "personality_portrait"),
]

SCORE_MAP = {"1": 1.0, "2": 0.7, "3": 0.3}


def collect_human_feedback() -> dict[str, Any] | None:
    """Interactively collect per-layer feedback from the user.

    Returns None if user chooses to skip optimization.
    Returns dict with layer scores and deviations.
    """
    print("\n" + "=" * 60)
    print("逐层评测 — 请为每层打分")
    print("  1 = 准确  2 = 部分准确  3 = 不准确  s = 跳过优化")
    print("=" * 60)

    feedback_layers: list[dict[str, Any]] = []

    for key, label, fields in EVAL_LAYERS:
        print(f"\n━━━ {label} ━━━")
        print(f"  包含: {fields}")

        while True:
            raw = input("  评分 (1/2/3/s): ").strip().lower()
            if raw == "s":
                print("\n  跳过优化。")
                return None
            if raw in SCORE_MAP:
                break
            print("  请输入 1、2、3 或 s")

        score = SCORE_MAP[raw]
        deviation = ""
        if score < 1.0:
            deviation = input("  哪里不对？简要描述: ").strip()

        feedback_layers.append(
            {
                "layer": key,
                "label": label,
                "score": score,
                "deviation": deviation,
            }
        )

    overall = sum(f["score"] for f in feedback_layers) / len(feedback_layers)
    print(f"\n  总分: {overall:.2f}")

    return {
        "layers": feedback_layers,
        "overall_score": overall,
    }


def feedback_to_optimizer_report(
    feedback: dict[str, Any],
    *,
    task: str = "init",
) -> dict[str, Any]:
    """Convert human feedback to the format expected by run_optimizer_agent()."""
    from openbiliclaw.eval.evaluator import FIELD_TO_PIPELINE
    from openbiliclaw.eval.optimizer import MODIFIABLE_FILES

    worst_fields: list[dict[str, Any]] = []
    for layer_fb in feedback["layers"]:
        if layer_fb["score"] >= 1.0:
            continue
        layer_key = layer_fb["layer"]
        # Map layer to representative fields
        field_map = {
            "core": ["core_traits", "mbti"],
            "values_layer": ["values", "motivational_drivers"],
            "interest": ["likes", "dislikes"],
            "role": ["life_stage", "current_phase"],
            "surface": ["cognitive_style", "depth_preference"],
            "portrait": ["personality_portrait"],
        }
        for field in field_map.get(layer_key, [layer_key]):
            worst_fields.append(
                {
                    "layer": layer_key.replace("_layer", ""),
                    "field": field,
                    "score": layer_fb["score"],
                    "deviation": layer_fb["deviation"] or f"{layer_fb['label']}不准确",
                }
            )

    # Sort by score ascending (worst first)
    worst_fields.sort(key=lambda f: f["score"])

    return {
        "task": task,
        "source": "human_feedback",
        "train_mean": feedback["overall_score"],
        "worst_fields": worst_fields[:5],
        "action": "EXPLOIT",
        "pipeline_hints": {
            f"{f['layer']}.{f['field']}": FIELD_TO_PIPELINE.get(f"{f['layer']}.{f['field']}", "")
            for f in worst_fields
            if FIELD_TO_PIPELINE.get(f"{f['layer']}.{f['field']}")
        },
        "modifiable_files": MODIFIABLE_FILES,
    }


async def run_optimization_cycle(
    feedback: dict[str, Any],
    *,
    project_root: Path,
    task: str = "init",
    run_logger: Any = None,
) -> dict[str, Any]:
    """Run the full optimization cycle: feedback → optimizer → apply → validate.

    Returns a summary dict of what happened.
    """
    from openbiliclaw.eval.agents import run_optimizer_agent
    from openbiliclaw.eval.optimizer import ParamChange, PromptOptimizer

    report = feedback_to_optimizer_report(feedback, task=task)

    if not report["worst_fields"]:
        print("\n  所有层评分满分，无需优化。")
        return {"optimized": False, "reason": "all_perfect"}

    print("\n" + "=" * 60)
    print("运行 Optimizer Agent...")
    print(
        f"  最大偏差: {report['worst_fields'][0]['layer']}.{report['worst_fields'][0]['field']}"
        f" ({report['worst_fields'][0]['score']:.1f})"
    )
    print("=" * 60)

    # Log input
    if run_logger:
        opt_step = run_logger.step("optimizer")
        opt_step.save_json("human_feedback.json", feedback)
        opt_step.save_json("optimizer_input.json", report)

    # Run optimizer agent
    optimization = await run_optimizer_agent(report, project_root)
    raw_changes = optimization.get("changes", [])
    summary = optimization.get("summary", "无建议")

    if run_logger:
        opt_step.save_json("optimizer_output.json", optimization)

    print(f"\n  建议: {summary[:100]}")
    print(f"  修改数: {len(raw_changes)}")

    if not raw_changes:
        print("  Optimizer 未提出修改。")
        return {"optimized": False, "reason": "no_changes", "summary": summary}

    # Show proposed changes and ask for confirmation
    for i, c in enumerate(raw_changes, 1):
        print(f"\n  修改 {i}:")
        print(f"    文件: {c.get('file_path', '?')}")
        print(f"    原因: {c.get('reason', '?')}")
        old = str(c.get("old_text", ""))[:60]
        new = str(c.get("new_text", ""))[:60]
        print(f"    旧: {old}...")
        print(f"    新: {new}...")

    confirm = input("\n  应用这些修改？(y/n): ").strip().lower()
    if confirm != "y":
        print("  已取消。")
        return {"optimized": False, "reason": "user_cancelled", "summary": summary}

    # Convert and apply
    optimizer = PromptOptimizer(project_root=project_root)
    param_changes = [
        ParamChange(
            param_name=str(c.get("file_path", "")),
            change_type="prompt",
            old_value=str(c.get("old_text", "")),
            new_value=str(c.get("new_text", "")),
            description=str(c.get("reason", "")),
            file_path=str(c.get("file_path", "")),
        )
        for c in raw_changes
        if isinstance(c, dict) and c.get("old_text") and c.get("new_text")
    ]

    applied_count = optimizer.apply(param_changes)
    print(f"\n  应用了 {applied_count}/{len(param_changes)} 处修改")

    if applied_count == 0:
        print("  没有匹配到可修改的内容。")
        return {"optimized": False, "reason": "no_match", "summary": summary}

    # Pytest validation
    if optimizer.has_pipeline_changes():
        print("  检测到 pipeline 代码修改，运行 pytest...")
        passed, test_output = optimizer.validate_with_tests()
        if not passed:
            optimizer.rollback()
            print(f"  ❌ 测试失败，已回滚: {test_output[:100]}")
            return {"optimized": False, "reason": "test_failed", "summary": summary}
        print("  ✅ 测试通过")
    else:
        print("  仅修改 prompt 模板，跳过 pytest")

    optimizer.commit()
    print(f"\n  ✅ 修改已提交 ({applied_count} 处)")

    if run_logger:
        opt_step.save_json(
            "applied_changes.json",
            [{"file": c.file_path, "desc": c.description} for c in param_changes],
        )

    return {
        "optimized": True,
        "applied_count": applied_count,
        "summary": summary,
        "changes": [c.description for c in param_changes],
    }
