"""Profile renderer — Markdown rendering and dual-file sync for OnionProfile."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


def render_profile_markdown(profile: OnionProfile) -> str:
    """Render an OnionProfile as human-readable Chinese markdown."""
    sections: list[str] = []

    sections.append("# 用户画像")

    # Core layer
    core_lines: list[str] = ["## 核心层 Core"]
    if profile.core.core_traits:
        core_lines.append("\n### 人格特质")
        for trait in profile.core.core_traits:
            core_lines.append(f"- {trait}")
    if profile.core.deep_needs:
        core_lines.append("\n### 深层需求")
        for need in profile.core.deep_needs:
            core_lines.append(f"- {need}")
    if profile.core.mbti.type:
        core_lines.append("\n### MBTI")
        core_lines.append(f"- 类型: {profile.core.mbti.type}")
        for key in ("E_I", "S_N", "T_F", "J_P"):
            dim = profile.core.mbti.dimensions.get(key)
            if dim:
                core_lines.append(f"- {key}: {dim.pole} (强度 {dim.strength:.2f})")
        core_lines.append(f"- 置信度: {profile.core.mbti.confidence:.2f}")
        if profile.core.mbti.inferred_from:
            core_lines.append(f"- 推断来源: {', '.join(profile.core.mbti.inferred_from)}")
    sections.append("\n".join(core_lines))

    # Values layer
    values_lines: list[str] = ["## 价值层 Values"]
    if profile.values_layer.values:
        values_lines.append("\n### 价值观")
        for v in profile.values_layer.values:
            values_lines.append(f"- {v}")
    if profile.values_layer.motivational_drivers:
        values_lines.append("\n### 动机驱动")
        for d in profile.values_layer.motivational_drivers:
            values_lines.append(f"- {d}")
    sections.append("\n".join(values_lines))

    # Interest layer
    interest_lines: list[str] = ["## 兴趣层 Interest"]
    if profile.interest.likes:
        interest_lines.append("\n### 喜好")
        for dom in profile.interest.likes:
            interest_lines.append(f"\n#### {dom.domain} (权重 {dom.weight:.2f})")
            for spec in dom.specifics:
                interest_lines.append(f"- {spec.name} ({spec.weight:.2f})")
    if profile.interest.dislikes:
        interest_lines.append("\n### 讨厌")
        for dom in profile.interest.dislikes:
            interest_lines.append(f"\n#### {dom.domain} (权重 {dom.weight:.2f})")
            for spec in dom.specifics:
                interest_lines.append(f"- {spec.name} ({spec.weight:.2f})")
    if profile.interest.favorite_up_users:
        interest_lines.append("\n### 常看UP主")
        for user in profile.interest.favorite_up_users:
            interest_lines.append(f"- {user}")
    sections.append("\n".join(interest_lines))

    # Role layer
    role_lines: list[str] = ["## 角色层 Role"]
    if profile.role.life_stage:
        role_lines.append(f"- 生活阶段: {profile.role.life_stage}")
    if profile.role.current_phase:
        role_lines.append(f"- 当前状态: {profile.role.current_phase}")
    sections.append("\n".join(role_lines))

    # Surface layer
    surface_lines: list[str] = ["## 表层 Surface"]
    if profile.surface.cognitive_style:
        surface_lines.append("\n### 认知风格")
        for s in profile.surface.cognitive_style:
            surface_lines.append(f"- {s}")
    style = profile.surface.style
    has_style = style.depth_preference != 0.5 or style.preferred_duration
    if has_style:
        surface_lines.append("\n### 内容偏好")
        if style.preferred_duration:
            surface_lines.append(f"- 时长偏好: {style.preferred_duration}")
        surface_lines.append(f"- 深度偏好: {style.depth_preference:.2f}")
        surface_lines.append(f"- 质量敏感度: {style.quality_sensitivity:.2f}")
    ctx = profile.surface.context
    has_context = ctx.weekday_patterns or ctx.weekend_patterns
    if has_context:
        surface_lines.append("\n### 情境模式")
        if ctx.weekday_patterns:
            surface_lines.append(f"- 工作日: {ctx.weekday_patterns}")
        if ctx.weekend_patterns:
            surface_lines.append(f"- 周末: {ctx.weekend_patterns}")
    surface_lines.append(f"- 探索开放度: {profile.surface.exploration_openness:.2f}")
    sections.append("\n".join(surface_lines))

    # Cross-layer narrative
    if profile.personality_portrait:
        sections.append(f"---\n\n## 综合叙事\n\n{profile.personality_portrait}")

    # Awareness & Insights
    # Both windows are chronological oldest→newest; render the newest 5.
    if profile.recent_awareness:
        awareness_lines = ["## 近期观察"]
        for note in profile.recent_awareness[-5:]:
            awareness_lines.append(f"- [{note.date}] {note.observation}")
        sections.append("\n".join(awareness_lines))

    if profile.active_insights:
        insight_lines = ["## 当前洞察"]
        for ins in profile.active_insights[-5:]:
            insight_lines.append(f"- {ins.hypothesis} (置信度: {ins.confidence:.0%})")
        sections.append("\n".join(insight_lines))

    return "\n\n".join(sections) + "\n"


def render_changelog_entry(
    *,
    timestamp: str,
    layer: str,
    changes: list[str],
    trigger: str = "",
    evidence: str = "",
) -> str:
    """Render a single changelog entry as markdown."""
    lines = [f"### {timestamp}"]
    for change in changes:
        lines.append(f"- [{layer}] {change}")
    if trigger:
        lines.append(f"  - 触发: {trigger}")
    if evidence:
        lines.append(f"  - 证据: {evidence}")
    return "\n".join(lines)


def sync_profile_files(profile: OnionProfile, data_dir: Path) -> None:
    """Write soul_profile.json and soul_profile.md as a dual-write pair."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    json_path = memory_dir / "soul_profile.json"
    md_path = memory_dir / "soul_profile.md"

    profile_dict = profile.to_dict()
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(profile_dict, f, ensure_ascii=False, indent=2)

    md_content = render_profile_markdown(profile)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.debug("Synced profile files: %s, %s", json_path, md_path)


def append_changelog(entry: str, data_dir: Path) -> None:
    """Append a changelog entry to soul_changelog.md."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    changelog_path = memory_dir / "soul_changelog.md"

    header = "# 画像更新日志\n\n"
    if not changelog_path.exists():
        with open(changelog_path, "w", encoding="utf-8") as f:
            f.write(header)

    with open(changelog_path, "a", encoding="utf-8") as f:
        f.write("\n" + entry + "\n")

    logger.debug("Appended changelog entry to %s", changelog_path)
