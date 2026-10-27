"""Standalone CLI discovery uses the independent filter's real construction seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw import cli
from openbiliclaw.config import Config
from openbiliclaw.decision.runtime import LoopLocalDecisionProvider
from openbiliclaw.discovery.semantic_filter import JevSemanticFilter, build_cli_semantic_filter


@pytest.fixture
def discovery_dependencies(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace unrelated discovery IO, leaving the CLI and filter factories real."""
    config = Config()
    database = object()
    constructed: list[SimpleNamespace] = []

    def build_engine(**kwargs: Any) -> SimpleNamespace:
        engine = SimpleNamespace(**kwargs, strategies=[])
        engine.register_strategy = engine.strategies.append
        constructed.append(engine)
        return engine

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(cli, "_build_memory_manager", object)
    monkeypatch.setattr(cli, "_get_runtime_database", lambda: database)
    monkeypatch.setattr(cli, "_build_bilibili_client", object)
    monkeypatch.setattr(cli, "_build_registry", object)
    monkeypatch.setattr(cli, "_build_usage_recorder", lambda: None)
    monkeypatch.setattr(cli, "_build_llm_concurrency_gate", lambda: None)
    monkeypatch.setattr("openbiliclaw.llm.service.LLMService", SimpleNamespace)
    monkeypatch.setattr("openbiliclaw.llm.registry.build_embedding_service", lambda *_: None)
    monkeypatch.setattr("openbiliclaw.discovery.engine.ContentDiscoveryEngine", build_engine)
    for strategy in (
        "SearchStrategy",
        "TrendingStrategy",
        "RelatedChainStrategy",
        "ExploreStrategy",
    ):
        monkeypatch.setattr(
            f"openbiliclaw.discovery.strategies.strategies.{strategy}", SimpleNamespace
        )

    def forbidden_transport(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("CLI construction must not create a decision transport")

    monkeypatch.setattr(
        "openbiliclaw.decision.runtime.build_decision_provider", forbidden_transport
    )
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return SimpleNamespace(config=config, database=database, constructed=constructed)


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_cli_discovery_injects_independent_filter_without_director_or_transport(
    discovery_dependencies: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    dependencies = discovery_dependencies
    config = dependencies.config
    config.director.mode = "off"
    config.decision.providers.jev.enabled = True
    config.discovery.semantic_filter.mode = mode
    config.discovery.semantic_filter.calibration_tag = "unit-test-only"
    config.discovery.semantic_filter.calibration_model = config.decision.providers.jev.model
    if mode != "off":
        monkeypatch.setenv("TYPESAFE_API_KEY", "unit-test-only-key")

    engine = cli._build_discovery_engine()

    assert len(engine.strategies) == 4
    assert engine.database is dependencies.database
    if mode == "off":
        assert engine.semantic_filter is None
    else:
        filtering = engine.semantic_filter
        assert isinstance(filtering, JevSemanticFilter)
        assert isinstance(filtering.provider, LoopLocalDecisionProvider)
        assert filtering.database is dependencies.database
        assert filtering.mode == mode


def test_cli_enabled_filter_fails_before_engine_construction_when_credentials_missing(
    discovery_dependencies: SimpleNamespace,
) -> None:
    dependencies = discovery_dependencies
    dependencies.config.decision.providers.jev.enabled = True
    dependencies.config.discovery.semantic_filter.mode = "shadow"

    with pytest.raises(ValueError, match="environment variable"):
        cli._build_discovery_engine()

    assert dependencies.constructed == []


def test_cli_filter_route_does_not_fall_back_when_provider_disabled(
    discovery_dependencies: SimpleNamespace,
) -> None:
    dependencies = discovery_dependencies
    dependencies.config.discovery.semantic_filter.mode = "shadow"

    with pytest.raises(ValueError, match="decision.providers.jev.enabled"):
        cli._build_discovery_engine()

    assert dependencies.constructed == []


@pytest.mark.parametrize("stub", [SimpleNamespace(), SimpleNamespace(discovery=SimpleNamespace())])
def test_legacy_config_double_with_missing_optional_route_is_off(stub: Any) -> None:
    assert build_cli_semantic_filter(stub, None) is None
