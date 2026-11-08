"""Pure consolidation prompt bytes shared by business and contract-only tools."""

from __future__ import annotations

import json
from typing import Any

CONSOLIDATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你是用户画像的整理器。输入是若干「嫌疑重复」的主题簇（cluster），\n"
    "分为 likes（兴趣主题）和 dislikes（避雷主题）两组。\n"
    "你要对每个簇内的主题做出裁决：哪些在用户画像里表达相同的推荐意图、\n"
    "继续并存只会重复占位（应合并），哪些能带来不同推荐结果（应保留）。\n"
    "</task>\n"
    "\n"
    "<rules>\n"
    "1. 只能输出操作（op），不能输出整理后的列表。每个操作是 merge 或 keep。\n"
    "2. merge 的 members 必须从该簇的 members 中【逐字原样复制】；普通成员可用字符串，\n"
    '   同名异类成员必须用 {"name": 原名, "category": 原分类} 精确引用。\n'
    "3. 每个簇内的每个主题，必须被 merge 或 keep 恰好覆盖一次，不能遗漏、不能重复。\n"
    "4. merge 至少 2 个 members。canonical 是合并后的代表性 item 名。优先选择能准确\n"
    "   覆盖整组的简洁旧 member；只有旧 member 都只覆盖一部分时才起具体组合名。\n"
    "   新名必须与 members 同等具体或更精确，不得为了看似完整而堆砌近义词。\n"
    "5. likes 的目标是减少画像槽位重复，不限于字典意义上的严格同义词。措辞变体、\n"
    "   同粒度且推荐/搜索结果高度重叠的标签、以及加了空泛前后缀但没有新增选择价值的\n"
    "   标签都应 merge（如「搞笑」vs「娱乐搞笑」）。\n"
    "   真正的父子兴趣若会带来不同推荐结果仍须 keep（如「篮球」vs「NBA」、\n"
    "   「AI技术」vs「AI视频技术」、「游戏」vs「手机游戏」）。\n"
    "6. dislikes 组的标准更严：只合并语义几乎相同的真同义项；【严禁向上泛化】——\n"
    "   canonical 绝不能比 members 更宽泛（如把「一个案例反复切悬念拖时长」归并成\n"
    "   「低质内容」是严重错误，会误伤大量正常内容）。拿不准时一律 keep。\n"
    "7. likes 可以按「是否重复占用同一推荐意图」从宽合并，但同样不允许把具体兴趣\n"
    "   向上合并成会改变召回范围的大类。拿不准两项能否产生不同结果时才 keep。\n"
    "8. likes 成员带 category（一级分类）。同名/近名但 category 不同且语义不同的条目\n"
    "   是【同名异义】（如 苹果(科技) vs 苹果(美食)），必须分别 keep，严禁合并。\n"
    "   只有确认它们是同一概念被误标了不同分类时才 merge；此时 merge.members 和\n"
    "   keep.member 都必须使用 {name, category}，使每个同名条目可被逐一追踪。\n"
    "9. 每个簇可带 known_distinct_pairs；这些 pair 是用户回滚或当前策略下已确认要分开\n"
    "   的成员，严禁在任何 merge 中放到一起，也无需重新裁决它们之间的关系。\n"
    "10. 输出严格 JSON，不要附带解释文本。\n"
    "11. 各变量见 user 消息：likes_clusters / dislikes_clusters（各簇带 cluster_id、\n"
    "   members、known_distinct_pairs 及权重 / category 元数据）。\n"
    "</rules>\n"
    "\n"
    "<output_schema>\n"
    "{\n"
    '  "likes": [\n'
    '    {"cluster_id": "L1", "op": "merge", "members": ["AI工具与技术", "AI工具与工程实践"],\n'
    '     "canonical": "AI工程工具链", "reason": "新 item 能同时代表工具和工程实践"},\n'
    '    {"cluster_id": "L2", "op": "keep", "name": "篮球", "reason": "NBA 是其子集而非同义"},\n'
    '    {"cluster_id": "H1", "op": "merge",\n'
    '     "members": [{"name": "苹果", "category": "科技"}, '
    '{"name": "苹果", "category": "资讯"}],\n'
    '     "canonical": "苹果公司"},\n'
    '    {"cluster_id": "H1", "op": "keep", "member": {"name": "苹果", "category": "美食"}}\n'
    "  ],\n"
    '  "dislikes": [\n'
    '    {"cluster_id": "D1", "op": "merge", "members": ["偶像团体练习室内容", "偶像练习室物料"],\n'
    '     "canonical": "偶像练习室物料"}\n'
    "  ]\n"
    "}\n"
    "</output_schema>"
)


def render_consolidation_user(
    *, likes_clusters: list[dict[str, Any]], dislikes_clusters: list[dict[str, Any]]
) -> str:
    """Render the unchanged legacy message without importing model SDKs."""
    return "\n\n".join(
        [
            "<likes_clusters>",
            json.dumps(likes_clusters, ensure_ascii=False, indent=2, sort_keys=True),
            "</likes_clusters>",
            "<dislikes_clusters>",
            json.dumps(dislikes_clusters, ensure_ascii=False, indent=2, sort_keys=True),
            "</dislikes_clusters>",
        ]
    )
