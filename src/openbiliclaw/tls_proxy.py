#!/usr/bin/env python3
"""OpenBiliClaw TLS reverse proxy.

Sits in front of ``openbiliclaw-backend`` on :2119, terminates TLS with the
project's self-signed certificate, and forwards traffic to the backend over
the internal Docker network. Beyond TLS termination its only job is to keep
the backend's cross-origin bearer-login check happy *without touching backend
code*:

  * Validate the incoming ``Origin``: allow the browser extension
    (``chrome-extension://``) and same-site web origins
    (``https://<host>:2119``); reject anything else with ``403`` before it
    ever reaches the backend.
  * Rewrite the web ``Origin`` scheme ``https://<host>:2119`` ->
    ``http://<host>:2119`` while preserving the original ``Host`` header, so
    the backend computes its own origin as ``http://<host>:2119`` and treats
    the request as same-origin (web password login no longer returns
    ``origin_forbidden``).

If no certificate exists in the cert directory on startup the proxy
auto-generates a self-signed CA + server certificate pair (RSA 2048,
SAN: localhost/127.0.0.1 + user-configured names, 3650-day validity)

Everything is configurable via environment variables with sane defaults, so
the container starts with no arguments.
"""

import datetime
import ipaddress
import os
import select
import sys
import ssl
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Module state — populated by ``start_tls_proxy()`` before the server
# starts.  Handler methods read these as plain module globals.
_HOST: str = "0.0.0.0"
_PORT: int = 2119
_BACKEND_HOST: str = "openbiliclaw-backend"
_BACKEND_PORT: int = 8420
_CERT_DIR: str = "/certs"
_CERT_FILE: str = "/certs/srv.crt"
_KEY_FILE: str = "/certs/srv.key"
_CRL_FILE: str = "/certs/ca.crl"
_CA_FILE: str = "/certs/ca.crt"
_AUTO_GEN: bool = False
_SAN_NAMES: list[str] = []


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    if origin.startswith("chrome-extension://"):
        return True
    # Web UI origin: any https://<host>:2119 (the port this proxy listens on).
    if origin.startswith("https://") and origin.endswith(":2119"):
        return True
    if origin.startswith("http://") and origin.endswith(":2119"):
        return True
    return False


def _build_san_entries(extra_names: list[str]) -> list:
    """Build SAN list: always localhost + 127.0.0.1, plus user-provided names."""
    entries = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for name in extra_names:
        if not name or name in ("localhost", "127.0.0.1"):
            continue
        try:
            ipaddress.ip_address(name)
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return entries


def _ensure_certs() -> None:
    """Verify server cert/key exist; auto-generate only if explicitly enabled.

    Certificates are NEVER generated implicitly — this avoids overwriting
    user-provided certs just because the proxy started before they were
    copied in, or because the user's cert files use different names.
    Auto-generation is controlled by the ``auto_gen_certs`` parameter
    passed to ``start_tls_proxy()`` (which sets the ``_AUTO_GEN`` flag).
    """
    srv_crt_path = os.path.join(_CERT_DIR, "srv.crt")
    srv_key_path = os.path.join(_CERT_DIR, "srv.key")
    if os.path.isfile(srv_crt_path) and os.path.isfile(srv_key_path):
        return

    if not _AUTO_GEN:
        sys.exit(
            f"\n  No server certificate found.\n"
            f"  Expected:  {srv_crt_path}\n"
            f"             {srv_key_path}\n\n"
            f"  To fix, either:\n"
            f"    1) Copy your cert files into the volume mounted at {_CERT_DIR}.\n"
            f"    2) Set AUTO_GEN_CERTS=1 to auto-generate a self-signed pair\n"
            f"       (CA: OpenBiliClaw Local CA, SAN: localhost/127.0.0.1 + configured names\n"
            f"        then download https://<host>:2119/ca.crt and trust the CA).\n"
        )

    # --- Explicit opt-in: auto-generate CA + server cert + CRL ---
    # Replicates the project's gen-certs.sh logic:
    #   CA: CN=OpenBiliClaw Local CA, RSA 2048, CA:TRUE, keyCertSign+cRLSign
    #   Server: CN=<first SAN or localhost>, SAN localhost/127.0.0.1 + configured names,
    #           signed by CA, RSA 2048, serverAuth, 3650 days
    #   CRL: empty revocation list, CRL DP -> https://localhost:2119/ca.crl
    os.makedirs(_CERT_DIR, exist_ok=True)
    now = datetime.datetime.now(datetime.UTC)

    # --- CA key + self-signed cert ---
    ca_key = rsa.generate_private_key(65537, 2048)
    ca_subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "OpenBiliClaw Local CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.CRLDistributionPoints(
                [
                    x509.DistributionPoint(
                        full_name=[x509.UniformResourceIdentifier("https://localhost:2119/ca.crl")],
                        relative_name=None,
                        reasons=None,
                        crl_issuer=None,
                    )
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_cert_bytes = ca_cert.public_bytes(serialization.Encoding.PEM)
    ca_key_bytes = ca_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    # --- Server key + CSR-signed cert ---
    srv_key = rsa.generate_private_key(65537, 2048)
    srv_subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, _SAN_NAMES[0] if _SAN_NAMES else "localhost")])
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(srv_subject)
        .issuer_name(ca_subject)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(_build_san_entries(_SAN_NAMES)),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.CRLDistributionPoints(
                [
                    x509.DistributionPoint(
                        full_name=[x509.UniformResourceIdentifier("https://localhost:2119/ca.crl")],
                        relative_name=None,
                        reasons=None,
                        crl_issuer=None,
                    )
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    srv_cert_bytes = srv_cert.public_bytes(serialization.Encoding.PEM)
    srv_key_bytes = srv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    # --- Empty CRL ---
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_subject)
        .last_update(now)
        .next_update(now + datetime.timedelta(days=3650))
        .add_extension(x509.CRLNumber(1000), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    crl_bytes = crl.public_bytes(serialization.Encoding.PEM)

    # --- Write all files ---
    _write_file(os.path.join(_CERT_DIR, "ca.crt"), ca_cert_bytes, 0o644)
    _write_file(os.path.join(_CERT_DIR, "ca.key"), ca_key_bytes, 0o600)
    _write_file(os.path.join(_CERT_DIR, "ca.crl"), crl_bytes, 0o644)
    _write_file(srv_crt_path, srv_cert_bytes, 0o644)
    _write_file(srv_key_path, srv_key_bytes, 0o600)


def _write_file(path: str, data: bytes, mode: int) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


def _rewrite_origin(origin: str) -> str:
    # Flatten only the web scheme so the backend derives the same origin.
    if origin.startswith("https://"):
        return "http://" + origin[len("https://"):]
    return origin


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "openbiliclaw-tls-proxy/1.0"

    def _reply(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if body and "content-length" not in (h.lower() for h in (headers or {})):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _relay_ws(self, conn):
        """Bidirectional raw-socket relay after a successful WebSocket upgrade.

        Once the 101 has been sent to the client we stop treating the
        connection as HTTP and pass raw bytes between the client TLS socket
        and the backend plain TCP socket until either side closes.
        """
        client = self.connection
        backend = conn.sock
        try:
            while True:
                readable, _, _ = select.select([client, backend], [], [], 60)
                if not readable:
                    break
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    if sock is client:
                        backend.sendall(data)
                    else:
                        client.sendall(data)
        except (OSError, ConnectionError):
            pass

    def _handle(self):
        path = self.path.split("?", 1)[0]

        # CA certificate endpoint: serve ca.crt so new clients can download it.
        if path == "/ca.crt":
            try:
                with open(_CA_FILE, "rb") as fh:
                    data = fh.read()
                self._reply(200, data, {"Content-Type": "application/x-x509-ca-cert"})
            except OSError:
                self._reply(404, b"not found")
            return

        # CRL endpoint: serve the revocation list directly from the mounted certs.
        if path == "/ca.crl":
            try:
                with open(_CRL_FILE, "rb") as fh:
                    data = fh.read()
                self._reply(200, data, {"Content-Type": "application/pkix-crl"})
            except OSError:
                self._reply(404, b"not found")
            return

        origin = self.headers.get("Origin", "")
        if not _origin_allowed(origin):
            self._reply(
                403,
                b"origin not allowed",
                {"Content-Type": "text/plain; charset=utf-8"},
            )
            return

        fwd_origin = _rewrite_origin(origin) if origin else None
        is_websocket = self.headers.get("Upgrade", "").lower() == "websocket"

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else None

        # Strip hop-by-hop headers.  For WebSocket we must preserve
        # Connection (carries the Upgrade directive) for the handshake.
        strip = {"transfer-encoding", "content-length"}
        if not is_websocket:
            strip.add("connection")
            strip.add("upgrade")
        fwd_headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in strip
        }
        # Preserve the original Host so the backend derives the same origin
        # (http://<host>:2119) that we rewrote the Origin to.
        if self.headers.get("Host"):
            fwd_headers["Host"] = self.headers.get("Host")
        if fwd_origin is not None:
            fwd_headers["Origin"] = fwd_origin

        try:
            conn = HTTPConnection(_BACKEND_HOST, _BACKEND_PORT, timeout=60)
            conn.request(self.command, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()

            # WebSocket upgrade: forward 101 and enter raw-socket relay.
            if is_websocket and resp.status == 101:
                self.send_response(101)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding",):
                        self.send_header(k, v)
                self.end_headers()
                self._relay_ws(conn)
                return

            data = resp.read()
            out = {
                k: v
                for k, v in resp.getheaders()
                if k.lower() not in ("transfer-encoding", "connection", "content-length")
            }
            self._reply(resp.status, data, out)
        except Exception as exc:  # noqa: BLE001 - surface backend errors to client
            self._reply(
                502,
                str(exc).encode(),
                {"Content-Type": "text/plain; charset=utf-8"},
            )
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _handle

    def log_message(self, *args):  # silence default stderr logging
        pass


def start_tls_proxy(
    host: str = "0.0.0.0",
    port: int = 2119,
    backend_host: str = "127.0.0.1",
    backend_port: int = 8420,
    cert_dir: str = "",
    cert_file: str = "",
    key_file: str = "",
    crl_file: str = "",
    ca_file: str = "",
    auto_gen_certs: bool = False,
    san_names: list[str] | None = None,
) -> ThreadingHTTPServer:
    """Start the TLS reverse proxy in the calling thread (blocks).

    For background use, call from a daemon thread::

        t = threading.Thread(target=start_tls_proxy, daemon=True, kwargs={...})
        t.start()
    """
    global _HOST, _PORT, _BACKEND_HOST, _BACKEND_PORT
    global _CERT_DIR, _CERT_FILE, _KEY_FILE, _CRL_FILE, _CA_FILE, _AUTO_GEN, _SAN_NAMES

    cdir = cert_dir or os.environ.get("CERT_DIR", "/certs")
    _HOST = host
    _PORT = port
    _BACKEND_HOST = backend_host
    _BACKEND_PORT = backend_port
    _CERT_DIR = cdir
    _CERT_FILE = cert_file or os.environ.get("CERT_FILE", os.path.join(cdir, "srv.crt"))
    _KEY_FILE = key_file or os.environ.get("KEY_FILE", os.path.join(cdir, "srv.key"))
    _CRL_FILE = crl_file or os.environ.get("CRL_FILE", os.path.join(cdir, "ca.crl"))
    _CA_FILE = ca_file or os.environ.get("CA_CERT_FILE", os.path.join(cdir, "ca.crt"))
    _AUTO_GEN = auto_gen_certs
    _SAN_NAMES = san_names or []

    _ensure_certs()
    server = ThreadingHTTPServer((_HOST, _PORT), ProxyHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=_CERT_FILE, keyfile=_KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    return server


if __name__ == "__main__":
    start_tls_proxy(
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "2119")),
        backend_host=os.environ.get("BACKEND_HOST", "openbiliclaw-backend"),
        backend_port=int(os.environ.get("BACKEND_PORT", "8420")),
        auto_gen_certs=os.environ.get("AUTO_GEN_CERTS", "1") in ("1", "true", "yes"),
    )
