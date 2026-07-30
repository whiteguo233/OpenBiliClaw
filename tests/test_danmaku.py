"""Tests for danmaku text condensing (P2) and its ranking bonus.

The condense strategy is driven by real measurement (see the module docstring
of ``discovery/danmaku.py``): frequency-based selection is backwards, because
the high-frequency danmaku are memes with no semantic value while the
informative ones are low-frequency and long. These tests pin that behaviour
using danmaku shapes taken from an actual sample of BV1LR336sEFX.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from openbiliclaw.discovery.danmaku import collapse_repeats, condense_danmaku
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

# ---------------------------------------------------------------------------
# Repeat collapsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("保护" * 30, "保护"),  # whole-string unit repetition
        ("好！！！！！！！！", "好！"),  # punctuation run
        ("还" + "哈" * 40, "还哈哈"),  # char run embedded in other text
        ("许愿予愿安洁丽娜，出必还愿！" * 5, "许愿予愿安洁丽娜，出必还愿！"),
        ("走走", "走走"),  # genuine double survives
        ("哈哈", "哈哈"),
        # Real-data wish-spam: a short semantic prefix + a 3-char unit ×12.
        # The prefix defeats whole-string periodicity and the unit is multi-char
        # so the single-char run rule can't see it — without _UNIT_RUN_RE the
        # spam tail survived at full length and dominated the embedding.
        (
            "许愿蕾米埃尔1+1不歪 " + "求你了" * 12,
            "许愿蕾米埃尔1+1不歪 求你了",
        ),
        ("看我看我看我", "看我"),  # embedded 2-char unit ×3
    ],
)
def test_collapse_repeats_shapes(raw: str, expected: str) -> None:
    assert collapse_repeats(raw) == expected


def test_collapse_repeats_preserves_digits() -> None:
    """Repeated digits are load-bearing — collapsing corrupts the number.

    Real battery-review danmaku contain "5000电池" and "10000mah"; an earlier
    version collapsed those to "500"/"100".
    """
    assert collapse_repeats("5000电池特别扎眼") == "5000电池特别扎眼"
    assert collapse_repeats("10000mah电池") == "10000mah电池"
    assert collapse_repeats("2026年") == "2026年"


def test_collapse_repeats_empty() -> None:
    assert collapse_repeats("") == ""
    assert collapse_repeats("   ") == ""


# ---------------------------------------------------------------------------
# Condensing — the core strategy
# ---------------------------------------------------------------------------


def test_condense_drops_high_frequency_memes() -> None:
    """The 613x/350x/310x entries in the real sample are pure noise."""
    texts = ["难说"] * 613 + ["已取餐"] * 350 + ["懂你意思"] * 310 + ["666"] * 9
    assert condense_danmaku(texts) == ""


def test_condense_prefers_long_low_frequency_content() -> None:
    """Informative danmaku are long and appear once — they must win."""
    informative = "这就是本地AI的优势，除了延迟低，还有几乎是绝对的隐私性和自由性"
    texts = ["难说"] * 100 + ["已取餐"] * 80 + [informative]
    out = condense_danmaku(texts)
    assert informative in out
    assert "难说" not in out
    assert "已取餐" not in out


def test_condense_excludes_spam_even_when_long() -> None:
    """ "保护"x30 is 60 chars but one word of meaning — must not outrank real text."""
    real = "苹果上市后系统优化导致零售机强于媒体机，这个结论挺反直觉的"
    texts = ["保护" * 30] * 5 + [real]
    out = condense_danmaku(texts)
    assert real in out
    assert "保护保护" not in out


def test_condense_drops_short_and_symbolic() -> None:
    texts = ["6", "？", "!!!", "123", "  ", "草"]
    assert condense_danmaku(texts) == ""


def test_condense_respects_max_chars() -> None:
    texts = [f"这是一条足够长的弹幕内容用于测试字数上限编号{i}" for i in range(50)]
    out = condense_danmaku(texts, max_chars=100)
    assert len(out) <= 100 + 3 * 10  # separators allowed some slack
    assert out


def test_condense_empty_inputs() -> None:
    assert condense_danmaku([]) == ""
    assert condense_danmaku(["", "   "]) == ""


def test_condense_orders_longest_first() -> None:
    short = "这条弹幕比较短一些啦"
    longer = "这条弹幕明显要长得多，包含了更多的实际内容和讨论细节信息"
    out = condense_danmaku([short, longer])
    assert out.index(longer) < out.index(short)


def test_condense_deduplicates() -> None:
    text = "这是一条有实际内容的弹幕讨论"
    out = condense_danmaku([text, text, text])
    assert out.count(text) == 1


# ---------------------------------------------------------------------------
# XML parsing (client-side)
# ---------------------------------------------------------------------------


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<i><chatserver>chat.bilibili.com</chatserver><maxlimit>3000</maxlimit>
<d p="1.5,1,25,16777215,0,0,0,0">这就是本地AI的优势</d>
<d p="2.5,1,25,16777215,0,0,0,0">难说</d>
<d p="3.5,1,25,16777215,0,0,0,0"></d>
<d p="4.5,1,25,16777215,0,0,0,0">  已取餐  </d>
</i>"""


def test_danmaku_xml_parse_shape() -> None:
    """Mirrors what get_danmaku_texts does with a real response body."""
    root = ElementTree.fromstring(_SAMPLE_XML)
    texts = [(n.text or "").strip() for n in root.iter("d")]
    texts = [t for t in texts if t]
    assert texts == ["这就是本地AI的优势", "难说", "已取餐"]


@pytest.mark.asyncio
async def test_get_danmaku_texts_rejects_bad_cid() -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    client = BilibiliAPIClient()
    try:
        assert await client.get_danmaku_texts(0) == []
        assert await client.get_danmaku_texts(-1) == []
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Storage + bonus
# ---------------------------------------------------------------------------


class _TextEmb:
    """Embedding fake: '数码'-ish text points one way, everything else another."""

    multimodal_enabled = False
    supports_image_embedding = False
    similarity_threshold = 0.82

    def __init__(self) -> None:
        self.store: dict[str, list[float]] = {}

    def _vec(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0] if ("电池" in text or "数码" in text) else [0.0, 1.0, 0.0]

    async def embed(self, text: str) -> list[float]:
        vec = self._vec(text)
        self.store[text.strip().lower()[:200]] = vec
        return vec

    def lookup_cached(self, text: str) -> list[float]:
        return list(self.store.get(text.strip().lower()[:200], []))

    def image_embedding_active(self) -> bool:
        return False


class _DummyLLM:
    async def complete_structured_task(self, **kwargs: object) -> Any:
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content="{}", provider="test", model="dummy", usage={})


def _profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="p",
        core_traits=[],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="数码", category="科技", weight=0.9)]
        ),
    )


def _seed(db: Database, bvid: str) -> None:
    db.cache_content(
        bvid=bvid,
        title="t",
        cover_url="",
        relevance_score=0.8,
        source="search",
        pool_expression="e",
        pool_topic_label="t",
        topic_group="g",
        style_key="s",
    )


def test_storage_marks_even_on_empty_result() -> None:
    """A video with no danmaku must not be re-fetched every cycle."""
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        assert [r["bvid"] for r in db.get_candidates_needing_danmaku(limit=10)] == ["BV1"]
        db.update_danmaku_text("BV1", danmaku_text="")  # empty result
        assert db.get_candidates_needing_danmaku(limit=10) == []
        assert db.get_danmaku_texts_for(["BV1"]) == {}  # empty text not returned
        db.close()


def test_storage_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        db.update_danmaku_text("BV1", danmaku_text="电池寿命衰减曲线")
        assert db.get_danmaku_texts_for(["BV1", "BVX"]) == {"BV1": "电池寿命衰减曲线"}
        db.close()


@pytest.mark.asyncio
async def test_danmaku_bonus_positive_for_on_topic() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        text = "电池寿命衰减曲线的问题"
        db.update_danmaku_text("BV1", danmaku_text=text)
        emb = _TextEmb()
        await emb.embed(text)  # simulate prewarm
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=emb,  # type: ignore[arg-type]
            danmaku_enabled=True,
        )
        cand = DiscoveredContent(bvid="BV1", title="t", relevance_score=0.8)
        bonus = await engine._danmaku_bonus_map([cand], _profile())
        assert bonus.get("BV1", 0.0) > 0.0
        db.close()


@pytest.mark.asyncio
async def test_danmaku_bonus_empty_when_disabled() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        db.update_danmaku_text("BV1", danmaku_text="电池寿命衰减曲线")
        emb = _TextEmb()
        await emb.embed("电池寿命衰减曲线")
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=emb,  # type: ignore[arg-type]
            danmaku_enabled=False,
        )
        cand = DiscoveredContent(bvid="BV1", title="t", relevance_score=0.8)
        assert await engine._danmaku_bonus_map([cand], _profile()) == {}
        db.close()


@pytest.mark.asyncio
async def test_danmaku_bonus_empty_without_stored_text() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_TextEmb(),  # type: ignore[arg-type]
            danmaku_enabled=True,
        )
        cand = DiscoveredContent(bvid="BV1", title="t", relevance_score=0.8)
        assert await engine._danmaku_bonus_map([cand], _profile()) == {}
        db.close()


@pytest.mark.asyncio
async def test_danmaku_prewarm_noops_without_client() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_TextEmb(),  # type: ignore[arg-type]
            danmaku_enabled=True,  # no bilibili_client injected
        )
        assert await engine.prewarm_pool_danmaku(limit=10) == 0
        db.close()


@pytest.mark.asyncio
async def test_danmaku_prewarm_stamps_and_embeds() -> None:
    class _FakeInfo:
        cid = 12345

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def get_video_info(self, bvid: str) -> _FakeInfo:
            return _FakeInfo()

        async def get_danmaku_texts(self, cid: int) -> list[str]:
            self.calls.append(cid)
            return ["电池寿命衰减曲线的问题讨论", "难说", "难说", "难说", "难说"]

    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed(db, "BV1")
        emb = _TextEmb()
        client = _FakeClient()
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=emb,  # type: ignore[arg-type]
            danmaku_enabled=True,
            bilibili_client=client,
        )
        processed = await engine.prewarm_pool_danmaku(limit=10)
        assert processed == 1
        assert client.calls == [12345]
        stored = db.get_danmaku_texts_for(["BV1"])
        assert "电池寿命衰减曲线的问题讨论" in stored.get("BV1", "")
        assert "难说" not in stored.get("BV1", "")  # meme filtered
        assert db.get_candidates_needing_danmaku(limit=10) == []  # idempotent
        db.close()
