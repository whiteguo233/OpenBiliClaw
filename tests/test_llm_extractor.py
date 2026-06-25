"""Tests for LLM content extraction from web pages (Phase 3)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from openbiliclaw.sources.llm_extractor import extract_content_from_page


@dataclass
class FakeLLMResponse:
    content: str = ""


class FakeLLMService:
    def __init__(self, response_content: str) -> None:
        self._response = response_content
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> FakeLLMResponse:
        self.calls.append(kwargs)
        return FakeLLMResponse(content=self._response)


class TestLLMExtractor:
    def test_extracts_items_from_valid_json(self) -> None:
        response = """[
            {
                "title": "机械键盘评测",
                "author": "键盘侠",
                "summary": "Cherry 红轴对比",
                "url": "https://example.com/p/1",
                "content_id": "1"
            },
            {
                "title": "静电容键盘",
                "author": "外设达人",
                "summary": "Topre 上手体验",
                "url": "https://example.com/p/2",
                "content_id": "2"
            }
        ]"""
        service = FakeLLMService(response)
        items = asyncio.run(
            extract_content_from_page(
                "这是一段关于机械键盘的页面文字，" * 5 + "内容足够长来触发 LLM 提取。",
                source_platform="xiaohongshu",
                llm_service=service,
            )
        )
        assert len(items) == 2
        assert items[0].title == "机械键盘评测"
        assert items[0].author_name == "键盘侠"
        assert items[0].content_id == "1"
        assert items[0].source_platform == "xiaohongshu"
        assert items[1].content_url == "https://example.com/p/2"

    def test_returns_empty_for_short_text(self) -> None:
        service = FakeLLMService("[]")
        items = asyncio.run(
            extract_content_from_page(
                "hi",
                source_platform="web",
                llm_service=service,
            )
        )
        assert items == []
        assert service.calls == []  # Should not call LLM

    def test_returns_empty_for_invalid_json(self) -> None:
        service = FakeLLMService("not json at all")
        items = asyncio.run(
            extract_content_from_page(
                "a" * 100,
                source_platform="web",
                llm_service=service,
            )
        )
        assert items == []

    def test_skips_items_without_title(self) -> None:
        response = '[{"author": "nobody", "url": "https://example.com"}, {"title": "有标题", "url": "https://example.com/3"}]'
        service = FakeLLMService(response)
        items = asyncio.run(
            extract_content_from_page(
                "a" * 100,
                source_platform="web",
                llm_service=service,
            )
        )
        assert len(items) == 1
        assert items[0].title == "有标题"

    def test_generates_content_id_from_url(self) -> None:
        response = '[{"title": "Test", "url": "https://example.com/posts/abc123"}]'
        service = FakeLLMService(response)
        items = asyncio.run(
            extract_content_from_page(
                "a" * 100,
                source_platform="web",
                llm_service=service,
            )
        )
        assert items[0].content_id == "abc123"

    def test_truncates_long_page_text(self) -> None:
        service = FakeLLMService("[]")
        long_text = "x" * 20000
        asyncio.run(
            extract_content_from_page(
                long_text,
                source_platform="web",
                llm_service=service,
            )
        )
        # The user_input sent to LLM should be truncated
        assert len(service.calls) == 1
        user_input = service.calls[0]["user_input"]
        assert len(user_input) < 10000
