"""Tests for the 营销号 (clickbait) heuristic.

The pattern bank in ``marketing_filter`` is the kind of code that
silently regresses — a tweak intended to catch one new pattern can
break the calibration of others. The cases below pin the current
behaviour: titles drawn from real-world examples (paraphrased to
avoid quoting specific videos) cluster into "should fire" (score
above the threshold) and "should not fire" (legitimate titles that
happen to share surface features).

A few cases sit deliberately near the threshold so that future tuning
work has a visible feedback signal — if score thresholds get
re-calibrated, these are the ones that should move.
"""

from __future__ import annotations

import pytest

from openbiliclaw.recommendation.marketing_filter import (
    DEFAULT_THRESHOLD,
    MarketingScore,
    score_marketing_signal,
)

# ── High-confidence positives (well above threshold) ───────────────


@pytest.mark.parametrize(
    "title",
    [
        # Sensationalist + curiosity-gap + 99% — three signals
        "震惊！99%的人都不知道的内幕真相，看完你就懂了！！！",
        # Anonymous referent + emoji density + stacked punctuation
        "某地一名男子做了这件事😱😱😱 网友：太可怕了！！！",
        # Speed-news + national-authority + threat
        "突发！国家终于出手，千万别再做这件事了！",
        # Listicle clickbait + curiosity-gap + ellipsis
        "10个让你后悔到现在的真相，第三个我居然...",
    ],
)
def test_high_confidence_positives(title: str) -> None:
    result = score_marketing_signal(title)
    assert result.is_likely_marketing, (
        f"expected high score for {title!r}, got {result.score:.2f} "
        f"(reasons: {result.reasons})"
    )
    # We also want at least 2 distinct reasons firing, otherwise the
    # threshold was crossed by a single saturated pattern (fragile).
    assert len(result.reasons) >= 2, (
        f"only {len(result.reasons)} reasons for {title!r}: {result.reasons}"
    )


# ── High-confidence negatives (well below threshold) ───────────────


@pytest.mark.parametrize(
    "title",
    [
        # Plain technical content
        "用 Rust 实现一个简单的 HTTP 服务器（第 1 部分）",
        # Educational video with a sober title
        "线性代数：矩阵的特征值与特征向量",
        # Cooking video
        "家常红烧排骨的做法",
        # Music cover
        "翻唱《海阔天空》— 钢琴版",
        # Sports highlights
        "2024 年 NBA 季后赛精彩集锦",
        # Question marks are OK in moderation
        "学习编程，应该从哪门语言开始？",
    ],
)
def test_high_confidence_negatives(title: str) -> None:
    result = score_marketing_signal(title)
    assert not result.is_likely_marketing, (
        f"unexpected high score for {title!r}: {result.score:.2f} "
        f"(reasons: {result.reasons})"
    )
    # And for the cleanest cases, the score should be small.
    assert result.score < 0.25, (
        f"score for clean title {title!r} should be near zero, "
        f"got {result.score:.2f}"
    )


# ── Component checks ──────────────────────────────────────────────


def test_empty_title_scores_zero() -> None:
    assert score_marketing_signal("").score == 0.0
    assert score_marketing_signal("   ").score == 0.0


def test_single_signal_stays_below_threshold() -> None:
    """One pattern firing should not by itself cross 0.6.

    This is the core "stacking confidence" invariant — a legitimate
    science explainer that uses "震惊" once shouldn't be filtered.
    Each pattern's weight is bounded so the user needs multiple
    independent signals before we act.
    """
    cases = [
        "震惊！",                       # sensationalist only
        "据悉今日有变化",               # 据悉 only
        "学到了！",                     # 学到 only
        "千万别错过",                   # 千万别 only
    ]
    for title in cases:
        s = score_marketing_signal(title)
        assert s.score < DEFAULT_THRESHOLD, (
            f"single signal {title!r} crossed threshold: {s.score:.2f} "
            f"(reasons: {s.reasons})"
        )


def test_description_repeats_title_signal() -> None:
    """When the description is just the title verbatim, +0.10."""
    title = "今天来聊点轻松的话题"
    plain = score_marketing_signal(title)
    repeated = score_marketing_signal(title, description=title)
    assert repeated.score > plain.score
    assert "描述复读标题" in repeated.reasons


def test_long_title_penalty() -> None:
    """Titles over 30 chars add a small penalty proportional to excess."""
    short = score_marketing_signal("A short title")
    long_title = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的标题"
    assert len(long_title) > 40
    long_result = score_marketing_signal(long_title)
    assert long_result.score > short.score
    assert any("标题过长" in r for r in long_result.reasons)


def test_emoji_density_scales() -> None:
    """3+ emoji is the trigger; more emojis = a slightly higher penalty,
    capped so a 20-emoji title doesn't blow up the score."""
    two_emoji = score_marketing_signal("回家了 😊😄")  # below threshold
    three_emoji = score_marketing_signal("回家了 😊😄😍")  # fires
    twenty_emoji = score_marketing_signal("回家了 " + "😊" * 20)
    assert all("高密度表情" not in r for r in two_emoji.reasons)
    assert any("高密度表情" in r for r in three_emoji.reasons)
    # cap: 20 emojis should not score 5x the 3-emoji case
    assert twenty_emoji.score <= three_emoji.score + 0.20


def test_threshold_parameter_does_not_affect_score() -> None:
    """The threshold is interpretation, not computation. Passing a
    custom threshold doesn't change the numeric score."""
    title = "震惊！某地居然发生了这种事！！！"
    default = score_marketing_signal(title)
    high = score_marketing_signal(title, threshold=0.9)
    low = score_marketing_signal(title, threshold=0.1)
    assert default.score == high.score == low.score


def test_score_is_bounded() -> None:
    """Score is always in [0, 1]. We construct a maximally-firing
    title and verify the final clamp holds."""
    title = (
        "震惊！突发！重磅！据悉某地一名男子的内幕真相，"
        "千万别错过！99%的人都不知道！！！😱😱😱😱😱😱"
    )
    result = score_marketing_signal(title)
    assert 0.0 <= result.score <= 1.0


def test_returns_dataclass_instance() -> None:
    """The public surface is a typed MarketingScore, not a tuple
    or a raw dict, so callers can rely on attribute access."""
    result = score_marketing_signal("震惊！")
    assert isinstance(result, MarketingScore)
    assert hasattr(result, "score")
    assert hasattr(result, "reasons")
    assert hasattr(result, "is_likely_marketing")


def test_duplicate_reasons_deduplicated() -> None:
    """If a title text triggers two near-identical patterns sharing
    a label, we only count once. (Defensive — none of the current
    patterns share labels, but future additions might.)"""
    # We can't easily trigger this without modifying the pattern bank,
    # so we verify the implementation's contract by inspection: each
    # reason appears at most once in the output list.
    title = "震惊！震惊！震惊！9成的人都不知道的真相！！！"
    result = score_marketing_signal(title)
    assert len(set(result.reasons)) == len(result.reasons), (
        f"duplicate reasons in {result.reasons}"
    )


# ── Reason surfacing ──────────────────────────────────────────────


def test_reasons_are_human_readable() -> None:
    """Reasons should be Chinese-language labels suitable for a
    "why was this filtered?" tooltip — not regex source or raw
    pattern indices."""
    title = "震惊！9成人都不知道的真相！！！"
    result = score_marketing_signal(title)
    assert len(result.reasons) >= 2
    # All reasons should be non-empty strings, none should look like
    # implementation detail (no regex chars, no Python identifiers).
    for reason in result.reasons:
        assert isinstance(reason, str)
        assert reason.strip()
        assert "\\" not in reason  # no escape sequences leaked
        assert "re.compile" not in reason
