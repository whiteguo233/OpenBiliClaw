"""Tests for LLM content extraction from web pages (Phase 3)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openbiliclaw.saved_sync.identity import make_item_key
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
    def test_web_identity_uses_full_url_when_model_shortens_ids(self) -> None:
        urls = [
            "https://example.com/post/rust-17",
            "https://example.com/post/garden-17",
            "https://elsewhere.example/post/rust-17",
        ]
        service = FakeLLMService(
            json.dumps(
                [
                    {"title": f"Article {i}", "url": url, "content_id": "17"}
                    for i, url in enumerate(urls)
                ]
            )
        )
        items = asyncio.run(
            extract_content_from_page(
                "article source text " * 10, source_platform="web", llm_service=service
            )
        )
        assert [item.item_key for item in items] == [make_item_key("web", "", url) for url in urls]
        assert len({item.item_key for item in items}) == 3

    def test_web_identity_is_stable_across_model_ids_and_relative_links(self) -> None:
        keys = []
        for model_id in ("17", "rust-17", "guessed-id"):
            service = FakeLLMService(
                json.dumps([{"title": "Rust", "url": "/post/rust-17", "content_id": model_id}])
            )
            items = asyncio.run(
                extract_content_from_page(
                    "article source text " * 10,
                    source_platform="web",
                    llm_service=service,
                    base_url="https://example.com/list",
                )
            )
            assert items[0].content_url == "https://example.com/post/rust-17"
            keys.append(items[0].item_key)
        assert len(set(keys)) == 1

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
        assert items[0].content_id == make_item_key(
            "web", "", "https://example.com/posts/abc123"
        ).removeprefix("web:")

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

    def test_extraction_opts_out_of_core_memory_injection(self) -> None:
        # Page extraction never reads the profile; it must not carry the
        # core-memory block (portrait included) into its prompt, which would
        # both waste tokens and churn the caller's cache prefix.
        service = FakeLLMService("[]")
        asyncio.run(
            extract_content_from_page(
                "a" * 100,
                source_platform="web",
                llm_service=service,
            )
        )
        assert len(service.calls) == 1
        assert service.calls[0].get("inject_core_memory") is False
