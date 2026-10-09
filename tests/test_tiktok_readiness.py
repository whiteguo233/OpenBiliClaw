"""Regression coverage for the TikTok source acceptance audit."""

from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.api.runtime_context import build_tiktok_discovery_strategies
from openbiliclaw.config import Config
from openbiliclaw.discovery.inspiration_provider import TiktokPlatformSearchBackend
from openbiliclaw.sources.tiktok_web import TiktokRouterClient, TiktokWebClient, TiktokWebResponse


async def test_forced_web_does_not_fall_back_after_breaker_opens() -> None:
    web = SimpleNamespace(get_tag_videos=AsyncMock(return_value=None))
    fallback = SimpleNamespace(get_tag_videos=AsyncMock(return_value=[]))
    router = TiktokRouterClient(web=web, ytdlp=fallback, mode="web")
    for _ in range(4):
        with suppress(RuntimeError):
            await router.get_tag_videos("science")
    fallback.get_tag_videos.assert_not_called()


def test_error_envelope_is_not_affirmative_empty() -> None:
    identity = SimpleNamespace(
        ensure_bootstrapped=lambda: True,
        signed_get=lambda *a: TiktokWebResponse(429, b'{"statusCode":429}', {}),
        base_params=lambda p: {},
    )
    with pytest.raises(RuntimeError):
        TiktokWebClient(identity=identity)._feed_items(5)


def test_feed_pull_size_is_independent_of_remaining_daily_calls() -> None:
    for remaining in (1, 2, 3):
        strategies = build_tiktok_discovery_strategies(
            config=Config(),
            client=SimpleNamespace(search_available=False),
            llm_service=None,
            concurrency=None,
            strategy_unit_budget={"tiktok_feed": remaining},
        )
        assert strategies[0].results_per_run == 12


async def test_inspiration_compacts_guest_search_phrase() -> None:
    client = SimpleNamespace(search_available=False, get_tag_videos=AsyncMock(return_value=[]))
    await TiktokPlatformSearchBackend(client).search("machine learning", limit=5)
    client.get_tag_videos.assert_awaited_once_with("machinelearning", limit=5)


async def test_keyword_claim_respects_remaining_budget(tmp_path: Path) -> None:
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.runtime.tiktok_producer import (
        TiktokDiscoveryProducer,
        TiktokStrategyRunResult,
    )
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "audit.db")
    db.initialize()
    db.insert_pending_keywords("tiktok", ["a", "b", "c", "d", "e"], "test")
    coordinator = KeywordFetchCoordinator(
        database=db,
        discovery_config=SimpleNamespace(unified_keyword_planner_enabled=True, fetch_batch=5),
    )
    seen = []

    async def discover(profile, **kwargs):
        seen.extend(kwargs["queries"])
        return TiktokStrategyRunResult([], len(kwargs["queries"]), {})

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=SimpleNamespace(get_profile=AsyncMock(return_value={})),
        discover=discover,
        strategies=("tiktok_search",),
        daily_search_budget=1,
        keyword_fetch=coordinator,
    )
    await producer.produce_if_due()
    assert len(seen) == 1
    assert producer.consumed_today("tiktok_search") == 1
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM discovery_keywords WHERE status='pending'"
        ).fetchone()[0]
        == 4
    )
    db.close()


async def test_web_breaker_recovers_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.sources import tiktok_web

    clock = [100.0]
    monkeypatch.setattr(tiktok_web.time, "monotonic", lambda: clock[0])
    web = SimpleNamespace(get_tag_videos=AsyncMock(side_effect=[None, []]))
    fallback = SimpleNamespace(get_tag_videos=AsyncMock(return_value=[{"id": "fallback"}]))
    router = TiktokRouterClient(web=web, ytdlp=fallback, max_web_failures=1, recovery_seconds=60)
    await router.get_tag_videos("science")
    await router.get_tag_videos("science")
    assert web.get_tag_videos.await_count == 1
    clock[0] += 61
    assert await router.get_tag_videos("science") == []
    assert web.get_tag_videos.await_count == 2


def test_rate_limit_is_shared_and_survives_client_recreation(tmp_path: Path) -> None:
    from openbiliclaw.sources.tiktok_state import TiktokRequestState

    first = TiktokRequestState(tmp_path / "requests.db", 0)
    second = TiktokRequestState(tmp_path / "requests.db", 0)
    first.defer(30)
    assert second.cooldown_remaining() > 0
    with pytest.raises(RuntimeError, match="rate_limited"):
        second.before_request()


def test_request_interval_reserves_separate_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.sources import tiktok_state

    now = [100.0]
    monkeypatch.setattr(tiktok_state.time, "time", lambda: now[0])
    monkeypatch.setattr(
        tiktok_state.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    first = tiktok_state.TiktokRequestState(tmp_path / "requests.db", 2)
    second = tiktok_state.TiktokRequestState(tmp_path / "requests.db", 2)
    first.before_request()
    second.before_request()
    assert now[0] == 102


async def test_failed_search_does_not_mark_all_claims_success(tmp_path: Path) -> None:
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.runtime.tiktok_producer import (
        TiktokDiscoveryProducer,
        TiktokStrategyRunResult,
    )
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "partial.db")
    db.initialize()
    db.insert_pending_keywords("tiktok", ["good", "bad"], "test")
    coordinator = KeywordFetchCoordinator(
        database=db,
        discovery_config=SimpleNamespace(unified_keyword_planner_enabled=True, fetch_batch=5),
    )

    async def discover(profile, **kwargs):
        return TiktokStrategyRunResult(
            [SimpleNamespace(score_threshold=0.6)], 2, {}, {"bad": "rate_limited"}
        )

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=SimpleNamespace(get_profile=AsyncMock(return_value={})),
        discover=discover,
        strategies=("tiktok_search",),
        keyword_fetch=coordinator,
    )
    result = await producer.produce_if_due()
    assert result["reason"] == "degraded"
    statuses = dict(db.conn.execute("SELECT keyword, status FROM discovery_keywords").fetchall())
    assert statuses == {"good": "used", "bad": "failed"}
    db.close()


@pytest.mark.parametrize("status,body", [(429, b""), (429, b'{"error":"quota"}')])
@pytest.mark.parametrize("gated", [False, True])
def test_http_rate_limit_persists_even_without_json_body(
    tmp_path: Path, status: int, body: bytes, gated: bool
) -> None:
    from openbiliclaw.sources.tiktok_state import TiktokRequestState

    state = TiktokRequestState(tmp_path / "shared.db", 0)
    identity = SimpleNamespace(
        request_state=state,
        ensure_bootstrapped=lambda: True,
        signed_get=lambda *args: TiktokWebResponse(
            status, body, {"retry-after": "30", "tt_orcas_res": "1" if gated else "0"}
        ),
        base_params=lambda page: {},
    )
    with pytest.raises(RuntimeError, match="rate_limited"):
        TiktokWebClient(identity=identity)._feed_items(3)
    assert TiktokRequestState(state.path, 0).cooldown_remaining() > 0


@pytest.mark.parametrize("body", [b'{"statusCode":10001}', b"{}", b'{"itemList":null}'])
def test_business_errors_and_missing_list_never_become_valid_empty(body: bytes) -> None:
    identity = SimpleNamespace(
        ensure_bootstrapped=lambda: True,
        signed_get=lambda *args: TiktokWebResponse(200, body, {}),
        base_params=lambda page: {},
    )
    with pytest.raises(RuntimeError):
        TiktokWebClient(identity=identity)._feed_items(3)


@pytest.mark.parametrize("fails", [False, True])
def test_transport_smoke_isolates_state_and_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    from typer.testing import CliRunner

    from openbiliclaw.cli import app
    from openbiliclaw.sources.tiktok_state import TiktokRequestError

    cfg = Config()
    cfg.data_dir = str(tmp_path)
    captured: list[Path] = []

    def build(config: Config, source: object) -> SimpleNamespace:
        captured.append(config.data_path)
        assert config.data_path != tmp_path
        return SimpleNamespace(
            web=None,
            get_feed=AsyncMock(
                side_effect=TiktokRequestError("rate_limited") if fails else None,
                return_value=[],
            ),
        )

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr("openbiliclaw.api.runtime_context._build_tiktok_client", build)
    monkeypatch.setattr("openbiliclaw.network.set_outbound_proxy", lambda *a, **kw: None)
    result = CliRunner().invoke(app, ["discover-tiktok", "--mode", "feed"])
    assert result.exit_code == int(fails)
    assert ("rate_limited" if fails else "candidate_writes=0") in result.stdout
    assert captured and not captured[0].exists()
    assert not list(tmp_path.iterdir())
