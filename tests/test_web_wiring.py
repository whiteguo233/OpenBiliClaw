"""Configuration and central-roster drift locks for public Web discovery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from openbiliclaw.api.runtime_context import _pool_source_shares_from_config
from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.runtime.source_policy import effective_pool_source_shares
from openbiliclaw.sources.web_recipe import build_web_recipe

if TYPE_CHECKING:
    from pathlib import Path


def test_web_config_defaults_and_round_trip(tmp_path: Path) -> None:
    config = Config()
    assert config.sources.web.enabled is True
    assert config.sources.web.request_timeout_seconds == 15
    assert config.sources.web.max_response_bytes == 2_097_152
    assert config.sources.web.max_redirects == 5
    assert config.sources.web.lease_seconds == 300
    assert config.scheduler.pool_source_shares["web"] == 1

    config.sources.web.enabled = False
    config.sources.web.request_timeout_seconds = 9
    config.sources.web.max_response_bytes = 1_000_000
    config.sources.web.max_redirects = 3
    config.sources.web.lease_seconds = 180
    config.scheduler.pool_source_shares["web"] = 2
    target = tmp_path / "config.toml"
    save_config(config, target)

    rendered = target.read_text(encoding="utf-8")
    loaded = load_config(target)
    assert "[sources.web]" in rendered
    assert "web = 2" in rendered
    assert loaded.sources.web == config.sources.web
    assert loaded.scheduler.pool_source_shares["web"] == 2


def test_web_share_requires_enabled_recipe_even_with_default_switch() -> None:
    config = Config()
    assert "web" not in effective_pool_source_shares(config)
    assert (
        effective_pool_source_shares(
            config,
            enabled_recipe_available=lambda source: source == "web",
        )["web"]
        == 1
    )
    config.sources.web.enabled = False
    assert "web" not in effective_pool_source_shares(
        config,
        enabled_recipe_available=lambda _source: True,
    )


def test_runtime_share_projection_reads_enabled_web_recipes() -> None:
    config = Config()
    without = SimpleNamespace(get_enabled_recipes=lambda **_kwargs: [])
    compiled = build_web_recipe(
        {
            "name": "Example feed",
            "strategy": "feed",
            "config": {"entry_url": "https://example.com/feed.xml"},
        },
        recipe_id="web-a",
    )
    with_web = SimpleNamespace(
        get_enabled_recipes=lambda **kwargs: (
            [compiled] if kwargs.get("source_type") == "web" else []
        )
    )
    with_legacy_web = SimpleNamespace(
        get_enabled_recipes=lambda **kwargs: (
            [
                {
                    "id": "legacy",
                    "source_type": "web",
                    "strategy": "web_extract",
                    "config": {"url": "https://example.com/article"},
                }
            ]
            if kwargs.get("source_type") == "web"
            else []
        )
    )
    assert "web" not in _pool_source_shares_from_config(config, database=without)
    assert "web" not in _pool_source_shares_from_config(config, database=with_legacy_web)
    assert _pool_source_shares_from_config(config, database=with_web)["web"] == 1
