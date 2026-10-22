"""Guided-init wiring tests for the TikTok full-source upgrade.

Mirrors the Douyin guided-init tests in ``tests/test_cli.py``: init
admission (``_INIT_SOURCE_ORDER`` / ``_PLATFORM_SOURCE_FIELDS`` /
``_select_init_platforms``), the CLI ``--yes-tiktok`` / ``--no-tiktok``
options, the enqueue/collect helpers, and event propagation into
preference analysis and the initial profile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import app

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_tiktok_is_a_guided_init_source() -> None:
    """The exclusion is lifted: tiktok is a legal guided-init platform."""
    from openbiliclaw.api.app import _INIT_SOURCE_ORDER, _select_init_platforms
    from openbiliclaw.runtime.init_prereqs import _PLATFORM_SOURCE_FIELDS

    assert "tiktok" in _INIT_SOURCE_ORDER
    assert "tiktok" in _PLATFORM_SOURCE_FIELDS
    # Explicit per-run selection admits tiktok even when config-disabled.
    assert _select_init_platforms(set(), {"tiktok"}) == {"tiktok"}
    # Legacy no-selection path admits a config-enabled tiktok.
    assert _select_init_platforms({"tiktok"}, None) == {"tiktok"}


def test_tiktok_guided_init_flag_is_enabled_in_shared_source_status() -> None:
    shared = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text()
    assert "tiktok: Object.freeze({ guidedInit: true })" in shared


def test_ask_tiktok_inclusion_defaults_no_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-interactive terminals (CI / piped stdin) skip TikTok silently."""
    from openbiliclaw.cli import _ask_tiktok_inclusion

    monkeypatch.delenv("OPENBILICLAW_NO_TIKTOK", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False)
    assert _ask_tiktok_inclusion() is False


def test_ask_tiktok_inclusion_env_skip_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.cli import _ask_tiktok_inclusion

    monkeypatch.setenv("OPENBILICLAW_NO_TIKTOK", "1")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    assert _ask_tiktok_inclusion() is False


def test_persist_init_source_enabled_flags_includes_tiktok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _persist_init_source_enabled_flags
    from openbiliclaw.config import Config

    config = Config()
    saved: list[Any] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr("openbiliclaw.config.save_config", lambda cfg: saved.append(cfg))

    _persist_init_source_enabled_flags(
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_tiktok=True,
    )

    assert config.sources.tiktok.enabled is True
    assert saved == [config]


def test_enqueue_tiktok_bootstrap_task_uses_shared_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI helper delegates to source_bootstrap.enqueue_tiktok_bootstrap."""
    from openbiliclaw.sources.source_bootstrap import BootstrapEnqueueResult

    captured: dict[str, Any] = {}

    class FakeDatabase:
        conn = object()

    def fake_enqueue(database: object, **kwargs: Any) -> BootstrapEnqueueResult:
        captured["database"] = database
        captured["kwargs"] = kwargs
        return BootstrapEnqueueResult(task_id="tt-task-1", created=True, reason="created")

    kicks: list[str] = []
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(
        "openbiliclaw.sources.source_bootstrap.enqueue_tiktok_bootstrap",
        fake_enqueue,
    )
    monkeypatch.setattr(cli_module, "_kick_task_dispatcher", kicks.append)

    task_id = cli_module._enqueue_tiktok_bootstrap_task()

    assert task_id == "tt-task-1"
    assert captured["kwargs"]["force"] is False
    assert captured["kwargs"]["incremental"] is False
    assert kicks == ["tiktok"]


def test_kick_task_dispatcher_supports_tiktok(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tiktok source key reaches the daemon kick endpoint."""
    import urllib.request

    urls: list[str] = []

    class FakeResponse:
        def close(self) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float = 0) -> FakeResponse:
        urls.append(str(req.full_url))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cli_module._kick_task_dispatcher("tiktok")
    assert urls == ["http://127.0.0.1:8420/api/sources/tiktok/kick"]


class _FakeTiktokQueue:
    """Queue stub returning a fixed row for the collect helper."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def get(self, _task_id: str) -> dict[str, Any]:
        return self._row


class _FakeDatabase:
    conn = object()


def _stub_tiktok_collect(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, Any],
) -> None:
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: _FakeDatabase())
    monkeypatch.setattr(
        "openbiliclaw.sources.tiktok_tasks.TiktokTaskQueue",
        lambda _db: _FakeTiktokQueue(row),
    )


def test_collect_tiktok_bootstrap_events_returns_skipped_for_no_task_id() -> None:
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    events, counts, status = _collect_tiktok_bootstrap_events(None, max_wait_seconds=2)
    assert events == []
    assert counts == {}
    assert status == "skipped"


def test_collect_tiktok_bootstrap_events_extracts_videos_from_completed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed task converts videos into canonical events (like/favorite/
    view/follow) and surfaces per-scope counts."""
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    _stub_tiktok_collect(
        monkeypatch,
        {
            "id": "task-1",
            "status": "completed",
            "result_json": json.dumps(
                {
                    "videos": [
                        {
                            "scope": "tt_collect",
                            "title": "收藏的视频",
                            "url": "https://www.tiktok.com/@c/video/a",
                            "video_id": "a",
                            "author": "u",
                        },
                        {
                            "scope": "tt_like",
                            "title": "点赞的视频",
                            "url": "https://www.tiktok.com/@c/video/b",
                            "video_id": "b",
                        },
                        {
                            "scope": "tt_history",
                            "title": "看过的视频",
                            "url": "https://www.tiktok.com/@c/video/c",
                            "video_id": "c",
                        },
                        {
                            "scope": "tt_follow",
                            "title": "关注的创作者",
                            "url": "https://www.tiktok.com/@d",
                            "creator_sec_uid": "d",
                        },
                    ],
                    "scope_counts": {
                        "tt_like": 1,
                        "tt_collect": 1,
                        "tt_history": 1,
                        "tt_follow": 1,
                    },
                }
            ),
        },
    )

    events, counts, status = _collect_tiktok_bootstrap_events("task-1", max_wait_seconds=0)
    assert status == "ok"
    assert [e["event_type"] for e in events] == ["favorite", "like", "view", "follow"]
    assert all(e["metadata"]["source_platform"] == "tiktok" for e in events)
    assert counts == {"tt_like": 1, "tt_collect": 1, "tt_history": 1, "tt_follow": 1}


def test_collect_tiktok_bootstrap_events_backfills_scope_counts_from_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing scope_counts fall back to per-event import_source counting."""
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    _stub_tiktok_collect(
        monkeypatch,
        {
            "id": "task-1",
            "status": "completed",
            "result_json": json.dumps(
                {
                    "videos": [
                        {
                            "scope": "tt_history",
                            "title": "看过的",
                            "url": "https://www.tiktok.com/@c/video/h1",
                            "video_id": "h1",
                        },
                        {
                            "scope": "tt_history",
                            "title": "看过的二",
                            "url": "https://www.tiktok.com/@c/video/h2",
                            "video_id": "h2",
                        },
                    ],
                }
            ),
        },
    )

    events, counts, status = _collect_tiktok_bootstrap_events("task-1", max_wait_seconds=0)
    assert status == "ok"
    assert counts["tt_history"] == 2
    assert counts["tt_like"] == 0
    assert len(events) == 2


def test_collect_tiktok_bootstrap_events_surfaces_terminal_degraded_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    _stub_tiktok_collect(
        monkeypatch,
        {
            "id": "task-degraded",
            "status": "completed",
            "result_json": json.dumps(
                {
                    "status": "degraded",
                    "videos": [
                        {
                            "scope": "tt_collect",
                            "title": "首屏收藏",
                            "url": "https://www.tiktok.com/@c/video/a",
                            "video_id": "a",
                        }
                    ],
                    "scope_counts": {"tt_collect": 1},
                }
            ),
        },
    )

    events, counts, status = _collect_tiktok_bootstrap_events("task-degraded", max_wait_seconds=0)
    assert status == "degraded"
    assert len(events) == 1
    assert counts["tt_collect"] == 1


def test_collect_tiktok_bootstrap_events_returns_timeout_when_task_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    _stub_tiktok_collect(
        monkeypatch,
        {"id": "task-1", "status": "pending", "result_json": "{}"},
    )

    _events, _counts, status = _collect_tiktok_bootstrap_events("task-1", max_wait_seconds=0.05)
    assert status == "timeout"


def test_collect_tiktok_bootstrap_events_surfaces_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _collect_tiktok_bootstrap_events

    _stub_tiktok_collect(
        monkeypatch,
        {
            "id": "task-1",
            "status": "failed",
            "result_json": json.dumps({"error": "login_wall"}),
        },
    )

    events, counts, status = _collect_tiktok_bootstrap_events("task-1", max_wait_seconds=0)
    assert status == "failed"
    assert events == []
    assert counts == {}


def test_init_yes_tiktok_includes_bootstrap_events_in_analysis_and_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`init --yes-tiktok` runs the tiktok collector and feeds its events to
    preference analysis and the initial profile build."""
    from openbiliclaw.bilibili.auth import AuthStatus
    from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile
    from openbiliclaw.sources.tiktok_tasks import tiktok_bootstrap_videos_to_events

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(self, **_: object) -> list[object]:
            return []

        async def get_following(self, **_: object) -> list[object]:
            return []

    class _FakeMemoryLayer:
        def __init__(self, data: dict[str, object] | None = None) -> None:
            self.data = data or {}

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []
            self.built_history: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            self.built_history.append(history)
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    tiktok_events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_collect",
                "title": "TikTok 收藏咖啡",
                "url": "https://www.tiktok.com/@c/video/tt-fav-1",
                "video_id": "tt-fav-1",
                "author": "TikTok 作者",
            },
            {
                "scope": "tt_history",
                "title": "TikTok 观看历史",
                "url": "https://www.tiktok.com/@c/video/tt-hist-1",
                "video_id": "tt-hist-1",
                "author": "历史作者",
            },
        ]
    )
    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()

    def fail_dy_enqueue(**_: object) -> str | None:
        raise AssertionError("non-interactive init should not enqueue douyin")

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=True: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul, raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(
        cli_module,
        "_run_init_discovery_backfill_async",
        fake_discovery_backfill,
    )
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", fail_dy_enqueue, raising=False)
    monkeypatch.setattr(cli_module, "_enqueue_tiktok_bootstrap_task", lambda: "fake-tt-task-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_tiktok_bootstrap_events",
        lambda task_id, **_: (
            tiktok_events,
            {"tt_like": 0, "tt_collect": 1, "tt_history": 1, "tt_follow": 0},
            "ok",
        ),
    )

    result = CliRunner().invoke(app, ["init", "--no-xhs", "--yes-tiktok"])

    assert result.exit_code == 0, result.output
    assert "TikTok" in result.stdout
    assert fake_soul.analyzed_events
    analyzed_events = fake_soul.analyzed_events[0]
    assert any(
        event.get("metadata", {}).get("source_platform") == "tiktok" for event in analyzed_events
    )
    assert fake_soul.built_history
    built_history = fake_soul.built_history[0]
    assert any(
        item.get("title") == "TikTok 收藏咖啡"
        and item.get("source_platform") == "tiktok"
        and "TikTok收藏" in str(item.get("context", ""))
        for item in built_history
    )
    assert any(
        item.get("title") == "TikTok 观看历史"
        and item.get("source_platform") == "tiktok"
        and "TikTok观看历史" in str(item.get("context", ""))
        for item in built_history
    )


def test_init_no_tiktok_skips_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--no-tiktok` wins even over --yes-tiktok and never enqueues."""
    from openbiliclaw.bilibili.auth import AuthStatus

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(self, **_: object) -> list[object]:
            return []

        async def get_following(self, **_: object) -> list[object]:
            return []

    class _FakeMemoryLayer:
        def __init__(self, data: dict[str, object] | None = None) -> None:
            self.data = data or {}

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            return None

        async def build_initial_profile(self, history: list[dict[str, object]]) -> Any:
            from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile

            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    enqueue_calls: list[bool] = []

    def fake_enqueue_tiktok(**_: object) -> str | None:
        enqueue_calls.append(True)
        return "fake-tt-task-id"

    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=True: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module, "_build_memory_manager", lambda: FakeMemoryManager(), raising=False
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(
        cli_module,
        "_run_init_discovery_backfill_async",
        fake_discovery_backfill,
    )
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(
        cli_module, "_enqueue_tiktok_bootstrap_task", fake_enqueue_tiktok, raising=False
    )

    result = CliRunner().invoke(
        app,
        ["init", "--no-xhs", "--yes-tiktok"],
        env={"OPENBILICLAW_NO_TIKTOK": "1"},
    )

    assert result.exit_code == 0, result.stdout
    assert enqueue_calls == []
    assert "OPENBILICLAW_NO_TIKTOK=1" in result.stdout
