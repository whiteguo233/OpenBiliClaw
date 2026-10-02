"""Focused Web source status, auth and read-only CLI contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw.api.app import create_app
from openbiliclaw.api.source_auth.verify import VERIFY_DEBOUNCE
from openbiliclaw.cli import app
from openbiliclaw.config import Config
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.storage.database import Database


def _save_web(database: Database, recipe_id: str, *, enabled: bool = True) -> None:
    database.save_source_recipe(
        {
            "id": recipe_id,
            "source_type": "web",
            "name": f"Recipe {recipe_id}",
            "strategy": "feed",
            "config": {
                "entry_url": f"https://{recipe_id}.example.com/feed.xml",
                "allowed_origins": [f"https://{recipe_id}.example.com"],
                "interval_minutes": 30,
                "max_items": 20,
                "max_pages": 1,
                "detail_fetch_limit": 10,
                "plan_version": 1,
            },
            "enabled": enabled,
        }
    )


def _api_client(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Database, Config]:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config()
    cfg.sources.web.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "web-source-auth.db")
    database.initialize()
    return (
        TestClient(create_app(memory_manager=object(), database=database, soul_engine=object())),
        database,
        cfg,
    )


def test_web_status_separates_global_recipe_and_observed_health(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, cfg = _api_client(tmp_path, monkeypatch)

    no_recipe = client.get("/api/sources/status").json()["web"]
    assert no_recipe["enabled"] is True
    assert (no_recipe["state"], no_recipe["logged_in"]) == ("no_auth", True)
    assert no_recipe["discovery_state"] == "unverified"
    assert no_recipe["auth"]["capabilities"]["discover"] == {
        "mode": "anonymous",
        "required": True,
        "ready": False,
        "state": "unavailable",
        "detail": (
            "未配置启用的 Web recipe；请先添加并启用一个公开 Feed、Sitemap 或静态列表 recipe。"
        ),
    }

    database.save_source_recipe(
        {
            "id": "legacy-extractor",
            "source_type": "web",
            "name": "Legacy extractor",
            "strategy": "web_extract",
            "config": {"url": "https://example.com/article"},
            "enabled": True,
        }
    )
    legacy_only = client.get("/api/sources/status").json()["web"]
    assert legacy_only["discovery_state"] == "unverified"
    assert legacy_only["auth"]["capabilities"]["discover"]["ready"] is False

    _save_web(database, "alpha")
    _save_web(database, "beta")
    never_run = client.get("/api/sources/status").json()["web"]
    assert never_run["discovery_state"] == "unverified"
    assert never_run["auth"]["capabilities"]["discover"]["ready"] is True

    claimed = {row["id"]: row for row in database.claim_due_web_recipes(limit=2)}
    assert database.complete_web_recipe_fetch(
        "alpha",
        lease_token=claimed["alpha"]["lease_token"],
        status="ok",
        discovered_count=1,
        enqueued_count=1,
        interval_minutes=30,
    )
    mixed = client.get("/api/sources/status").json()["web"]
    assert mixed["discovery_state"] == "partial"
    assert "健康 1" in mixed["detail"]
    assert "待首次运行 1" in mixed["detail"]

    assert database.complete_web_recipe_fetch(
        "beta",
        lease_token=claimed["beta"]["lease_token"],
        status="empty",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=30,
    )
    healthy = client.get("/api/sources/status").json()["web"]
    assert healthy["discovery_state"] == "ready"

    cfg.sources.web.enabled = False
    disabled = client.get("/api/sources/status").json()["web"]
    assert disabled["enabled"] is False
    assert disabled["discovery_state"] == "disabled"
    assert disabled["auth"]["capabilities"]["discover"]["ready"] is False


def test_web_status_aggregates_failure_and_rate_limit_without_auth_overclaim(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, _cfg = _api_client(tmp_path, monkeypatch)
    _save_web(database, "failed")
    _save_web(database, "limited")
    claimed = {row["id"]: row for row in database.claim_due_web_recipes(limit=2)}
    assert database.complete_web_recipe_fetch(
        "failed",
        lease_token=claimed["failed"]["lease_token"],
        status="failed",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=30,
        error_code="http_500",
        scope_complete=False,
    )
    failed = client.get("/api/sources/status").json()["web"]
    assert failed["discovery_state"] == "error"
    assert failed["state"] == "no_auth"

    assert database.complete_web_recipe_fetch(
        "limited",
        lease_token=claimed["limited"]["lease_token"],
        status="rate_limited",
        discovered_count=0,
        enqueued_count=0,
        interval_minutes=30,
        retry_after_seconds=300,
        error_code="http_429",
        scope_complete=False,
    )
    limited = client.get("/api/sources/status").json()["web"]
    assert limited["discovery_state"] == "rate_limited"
    assert limited["feed_paused"] is True
    assert limited["auth"]["auth_required"] is False


def test_web_credentials_are_absent_and_writes_are_explicitly_rejected(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _database, _cfg = _api_client(tmp_path, monkeypatch)

    credential = client.get("/api/sources/credentials").json()["web"]
    assert credential["available"] is False
    assert credential["value"] == ""
    assert credential["form"]["kind"] == "none"
    assert credential["form"]["label"] == "Web"

    rejected = client.post(
        "/api/sources/web/credential",
        json={"kind": "cookie", "value": "session=secret"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["accepted"] is False
    assert rejected.json()["persisted"] is False
    assert rejected.json()["error_code"] == "credential_not_writable"


def test_web_verify_is_local_and_indeterminate(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.api.source_auth.verify as verify_module

    client, _database, _cfg = _api_client(tmp_path, monkeypatch)
    VERIFY_DEBOUNCE.clear()

    async def fail_live_probe(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Web verify attempted outbound I/O")

    monkeypatch.setattr(verify_module, "run_live_probe", fail_live_probe)
    response = client.post("/api/sources/web/verify")

    assert response.status_code == 200
    assert response.json()["outcome"] == "indeterminate"
    assert response.json()["auth"]["verify_method"] == "none"
    assert "无需登录" in response.json()["message"]


def _patch_cli_bootstrap(monkeypatch: pytest.MonkeyPatch, cfg: Config, database: Any) -> None:
    monkeypatch.setattr(cli_module, "_bootstrap_container_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda **_kw: None)
    monkeypatch.setattr(cli_module, "_sync_outbound_proxy", lambda: None)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)


def test_discover_web_recipe_preview_is_read_only_and_does_not_need_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.sources.web_adapter as adapter_module

    cfg = Config()
    cfg.sources.web.enabled = True
    recipe = {
        "id": "recipe-1",
        "source_type": "web",
        "name": "Example feed",
        "strategy": "feed",
        "config": {
            "entry_url": "https://example.com/feed.xml",
            "allowed_origins": ["https://example.com"],
            "interval_minutes": 30,
            "max_items": 20,
            "max_pages": 1,
            "detail_fetch_limit": 10,
            "plan_version": 1,
        },
        "target_share": 4,
        "enabled": True,
        "created_by": "user",
        "created_at": "",
        "last_fetched_at": "",
    }

    class ReadOnlyDatabase:
        def get_all_recipes(self) -> list[dict[str, Any]]:
            return [recipe]

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"read-only preview touched database method {name}")

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdapter:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["llm_service"] is None

        async def fetch_result(
            self,
            selected: Any,
            _profile: Any,
            limit: int,
            *,
            state: dict[str, Any],
        ) -> SourceFetchResult:
            calls.append((selected.id, state))
            assert limit == 3
            return SourceFetchResult(
                status="ok",
                items=[
                    DiscoveredContent(
                        content_id="post-1",
                        title="A public post",
                        author_name="Example Author",
                        content_url="https://example.com/post-1",
                        source_platform="web",
                        source_strategy="web-feed",
                    )
                ],
            )

    monkeypatch.setattr(adapter_module, "WebSourceAdapter", FakeAdapter)
    _patch_cli_bootstrap(monkeypatch, cfg, ReadOnlyDatabase())

    result = CliRunner().invoke(
        app,
        ["discover-web", "--recipe-id", "recipe-1", "--limit", "3"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("recipe-1", {})]
    assert "A public post" in result.output
    assert "未写入候选池、seen、cursor、memory" in result.output


def test_discover_web_rejects_missing_or_disabled_recipe_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.sources.web_adapter as adapter_module

    cfg = Config()
    cfg.sources.web.enabled = True

    class ReadOnlyDatabase:
        def get_all_recipes(self) -> list[dict[str, Any]]:
            return []

    class FailAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("missing recipe must not fetch")

    monkeypatch.setattr(adapter_module, "WebSourceAdapter", FailAdapter)
    _patch_cli_bootstrap(monkeypatch, cfg, ReadOnlyDatabase())

    result = CliRunner().invoke(app, ["discover-web", "--recipe-id", "missing"])

    assert result.exit_code == 1
    assert "找不到 Web recipe" in result.output


def test_discover_web_preview_rejects_legacy_web_extract_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.sources.web_adapter as adapter_module

    cfg = Config()
    cfg.sources.web.enabled = True

    class ReadOnlyDatabase:
        def get_all_recipes(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "legacy-extractor",
                    "source_type": "web",
                    "name": "Legacy",
                    "strategy": "web_extract",
                    "config": {"url": "https://example.com/article"},
                    "enabled": True,
                }
            ]

    class FailAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("legacy recipe must not enter public preview")

    monkeypatch.setattr(adapter_module, "WebSourceAdapter", FailAdapter)
    _patch_cli_bootstrap(monkeypatch, cfg, ReadOnlyDatabase())

    result = CliRunner().invoke(
        app,
        ["discover-web", "--recipe-id", "legacy-extractor"],
    )

    assert result.exit_code == 1
    assert "不是正式公开 Web recipe" in result.output


def test_probe_web_is_read_only_and_prints_typed_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.sources.web_probe as probe_module

    cfg = Config()
    cfg.sources.web.enabled = False
    calls: list[tuple[str, dict[str, Any]]] = []

    class Sample:
        title = "A public post [/bold] \x1b[31m"
        author_name = "Alice \x9b31m"
        published_at = "2026-08-12T08:00:00Z"
        content_url = "https://example.com/posts/1"

    class Candidate:
        strategy = "feed"
        confidence = "high"
        discovery_method = "html_alternate"
        entry_url = "https://example.com/feed.xml"
        detail = "[link=https://evil.invalid]trusted.example[/link]\x1b]8;;https://evil.invalid\x07"
        recipe = {"name": "Example feed"}
        sample_items = (Sample(),)

    class Result:
        status = "ready"
        detail = "[/bold] 找到 1 个可编辑方案。\x1b[31m"
        requested_url = "https://example.com/"
        final_url = "https://example.com/"
        page_title = "[/bold] Example\x1b]8;;https://evil.invalid\x07"
        candidates = (Candidate(),)

    async def fake_probe(url: str, **kwargs: Any) -> Result:
        calls.append((url, kwargs))
        return Result()

    monkeypatch.setattr(probe_module, "probe_public_web_url", fake_probe)
    _patch_cli_bootstrap(monkeypatch, cfg, object())

    result = CliRunner().invoke(app, ["probe-web", "https://example.com/"])

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "https://example.com/",
            {
                "timeout_seconds": 15.0,
                "max_response_bytes": 2_097_152,
                "max_redirects": 5,
            },
        )
    ]
    assert "A public post" in result.output
    assert "[/bold] Example" in result.output
    assert "[link=https://evil.invalid]trusted.example[/link]" in result.output
    assert "\x1b" not in result.stdout_bytes.decode("utf-8", errors="replace")
    assert "\x07" not in result.stdout_bytes.decode("utf-8", errors="replace")
    assert "\x9b" not in result.stdout_bytes.decode("utf-8", errors="replace")
    assert "html_alternate" in result.output
    assert "未创建 recipe" in result.output
    assert "尚未保存" in result.output


def test_probe_web_no_match_is_visible_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.sources.web_probe as probe_module

    cfg = Config()

    class Result:
        status = "no_match"
        detail = "没有发现可安全采用的公开入口。"
        requested_url = "https://example.com/"
        final_url = "https://example.com/"
        page_title = "Example"
        candidates: tuple[object, ...] = ()

    async def fake_probe(_url: str, **_kwargs: Any) -> Result:
        return Result()

    monkeypatch.setattr(probe_module, "probe_public_web_url", fake_probe)
    _patch_cli_bootstrap(monkeypatch, cfg, object())

    result = CliRunner().invoke(app, ["probe-web", "https://example.com/"])

    assert result.exit_code == 1
    assert "没有发现可安全采用的公开入口" in result.output
    assert "未创建 recipe" in result.output


def _patch_formal_web_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Config,
    *,
    producer_result: dict[str, object],
    admitted_items: list[DiscoveredContent] | None = None,
) -> dict[str, Any]:
    import openbiliclaw.runtime.web_producer as producer_module
    import openbiliclaw.sources.web_adapter as adapter_module

    calls: dict[str, Any] = {}

    class FakeDatabase:
        pass

    class FakeAdapter:
        def __init__(self, **kwargs: Any) -> None:
            calls["adapter_kwargs"] = kwargs
            calls["adapter"] = self

    class FakePipeline:
        last_admitted_items = list(admitted_items or [])

    class FakeProducer:
        def __init__(self, **kwargs: Any) -> None:
            calls["producer_kwargs"] = kwargs

        async def produce_if_due(self, *, limit: int, force: bool) -> dict[str, object]:
            calls["produce"] = (limit, force)
            return producer_result

    database = FakeDatabase()
    soul_engine = object()
    discovery_engine = object()
    candidate_pipeline = FakePipeline()
    _patch_cli_bootstrap(monkeypatch, cfg, database)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: soul_engine)
    monkeypatch.setattr(cli_module, "_build_discovery_engine", lambda: discovery_engine)

    def build_pipeline(**kwargs: Any) -> FakePipeline:
        calls["pipeline_kwargs"] = kwargs
        return candidate_pipeline

    monkeypatch.setattr(cli_module, "_build_discovery_candidate_pipeline", build_pipeline)
    monkeypatch.setattr(adapter_module, "WebSourceAdapter", FakeAdapter)
    monkeypatch.setattr(producer_module, "WebDiscoveryProducer", FakeProducer)
    calls.update(
        {
            "database": database,
            "soul_engine": soul_engine,
            "discovery_engine": discovery_engine,
            "candidate_pipeline": candidate_pipeline,
        }
    )
    return calls


def test_formal_discover_web_uses_adapter_producer_and_shared_candidate_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config()
    cfg.sources.web.enabled = True
    admitted = DiscoveredContent(
        content_id="web:item-1",
        title="A newly admitted Web article",
        author_name="Public Author",
        content_url="https://example.com/articles/1",
        source_platform="web",
        source_strategy="web-feed",
    )
    calls = _patch_formal_web_dependencies(
        monkeypatch,
        cfg,
        producer_result={
            "source_family": "web",
            "reason": "ok",
            "ran": True,
            "made_progress": True,
            "productive_supply": True,
            "discovered": 2,
            "enqueued": 1,
            "recipe_results": {
                "recipe-1": {
                    "status": "ok",
                    "discovered": 2,
                    "new": 1,
                    "enqueued": 1,
                    "error_code": "",
                    "scope_complete": True,
                }
            },
        },
        admitted_items=[admitted],
    )

    result = CliRunner().invoke(
        app,
        ["discover", "--source", "web", "--limit", "7", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert calls["produce"] == (7, True)
    assert calls["adapter_kwargs"] == {
        "llm_service": None,
        "request_timeout_seconds": 15.0,
        "max_response_bytes": 2_097_152,
        "max_redirects": 5,
    }
    assert calls["pipeline_kwargs"] == {
        "config": cfg,
        "database": calls["database"],
        "discovery_engine": calls["discovery_engine"],
    }
    assert calls["producer_kwargs"] == {
        "database": calls["database"],
        "adapter": calls["adapter"],
        "candidate_pipeline": calls["candidate_pipeline"],
        "enabled": True,
        "lease_seconds": 300,
        "request_timeout_seconds": 15,
        "soul_engine": calls["soul_engine"],
    }
    assert "正式 producer" in result.output
    assert "A newly admitted Web article" in result.output
    assert "recipe-1:ok" in result.output


@pytest.mark.parametrize(
    ("reason", "recipe_results", "expected_exit", "expected_text"),
    [
        ("no_recipes", {}, 1, "没有启用的 Web recipe"),
        ("throttled", {}, 0, "Web discovery 尚未到期"),
        ("valid_empty", {"feed": {"status": "empty"}}, 0, "有效完成"),
        (
            "partial",
            {
                "feed": {
                    "status": "degraded",
                    "discovered": 2,
                    "new": 1,
                    "enqueued": 1,
                    "error_code": "schema_changed",
                    "scope_complete": False,
                }
            },
            1,
            "schema_changed",
        ),
        (
            "ok",
            {
                "good": {"status": "ok", "enqueued": 1},
                "limited": {
                    "status": "rate_limited",
                    "enqueued": 0,
                    "error_code": "http_429",
                    "scope_complete": False,
                },
            },
            1,
            "http_429",
        ),
        (
            "error",
            {
                "feed": {
                    "status": "failed",
                    "discovered": 0,
                    "new": 0,
                    "enqueued": 0,
                    "error_code": "robots_unavailable",
                    "scope_complete": False,
                }
            },
            1,
            "robots_unavailable",
        ),
    ],
)
def test_formal_discover_web_surfaces_scheduler_outcomes_honestly(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    recipe_results: dict[str, object],
    expected_exit: int,
    expected_text: str,
) -> None:
    cfg = Config()
    cfg.sources.web.enabled = True
    _patch_formal_web_dependencies(
        monkeypatch,
        cfg,
        producer_result={
            "source_family": "web",
            "reason": reason,
            "ran": reason not in {"no_recipes", "throttled"},
            "made_progress": reason == "partial",
            "productive_supply": reason == "partial",
            "discovered": 2 if reason == "partial" else 0,
            "enqueued": 1 if reason == "partial" else 0,
            "recipe_results": recipe_results,
        },
    )

    result = CliRunner().invoke(app, ["discover", "--source", "web"])

    assert result.exit_code == expected_exit, result.output
    assert expected_text in result.output
    if reason in {"ok", "partial"}:
        assert "已成功入池的候选会保留" in result.output


def test_formal_discover_web_disabled_fails_before_runtime_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config()
    cfg.sources.web.enabled = False
    _patch_cli_bootstrap(monkeypatch, cfg, object())
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: (_ for _ in ()).throw(AssertionError("disabled Web must not build discovery")),
    )

    result = CliRunner().invoke(app, ["discover", "--source", "web", "--force"])

    assert result.exit_code == 1
    assert "Web 公开发现未启用" in result.output
