"""Tests for prompt-layer rendering cache helpers."""

from __future__ import annotations

from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache, profile_prompt_layers


def test_prompt_layer_cache_reuses_unchanged_layer_text() -> None:
    cache = PromptLayerRenderCache()

    first = cache.render_json_layer("profile_core", {"traits": ["stable"], "score": 1})
    second = cache.render_json_layer("profile_core", {"score": 1, "traits": ["stable"]})
    changed = cache.render_json_layer("profile_core", {"traits": ["changed"], "score": 1})

    assert second is first
    assert changed is not first
    assert cache.stats() == {
        "profile_core": {"digest": cache.layer_digest("profile_core"), "hits": 1, "misses": 2}
    }


def test_profile_prompt_layers_orders_stable_profile_before_recent_context() -> None:
    layers = profile_prompt_layers(
        {
            "active_insights": ["volatile"],
            "core_traits": ["stable"],
            "interests": [{"name": "stable-interest"}],
            "style": {"depth_preference": 0.8},
            "current_phase": "semi-stable",
        }
    )

    assert [name for name, _payload in layers] == [
        "profile_core",
        "profile_life_context",
        "profile_interests",
        "profile_style_context",
        "profile_recent_context",
    ]
    assert layers[0][1] == {"core_traits": ["stable"]}
    assert layers[-1][1] == {"active_insights": ["volatile"]}
