"""Shared release transport settings and per-install status/relay identity.

Normal free LLM instances own their model and an immutable transport snapshot;
embedding is configured independently. This binding supplies installation state
and the explicit legacy harness bootstrap, not application model routing.
Config loading migrates the retired global switch into ordinary instance chains.
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
    # enabled/model/embedding_mode/fallback flags are retained for migration and
    # explicitly injected legacy harnesses. Native routes use instance models.
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
