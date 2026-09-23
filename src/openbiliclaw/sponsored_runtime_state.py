"""Process-wide Sponsored Runtime settings installed by config loading.

The open-source core constructs ``LLMService`` in many places (API runtime,
CLI, soul engine, OpenClaw bootstrap). Passing a new field through every
constructor would be invasive for a setting that is process-wide by nature:
the official build has one configuration and one runtime child.

``load_config_with_diagnostics`` installs the ``[llm.sponsored]`` table here;
``LLMService`` reads it lazily on the first sponsored task and still honors the
development env bootstrap as a fallback. Tests call the reset helper directly.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class SponsoredRuntimeSettings:
    """Validated ``[llm.sponsored]`` configuration."""

    enabled: bool = False
    # Executable path or command line for obc-sponsored-runtime. Official
    # installers point at the bundled binary; source builds leave it empty.
    runtime_path: str = ""
    fallback_to_user_provider: bool = True
    notify_on_fallback: bool = True
    request_timeout_seconds: float = DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS

    @property
    def has_runtime(self) -> bool:
        return bool(self.runtime_path.strip())


_SETTINGS = SponsoredRuntimeSettings()


def install_sponsored_settings(settings: SponsoredRuntimeSettings) -> None:
    """Replace the process-wide settings (called by config loading)."""

    global _SETTINGS
    _SETTINGS = settings


def current_sponsored_settings() -> SponsoredRuntimeSettings:
    return _SETTINGS


def reset_sponsored_settings() -> None:
    """Restore defaults (test seam)."""

    install_sponsored_settings(SponsoredRuntimeSettings())
