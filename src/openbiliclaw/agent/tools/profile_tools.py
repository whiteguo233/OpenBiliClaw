"""Profile reading tools (M3).

``get_profile`` surfaces the effective onion-model soul profile
(AI profile ⊕ user overrides) via ``SoulEngine.get_profile()``, rendered with
the same markdown renderer the on-disk profile mirror uses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .common import maybe_await, require_component
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)


def build_profile_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the profile-domain tools bound to ``ctx``."""
    return [
        Tool(
            name="get_profile",
            description=(
                "获取当前用户画像（洋葱模型：人格画像、核心价值观、兴趣树、厌恶项，"
                "含用户手动修改的生效版本）。当对话涉及用户的口味、偏好、性格时调用。"
            ),
            permission_level="read",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _get_profile(ctx, args),
        ),
    ]


async def _get_profile(ctx: AgentToolContext, _args: dict[str, Any]) -> str:
    engine = require_component(ctx.soul_engine, "soul_engine")
    try:
        profile = await maybe_await(engine.get_profile())
    except Exception as exc:
        # SoulProfileNotInitializedError is a normal pre-init state, not a fault.
        if type(exc).__name__ == "SoulProfileNotInitializedError":
            return "用户画像尚未初始化（首次构建通常需要几分钟），暂无画像可读。"
        raise
    from openbiliclaw.soul.profile_renderer import render_profile_markdown

    return render_profile_markdown(profile)
