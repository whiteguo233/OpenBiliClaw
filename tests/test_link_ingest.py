"""Tests for chat-message link ingestion (sources/link_ingest.py, issue #83)."""

from __future__ import annotations

from typing import Any

import httpx

from openbiliclaw.bilibili.api import VideoInfo
from openbiliclaw.sources.link_ingest import (
    IngestedLink,
    LinkIngestor,
    build_share_event,
    extract_urls,
    parse_html_metadata,
    render_prompt_block,
    render_relation_hint,
)

_BVID = "BV1xx411c7mD"
_XHS_NOTE_ID = "0123456789abcdef01234567"


class FakeBilibiliClient:
    """BilibiliAPIClient-shaped double returning fixed /view data."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def get_video_info(self, bvid: str) -> VideoInfo:
        self.calls.append(bvid)
        if self.fail:
            raise httpx.ConnectError("boom")
        return VideoInfo(
            bvid=bvid,
            title="讲透历史叙事",
            description="一期讲透历史叙事方法论的长视频。",
            up_name="历史实验室",
        )

    async def get_video_tags(self, bvid: str, *, limit: int = 20) -> list[str]:
        return ["历史", "人文", "方法论"][:limit]


def _html_client(routes: dict[str, httpx.Response]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in routes:
            return routes[url]
        return httpx.Response(404, content=b"not found")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
            trust_env=False,
        )

    return factory


def _html_page(title: str, *, og: dict[str, str] | None = None, description: str = "") -> bytes:
    metas = "".join(
        f'<meta property="{key}" content="{value}">' for key, value in (og or {}).items()
    )
    if description:
        metas += f'<meta name="description" content="{description}">'
    return (f"<html><head><title>{title}</title>{metas}</head><body>content</body></html>").encode()


def test_extract_urls_basic_and_dedup() -> None:
    text = (
        "看看这个 https://www.bilibili.com/video/BV1xx411c7mD 和这个 "
        "https://www.zhihu.com/question/123,还有重复的 "
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )
    urls = extract_urls(text)
    assert urls == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://www.zhihu.com/question/123",
    ]


def test_extract_urls_strips_trailing_cjk_punctuation() -> None:
    urls = extract_urls("我喜欢 https://b23.tv/abc123。")
    assert urls == ["https://b23.tv/abc123"]


def test_extract_urls_empty_when_no_link() -> None:
    assert extract_urls("今天想聊点什么呢") == []


async def test_bilibili_link_uses_api_client_and_records_share_event() -> None:
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    ingestor = LinkIngestor(bilibili_client=FakeBilibiliClient(), event_sink=sink)
    result = await ingestor.ingest(f"我就喜欢这个 https://www.bilibili.com/video/{_BVID}")

    assert len(result.links) == 1
    link = result.links[0]
    assert link.status == "ok"
    assert link.platform == "bilibili"
    assert link.content_id == _BVID
    assert link.title == "讲透历史叙事"
    assert link.author == "历史实验室"
    assert link.tags == ["历史", "人文", "方法论"]
    assert "讲透历史叙事" in result.prompt_block
    assert "UP主:历史实验室" in result.prompt_block
    assert result.relation_hint == "[分享了链接《讲透历史叙事》]"
    assert result.recorded_events == 1

    event = events[0]
    assert event["event_type"] == "share"
    assert event["source_platform"] == "bilibili"
    assert event["title"] == "讲透历史叙事"
    assert event["url"] == f"https://www.bilibili.com/video/{_BVID}"
    assert event["metadata"]["bvid"] == _BVID
    assert event["metadata"]["ingest_channel"] == "chat_link"
    assert "我就喜欢这个" in event["metadata"]["comment_text"]
    # share 默认信号强度 0.85,显式正向偏好。
    assert event["metadata"]["signal_strength"] == 0.85


async def test_b23_short_link_expands_then_uses_api_client() -> None:
    routes = {
        "https://b23.tv/abc123": httpx.Response(
            302, headers={"Location": f"https://www.bilibili.com/video/{_BVID}?spm=1"}
        ),
        f"https://www.bilibili.com/video/{_BVID}?spm=1": httpx.Response(
            200, content=b"<html></html>", headers={"Content-Type": "text/html"}
        ),
    }
    client = FakeBilibiliClient()
    ingestor = LinkIngestor(bilibili_client=client, http_client_factory=_html_client(routes))
    result = await ingestor.ingest("https://b23.tv/abc123")

    link = result.links[0]
    assert link.status == "ok"
    assert client.calls == [_BVID]
    assert link.resolved_url == f"https://www.bilibili.com/video/{_BVID}"


async def test_zhihu_link_falls_back_to_og_metadata() -> None:
    url = "https://www.zhihu.com/question/123/answer/456"
    routes = {
        url: httpx.Response(
            200,
            content=_html_page(
                "页面标题",
                og={"og:title": "如何理解历史叙事?", "og:description": "知乎问题描述"},
            ),
            headers={"Content-Type": "text/html; charset=utf-8"},
        ),
    }
    ingestor = LinkIngestor(http_client_factory=_html_client(routes))
    result = await ingestor.ingest(f"这个回答不错 {url}")

    link = result.links[0]
    assert link.status == "ok"
    assert link.platform == "zhihu"
    assert link.title == "如何理解历史叙事?"
    assert link.summary == "知乎问题描述"
    assert "[知乎]" in result.prompt_block


async def test_xhslink_short_link_records_note_id() -> None:
    landing = f"https://www.xiaohongshu.com/explore/{_XHS_NOTE_ID}?xsec_token=abc"
    routes = {
        "https://xhslink.com/a/xyz": httpx.Response(302, headers={"Location": landing}),
        landing: httpx.Response(
            200,
            content=_html_page(
                "xhs page",
                og={"og:title": "手冲咖啡入门", "og:description": "从零开始的手冲指南"},
            ),
            headers={"Content-Type": "text/html"},
        ),
    }
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    ingestor = LinkIngestor(http_client_factory=_html_client(routes), event_sink=sink)
    result = await ingestor.ingest("小红书这个 https://xhslink.com/a/xyz 我就喜欢这个")

    link = result.links[0]
    assert link.status == "ok"
    assert link.platform == "xiaohongshu"
    assert link.content_id == _XHS_NOTE_ID
    assert events[0]["metadata"]["content_id"] == _XHS_NOTE_ID
    assert events[0]["source_platform"] == "xiaohongshu"


async def test_fetch_failure_degrades_to_plain_link_without_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True, trust_env=False
        )

    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    ingestor = LinkIngestor(http_client_factory=factory, event_sink=sink)
    result = await ingestor.ingest("看看 https://www.zhihu.com/question/999 怎么样")

    link = result.links[0]
    assert link.status == "failed"
    assert link.error
    assert "内容抓取失败" in result.prompt_block
    assert result.relation_hint == ""
    assert result.recorded_events == 0
    assert events == []


async def test_non_html_response_is_rejected() -> None:
    url = "https://example.com/pic.png"
    routes = {
        url: httpx.Response(200, content=b"\x89png", headers={"Content-Type": "image/png"}),
    }
    ingestor = LinkIngestor(http_client_factory=_html_client(routes))
    result = await ingestor.ingest(url)
    assert result.links[0].status == "failed"
    assert "non-HTML" in result.links[0].error


async def test_html_body_is_capped() -> None:
    url = "https://www.zhihu.com/question/big"
    big = "<html><head><title>big</title></head><body>" + "x" * (600 * 1024) + "</body></html>"
    routes = {
        url: httpx.Response(200, content=big.encode(), headers={"Content-Type": "text/html"}),
    }
    ingestor = LinkIngestor(http_client_factory=_html_client(routes), max_html_bytes=1024)
    result = await ingestor.ingest(url)
    assert result.links[0].status == "ok"
    assert result.links[0].title == "big"


async def test_max_links_caps_processing() -> None:
    client = FakeBilibiliClient()
    ingestor = LinkIngestor(bilibili_client=client, max_links=2)
    message = " ".join(f"https://www.bilibili.com/video/BV1xx41{i}c7mD" for i in range(4))
    result = await ingestor.ingest(message)
    assert len(result.links) == 2
    assert len(client.calls) == 2


async def test_event_sink_failure_does_not_block() -> None:
    async def bad_sink(event: dict[str, Any]) -> None:
        raise RuntimeError("db down")

    ingestor = LinkIngestor(bilibili_client=FakeBilibiliClient(), event_sink=bad_sink)
    result = await ingestor.ingest(f"https://www.bilibili.com/video/{_BVID}")
    assert result.links[0].status == "ok"
    assert result.recorded_events == 0
    assert result.prompt_block  # 上下文块仍然生成


async def test_bilibili_api_failure_falls_back_gracefully() -> None:
    ingestor = LinkIngestor(bilibili_client=FakeBilibiliClient(fail=True))
    result = await ingestor.ingest(f"https://www.bilibili.com/video/{_BVID}")
    link = result.links[0]
    assert link.status == "failed"
    assert "内容抓取失败" in result.prompt_block


async def test_av_style_url_falls_back_to_html_instead_of_api() -> None:
    """b23.tv 老链接展开成 /video/av170001/ 时不能把 av 号喂给 /view API。"""
    url = "https://www.bilibili.com/video/av170001/"
    routes = {
        url: httpx.Response(
            200,
            content=_html_page("av 页面", og={"og:title": "老视频标题"}),
            headers={"Content-Type": "text/html"},
        ),
    }
    client = FakeBilibiliClient()
    ingestor = LinkIngestor(bilibili_client=client, http_client_factory=_html_client(routes))
    result = await ingestor.ingest(url)

    assert client.calls == []
    assert result.links[0].status == "ok"
    assert result.links[0].title == "老视频标题"


async def test_message_without_links_short_circuits() -> None:
    ingestor = LinkIngestor(bilibili_client=FakeBilibiliClient())
    result = await ingestor.ingest("今天心情不错")
    assert result.links == []
    assert result.prompt_block == ""
    assert result.relation_hint == ""


def test_parse_html_metadata_prefers_og_title() -> None:
    html = (
        "<html><head><title>fallback</title>"
        '<meta property="og:title" content="og 标题">'
        '<meta name="description" content="描述文字">'
        "</head></html>"
    )
    metadata = parse_html_metadata(html)
    assert metadata.title == "og 标题"
    assert metadata.description == "描述文字"


def test_build_share_event_uses_resolved_attribution() -> None:
    link = IngestedLink(
        original_url="https://b23.tv/abc",
        resolved_url=f"https://www.bilibili.com/video/{_BVID}",
        platform="bilibili",
        content_id=_BVID,
        title="标题",
        author="UP",
        status="ok",
    )
    event = build_share_event(link, "我就喜欢这个")
    assert event["content_id"] == _BVID
    assert event["source_confidence"] == "exact"
    assert event["context"].startswith("在B 站分享了")


def test_render_relation_hint_multiple_links() -> None:
    links = [
        IngestedLink(original_url="https://a.example", title="第一个", status="ok"),
        IngestedLink(original_url="https://b.example", title="第二个", status="ok"),
    ]
    assert render_relation_hint(links) == "[分享了链接《第一个》等2个]"


def test_render_prompt_block_empty_for_no_links() -> None:
    assert render_prompt_block([]) == ""
