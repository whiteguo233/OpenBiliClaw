"""Packaging-metadata tests for dependency metadata.

Guards that the default package install includes the X discovery dependency.
The ``openbiliclaw[x]`` extra remains as a backwards-compatible alias.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _project_data() -> dict[str, object]:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]


def _runtime_dependencies() -> list[str]:
    return list(_project_data()["dependencies"])  # type: ignore[index]


def _optional_dependencies() -> dict[str, list[str]]:
    return _project_data()["optional-dependencies"]  # type: ignore[index]


def test_default_dependencies_include_twitter_cli() -> None:
    requirements = _runtime_dependencies()
    twitter_reqs = [r for r in requirements if r.replace("_", "-").startswith("twitter-cli")]
    assert twitter_reqs, f"default install must require twitter-cli, got {requirements!r}"
    assert any(">=" in r for r in twitter_reqs), (
        f"twitter-cli requirement must pin a minimum version, got {twitter_reqs!r}"
    )


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
