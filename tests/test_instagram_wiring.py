"""Cross-cutting drift locks for Instagram backend/product wiring."""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import (
    _INSTAGRAM_LOGIN_FAILURE_CODES,
    _instagram_login_failure_code,
    _instagram_permission_conflict_detail,
    _instagram_result_login_failure,
    create_app,
)
from openbiliclaw.api.models import (
    SourcesConfigOut,
    SourcesCredentialsResponse,
    SourcesStatusResponse,
)
from openbiliclaw.api.source_auth.providers import SourceAuthContext, auth_instagram
from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.runtime import image_cache
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.runtime.source_policy import (
    DEFAULT_POOL_SOURCE_SHARES,
    DEFAULT_SOURCE_ENABLED,
    SOURCE_ORDER,
    effective_pool_source_shares,
)
from openbiliclaw.sources.bootstrap_state import normalize_source_bootstrap_state
from openbiliclaw.sources.identity_keys import dedup_key
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    infer_source_platform_from_url,
    normalize_source_platform,
    source_family,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _literal_module_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing module assignment: {name}")


def test_instagram_config_defaults_cap_and_round_trip(tmp_path: Path) -> None:
    config = Config()
    assert config.sources.instagram.enabled is False
    assert config.sources.instagram.source_modes == ("topic", "creator")
    assert config.sources.instagram.daily_topic_budget == 60
    assert config.sources.instagram.daily_creator_budget == 30
    assert config.sources.instagram.request_interval_seconds == 3
    assert config.sources.instagram.min_interval_minutes == 10
    assert config.sources.instagram.bootstrap_limit == 300
    assert config.scheduler.pool_source_shares["instagram"] == 1

    config.sources.instagram.enabled = True
    config.sources.instagram.source_modes = ("creator", "topic")
    config.sources.instagram.daily_topic_budget = 19
    config.sources.instagram.daily_creator_budget = 7
    config.sources.instagram.request_interval_seconds = 4
    config.sources.instagram.min_interval_minutes = 21
    config.sources.instagram.bootstrap_limit = 150
    config.scheduler.pool_source_shares["instagram"] = 3
    target = tmp_path / "config.toml"
    save_config(config, target)
    rendered = target.read_text(encoding="utf-8")
    loaded = load_config(target)

    assert "[sources.instagram]" in rendered
    assert 'source_modes = ["creator", "topic"]' in rendered
    assert "instagram = 3" in rendered
    assert loaded.sources.instagram == config.sources.instagram
    assert loaded.scheduler.pool_source_shares["instagram"] == 3

    target.write_text(
        "[sources.instagram]\nrequest_interval_seconds = 99\nbootstrap_limit = 999\n",
        encoding="utf-8",
    )
    clamped = load_config(target)
    assert clamped.sources.instagram.request_interval_seconds == 30
    assert clamped.sources.instagram.bootstrap_limit == 300


def test_instagram_creator_only_config_is_normalized_to_seeded_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.storage.database import Database

    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "test-key"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr("openbiliclaw.config.save_config", lambda *_a, **_kw: None)

    async def _fake_rebuild(self: RuntimeContext, updated: Config) -> None:
        self.config = updated

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
    database = Database(tmp_path / "instagram-api-config.db")
    database.initialize()

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.put(
            "/api/config",
            json={"sources": {"instagram": {"source_modes": ["creator"]}}},
        )

    assert response.status_code == 202, response.text
    assert response.json()["config"]["sources"]["instagram"]["source_modes"] == [
        "topic",
        "creator",
    ]
    assert config.sources.instagram.source_modes == ("topic", "creator")


def test_instagram_source_policy_and_canonical_registry_are_exact() -> None:
    from openbiliclaw.runtime.refresh import _PLATFORM_SOURCE_ORDER

    assert SOURCE_ORDER[-1] == "instagram"
    assert _PLATFORM_SOURCE_ORDER == SOURCE_ORDER
    assert SOURCE_ORDER.count("instagram") == 1
    assert DEFAULT_SOURCE_ENABLED["instagram"] is False
    assert DEFAULT_POOL_SOURCE_SHARES["instagram"] == 1
    assert CANONICAL_SOURCE_FAMILIES[-1] == "instagram"
    assert normalize_source_platform("instagram") == "instagram"
    assert normalize_source_platform("ig") == "instagram"
    assert normalize_source_platform("ins") == "ins"
    assert source_family("instagram-topic", "ig") == "instagram"
    assert infer_source_platform_from_url("https://www.instagram.com/p/DAb_cd-123/") == (
        "instagram"
    )
    assert infer_source_platform_from_url("https://instagr.am/p/DAb_cd-123/") == ""
    assert dedup_key("https://www.instagram.com/reel/DAb_cd-123/") == ("instagram:DAb_cd-123")
    assert dedup_key("https://evilinstagram.com/reel/DAb_cd-123/") == ""

    config = Config()
    config.sources.instagram.enabled = True
    config.scheduler.pool_source_shares["instagram"] = 4
    assert effective_pool_source_shares(config)["instagram"] == 4
    config.sources.instagram.enabled = False
    assert "instagram" not in effective_pool_source_shares(config)


def test_instagram_models_auth_and_explicit_logout_readiness() -> None:
    assert "instagram" in SourcesStatusResponse.model_fields
    assert "instagram" in SourcesCredentialsResponse.model_fields
    assert "instagram" in SourcesConfigOut.model_fields
    assert SourcesConfigOut().instagram.bootstrap_limit == 300

    class LoggedOutDatabase:
        @staticmethod
        def get_instagram_login_state() -> tuple[bool, str]:
            return False, datetime.now(UTC).isoformat()

    contract = auth_instagram(SourceAuthContext(cfg=Config(), database=LoggedOutDatabase()))
    assert contract.auth_required is False
    assert contract.capabilities["discover"].state == "ready"
    assert contract.capabilities["profile"].state == "login_required"
    assert contract.capabilities["bootstrap"].state == "login_required"
    assert contract.verification == "failed"


def test_instagram_keyword_planner_and_merged_prompt_include_instagram() -> None:
    from openbiliclaw.llm.prompts import (
        build_merged_keywords_prompt,
        platform_supply_advantage,
    )
    from openbiliclaw.runtime import keyword_planner

    assert "instagram" in keyword_planner._PLANNER_PLATFORMS
    assert keyword_planner._PLANNER_PLATFORMS[-1] == "instagram"
    assert "instagram" in keyword_planner._PLATFORM_QUERY_STYLES
    assert (
        "photography"
        in keyword_planner._PLATFORM_QUERY_STYLES["instagram"]["native_markers"]
    )
    assert "topic slug" in platform_supply_advantage("instagram")

    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "摄影", "weight": 0.8}]},
        platform_blocks=[{"platform": "instagram", "need": 2}],
    )

    assert "instagram" in messages[0]["content"]
    assert '"platform": "instagram"' in messages[1]["content"]


def test_instagram_bootstrap_state_rejects_raw_account_ids() -> None:
    raw = normalize_source_bootstrap_state({"instagram_account_key": "25025320"})
    assert raw["instagram_account_key"] == ""
    opaque = "sha256:" + "a" * 64
    normalized = normalize_source_bootstrap_state({"instagram_account_key": opaque})
    assert normalized["instagram_account_key"] == opaque


def test_instagram_api_routes_config_and_csrf_are_registered() -> None:
    app_source = _read("src/openbiliclaw/api/app.py")
    auth_source = _read("src/openbiliclaw/api/auth.py")
    for route in (
        "/api/sources/instagram/next-task",
        "/api/sources/instagram/task-result",
        "/api/sources/instagram/kick",
        "/api/sources/instagram/login-state",
    ):
        assert route in app_source
    assert 'sources_data.get("instagram")' in app_source
    assert "InstagramSourceConfigOut(" in app_source
    assert '"instagram": "browser_heartbeat"' in _read("src/openbiliclaw/api/source_auth/verify.py")
    assert '"instagram": CredentialSpec(' in _read("src/openbiliclaw/api/source_auth/write.py")
    assert '"/api/sources/instagram/next-task"' in auth_source
    task_result_route = app_source.split('@app.post("/api/sources/instagram/task-result")', 1)[
        1
    ].split('@app.post("/api/sources/instagram/kick")', 1)[0]
    assert {
        "instagram_login_required",
        "login_required",
        "http_401",
        "http_403",
    } == _INSTAGRAM_LOGIN_FAILURE_CODES
    assert _instagram_login_failure_code("instagram_saved:http_401") == "http_401"
    assert _instagram_login_failure_code("account_identity_missing") == ""
    assert _instagram_login_failure_code("challenge_required") == ""
    assert (
        _instagram_result_login_failure(
            {
                "error": "instagram_liked:item_cap_reached",
                "debug": {"failures": ["instagram_saved:http_403"]},
            }
        )
        == "http_403"
    )
    assert "queue.fail(" not in task_result_route
    assert "queue.stage_final_result_with_freshness(" in task_result_route
    assert task_result_route.count("_mark_source_bootstrap_keys(") == 1
    assert "and profile_update" in task_result_route
    assert "and not smoke_only" in task_result_route
    assert "and not skip_profile" in task_result_route
    assert (
        _instagram_permission_conflict_detail(PermissionError("task_claim_conflict"))
        == "task_claim_conflict"
    )
    assert (
        _instagram_permission_conflict_detail(PermissionError("instagram_account_changed"))
        == "instagram_account_switch_not_supported"
    )


def test_instagram_failed_result_is_staged_and_prefixed_login_code_clears_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-failed.db")
    database.initialize()
    database.set_instagram_login_state(True, "2026-08-12T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "failed",
                "items": [],
                "error": "instagram_saved:http_401",
                "debug": {
                    "failures": ["instagram_saved:http_401"],
                    "response_observed": False,
                },
            },
        )

    assert response.status_code == 200, response.text
    stored = queue.get(task_id)
    assert stored is not None and stored["status"] == "failed"
    canonical = json.loads(str(stored["result_json"]))
    assert canonical["_openbiliclaw_terminal_status"] == "failed"
    assert canonical["error"] == "instagram_saved:http_401"
    assert database.get_instagram_login_state()[0] is False


def test_instagram_staged_replay_does_not_overwrite_newer_logged_out_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import (
        INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD,
        InstagramTaskQueue,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-replay.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None
    queue.stage_final_result(
        task_id,
        terminal_status="empty",
        claim_token=str(claim["claim_token"]),
        items=[],
        scope_complete={
            "instagram_liked": True,
            "instagram_saved": True,
            "instagram_following": True,
        },
        account_id="123456",
        debug={"response_observed": True, "terminal_evidence": "all_scopes_complete"},
    )
    staged = queue.get(task_id)
    assert staged is not None
    staged_payload = json.loads(str(staged["result_json"]))
    evidence_at = datetime.fromisoformat(staged_payload[INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD])
    newer_heartbeat_at = (evidence_at + timedelta(seconds=1)).isoformat()
    database.set_instagram_login_state(False, newer_heartbeat_at)

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "empty",
                "items": [],
                # A stale/reclaimed callback must not replace or invalidate
                # the already-frozen canonical account identity.
                "account_id": "999999",
                "scope_complete": {
                    "instagram_liked": True,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
                "debug": {
                    "response_observed": True,
                    "terminal_evidence": "fresh_callback_ignored",
                },
            },
        )

    assert response.status_code == 200, response.text
    stored = queue.get(task_id)
    assert stored is not None and stored["status"] == "completed"
    assert database.get_instagram_login_state() == (
        False,
        newer_heartbeat_at,
    )


def test_instagram_staged_failed_replay_does_not_overwrite_newer_login_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import (
        INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD,
        InstagramTaskQueue,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-failed-replay.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None
    queue.stage_final_result(
        task_id,
        terminal_status="failed",
        claim_token=str(claim["claim_token"]),
        error="login_required",
        debug={"failures": ["login_required"], "response_observed": False},
    )
    staged = queue.get(task_id)
    assert staged is not None
    staged_payload = json.loads(str(staged["result_json"]))
    evidence_at = datetime.fromisoformat(staged_payload[INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD])
    newer_heartbeat_at = (evidence_at + timedelta(seconds=1)).isoformat()
    database.set_instagram_login_state(True, newer_heartbeat_at)

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "failed",
                "items": [],
                "error": "login_required",
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state() == (
        True,
        newer_heartbeat_at,
    )


def test_instagram_staged_success_replay_repairs_missing_heartbeat_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import (
        INSTAGRAM_BOOTSTRAP_SCOPES,
        INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD,
        InstagramTaskQueue,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-staged-success-heartbeat-repair.db")
    database.initialize()
    database.set_instagram_login_state(False, "2000-01-01T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None
    canonical = queue.stage_final_result(
        task_id,
        terminal_status="empty",
        claim_token=str(claim["claim_token"]),
        items=[],
        scope_complete={scope: True for scope in INSTAGRAM_BOOTSTRAP_SCOPES},
        account_id="123456",
        debug={
            "identity_resolved": True,
            "response_observed": True,
            "terminal_evidence": "all_scopes_complete",
        },
    )
    evidence_at = str(canonical[INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD])

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "empty",
                "account_id": "123456",
                "items": [],
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state() == (True, evidence_at)


def test_instagram_staged_failure_replay_repairs_missing_heartbeat_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import (
        INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD,
        InstagramTaskQueue,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-staged-failure-heartbeat-repair.db")
    database.initialize()
    database.set_instagram_login_state(True, "2000-01-01T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None
    canonical = queue.stage_final_result(
        task_id,
        terminal_status="failed",
        claim_token=str(claim["claim_token"]),
        items=[],
        error="login_required",
        debug={"failures": ["login_required"], "response_observed": False},
    )
    evidence_at = str(canonical[INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD])

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "failed",
                "items": [],
                "error": "login_required",
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state() == (False, evidence_at)


def test_instagram_fresh_bootstrap_identity_evidence_sets_positive_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-fresh-heartbeat.db")
    database.initialize()
    database.set_instagram_login_state(False, "2026-08-12T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "empty",
                "items": [],
                "account_id": "123456",
                "scope_complete": {
                    "instagram_liked": True,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
                "debug": {
                    "identity_resolved": True,
                    "response_observed": True,
                    "terminal_evidence": "all_scopes_complete",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state()[0] is True


def test_instagram_discover_login_code_does_not_clear_personal_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-discover-login-code.db")
    database.initialize()
    database.set_instagram_login_state(True, "2026-08-12T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "failed",
                "items": [],
                "error": "instagram_saved:http_401",
                "debug": {"failures": ["instagram_saved:http_401"]},
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state() == (
        True,
        "2026-08-12T00:00:00+00:00",
    )


def test_instagram_bootstrap_account_mismatch_fails_without_rows_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    from openbiliclaw.sources.instagram_tasks import (
        InstagramTaskQueue,
        instagram_account_key,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-account-mismatch.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "smoke_only": True,
            "purpose": "smoke",
            "expected_account_key": instagram_account_key("111111"),
        },
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "ok",
                "account_id": "222222",
                "items": [
                    {
                        "scope": "instagram_saved",
                        "id": "3712345678901234567",
                        "code": "DAb_cd-123",
                        "content_type": "post",
                        "url": "https://www.instagram.com/p/DAb_cd-123/",
                    }
                ],
                "scope_complete": {
                    "instagram_liked": True,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
                "debug": {
                    "response_observed": True,
                    "terminal_evidence": "all_scopes_complete",
                },
            },
        )
        discover_id = queue.enqueue_with_id(
            "discover",
            {"mode": "topic", "topic": "technology"},
            daily_budget=0,
        )
        assert discover_id is not None
        next_response = client.get("/api/sources/instagram/next-task")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "instagram_account_switch_not_supported"
    stored = queue.get(task_id)
    assert stored is not None and stored["status"] == "failed"
    canonical = json.loads(str(stored["result_json"]))
    assert canonical == {
        "status": "failed",
        "error": "instagram_account_changed",
        "cancelled": True,
    }
    assert next_response.status_code == 200, next_response.text
    assert next_response.json()["id"] == discover_id


def test_instagram_staged_account_mismatch_quarantines_without_rewriting_canonical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import (
        InstagramTaskQueue,
        instagram_account_key,
    )
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-staged-account-mismatch.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "smoke_only": True,
            "purpose": "smoke",
            "expected_account_key": instagram_account_key("111111"),
        },
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None
    queue.stage_final_result(
        task_id,
        terminal_status="empty",
        claim_token=str(claim["claim_token"]),
        items=[],
        scope_complete={
            "instagram_liked": True,
            "instagram_saved": True,
            "instagram_following": True,
        },
        account_id="222222",
        debug={
            "response_observed": True,
            "terminal_evidence": "all_scopes_complete",
        },
    )
    before = queue.get(task_id)
    assert before is not None
    frozen_result = before["result_json"]

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "empty",
                "account_id": "222222",
                "items": [],
                "scope_complete": {
                    "instagram_liked": True,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
            },
        )
        discover_id = queue.enqueue_with_id(
            "discover",
            {"mode": "creator", "username": "openai"},
            daily_budget=0,
        )
        assert discover_id is not None
        next_response = client.get("/api/sources/instagram/next-task")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "instagram_account_switch_not_supported"
    stored = queue.get(task_id)
    assert stored is not None and stored["status"] == "failed"
    assert stored["result_json"] == frozen_result
    assert next_response.status_code == 200, next_response.text
    assert next_response.json()["id"] == discover_id


def test_instagram_missing_identity_terminalizes_claim_before_rejecting_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-missing-identity.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "empty",
                "items": [],
                "scope_complete": {
                    "instagram_liked": True,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
                "debug": {
                    "response_observed": True,
                    "terminal_evidence": "all_scopes_complete",
                },
            },
        )
        discover_id = queue.enqueue_with_id(
            "discover",
            {"mode": "topic", "topic": "technology"},
            daily_budget=0,
        )
        assert discover_id is not None
        next_response = client.get("/api/sources/instagram/next-task")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "instagram_identity_required"
    stored = queue.get(task_id)
    assert stored is not None and stored["status"] == "failed"
    assert json.loads(str(stored["result_json"])) == {
        "status": "failed",
        "error": "account_identity_missing",
        "cancelled": True,
    }
    assert next_response.status_code == 200, next_response.text
    assert next_response.json()["id"] == discover_id


def test_disabled_instagram_dispatches_explicit_bootstrap_but_not_discover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources import source_bootstrap
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    assert config.sources.instagram.enabled is False
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-disabled-explicit-fetch.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    discover_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "technology"},
        daily_budget=0,
    )
    bootstrap = source_bootstrap.enqueue_instagram_bootstrap(
        database,
        force=True,
        smoke_only=True,
    )
    assert discover_id is not None
    assert bootstrap.created is True and bootstrap.task_id is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        claimed = client.get("/api/sources/instagram/next-task")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["id"] == bootstrap.task_id
        assert claimed.json()["type"] == "bootstrap_events"
        completed = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": bootstrap.task_id,
                "claim_token": claimed.json()["claim_token"],
                "status": "failed",
                "items": [],
                "error": "account_identity_missing",
            },
        )

    assert completed.status_code == 200, completed.text
    discover = queue.get(discover_id)
    assert discover is not None and discover["status"] == "pending"
    released = source_bootstrap.enqueue_xhs_bootstrap(database, force=True)
    assert released.created is True
    assert released.reason == "created"


@pytest.mark.parametrize(
    ("permission_code", "expected_detail"),
    [
        ("task_claim_conflict", "task_claim_conflict"),
        ("instagram_account_changed", "instagram_account_switch_not_supported"),
    ],
)
def test_instagram_task_result_distinguishes_permission_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    permission_code: str,
    expected_detail: str,
) -> None:
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / f"instagram-api-{permission_code}.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    def _raise_permission(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise PermissionError(permission_code)

    monkeypatch.setattr(
        InstagramTaskQueue,
        "stage_final_result_with_freshness",
        _raise_permission,
    )
    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "failed",
                "items": [],
                "error": "failed",
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == expected_detail


def test_instagram_partial_result_never_mints_positive_login_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    database = Database(tmp_path / "instagram-api-partial.db")
    database.initialize()
    database.set_instagram_login_state(False, "2026-08-12T00:00:00+00:00")
    queue = InstagramTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    assert task_id is not None
    claim = queue.next_pending()
    assert claim is not None

    with TestClient(
        create_app(memory_manager=object(), database=database, soul_engine=object())
    ) as client:
        response = client.post(
            "/api/sources/instagram/task-result",
            json={
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "partial",
                "account_id": "123456",
                "items": [
                    {
                        "scope": "instagram_saved",
                        "id": "3712345678901234567",
                        "code": "DAb_cd-123",
                        "content_type": "post",
                        "url": "https://www.instagram.com/p/DAb_cd-123/",
                    }
                ],
                "scope_complete": {
                    "instagram_liked": False,
                    "instagram_saved": False,
                    "instagram_following": False,
                },
                "error": "instagram_saved:item_cap_reached",
                "debug": {
                    "response_observed": True,
                    "terminal_evidence": "accepted_nonterminal_scope_page",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert database.get_instagram_login_state()[0] is False


def test_instagram_guided_init_cli_and_bootstrap_registration_are_exact() -> None:
    cli_source = _read("src/openbiliclaw/cli.py")
    bootstrap_source = _read("src/openbiliclaw/sources/source_bootstrap.py")
    assert '@app.command("fetch-instagram")' in cli_source
    assert '"--yes-instagram"' in cli_source
    assert '"--no-instagram"' in cli_source
    assert "include_instagram=include_instagram" in cli_source
    assert cli_source.count("def _enqueue_instagram_bootstrap_task(") == 1
    assert bootstrap_source.count("def enqueue_instagram_bootstrap(") == 1
    assert 'configured_limit("bootstrap_limit", 300, 300)' in bootstrap_source
    assert 'configured_limit("request_interval_seconds", 3, 30) * 1000' in bootstrap_source
    assert '"request_interval_ms": request_interval_ms' in bootstrap_source
    assert 'OPENBILICLAW_INSTAGRAM_BOOTSTRAP_WAIT_SECONDS", "780"' in cli_source
    assert "default_wait_seconds=780" in cli_source
    assert (
        "def fetch_instagram(\n    wait_seconds: float = typer.Option(\n        780.0,"
        in cli_source
    )
    instagram_collector = cli_source.split("def _collect_instagram_bootstrap_events(", 1)[1].split(
        "def _collect_zhihu_bootstrap_events(", 1
    )[0]
    assert "queue.cancel_task(" not in instagram_collector


def test_instagram_formal_discover_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw import cli

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        cli,
        "_run_instagram_discovery",
        lambda *, limit, force=False: calls.append((limit, force)),
    )
    cli.discover(source="ig", strategies=None, limit=7, force=True)
    assert calls == [(7, True)]

    cli_source = _read("src/openbiliclaw/cli.py")
    assert "def _run_instagram_discovery(" in cli_source
    assert "_run_instagram_discovery(limit=limit, force=force)" in cli_source
    assert 'kick=lambda: _kick_task_dispatcher("instagram")' in cli_source
    assert "linuxdo、v2ex、weibo 或 instagram" in cli_source


def test_instagram_formal_discover_producer_kicks_instagram_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw import cli
    from openbiliclaw.runtime import instagram_producer as producer_module
    from openbiliclaw.runtime import keyword_fetch as keyword_fetch_module
    from openbiliclaw.sources import instagram_tasks as task_module

    config = Config()
    config.sources.instagram.enabled = True
    config.sources.instagram.source_modes = ("topic",)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        last_admitted_items: list[Any] = []

    class Producer:
        def __init__(self, **kwargs: Any) -> None:
            self.kick = kwargs["kick"]

        async def produce_if_due(self, **_kwargs: Any) -> dict[str, object]:
            self.kick()
            return {"reason": "empty", "discovered": 0, "enqueued": 0}

    kicked: list[str] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr(cli, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli, "_get_runtime_database", lambda: object())
    monkeypatch.setattr(cli, "_build_soul_engine", Soul)
    monkeypatch.setattr(cli, "_build_discovery_engine", lambda: object())
    monkeypatch.setattr(
        cli,
        "_build_discovery_candidate_pipeline",
        lambda **_kwargs: Pipeline(),
    )
    monkeypatch.setattr(cli, "_kick_task_dispatcher", kicked.append)
    monkeypatch.setattr(producer_module, "InstagramDiscoveryProducer", Producer)
    monkeypatch.setattr(
        keyword_fetch_module,
        "KeywordFetchCoordinator",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(task_module, "InstagramTaskQueue", lambda _database: object())
    monkeypatch.setattr(cli, "_print_page_title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_status_panel", lambda *_args, **_kwargs: None)

    cli._run_instagram_discovery(limit=1, force=True)

    assert kicked == ["instagram"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"error": "instagram_saved:http_401"}, "login_required"),
        (
            {
                "error": "instagram_saved:item_cap_reached",
                "debug": {"failures": ["instagram_following:challenge_required"]},
            },
            "challenge",
        ),
        (
            {"debug": {"failures": ["instagram_liked:rate_limited"]}},
            "rate_limited",
        ),
        ({"error": "sessionid=secret login_required raw response"}, ""),
    ],
)
def test_instagram_cli_failure_classification_is_exact(
    result: dict[str, Any],
    expected: str,
) -> None:
    from openbiliclaw.cli import _instagram_result_failure_status

    assert _instagram_result_failure_status(result) == expected


@pytest.mark.parametrize("status", ["login_required", "challenge", "rate_limited"])
def test_fetch_instagram_auth_and_rate_failures_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    import typer

    from openbiliclaw import cli

    monkeypatch.setattr(cli, "_run_single_source_bootstrap", lambda **_kwargs: status)
    with pytest.raises(typer.Exit) as exc_info:
        cli.fetch_instagram(wait_seconds=0, force=True)
    assert exc_info.value.exit_code == 1


def test_instagram_failed_bootstrap_is_retried(tmp_path: Path) -> None:
    from openbiliclaw.sources import source_bootstrap
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-bootstrap-retry.db")
    database.initialize()
    first = source_bootstrap.enqueue_instagram_bootstrap(database, force=True)
    assert first.task_id is not None
    queue = InstagramTaskQueue(database)
    claimed = queue.next_pending()
    assert claimed is not None
    assert claimed["id"] == first.task_id
    assert queue.fail(
        first.task_id,
        claim_token=str(claimed["claim_token"]),
        error="http_401",
    )

    retried = source_bootstrap.enqueue_instagram_bootstrap(database)

    assert retried.created is True
    assert retried.reason == "created"
    assert retried.task_id not in {None, first.task_id}


def test_instagram_partial_recent_bootstrap_is_not_reused() -> None:
    from openbiliclaw.sources.source_bootstrap import _instagram_reusable_recent_task

    class Queue:
        @staticmethod
        def find_recent_task(
            task_type: str,
            *,
            recent_hours: float,
            statuses: tuple[str, ...],
        ) -> dict[str, Any] | None:
            assert task_type == "bootstrap_events"
            assert recent_hours > 0
            if statuses == ("completed",):
                return {
                    "id": "partial",
                    "status": "completed",
                    "created_at": "2026-08-12 00:00:00",
                    "result_json": '{"_openbiliclaw_terminal_status":"partial"}',
                }
            return None

    assert _instagram_reusable_recent_task(Queue(), recent_hours=6) is None


def test_instagram_recent_bootstrap_reuse_requires_exact_purpose_caps_and_account() -> None:
    import json

    from openbiliclaw.sources.source_bootstrap import _instagram_reusable_recent_task

    account_key = "sha256:" + "a" * 64
    expected = {
        "scopes": ["instagram_liked", "instagram_saved", "instagram_following"],
        "max_items_per_scope": 300,
        "max_pages_per_scope": 20,
        "request_interval_ms": 3000,
        "profile_update": True,
        "smoke_only": False,
        "profile_rebuild": False,
        "purpose": "guided-init",
        "expected_account_key": account_key,
    }

    class Queue:
        payload = dict(expected)

        @classmethod
        def find_recent_task(
            cls,
            task_type: str,
            *,
            recent_hours: float,
            statuses: tuple[str, ...],
        ) -> dict[str, Any] | None:
            assert task_type == "bootstrap_events"
            assert recent_hours == 6
            if statuses == ("completed",):
                return {
                    "id": "completed",
                    "status": "completed",
                    "created_at": "2026-08-12 00:00:00",
                    "payload_json": json.dumps(cls.payload),
                    "result_json": json.dumps(
                        {
                            "status": "empty",
                            "account_key": account_key,
                            "_openbiliclaw_terminal_status": "empty",
                        }
                    ),
                }
            return None

    assert (
        _instagram_reusable_recent_task(Queue(), recent_hours=6, expected_payload=expected)
        is not None
    )
    for field, mismatch in (
        ("max_items_per_scope", 299),
        ("request_interval_ms", 4000),
        ("profile_update", False),
        ("smoke_only", True),
        ("purpose", "smoke"),
        ("expected_account_key", "sha256:" + "b" * 64),
    ):
        Queue.payload = {**expected, field: mismatch}
        assert (
            _instagram_reusable_recent_task(Queue(), recent_hours=6, expected_payload=expected)
            is None
        ), field


def test_instagram_product_surfaces_are_registered() -> None:
    shared = _read("src/openbiliclaw/web/shared/source-status.js")
    setup = _read("src/openbiliclaw/web/setup/index.html")
    desktop = _read("src/openbiliclaw/web/desktop/assets/js/app.js")
    mobile = _read("src/openbiliclaw/web/js/view-models.js")
    popup = _read("extension/popup/popup-helpers.js")
    example = _read("config.example.toml")
    assert "instagram: Object.freeze({ guidedInit: true })" in shared
    assert "SourceStatus.INIT_SOURCE_KEYS.map" in setup
    assert '{ key: "instagram", label: "Instagram" }' in desktop
    assert 'instagram: "Instagram"' in mobile
    assert 'instagram: "Instagram"' in popup
    assert "[sources.instagram]" in example
    assert "bootstrap_limit = 300" in example


def test_instagram_image_cdn_is_https_allowlisted() -> None:
    assert "cdninstagram.com" in image_cache.ALLOWED_IMAGE_HOST_SUFFIXES
    assert "fbcdn.net" in image_cache.ALLOWED_IMAGE_HOST_SUFFIXES
    assert image_cache.is_allowed_image_url("https://scontent-lax3-2.cdninstagram.com/image.jpg")
    assert image_cache.is_allowed_image_url("https://instagram.fabc1-1.fna.fbcdn.net/profile.jpg")
    assert not image_cache.is_allowed_image_url("https://evilfbcdn.net/image.jpg")


def _controller(*, instagram_producer: object | None = None) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        discovery_engine=object(),
        recommendation_engine=object(),
        pool_target_count=10,
        pool_source_shares={"instagram": 1},
        instagram_producer=instagram_producer,
    )


@pytest.mark.asyncio
async def test_instagram_runtime_context_and_refresh_ticker_are_wired() -> None:
    runtime_context_source = _read("src/openbiliclaw/api/runtime_context.py")
    assert "build_instagram_discovery_producer" in runtime_context_source
    assert "instagram_producer=new_instagram_producer" in runtime_context_source
    assert '"instagram_task_available"' in runtime_context_source
    assert "instagram_producer" in ContinuousRefreshController.__dataclass_fields__

    producer = object()
    controller = _controller(instagram_producer=producer)
    calls: list[tuple[str, object | None]] = []

    async def fake_tick_platform_producer(
        *, source_family: str, producer: object | None
    ) -> dict[str, object]:
        calls.append((source_family, producer))
        return {"discovered": 1, "reason": "ok"}

    controller._tick_platform_producer = fake_tick_platform_producer  # type: ignore[method-assign]
    assert await controller._tick_instagram_producer() == {
        "discovered": 1,
        "reason": "ok",
    }
    assert calls == [("instagram", producer)]
    assert '"instagram": self._tick_instagram_producer' in inspect.getsource(
        ContinuousRefreshController._run_deficit_producers_once
    )


def test_instagram_existing_producer_does_not_report_stranded_share(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    controller = _controller(instagram_producer=object())
    controller._warn_on_stranded_source_shares()
    assert "instagram" not in caplog.text.casefold()


@pytest.mark.asyncio
async def test_instagram_topic_falls_back_when_planner_has_no_claims() -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class EmptyPlanner:
        @staticmethod
        def should_claim() -> bool:
            return True

        @staticmethod
        def claim(platform: str, *, n: int) -> list[object]:
            assert platform == "instagram"
            assert n > 0
            return []

    class Queue:
        payload: dict[str, Any] | None = None

        def enqueue_with_id(
            self, task_type: str, payload: dict[str, Any], *, daily_budget: int
        ) -> str:
            assert task_type == "discover"
            assert daily_budget == 0
            self.payload = payload
            return "ig-task"

        @staticmethod
        def get(task_id: str) -> dict[str, Any]:
            assert task_id == "ig-task"
            return {
                "status": "completed",
                "result_json": (
                    '{"status":"empty","items":[],"scope_counts":{},'
                    '"scope_complete":{"discover":true}}'
                ),
            }

    class Interests:
        interests = ["robotics"]

    class Profile:
        preferences = Interests()

    queue = Queue()
    producer = InstagramDiscoveryProducer(
        database=object(),
        task_queue=queue,
        soul_engine=object(),
        keyword_fetch=EmptyPlanner(),
        wait_seconds=0,
    )
    contents, claims, reason = await producer._run_topics(Profile(), 3)
    assert contents == []
    assert claims == []
    assert reason == "empty"
    assert queue.payload is not None
    assert queue.payload["topic"] == "robotics"
    assert "source_keyword_id" not in queue.payload


@pytest.mark.asyncio
async def test_instagram_topic_preserves_planner_claim_lifecycle() -> None:
    from types import SimpleNamespace

    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    claim = SimpleNamespace(id=17, keyword="robotics")

    class Planner:
        events: list[tuple[str, int]] = []

        @staticmethod
        def should_claim() -> bool:
            return True

        @staticmethod
        def claim(platform: str, *, n: int) -> list[object]:
            assert platform == "instagram"
            assert n > 0
            return [claim]

        @classmethod
        def mark_executing(cls, item: Any) -> None:
            cls.events.append(("executing", int(item.id)))

        @classmethod
        def mark_failed(cls, items: list[Any]) -> None:
            cls.events.extend(("failed", int(item.id)) for item in items)

    class Queue:
        payload: dict[str, Any] | None = None

        def enqueue_with_id(
            self, task_type: str, payload: dict[str, Any], *, daily_budget: int
        ) -> str:
            assert task_type == "discover"
            assert daily_budget == 0
            self.payload = payload
            return "topic-task"

        @staticmethod
        def get(_task_id: str) -> dict[str, Any]:
            return {
                "status": "completed",
                "result_json": '{"status":"empty","items":[]}',
            }

    queue = Queue()
    producer = InstagramDiscoveryProducer(
        database=object(),
        task_queue=queue,
        soul_engine=object(),
        keyword_fetch=Planner(),
        wait_seconds=0,
    )

    contents, claims, reason = await producer._run_topics(object(), 3)
    producer._finish_topic_claims(claims, set(), reason)

    assert contents == []
    assert reason == "empty"
    assert queue.payload is not None
    assert queue.payload["source_keyword_id"] == 17
    assert Planner.events == [("executing", 17), ("failed", 17)]


@pytest.mark.asyncio
async def test_instagram_daemon_producer_does_not_strand_tasks_without_extension() -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class AbsentPresence:
        @staticmethod
        def is_present(grace_seconds: int) -> bool:
            assert grace_seconds == 90
            return False

    producer = InstagramDiscoveryProducer(
        database=object(),
        task_queue=object(),
        soul_engine=object(),
        enabled=True,
        presence=AbsentPresence(),
    )

    assert await producer.produce_if_due(force=True) == {
        "discovered": 0,
        "reason": "extension_absent",
    }


@pytest.mark.asyncio
async def test_instagram_default_modes_reserve_retained_capacity_for_creator() -> None:
    import sqlite3
    from types import SimpleNamespace

    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class Database:
        conn = sqlite3.connect(":memory:")

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        @staticmethod
        def pool_full_for_source(source: str) -> bool:
            assert source == "instagram"
            return False

        @staticmethod
        def enqueue_candidates(items: list[object], *, source_context: str) -> int:
            assert len(items) == 1
            assert source_context.startswith("instagram-")
            return 1

    topic = SimpleNamespace(
        content_id="1",
        source_strategy="instagram-topic",
        source_keyword_id=None,
    )
    creator = SimpleNamespace(
        content_id="2",
        source_strategy="instagram-creator",
        source_keyword_id=None,
    )
    limits: list[tuple[str, int]] = []

    async def run_topics(_profile: object, limit: int) -> tuple[list[Any], list[Any], str]:
        limits.append(("topic", limit))
        return [topic], [], "ok"

    async def run_creators(_seeds: list[Any], limit: int) -> tuple[list[Any], str]:
        limits.append(("creator", limit))
        return [creator], "ok"

    producer = InstagramDiscoveryProducer(
        database=Database(),
        task_queue=object(),
        soul_engine=Soul(),
        enabled=True,
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
    )
    producer._run_topics = run_topics  # type: ignore[method-assign]
    producer._run_creators = run_creators  # type: ignore[method-assign]
    producer._stamp_run = lambda _inserted: None  # type: ignore[method-assign]

    result = await producer.produce_if_due(limit=2, force=True)

    assert limits == [("topic", 1), ("creator", 1)]
    assert result["discovered"] == 2
    assert result["enqueued"] == 2


@pytest.mark.asyncio
async def test_instagram_empty_attempt_is_throttled_without_productive_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    from openbiliclaw.runtime import instagram_producer as producer_module
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class Database:
        conn = sqlite3.connect(":memory:")

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    productive_runs: list[int] = []
    monkeypatch.setattr(
        producer_module,
        "record_producer_run",
        lambda _database, _source, count: productive_runs.append(int(count)),
    )
    producer = InstagramDiscoveryProducer(
        database=Database(),
        task_queue=object(),
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        min_interval_minutes=10,
    )

    async def empty_topics(
        _profile: object,
        _limit: int,
    ) -> tuple[list[Any], list[Any], str]:
        return [], [], "empty"

    producer._run_topics = empty_topics  # type: ignore[method-assign]
    first = await producer.produce_if_due(limit=3, force=True)
    second = await producer.produce_if_due(limit=3)

    assert producer.wait_seconds >= 780
    assert first["reason"] == "empty"
    assert second == {"discovered": 0, "reason": "throttled"}
    assert productive_runs == []


@pytest.mark.asyncio
async def test_instagram_producer_adopts_late_owned_result_once_after_restart(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-restart.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        seen: set[str] = set()
        calls = 0

        @staticmethod
        def pool_full_for_source(source: str) -> bool:
            assert source == "instagram"
            return False

        @classmethod
        def enqueue_candidates(cls, items: list[Any], *, source_context: str) -> int:
            assert source_context == "instagram-topic"
            cls.calls += 1
            content_id = str(items[0].content_id)
            if content_id in cls.seen:
                return 0
            cls.seen.add(content_id)
            return 1

    first_process = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        wait_seconds=0,
    )
    first_process._ensure_budget_table()
    task_id = first_process._enqueue_owned_discover(
        {
            "mode": "topic",
            "topic": "technology",
            "max_items": 1,
            "source_keyword_id": 41,
        },
        mode="topic",
        source_keyword_id=41,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    assert await first_process._wait_for_task(task_id) == {
        "status": "timeout",
        "items": [],
    }
    queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=str(claimed["claim_token"]),
        items=[
            {
                "id": "3712345678901234567",
                "code": "DAb_cd-123",
                "content_type": "reel",
                "url": "https://www.instagram.com/reel/DAb_cd-123/",
                "description": "Robotics reel",
                "author_id": "25025320",
                "author_name": "openai",
            }
        ],
        debug={
            "response_observed": True,
            "terminal_evidence": "recognized_envelope_has_next_page_false",
        },
    )
    assert queue.complete(task_id, claim_token=str(claimed["claim_token"]))
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task "
        "SET lease_until=datetime('now', '-1 second') WHERE task_id=?",
        (task_id,),
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        min_interval_minutes=10,
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
    )
    result = await restarted.produce_if_due(limit=1)
    second = await restarted.produce_if_due(limit=1)

    assert result["discovered"] == 1
    assert result["enqueued"] == 1
    assert second == {"discovered": 0, "reason": "throttled"}
    assert Pipeline.calls == 1
    owner = database.conn.execute(
        "SELECT consumed_at FROM instagram_discovery_owned_task WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert owner is not None and owner["consumed_at"] is not None


def _finish_owned_instagram_topic(
    queue: Any,
    task_id: str,
    *,
    content_id: str = "3712345678901234567",
    code: str = "DAb_cd-123",
    failed: bool = False,
) -> None:
    claim = queue.next_pending()
    assert claim is not None and claim["id"] == task_id
    if failed:
        assert queue.fail(
            task_id,
            claim_token=str(claim["claim_token"]),
            error="http_error",
        )
        return
    queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=str(claim["claim_token"]),
        items=[
            {
                "id": content_id,
                "code": code,
                "content_type": "reel",
                "url": f"https://www.instagram.com/reel/{code}/",
                "author_id": "25025320",
                "author_name": "openai",
            }
        ],
        debug={
            "response_observed": True,
            "terminal_evidence": "recognized_envelope_has_next_page_false",
        },
    )
    assert queue.complete(task_id, claim_token=str(claim["claim_token"]))


@pytest.mark.asyncio
async def test_instagram_recovery_scans_past_duplicate_limit_to_find_unfinished_owner(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-duplicate-before-pending.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        @staticmethod
        def pool_full_for_source(_source: str) -> bool:
            return False

        @staticmethod
        def enqueue_candidates(_items: list[Any], *, source_context: str) -> int:
            assert source_context == "instagram-topic"
            return 1

    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
    )
    original._ensure_budget_table()
    task_ids = [
        original._enqueue_owned_discover(
            {"mode": "topic", "topic": f"seed-{index}", "max_items": 1},
            mode="topic",
            source_keyword_id=None,
        )
        for index in range(3)
    ]
    assert all(task_ids)
    _finish_owned_instagram_topic(queue, str(task_ids[0]))
    _finish_owned_instagram_topic(queue, str(task_ids[1]))
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task SET lease_until=datetime('now', '-1 second')"
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic", "creator"),
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
        wait_seconds=0,
    )
    result = await restarted.produce_if_due(limit=2, force=True)
    count = database.conn.execute(
        "SELECT COUNT(*) FROM instagram_tasks WHERE type='discover'"
    ).fetchone()

    assert result["mode_results"] == {"topic": "timeout"}
    assert "creator" not in result["mode_results"]
    assert count is not None and int(count[0]) == 3
    pending = queue.get(str(task_ids[2]))
    assert pending is not None and pending["status"] == "pending"


@pytest.mark.asyncio
async def test_instagram_recovered_failure_blocks_fresh_creator_suffix(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-success-then-failed.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
    )
    original._ensure_budget_table()
    success_id = original._enqueue_owned_discover(
        {"mode": "topic", "topic": "success", "max_items": 1},
        mode="topic",
        source_keyword_id=None,
    )
    failed_id = original._enqueue_owned_discover(
        {"mode": "topic", "topic": "failed", "max_items": 1},
        mode="topic",
        source_keyword_id=None,
    )
    assert success_id is not None and failed_id is not None
    _finish_owned_instagram_topic(queue, success_id)
    _finish_owned_instagram_topic(queue, failed_id, failed=True)
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task SET lease_until=datetime('now', '-1 second')"
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic", "creator"),
        wait_seconds=0,
    )
    result = await restarted.produce_if_due(limit=2, force=True)
    count = database.conn.execute(
        "SELECT COUNT(*) FROM instagram_tasks WHERE type='discover'"
    ).fetchone()

    assert result["mode_results"]["topic"] == "failed"
    assert "creator" not in result["mode_results"]
    assert count is not None and int(count[0]) == 2


@pytest.mark.asyncio
async def test_instagram_failed_handoff_does_not_consume_unreplayed_owner_rows(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-handoff-retry-limit.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        fail = True

        @staticmethod
        def pool_full_for_source(_source: str) -> bool:
            return False

        @classmethod
        def enqueue_candidates(cls, _items: list[Any], *, source_context: str) -> int:
            assert source_context == "instagram-topic"
            if cls.fail:
                raise RuntimeError("temporary candidate handoff failure")
            return 1

    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
    )
    original._ensure_budget_table()
    task_ids = [
        original._enqueue_owned_discover(
            {"mode": "topic", "topic": f"seed-{index}", "max_items": 1},
            mode="topic",
            source_keyword_id=None,
        )
        for index in range(3)
    ]
    assert all(task_ids)
    for index, task_id in enumerate(task_ids):
        _finish_owned_instagram_topic(
            queue,
            str(task_id),
            content_id=str(3712345678901234567 + index),
            code=f"DAb_cd-{index}",
        )
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task SET lease_until=datetime('now', '-1 second')"
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
    )
    first = await restarted.produce_if_due(limit=3, force=True)
    assert first["enqueued"] == 0
    Pipeline.fail = False
    second = await restarted.produce_if_due(limit=1, force=True)
    assert second["enqueued"] == 1

    owners = database.conn.execute(
        "SELECT task_id, consumed_at FROM instagram_discovery_owned_task ORDER BY rowid"
    ).fetchall()
    assert owners[0]["consumed_at"] is not None
    assert owners[1]["consumed_at"] is None
    assert owners[2]["consumed_at"] is None


@pytest.mark.asyncio
async def test_instagram_producer_adopts_unfinished_owned_task_without_queue_growth(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-pending.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
    )
    original._ensure_budget_table()
    task_id = original._enqueue_owned_discover(
        {"mode": "topic", "topic": "technology", "max_items": 1},
        mode="topic",
        source_keyword_id=None,
    )
    assert task_id is not None
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task "
        "SET lease_until=datetime('now', '-1 second') WHERE task_id=?",
        (task_id,),
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        wait_seconds=0,
    )
    result = await restarted.produce_if_due(limit=1, force=True)
    count = database.conn.execute(
        "SELECT COUNT(*) FROM instagram_tasks WHERE type='discover'"
    ).fetchone()

    assert result == {
        "discovered": 0,
        "enqueued": 0,
        "mode_results": {"topic": "timeout"},
        "reason": "recovery_blocked",
    }
    assert count is not None and int(count[0]) == 1
    assert queue.get(task_id)["status"] == "pending"  # type: ignore[index]


@pytest.mark.asyncio
async def test_instagram_live_owner_lease_blocks_a_second_producer(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-owner-lease.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=object(),
        enabled=True,
    )
    original._ensure_budget_table()
    task_id = original._enqueue_owned_discover(
        {"mode": "topic", "topic": "technology", "max_items": 1},
        mode="topic",
        source_keyword_id=None,
    )
    assert task_id is not None

    contender = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=object(),
        enabled=True,
        wait_seconds=0,
    )
    recovered = await contender._recover_owned_tasks(
        limit=1,
        modes=("topic", "creator"),
        extension_present=True,
    )

    assert recovered == (
        [],
        set(),
        {"topic": "owned_task_active"},
        [],
        True,
    )
    row = database.conn.execute(
        "SELECT owner_token FROM instagram_discovery_owned_task WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row is not None and row["owner_token"] == original._owner_token


@pytest.mark.asyncio
async def test_instagram_offline_recovery_consumes_terminal_without_fresh_lane(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-offline-recovery.db")
    database.initialize()
    queue = InstagramTaskQueue(database)

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Pipeline:
        @staticmethod
        def pool_full_for_source(_source: str) -> bool:
            return False

        @staticmethod
        def enqueue_candidates(_items: list[Any], *, source_context: str) -> int:
            assert source_context == "instagram-topic"
            return 1

    class AbsentPresence:
        @staticmethod
        def is_present(_grace_seconds: int) -> bool:
            return False

    original = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
    )
    original._ensure_budget_table()
    task_id = original._enqueue_owned_discover(
        {"mode": "topic", "topic": "technology", "max_items": 1},
        mode="topic",
        source_keyword_id=None,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=str(claimed["claim_token"]),
        items=[
            {
                "id": "3712345678901234567",
                "code": "DAb_cd-123",
                "content_type": "reel",
                "url": "https://www.instagram.com/reel/DAb_cd-123/",
                "author_id": "25025320",
                "author_name": "openai",
            }
        ],
        debug={
            "response_observed": True,
            "terminal_evidence": "recognized_envelope_has_next_page_false",
        },
    )
    assert queue.complete(task_id, claim_token=str(claimed["claim_token"]))
    database.conn.execute(
        "UPDATE instagram_discovery_owned_task "
        "SET lease_until=datetime('now', '-1 second') WHERE task_id=?",
        (task_id,),
    )
    database.conn.commit()

    restarted = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=Soul(),
        enabled=True,
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
        presence=AbsentPresence(),
    )
    result = await restarted.produce_if_due(limit=2)
    task_count = database.conn.execute(
        "SELECT COUNT(*) FROM instagram_tasks WHERE type='discover'"
    ).fetchone()

    assert result["discovered"] == 1
    assert result["enqueued"] == 1
    assert result["mode_results"] == {
        "topic": "ok",
        "creator": "extension_absent",
    }
    assert task_count is not None and int(task_count[0]) == 1


@pytest.mark.asyncio
async def test_instagram_producer_recovery_ignores_bootstrap_and_other_owner(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer
    from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "instagram-producer-ownership.db")
    database.initialize()
    queue = InstagramTaskQueue(database)
    bootstrap_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"smoke_only": True, "purpose": "smoke"},
        daily_budget=0,
    )
    other_discover_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "not-producer-owned"},
        daily_budget=0,
    )
    assert bootstrap_id is not None and other_discover_id is not None

    producer = InstagramDiscoveryProducer(
        database=database,
        task_queue=queue,
        soul_engine=object(),
        enabled=True,
        wait_seconds=0,
    )
    producer._ensure_budget_table()
    recovered = await producer._recover_owned_tasks(
        limit=10,
        modes=("topic", "creator"),
        extension_present=True,
    )

    assert recovered == ([], set(), {}, [], False)
    assert queue.get(bootstrap_id)["status"] == "pending"  # type: ignore[index]
    assert queue.get(other_discover_id)["status"] == "pending"  # type: ignore[index]


def test_instagram_creator_only_mode_normalizes_to_seeded_composition() -> None:
    from openbiliclaw.runtime.instagram_producer import _normalize_modes

    assert _normalize_modes(("creator",)) == ("topic", "creator")


def test_instagram_creator_seeds_reject_display_names_that_poison_queue() -> None:
    from types import SimpleNamespace

    from openbiliclaw.runtime.instagram_producer import _creator_seed_usernames

    assert _creator_seed_usernames(
        [
            SimpleNamespace(author_name="Open AI Official"),
            SimpleNamespace(author_name="@open.ai"),
            SimpleNamespace(author_name="open-ai"),
        ]
    ) == ["open.ai"]


@pytest.mark.asyncio
async def test_instagram_candidate_handoff_failure_keeps_prior_retained_accounting() -> None:
    import sqlite3
    from types import SimpleNamespace

    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class Database:
        conn = sqlite3.connect(":memory:")

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class Planner:
        used: list[int] = []

        @classmethod
        def mark_used(cls, claims: list[Any]) -> None:
            cls.used.extend(int(claim.id) for claim in claims)

    class Pipeline:
        calls = 0

        @staticmethod
        def pool_full_for_source(_source: str) -> bool:
            return False

        @classmethod
        def enqueue_candidates(cls, _items: list[Any], *, source_context: str) -> int:
            assert source_context == "instagram-topic"
            cls.calls += 1
            if cls.calls == 2:
                raise RuntimeError("candidate store unavailable")
            return 1

    first = SimpleNamespace(
        content_id="first",
        source_strategy="instagram-topic",
        source_keyword_id=17,
    )
    second = SimpleNamespace(
        content_id="second",
        source_strategy="instagram-topic",
        source_keyword_id=17,
    )
    claim = SimpleNamespace(id=17, keyword="robotics")

    async def run_topics(
        _profile: object,
        _limit: int,
    ) -> tuple[list[Any], list[Any], str]:
        producer.database.conn.execute(
            "INSERT INTO instagram_discovery_owned_task "
            "(task_id, mode, keyword, owner_token, lease_until) "
            "VALUES ('handoff-task', 'topic', 'robotics', ?, CURRENT_TIMESTAMP)",
            (producer._owner_token,),
        )
        producer.database.conn.commit()
        producer._pending_consumption_task_ids.add("handoff-task")
        return [first, second], [claim], "ok"

    producer = InstagramDiscoveryProducer(
        database=Database(),
        task_queue=object(),
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        min_interval_minutes=0,
        candidate_pipeline=Pipeline(),
        candidate_evaluation_owned_by_coordinator=True,
        keyword_fetch=Planner(),
    )
    producer._run_topics = run_topics  # type: ignore[method-assign]
    result = await producer.produce_if_due(limit=2, force=True)
    retained = producer.database.conn.execute(
        "SELECT COALESCE(SUM(retained_count), 0) FROM instagram_discovery_budget WHERE mode='topic'"
    ).fetchone()
    owner = producer.database.conn.execute(
        "SELECT consumed_at FROM instagram_discovery_owned_task WHERE task_id='handoff-task'"
    ).fetchone()

    assert result["discovered"] == 2
    assert result["enqueued"] == 1
    assert retained is not None and int(retained[0]) == 1
    assert owner is not None and owner[0] is None
    assert Planner.used == [17]


@pytest.mark.asyncio
async def test_instagram_cycle_deadline_stops_additional_browser_tasks() -> None:
    import asyncio
    import sqlite3

    from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer

    class Database:
        conn = sqlite3.connect(":memory:")

    class Interests:
        interests = ["robotics", "technology"]

    class Profile:
        preferences = Interests()

    class Soul:
        @staticmethod
        async def get_profile() -> object:
            return Profile()

    class Queue:
        enqueued: list[str] = []

        @classmethod
        def enqueue_with_id(
            cls,
            _task_type: str,
            payload: dict[str, Any],
            *,
            daily_budget: int,
        ) -> str:
            assert daily_budget == 0
            cls.enqueued.append(str(payload["topic"]))
            return f"task-{len(cls.enqueued)}"

    producer = InstagramDiscoveryProducer(
        database=Database(),
        task_queue=Queue(),
        soul_engine=Soul(),
        enabled=True,
        source_modes=("topic",),
        min_interval_minutes=0,
    )

    async def finish_first_after_cycle_deadline(_task_id: str) -> dict[str, Any]:
        producer._cycle_deadline = asyncio.get_running_loop().time()
        return {"status": "empty", "items": []}

    producer._wait_for_task = finish_first_after_cycle_deadline  # type: ignore[method-assign]
    result = await producer.produce_if_due(limit=3, force=True)

    assert Queue.enqueued == ["robotics"]
    assert result["mode_results"] == {"topic": "cycle_deadline"}
    assert result["reason"] == "cycle_deadline"


def test_instagram_late_creator_recovery_cannot_restart_topic_cycle() -> None:
    from openbiliclaw.runtime.instagram_producer import _fresh_modes_after_recovery

    assert (
        _fresh_modes_after_recovery(
            ("topic", "creator"),
            {"creator"},
        )
        == frozenset()
    )
    assert _fresh_modes_after_recovery(
        ("topic", "creator"),
        {"topic"},
    ) == frozenset({"creator"})
    assert _fresh_modes_after_recovery(
        ("topic", "creator"),
        set(),
    ) == frozenset({"topic", "creator"})


def test_instagram_api_rosters_match_source_policy() -> None:
    app_path = ROOT / "src/openbiliclaw/api/app.py"
    share_roster = _literal_module_assignment(app_path, "_SOURCE_SHARE_ORDER")
    init_roster = _literal_module_assignment(app_path, "_INIT_SOURCE_ORDER")
    assert share_roster[-1] == "instagram"
    assert init_roster == share_roster
