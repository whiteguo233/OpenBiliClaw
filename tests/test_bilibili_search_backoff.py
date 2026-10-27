"""Tests for cross-process sharing of the Bilibili search backoff state."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.bilibili import search_backoff
from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.bilibili.search_backoff import configure_search_backoff_state_path

if TYPE_CHECKING:
    from pathlib import Path

_STATE_FILENAME = "bilibili_search_backoff.json"


@pytest.fixture(autouse=True)
def _reset_bilibili_search_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_level", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_voucher_block_streak", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_dom_fallback_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_activated_mono", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_activated_wall", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_probe_used", False)


def _simulate_fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the in-process ClassVars, as a newly spawned process would see them."""
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_level", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_voucher_block_streak", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_dom_fallback_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_activated_mono", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_activated_wall", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_probe_used", False)


def _state_file(tmp_path: Path) -> Path:
    return tmp_path / _STATE_FILENAME


def test_412_cooldown_shared_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    BilibiliAPIClient._activate_search_cooldown(
        base_seconds=BilibiliAPIClient._SEARCH_COOLDOWN_412_SECONDS
    )

    _simulate_fresh_process(monkeypatch)

    remaining = BilibiliAPIClient.search_cooldown_remaining()
    assert 590.0 < remaining <= 600.0


def test_dom_fallback_shared_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    BilibiliAPIClient._activate_search_dom_fallback()

    _simulate_fresh_process(monkeypatch)

    remaining = BilibiliAPIClient.search_dom_fallback_remaining()
    assert 170.0 < remaining <= 180.0


def test_voucher_streak_and_level_shared_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    BilibiliAPIClient._record_voucher_block()
    BilibiliAPIClient._record_voucher_block()

    _simulate_fresh_process(monkeypatch)
    BilibiliAPIClient.search_cooldown_remaining()  # pulls the shared state in

    assert BilibiliAPIClient._search_voucher_block_streak == 2


def test_most_conservative_deadline_wins(tmp_path: Path) -> None:
    BilibiliAPIClient._activate_search_cooldown(base_seconds=600.0)
    # A second process that only knows a shorter cooldown must not shorten
    # the persisted one.
    shorter = search_backoff.snapshot_from_monotonic(
        cooldown_until=time.monotonic() + 60.0,
        cooldown_level=1,
        voucher_block_streak=0,
        dom_fallback_until=0.0,
    )
    search_backoff.persist_shared_backoff(shorter)

    shared = search_backoff.read_shared_backoff()
    assert shared is not None
    assert shared.cooldown_until - time.time() > 590.0


def test_reset_counters_propagates_to_disk(tmp_path: Path) -> None:
    BilibiliAPIClient._record_voucher_block()
    BilibiliAPIClient._record_voucher_block()

    BilibiliAPIClient._reset_search_cooldown_backoff()

    shared = search_backoff.read_shared_backoff()
    assert shared is not None
    assert shared.voucher_block_streak == 0
    assert shared.cooldown_level == 0


def test_stale_counters_do_not_trip_fresh_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stale = {
        "version": 1,
        "scope": "global",
        "cooldown_until": 0.0,
        "cooldown_level": 3,
        "voucher_block_streak": 5,
        "dom_fallback_until": 0.0,
        "updated_at": time.time() - 7200.0,
    }
    _state_file(tmp_path).write_text(json.dumps(stale), encoding="utf-8")

    _simulate_fresh_process(monkeypatch)

    duration = BilibiliAPIClient._record_voucher_block()
    assert duration == 0.0
    assert BilibiliAPIClient._search_voucher_block_streak == 1
    assert BilibiliAPIClient._search_cooldown_level == 0


def test_persistence_disabled_matches_inprocess_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_search_backoff_state_path(None)

    BilibiliAPIClient._activate_search_cooldown(base_seconds=600.0)
    assert BilibiliAPIClient.search_cooldown_remaining() > 590.0

    _simulate_fresh_process(monkeypatch)
    assert BilibiliAPIClient.search_cooldown_remaining() == 0.0


def test_unwritable_state_path_fails_open(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    configure_search_backoff_state_path(blocker / "state.json")

    duration = BilibiliAPIClient._activate_search_cooldown(base_seconds=600.0)

    assert duration == 600.0
    assert BilibiliAPIClient.search_cooldown_remaining() > 590.0


def test_state_file_schema(tmp_path: Path) -> None:
    BilibiliAPIClient._activate_search_cooldown(base_seconds=600.0)

    raw = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["scope"] == "global"
    assert raw["cooldown_until"] > time.time() + 590.0
    assert raw["cooldown_level"] == 1
    assert raw["dom_fallback_until"] > time.time() + 590.0
    assert raw["updated_at"] > 0.0
    assert raw["activated_at"] > time.time() - 5.0
    assert raw["last_probe_at"] == 0.0


def _write_half_passed_window(tmp_path: Path) -> None:
    """Persist a cooldown whose midpoint has passed and whose probe is unspent."""
    now = time.time()
    state = {
        "version": 1,
        "scope": "global",
        "cooldown_until": now + 200.0,
        "cooldown_level": 1,
        "voucher_block_streak": 0,
        "dom_fallback_until": now + 200.0,
        "updated_at": now - 400.0,
        "activated_at": now - 400.0,
        "last_probe_at": 0.0,
    }
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")


def test_probe_not_due_before_half_window(tmp_path: Path) -> None:
    BilibiliAPIClient._activate_search_cooldown(base_seconds=600.0)

    assert BilibiliAPIClient._consume_search_recovery_probe() is False


def test_probe_due_after_half_window_and_spent_once(tmp_path: Path) -> None:
    _write_half_passed_window(tmp_path)

    assert BilibiliAPIClient._consume_search_recovery_probe() is True
    # The same window's probe is spent — a second call must not probe again.
    assert BilibiliAPIClient._consume_search_recovery_probe() is False

    shared = search_backoff.read_shared_backoff()
    assert shared is not None
    assert shared.last_probe_at > 0.0


def test_probe_budget_shared_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_half_passed_window(tmp_path)
    assert BilibiliAPIClient._consume_search_recovery_probe() is True

    _simulate_fresh_process(monkeypatch)

    assert BilibiliAPIClient._consume_search_recovery_probe() is False


def test_probe_inmemory_fallback_when_persistence_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_search_backoff_state_path(None)
    now = time.monotonic()
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", now + 100.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_activated_mono", now - 100.0)

    assert BilibiliAPIClient._consume_search_recovery_probe() is True
    assert BilibiliAPIClient._consume_search_recovery_probe() is False


def test_no_probe_without_active_cooldown(tmp_path: Path) -> None:
    assert BilibiliAPIClient._consume_search_recovery_probe() is False


class _ProbeSearchClient:
    """Fake transport serving WBI nav keys and one successful search page."""

    def __init__(self) -> None:
        self.search_calls = 0

    async def get(self, url: str, params: object = None, headers: object = None) -> object:
        if url.endswith("/x/web-interface/nav"):
            return _ProbeResponse(
                {
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                        }
                    },
                }
            )
        self.search_calls += 1
        return _ProbeResponse({"code": 0, "data": {"result": [{"id": 1, "title": "ok"}]}})

    async def aclose(self) -> None:
        return None


class _ProbeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


async def test_successful_probe_clears_cooldown(tmp_path: Path) -> None:
    _write_half_passed_window(tmp_path)
    client = BilibiliAPIClient()
    transport = _ProbeSearchClient()
    client._client = transport  # type: ignore[assignment]

    results = await client.search("编程")

    assert transport.search_calls == 1  # probe is a single-attempt request
    assert len(results) == 1
    assert BilibiliAPIClient.search_cooldown_remaining() == 0.0
    assert BilibiliAPIClient.search_dom_fallback_remaining() == 0.0
    shared = search_backoff.read_shared_backoff()
    assert shared is not None
    assert shared.cooldown_until == 0.0
    assert shared.cooldown_level == 0
