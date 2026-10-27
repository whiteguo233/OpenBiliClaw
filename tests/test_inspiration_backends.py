"""Shared inspiration-backend vocabulary and the bundled grounding API keys."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from openbiliclaw.config import Config
from openbiliclaw.discovery.inspiration_backends import (
    DEFAULT_INSPIRATION_SEARCH_BACKENDS,
    EXTERNAL_INSPIRATION_SEARCH_BACKENDS,
    INSPIRATION_SEARCH_BACKEND_ALIASES,
    normalize_inspiration_search_backends,
)
from openbiliclaw.discovery.inspiration_provider import (
    ExaInspirationProvider,
    FallbackInspirationSearchProvider,
    InspirationGroundingKeys,
    build_inspiration_search_provider,
)

if TYPE_CHECKING:
    import pytest


def test_alias_targets_are_self_mapping_canonical_names() -> None:
    for target in set(INSPIRATION_SEARCH_BACKEND_ALIASES.values()):
        assert INSPIRATION_SEARCH_BACKEND_ALIASES.get(target) == target


def test_external_backends_are_all_canonical() -> None:
    canonical = set(INSPIRATION_SEARCH_BACKEND_ALIASES.values())
    assert canonical >= EXTERNAL_INSPIRATION_SEARCH_BACKENDS
    assert "local_cache" not in EXTERNAL_INSPIRATION_SEARCH_BACKENDS
    assert "platform_sources" not in EXTERNAL_INSPIRATION_SEARCH_BACKENDS


def test_config_default_chain_matches_shared_chain() -> None:
    assert Config().discovery.inspiration_search_backends == DEFAULT_INSPIRATION_SEARCH_BACKENDS


def test_normalize_maps_aliases_case_and_comma_string() -> None:
    assert normalize_inspiration_search_backends("Bing, you.com,local-cache,Exa") == (
        "bing_rss",
        "you",
        "local_cache",
        "exa",
    )


def test_normalize_drops_unknown_and_duplicate_backends() -> None:
    assert normalize_inspiration_search_backends(["exa", "EXA", "keenable", "nope"]) == (
        "exa",
        "keenable",
    )


def test_normalize_falls_back_to_shared_default_chain() -> None:
    assert normalize_inspiration_search_backends(None) == DEFAULT_INSPIRATION_SEARCH_BACKENDS
    assert normalize_inspiration_search_backends([]) == DEFAULT_INSPIRATION_SEARCH_BACKENDS


def test_normalize_honours_empty_default_for_additive_callers() -> None:
    assert normalize_inspiration_search_backends(["nope"], default=()) == ()
    assert normalize_inspiration_search_backends(None, default=()) == ()


def test_grounding_keys_read_discovery_section() -> None:
    discovery = SimpleNamespace(
        exa_api_key=" exa ",
        you_api_key="you",
        serply_api_key="",
        keenable_api_key="stale-key",
    )

    keys = InspirationGroundingKeys.from_discovery_config(discovery)

    # Keenable is anonymous-only, so a leftover key must not travel with the bundle.
    assert keys == InspirationGroundingKeys(exa=" exa ", you="you", serply="")


def test_grounding_keys_default_to_empty_for_missing_fields() -> None:
    assert InspirationGroundingKeys.from_discovery_config(SimpleNamespace()) == (
        InspirationGroundingKeys()
    )


def test_builder_uses_bundled_exa_key_instead_of_mcporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    provider = build_inspiration_search_provider(
        ["exa"], grounding_keys=InspirationGroundingKeys(exa="test-exa-key")
    )

    assert isinstance(provider, FallbackInspirationSearchProvider)
    (inner,) = provider.providers
    assert isinstance(inner, ExaInspirationProvider)


def test_builder_without_bundled_key_stays_on_the_mcporter_free_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert build_inspiration_search_provider(["exa"]) is None
