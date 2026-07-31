"""Tests for the TLS reverse proxy module."""

from __future__ import annotations

import ipaddress
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# Module ref needed for patch.multiple() targets
from openbiliclaw import tls_proxy as _tls_proxy_mod
from openbiliclaw.tls_proxy import (
    _build_san_entries,
    _ensure_certs,
    _origin_allowed,
    _rewrite_origin,
)

# ── _build_san_entries ──────────────────────────────────────────────────────


class TestBuildSanEntries:
    """SAN list construction from user-provided names."""

    def test_always_includes_localhost_and_loopback(self) -> None:
        entries = _build_san_entries([])
        dns_names = [e.value for e in entries if isinstance(e.value, str)]
        ip_addrs = [str(e.value) for e in entries if not isinstance(e.value, str)]
        assert "localhost" in dns_names
        assert "127.0.0.1" in ip_addrs

    def test_adds_user_provided_dns_names(self) -> None:
        entries = _build_san_entries(["mybili.lan", "server.home"])
        dns_names = [e.value for e in entries if isinstance(e.value, str)]
        assert "mybili.lan" in dns_names
        assert "server.home" in dns_names

    def test_adds_user_provided_ip(self) -> None:
        entries = _build_san_entries(["192.168.1.100"])
        ip_values = [str(e.value) for e in entries if not isinstance(e.value, str)]
        assert "192.168.1.100" in ip_values

    def test_does_not_duplicate_localhost(self) -> None:
        entries = _build_san_entries(["localhost", "127.0.0.1"])
        dns_names = [e.value for e in entries if isinstance(e.value, str)]
        assert dns_names.count("localhost") == 1

    def test_empty_string_ignored(self) -> None:
        entries = _build_san_entries([""])
        assert len(entries) == 2  # only localhost + 127.0.0.1


# ── _origin_allowed ─────────────────────────────────────────────────────────


class TestOriginAllowed:
    """Origin validation for cross-origin access control."""

    def test_extension_origin_allowed(self) -> None:
        assert _origin_allowed("chrome-extension://abcdefghijklmnop") is True

    def test_same_site_https_origin_allowed(self) -> None:
        assert _origin_allowed("https://sushe:2119") is True
        assert _origin_allowed("https://192.168.1.100:2119") is True

    def test_none_origin_allowed(self) -> None:
        assert _origin_allowed(None) is True
        assert _origin_allowed("") is True

    def test_foreign_origin_blocked(self) -> None:
        assert _origin_allowed("https://evil.com") is False
        assert _origin_allowed("http://evil.com") is False

    def test_http_same_host_allowed(self) -> None:
        # http://<host>:2119 is allowed (proxy forwards to backend which accepts http)
        assert _origin_allowed("http://sushe:2119") is True


# ── _rewrite_origin ─────────────────────────────────────────────────────────


class TestRewriteOrigin:
    """Origin scheme rewriting for backend same-origin check."""

    def test_https_rewritten_to_http(self) -> None:
        assert _rewrite_origin("https://sushe:2119") == "http://sushe:2119"

    def test_extension_origin_unchanged(self) -> None:
        assert _rewrite_origin("chrome-extension://abc") == "chrome-extension://abc"

    def test_http_origin_unchanged(self) -> None:
        assert _rewrite_origin("http://sushe:2119") == "http://sushe:2119"

    def test_empty_origin_unchanged(self) -> None:
        assert _rewrite_origin("") == ""


# ── _ensure_certs ───────────────────────────────────────────────────────────


class TestEnsureCerts:
    """Certificate auto-generation."""

    def test_generates_all_files_when_missing(self, tmp_path: Path) -> None:
        with patch.multiple(
            _tls_proxy_mod,
            _CERT_DIR=str(tmp_path),
            _CERT_FILE=str(tmp_path / "srv.crt"),
            _KEY_FILE=str(tmp_path / "srv.key"),
            _CA_FILE=str(tmp_path / "ca.crt"),
            _CRL_FILE=str(tmp_path / "ca.crl"),
            _AUTO_GEN=True,
            _SAN_NAMES=["192.168.1.50", "myhost.lan"],
        ):
            _ensure_certs()
            assert (tmp_path / "srv.crt").exists()
            assert (tmp_path / "srv.key").exists()
            assert (tmp_path / "ca.crt").exists()
            assert (tmp_path / "ca.crl").exists()

    def test_skips_when_certs_exist(self, tmp_path: Path) -> None:
        # Pre-create dummy cert files
        (tmp_path / "srv.crt").write_text("existing cert")
        (tmp_path / "srv.key").write_text("existing key")
        original_mtime = os.path.getmtime(tmp_path / "srv.crt")

        with patch.multiple(
            _tls_proxy_mod,
            _CERT_DIR=str(tmp_path),
            _CERT_FILE=str(tmp_path / "srv.crt"),
            _KEY_FILE=str(tmp_path / "srv.key"),
            _AUTO_GEN=True,
            _SAN_NAMES=[],
        ):
            _ensure_certs()
            # Files should not be overwritten
            assert os.path.getmtime(tmp_path / "srv.crt") == original_mtime

    def test_generated_cert_has_correct_san(self, tmp_path: Path) -> None:
        from cryptography import x509

        with patch.multiple(
            _tls_proxy_mod,
            _CERT_DIR=str(tmp_path),
            _CERT_FILE=str(tmp_path / "srv.crt"),
            _KEY_FILE=str(tmp_path / "srv.key"),
            _CA_FILE=str(tmp_path / "ca.crt"),
            _CRL_FILE=str(tmp_path / "ca.crl"),
            _AUTO_GEN=True,
            _SAN_NAMES=["10.0.0.5", "bili.server.lan"],
        ):
            _ensure_certs()
            cert = x509.load_pem_x509_certificate((tmp_path / "srv.crt").read_bytes())
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            dns_names = san_ext.value.get_values_for_type(x509.DNSName)
            ip_addrs = san_ext.value.get_values_for_type(x509.IPAddress)
            assert "localhost" in dns_names
            assert "bili.server.lan" in dns_names
            assert ipaddress.IPv4Address("127.0.0.1") in ip_addrs
            assert ipaddress.IPv4Address("10.0.0.5") in ip_addrs

    def test_generated_cert_has_ca_true(self, tmp_path: Path) -> None:
        from cryptography import x509

        with patch.multiple(
            _tls_proxy_mod,
            _CERT_DIR=str(tmp_path),
            _CERT_FILE=str(tmp_path / "srv.crt"),
            _KEY_FILE=str(tmp_path / "srv.key"),
            _CA_FILE=str(tmp_path / "ca.crt"),
            _CRL_FILE=str(tmp_path / "ca.crl"),
            _AUTO_GEN=True,
            _SAN_NAMES=[],
        ):
            _ensure_certs()
            ca_cert = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
            bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
            assert bc.value.ca is True

    def test_generated_cert_signed_by_ca(self, tmp_path: Path) -> None:
        from cryptography import x509

        with patch.multiple(
            _tls_proxy_mod,
            _CERT_DIR=str(tmp_path),
            _CERT_FILE=str(tmp_path / "srv.crt"),
            _KEY_FILE=str(tmp_path / "srv.key"),
            _CA_FILE=str(tmp_path / "ca.crt"),
            _CRL_FILE=str(tmp_path / "ca.crl"),
            _AUTO_GEN=True,
            _SAN_NAMES=[],
        ):
            _ensure_certs()
            ca_cert = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
            srv_cert = x509.load_pem_x509_certificate((tmp_path / "srv.crt").read_bytes())
            assert srv_cert.issuer == ca_cert.subject


# ── config integration ──────────────────────────────────────────────────────


class TestTlsProxyConfig:
    """Config loading for [tls_proxy] section."""

    def test_defaults_disabled(self) -> None:
        from openbiliclaw.config import TlsProxyConfig

        cfg = TlsProxyConfig()
        assert cfg.enabled is False
        assert cfg.port == 2119
        assert cfg.cert_dir == ""
        assert cfg.san_names == []

    def test_env_var_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openbiliclaw.config import _build_tls_proxy

        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_ENABLED", "1")
        cfg = _build_tls_proxy({})
        assert cfg.enabled is True

    def test_san_names_from_config(self) -> None:
        from openbiliclaw.config import _build_tls_proxy

        cfg = _build_tls_proxy({"tls_proxy": {"san_names": ["10.0.0.1", "host.lan"]}})
        assert cfg.san_names == ["10.0.0.1", "host.lan"]

    def test_san_names_as_comma_string(self) -> None:
        from openbiliclaw.config import _build_tls_proxy

        cfg = _build_tls_proxy({"tls_proxy": {"san_names": "10.0.0.1, host.lan"}})
        assert cfg.san_names == ["10.0.0.1", "host.lan"]
