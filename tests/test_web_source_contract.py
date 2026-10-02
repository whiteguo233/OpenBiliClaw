"""Frozen Phase A contract for anonymous public-Web discovery."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

from openbiliclaw.sources.platforms import (
    PLATFORM_WEB,
    SOURCE_FAMILY_RULES,
)
from openbiliclaw.sources.web_recipe import build_web_recipe

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "platform-source-contract.web.toml"
WEB_PUBLIC_PATH = ROOT / "src" / "openbiliclaw" / "sources" / "web_public.py"

EXPECTED_N_A_KEYS = {
    "search.integration",
    "profile.signals",
    "profile.incremental",
    "profile.refresh-mode",
    "extension.task",
    "extension.task-marker",
    "extension.background",
    "extension.early-response",
    "extension.cookie-sync",
    "surface.setup",
    "surface.credentials",
    "engagement.view",
    "engagement.like",
    "engagement.favorite",
    "engagement.comment",
    "engagement.share",
    "engagement.danmaku",
    "media.image",
    "media.deep-link",
    "media.native-save",
}


def _contract() -> dict[str, Any]:
    with CONTRACT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def test_web_contract_freezes_phase_a_and_exact_exclusion_nodeids() -> None:
    contract = _contract()

    assert contract["schema_version"] == 2
    assert contract["canonical_slug"] == "web"
    assert contract["integration_level"] == "discovery-only"
    assert contract["host_policy"] == "recipe-allowlist"
    assert contract["hosts"] == []
    assert contract["transport"]["kind"] == "web-http"
    assert contract["transport"]["owner"] == "backend"
    assert set(contract["transport"]["capability_routes"]) == {"discover"}
    assert contract["discover"]["modes"] == ["feed", "sitemap", "listing"]

    exclusions = contract["exclusions"]
    exclusion_tests = contract["exclusion_tests"]
    assert set(exclusions) == EXPECTED_N_A_KEYS
    assert set(exclusion_tests) == EXPECTED_N_A_KEYS
    assert all(str(reason).strip() for reason in exclusions.values())
    for references in exclusion_tests.values():
        assert len(references) == 1
        relative, nodeid = references[0].split("::", 1)
        assert relative == "tests/test_web_source_contract.py"
        assert callable(globals().get(nodeid))


def test_web_canonical_registry_is_one_dynamic_host_family() -> None:
    web_rules = [rule for rule in SOURCE_FAMILY_RULES if rule.family == PLATFORM_WEB]

    assert PLATFORM_WEB == "web"
    assert len(web_rules) == 1
    assert web_rules[0].platform_aliases == frozenset({"web"})
    assert web_rules[0].source_prefixes == ("web-", "web_")
    assert web_rules[0].url_hosts == ()


def test_web_recipe_is_typed_bounded_and_backend_owned() -> None:
    recipe = build_web_recipe(
        {
            "name": "Example public feed",
            "strategy": "feed",
            "config": {
                "entry_url": "https://news.example.com/feed.xml",
                "allowed_origins": ["https://articles.example.com"],
                "interval_minutes": 30,
                "max_items": 20,
                "max_pages": 2,
                "detail_fetch_limit": 5,
            },
        },
        recipe_id="web-recipe-1",
        created_by="user",
    )

    assert recipe["id"] == "web-recipe-1"
    assert recipe["source_type"] == "web"
    assert recipe["strategy"] == "feed"
    assert recipe["config"]["allowed_origins"] == [
        "https://news.example.com",
        "https://articles.example.com",
    ]
    assert recipe["config"]["plan_version"] == 1
    assert not ({"state", "etag", "cursor", "status"} & set(recipe))


def test_web_transport_and_parser_entrypoint_declares_bounded_symbols() -> None:
    contract = _contract()

    assert WEB_PUBLIC_PATH.is_file()
    tree = ast.parse(WEB_PUBLIC_PATH.read_text(encoding="utf-8"))
    symbols = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "SafeWebTransport",
        "canonicalize_web_url",
        "web_content_id",
        "parse_web_feed",
        "parse_sitemap",
        "parse_html_listing",
        "parse_html_detail",
    } <= symbols
    assert "src/openbiliclaw/sources/web_public.py" in contract["transport"]["entrypoints"]


def test_web_search_integration_is_follow_only() -> None:
    assert "search" not in _contract()["discover"]["modes"]


def test_web_profile_signals_are_absent() -> None:
    assert _contract()["profile"]["signals"] is False


def test_web_profile_incremental_is_absent() -> None:
    assert _contract()["profile"]["incremental"] is False


def test_web_profile_refresh_mode_is_none() -> None:
    assert _contract()["profile"]["refresh_mode"] == "none"


def test_web_extension_task_is_none() -> None:
    assert _contract()["extension"]["task"] == "none"


def test_web_extension_task_marker_is_absent() -> None:
    assert _contract()["extension"]["task_marker"] is False


def test_web_extension_background_is_absent() -> None:
    assert _contract()["extension"]["background"] is False


def test_web_extension_early_response_is_absent() -> None:
    assert _contract()["extension"]["early_response"] is False


def test_web_extension_cookie_sync_is_absent() -> None:
    assert _contract()["extension"]["cookie_sync"] is False


def test_web_surface_setup_is_excluded() -> None:
    assert _contract()["surfaces"]["setup"] is False


def test_web_surface_credentials_are_excluded() -> None:
    assert _contract()["surfaces"]["credentials"] is False


def test_web_engagement_view_is_unavailable() -> None:
    assert _contract()["engagement"]["view"] == "unavailable"


def test_web_engagement_like_is_unavailable() -> None:
    assert _contract()["engagement"]["like"] == "unavailable"


def test_web_engagement_favorite_is_unavailable() -> None:
    assert _contract()["engagement"]["favorite"] == "unavailable"


def test_web_engagement_comment_is_unavailable() -> None:
    assert _contract()["engagement"]["comment"] == "unavailable"


def test_web_engagement_share_is_unavailable() -> None:
    assert _contract()["engagement"]["share"] == "unavailable"


def test_web_engagement_danmaku_is_unavailable() -> None:
    assert _contract()["engagement"]["danmaku"] == "unavailable"


def test_web_media_image_is_none() -> None:
    assert _contract()["media"]["image"] == "none"


def test_web_media_deep_link_is_browser_fallback() -> None:
    assert _contract()["media"]["deep_link"] == "browser-fallback"


def test_web_media_native_save_is_absent() -> None:
    assert _contract()["media"]["native_save"] is False
