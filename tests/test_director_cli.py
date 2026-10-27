"""CLI renders the same durable screen contract without model network calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw import cli
from openbiliclaw.config import Config
from openbiliclaw.recommendation.director.runtime import DirectorRuntime
from openbiliclaw.recommendation.director.store import DirectorStore
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_runtime import FakeAgent, FakeDecision, seed_database


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
def test_cli_generates_renders_acknowledges_and_restores_same_screen(
    tmp_path, monkeypatch, strategy
):
    database = seed_database(tmp_path / "cli.db")
    config = Config()
    config.director.mode = "enforce"
    config.director.strategy = strategy
    agent = FakeAgent()
    engine = RecommendationEngine(llm=agent, database=database)
    rendered = []
    providers = []

    async def profile():
        return SoulProfile()

    def build(*args, **kwargs):
        provider = FakeDecision()
        providers.append(provider)
        return DirectorRuntime(config, database, engine, agent, provider=provider)

    monkeypatch.setattr(cli, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli, "_build_soul_engine", lambda: SimpleNamespace(get_profile=profile))
    monkeypatch.setattr(cli, "_build_recommendation_engine", lambda: engine)
    monkeypatch.setattr(cli, "_get_runtime_database", lambda: database)
    monkeypatch.setattr(
        cli, "_print_recommendation_card", lambda item, index: rendered.append(item)
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(
        "openbiliclaw.recommendation.director.runtime.build_director_runtime", build
    )
    try:
        cli.recommend(request_id="cli-first", limit=4)
        assert len(rendered) == 4
        store = DirectorStore(database)
        batch = store.restore("cli")
        assert batch is not None
        assert all(
            database.get_recommendation_by_id(item.recommendation_id)["presented"]
            for item in rendered
        )
        assert database.count_recommendations() == 4
        cli.recommend(restore=True, limit=4)
        assert len(rendered) == 8
        assert [item.recommendation_id for item in rendered[:4]] == [
            item.recommendation_id for item in rendered[4:]
        ]
        assert database.count_recommendations() == 4
        assert store.session("cli").state_revision == 1
        assert all(provider.closed for provider in providers)
        if strategy == "agent_only":
            assert not any(provider.requests for provider in providers)
    finally:
        database.close()
