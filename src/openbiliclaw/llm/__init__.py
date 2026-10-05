"""LLM package — multi-model provider support."""

from .api_route_provider import ApiRouteProvider
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
from .cheaperinference_provider import CheaperInferenceProvider
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
from .requesty_provider import RequestyProvider
from .service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    LLMServiceError,
    is_llm_rate_limit_error,
)

__all__ = [
    "ApiRouteProvider",
    "CheaperInferenceProvider",
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
    "RequestyProvider",
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
