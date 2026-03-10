"""Regression tests for optional Gemini SDK imports."""

from __future__ import annotations

import importlib
import sys

import pytest


def test_registry_module_imports_without_google_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing registry should not require the Gemini SDK when unused."""
    monkeypatch.delitem(sys.modules, "openbiliclaw.llm.gemini_provider", raising=False)
    monkeypatch.delitem(sys.modules, "openbiliclaw.llm.registry", raising=False)

    module = importlib.import_module("openbiliclaw.llm.registry")

    assert module.build_llm_registry is not None


def test_gemini_provider_raises_helpful_error_without_sdk() -> None:
    """Using Gemini without google-genai installed should fail clearly."""
    module = importlib.import_module("openbiliclaw.llm.gemini_provider")

    with pytest.raises(Exception, match="google-genai"):
        module.GeminiProvider(api_key="test-key")
