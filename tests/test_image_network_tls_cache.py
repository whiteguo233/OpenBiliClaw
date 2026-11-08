"""Default TLS reuse must not retain stale or custom certificate trust."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from openbiliclaw.runtime import image_network as network


@pytest.fixture(autouse=True)
def clear_default_context_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    network._cached_default_ssl_context.cache_clear()


def test_default_context_is_verified_and_separate_by_trust_mode() -> None:
    direct = network._image_ssl_context(trust_env=False)
    inherited = network._image_ssl_context(trust_env=True)
    assert network._image_ssl_context(trust_env=False) is direct
    assert network._image_ssl_context(trust_env=True) is inherited
    assert direct is not inherited
    for context in (direct, inherited):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_custom_ca_is_reloaded_each_time_and_direct_ignores_it(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    contexts: list[bool] = []

    def create_context(*, trust_env: bool) -> ssl.SSLContext:
        contexts.append(trust_env)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(network.httpx, "create_ssl_context", create_context)
    direct = network._image_ssl_context(trust_env=False)
    monkeypatch.setenv(variable, "custom-ca")
    first = network._image_ssl_context(trust_env=True)
    assert network._image_ssl_context(trust_env=True) is not first
    monkeypatch.setenv(variable, "replacement-ca")
    assert network._image_ssl_context(trust_env=True) is not first
    assert network._image_ssl_context(trust_env=False) is direct
    assert contexts == [False, True, True, True]


def test_bundle_change_and_process_change_rebuild_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(Path(network.certifi.where()).read_bytes())
    monkeypatch.setattr(network.certifi, "where", lambda: str(bundle))
    initial = network._image_ssl_context(trust_env=False)
    bundle.write_bytes(bundle.read_bytes() + b"\n")
    replacement = network._image_ssl_context(trust_env=False)
    assert replacement is not initial
    process_id = network.os.getpid()
    monkeypatch.setattr(network.os, "getpid", lambda: process_id + 1)
    assert network._image_ssl_context(trust_env=False) is not replacement
    bundle.unlink()
    with pytest.raises(FileNotFoundError):
        network._image_ssl_context(trust_env=False)
