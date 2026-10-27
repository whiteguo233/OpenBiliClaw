"""Preregistered synthetic fixtures for a small director behavior comparison.

These are invented Chinese titles/summaries, not real articles or user history.
Labels are authored before model runs and must never enter the model request.
The deliberately high-scoring repetitive introductions make this a stress test,
not a sample of the live pool or evidence of user satisfaction/CTR improvements.
No metric is an overall quality score, and no LLM judge is involved.

Rubric provenance:
* ``task_support`` in practical_agent is the set of concrete implementation,
  evaluation, operations or failure-analysis items (c07-c14 and c20).
* ``task_support`` in local_style_feedback is substantive AI material: the
  previous set plus the analytical c23. Generic introductions are not banned,
  but selecting only outdoor items must not look like a successful style fix.
* ``task_support`` in weak_click is all non-hype material matching either stable
  interest. Independent interest coverage is reported separately; we do not
  require a particular ratio or claim that one screen must cover both interests.
* ``forbidden_style`` is c06/c21/c22 only in local_style_feedback, whose exact
  user complaint concerns exaggerated titles. This is an evaluator preference
  label, NOT a production hard constraint and NOT a ban on the AI topic.
* ``duplicate:agent_definition`` and ``duplicate:rag_definition`` mark distinct
  titles that repeat the same introductory material, not merely a shared topic.
* ``supported_exploration`` is c20, a new route-planning application with an
  explicit connection to both established interests. More is not always better.
* ``topic:*`` measures two broad established domains. ``stable_interest_*``
  excludes the bridge card so one cross-domain item cannot manufacture two
  independent browsing options.

Weak feedback does not establish mastery, fatigue or dislike. Selection counts
cannot prove the absence of those inferences: the generated brief/uncertainties
must additionally be inspected and quoted by the reporting runner.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.recommendation.director.models import (
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    ExperiencedCard,
    FeedbackContent,
    RealFeedback,
    RecentScreen,
    ScreenAction,
    ScreenConstraints,
)
from openbiliclaw.soul.profile import (
    AwarenessNote,
    InterestTag,
    PreferenceLayer,
    SoulProfile,
    StylePreference,
)
from openbiliclaw.soul.profile_views import build_director_profile_view

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURE_VERSION = "director-effect-cases.v1"
_TIMESTAMP = 1_790_769_600.0


@dataclass(frozen=True)
class EvalCase:
    """One synthetic request and evaluator-only preregistered reference sets."""

    case_id: str
    title: str
    request: CompositionRequest
    labels: dict[str, tuple[str, ...]]


def _cards() -> tuple[CompositionCard, ...]:
    # Synthetic decoy ordering, deliberately not calibrated live ranking scores.
    rows = (
        (
            "c01",
            "Agent 到底是什么：从聊天框说起",
            "用问答、记忆和工具调用三个概念介绍 Agent；以天气查询作演示，不包含实现步骤。",
            "ai_intro",
            "概念讲解",
            100,
        ),
        (
            "c02",
            "从聊天机器人到会用工具的助手",
            "解释 Agent 与普通聊天机器人的区别，依次介绍记忆、规划和工具调用，演示查询天气。",
            "ai_intro",
            "概念讲解",
            99,
        ),
        (
            "c03",
            "一张图看懂 Agent 的组成",
            "围绕记忆、规划、工具三个模块介绍 Agent，例子是自动查询天气，定位为入门概览。",
            "ai_intro",
            "概念讲解",
            97,
        ),
        (
            "c04",
            "给大模型接上资料库，需要哪几步",
            "解释 RAG 的分块、检索和生成三个阶段，用企业知识库作概念示意，不提供参数或实验。",
            "ai_intro",
            "概念讲解",
            96,
        ),
        (
            "c05",
            "RAG 入门：资料如何变成答案",
            "按分块、检索、生成的顺序介绍知识库问答，展示流程图和企业资料查询的示意场景。",
            "ai_intro",
            "概念讲解",
            93,
        ),
        (
            "c06",
            "又一个王炸！普通人也能让 Agent 替自己工作",
            "围绕自动打开网页和填写表单的演示，强调工作方式将被彻底改变，未提供复现环境。",
            "ai_news",
            "夸张标题",
            95,
        ),
        (
            "c07",
            "一个工单助手的实现笔记",
            "从工单分类到调用内部查询接口，给出工具参数校验、人工确认与最小运行示例。",
            "ai_practice",
            "项目记录",
            86,
        ),
        (
            "c08",
            "工具调用重复执行后，我们改了什么",
            "复盘一次重试造成重复操作的案例，展示幂等键、超时状态与补偿流程的修改前后差异。",
            "ai_practice",
            "失败复盘",
            85,
        ),
        (
            "c09",
            "给问答助手做一个小型回归集",
            "从三十条真实形态的问题出发，划分可回答与应拒答样本，展示离线回归脚本和误差归类。",
            "ai_eval",
            "方法教程",
            84,
        ),
        (
            "c10",
            "同一批请求，换模型和缓存后成本怎么变",
            "使用合成请求报告输入输出 token、缓存命中与响应时间，说明不同模型组合的适用边界。",
            "ai_ops",
            "测量记录",
            83,
        ),
        (
            "c11",
            "把本地助手跑进一个容器",
            "展示配置注入、健康检查、日志采集与服务重启步骤，附最小部署文件及常见失败情况。",
            "ai_ops",
            "部署教程",
            82,
        ),
        (
            "c12",
            "一次知识库检索失准的排查记录",
            "固定问答集比较分块长度和混合检索，给出命中与漏检样例，区分检索失败和生成失败。",
            "ai_eval",
            "实验记录",
            81,
        ),
        (
            "c13",
            "从对话记录里定位助手的失败步骤",
            "用 trace 串联检索、工具调用和回答，展示脱敏字段、错误分类以及如何重放单次请求。",
            "ai_ops",
            "调试教程",
            80,
        ),
        (
            "c14",
            "让助手修改数据前，先加一道确认",
            "为写入操作设计权限边界和人工确认，比较预览、撤销与拒绝执行的具体接口。",
            "ai_practice",
            "接口设计",
            79,
        ),
        (
            "c15",
            "周末环线的路线笔记：距离之外还要看什么",
            "以虚构郊外环线演示爬升、岔路、补水点与返程安排，给出一份可独立使用的路线检查表。",
            "outdoor_route",
            "经验分享",
            88,
        ),
        (
            "c16",
            "同一条山路晴雨两天走，装备取舍有什么不同",
            "记录鞋袜、防雨层和背包防水的实际取舍，明确适用条件，不涉及危险路线或极限活动。",
            "outdoor_gear",
            "对照记录",
            87,
        ),
        (
            "c17",
            "第一次走陌生路线前，如何看懂等高线",
            "从坡度、山脊和山谷三个判断入手，用示意地图演示怎样核对步行轨迹。",
            "outdoor_route",
            "方法教程",
            78,
        ),
        (
            "c18",
            "我的日间徒步背包，哪些东西后来没再带",
            "逐项说明普通日间徒步装备的用途、重量与取舍，保留必要应急物品并说明场景限制。",
            "outdoor_gear",
            "装备记录",
            77,
        ),
        (
            "c19",
            "城市近郊散步时，我怎样记录沿途的植物",
            "分享按叶形、花期和生境整理观察笔记的方法，以认识常见植物为目的，不提供食用建议。",
            "outdoor_nature",
            "观察记录",
            76,
        ),
        (
            "c20",
            "把路线笔记做成一个可追问的小助手",
            "用个人徒步笔记搭建检索应用，展示来源引用和缺失信息提示；明确它不能代替实时路况及专业导航。",
            "ai_practice",
            "跨领域项目",
            75,
        ),
        (
            "c21",
            "这次真要变天了！一个助手顶一整个团队",
            "围绕几个自动回复和资料整理的演示展开，宣称多数工作马上不再需要人，未展示限制和评估。",
            "ai_news",
            "夸张标题",
            98,
        ),
        (
            "c22",
            "别再学旧技术了，Agent 已经改写一切",
            "汇集多个工具的演示片段，以职业焦虑和全面替代为主线，缺少可核查的实验与部署细节。",
            "ai_news",
            "夸张标题",
            94,
        ),
        (
            "c23",
            "规划、搜索与策略：怎样区分几类智能体",
            "从问题定义和反馈方式比较规划器、搜索算法与学习策略，讨论各自限制，偏分析而非上手教程。",
            "ai_theory",
            "原理分析",
            74,
        ),
        (
            "c24",
            "阴天的山路照片，怎样保留层次",
            "用同一位置的照片比较曝光、前景与色彩处理，记录徒步途中便于完成的拍摄方法。",
            "outdoor_photo",
            "方法教程",
            73,
        ),
    )
    return tuple(
        CompositionCard(
            ref=ref,
            item_key=f"synthetic-effect:{ref}",
            title=title,
            summary=summary,
            topic_group=topic,
            content_style=style,
            source_platform="synthetic",
            baseline_score=float(score),
        )
        for ref, title, summary, topic, style, score in rows
    )


def _profile() -> SoulProfile:
    """Fixed synthetic taste; no inferred fatigue, mastery or AI-topic ban."""
    return SoulProfile(
        cognitive_style=["喜欢通过具体例子理解问题"],
        current_phase="正在把一个个人 AI 助手从演示推进到可运行的工具",
        preferences=PreferenceLayer(
            interests=[
                InterestTag("AI 工程与智能体应用", "科技", weight=0.9),
                InterestTag("日间徒步与自然观察", "户外", weight=0.8),
            ],
            style=StylePreference(depth_preference=0.7),
            exploration_openness=0.4,
        ),
        recent_awareness=[
            AwarenessNote(
                date="2026-09-29",
                observation="用户明确提过想了解助手落地，也一直保留着周末徒步的兴趣。",
                note_id="synthetic-awareness-1",
            )
        ],
    )


def _history(
    case_id: str,
) -> tuple[tuple[RealFeedback, ...], tuple[RecentScreen, ...]]:
    """Persisted-shape synthetic events; never pass these off as real telemetry."""
    past_ai = FeedbackContent(
        ref="past_ai",
        title="一个资料整理助手的运行记录",
        summary="展示资料提取、工具调用和结果检查的基本流程。",
        topic_group="ai_practice",
        content_style="项目记录",
        source_platform="synthetic",
    )
    past_outdoor = FeedbackContent(
        ref="past_outdoor",
        title="近郊步道的一次日间行走记录",
        summary="介绍一条虚构步道的路线选择和沿途观察。",
        topic_group="outdoor_route",
        content_style="经验分享",
        source_platform="synthetic",
    )
    if case_id == "practical_agent":
        past_ai = past_ai.model_copy(
            update={
                "title": "Agent 入门：规划、记忆和工具",
                "summary": "通过天气查询介绍三个基本组成，没有实现细节。",
                "topic_group": "ai_intro",
                "content_style": "概念讲解",
            }
        )
    elif case_id == "local_style_feedback":
        past_ai = past_ai.model_copy(
            update={
                "title": "震撼！AI 助手即将取代所有工作",
                "summary": "若干自动化演示与全面替代的结论，缺少实验细节。",
                "topic_group": "ai_news",
                "content_style": "夸张标题",
            }
        )

    batch_ref = f"synthetic-batch-{case_id}"
    ack = RealFeedback(
        event_ref=f"synthetic-ack-{case_id}",
        batch_ref=batch_ref,
        kind="presented",
        description="这两张卡进入可见区域；没有观看时长或读完证据。",
        occurred_at=_TIMESTAMP,
        content=(past_ai, past_outdoor),
    )
    feedback = [ack]
    actions: tuple[ScreenAction, ...] = ()
    if case_id == "local_style_feedback":
        event = RealFeedback(
            event_ref="synthetic-style-dislike",
            batch_ref=batch_ref,
            kind="dislike",
            description="这个标题太夸张",
            occurred_at=_TIMESTAMP + 1,
            content=(past_ai,),
        )
        feedback.append(event)
        actions = (
            ScreenAction(
                event_ref=event.event_ref,
                kind="dislike",
                note=event.description,
                occurred_at=event.occurred_at,
            ),
        )
    elif case_id == "weak_click":
        event = RealFeedback(
            event_ref="synthetic-single-click",
            batch_ref=batch_ref,
            kind="click",
            description="点击打开了这张卡；没有点赞、读完或观看时长证据。",
            occurred_at=_TIMESTAMP + 1,
            content=(past_ai,),
        )
        feedback.append(event)
        actions = (
            ScreenAction(
                event_ref=event.event_ref,
                kind="click",
                occurred_at=event.occurred_at,
            ),
        )
    screen = RecentScreen(
        batch_ref=batch_ref,
        direction="从长期兴趣中提供两个独立的浏览入口",
        committed_at=_TIMESTAMP - 1,
        cards=(
            ExperiencedCard(
                **past_ai.model_dump(),
                position=1,
                exposure_confirmed=True,
                exposure_at=_TIMESTAMP,
                actions=actions,
            ),
            ExperiencedCard(
                **past_outdoor.model_dump(),
                position=2,
                exposure_confirmed=True,
                exposure_at=_TIMESTAMP,
            ),
        ),
    )
    return tuple(feedback), (screen,)


def build_cases() -> tuple[EvalCase, ...]:
    """Build deterministic requests with identical pool/profile, varying evidence."""
    cards = _cards()
    profile = _profile()
    practical = tuple(f"c{i:02d}" for i in range(7, 15)) + ("c20",)
    ai = tuple(f"c{i:02d}" for i in range(1, 15)) + ("c21", "c22", "c23")
    outdoors = tuple(f"c{i:02d}" for i in range(15, 20)) + ("c24",)
    hype = ("c06", "c21", "c22")
    specs = (
        (
            "practical_agent",
            "明确需求：从泛泛介绍转向 Agent 工程落地",
            "给我点真正能落地的 Agent 工程内容，这次不想再看泛泛介绍。",
            practical,
            (),
        ),
        (
            "local_style_feedback",
            "局部反馈：反感夸张标题，不等于反感 AI",
            "",
            (*practical, "c23"),
            hype,
        ),
        (
            "weak_click",
            "弱证据：一次点击与一次无操作不能改写长期兴趣",
            "",
            tuple(card.ref for card in cards if card.ref not in hype),
            (),
        ),
    )
    cases = []
    for case_id, title, intent, task_support, forbidden in specs:
        feedback, screens = _history(case_id)
        request = CompositionRequest(
            request_id=f"synthetic-request-{case_id}",
            session=CompositionSession(
                session_ref=f"synthetic-session-{case_id}",
                stream_ref=f"synthetic-stream-{case_id}",
                state_revision=len(feedback),
                feedback_cursor=feedback[-1].event_ref,
                configuration_revision=1,
                explicit_intent=intent,
                real_feedback=feedback,
                profile_context=json.loads(json.dumps(build_director_profile_view(profile))),
                fast_profile_context=json.loads(
                    json.dumps(build_director_profile_view(profile, fast=True))
                ),
                recent_screens=screens,
                latest_preference_revision=2 if case_id == "local_style_feedback" else 0,
            ),
            candidate_set_ref="synthetic-pool-v1",
            candidate_revision=1,
            feature_schema_version=FIXTURE_VERSION,
            direction_ref="synthetic-general-direction",
            direction="结合用户画像、本次要求与实际反馈，为下一屏提供有用且不过度重复的选择。",
            cards=cards,
            constraints=ScreenConstraints(version="synthetic-no-topic-quota-v1"),
            target_count=6,
            decision_budget_seconds=60.0,
        )
        cases.append(
            EvalCase(
                case_id=case_id,
                title=title,
                request=request,
                labels={
                    "task_support": tuple(task_support),
                    "forbidden_style": tuple(forbidden),
                    "supported_exploration": ("c20",),
                    "stable_interest_ai": ai,
                    "stable_interest_outdoors": outdoors,
                    "topic:ai": (*ai, "c20"),
                    "topic:outdoors": (*outdoors, "c20"),
                    "duplicate:agent_definition": ("c01", "c02", "c03"),
                    "duplicate:rag_definition": ("c04", "c05"),
                },
            )
        )
    return tuple(cases)


def score_selection(
    case: EvalCase, refs: Sequence[str], *, expected_count: int | None = None
) -> dict[str, int | float | bool]:
    """Report raw selection counts; no pooled quality score or LLM assessment.

    Unknown/duplicate IDs fail validity. Semantic counts use unique known refs
    so invalid repeats cannot inflate support. The caller must report validity
    alongside the counts and must not hide failed model outputs with a fallback.
    The default expected length is one screen. For multi-screen reference audits,
    the caller can specify a different length and must report screen shape
    separately; setting this to the actual length does not validate screen size.
    """
    available = {card.ref for card in case.request.cards}
    counts = Counter(refs)
    selected = set(refs) & available

    def count_label(label: str) -> int:
        return len(selected.intersection(case.labels.get(label, ())))

    duplicate_counts = [
        len(selected.intersection(members))
        for label, members in case.labels.items()
        if label.startswith("duplicate:")
    ]
    unknown_count = sum(count for ref, count in counts.items() if ref not in available)
    repeated_ref_count = sum(count - 1 for count in counts.values())
    target_count = case.request.target_count if expected_count is None else expected_count
    valid = len(refs) == target_count and not unknown_count and not repeated_ref_count
    return {
        "selected_count": len(refs),
        "unique_known_count": len(selected),
        "unknown_ref_count": unknown_count,
        "repeated_ref_count": repeated_ref_count,
        "valid_selection": valid,
        "task_support_count": count_label("task_support"),
        "forbidden_style_count": count_label("forbidden_style"),
        "semantic_duplicate_excess": sum(max(0, count - 1) for count in duplicate_counts),
        "semantic_duplicate_pairs": sum(count * (count - 1) // 2 for count in duplicate_counts),
        "supported_exploration_count": count_label("supported_exploration"),
        "topical_coverage": sum(
            bool(selected.intersection(members))
            for label, members in case.labels.items()
            if label.startswith("topic:")
        ),
        "independent_interest_coverage": sum(
            bool(count_label(label)) for label in ("stable_interest_ai", "stable_interest_outdoors")
        ),
        "ai_count": count_label("stable_interest_ai"),
        "outdoor_count": count_label("stable_interest_outdoors"),
    }
