"""Device proof of possession for the sponsored relay; no shared client secret."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

SESSION_SECONDS = 300
PROOF_SECONDS = 60


class RelayDeniedError(ValueError):
    """Stable rejection without echoing credentials or request data."""


class RelayLimitError(RelayDeniedError):
    """Availability limit, never credential expiry."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def proof_message(path: str, body: bytes, timestamp: str, nonce: str, token: str) -> bytes:
    return f"POST\n{path}\n{digest(body)}\n{timestamp}\n{nonce}\n{digest(token.encode())}".encode()


def public_id(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes_raw().hex()


def signed_headers(
    key: Ed25519PrivateKey, path: str, body: bytes, token: str = ""
) -> dict[str, str]:
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    signature = key.sign(proof_message(path, body, timestamp, nonce, token))
    headers = {
        "X-OBC-Device": public_id(key),
        "X-OBC-Time": timestamp,
        "X-OBC-Nonce": nonce,
        "X-OBC-Proof": base64.b64encode(signature).decode(),
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def load_device_key(path: Path) -> Ed25519PrivateKey:
    """Create one private device identity, never a provider API key."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Ed25519PrivateKey.generate()
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix=".relay-device-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                os.link(temporary, path)
        finally:
            os.unlink(temporary)
    # Refuse symlinks/nonregular files and shared Unix files before reading.
    import stat

    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0),
    )
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        # A concurrent creator briefly owns both the private temporary link
        # and final link. Wait on this same inode before reading any secret;
        # persistent additional links still fail closed.
        for _ in range(10):
            if info.st_nlink != 2:
                break
            time.sleep(0.01)
            info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RelayDeniedError("unsafe device identity file")
        if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise RelayDeniedError("device identity must be private")
        data = stream.read(33)
    if len(data) != 32:
        raise RelayDeniedError("invalid device identity")
    return Ed25519PrivateKey.from_private_bytes(data)


class RelayStore:
    """Atomic replay, revocation, enrollment and quota state shared across workers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS proofs (
                    device TEXT, nonce TEXT, expires INTEGER, PRIMARY KEY(device, nonce));
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, device TEXT, expires INTEGER);
                CREATE TABLE IF NOT EXISTS enrollment (ip TEXT, at INTEGER);
                CREATE TABLE IF NOT EXISTS reservations (device TEXT, at INTEGER, tokens INTEGER);
                CREATE INDEX IF NOT EXISTS enrollment_at ON enrollment(at);
                CREATE INDEX IF NOT EXISTS reservations_at ON reservations(at);
            """)
        if os.name != "nt":
            path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def authenticate(self, headers: Any, path: str, body: bytes, *, ip: str = "") -> str:
        """Verify before recording a nonce; captured tokens alone cannot authenticate."""
        device = headers.get("x-obc-device", "")
        timestamp, nonce = headers.get("x-obc-time", ""), headers.get("x-obc-nonce", "")
        bearer = headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else ""
        now = int(time.time())
        try:
            if len(device) != 64 or len(nonce) != 32 or abs(int(timestamp) - now) > PROOF_SECONDS:
                raise ValueError
            if bytes.fromhex(nonce).hex() != nonce or bytes.fromhex(device).hex() != device:
                raise ValueError
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(device))
            signature = base64.b64decode(headers.get("x-obc-proof", ""), validate=True)
            key.verify(signature, proof_message(path, body, timestamp, nonce, token))
        except (ValueError, TypeError, InvalidSignature):
            raise RelayDeniedError("invalid device proof") from None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM proofs WHERE expires < ?", (now,))
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            record = db.execute("SELECT revoked FROM devices WHERE id=?", (device,)).fetchone()
            if record and record[0]:
                raise RelayDeniedError("device revoked")
            if (
                db.execute("SELECT count(*) FROM proofs WHERE device=?", (device,)).fetchone()[0]
                >= 120
            ):
                raise RelayLimitError("device request rate exceeded")
            if path == "/v1/session":
                if record is None:
                    # No forwarded header is trusted here: the ASGI server owns client IP.
                    ip_hash = digest(ip.encode())
                    db.execute("DELETE FROM enrollment WHERE at < ?", (now - 86400,))
                    per_ip = db.execute(
                        "SELECT count(*) FROM enrollment WHERE ip=?", (ip_hash,)
                    ).fetchone()[0]
                    total = db.execute("SELECT count(*) FROM enrollment").fetchone()[0]
                    if per_ip >= 5 or total >= 1000:
                        raise RelayLimitError("enrollment limited")
                    db.execute("INSERT INTO devices(id) VALUES (?)", (device,))
                    db.execute("INSERT INTO enrollment VALUES (?,?)", (ip_hash, now))
            else:
                session = db.execute(
                    "SELECT device FROM sessions WHERE hash=? AND expires>?",
                    (digest(token.encode()), now),
                ).fetchone()
                if not record or not session or session[0] != device:
                    raise RelayDeniedError("session expired or invalid")
            try:
                db.execute(
                    "INSERT INTO proofs VALUES (?,?,?)", (device, nonce, now + 2 * PROOF_SECONDS)
                )
            except sqlite3.IntegrityError:
                raise RelayDeniedError("replayed request") from None
        return str(device)

    def session(self, device: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            # A device needs only a bounded number of concurrent sessions.
            db.execute(
                "DELETE FROM sessions WHERE device=? AND rowid NOT IN "
                "(SELECT rowid FROM sessions WHERE device=? ORDER BY rowid DESC LIMIT 3)",
                (device, device),
            )
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?)",
                (digest(token.encode()), device, int(time.time()) + SESSION_SECONDS),
            )
        return {"token": token, "expires_in": SESSION_SECONDS}

    def revoke(self, device: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device,))
            db.execute("DELETE FROM sessions WHERE device=?", (device,))

    def reserve(self, device: str, tokens: int) -> None:
        """Conservative reservations persist on failure/disconnect, never refund blindly."""
        if type(tokens) is not int or tokens <= 0:
            raise RelayDeniedError("invalid reservation")
        now = int(time.time())
        day, month = now - 86400, now - 31 * 86400
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM reservations WHERE at < ?", (month,))
            for scope, args, rpm, daily_requests, daily_tokens, monthly_tokens in (
                ("", (), 300, 20000, 20000000, 300000000),
                (" AND device=?", (device,), 30, 1000, 1000000, 10000000),
            ):
                counts = db.execute(
                    "SELECT sum(at>?),sum(at>?),coalesce(sum(CASE WHEN at>? "
                    "THEN tokens ELSE 0 END),0),coalesce(sum(tokens),0) "
                    "FROM reservations WHERE 1=1" + scope,
                    (now - 60, day, day, *args),
                ).fetchone()
                if (
                    (counts[0] or 0) >= rpm
                    or (counts[1] or 0) >= daily_requests
                    or counts[2] + tokens > daily_tokens
                    or counts[3] + tokens > monthly_tokens
                ):
                    raise RelayLimitError("quota exceeded")
            db.execute("INSERT INTO reservations VALUES (?,?,?)", (device, now, tokens))


def wire_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
