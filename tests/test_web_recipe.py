"""Typed, backend-owned public Web recipe validation."""

from __future__ import annotations

import pytest

from openbiliclaw.sources.web_recipe import (
    WebRecipeValidationError,
    build_web_recipe,
    is_compiled_public_web_recipe,
)


def test_build_web_recipe_compiles_explicit_origin_and_bounds() -> None:
    recipe = build_web_recipe(
        {
            "name": "Example engineering",
            "strategy": "feed",
            "config": {
                "entry_url": "HTTPS://News.Example.com/feed.xml#latest",
                "allowed_origins": ["https://www.example.com"],
                "interval_minutes": 30,
                "max_items": 12,
                "max_pages": 2,
                "detail_fetch_limit": 6,
                "listing": {
                    "item_selector": "article.entry",
                    "link_selector": "a.title",
                    "title_selector": "h2",
                    "summary_selector": "p.summary",
                    "author_selector": ".author",
                    "published_selector": "time",
                    "published_attribute": "datetime",
                    "empty_selector": ".empty",
                },
            },
            "target_share": 3,
        },
        recipe_id="recipe-1",
        created_by="user",
    )

    assert recipe == {
        "id": "recipe-1",
        "source_type": "web",
        "name": "Example engineering",
        "strategy": "feed",
        "config": {
            "entry_url": "https://news.example.com/feed.xml",
            "allowed_origins": [
                "https://news.example.com",
                "https://www.example.com",
            ],
            "interval_minutes": 30,
            "max_items": 12,
            "max_pages": 2,
            "detail_fetch_limit": 6,
            "listing": {
                "item_selector": "article.entry",
                "link_selector": "a.title",
                "title_selector": "h2",
                "summary_selector": "p.summary",
                "author_selector": ".author",
                "published_selector": "time",
                "published_attribute": "datetime",
                "empty_selector": ".empty",
            },
            "plan_version": 1,
        },
        "target_share": 3,
        "enabled": True,
        "created_by": "user",
    }


def test_compiled_recipe_marker_rejects_legacy_and_untyped_web_rows() -> None:
    compiled = build_web_recipe(
        {
            "name": "Example feed",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
        },
        recipe_id="compiled",
    )
    assert is_compiled_public_web_recipe(compiled)
    assert not is_compiled_public_web_recipe(
        {
            **compiled,
            "strategy": "web_extract",
            "config": {"url": "https://example.com/article"},
        }
    )
    assert not is_compiled_public_web_recipe(
        {**compiled, "config": {**compiled["config"], "plan_version": True}}
    )
    assert not is_compiled_public_web_recipe(
        {**compiled, "config": {**compiled["config"], "allowed_origins": "https://example.com"}}
    )


@pytest.mark.parametrize("strategy", ["search", "browser", "auto", "web_extract", ""])
def test_web_recipe_rejects_uncontracted_strategy(strategy: str) -> None:
    with pytest.raises(WebRecipeValidationError, match="strategy"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": strategy,
                "config": {"entry_url": "https://example.com"},
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/feed",
        "http://127.0.0.1/feed",
        "http://[::1]/feed",
        "https://user:secret@example.com/feed",
        "ftp://example.com/feed",
        "https://example.com:8443/feed",
        "https://example.com:80/feed",
        "http://example.com:443/feed",
        "https://bad_host.example/feed",
        "https://-bad.example/feed",
        "https://example.com\\@evil.example/feed",
        "https://router.local/feed",
        "http://①②⑦.⓪.⓪.①/x",
        "https://foo.ⓛⓞⓒⓐⓛ/feed",
        "https://foo.ｌａｎ/feed",
    ],
)
def test_web_recipe_rejects_non_public_or_credentialed_entry_url(url: str) -> None:
    with pytest.raises(WebRecipeValidationError, match="entry_url"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "feed",
                "config": {"entry_url": url},
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/read/cv1",
        "https://x.com/openai",
        "https://www.zhihu.com/question/1",
        "https://www.reddit.com/r/python/",
    ],
)
def test_web_recipe_does_not_shadow_dedicated_platforms(url: str) -> None:
    with pytest.raises(WebRecipeValidationError, match="dedicated source"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "listing",
                "config": {"entry_url": url},
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("interval_minutes", 4),
        ("interval_minutes", 10081),
        ("max_items", 0),
        ("max_items", 51),
        ("max_pages", 0),
        ("max_pages", 6),
        ("detail_fetch_limit", -1),
        ("detail_fetch_limit", 21),
    ],
)
def test_web_recipe_rejects_out_of_bounds_budget(field: str, value: int) -> None:
    config = {"entry_url": "https://example.com", field: value}
    with pytest.raises(WebRecipeValidationError, match=field):
        build_web_recipe({"name": "bad", "strategy": "listing", "config": config})


def test_web_recipe_rejects_client_owned_fields_and_unknown_config() -> None:
    with pytest.raises(WebRecipeValidationError, match="client-owned"):
        build_web_recipe(
            {
                "id": "chosen-by-client",
                "source_type": "web",
                "created_by": "system",
                "name": "bad",
                "strategy": "feed",
                "config": {
                    "entry_url": "https://example.com/feed",
                    "etag": "inject-state",
                },
            }
        )


def test_web_recipe_requires_every_extra_origin_to_be_explicitly_safe() -> None:
    with pytest.raises(WebRecipeValidationError, match="allowed_origins"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "sitemap",
                "config": {
                    "entry_url": "https://example.com/sitemap.xml",
                    "allowed_origins": ["http://169.254.169.254"],
                },
            }
        )


@pytest.mark.parametrize(
    "origin",
    [
        "https://cdn.example.com/private",
        "https://cdn.example.com/?scope=private",
        "https://cdn.example.com/#private",
    ],
)
def test_web_recipe_rejects_allowed_origin_that_would_broaden_a_path(origin: str) -> None:
    with pytest.raises(WebRecipeValidationError, match="exact origins"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "feed",
                "config": {
                    "entry_url": "https://example.com/feed.xml",
                    "allowed_origins": [origin],
                },
            }
        )


def test_web_recipe_rejects_unknown_or_oversized_listing_plan() -> None:
    with pytest.raises(WebRecipeValidationError, match="listing"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "listing",
                "config": {
                    "entry_url": "https://example.com/blog",
                    "listing": {"javascript": "document.querySelectorAll('a')"},
                },
            }
        )


def test_web_recipe_requires_a_prevalidated_listing_item_selector() -> None:
    with pytest.raises(WebRecipeValidationError, match="item selector"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "listing",
                "config": {"entry_url": "https://example.com/blog"},
            }
        )
    with pytest.raises(WebRecipeValidationError, match="listing plan"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "listing",
                "config": {
                    "entry_url": "https://example.com/blog",
                    "listing": {
                        "item_selector": "article:nth-child(1)",
                        "link_selector": "a",
                    },
                },
            }
        )


def test_web_recipe_sitemap_requires_a_detail_budget() -> None:
    with pytest.raises(WebRecipeValidationError, match="detail_fetch_limit"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "sitemap",
                "config": {
                    "entry_url": "https://example.com/sitemap.xml",
                    "detail_fetch_limit": 0,
                },
            }
        )
    with pytest.raises(WebRecipeValidationError, match="listing.item_selector"):
        build_web_recipe(
            {
                "name": "bad",
                "strategy": "listing",
                "config": {
                    "entry_url": "https://example.com/blog",
                    "listing": {"item_selector": "a" * 257},
                },
            }
        )


def test_web_recipe_update_preserves_server_owned_identity() -> None:
    existing = build_web_recipe(
        {
            "name": "Before",
            "strategy": "listing",
            "config": {
                "entry_url": "https://example.com/blog",
                "listing": {"item_selector": "article", "link_selector": "a"},
            },
        },
        recipe_id="recipe-1",
        created_by="agent",
    )
    updated = build_web_recipe(
        {
            "name": "After",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
            "enabled": False,
            "target_share": 2,
        },
        recipe_id=existing["id"],
        created_by=existing["created_by"],
    )
    assert updated["id"] == "recipe-1"
    assert updated["created_by"] == "agent"
    assert updated["enabled"] is False
