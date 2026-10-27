"""Pure Jev evaluator settings, HTTP save guards, and four-surface wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.config import (
    Config,
    ConfigError,
    JevContextLimits,
    JevEvaluationConfig,
    build_jev_evaluation_config,
    decision_routes_enabled,
    load_config,
    save_config,
    validate_director_routes,
)

ROOT = Path(__file__).resolve().parents[1]


def _configured() -> Config:
    cfg = Config()
    cfg.llm.deepseek.api_key = "test-chat-key"
    cfg.decision.providers.jev.enabled = True
    cfg.decision.providers.jev.api_key_env = "OPENBILICLAW_TEST_JEV_KEY"
    return cfg


def test_jev_evaluation_roundtrip_and_route_enablement(tmp_path: Path) -> None:
    cfg = _configured()
    cfg.discovery.eval_scorer = "jev"
    cfg.discovery.jev_evaluation = JevEvaluationConfig(
        topic_labels=["科技", "音乐"], franchise_labels=["原神"], max_calls=8
    )
    target = tmp_path / "config.toml"
    save_config(cfg, target)
    loaded = load_config(target)
    assert loaded.discovery.eval_scorer == "jev"
    assert loaded.discovery.jev_evaluation == cfg.discovery.jev_evaluation
    assert decision_routes_enabled(loaded)
    assert "OPENBILICLAW_TEST_JEV_KEY" in target.read_text()


def test_single_pass_packing_controls_survive_config_roundtrip(tmp_path: Path) -> None:
    cfg = _configured()
    cfg.discovery.jev_evaluation = build_jev_evaluation_config(
        {
            "max_candidates_per_request": 3,
            "max_questions_per_request": 20,
            "max_question_bytes": 2048,
        }
    )
    target = save_config(cfg, tmp_path / "packing.toml")
    loaded = load_config(target, consult_environment=False).discovery.jev_evaluation
    assert loaded.max_candidates_per_request == 3
    assert loaded.max_questions_per_request == 20
    assert loaded.max_question_bytes == 2048


def test_config_cannot_enlarge_an_official_models_standard_window(tmp_path: Path) -> None:
    cfg = _configured()
    cfg.decision.providers.jev.context_limits = {"jev-1.13.0": JevContextLimits(128_000, 64_000)}
    with pytest.raises(ConfigError, match="context_limits"):
        save_config(cfg, tmp_path / "invalid-capacity.toml")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("topic_labels", ["科技", "科技"]),
        ("topic_labels", [" "]),
        ("topic_labels", [" 科技"]),
        ("topic_labels", ["unknown"]),
        ("topic_labels", ["中" * 27]),
        ("topic_labels", list(map(str, range(255)))),
        ("franchise_labels", [1]),
        ("max_state_question_bytes", 24001),
        ("max_request_bytes", 48001),
        ("max_request_bytes", 255),
        ("max_calls", True),
        ("max_candidates_per_request", 0),
        ("max_candidates_per_request", True),
        ("max_questions_per_request", 9),
        ("max_questions_per_request", True),
        ("max_question_bytes", 255),
        ("budget_seconds", float("nan")),
    ],
)
def test_jev_evaluation_rejects_invalid_settings(field: str, value: object) -> None:
    cfg = _configured()
    setattr(cfg.discovery.jev_evaluation, field, value)
    with pytest.raises(ConfigError, match=f"discovery.jev_evaluation.{field}"):
        validate_director_routes(cfg)


def test_jev_evaluation_rejects_unknown_settings() -> None:
    with pytest.raises(ConfigError, match="不支持的配置字段"):
        build_jev_evaluation_config({"api_key": "must-never-be-echoed"})


@pytest.mark.parametrize("conflict", ["disabled", "multimodal", "prefilter", "model_alias"])
def test_jev_evaluation_rejects_conflicting_routes(conflict: str) -> None:
    cfg = _configured()
    cfg.discovery.eval_scorer = "jev"
    if conflict == "disabled":
        cfg.decision.providers.jev.enabled = False
    elif conflict == "multimodal":
        cfg.discovery.multimodal_evaluation_enabled = True
    elif conflict == "model_alias":
        cfg.decision.providers.jev.model = "jev-latest"
    else:
        cfg.discovery.semantic_filter.mode = "shadow"
    with pytest.raises(ConfigError):
        validate_director_routes(cfg)


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: Config) -> TestClient:
    from openbiliclaw.api.app import create_app

    path = tmp_path / "config.toml"
    save_config(cfg, path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    # This test verifies HTTP config validation/persistence, not live discovery.
    # Keep the placeholder dependency graph from starting background model work.
    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.rebuild_from_config", AsyncMock()
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: load_config(path))
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda c, path=None: save_config(c, path or tmp_path / "config.toml"),
    )
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8420")


def test_jev_api_roundtrip_ready_and_no_secret(monkeypatch, tmp_path: Path) -> None:
    cfg = _configured()
    secret = "test-only-jev-secret-do-not-return"
    monkeypatch.setenv(cfg.decision.providers.jev.api_key_env, secret)
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.get("/api/config?reveal_keys=true")
    assert response.status_code == 200
    assert secret not in response.text
    assert response.json()["discovery"]["jev_provider"]["ready"] is True
    context = response.json()["discovery"]["jev_provider"]["context_budget"]
    assert context["max_request_tokens"] == 64_000
    assert context["max_state_question_tokens"] == 32_000
    assert context["max_request_bytes"] == 48_000
    response = client.put(
        "/api/config",
        json={
            "discovery": {
                "eval_scorer": "jev",
                "jev_evaluation": {
                    "franchise_labels": ["原神"],
                    "max_candidates_per_request": 2,
                    "max_questions_per_request": 20,
                    "max_question_bytes": 4096,
                },
            }
        },
    )
    assert response.status_code == 202, response.text
    returned = response.json()["config"]["discovery"]
    assert returned["eval_scorer"] == "jev"
    assert returned["jev_evaluation"]["franchise_labels"] == ["原神"]
    assert returned["jev_evaluation"]["max_candidates_per_request"] == 2
    assert returned["jev_evaluation"]["max_questions_per_request"] == 20
    assert returned["jev_evaluation"]["max_question_bytes"] == 4096
    loaded = load_config(tmp_path / "config.toml")
    assert loaded.discovery.eval_scorer == "jev"
    assert loaded.discovery.jev_evaluation.topic_labels == cfg.discovery.jev_evaluation.topic_labels
    assert secret not in (tmp_path / "config.toml").read_text()


@pytest.mark.parametrize("registered", [False, True])
def test_jev_api_readiness_requires_exact_model_capacity(monkeypatch, tmp_path, registered) -> None:
    cfg = _configured()
    cfg.decision.providers.jev.model = "operator-fixed"
    cfg.decision.providers.jev.context_limits = {
        "another-fixed": JevContextLimits(256_000, 128_000)
    }
    if registered:
        cfg.decision.providers.jev.context_limits["operator-fixed"] = JevContextLimits(
            128_000, 64_000
        )
    monkeypatch.setenv(cfg.decision.providers.jev.api_key_env, "test-only-key")
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.get("/api/config")
    assert response.status_code == 200
    readiness = response.json()["discovery"]["jev_provider"]
    assert readiness["ready"] is registered
    if registered:
        assert readiness["context_budget"]["max_request_bytes"] == 96_000
        assert readiness["context_budget"]["source"] == "operator_confirmed"
    else:
        assert readiness["context_budget"] is None
        assert "精确模型 ID" in readiness["reason"]
    assert readiness["context_limits"]["another-fixed"]["max_request_tokens"] == 256_000
    before = (tmp_path / "config.toml").read_bytes()
    response = client.put("/api/config", json={"discovery": {"eval_scorer": "jev"}})
    assert response.status_code == (202 if registered else 400), response.text
    if not registered:
        assert (tmp_path / "config.toml").read_bytes() == before


@pytest.mark.parametrize(
    "conflict", ["missing_key", "multimodal", "oversize", "unknown_field", "invalid_labels"]
)
def test_jev_api_rejects_without_persisting(monkeypatch, tmp_path: Path, conflict: str) -> None:
    cfg = _configured()
    monkeypatch.delenv(cfg.decision.providers.jev.api_key_env, raising=False)
    if conflict != "missing_key":
        monkeypatch.setenv(cfg.decision.providers.jev.api_key_env, "test-jev-key")
    client = _client(monkeypatch, tmp_path, cfg)
    before = (tmp_path / "config.toml").read_bytes()
    discovery: dict[str, object] = {"eval_scorer": "jev"}
    if conflict == "multimodal":
        discovery["multimodal_evaluation_enabled"] = True
    elif conflict == "oversize":
        discovery["jev_evaluation"] = {"max_request_bytes": 100000}
    elif conflict == "unknown_field":
        discovery["jev_evaluation"] = {"api_key": "not-a-real-secret"}
    elif conflict == "invalid_labels":
        discovery["jev_evaluation"] = {"topic_labels": [1, "valid"]}
    response = client.put("/api/config", json={"discovery": discovery})
    assert response.status_code in {400, 422}, response.text
    assert (tmp_path / "config.toml").read_bytes() == before
    assert "not-a-real-secret" not in response.text


def test_jev_evaluator_three_ui_surfaces_have_honest_choice_and_readiness() -> None:
    for html_path, js_path in (
        (
            "src/openbiliclaw/web/desktop/index.html",
            "src/openbiliclaw/web/desktop/assets/js/app.js",
        ),
        ("extension/popup/popup.html", "extension/popup/popup.js"),
        ("src/openbiliclaw/web/js/app.js", "src/openbiliclaw/web/js/app.js"),
    ):
        html = (ROOT / html_path).read_text()
        js = (ROOT / js_path).read_text()
        assert '<option value="jev">Jev（完整评估，纯文本）</option>' in html
        for phrase in ("不隐式调用 LLM", "新 IP 保留未知", "不支持封面图片", "字节预算"):
            assert phrase in html
        assert "jev_provider" in js
        assert "api_key_env" in js
        assert "eval_scorer:" in js
