"""营销号 (clickbait / engagement-farming) detector.

The "营销号" archetype on Chinese video platforms is the rough analogue
of clickbait content farms in English: low-effort videos optimized for
view-count over substance, identifiable from their title alone by a
narrow set of rhetorical and typographic conventions. This module
returns a 0–1 score per video plus a list of human-readable reasons.

Design notes
------------

We deliberately keep this heuristic and offline:

  * It runs inside the scoring loop on every recommendation candidate.
    A network call or LLM hop per candidate is a non-starter at that
    cadence; the existing curator scores ~50 candidates in <1 ms.
  * The output is meant to be a *demotion signal* in the curator, not
    a hard filter. Heuristics misfire — a legitimate science-news
    video can use "震惊" in earnest, a sober explainer can end with
    "?". A demotion bias lets the user still see borderline content
    if it's otherwise highly relevant.
  * The proactive-delight push path *does* use this as a hard filter
    because pushing a notification for a clickbait video is much
    worse UX than burying it under three rows of better content.

The pattern bank is not exhaustive — it's targeted at the highest-
volume offenders we've observed. Patterns that match too broadly
(e.g. plain "?" punctuation) live with low weights and only fire
when stacked with other signals, never alone.

The module is dependency-free and pure: ``score()`` is a function of
(title, description, tags) — same input, same output. Easy to unit-
test and easy to swap if we later want to plug in a learned model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "MarketingScore",
    "score_marketing_signal",
    "DEFAULT_THRESHOLD",
]


# ── Configuration constants ────────────────────────────────────────

# Score above this is considered "likely 营销号" by callers that need
# a binary decision. The curator's demote weight applies linearly to
# the score below this threshold, so this is the cutoff for hard
# filtering, not the demotion knee.
DEFAULT_THRESHOLD = 0.6

# We cap individual reason contributions at this value to keep one
# extremely matching pattern from saturating the score on its own —
# 营销号 detection is robust when multiple signals agree, and a
# single overfit pattern shouldn't be enough.
_MAX_SINGLE_CONTRIBUTION = 0.45


# ── Pattern bank ───────────────────────────────────────────────────
#
# Each entry: (regex, weight, label). Weights are tuned so that
# stacking 2-3 mid-weight signals comfortably crosses the default
# 0.6 threshold, while any single signal stays below.
#
# Patterns are matched against the lowercased title (and sometimes
# description). Chinese is case-insensitive natively but we lower()
# for the mixed-CJK-Latin case ("BREAKING NEWS!!!" is suspicious).

_TITLE_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
    # ── Sensationalist news hooks ──────────────────────────────────
    # The classic 营销号 opener. "震惊!" / "突发!" / "重磅!" by themselves
    # are aggressive enough to fire most of the threshold on their own.
    (re.compile(r"震惊[!！\s]"), 0.30, "震惊式标题"),
    (re.compile(r"^(突发|刚刚|紧急|重磅|警惕|注意了?|[【\[]速报[】\]])"), 0.28, "速报式标题"),
    (re.compile(r"^据悉"), 0.20, "据悉式标题"),
    (re.compile(r"国家(终于|出手|宣布)"), 0.25, "权威诉求"),

    # ── Mystery / curiosity-gap hooks ──────────────────────────────
    # "居然/竟然/没想到/真相/内幕/揭秘/曝光" — the curiosity-gap
    # vocabulary, very high-precision when in the leading 8 chars.
    (re.compile(r"(竟然|居然|没想到)"), 0.18, "悬念式连词"),
    (re.compile(r"(内幕|真相|揭秘|曝光|惊人秘密|不为人知)"), 0.22, "揭秘式词汇"),
    (re.compile(r"难以置信|不敢相信"), 0.20, "不可信夸张"),

    # ── Demonstrative-vague subjects ───────────────────────────────
    # "某地/某男/某女/一名男子" — deliberately anonymous referent so
    # the video can be about anything. Signature of low-substance
    # content farms.
    (re.compile(r"(某地|某男|某女|某市|某村|某省)"), 0.25, "匿名指代"),
    (re.compile(r"^(一名|一位)(男子|女子|网友|学生|老人|大爷|大妈)"), 0.18, "匿名主语开头"),

    # ── Implicit-content gates ─────────────────────────────────────
    # "看完...就懂了/学到了/收藏起来" — title doesn't tell you the
    # content, demands a click to find out.
    (re.compile(r"看完.{0,12}(就懂|就明白|秒懂)"), 0.20, "看完才知道"),
    (re.compile(r"(必须收藏|赶紧收藏|建议收藏|强烈收藏)"), 0.18, "收藏诉求"),
    (re.compile(r"(学到了|涨知识了|赚到了)[!！]?$"), 0.14, "感叹学到"),

    # ── Number-shock formats ───────────────────────────────────────
    # "3个理由让你/十大秘密" — listicle clickbait. Common, lower-
    # weight on its own because legitimate listicles use this too.
    (re.compile(r"^[0-9一二三四五六七八九十]+(个|大|条|种|招|样)[^,，。.!！?？]{2,20}(秘密|理由|真相|妙招|绝招|技巧)"),
     0.16, "数字+秘密/理由"),
    (re.compile(r"(吓人|可怕|恐怖)的(真相|事实|内幕)"), 0.25, "恐怖式真相"),

    # ── Implicit threats and warnings ──────────────────────────────
    (re.compile(r"千万(别|不要|不能)"), 0.16, "禁止式警告"),
    (re.compile(r"(99%|9成|大多数)的人都(不知道|做错|搞错)"), 0.22, "你不知道的普遍性"),
)

# Punctuation/emoji-density patterns work on the raw title (no
# lowering, no normalization) because spacing and stacking matter.
_RAW_TITLE_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
    # 3+ consecutive ! or ? (or mixed). Single ! is normal; stacking
    # is a sign of typographic shouting.
    (re.compile(r"[!！?？]{3,}"), 0.18, "连续标点"),
    # Mixed !? sequences like "??!?" or "!??!"
    (re.compile(r"[!！][?？]|[?？][!！]"), 0.10, "感叹疑问混用"),
    # Trailing ellipsis with "..." or "…" — the suspense-cliff close.
    (re.compile(r"(\.\.\.|…)\s*$"), 0.10, "悬念省略号"),
)


# Common emoji ranges. We don't try to be exhaustive — the heaviest-
# stacking emoji titles all use these blocks.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"          # misc symbols
    "\u2700-\u27BF"          # dingbats
    "]",
    flags=re.UNICODE,
)


# ── Output type ───────────────────────────────────────────────────


@dataclass
class MarketingScore:
    """Result of scoring one candidate.

    ``score`` is in [0, 1]; ``reasons`` is the user-facing breakdown
    (one entry per fired pattern, suitable for surfacing in a "why
    was this demoted?" tooltip).
    """

    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def is_likely_marketing(self) -> bool:
        return self.score >= DEFAULT_THRESHOLD


# ── Public entry point ────────────────────────────────────────────


def score_marketing_signal(
    title: str,
    *,
    description: str = "",
    tags: str | list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> MarketingScore:
    """Score (title, description, tags) for 营销号 likelihood.

    The score is a saturating sum of pattern weights — once we've
    accumulated enough signal to be confident, additional firing
    patterns add diminishing amounts. This keeps a title that hits
    six patterns from running away to a score of 1.5 (which would
    look fine after the final clamp but would compress the useful
    distinguishing range).

    ``threshold`` is recorded but not used here — it's part of the
    return value's interpretation (``is_likely_marketing``). Callers
    can pass their own threshold so the same code path can serve
    both the curator (soft demote) and the delight push (hard skip).
    """
    if not title or not title.strip():
        return MarketingScore(score=0.0)

    raw = title.strip()
    lowered = raw.lower()

    total = 0.0
    reasons: list[str] = []
    seen_labels: set[str] = set()  # de-dup so two near-identical patterns don't double-count

    # Lowered-title patterns
    for pattern, weight, label in _TITLE_PATTERNS:
        if label in seen_labels:
            continue
        if pattern.search(lowered):
            contribution = min(weight, _MAX_SINGLE_CONTRIBUTION)
            total += contribution
            reasons.append(label)
            seen_labels.add(label)

    # Raw-title (punctuation/spacing) patterns
    for pattern, weight, label in _RAW_TITLE_PATTERNS:
        if label in seen_labels:
            continue
        if pattern.search(raw):
            contribution = min(weight, _MAX_SINGLE_CONTRIBUTION)
            total += contribution
            reasons.append(label)
            seen_labels.add(label)

    # Emoji density — 3+ emojis in a single title is heavily
    # correlated with promo / engagement-farm content. Single
    # emojis are normal and ignored.
    emoji_count = len(_EMOJI_PATTERN.findall(raw))
    if emoji_count >= 3:
        # 0.12 for 3 emojis, +0.04 each up to a small cap.
        emoji_score = min(0.20, 0.12 + 0.04 * (emoji_count - 3))
        total += emoji_score
        reasons.append(f"高密度表情({emoji_count}个)")

    # Title length penalty: extremely long titles (>40 CJK chars) are
    # usually keyword-stuffed for search.  A character-count proxy is
    # fine — we don't distinguish CJK from Latin width here because
    # both kinds of long titles signal the same intent.
    if len(raw) > 40:
        excess = len(raw) - 40
        length_score = min(0.18, 0.05 + 0.005 * excess)
        total += length_score
        reasons.append(f"标题过长({len(raw)}字)")

    # Description signal: if there's a non-trivial description that
    # *contains* the entire title verbatim (or is identical to it),
    # the description was almost certainly autogenerated by stuffing
    # the title in — a common 营销号 batch-upload signature.
    desc = (description or "").strip()
    if desc and 0 < len(desc) <= 120 and raw in desc and len(desc) - len(raw) < 20:
        total += 0.10
        reasons.append("描述复读标题")

    # Stacking bonus. When three or more independent signals agree,
    # we're well past coincidence: a single legitimate use of "震惊"
    # in a serious context is one thing, but title with three
    # distinct 营销号 signatures is the canonical clickbait
    # signature, and the per-pattern weights are individually
    # conservative so that single signals don't fire alone.
    # +0.05 for the 3rd signal, +0.03 for each additional, capped.
    distinct_signals = len(reasons)
    if distinct_signals >= 3:
        stacking_bonus = min(0.20, 0.05 + 0.03 * (distinct_signals - 3))
        total += stacking_bonus
        reasons.append(f"信号叠加({distinct_signals}项)")

    # Final clamp — score is a probability-like quantity in [0, 1].
    total = min(1.0, max(0.0, total))

    return MarketingScore(score=total, reasons=reasons)
