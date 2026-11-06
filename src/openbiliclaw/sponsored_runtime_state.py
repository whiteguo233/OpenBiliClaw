"""Process-wide Sponsored Runtime settings installed by config loading.

The open-source core constructs ``LLMService`` in many places (API runtime,
CLI, soul engine, OpenClaw bootstrap). Passing a new field through every
constructor would be invasive for a setting that is process-wide by nature:
the official build has one configuration and a bounded pool of runtime children.

``load_config_with_diagnostics`` installs the ``[llm.sponsored]`` table here;
``LLMService`` reads it lazily on the first sponsored task and still honors the
development env bootstrap as a fallback. Tests call the reset helper directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.runtime.relay_release import official_relay_url

DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class SponsoredRuntimeSettings:
    """Validated ``[llm.sponsored]`` configuration."""

    enabled: bool = False
    # Executable path or command line for obc-sponsored-runtime. Official
    # installers point at the bundled binary; source builds leave it empty.
    runtime_path: str = ""
    relay_url: str = field(default_factory=official_relay_url)
    model: str = "Qwen/Qwen3-8B"
    embedding_mode: str = "cloud"
    fallback_to_other_free_model: bool = True
    fallback_to_user_provider: bool = False
    notify_on_fallback: bool = True
    request_timeout_seconds: float = DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS

    @property
    def has_runtime(self) -> bool:
        return bool(self.runtime_path.strip())

    @property
    def uses_cloud_embedding(self) -> bool:
        """Whether this plan owns the text-vector route."""
        return self.enabled and self.embedding_mode == "cloud"


_SETTINGS = SponsoredRuntimeSettings()
_DATA_PATH: Path | None = None


def install_sponsored_settings(
    settings: SponsoredRuntimeSettings, *, data_path: Path | None = None
) -> None:
    """Replace the process-wide settings (called by config loading)."""

    global _SETTINGS, _DATA_PATH
    _SETTINGS = settings
    _DATA_PATH = data_path
    from openbiliclaw.sponsored_status import configure_status_directory

    configure_status_directory(data_path)


def current_sponsored_settings() -> SponsoredRuntimeSettings:
    return _SETTINGS


def relay_device_path() -> Path:
    """Private per-install identity; never serialized into config/API output."""
    from pathlib import Path

    return (_DATA_PATH or Path("data")) / "runtime" / "relay-device.key"


def reset_sponsored_settings() -> None:
    """Restore defaults (test seam)."""

    install_sponsored_settings(SponsoredRuntimeSettings())
