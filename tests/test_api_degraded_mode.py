from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import build_runtime_context
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config
from openbiliclaw.llm.registry import RegistryBuildError


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _invalid_config(tmp_path) -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="", model="gpt-4o-mini"),
            ollama=LLMProviderConfig(model="", base_url=""),
        ),
        data_dir=str(tmp_path / "data"),
    )


def _valid_config(tmp_path) -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-valid-openai-key", model="gpt-4o-mini"),
        ),
        data_dir=str(tmp_path / "data"),
    )


def _save_project_config(monkeypatch: pytest.MonkeyPatch, tmp_path, cfg: Config) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(cfg, tmp_path / "config.toml")


def _wait_for_config_apply(client: TestClient, expected: str) -> dict[str, object]:
    for _ in range(200):
        status = client.get("/api/config/apply-status").json()
        if status["state"] == expected:
            return status
        time.sleep(0.01)
    pytest.fail(f"后台配置状态未进入 {expected}")


def test_build_runtime_context_stays_strict_for_invalid_llm_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)

    with pytest.raises(RegistryBuildError):
        build_runtime_context(_invalid_config(tmp_path))


def test_create_app_boots_degraded_when_registry_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))

    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reason"] == "llm_registry_unavailable"
    assert body["issues"]
    assert body["issues"][0]["severity"] == "blocking"


def test_degraded_ping_stays_live_and_advertises_fast_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/ping")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "openbiliclaw-api"
    assert body["degraded"] is True
    assert body["degraded_reason"] == "llm_registry_unavailable"
    assert body["issues"][0]["severity"] == "blocking"


def test_degraded_config_get_includes_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == "llm_registry_unavailable"
    assert any(issue["severity"] == "blocking" for issue in body["issues"])


def test_degraded_config_put_recovers_runtime_in_process_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    app = create_app()

    with TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420") as client:
        assert client.get("/api/recommendations").status_code == 503

        response = client.put(
            "/api/config",
            json={
                "suppress_background_llm_work": True,
                "llm": {"openai": {"api_key": "sk-new-valid-key"}},
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["reloaded"] is False
        assert body["apply_state"] == "queued"
        assert body["rollback_applied"] is False
        assert body["restart_required"] is False
        assert body["config"]["degraded"] is False
        status = _wait_for_config_apply(client, "applied")
        assert "无需重启" in status["message"]
        assert app.state.runtime_context.degraded is False
        assert app.state.degraded is False
        assert app.state.degraded_reason == ""
        assert app.state.degraded_issues == []
        assert (
            app.state.feedback_batch_scheduler.soul_engine is app.state.runtime_context.soul_engine
        )

        ping = client.get("/api/ping").json()
        assert "degraded" not in ping
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/recommendations").status_code != 503

    assert "sk-new-valid-key" in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_degraded_config_put_keeps_guard_and_rolls_back_if_in_process_rebuild_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    config_path = tmp_path / "config.toml"
    before = config_path.read_bytes()
    app = create_app()

    async def fail_rebuild(new_config: object) -> None:  # noqa: ARG001
        raise RuntimeError("simulated degraded recovery failure")

    monkeypatch.setattr(
        app.state.runtime_context,
        "rebuild_from_config",
        fail_rebuild,
    )

    with TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420") as client:
        response = client.put(
            "/api/config",
            json={"llm": {"openai": {"api_key": "sk-new-valid-key"}}},
        )

        assert response.status_code == 202
        body = response.json()
        assert body["ok"] is True
        assert body["reloaded"] is False
        assert body["rollback_applied"] is False
        assert body["restart_required"] is False
        status = _wait_for_config_apply(client, "failed")
        assert "simulated degraded recovery failure" in status["error"]
        assert app.state.runtime_context.degraded is True
        assert client.get("/api/recommendations").status_code == 503

    assert config_path.read_bytes() == before


def test_degraded_config_put_keeps_recovered_runtime_if_background_restart_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Ancillary loop startup must not roll a healthy rebuilt core back."""
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    config_path = tmp_path / "config.toml"
    app = create_app()

    async def fail_background_restart(
        app_arg: object,  # noqa: ARG001
        *,
        run_post_reload_llm_work: bool = True,  # noqa: ARG001
    ) -> None:
        raise RuntimeError("simulated background restart failure")

    monkeypatch.setattr(
        app.state.runtime_context,
        "restart_background_tasks",
        fail_background_restart,
    )

    with TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420") as client:
        response = client.put(
            "/api/config",
            json={
                "suppress_background_llm_work": True,
                "llm": {"openai": {"api_key": "sk-new-valid-key"}},
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["ok"] is True
        assert body["reloaded"] is False
        assert body["rollback_applied"] is False
        assert body["restart_required"] is False
        status = _wait_for_config_apply(client, "applied")
        assert "无需重启" not in status["message"]
        assert "不影响继续初始化" in status["message"]
        assert app.state.runtime_context.degraded is False
        assert client.get("/api/health").json()["status"] == "ok"

    assert "sk-new-valid-key" in config_path.read_text(encoding="utf-8")


def test_degraded_mode_keeps_mobile_static_shell_assets_reachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    mobile_response = client.get("/m/")
    favicon_response = client.get("/favicon.ico")

    assert mobile_response.status_code == 200
    assert favicon_response.status_code == 200
    assert favicon_response.headers.get("content-type", "").startswith("image/png")


def test_degraded_mode_keeps_desktop_and_setup_recovery_shells_reachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    root_response = client.get("/", follow_redirects=False)
    desktop_response = client.get("/web")
    desktop_asset_response = client.get("/web/assets/js/app.js")
    setup_response = client.get("/setup/")
    shared_module_response = client.get("/shared/source-status.js")
    unrelated_prefix_response = client.get("/webhook")

    assert root_response.status_code == 302
    assert root_response.headers["location"] == "/setup/"
    assert desktop_response.status_code == 200
    assert desktop_response.headers.get("content-type", "").startswith("text/html")
    assert desktop_asset_response.status_code == 200
    assert "javascript" in desktop_asset_response.headers.get("content-type", "")
    assert setup_response.status_code == 200
    assert setup_response.headers.get("content-type", "").startswith("text/html")
    # The setup wizard's <script src="/shared/source-status.js"> runs at parse
    # time; a 503 here leaves SourceStatus undefined and the whole wizard dead,
    # so the degraded config could never be repaired from the browser.
    assert shared_module_response.status_code == 200
    assert "javascript" in shared_module_response.headers.get("content-type", "")
    assert unrelated_prefix_response.status_code == 503


@pytest.mark.parametrize(
    ("method", "path", "json_payload"),
    [
        ("get", "/api/recommendations", None),
        ("get", "/api/profile-summary", None),
        ("post", "/api/events", {"events": []}),
        ("post", "/api/sources/xhs/observed-urls", {"items": []}),
    ],
)
def test_degraded_non_config_endpoints_return_503(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    method: str,
    path: str,
    json_payload: dict[str, object] | None,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    request = getattr(client, method)
    response = request(path, json=json_payload) if json_payload is not None else request(path)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["reason"] == "llm_registry_unavailable"


def test_degraded_mode_allows_llm_independent_repair_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Sources status/verify and embedding repair only need config + database.

    Blocking them made the settings 平台源 tab and the embedding banner fail
    with misleading "backend unavailable" copy while degraded, even though
    fixing platform logins / pulling bge-m3 is exactly what a user can
    usefully do while repairing the LLM config.
    """
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    status_response = client.get("/api/sources/status")
    verify_response = client.post("/api/sources/bilibili/verify")
    repair_status_response = client.get("/api/embedding/repair")

    assert status_response.status_code == 200
    assert "bilibili" in status_response.json()
    # Verify may legitimately fail (no cookie configured) but must NOT be the
    # degraded guard's 503 envelope.
    assert verify_response.status_code != 503
    assert repair_status_response.status_code != 503


def test_degraded_mode_allows_draft_llm_recovery_probes_and_model_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A broken active registry must not block testing its replacement draft.

    The setup wizard and both full settings surfaces submit an unsaved v2
    instance to these endpoints.  They are control-plane recovery operations:
    they build from the submitted draft and do not need the failed active
    registry.
    """
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    app = create_app()

    assert app.state.runtime_context.degraded is True

    class FakeRegistry:
        default_provider = "sensenova-main"

        def is_chat_capable(self, instance_id: str) -> bool:
            return instance_id == "sensenova-main"

        def provider_type(self, instance_id: str) -> str:
            assert instance_id == "sensenova-main"
            return "openai_compatible"

        def get(self, instance_id: str) -> SimpleNamespace:
            assert instance_id == "sensenova-main"
            return SimpleNamespace(_model="sensenova-6.7-flash-lite")

        async def complete_provider(
            self,
            instance_id: str,
            messages: list[dict[str, str]],
            **kwargs: object,
        ) -> SimpleNamespace:
            assert instance_id == "sensenova-main"
            assert messages
            assert kwargs["model"] == "sensenova-6.7-flash-lite"
            return SimpleNamespace(
                content="OK",
                instance_id=instance_id,
                provider="openai_compatible",
                model="sensenova-6.7-flash-lite",
            )

    class FakeDiscoveryProvider:
        async def list_models(self) -> list[str]:
            return ["sensenova-6.7-flash-lite"]

    import openbiliclaw.llm.registry as registry_module

    monkeypatch.setattr(registry_module, "build_llm_registry", lambda cfg: FakeRegistry())
    monkeypatch.setattr(
        registry_module,
        "_build_instance_provider",
        lambda cfg, provider, instance: FakeDiscoveryProvider(),
    )

    submitted_config = {
        "llm": {
            "routing_version": 2,
            "instances": {
                "sensenova-main": {
                    "name": "SenseNova",
                    "provider_type": "openai_compatible",
                    "enabled": True,
                    "api_key": "sk-sensenova-test",
                    "model": "sensenova-6.7-flash-lite",
                    "base_url": "https://token.sensenova.cn/v1",
                }
            },
            "default_chain": ["sensenova-main"],
            "routes": {},
        }
    }
    client = TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420")

    # The degraded context still owns the shared gate needed by a real draft
    # probe; it intentionally does not construct any business LLM service.
    assert app.state.runtime_context.llm_concurrency_gate is not None

    probe = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm_instance",
            "instance_id": "sensenova-main",
            "config": submitted_config,
        },
    )
    discovery = client.post(
        "/api/config/discover-models",
        json={
            "instance_id": "sensenova-main",
            "config": submitted_config,
        },
    )
    source_share = client.get("/api/config/source-share-suggestion")
    business_api = client.get("/api/recommendations")

    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    assert probe.json()["instance_id"] == "sensenova-main"
    assert discovery.status_code == 200
    assert discovery.json()["ok"] is True
    assert discovery.json()["models"] == ["sensenova-6.7-flash-lite"]
    assert source_share.status_code == 200
    assert business_api.status_code == 503


def test_degraded_update_status_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Update status must bypass the degraded 503 gate.

    A backend that can't build its LLM registry is exactly when the user may
    need to pull a fix-carrying release, so ``/api/update-status`` (and manual
    check/apply) stay on the degraded allow-list and the degraded context now
    builds a real ``AutoUpdateService`` to back them.
    """
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/update-status")

    assert response.status_code == 200
    body = response.json()
    assert "backend" in body
    # Not the 503 degraded envelope.
    assert body.get("status") != "degraded"
    assert "install_mode" in body["backend"]


def test_degraded_runtime_stream_sends_degraded_event_and_stays_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    with client.websocket_connect("/api/runtime-stream") as websocket:
        event = websocket.receive_json()
        assert event["type"] == "degraded"
        assert event["reason"] == "llm_registry_unavailable"
        assert event["issues"]


def test_degraded_init_post_rejects_with_actionable_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """POST /api/init while degraded must explain the LLM-config cause, not
    return a bare error or crash on a missing coordinator (project rule 7)."""
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    app = create_app()
    app.state.auth_gate.is_trusted_local = lambda request: True
    client = TestClient(app)

    response = client.post("/api/init", json={})

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "degraded"
    assert "LLM 配置有误" in body["detail"]
    assert "设置页" in body["detail"]


def test_degraded_init_status_reports_degraded_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """GET /api/init-status while degraded must surface a degraded-aware reason
    with an actionable detail instead of a generic 'AI 服务不可用' line."""
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/init-status")

    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "degraded"
    assert "LLM 配置有误" in body["detail"]
    assert "设置页" in body["detail"]
    assert body["can_start"] is False


def test_normal_boot_health_payload_reports_profile_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _valid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "openbiliclaw-api"
    assert body["profile_ready"] is False


def test_in_process_degraded_recovery_stays_normal_after_later_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    with TestClient(
        create_app(), client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420"
    ) as degraded_client:
        response = degraded_client.put(
            "/api/config",
            json={
                "suppress_background_llm_work": True,
                "llm": {"openai": {"api_key": "sk-new-valid-key"}},
            },
        )
        assert response.status_code == 202
        assert response.json()["reloaded"] is False
        assert response.json()["restart_required"] is False
        _wait_for_config_apply(degraded_client, "applied")

    normal_client = TestClient(create_app())
    health = normal_client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["service"] == "openbiliclaw-api"
    assert health["profile_ready"] is False
