"""Exercise real IPC workers behind the relay and hostile credential reuse."""

from __future__ import annotations

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openbiliclaw.llm.relay_auth import RelayDeniedError, RelayStore, public_id, signed_headers
from openbiliclaw.llm.sponsored_errors import SponsoredAuthError, SponsoredUpstreamError
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS, approved_registry
from openbiliclaw.llm.sponsored_relay_client import SponsoredRelayProvider, validate_relay_url
from openbiliclaw.sponsored_relay import create_relay


async def test_disconnect_before_body_releases_server_capacity(tmp_path):
    from starlette.requests import ClientDisconnect

    from openbiliclaw.llm.relay_auth import wire_json

    app = create_relay(
        command=[sys.executable, "-m", "openbiliclaw.llm.sponsored_mock_runtime", "--approved"],
        state_path=tmp_path / "relay.db",
        worker_count=2,
    )
    key = Ed25519PrivateKey.generate()
    token = enroll(RelayStore(tmp_path / "relay.db"), key)
    body = wire_json(
        {
            "task_id": "llm.embedding_text",
            "contract_version": 3,
            "input": {"text": "hello"},
            "stream": False,
        }
    )
    statuses = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
        raise OSError("test disconnect before body")

    async with app.router.lifespan_context(app):
        for _ in range(4):
            headers = httpx.Headers(signed_headers(key, "/v1/task", body, token))
            scope = {
                "type": "http",
                "asgi": {"spec_version": "2.4"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/v1/task",
                "query_string": b"",
                "root_path": "",
                "headers": [(key.lower(), value) for key, value in headers.raw],
                "client": ("127.0.0.1", 12345),
                "server": ("relay.example", 443),
            }
            with pytest.raises(ClientDisconnect):
                await app(scope, receive, send)
    assert statuses == [200] * 4


def test_official_relay_package_omits_key_sdk_and_rejects_old_bundle(tmp_path, monkeypatch):
    from openbiliclaw.runtime import relay_release, sponsored_bundle
    from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings

    monkeypatch.setattr(relay_release, "OFFICIAL_RELAY_URL", "https://relay.example")
    monkeypatch.setattr(
        sponsored_bundle, "ensure_runtime", lambda: pytest.fail("must not download provider key")
    )
    assert SponsoredRuntimeSettings().relay_url == "https://relay.example"
    target = tmp_path / "sponsored"
    assert sponsored_bundle.stage_runtime_bundle(target)
    assert not target.exists()
    target.mkdir()
    with pytest.raises(sponsored_bundle.BundleError, match="key-bearing"):
        sponsored_bundle.stage_runtime_bundle(target)


async def test_relay_router_does_not_fall_back_to_local_shared_key(tmp_path, monkeypatch):
    from openbiliclaw.llm.sponsored_router import close_shared_provider, shared_sponsored_provider
    from openbiliclaw.runtime import sponsored_bundle
    from openbiliclaw.sponsored_runtime_state import (
        SponsoredRuntimeSettings,
        install_sponsored_settings,
        reset_sponsored_settings,
    )

    monkeypatch.setattr(
        sponsored_bundle,
        "installed_runtime_command",
        lambda: pytest.fail("must not load local key"),
    )
    install_sponsored_settings(
        SponsoredRuntimeSettings(enabled=True, relay_url="https://relay.example"),
        data_path=tmp_path,
    )
    try:
        assert isinstance(shared_sponsored_provider(), SponsoredRelayProvider)
    finally:
        await close_shared_provider()
        reset_sponsored_settings()


def test_device_identity_is_atomic_and_private(tmp_path):
    from openbiliclaw.llm.relay_auth import load_device_key

    with ThreadPoolExecutor(max_workers=8) as workers:
        keys = list(
            workers.map(lambda _: public_id(load_device_key(tmp_path / "device.key")), range(16))
        )
    assert len(set(keys)) == 1


def test_config_round_trip_preserves_relay(tmp_path, monkeypatch):
    from openbiliclaw.config import _build_config, load_config_with_diagnostics, save_config

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    path = tmp_path / "config.toml"
    cfg = _build_config(
        {"llm": {"sponsored": {"enabled": True, "relay_url": "https://relay.example"}}}
    )
    save_config(cfg, path)
    reloaded, _ = load_config_with_diagnostics(path)
    assert reloaded.llm.sponsored.relay_url == "https://relay.example"


def enroll(store, key):
    device = store.authenticate(
        httpx.Headers(signed_headers(key, "/v1/session", b"{}")),
        "/v1/session",
        b"{}",
        ip="192.0.2.1",
    )
    return store.session(device)["token"]


def test_captured_token_replay_tamper_expiry_and_revocation(tmp_path):
    store = RelayStore(tmp_path / "relay.db")
    key = Ed25519PrivateKey.generate()
    token = enroll(store, key)
    body = b'{"input":"hello"}'
    headers = httpx.Headers(signed_headers(key, "/v1/task", body, token))
    assert store.authenticate(headers, "/v1/task", body) == public_id(key)
    with pytest.raises(RelayDeniedError, match="replayed"):
        store.authenticate(headers, "/v1/task", body)
    with pytest.raises(RelayDeniedError):
        store.authenticate(httpx.Headers({"Authorization": f"Bearer {token}"}), "/v1/task", body)
    with pytest.raises(RelayDeniedError):
        store.authenticate(
            httpx.Headers(signed_headers(key, "/v1/task", body, token)), "/v1/task", body + b" "
        )
    other = Ed25519PrivateKey.generate()
    enroll(store, other)
    with pytest.raises(RelayDeniedError):
        store.authenticate(
            httpx.Headers(signed_headers(other, "/v1/task", body, token)), "/v1/task", body
        )
    with store.connect() as db:
        db.execute("UPDATE sessions SET expires=?", (int(time.time()) - 1,))
    with pytest.raises(RelayDeniedError):
        store.authenticate(
            httpx.Headers(signed_headers(key, "/v1/task", body, token)), "/v1/task", body
        )
    enroll(store, key)
    store.revoke(public_id(key))
    with pytest.raises(RelayDeniedError):
        enroll(store, key)


def test_atomic_concurrent_replay_and_quota(tmp_path):
    store = RelayStore(tmp_path / "relay.db")
    key = Ed25519PrivateKey.generate()
    token = enroll(store, key)
    headers = httpx.Headers(signed_headers(key, "/v1/task", b"{}", token))

    def attempt(_):
        try:
            store.authenticate(headers, "/v1/task", b"{}")
            return True
        except RelayDeniedError:
            return False

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(attempt, range(8))) == 1
    store.reserve(public_id(key), 999999)
    with pytest.raises(RelayDeniedError, match="quota"):
        RelayStore(tmp_path / "relay.db").reserve(public_id(key), 2)


@pytest.mark.parametrize(
    "url",
    [
        "http://relay.example",
        "https://u:p@relay.example",
        "https://relay.example/v1",
        "https://relay.example?key=x",
        "https://relay.example#x",
    ],
)
def test_reject_insecure_or_credentialed_origin(url):
    with pytest.raises(ValueError):
        validate_relay_url(url)


@pytest.mark.parametrize("model", CHAT_MODELS)
async def test_relay_real_ipc_chat_stream_embedding_concurrency(tmp_path, model):
    app = create_relay(
        command=[sys.executable, "-m", "openbiliclaw.llm.sponsored_mock_runtime", "--approved"],
        state_path=tmp_path / "server" / "relay.db",
    )
    provider = SponsoredRelayProvider(
        "https://relay.example",
        tmp_path / "client" / "device.key",
        model=model,
        registry=approved_registry(),
        fallback_to_other_free_model=False,
    )
    await provider._http.aclose()
    provider._http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://relay.example"
    )
    async with app.router.lifespan_context(app):
        try:

            async def chat():
                chunks = [
                    c
                    async for c in provider.stream_complete(
                        [{"role": "user", "content": "hello"}], max_tokens=64
                    )
                ]
                assert any(c.delta for c in chunks)
                assert chunks[-1].response.model == model
                return chunks[-1].response

            result, vector, text = await asyncio.gather(
                chat(),
                provider.embedding("hello"),
                provider.complete([{"role": "user", "content": "hello"}], max_tokens=64),
            )
            assert result.model == text.model == model
            assert len(vector) == 1024
            RelayStore(tmp_path / "server" / "relay.db").revoke(public_id(provider._device))
            with pytest.raises(SponsoredAuthError):
                await provider.embedding("revoked")
        finally:
            await provider.aclose()


async def test_redirect_does_not_forward_device_token(tmp_path):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example/steal"})

    provider = SponsoredRelayProvider("https://relay.example", tmp_path / "device.key")
    await provider._http.aclose()
    provider._http = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=False
    )
    try:
        with pytest.raises(SponsoredUpstreamError):
            await provider._ensure_started()
        assert calls == ["https://relay.example/v1/session"]
    finally:
        await provider.aclose()
