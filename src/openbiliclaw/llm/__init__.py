"""LLM package — multi-model provider support."""

from .base import (
    HealthCheckResult,
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_provider import DeepSeekProvider, OpenAIProvider
from .openrouter_provider import OpenRouterProvider
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

__all__ = [
    "ClaudeProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "HealthCheckResult",
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
    "RegistryBuildError",
    "RegistrySummary",
    "LLMProviderExecutionError",
    "LLMService",
    "LLMServiceError",
    "LLMResponseContentError",
    "build_llm_registry",
    "is_llm_rate_limit_error",
    "summarize_registry",
]
