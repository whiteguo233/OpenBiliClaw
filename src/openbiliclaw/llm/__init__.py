"""Public LLM API; providers load only when their exports are requested.

Protocol/contract-only tools must not import every external model SDK.
Existing from-import names remain available through lazy module attributes.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import (
    HealthCheckResult,
    LLMAuthError,
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    classify_llm_failure_kind,
    classify_llm_unavailability,
)

if TYPE_CHECKING:
    from .claude_provider import ClaudeProvider
    from .gemini_provider import GeminiProvider
    from .ollama_provider import OllamaProvider
    from .openai_provider import DeepSeekProvider, OpenAIProvider
    from .openrouter_provider import OpenRouterProvider
    from .orcarouter_provider import OrcaRouterProvider
    from .registry import (
        RegistryBuildError,
        RegistrySummary,
        build_llm_registry,
        summarize_registry,
    )
    from .service import (
        LLMProviderExecutionError,
        LLMResponseContentError,
        LLMService,
        LLMServiceError,
        is_llm_rate_limit_error,
    )

_LAZY_EXPORTS = {
    "ClaudeProvider": ".claude_provider",
    "GeminiProvider": ".gemini_provider",
    "OllamaProvider": ".ollama_provider",
    "DeepSeekProvider": ".openai_provider",
    "OpenAIProvider": ".openai_provider",
    "OpenRouterProvider": ".openrouter_provider",
    "OrcaRouterProvider": ".orcarouter_provider",
    "RegistryBuildError": ".registry",
    "RegistrySummary": ".registry",
    "build_llm_registry": ".registry",
    "summarize_registry": ".registry",
    "LLMProviderExecutionError": ".service",
    "LLMResponseContentError": ".service",
    "LLMService": ".service",
    "LLMServiceError": ".service",
    "is_llm_rate_limit_error": ".service",
}


def __getattr__(name: str) -> Any:
    """Resolve an existing provider/service export without eager SDK imports."""
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "ClaudeProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "HealthCheckResult",
    "LLMAuthError",
    "LLMFallbackError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "OrcaRouterProvider",
    "RegistryBuildError",
    "RegistrySummary",
    "LLMProviderExecutionError",
    "LLMService",
    "LLMServiceError",
    "LLMResponseContentError",
    "build_llm_registry",
    "classify_llm_unavailability",
    "classify_llm_failure_kind",
    "is_llm_rate_limit_error",
    "summarize_registry",
]
