from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import (
    Config,
    EmbeddingConfig,
    LLMConfig,
    LLMProviderConfig,
    save_config,
)
from openbiliclaw.config import (
    load_config as load_config_from_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_client(
    monkeypatch,
    tmp_path: Path,
    initial_cfg: Config,
) -> tuple[TestClient, Config, Path]:
    config_path = tmp_path / "config.toml"
    save_config(initial_cfg, config_path)

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: initial_cfg)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda cfg, path=None: save_config(cfg, config_path),
    )

    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), initial_cfg, config_path


def _base_config() -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="sk-real-key-1234567890abcdef",
                model="gpt-4o-mini",
            ),
            claude=LLMProviderConfig(api_key="claude-real-key", model="claude-3-5-haiku"),
            deepseek=LLMProviderConfig(api_key="deepseek-real-key", model="deepseek-chat"),
            openrouter=LLMProviderConfig(api_key="openrouter-real-key", model="openrouter/auto"),
            openai_compatible=LLMProviderConfig(
                api_key="compat-real-key",
                model="mimo-v2.5-pro",
                base_url="https://token-plan-sgp.xiaomimimo.com/v1",
            ),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-embedding-real-key",
                base_url="https://embed.example.com/v1",
            ),
        )
    )


def test_put_config_ignores_masked_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": "sk-d****cdef"}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_ignores_empty_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": ""}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_writes_real_new_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"api_key": "sk-new-real-key-fedcba0987654321"}}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == (
        "sk-new-real-key-fedcba0987654321"
    )


def test_put_config_ignores_empty_chat_provider_model(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"model": ""}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.model == "gpt-4o-mini"


def test_put_config_writes_real_new_chat_provider_model(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.model == "gpt-4.1-mini"


def test_put_config_round_trips_openai_auth_mode(monkeypatch, tmp_path) -> None:
    from openbiliclaw.llm.codex_auth import CodexCredentials

    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.load_codex_credentials",
        lambda: CodexCredentials("access-token", "refresh-token", 9999999999),
    )

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"auth_mode": "codex_oauth"}}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.auth_mode == "codex_oauth"
    get_response = client.get("/api/config")
    assert get_response.status_code == 200
    assert get_response.json()["llm"]["openai"]["auth_mode"] == "codex_oauth"


def test_put_config_round_trips_explicit_fallback_providers(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={
            "llm": {
                "fallback_provider": "gemini",
                "embedding": {"fallback_provider": "ollama"},
            }
        },
    )

    assert response.status_code == 200
    loaded = load_config_from_path(config_path)
    assert loaded.llm.fallback_provider == "gemini"
    assert loaded.llm.embedding.fallback_provider == "ollama"

    get_response = client.get("/api/config")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["llm"]["fallback_provider"] == "gemini"
    assert body["llm"]["embedding"]["fallback_provider"] == "ollama"


def test_put_config_round_trips_embedding_output_dimensionality(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"embedding": {"output_dimensionality": 768}}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.embedding.output_dimensionality == 768

    get_response = client.get("/api/config")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["llm"]["embedding"]["output_dimensionality"] == 768


def test_put_config_rejects_invalid_embedding_output_dimensionality(
    monkeypatch,
    tmp_path,
) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"embedding": {"output_dimensionality": "wide"}}},
    )

    assert response.status_code == 400
    assert load_config_from_path(config_path).llm.embedding.output_dimensionality == 1024


def test_put_config_ignores_whitespace_only_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": "   "}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_uses_same_guard_for_other_chat_providers(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    for provider_name in ("claude", "deepseek", "openrouter", "openai_compatible"):
        before = getattr(load_config_from_path(config_path).llm, provider_name).api_key
        masked = before[:2] + "****" + before[-2:]
        response = client.put(
            "/api/config",
            json={"llm": {provider_name: {"api_key": masked}}},
        )
        assert response.status_code == 200
        assert getattr(load_config_from_path(config_path).llm, provider_name).api_key == before

        response = client.put(
            "/api/config",
            json={"llm": {provider_name: {"api_key": ""}}},
        )
        assert response.status_code == 200
        assert getattr(load_config_from_path(config_path).llm, provider_name).api_key == before


def test_put_config_explicit_reset_clears_allowlisted_secret(monkeypatch, tmp_path) -> None:
    client, cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"reset_fields": ["llm.openai.api_key"]})

    assert response.status_code == 200
    assert cfg.llm.openai.api_key == ""
    assert load_config_from_path(config_path).llm.openai.api_key == ""


def test_put_config_unknown_reset_is_rejected_without_mutation(monkeypatch, tmp_path) -> None:
    client, cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = config_path.read_text(encoding="utf-8")

    response = client.put(
        "/api/config",
        json={
            "reset_fields": ["storage.db_path"],
            "llm": {"openai": {"model": "gpt-4.1-mini"}},
        },
    )

    assert response.status_code == 400
    assert config_path.read_text(encoding="utf-8") == before
    assert cfg.llm.openai.model == "gpt-4o-mini"


def test_put_config_ignores_empty_embedding_model_and_base_url(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"embedding": {"model": "", "base_url": ""}}},
    )

    assert response.status_code == 200
    embedding = load_config_from_path(config_path).llm.embedding
    assert embedding.model == "text-embedding-3-small"
    assert embedding.base_url == "https://embed.example.com/v1"


# ── Source cookie guards (bilibili masked/empty echo; dy/x file routing) ──


def _cookie_config(tmp_path: Path) -> Config:
    from openbiliclaw.config import BilibiliConfig

    cfg = _base_config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.bilibili = BilibiliConfig(
        auth_method="cookie",
        cookie="SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42",
    )
    return cfg


def test_put_config_ignores_masked_bilibili_cookie_echo(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={"bilibili": {"cookie": "SESS************ID=42"}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )


def test_put_config_ignores_empty_bilibili_cookie(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put("/api/config", json={"bilibili": {"cookie": ""}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )


def test_put_config_writes_real_new_bilibili_cookie(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={"bilibili": {"cookie": "SESSDATA=new-sess; bili_jct=new-csrf; DedeUserID=43"}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).bilibili.cookie == (
        "SESSDATA=new-sess; bili_jct=new-csrf; DedeUserID=43"
    )


def test_put_config_routes_douyin_cookie_to_data_file(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"douyin": {"cookie": "sessionid=dy-sess; ttwid=dy-tw"}}},
    )

    assert response.status_code == 200
    # Secret lands in data/douyin_cookie.json, never in config.toml.
    assert DouyinCookieManager(cfg.data_path).load_cookie() == "sessionid=dy-sess; ttwid=dy-tw"
    assert "dy-sess" not in config_path.read_text(encoding="utf-8")


def test_put_config_routes_x_cookie_to_data_file(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.x_auth import XCookieManager

    monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"twitter": {"cookie": "auth_token=x-at; ct0=x-csrf"}}},
    )

    assert response.status_code == 200
    assert XCookieManager(cfg.data_path).load_cookie() == "auth_token=x-at; ct0=x-csrf"
    assert "x-at" not in config_path.read_text(encoding="utf-8")


def test_put_config_ignores_masked_douyin_cookie_echo(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    manager = DouyinCookieManager(cfg.data_path)
    manager.set_cookie("sessionid=dy-real", source="test")
    client, _cfg, _config_path = _make_client(monkeypatch, tmp_path, cfg)

    response = client.put(
        "/api/config",
        json={"sources": {"douyin": {"cookie": "sess************real"}}},
    )

    assert response.status_code == 200
    assert manager.load_cookie() == "sessionid=dy-real"


def test_put_config_empty_cookie_env_keeps_existing_name(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _cookie_config(tmp_path))

    response = client.put(
        "/api/config",
        json={
            "sources": {
                "douyin": {"cookie_env": ""},
                "twitter": {"cookie_env": ""},
            }
        },
    )

    assert response.status_code == 200
    saved = load_config_from_path(config_path)
    assert saved.sources.douyin.cookie_env == "OPENBILICLAW_DOUYIN_COOKIE"
    assert saved.sources.twitter.cookie_env == "OPENBILICLAW_X_COOKIE"


def test_get_config_exposes_douyin_and_x_cookies_like_bilibili(monkeypatch, tmp_path) -> None:
    from openbiliclaw.sources.douyin_auth import DouyinCookieManager
    from openbiliclaw.sources.x_auth import XCookieManager

    monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
    monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
    cfg = _cookie_config(tmp_path)
    DouyinCookieManager(cfg.data_path).set_cookie(
        "sessionid=dy-sess-1234567890; ttwid=dy-tw", source="test"
    )
    XCookieManager(cfg.data_path).set_cookie(
        "auth_token=x-at-1234567890; ct0=x-csrf", source="test"
    )
    client, _cfg, _config_path = _make_client(monkeypatch, tmp_path, cfg)

    masked = client.get("/api/config").json()
    assert "****" in masked["sources"]["douyin"]["cookie"]
    assert "dy-sess-1234567890" not in masked["sources"]["douyin"]["cookie"]
    assert "****" in masked["sources"]["twitter"]["cookie"]
    assert "****" in masked["bilibili"]["cookie"]

    revealed = client.get("/api/config?reveal_keys=true").json()
    assert revealed["sources"]["douyin"]["cookie"] == "sessionid=dy-sess-1234567890; ttwid=dy-tw"
    assert revealed["sources"]["twitter"]["cookie"] == "auth_token=x-at-1234567890; ct0=x-csrf"
    assert revealed["bilibili"]["cookie"] == (
        "SESSDATA=real-sess; bili_jct=real-csrf; DedeUserID=42"
    )
