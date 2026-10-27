from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import openbiliclaw.cli as cli
from openbiliclaw.cli import app
from openbiliclaw.config import Config
from openbiliclaw.sources.github_client import GitHubAPIError, GitHubIdentity

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _repository() -> dict[str, Any]:
    payload = json.loads((FIXTURES / "search_repositories_page.json").read_text())
    return dict(payload["items"][0])


def _star_event() -> dict[str, Any]:
    return {
        "event_type": "favorite",
        "source_platform": "github",
        "title": "whiteguo233/OpenBiliClaw",
        "url": "https://github.com/whiteguo233/OpenBiliClaw",
        "author": "whiteguo233",
        "context": "在 GitHub Star 了公开仓库 whiteguo233/OpenBiliClaw",
        "content_id": "repository:1175278883",
        "metadata": {
            "source_platform": "github",
            "content_type": "repository",
            "content_id": "repository:1175278883",
            "repository_id": "1175278883",
            "author_name": "whiteguo233",
            "favorite_count": 3109,
            "engagement_available": ["favorite"],
            "visibility": "public",
        },
    }


class _GitHubClientDouble:
    created_tokens: list[str | None] = []

    def __init__(self, *, token: str | None = None, **_: Any) -> None:
        self.token = token
        self.created_tokens.append(token)

    async def __aenter__(self) -> _GitHubClientDouble:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


def _fetch_result(*, complete: bool = True, events: list[dict[str, Any]] | None = None) -> Any:
    selected_events = list(events if events is not None else [_star_event()])
    return SimpleNamespace(
        events=selected_events,
        scope_complete=complete,
        affirmative_empty=complete and not selected_events,
        terminal_evidence="link_exhausted" if complete else "page_cap",
        pages_fetched=1,
        rows_seen=len(selected_events),
        duplicates=0,
        rejected_private=0,
        rejected_malformed=0,
    )


def test_github_history_conversion_keeps_repository_provenance() -> None:
    rows = cli._github_events_to_history_items([_star_event()])

    assert rows == [
        {
            "title": "whiteguo233/OpenBiliClaw",
            "url": "https://github.com/whiteguo233/OpenBiliClaw",
            "author": "whiteguo233",
            "event_type": "favorite",
            "context": "在 GitHub Star 了公开仓库 whiteguo233/OpenBiliClaw",
            "metadata": _star_event()["metadata"],
            "source_platform": "github",
        }
    ]


def test_fetch_github_init_data_ignores_generic_github_env_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.sources.github.username = "whiteguo233"
    config.sources.github.access_token = ""
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-used")
    monkeypatch.setenv("GH_TOKEN", "must-not-be-used-either")
    monkeypatch.delenv("OPENBILICLAW_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    _GitHubClientDouble.created_tokens.clear()
    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.GitHubClient",
        _GitHubClientDouble,
    )

    async def _identity(client: Any, *, username: object = "") -> GitHubIdentity:
        assert client.token is None
        assert username == "whiteguo233"
        return GitHubIdentity(login="whiteguo233", user_id=3350171, evidence="accepted")

    async def _fetch(client: Any, **kwargs: Any) -> Any:
        assert client.token is None
        assert kwargs["username"] == "whiteguo233"
        return _fetch_result()

    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.resolve_github_bootstrap_identity",
        _identity,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.github.fetch_github_public_starred_events",
        _fetch,
    )

    events, counts, status = asyncio.run(cli._fetch_github_init_data(username="whiteguo233"))

    assert status == "complete"
    assert events == [_star_event()]
    assert counts["identity_id"] == 3350171
    assert _GitHubClientDouble.created_tokens == [None]


def test_fetch_github_init_data_preserves_partial_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.sources.github.username = "whiteguo233"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.GitHubClient",
        _GitHubClientDouble,
    )

    async def _identity(*_: Any, **__: Any) -> GitHubIdentity:
        return GitHubIdentity(login="whiteguo233", user_id=3350171, evidence="accepted")

    async def _fetch(*_: Any, **__: Any) -> Any:
        return _fetch_result(complete=False)

    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.resolve_github_bootstrap_identity",
        _identity,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.github.fetch_github_public_starred_events",
        _fetch,
    )

    events, counts, status = asyncio.run(cli._fetch_github_init_data(username="whiteguo233"))

    assert status == "partial"
    assert events == [_star_event()]
    assert counts["terminal_evidence"] == "page_cap"


def test_persist_init_source_flags_saves_github_identity_and_explicit_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    saved: list[Config] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr("openbiliclaw.config.save_config", saved.append)

    cli._persist_init_source_enabled_flags(
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_github=True,
        github_username="whiteguo233",
        github_token="explicit-pat",
    )

    assert saved == [config]
    assert config.sources.github.enabled is True
    assert config.sources.github.username == "whiteguo233"
    assert config.sources.github.access_token == "explicit-pat"


def test_run_guided_init_github_only_persists_analyzes_and_builds_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _star_event()
    state: dict[str, Any] = {"propagated": [], "analyzed": [], "history": []}

    async def _fetch(**_: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        return [event], {"repositories": 1, "scope_complete": True}, "complete"

    monkeypatch.setattr(cli, "_fetch_github_init_data", _fetch)
    monkeypatch.setattr(cli, "_maybe_update_init_source_shares", lambda counts: None)

    class _Memory:
        async def propagate_event(self, received: dict[str, Any]) -> None:
            state["propagated"].append(received)

    class _Soul:
        async def analyze_events(self, events: list[dict[str, Any]], **_: Any) -> None:
            state["analyzed"] = list(events)

        async def build_initial_profile(self, history: list[dict[str, Any]]) -> dict[str, bool]:
            state["history"] = list(history)
            return {"ok": True}

    async def _backfill(*_: Any, **__: Any) -> int:
        return 1

    result = asyncio.run(
        cli.run_guided_init(
            client=None,
            memory=_Memory(),
            soul_engine=_Soul(),
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_github=True,
            github_username="whiteguo233",
            target_pool_count=1,
            discover_backfill=_backfill,
        )
    )

    assert result.github_status == "complete"
    assert result.github_events == [event]
    assert state["propagated"] == [event]
    assert state["analyzed"] == [event]
    assert state["history"][0]["source_platform"] == "github"


def test_run_guided_init_github_identity_mismatch_has_stable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fetch(**_: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        raise GitHubAPIError("identity_mismatch", "secret-safe mismatch")

    monkeypatch.setattr(cli, "_fetch_github_init_data", _fetch)

    with pytest.raises(cli.GuidedInitError) as exc_info:
        asyncio.run(
            cli.run_guided_init(
                client=None,
                memory=object(),
                soul_engine=object(),
                favorite_limit=0,
                follow_limit=0,
                include_bili=False,
                include_xhs=False,
                include_dy=False,
                include_yt=False,
                include_github=True,
                github_username="different-account",
                target_pool_count=1,
                discover_backfill=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.reason == "github_identity_mismatch"
    assert "numeric user id" in exc_info.value.message


def test_run_guided_init_isolates_github_failure_when_reddit_has_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reddit_event = {
        "event_type": "favorite",
        "title": "A useful Reddit post",
        "url": "https://www.reddit.com/r/python/comments/abc/example/",
        "context": "在 Reddit 收藏了帖子",
        "metadata": {"source_platform": "reddit", "content_id": "t3_abc"},
    }
    state: dict[str, Any] = {"propagated": [], "analyzed": [], "history": []}

    async def _github_fetch(**_: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        raise GitHubAPIError("unauthorized", "secret-safe denial", status_code=401)

    def _collector(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], dict[str, int], str]:
        return [reddit_event], {"reddit_saved": 1}, "ok"

    monkeypatch.setattr(cli, "_fetch_github_init_data", _github_fetch)
    monkeypatch.setattr(cli, "_enqueue_reddit_bootstrap_task", lambda **_: None)
    monkeypatch.setattr(cli, "_collect_reddit_bootstrap_events", _collector)
    monkeypatch.setattr(cli, "_maybe_update_init_source_shares", lambda counts: None)

    class _Memory:
        async def propagate_event(self, received: dict[str, Any]) -> None:
            state["propagated"].append(received)

    class _Soul:
        async def analyze_events(self, events: list[dict[str, Any]], **_: Any) -> None:
            state["analyzed"] = list(events)

        async def build_initial_profile(self, history: list[dict[str, Any]]) -> dict[str, bool]:
            state["history"] = list(history)
            return {"ok": True}

    async def _backfill(*_: Any, **__: Any) -> int:
        return 1

    result = asyncio.run(
        cli.run_guided_init(
            client=None,
            memory=_Memory(),
            soul_engine=_Soul(),
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_reddit=True,
            include_github=True,
            github_token="expired-pat",
            target_pool_count=1,
            discover_backfill=_backfill,
        )
    )

    assert result.github_status == "partial"
    assert result.github_scope_counts["terminal_evidence"] == "isolated_source_failure"
    assert result.github_scope_counts["error_code"] == "github_token_rejected"
    assert result.github_events == []
    assert result.reddit_status == "ok"
    assert state["propagated"] == [reddit_event]
    assert state["analyzed"] == [reddit_event]
    assert state["history"][0]["source_platform"] == "reddit"


def test_fetch_github_defaults_to_preview_without_local_or_llm_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.sources.github.username = "whiteguo233"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.GitHubClient",
        _GitHubClientDouble,
    )

    async def _identity(*_: Any, **__: Any) -> GitHubIdentity:
        return GitHubIdentity(login="whiteguo233", user_id=3350171, evidence="accepted")

    async def _fetch(*_: Any, **__: Any) -> Any:
        return _fetch_result()

    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.resolve_github_bootstrap_identity",
        _identity,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.github.fetch_github_public_starred_events",
        _fetch,
    )
    monkeypatch.setattr(
        cli,
        "_write_events_to_memory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local write")),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_init_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM path")),
    )

    result = CliRunner().invoke(app, ["fetch-github"])

    assert result.exit_code == 0, result.output
    assert "0（只读预览）" in result.output
    assert "upstream-state-unchanged" in result.output


@pytest.mark.parametrize(
    ("command", "expected_fragment"),
    [
        (["discover-github", "agent"], "is:public"),
        (["discover-github-ranked"], "is:public"),
        (["discover-github-latest"], "is:public"),
    ],
)
def test_github_discovery_smokes_enforce_public_query(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    expected_fragment: str,
) -> None:
    config = Config()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)

    class _SearchClient(_GitHubClientDouble):
        async def search_repositories(self, query: str, **kwargs: Any) -> Any:
            calls.append({"query": query, **kwargs})
            return SimpleNamespace(
                items=[_repository()],
                incomplete_results=False,
                search_capped=False,
                next_page=None,
            )

    monkeypatch.setattr("openbiliclaw.sources.github_client.GitHubClient", _SearchClient)
    result = CliRunner().invoke(app, command)

    assert result.exit_code == 0, result.output
    assert expected_fragment in calls[0]["query"]
    assert "is:private" not in calls[0]["query"]
    assert "本地写入" in result.output
    assert "LLM" in result.output
    assert "upstream-state-unchanged" in result.output


def test_github_discovery_smoke_reports_rejected_terminal_rows_as_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    private_repository = _repository()
    private_repository["private"] = True
    private_repository["visibility"] = "private"
    calls: list[str] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)

    class _SearchClient(_GitHubClientDouble):
        async def search_repositories(self, query: str, **kwargs: Any) -> Any:
            del kwargs
            calls.append(query)
            return SimpleNamespace(
                items=[private_repository],
                incomplete_results=False,
                search_capped=False,
                next_page=None,
            )

    monkeypatch.setattr("openbiliclaw.sources.github_client.GitHubClient", _SearchClient)

    result = CliRunner().invoke(
        app,
        ["discover-github", "is:private local agent", "--limit", "3"],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["local agent in:name,description,readme is:public fork:false"]
    assert "partial" in result.output
    assert "拒绝私有 / 异常行" in result.output
    assert "1" in result.output


def test_formal_discover_accepts_github_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        cli,
        "_run_github_discovery",
        lambda *, limit, force=False: calls.append((limit, force)),
    )

    result = CliRunner().invoke(app, ["discover", "--source", "gh", "--limit", "7", "--force"])

    assert result.exit_code == 0, result.output
    assert calls == [(7, True)]


def test_init_help_exposes_github_opt_in_and_identity_flags() -> None:
    from typer.main import get_command

    init_command = get_command(app).commands["init"]
    option_names = {
        option for param in init_command.params for option in getattr(param, "opts", [])
    }

    assert "--yes-github" in option_names
    assert "--no-github" in option_names
    assert "--github-username" in option_names
    assert "--github-token" in option_names


def test_init_command_isolates_invalid_github_pat_in_mixed_source_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(cli, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(
        cli,
        "_get_runtime_database",
        lambda: SimpleNamespace(max_llm_usage_id=lambda: None),
    )
    monkeypatch.setattr(cli, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli, "_build_soul_engine", lambda: object())
    monkeypatch.setattr(
        cli,
        "_build_bilibili_client",
        lambda: (_ for _ in ()).throw(AssertionError("Bilibili must stay disabled")),
    )
    monkeypatch.setattr(cli, "_ask_network_binding", lambda: False)
    monkeypatch.setattr(cli, "_persist_api_host_choice", lambda **_: None)
    monkeypatch.setattr(cli, "_maybe_setup_password_in_init", lambda **_: None)
    monkeypatch.setattr(cli, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli, "_print_init_cost_summary", lambda *_: None)

    class _Client(_GitHubClientDouble):
        @property
        def has_access_token(self) -> bool:
            return bool(self.token)

    async def _reject(*_: Any, **__: Any) -> GitHubIdentity:
        raise GitHubAPIError("unauthorized", "secret-safe denial", status_code=401)

    monkeypatch.setattr("openbiliclaw.sources.github_client.GitHubClient", _Client)
    monkeypatch.setattr(
        "openbiliclaw.sources.github_client.resolve_github_bootstrap_identity",
        _reject,
    )

    captured: dict[str, Any] = {}
    persisted: dict[str, Any] = {}
    reddit_event = {
        "event_type": "favorite",
        "title": "Reddit saved",
        "metadata": {"source_platform": "reddit"},
    }

    def _persist(**kwargs: Any) -> None:
        persisted.update(kwargs)

    async def _guided(**kwargs: Any) -> cli.InitResult:
        captured.update(kwargs)
        return cli.InitResult(
            history=[],
            favorites_data=[],
            following_data=[],
            events=[reddit_event],
            bilibili_event_count=0,
            xhs_events=[],
            xhs_scope_counts={},
            xhs_status="skipped",
            dy_events=[],
            dy_scope_counts={},
            dy_status="skipped",
            yt_events=[],
            yt_scope_counts={},
            yt_status="skipped",
            zhihu_events=[],
            zhihu_scope_counts={},
            zhihu_status="skipped",
            reddit_events=[reddit_event],
            reddit_scope_counts={"reddit_saved": 1},
            reddit_status="ok",
            profile_data={"ok": True},
            discovered_count=0,
            discovery_error=False,
            discover_exc=None,
        )

    monkeypatch.setattr(cli, "_persist_init_source_enabled_flags", _persist)
    monkeypatch.setattr(cli, "run_guided_init", _guided)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--no-bilibili",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
            "--no-x",
            "--no-zhihu",
            "--yes-reddit",
            "--no-bangumi",
            "--no-linuxdo",
            "--no-v2ex",
            "--no-weibo",
            "--yes-github",
            "--github-token",
            "expired-request-pat",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["include_reddit"] is True
    assert captured["include_github"] is False
    assert captured["github_username"] == ""
    assert captured["github_token"] == ""
    assert persisted["include_github"] is True
    assert persisted["github_token"] == ""


def test_keyword_inspiration_dry_run_uses_and_closes_github_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.bilibili.enabled = False
    config.sources.github.enabled = True
    config.discovery.inspiration_search_enabled = True
    config.discovery.inspiration_search_backends = ("platform_sources",)
    database = Database(tmp_path / "github-inspiration-cli.db")
    database.initialize()
    calls: list[str] = []
    closed: list[bool] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["token"] is None

        async def search_repositories(self, query: str, **kwargs: Any) -> Any:
            del kwargs
            calls.append(query)
            return SimpleNamespace(items=[_repository()], next_page=None)

        async def aclose(self) -> None:
            closed.append(True)

    class _Soul:
        async def get_profile(self) -> dict[str, Any]:
            return {"preferences": {"interests": [{"name": "local agent"}]}}

    class _LLMService:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

    class _Planner:
        def __init__(self, **kwargs: Any) -> None:
            self.provider = kwargs["inspiration_provider"]

        async def preview_inspiration_keywords(
            self,
            platforms: list[str],
            **kwargs: Any,
        ) -> dict[str, Any]:
            del kwargs
            rows = await self.provider.search("local agent", limit=1)
            return {
                "platforms": platforms,
                "grounding": [
                    {"title": row.title, "url": row.url, "highlights": row.highlights}
                    for row in rows
                ],
            }

    monkeypatch.delenv("OPENBILICLAW_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr("openbiliclaw.sources.github_client.GitHubClient", _Client)
    monkeypatch.setattr("openbiliclaw.llm.service.LLMService", _LLMService)
    monkeypatch.setattr(
        "openbiliclaw.llm.service.module_overrides_from_config",
        lambda _config: {},
    )
    monkeypatch.setattr("openbiliclaw.runtime.keyword_planner.KeywordPlanner", _Planner)
    monkeypatch.setattr(cli, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli, "_get_runtime_database", lambda: database)
    monkeypatch.setattr(cli, "_build_registry", lambda: object())
    monkeypatch.setattr(cli, "_build_usage_recorder", lambda: object())
    monkeypatch.setattr(cli, "_build_llm_concurrency_gate", lambda: object())
    monkeypatch.setattr(cli, "_build_soul_engine", _Soul)

    result = CliRunner().invoke(
        app,
        ["keyword-inspiration-dry-run", "--platform", "github", "--limit", "1"],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["local agent in:name,description,readme is:public fork:false"]
    assert closed == [True]
    assert '"platforms": [\n    "github"' in result.output
    assert "https://github.com/whiteguo233/OpenBiliClaw" in result.output
