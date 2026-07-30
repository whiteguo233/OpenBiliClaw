"""Danmaku (弹幕) text condensing for semantic enrichment.

Bilibili videos carry semantics that ``title`` + ``description`` miss entirely:
the description is often "求三连" boilerplate, and ``body_text`` is empty on
the Bilibili path (only X / Zhihu / Reddit / Bangumi populate it). Danmaku is
what the audience is actually discussing.

Selection strategy is driven by real measurement, not intuition. Sampling
BV1LR336sEFX (3600 danmaku) showed that **frequency-based selection is exactly
backwards**:

    613x  难说
    350x  已取餐
    310x  懂你意思
      9x  666

The high-frequency entries are community memes with ~zero semantic value. The
genuinely informative danmaku are **low-frequency and long**, each appearing
once:

    "这就是本地AI的优势，除了延迟低，还有几乎是绝对的隐私性和自由性"
    "苹果上市后系统优化导致零售机强于媒体机"

So taking the top-N by frequency would filter out precisely the useful signal
and keep only noise.

Length alone is not enough either — spam inflates it. The same sample had
``保护`` repeated 30 times (76 chars, one word of meaning) and
``许愿予愿安洁丽娜，出必还愿！`` repeated 5 times, which naive
length-sorting promotes straight to the top.

Hence: **collapse repeats first, then filter memes, then rank by the
collapsed length.**

Pure functions, no I/O — the caller fetches and persists.
"""

from __future__ import annotations

import re
from collections import Counter

# Danmaku appearing more often than this are treated as memes/catchphrases
# regardless of length. Real content danmaku are near-unique (the informative
# ones in the sample all appeared exactly once).
_MAX_FREQUENCY = 3

# Minimum length AFTER repeat-collapsing. Below this a danmaku carries no
# usable semantics (the sample's mean was 7.2 chars, dominated by memes).
_MIN_COLLAPSED_LENGTH = 6

# High-frequency community memes and reaction noise. These carry no
# information about what the video is about. Matched after normalisation
# (whitespace-stripped, repeats collapsed) as a whole string.
_STOPWORD_DANMAKU: frozenset[str] = frozenset(
    {
        "难说",
        "已取餐",
        "懂你意思",
        "遥遥领先",
        "前排",
        "沙发",
        "打卡",
        "火钳刘明",
        "awsl",
        "666",
        "牛逼",
        "牛批",
        "爆了",
        "爽",
        "保护",
        "哈",
        "草",
        "顶",
        "支持",
        "第一",
        "来了",
        "签到",
        "早",
        "泪目",
        "破防",
        "笑死",
        "绝了",
        "好家伙",
        "蚌埠住了",
        "典",
        "有一说一",
        "不明觉厉",
        "мама",
        "awa",
        "qwq",
        "yyds",
    }
)

# Characters that carry no semantics on their own — a danmaku made only of
# these is dropped.
_NON_SEMANTIC_RE = re.compile(r"^[\W\d_]+$", re.UNICODE)

# Repeated punctuation runs ("！！！！！！") collapse to a single mark.
_PUNCT_RUN_RE = re.compile(r"([^\w\s])\1{1,}", re.UNICODE)

# Any NON-DIGIT character repeated 3+ times in a run collapses to two.
# Catches the "还哈哈哈哈…哈" and "A啊啊啊啊…QAQ" shapes the whole-string
# periodicity check misses, because there the repeated run is embedded in
# other text rather than constituting the entire danmaku. Two copies are kept
# so genuine doubles (e.g. 走走) survive.
#
# Digits are exempt: repeated digits are load-bearing ("5000电池", "10000mah"
# — both appear in real battery-review danmaku), and collapsing them silently
# corrupts the number.
_CHAR_RUN_RE = re.compile(r"(\D)\1{2,}", re.UNICODE)

# A short unit (1-6 chars) repeated 3+ times ANYWHERE in the string collapses
# to one copy. This is the multi-char generalisation of _CHAR_RUN_RE, needed
# for the real-data wish-spam shape that defeated the original collapse:
# ``许愿蕾米埃尔1+1不歪 求你了求你了…求你了`` (a 3-char wish suffix ×12 behind a
# short semantic prefix). _CHAR_RUN_RE can't see it ("求你了" is not one char),
# and _collapse_repeated_unit can't see it (the prefix breaks whole-string
# periodicity), so the spam tail survived at full length, ranked high by
# collapsed length, and polluted the embedding with "求你了"×12.
#
# Two guards, both learned from real data:
#  * ``(?=\D)`` — the unit must START on a non-digit. Repeated digits are
#    load-bearing ("5000电池", "10000mah", "2026年"); without this guard the
#    regex collapsed the "000" inside "5000" to "0", corrupting the number
#    (the same class of bug _CHAR_RUN_RE's digit exemption exists for).
#  * ``.{1,6}?`` lazy — pick the SMALLEST repeating unit so "求你了"×12 collapses
#    to one "求你了", not to a 6-char double ("求你了求你了") that a greedy
#    quantifier leaves behind. Unit length is capped at 6: spam units are short
#    (保护×2, 求你了×3, 已取餐×3), and a higher cap risks collapsing legitimate
#    varied text. Run AFTER _CHAR_RUN_RE so genuine doubles it preserves
#    (走走, 哈哈 — only 2 copies, below the 3+ threshold here) are untouched.
_UNIT_RUN_RE = re.compile(r"(?=\D)(.{1,6}?)\1{2,}", re.UNICODE)

# Whitespace runs collapse to one space.
_SPACE_RUN_RE = re.compile(r"\s+")


def _collapse_repeated_unit(text: str) -> str:
    """Collapse a string built by repeating one short unit.

    ``保护保护保护…`` (30x) becomes ``保护``; ``许愿…出必还愿！`` repeated
    5 times becomes one copy. Only whole-string repetition is collapsed, so a
    sentence that merely contains a repeated word is untouched.
    """
    n = len(text)
    if n < 4:
        return text
    # A repeated string has a period p that divides its length; the smallest
    # such p is found via the classic "does it occur in (s+s)[1:-1]" trick,
    # generalised here to return the unit itself.
    for unit_len in range(1, n // 2 + 1):
        if n % unit_len:
            continue
        unit = text[:unit_len]
        if unit * (n // unit_len) == text:
            return unit
    return text


def collapse_repeats(text: str) -> str:
    """Normalise a danmaku for length-based ranking.

    Collapses repeated punctuation runs, whitespace runs, and whole-string
    unit repetition, so spam cannot inflate its apparent information content.
    """
    normalized = _SPACE_RUN_RE.sub(" ", (text or "").strip())
    if not normalized:
        return ""
    normalized = _PUNCT_RUN_RE.sub(r"\1", normalized)
    normalized = _CHAR_RUN_RE.sub(r"\1\1", normalized)
    normalized = _UNIT_RUN_RE.sub(r"\1", normalized)
    return _collapse_repeated_unit(normalized).strip()


def _is_meme(collapsed: str) -> bool:
    lowered = collapsed.lower()
    if lowered in _STOPWORD_DANMAKU:
        return True
    # A stopword padded with punctuation is still a stopword ("666！", "草…").
    stripped = re.sub(r"[\W_]+$", "", lowered)
    return bool(stripped) and stripped in _STOPWORD_DANMAKU


def condense_danmaku(
    texts: list[str],
    *,
    max_chars: int = 500,
    top_n: int = 40,
) -> str:
    """Condense raw danmaku into a semantic summary string.

    Ranks by **collapsed length**, not frequency — see the module docstring for
    the measurement that drove this. Entries appearing more than
    ``_MAX_FREQUENCY`` times are dropped as memes even when long.

    Returns ``""`` when nothing survives, so the caller can skip writing an
    empty value into the cache (never cache a failed/empty result).
    """
    if not texts:
        return ""

    collapsed_by_raw: dict[str, str] = {}
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        if text not in collapsed_by_raw:
            collapsed_by_raw[text] = collapse_repeats(text)

    # Frequency is computed on the COLLAPSED form so that spam variants
    # ("保护"x30 and "保护"x50) count as the same meme.
    frequency: Counter[str] = Counter()
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        collapsed = collapsed_by_raw.get(text, "")
        if collapsed:
            frequency[collapsed] += 1

    candidates: list[str] = []
    seen: set[str] = set()
    for collapsed in collapsed_by_raw.values():
        if not collapsed or collapsed in seen:
            continue
        seen.add(collapsed)
        if len(collapsed) < _MIN_COLLAPSED_LENGTH:
            continue
        if _NON_SEMANTIC_RE.match(collapsed):
            continue
        if _is_meme(collapsed):
            continue
        if frequency[collapsed] > _MAX_FREQUENCY:
            continue
        candidates.append(collapsed)

    if not candidates:
        return ""

    # Longest first: length (after collapsing) is the best available proxy for
    # information content, per the sampled distribution.
    candidates.sort(key=lambda s: (-len(s), s))

    picked: list[str] = []
    total = 0
    limit = max(1, int(max_chars))
    for item in candidates[: max(1, int(top_n))]:
        if total + len(item) > limit:
            continue
        picked.append(item)
        total += len(item)
        if total >= limit:
            break

    # " | " rather than " ": individual danmaku frequently contain spaces
    # themselves, and an explicit separator keeps the boundaries readable in
    # logs and stable as one embedding input.
    return " | ".join(picked)
