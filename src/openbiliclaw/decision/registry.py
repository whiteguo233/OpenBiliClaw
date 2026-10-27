"""Build explicitly enabled decision providers without borrowing chat credentials."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from openbiliclaw.config import ConfigError, validate_jev_decision_config

from .jev_provider import JevDecisionProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from openbiliclaw.config import DecisionConfig, JevDecisionConfig

    from .base import DecisionProvider


class DecisionConfigurationError(ConfigError):
    """An explicitly requested decision provider cannot be constructed safely."""


def _resolve_jev_api_key(
    settings: JevDecisionConfig,
    environ: Mapping[str, str] | None,
) -> str:
    variables = os.environ if environ is None else environ
    api_key = settings.api_key or variables.get(settings.api_key_env, "").strip()
    if not api_key:
        raise DecisionConfigurationError(
            "Enabled Jev requires a saved token or the environment variable named by api_key_env."
        )
    # HTTP header syntax, not vendor key shape; never echo received values.
    if not api_key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in api_key):
        raise DecisionConfigurationError("The Jev credential is not a valid bearer token.")
    return api_key


def validate_decision_provider_credentials(
    config: DecisionConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Check enabled credentials without constructing a transport or retaining the key."""
    settings = config.providers.jev
    validate_jev_decision_config(settings)
    if settings.enabled:
        _resolve_jev_api_key(settings, environ)


def build_decision_provider(
    config: DecisionConfig,
    *,
    provider_name: str = "jev",
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> DecisionProvider | None:
    """Build one enabled provider, or return None without credentials/network work.

    This factory does not register a chat provider or enable a recommendation
    route. The caller owns the resulting provider's lifetime. An injected HTTP
    client remains caller-owned even after the provider is closed.
    """
    if provider_name != "jev":
        raise DecisionConfigurationError("Unsupported decision provider; expected jev.")
    settings = config.providers.jev
    validate_jev_decision_config(settings)
    if not settings.enabled:
        return None

    api_key = _resolve_jev_api_key(settings, environ)
    return JevDecisionProvider(
        api_key=api_key,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        max_attempts=settings.max_attempts,
        client=client,
        endpoint_url=settings.endpoint_url,
    )
