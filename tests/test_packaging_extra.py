"""Packaging-metadata tests for optional dependency extras.

Guards that the ``openbiliclaw[x]`` extra is declared and pins
``twitter-cli``. Without this extra, :mod:`openbiliclaw.sources.x_client`
would have nothing to lazily import on the X-enabled path.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _optional_dependencies() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_x_extra_is_declared() -> None:
    extras = _optional_dependencies()
    assert "x" in extras, "expected an [project.optional-dependencies] 'x' extra"


def test_x_extra_pins_twitter_cli() -> None:
    requirements = _optional_dependencies()["x"]
    twitter_reqs = [r for r in requirements if r.replace("_", "-").startswith("twitter-cli")]
    assert twitter_reqs, f"x extra must require twitter-cli, got {requirements!r}"
    # Must pin a minimum version (>=0.8.5 per the spike) so XClient builds
    # against a known surface.
    assert any(">=" in r for r in twitter_reqs), (
        f"twitter-cli requirement must pin a minimum version, got {twitter_reqs!r}"
    )
