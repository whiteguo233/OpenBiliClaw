"""Regression tests for optional Gemini SDK imports."""

from __future__ import annotations

import importlib
import sys
from contextlib import suppress

import pytest


def test_registry_module_imports_without_google_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing registry should not require the Gemini SDK when unused."""
    llm_package = sys.modules.get("openbiliclaw.llm")
    original_registry_attr = (
        getattr(llm_package, "registry", None) if llm_package is not None else None
    )

    try:
        monkeypatch.delitem(sys.modules, "openbiliclaw.llm.gemini_provider", raising=False)
        monkeypatch.delitem(sys.modules, "openbiliclaw.llm.registry", raising=False)

        module = importlib.import_module("openbiliclaw.llm.registry")

        assert module.build_llm_registry is not None
    finally:
        if llm_package is not None:
            if original_registry_attr is None:
                with suppress(AttributeError):
                    delattr(llm_package, "registry")
            else:
                vars(llm_package)["registry"] = original_registry_attr


def test_gemini_provider_raises_helpful_error_without_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using Gemini without google-genai installed should fail clearly."""
    module = importlib.import_module("openbiliclaw.llm.gemini_provider")
    monkeypatch.setattr(module, "genai", None)
    monkeypatch.setattr(module, "types", None)

    with pytest.raises(Exception, match="google-genai"):
        module.GeminiProvider(api_key="test-key")
