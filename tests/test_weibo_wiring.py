"""Drift locks for Weibo's cross-cutting central wiring.

These tests deliberately stay at configuration/registry boundaries.  They do
not construct the API daemon or contact Weibo, but they fail if a future source
refactor adds Weibo in one central roster and silently drops it from another.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.api.models import (
    SourcesConfigOut,
    SourcesCredentialsResponse,
    SourcesStatusResponse,
)
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.config import Config, ConfigError, load_config, save_config
from openbiliclaw.llm.prompts import (
    build_merged_keywords_prompt,
    platform_supply_advantage,
)
from openbiliclaw.runtime import image_cache, keyword_planner
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.runtime.source_policy import (
    DEFAULT_POOL_SOURCE_SHARES,
    DEFAULT_SOURCE_ENABLED,
    SOURCE_ORDER,
    effective_pool_source_shares,
)
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    infer_source_platform_from_url,
    normalize_source_platform,
    source_family,
)


def test_weibo_config_defaults_and_round_trip(tmp_path: Path) -> None:
    config = Config()

    assert config.sources.weibo.enabled is False
    assert config.sources.weibo.source_modes == ("search", "hot", "creator")
    assert config.sources.weibo.daily_search_budget == 60
    assert config.sources.weibo.daily_hot_budget == 10
    assert config.sources.weibo.daily_creator_budget == 30
    assert config.sources.weibo.request_interval_seconds == 3
    assert config.sources.weibo.min_interval_minutes == 10
    assert config.scheduler.pool_source_shares["weibo"] == 1

    config.sources.weibo.enabled = True
    config.sources.weibo.source_modes = ("creator", "hot")
    config.sources.weibo.daily_search_budget = 41
    config.sources.weibo.daily_hot_budget = 17
    config.sources.weibo.daily_creator_budget = 23
    config.sources.weibo.request_interval_seconds = 4
    config.sources.weibo.min_interval_minutes = 19
    config.scheduler.pool_source_shares["weibo"] = 3
    target = tmp_path / "config.toml"

    save_config(config, target)
    rendered = target.read_text(encoding="utf-8")
    loaded = load_config(target)

    assert "[sources.weibo]" in rendered
    assert 'source_modes = ["creator", "hot"]' in rendered
    assert "weibo = 3" in rendered
    assert loaded.sources.weibo == config.sources.weibo
    assert loaded.scheduler.pool_source_shares["weibo"] == 3


@pytest.mark.parametrize("raw_value", ["1.5", "true"])
def test_weibo_config_rejects_non_integer_budget_types(
    tmp_path: Path,
    raw_value: str,
) -> None:
    target = tmp_path / "config.toml"
    target.write_text(
        f"[sources.weibo]\ndaily_search_budget = {raw_value}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match=r"sources\.weibo\.daily_search_budget 必须是非负整数",
    ):
        load_config(target)


def test_weibo_source_policy_order_enablement_and_share() -> None:
    assert SOURCE_ORDER[-1] == "weibo"
    assert SOURCE_ORDER.count("weibo") == 1
    assert DEFAULT_SOURCE_ENABLED["weibo"] is False
    assert DEFAULT_POOL_SOURCE_SHARES["weibo"] == 1

    config = Config()
    config.sources.weibo.enabled = True
    config.scheduler.pool_source_shares["weibo"] = 4
    assert effective_pool_source_shares(config)["weibo"] == 4

    config.sources.weibo.enabled = False
    assert "weibo" not in effective_pool_source_shares(config)


@pytest.mark.parametrize("alias", ["weibo", "wb", "微博"])
def test_weibo_platform_alias_and_strategy_resolution(alias: str) -> None:
    assert normalize_source_platform(alias) == "weibo"
    assert source_family("weibo-hot", alias) == "weibo"
    assert CANONICAL_SOURCE_FAMILIES[-1] == "weibo"


@pytest.mark.parametrize(
    "url",
    [
        "https://weibo.com/u/123",
        "https://www.weibo.com/123/AbCd",
        "https://m.weibo.cn/detail/123",
    ],
)
def test_weibo_url_inference_uses_exact_host_boundaries(url: str) -> None:
    assert infer_source_platform_from_url(url) == "weibo"
    assert infer_source_platform_from_url(f"https://example.com/{url}") == ""


def test_keyword_planner_and_merged_prompt_include_weibo() -> None:
    assert keyword_planner._PLANNER_PLATFORMS[-1] == "weibo"
    assert "weibo" in keyword_planner._PLATFORM_QUERY_STYLES
    assert "热议" in keyword_planner._PLATFORM_QUERY_STYLES["weibo"]["native_markers"]
    assert "实时" in platform_supply_advantage("weibo")

    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "人工智能", "weight": 0.8}]},
        platform_blocks=[{"platform": "weibo", "need": 2}],
    )

    assert "weibo" in messages[0]["content"]
    assert '"platform": "weibo"' in messages[1]["content"]


def test_weibo_image_cdn_is_allowlisted_and_forced_direct() -> None:
    url = "https://wx1.sinaimg.cn/large/demo.jpg"

    assert "sinaimg.cn" in image_cache.ALLOWED_IMAGE_HOST_SUFFIXES
    assert "sinaimg.cn" in image_cache._DIRECT_FETCH_HOST_SUFFIXES
    assert image_cache.is_allowed_image_url(url) is True
    assert image_cache._is_direct_fetch_host("wx1.sinaimg.cn") is True
    assert image_cache.is_allowed_image_url("https://evilsinaimg.cn/demo.jpg") is False


def _controller(*, weibo_producer: object | None = None) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
        pool_target_count=10,
        pool_source_shares={"weibo": 1},
        weibo_producer=weibo_producer,
    )


@pytest.mark.asyncio
async def test_runtime_context_and_refresh_ticker_wire_weibo() -> None:
    assert "weibo_client" in RuntimeContext.__dataclass_fields__
    assert "weibo_producer" in ContinuousRefreshController.__dataclass_fields__

    rebuild_source = inspect.getsource(RuntimeContext._rebuild_components)
    assert "WeiboClient" in rebuild_source
    assert "WeiboDiscoveryProducer" in rebuild_source
    assert "weibo_client=new_weibo_client" in rebuild_source
    assert "weibo_producer=new_weibo_producer" in rebuild_source

    producer = object()
    controller = _controller(weibo_producer=producer)
    calls: list[tuple[str, object | None]] = []

    async def fake_tick_platform_producer(
        *,
        source_family: str,
        producer: object | None,
    ) -> dict[str, object]:
        calls.append((source_family, producer))
        return {"discovered": 1, "reason": "ok"}

    controller._tick_platform_producer = fake_tick_platform_producer  # type: ignore[method-assign]

    assert await controller._tick_weibo_producer() == {"discovered": 1, "reason": "ok"}
    assert calls == [("weibo", producer)]
    deficit_source = inspect.getsource(ContinuousRefreshController._run_deficit_producers_once)
    forever_source = inspect.getsource(ContinuousRefreshController.run_forever)
    assert '"weibo": self._tick_weibo_producer' in deficit_source
    assert "self._loop_weibo_producer()" in forever_source


def test_refresh_stranded_share_check_names_missing_weibo_producer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    controller = _controller(weibo_producer=None)

    controller._warn_on_stranded_source_shares()

    assert "weibo" in caplog.text
    caplog.clear()
    controller.weibo_producer = object()
    controller._warn_on_stranded_source_shares()
    assert "weibo" not in caplog.text


def test_api_models_expose_weibo_status_credential_and_config_fields() -> None:
    assert "weibo" in SourcesStatusResponse.model_fields
    assert "weibo" in SourcesCredentialsResponse.model_fields
    assert "weibo" in SourcesConfigOut.model_fields

    assert SourcesStatusResponse().weibo.state == "missing"
    assert SourcesCredentialsResponse().weibo.available is False
    assert SourcesConfigOut().weibo.source_modes == ["search", "hot", "creator"]


def _literal_module_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing module assignment: {name}")


def test_guided_init_backend_roster_includes_logged_in_weibo() -> None:
    app_path = Path(__file__).parents[1] / "src/openbiliclaw/api/app.py"
    share_roster = _literal_module_assignment(app_path, "_SOURCE_SHARE_ORDER")
    init_roster = _literal_module_assignment(app_path, "_INIT_SOURCE_ORDER")

    assert "weibo" in share_roster
    assert "weibo" in init_roster
    # Guided init covers every share source except tiktok (guidedInit: false).
    assert init_roster == tuple(source for source in share_roster if source != "tiktok")
