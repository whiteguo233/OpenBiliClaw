"""Formal runtime wiring locks for recipe-driven public-Web discovery."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from openbiliclaw.api.runtime_context import (
    RuntimeContext,
    _pool_source_shares_from_config,
)
from openbiliclaw.config import Config
from openbiliclaw.runtime.refresh import (
    _PLATFORM_SOURCE_ORDER,
    ContinuousRefreshController,
)
from openbiliclaw.sources.web_recipe import build_web_recipe


class _Producer:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def produce_if_due(self, *, limit: int) -> dict[str, object]:
        self.calls.append(limit)
        return {
            "source_family": "web",
            "reason": "ok",
            "enqueued": 1,
            "productive_supply": True,
        }


def _controller(
    *,
    web_producer: object | None = None,
    pool_source_shares: dict[str, int] | None = None,
    pool_source_shares_provider=None,
) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
        pool_target_count=10,
        discovery_limit=5,
        pool_source_shares=pool_source_shares or {"web": 1},
        pool_source_shares_provider=pool_source_shares_provider,
        web_producer=web_producer,
    )


def test_runtime_context_registers_adapter_builds_producer_and_defers_eval() -> None:
    source = inspect.getsource(RuntimeContext._rebuild_components)

    assert "web_adapter = WebSourceAdapter(" in source
    assert "new_discovery_engine.register_adapter(web_adapter)" in source
    assert "new_web_producer = WebDiscoveryProducer(" in source
    assert "adapter=web_adapter" in source
    assert 'enabled=bool(getattr(web_cfg, "enabled", True))' in source
    assert "web_producer=new_web_producer" in source
    assert "new_web_producer," in source
    assert "producer.candidate_evaluation_owned_by_coordinator = True" in source


def test_runtime_share_provider_tracks_enabled_recipe_without_restart() -> None:
    config = Config()
    recipes: list[dict[str, object]] = []
    database = SimpleNamespace(
        get_enabled_recipes=lambda **kwargs: (
            list(recipes) if kwargs.get("source_type") == "web" else []
        )
    )
    controller = _controller(
        pool_source_shares={"bilibili": 5},
        pool_source_shares_provider=lambda: _pool_source_shares_from_config(
            config,
            database=database,
        ),
    )

    assert "web" not in controller._normalized_pool_source_shares()
    recipes.append(
        build_web_recipe(
            {
                "name": "Example feed",
                "strategy": "feed",
                "config": {"entry_url": "https://example.com/feed.xml"},
            },
            recipe_id="web-a",
        )
    )
    assert controller._normalized_pool_source_shares()["web"] == 1
    config.sources.web.enabled = False
    assert "web" not in controller._normalized_pool_source_shares()


@pytest.mark.asyncio
async def test_web_ticker_uses_source_deficit_and_formal_producer() -> None:
    producer = _Producer()
    controller = _controller(web_producer=producer)
    controller._is_initialized = lambda: True  # type: ignore[method-assign]
    controller._source_deficit = lambda _source: 3  # type: ignore[method-assign]

    result = await controller._tick_web_producer()

    assert producer.calls == [3]
    assert result == {
        "source_family": "web",
        "reason": "ok",
        "enqueued": 1,
        "productive_supply": True,
        "requested_limit": 3,
    }


def test_web_is_in_every_refresh_producer_roster() -> None:
    assert _PLATFORM_SOURCE_ORDER.count("web") == 1
    assert _PLATFORM_SOURCE_ORDER[-1] == "web"

    deficit_source = inspect.getsource(ContinuousRefreshController._run_deficit_producers_once)
    forever_source = inspect.getsource(ContinuousRefreshController.run_forever)
    loop_source = inspect.getsource(ContinuousRefreshController._loop_web_producer)
    assert '"web": self._tick_web_producer' in deficit_source
    assert "self._loop_web_producer()" in forever_source
    assert "await self._tick_web_producer()" in loop_source


def test_refresh_stranded_share_check_names_missing_web_producer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    controller = _controller(web_producer=None)

    controller._warn_on_stranded_source_shares()

    assert "web" in caplog.text
    caplog.clear()
    controller.web_producer = object()
    controller._warn_on_stranded_source_shares()
    assert "web" not in caplog.text
