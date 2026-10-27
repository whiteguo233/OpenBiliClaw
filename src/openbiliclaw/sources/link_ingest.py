"""Chat-message link ingestion (对话内链接摄入, issue #83).

# [INPUT]: 用户聊天消息文本(含 B站/知乎/小红书链接或 b23.tv/xhslink.com 短链)
# [OUTPUT]: 链接内容摘要 prompt 块、对话历史 relation 提示、share 偏好事件
# [POS]: sources 层的对话链接预处理服务;soul/dialogue 在回复前调用,
#        Web(/api/chat*)与 CLI(openbiliclaw chat)两条入口共用
# [PROTOCOL]: 变更时更新此头部,然后检查 CLAUDE.md

用户在聊天里粘贴链接并直说「我就喜欢这个」时,本模块:

1. 从消息提取 URL,展开 b23.tv / xhslink.com 短链(跟随重定向,带超时与大小限制);
2. 按平台抓取内容摘要 —— bilibili 复用 ``BilibiliAPIClient`` 的 /view 元数据
   (标题/简介/UP 主/标签),其余平台抓取页面 title/description/og 元数据;
3. 渲染成当轮 prompt 上下文块,并把每个抓取成功的链接通过 ``event_sink``
   记录为 ``share`` 事件(显式正向偏好信号,默认信号强度 0.85,进统一兴趣线)。

任何一步失败都只影响该链接本身:抓取失败的链接以「仅链接」形态进入上下文,
事件写入失败只记日志 —— 聊天永不被链接摄入阻塞。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

import httpx

from openbiliclaw.sources.event_format import build_event
from openbiliclaw.sources.identity_keys import bvid_from_url, note_id_from_url
from openbiliclaw.sources.platforms import (
    PLATFORM_BILIBILI,
    infer_source_platform_from_url,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # 偏好事件写入路径;生产环境为 ``MemoryManager.propagate_event``。
    EventSink = Callable[[dict[str, Any]], Awaitable[None]]
    # 测试注入点;返回一个可用的 httpx.AsyncClient(可带 MockTransport)。
    HttpClientFactory = Callable[[], httpx.AsyncClient]

logger = logging.getLogger(__name__)

# 每条消息最多处理的链接数:链接摄入是聊天的附属增强,一屏链接不能把当轮
# 延迟放大到不可接受。
MAX_LINKS_PER_MESSAGE = 3
# 单页面读取上限:title/og 元数据都在 <head> 里,256 KiB 足够;正文不抓。
MAX_HTML_BYTES = 256 * 1024
# 单次抓取超时:聊天轮次对延迟敏感,宁可降级为纯文本也不挂住回复。
FETCH_TIMEOUT_SECONDS = 8.0
# 注入 prompt 的简介/摘要长度上限。
MAX_SUMMARY_CHARS = 300
# 用户消息摘录进 share 事件 comment_text 的上限(第一人称兴趣表达,
# 远小于 event_format.COMMENT_TEXT_MAX_CHARS)。
MAX_CHAT_EXCERPT_CHARS = 200
# relation 提示里的标题长度上限。
_RELATION_TITLE_CHARS = 24

# 短链域名 —— 与 sources/platforms.py 的 url_hosts 登记保持一致
# (bilibili: b23.tv;xiaohongshu: xhslink.com)。短链本身不含内容 id,
# 必须先跟随重定向拿落地 URL,再二次识别平台。
_SHORT_LINK_HOSTS = frozenset({"b23.tv", "xhslink.com"})

_URL_RE = re.compile(r"https?://[^\s<>\"'「」『』（）()【】\[\],;]+")
# 合法 BV 号:BV + 10 位Base58 字符。av 号落地页(如 b23.tv 老链接展开成
# /video/av170001/)不匹配,回退到 HTML 元数据抓取,而不是把 av 号喂给 /view API。
_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
# URL 尾部常见的句读符号不属于链接本身。
_URL_TRAILING_PUNCT = ".,;:!?、。,;:!?~"

# og/description 元数据白名单:meta name/property → 归一化字段。
_META_KEY_MAP = {
    "og:title": "title",
    "twitter:title": "title",
    "description": "description",
    "og:description": "description",
    "twitter:description": "description",
}

# prompt 块里的平台展示名;未登记的用注册表原始 family 名。
_PLATFORM_LABELS = {
    "bilibili": "B站",
    "xiaohongshu": "小红书",
    "zhihu": "知乎",
    "douyin": "抖音",
    "weibo": "微博",
}

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class SupportsVideoInfo(Protocol):
    """The slice of ``BilibiliAPIClient`` that link ingestion needs."""

    async def get_video_info(self, bvid: str) -> Any: ...

    async def get_video_tags(self, bvid: str, *, limit: int = 20) -> list[str]: ...


@dataclass
class IngestedLink:
    """One URL extracted from a chat message, with its fetch outcome."""

    original_url: str
    resolved_url: str = ""
    platform: str = ""
    content_id: str = ""
    title: str = ""
    author: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "pending"  # "ok" | "failed"
    error: str = ""


@dataclass
class LinkIngestResult:
    """Aggregate outcome of ingesting one chat message."""

    links: list[IngestedLink] = field(default_factory=list)
    prompt_block: str = ""
    relation_hint: str = ""
    recorded_events: int = 0


@dataclass
class PageMetadata:
    """Best-effort title/description extracted from an HTML page."""

    title: str = ""
    description: str = ""


def extract_urls(text: str) -> list[str]:
    """Extract http(s) URLs from chat text, deduped in order of appearance."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING_PUNCT)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower().rstrip(".")


def _is_short_link(url: str) -> bool:
    host = _host_of(url)
    return any(host == base or host.endswith(f".{base}") for base in _SHORT_LINK_HOSTS)


def _truncate(text: str, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


class _PageMetadataParser(HTMLParser):
    """Collect <title> text and whitelisted <meta> fields from a page head."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        attr_map = {name: value for name, value in attrs if value}
        key = (attr_map.get("name") or attr_map.get("property") or "").strip().lower()
        field_name = _META_KEY_MAP.get(key)
        content = (attr_map.get("content") or "").strip()
        if field_name and content:
            self.meta.setdefault(field_name, content)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def parse_html_metadata(html: str) -> PageMetadata:
    """Parse title/description/og metadata from an HTML document.

    Best-effort by design: a malformed page returns whatever was collected
    before the parser stumbled, never an exception.
    """
    parser = _PageMetadataParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        logger.debug("HTML metadata parse stopped early", exc_info=True)
    title = parser.meta.get("title") or " ".join(parser.title_parts).strip()
    return PageMetadata(
        title=_truncate(title, MAX_SUMMARY_CHARS),
        description=_truncate(parser.meta.get("description", ""), MAX_SUMMARY_CHARS),
    )


def render_prompt_block(links: list[IngestedLink]) -> str:
    """Render fetched link summaries as a context block for the chat prompt."""
    if not links:
        return ""
    lines = [
        "【用户分享的链接】(用户主动把链接发给你,是明确的兴趣表达;"
        "抓取成功的已按 share 信号记入兴趣线。回复时请体现你理解了链接内容,"
        "抓取失败的不要编造内容)"
    ]
    for index, link in enumerate(links, start=1):
        label = _PLATFORM_LABELS.get(link.platform, link.platform or "网页")
        url = link.resolved_url or link.original_url
        if link.status == "ok":
            lines.append(f"{index}. [{label}] 《{link.title}》")
            details: list[str] = []
            if link.author:
                details.append(f"作者/UP主:{link.author}")
            if link.tags:
                details.append(f"标签:{' / '.join(link.tags)}")
            if details:
                lines.append(f"   {' · '.join(details)}")
            if link.summary:
                lines.append(f"   简介:{link.summary}")
        else:
            lines.append(f"{index}. [{label}] {url}(内容抓取失败,只有链接本身)")
            continue
        lines.append(f"   链接:{url}")
    return "\n".join(lines)


def render_relation_hint(links: list[IngestedLink]) -> str:
    """Render the durable history prefix so later turns stay aware of the share."""
    titles = [link.title for link in links if link.status == "ok" and link.title]
    if not titles:
        return ""
    suffix = f"等{len(titles)}个" if len(titles) > 1 else ""
    return f"[分享了链接《{_truncate(titles[0], _RELATION_TITLE_CHARS)}》{suffix}]"


def build_share_event(link: IngestedLink, message: str) -> dict[str, Any]:
    """Build the canonical ``share`` preference event for one fetched link.

    The user's own message text rides along as ``comment_text`` — 「我就喜欢
    这个」这类第一人称表达是最强的兴趣信号,且该字段在偏好分析的 compact
    路径白名单内(``_COMPACT_METADATA_KEYS``)。
    """
    metadata: dict[str, Any] = {"ingest_channel": "chat_link"}
    if link.platform == PLATFORM_BILIBILI and link.content_id:
        metadata["bvid"] = link.content_id
    elif link.content_id:
        metadata["content_id"] = link.content_id
    excerpt = _truncate(message, MAX_CHAT_EXCERPT_CHARS)
    if excerpt:
        metadata["comment_text"] = excerpt
    return build_event(
        event_type="share",
        source_platform=link.platform,
        title=link.title,
        url=link.resolved_url or link.original_url,
        author=link.author,
        metadata=metadata,
    )


class LinkIngestor:
    """Fetch and record links shared in chat messages.

    ``bilibili_client`` 复用 ``BilibiliAPIClient``(仅需 ``get_video_info``;
    ``get_video_tags`` 可选,失败静默)。缺省时 bilibili 链接也降级为页面
    元数据抓取。``event_sink`` 为偏好事件写入路径(生产环境
    ``MemoryManager.propagate_event``);None 时只抓内容不记偏好。
    ``http_client_factory`` 是测试注入点;缺省每次抓取新建
    ``trust_env=False`` 的 httpx.AsyncClient(pitfall 规则 1:B站/知乎/
    小红书均为 CN 站点,继承系统代理会被风控或超时),用完即关,
    不给配置热重载留悬挂连接。
    """

    def __init__(
        self,
        *,
        bilibili_client: SupportsVideoInfo | None = None,
        event_sink: EventSink | None = None,
        http_client_factory: HttpClientFactory | None = None,
        timeout: float = FETCH_TIMEOUT_SECONDS,
        max_html_bytes: int = MAX_HTML_BYTES,
        max_links: int = MAX_LINKS_PER_MESSAGE,
    ) -> None:
        self._bilibili_client = bilibili_client
        self._event_sink = event_sink
        self._http_client_factory = http_client_factory
        self._timeout = timeout
        self._max_html_bytes = max_html_bytes
        self._max_links = max(1, max_links)

    async def ingest(self, message: str) -> LinkIngestResult:
        """Extract, fetch and record the links in one chat message.

        Network failures degrade per link; this method only raises on
        programmer error, and callers (``SocraticDialogue``) guard even that.
        """
        urls = extract_urls(message)[: self._max_links]
        if not urls:
            return LinkIngestResult()
        links = list(await asyncio.gather(*(self._ingest_one(url) for url in urls)))
        recorded = await self._record_share_events(links, message)
        return LinkIngestResult(
            links=links,
            prompt_block=render_prompt_block(links),
            relation_hint=render_relation_hint(links),
            recorded_events=recorded,
        )

    async def _ingest_one(self, url: str) -> IngestedLink:
        link = IngestedLink(original_url=url, resolved_url=url)
        link.platform = infer_source_platform_from_url(url)
        try:
            if _is_short_link(url):
                resolved = await self._expand_short_link(url)
                if resolved and resolved != url:
                    link.resolved_url = resolved
                    link.platform = infer_source_platform_from_url(resolved) or link.platform
            bvid = bvid_from_url(link.resolved_url) or bvid_from_url(url)
            if (
                link.platform == PLATFORM_BILIBILI
                and _BVID_RE.fullmatch(bvid)
                and self._bilibili_client is not None
            ):
                await self._fill_bilibili(link, bvid)
            else:
                await self._fill_from_html(link)
        except Exception as exc:
            link.status = "failed"
            link.error = _truncate(str(exc), 120)
            logger.info("Link ingestion degraded for %s: %s", url, link.error)
        return link

    async def _fill_bilibili(self, link: IngestedLink, bvid: str) -> None:
        """Fill a bilibili link from the /view API (标题/简介/UP 主/标签)."""
        assert self._bilibili_client is not None
        info = await self._bilibili_client.get_video_info(bvid)
        link.content_id = bvid
        link.resolved_url = f"https://www.bilibili.com/video/{bvid}"
        link.title = str(getattr(info, "title", "") or "").strip()
        link.author = str(getattr(info, "up_name", "") or "").strip()
        link.summary = _truncate(getattr(info, "description", ""), MAX_SUMMARY_CHARS)
        get_tags = getattr(self._bilibili_client, "get_video_tags", None)
        if callable(get_tags):
            try:
                link.tags = [str(tag) for tag in await get_tags(bvid, limit=5) if str(tag).strip()]
            except Exception:
                logger.debug("bilibili tag fetch failed for %s; skipping tags", bvid)
        if not link.title:
            raise ValueError("bilibili /view returned no title")
        link.status = "ok"

    async def _fill_from_html(self, link: IngestedLink) -> None:
        """Fill any other link from page title/description/og metadata."""
        final_url, html = await self._fetch_html(link.resolved_url)
        link.resolved_url = final_url
        if not link.platform:
            link.platform = infer_source_platform_from_url(final_url)
        if not link.content_id and link.platform == "xiaohongshu":
            link.content_id = note_id_from_url(final_url)
        metadata = parse_html_metadata(html)
        link.title = metadata.title
        link.summary = metadata.description
        if not link.title and not link.summary:
            raise ValueError("page returned no usable metadata")
        link.status = "ok"

    def _new_http_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(
            headers={"User-Agent": _BROWSER_USER_AGENT},
            timeout=self._timeout,
            follow_redirects=True,
            trust_env=False,
        )

    async def _expand_short_link(self, url: str) -> str:
        """Follow a short link's redirects and return the landing URL."""
        client = self._new_http_client()
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                # 不读 body:拿落地 URL 就够了。
                return str(response.url)
        finally:
            await client.aclose()

    async def _fetch_html(self, url: str) -> tuple[str, str]:
        """GET a page with redirect following, returning (final_url, decoded_html).

        The body is capped at ``max_html_bytes``; non-HTML responses are
        rejected so images/archives never reach the parser.
        """
        client = self._new_http_client()
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if content_type and not any(
                    marker in content_type.lower() for marker in ("html", "text/plain", "xml")
                ):
                    raise ValueError(f"non-HTML content-type: {content_type}")
                final_url = str(response.url)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes(65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= self._max_html_bytes:
                        break
            raw = b"".join(chunks)[: self._max_html_bytes]
            charset = response.charset_encoding or "utf-8"
        finally:
            await client.aclose()
        return final_url, raw.decode(charset, errors="replace")

    async def _record_share_events(self, links: list[IngestedLink], message: str) -> int:
        """Record one ``share`` event per successfully fetched link."""
        sink = self._event_sink
        if sink is None:
            return 0
        recorded = 0
        for link in links:
            if link.status != "ok":
                continue
            try:
                await sink(build_share_event(link, message))
                recorded += 1
            except Exception:
                logger.warning(
                    "link share event sink failed for %s; preference signal lost, chat continues",
                    link.resolved_url or link.original_url,
                    exc_info=True,
                )
        return recorded
