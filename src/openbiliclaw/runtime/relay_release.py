"""Reviewed release default. Set only after the production relay is verified.

A nonempty origin makes official client packages omit the provider-key SDK.
No environment variable or browser request may change packaging mode.
"""

from urllib.parse import urlsplit

OFFICIAL_RELAY_URL = ""


def validate_relay_url(value: str) -> str:
    """Only TLS origins; no credentials, path, fragments or plaintext fallback."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("sponsored relay must be an HTTPS origin")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid relay port")
    return value.rstrip("/")


def official_relay_url() -> str:
    return validate_relay_url(OFFICIAL_RELAY_URL) if OFFICIAL_RELAY_URL else ""
