"""Separate "operating this product" from "what the user likes".

A real profile accumulated thirteen dialogue-derived interests — 「系统配置与
测试」「Agent配置工具链验证」「参数化调试方法论」— produced while the user was
configuring, testing and debugging OpenBiliClaw itself, plus dislike topics
like 「测试后配置未干净还原」「口头确认代替真实工具调用」 that are complaints
about agent behaviour, not content preferences. Running a tool is not a taste;
once such a row exists it feeds discovery keywords and the portrait as if it
were content preference.

The classifier is deliberately conservative in one direction only: an
operational word alone never disqualifies a topic (「软件测试」 is a legitimate
interest for many people). A single-word match is reported for review; only a
compound operational phrase — or an operational word inside a turn that is
explicitly about this product — is treated as meta-usage.
"""

from __future__ import annotations

# Naming the product is enough to mark a conversation as "about the tool".
PRODUCT_ANCHORS: tuple[str, ...] = (
    "openbiliclaw",
    "openbili claw",
    "biliclaw",
    "本产品",
    "这个项目",
    "本项目",
    "这个工具",
    "这套工具",
    "推荐管道",
    "订阅源",
    "画像重建",
    "抓取任务",
    "config.toml",
    "api key",
    "环境变量",
    "本地服务",
    "后端服务",
    "daemon",
    "worker",
)

# Operational vocabulary. Never sufficient on its own: most of these are also
# ordinary technical interests.
OPERATIONAL_TERMS: tuple[str, ...] = (
    "配置",
    "调试",
    "部署",
    "测试",
    "排障",
    "排查",
    "日志",
    "接口",
    "安装",
    "升级",
    "回滚",
    "参数",
    "管道",
    "抓取",
    "同步",
    "初始化",
    "重建",
    "迁移",
    "复现",
    "验证",
)

# Compound phrases that are unambiguous even with a single hit. A bare 「测试」
# is a real interest (software testing); 「边界测试」 in this product's own
# vocabulary is QA of the tool.
OPERATIONAL_PHRASES: tuple[str, ...] = (
    "配置管理",
    "边界测试",
    "工具链验证",
    "参数调优",
    "调试方法",
    "灰度发布",
    "端到端验证",
)

# Dialogue sources whose rows may be meta-usage rather than taste.
DIALOGUE_SOURCES: frozenset[str] = frozenset(
    {"dialogue", "dialogue_insight", "对话洞察", "对话", "chat"}
)


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def is_product_operations_message(text: str) -> bool:
    """Whether a chat turn is about running this product rather than the user.

    Naming the product qualifies; otherwise two operational terms must appear
    together, which keeps 「我想学软件测试」 from being read as tooling.
    """
    lowered = str(text or "").casefold().strip()
    if not lowered:
        return False
    if _hits(lowered, PRODUCT_ANCHORS):
        return True
    return len(_hits(lowered, OPERATIONAL_TERMS)) >= 2


def is_dialogue_source(source: str) -> bool:
    """Whether an interest's provenance is a chat/dialogue path.

    Observed values in the wild: ``dialogue``, ``对话洞察``, ``chat feedback
    confirmed``. Matching is substring-based because producers keep inventing
    variants, and a missed variant is exactly how the tool-usage topics got in.
    """
    lowered = str(source or "").strip().casefold()
    if not lowered:
        return False
    return any(token in lowered for token in ("dialogue", "对话", "chat"))


def is_operational_topic(name: str) -> bool:
    """Whether a topic name is operational vocabulary at all (single hit)."""
    return bool(_hits(str(name or "").casefold(), OPERATIONAL_TERMS))


def looks_like_product_ops_topic(name: str) -> bool:
    """Whether a stored topic name is a compound operational phrase.

    Used by the offline repair where the original conversation is gone, so the
    evidence bar is higher: two distinct operational terms, or one unambiguous
    compound phrase. 「软件测试」 is left alone and reported for review instead
    of being archived.
    """
    lowered = str(name or "").casefold()
    if _hits(lowered, OPERATIONAL_PHRASES):
        return True
    return len(_hits(lowered, OPERATIONAL_TERMS)) >= 2


def should_drop_dialogue_candidate(content: str, *, user_message: str) -> bool:
    """Whether one dialogue candidate is an artefact of using the tool.

    Both conditions must hold: the turn is about the product, *and* the
    candidate itself is operational. A taste mentioned in the same turn (「我
    喜欢播客」) survives.
    """
    if not is_operational_topic(content):
        return False
    return is_product_operations_message(user_message)
