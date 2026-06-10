"""Tests for the LLM registry and fallback behavior."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from openbiliclaw.config import Config, EmbeddingConfig, LLMConfig, LLMProviderConfig
from openbiliclaw.llm.base import (
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMRegistry,
    LLMResponse,
    LLMResponseError,
)
from openbiliclaw.llm.gemini_provider import gemini_sdk_available
from openbiliclaw.llm.registry import (
    RegistryBuildError,
    build_embedding_service,
    build_llm_registry,
)


@dataclass
class FakeProvider(LLMProvider):
    """Simple fake provider for registry tests."""

    provider_name: str
    responses: list[LLMResponse] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    health: bool = True
    call_count: int = 0
    models: list[str | None] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.provider_name

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.call_count += 1
        self.models.append(model)
        if self.errors:
            raise self.errors.pop(0)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="ok", provider=self.provider_name, model="fake")

    async def health_check(self) -> bool:
        return self.health


def test_build_llm_registry_registers_available_providers() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            deepseek=LLMProviderConfig(api_key="deepseek-key"),
            ollama=LLMProviderConfig(model="llama3"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openai"
    assert registry.available_providers == ["openai", "deepseek", "ollama"]


def test_build_llm_registry_registers_openai_with_codex_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm.codex_auth import CodexCredentials

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="",
                model="gpt-5-nano",
                auth_mode="codex_oauth",
            ),
        )
    )

    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.load_codex_credentials",
        lambda: CodexCredentials("access-token", "refresh-token", 9999999999),
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openai"
    assert registry.available_providers == ["openai"]
    provider = registry.get("openai")
    assert provider._client.api_key == "access-token"


def test_build_llm_registry_registers_openrouter() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(
                api_key="openrouter-key",
                model="openai/gpt-4o-mini",
                base_url="https://openrouter.ai/api/v1",
            ),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openrouter"
    assert "openrouter" in registry.available_providers


def test_build_llm_registry_registers_openai_compatible() -> None:
    """v0.3.32+ — openai_compatible is a first-class registered provider,
    distinct from openai. Both can coexist in the same registry."""
    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai=LLMProviderConfig(api_key="sk-real-openai"),
            openai_compatible=LLMProviderConfig(
                api_key="gsk-groq-test",
                model="llama-3.1-70b-versatile",
                base_url="https://api.groq.com/openai/v1",
            ),
        )
    )
    registry = build_llm_registry(config)

    assert registry.default_provider == "openai_compatible"
    # Both providers coexist as independent registry entries.
    assert "openai" in registry.available_providers
    assert "openai_compatible" in registry.available_providers

    # The two are different OpenAIProvider *instances* with different
    # name and base_url — this is what guarantees billing / cost stats
    # don't collapse them.
    openai = registry.get("openai")
    compat = registry.get("openai_compatible")
    assert openai.name == "openai"
    assert compat.name == "openai_compatible"
    assert openai is not compat
    assert compat.base_url == "https://api.groq.com/openai/v1"


def test_build_llm_registry_refuses_openai_compatible_without_base_url() -> None:
    """A Groq / Together / vLLM provider WITHOUT a base_url is just an
    expensive way of mistyping ``openai`` — it would hit api.openai.com
    with the wrong api_key and 401. Refuse to register so the failure is
    surfaced at startup, not on the first chat request."""
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-openai"),
            openai_compatible=LLMProviderConfig(
                api_key="gsk-groq-test",
                model="llama-3.1-70b-versatile",
                base_url="",  # ← missing
            ),
        )
    )
    registry = build_llm_registry(config)
    assert "openai_compatible" not in registry.available_providers


def test_openai_compatible_can_serve_as_embedding_provider(tmp_path) -> None:
    """Most OpenAI-compat backends (Together, vLLM, Azure) expose
    /v1/embeddings. ``openai_compatible`` must therefore be valid as
    [llm.embedding].provider — the embedding service builds a dedicated
    instance pointing at the user-supplied base_url."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-openai"),
            embedding=EmbeddingConfig(
                provider="openai_compatible",
                model="bge-large-en-v1.5",
                api_key="vllm-token",
                base_url="http://localhost:8000/v1",
                output_dimensionality=1024,
            ),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)

    assert service is not None
    assert service._provider.name == "openai_compatible"
    assert service._model == "bge-large-en-v1.5"
    assert service._cache_model == "bge-large-en-v1.5"
    # Built against the embedding-section base_url, not [llm.openai].
    assert str(service._provider._client.base_url).rstrip("/") == ("http://localhost:8000/v1")
    assert service._provider._client.api_key == "vllm-token"


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
def test_build_llm_registry_registers_gemini() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(
                api_key="gemini-key",
                model="gemini-2.5-flash",
            ),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "gemini"
    assert "gemini" in registry.available_providers


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
def test_build_llm_registry_registers_gemini_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "env-gemini-key")
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "gemini"
    assert "gemini" in registry.available_providers


def test_build_llm_registry_downgrades_default_provider() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="claude",
            openai=LLMProviderConfig(api_key="openai-key"),
            ollama=LLMProviderConfig(model="llama3"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openai"


def test_build_llm_registry_requires_explicit_ollama_config() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            ollama=LLMProviderConfig(model="", base_url=""),
        )
    )

    with pytest.raises(RegistryBuildError):
        build_llm_registry(config)


def test_build_llm_registry_registers_ollama_when_base_url_is_explicit() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            ollama=LLMProviderConfig(model="", base_url="http://localhost:11434/v1"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "ollama"
    assert registry.available_providers == ["ollama"]


def test_build_llm_registry_does_not_auto_register_ollama_for_embedding(
    tmp_path,
) -> None:
    """Pre-v0.3.32, the chat registry auto-registered Ollama as
    embedding-only whenever ``[llm.embedding] provider = "ollama"``. That
    hack existed because embedding pulled its provider via
    ``registry.get(name)``.

    v0.3.32+ ``build_embedding_service`` constructs its own dedicated
    Ollama provider directly from ``[llm.embedding]`` (with a back-compat
    read of ``[llm.ollama]``), so the chat registry no longer needs to
    carry an embedding-only entry. This test pins both halves of the new
    contract:

      1. The chat registry stays clean — ``ollama`` is NOT registered
         when only the embedding section asks for it.
      2. The embedding service still resolves Ollama and uses the right
         model — the user's "I want local embedding" preference must
         survive the refactor.
    """
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="test-key", model="gemini-2.0-flash"),
            ollama=LLMProviderConfig(model="", base_url=""),
            embedding=EmbeddingConfig(provider="ollama", model="bge-m3"),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)

    # (1) chat registry no longer carries an embedding-only Ollama entry.
    assert "ollama" not in registry.available_providers
    assert registry.default_provider == "gemini"

    # (2) embedding still works — it builds its own Ollama provider.
    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._provider.name == "ollama"
    assert service._model == "bge-m3"


def test_build_embedding_service_picks_bge_m3_default_for_ollama(
    tmp_path,
) -> None:
    """When [llm.embedding] provider=ollama and model is empty, the service
    must use bge-m3 — not the gemini-embedding-001 default — so the
    install-time wizard's choice actually takes effect."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434/v1"),
            embedding=EmbeddingConfig(provider="ollama", model=""),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._model == "bge-m3"


def test_build_embedding_service_respects_explicit_model_override(
    tmp_path,
) -> None:
    """An explicit [llm.embedding] model wins over the per-provider default."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434/v1"),
            embedding=EmbeddingConfig(provider="ollama", model="custom-embed-v2"),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._model == "custom-embed-v2"


def test_ollama_embedding_with_empty_credentials_uses_local_default_without_warning(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ollama embedding is local, so empty credentials are not chat
    credential back-compat.
    """
    import logging

    from openbiliclaw.config import EmbeddingConfig
    from openbiliclaw.llm import registry as registry_mod

    registry_mod._embedding_compat_warned.clear()
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            ollama=LLMProviderConfig(model="", base_url=""),
            embedding=EmbeddingConfig(
                provider="ollama",
                model="bge-m3",
                api_key="",
                base_url="",
            ),
        ),
        data_dir=str(tmp_path),
    )

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.llm.registry"):
        service = build_embedding_service(config, LLMRegistry())

    assert service is not None
    assert service._provider.name == "ollama"
    assert service._model == "bge-m3"
    assert service._provider.base_url == "http://localhost:11434/v1"
    assert "back-compat" not in caplog.text.lower()


def test_ollama_embedding_without_base_url_uses_local_default(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from openbiliclaw.config import EmbeddingConfig
    from openbiliclaw.llm import registry as registry_mod

    registry_mod._embedding_compat_warned.clear()
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            ollama=LLMProviderConfig(model="", base_url="http://remote-ollama:11434"),
            embedding=EmbeddingConfig(provider="ollama", model="bge-m3"),
        ),
        data_dir=str(tmp_path),
    )

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.llm.registry"):
        service = build_embedding_service(config, LLMRegistry())

    assert service is not None
    assert service._provider.name == "ollama"
    assert service._provider.base_url == "http://localhost:11434/v1"
    assert "back-compat" not in caplog.text.lower()


# ---------------------------------------------------------------------------
# Regression: providers without an embeddings endpoint must NOT silently
# return None. v0.3.18 and earlier handed the request to Claude / DeepSeek /
# OpenRouter via ``hasattr(provider, "embed")`` — DeepSeek and OpenRouter
# inherit ``embed`` from OpenAIProvider so the check passed even though
# the backend has no embeddings route, and the call would 404 at runtime.
# v0.3.19+ uses the ``supports_embedding`` flag and falls back to a
# provider that actually works.


def test_build_embedding_service_falls_back_when_claude_is_default(
    tmp_path,
) -> None:
    """Claude has no embeddings API. When it's the default LLM and the
    [llm.embedding] section is empty, embedding must transparently fall
    back to a registered provider that can actually embed (Ollama in this
    fixture). Previously this returned None and the recommendation
    pipeline silently lost diversity / dedup."""
    config = Config(
        llm=LLMConfig(
            default_provider="claude",
            claude=LLMProviderConfig(api_key="claude-key"),
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434/v1"),
            embedding=EmbeddingConfig(fallback_enabled=True, fallback_provider="ollama"),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is not None, "embedding must fall back, not silently disable"
    # Ollama wins the fallback chain (ordered: requested → ollama → gemini → openai).
    assert service._provider.name == "ollama"
    assert service._model == "bge-m3"


def test_build_embedding_service_falls_back_when_deepseek_is_default(
    tmp_path,
) -> None:
    """DeepSeek inherits ``embed`` from OpenAIProvider but its backend has
    no embeddings route. ``supports_embedding=False`` makes the fallback
    chain skip it instead of letting the call 404 at runtime."""
    config = Config(
        llm=LLMConfig(
            default_provider="deepseek",
            deepseek=LLMProviderConfig(api_key="deepseek-key"),
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434/v1"),
            embedding=EmbeddingConfig(fallback_enabled=True, fallback_provider="ollama"),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._provider.name == "ollama"


def test_build_embedding_service_returns_none_with_no_capable_provider(
    tmp_path,
) -> None:
    """When no registered provider can actually embed (e.g. Claude only),
    the service returns None — but logs a warning so the failure mode is
    observable, not silent."""
    config = Config(
        llm=LLMConfig(
            default_provider="claude",
            claude=LLMProviderConfig(api_key="claude-key"),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is None


def test_build_embedding_service_empty_provider_does_not_follow_default_llm(
    tmp_path,
) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            embedding=EmbeddingConfig(provider="", fallback_enabled=False),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)

    service = build_embedding_service(config, registry)

    assert service is None


def test_build_embedding_service_does_not_borrow_chat_credentials_when_disabled(
    tmp_path,
) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            embedding=EmbeddingConfig(provider="openai", api_key="", fallback_enabled=False),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)

    service = build_embedding_service(config, registry)

    assert service is None


def test_ollama_base_url_normalised_to_v1_suffix() -> None:
    """Older config.example.toml shipped ``base_url = "http://localhost:11434"``
    (no /v1). The OpenAI SDK then calls ``/chat/completions`` directly,
    which Ollama 404s — its OpenAI-compat shim lives at /v1. The
    registry must auto-append /v1 so users with stale configs still work
    after upgrade. Regression for v0.3.20.1."""
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        ),
    )
    registry = build_llm_registry(config)
    ollama_provider = registry.get("ollama")
    assert ollama_provider.base_url.endswith("/v1"), (
        f"expected /v1 suffix, got {ollama_provider.base_url!r}"
    )


def test_openai_primary_with_default_embedding_model_uses_correct_default(
    tmp_path,
) -> None:
    """Pre-v0.3.20.1, config.example.toml hardcoded
    ``[llm.embedding] model = "gemini-embedding-001"`` as the default.
    For OpenAI / Ollama / DeepSeek primaries that string was wrong —
    OpenAI's embeddings endpoint 404s on it. After the fix
    config.example.toml ships ``model = ""`` so the registry's
    per-provider defaults kick in (text-embedding-3-small for OpenAI)."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            embedding=EmbeddingConfig(
                provider="openai",
                model="",
                api_key="openai-key",
            ),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._provider.name == "openai"
    assert service._model == "text-embedding-3-small", (
        f"expected text-embedding-3-small for OpenAI primary, got {service._model!r}"
    )


# ---------------------------------------------------------------------------
# v0.3.32 — embedding gets its own api_key/base_url; chat-side credentials
# are only used as a back-compat fallback. The four tests below pin the
# new contract: independent credentials, cross-provider isolation,
# back-compat fallback, and the WARNING that announces it.


def test_embedding_uses_dedicated_credentials_over_chat_block(
    tmp_path,
) -> None:
    """When [llm.embedding] supplies its own api_key/base_url, those win
    over [llm.<provider>]. The chat block's api_key must NOT leak into
    the embedding provider — they are different connections."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="chat-side-openai-key",
                base_url="https://chat.example.com/v1",
            ),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="dedicated-embedding-key",
                base_url="https://embed.example.com/v1",
            ),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    service = build_embedding_service(config, registry)

    assert service is not None
    assert service._provider.name == "openai"
    # Provider was constructed with the embedding-section credentials,
    # not borrowed from [llm.openai]. We inspect the underlying OpenAI
    # SDK client because OpenAIProvider doesn't re-expose api_key.
    assert service._provider._client.api_key == "dedicated-embedding-key"
    # AsyncOpenAI normalises base_url with a trailing slash.
    assert str(service._provider._client.base_url).rstrip("/") == ("https://embed.example.com/v1")


def test_embedding_provider_independent_from_chat_provider(
    tmp_path,
) -> None:
    """Cross-provider scenario: chat uses DeepSeek, embedding uses a
    fully self-contained Gemini config. Neither block borrows from the
    other — DeepSeek doesn't carry an embedding-capable backend, and
    Gemini chat is not configured at all. Embedding must still build
    against the dedicated [llm.embedding] credentials."""
    if not gemini_sdk_available():
        pytest.skip("gemini SDK not installed in this environment")
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="deepseek",
            deepseek=LLMProviderConfig(api_key="deepseek-key"),
            # NOTE: [llm.gemini] left totally empty — embedding still works.
            gemini=LLMProviderConfig(api_key=""),
            embedding=EmbeddingConfig(
                provider="gemini",
                model="gemini-embedding-001",
                api_key="dedicated-gemini-embedding-key",
            ),
        ),
        data_dir=str(tmp_path),
    )
    registry = build_llm_registry(config)
    # Chat registry: only deepseek (gemini has no api_key, so it is NOT
    # auto-registered for embedding's sake under the new design).
    assert "gemini" not in registry.available_providers

    service = build_embedding_service(config, registry)
    assert service is not None
    assert service._provider.name == "gemini"
    assert service._model == "gemini-embedding-001"


def test_embedding_back_compat_falls_back_to_chat_block(tmp_path) -> None:
    """Old configs (pre-v0.3.32) only had [llm.embedding] provider/model
    and relied on [llm.<provider>] for credentials. That path must still
    work — the embedding service is built and it uses the chat-side
    api_key transparently.

    Fixture: user explicitly chose openai for embedding but did NOT fill
    [llm.embedding].api_key. Backend must borrow from [llm.openai]."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="legacy-chat-side-key",
                base_url="https://api.openai.com/v1",
            ),
            embedding=EmbeddingConfig(
                provider="openai",
                model="",
                fallback_enabled=True,
                fallback_provider="openai",
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is not None
    assert service._provider.name == "openai"
    # The dedicated provider was built with chat-side credentials —
    # proves the back-compat code path was taken.
    assert service._provider._client.api_key == "legacy-chat-side-key"
    assert service._model == "text-embedding-3-small"


def test_embedding_does_not_auto_fallback_without_explicit_fallback_provider(tmp_path) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            gemini=LLMProviderConfig(api_key="gemini-key"),
            embedding=EmbeddingConfig(
                provider="openai",
                api_key="",
                fallback_enabled=True,
                fallback_provider="",
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is None


def test_gemini_embedding_uses_independent_dimension_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import registry as registry_mod

    class StubGeminiProvider:
        supports_embedding = True
        name = "gemini"

        def __init__(
            self,
            api_key: str,
            model: str = "gemini-2.5-flash",
            timeout: float = 300.0,
            base_url: str = "",
            embedding_output_dimensionality: int | None = None,
        ) -> None:
            self.api_key = api_key
            self.model = model
            self.timeout = timeout
            self.base_url = base_url
            self.embedding_output_dimensionality = embedding_output_dimensionality

        async def embed(self, text: str, *, model: str | None = None) -> list[float]:
            return [0.1] * int(self.embedding_output_dimensionality or 3072)

    monkeypatch.setattr(registry_mod, "gemini_sdk_available", lambda: True)
    monkeypatch.setattr(registry_mod, "GeminiProvider", StubGeminiProvider)

    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(
                api_key="chat-side-gemini-key",
                model="gemini-2.5-flash",
            ),
            embedding=EmbeddingConfig(
                provider="gemini",
                model="gemini-embedding-001",
                api_key="dedicated-gemini-embedding-key",
                base_url="https://embed-gemini.example.test",
                output_dimensionality=1024,
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, LLMRegistry())

    assert service is not None
    assert service._provider.api_key == "dedicated-gemini-embedding-key"
    assert service._provider.base_url == "https://embed-gemini.example.test"
    assert service._provider.embedding_output_dimensionality == 1024
    assert service._model == "gemini-embedding-001"
    assert service._cache_model == "gemini-embedding-001#dim=1024"


def test_openai_embedding_uses_independent_dimension_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import registry as registry_mod

    class StubOpenAIProvider:
        supports_embedding = True

        def __init__(
            self,
            api_key: str,
            model: str = "gpt-4o",
            base_url: str = "",
            provider_name: str = "openai",
            embedding_output_dimensionality: int = 0,
            **_: object,
        ) -> None:
            self.api_key = api_key
            self.model = model
            self.base_url = base_url
            self._provider_name = provider_name
            self.embedding_output_dimensionality = embedding_output_dimensionality

        @property
        def name(self) -> str:
            return self._provider_name

        async def embed(self, text: str, *, model: str | None = None) -> list[float]:
            return [0.1] * int(self.embedding_output_dimensionality or 1536)

    monkeypatch.setattr(registry_mod, "OpenAIProvider", StubOpenAIProvider)

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="chat-side-openai-key",
                model="gpt-4o-mini",
            ),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="dedicated-openai-embedding-key",
                base_url="https://embed-openai.example.test/v1",
                output_dimensionality=1024,
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, LLMRegistry())

    assert service is not None
    assert service._provider.api_key == "dedicated-openai-embedding-key"
    assert service._provider.embedding_output_dimensionality == 1024
    assert service._model == "text-embedding-3-small"
    assert service._cache_model == "text-embedding-3-small#dim=1024"


def test_openai_embedding_dimensions_are_limited_to_embedding_3_models() -> None:
    from openbiliclaw.llm.openai_provider import OpenAIProvider

    openai_provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-4o-mini",
        embedding_output_dimensionality=1024,
    )
    compatible_provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        provider_name="openai_compatible",
        embedding_output_dimensionality=1024,
    )

    assert openai_provider._supports_embedding_dimensions("text-embedding-3-small") is True
    assert openai_provider._supports_embedding_dimensions("text-embedding-ada-002") is False
    assert compatible_provider._supports_embedding_dimensions("text-embedding-3-small") is False


def test_ollama_embedding_cache_model_ignores_unhonored_dimension(tmp_path) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(
                model="llama3",
                base_url="http://localhost:11434/v1",
            ),
            embedding=EmbeddingConfig(
                provider="ollama",
                model="bge-m3",
                base_url="http://localhost:11434/v1",
                output_dimensionality=768,
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is not None
    assert service._provider.name == "ollama"
    assert service._cache_model == "bge-m3"


def test_embedding_cache_separates_same_text_by_dimension(tmp_path) -> None:
    from openbiliclaw.llm.embedding import EmbeddingCache, EmbeddingService

    class StaticEmbedder:
        name = "static"

        async def embed(self, text: str, *, model: str = "") -> list[float]:
            if "dim=1024" in model:
                return [1.0] * 1024
            return [1.0] * 768

    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()

    service_768 = EmbeddingService(
        StaticEmbedder(),
        model="fake-embedding#dim=768",
        cache_model="fake-embedding#dim=768",
        persistent_cache=cache,
    )
    service_1024 = EmbeddingService(
        StaticEmbedder(),
        model="fake-embedding#dim=1024",
        cache_model="fake-embedding#dim=1024",
        persistent_cache=cache,
    )

    vector_768 = asyncio.run(service_768.embed("same text"))
    vector_1024 = asyncio.run(service_1024.embed("same text"))

    assert len(vector_768) == 768
    assert len(vector_1024) == 1024

    class FailingEmbedder:
        name = "failing"

        async def embed(self, text: str, *, model: str = "") -> list[float]:
            raise AssertionError("lookup_cached should read L2 without calling provider")

    cached_768 = EmbeddingService(
        FailingEmbedder(),
        model="fake-embedding#dim=768",
        cache_model="fake-embedding#dim=768",
        persistent_cache=cache,
    )
    cached_1024 = EmbeddingService(
        FailingEmbedder(),
        model="fake-embedding#dim=1024",
        cache_model="fake-embedding#dim=1024",
        persistent_cache=cache,
    )

    assert len(cached_768.lookup_cached("same text")) == 768
    assert len(cached_1024.lookup_cached("same text")) == 1024


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
def test_embedding_uses_explicit_fallback_provider(tmp_path) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            gemini=LLMProviderConfig(api_key="gemini-key"),
            embedding=EmbeddingConfig(
                provider="openai",
                api_key="",
                fallback_enabled=True,
                fallback_provider="gemini",
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is not None
    assert service._provider.name == "gemini"


def test_openai_embedding_chat_credential_fallback_still_warns_once(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from openbiliclaw.config import EmbeddingConfig
    from openbiliclaw.llm import registry as registry_mod

    registry_mod._embedding_compat_warned.clear()
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-test", base_url=""),
            embedding=EmbeddingConfig(
                provider="openai",
                api_key="",
                base_url="",
                fallback_enabled=True,
            ),
        ),
        data_dir=str(tmp_path),
    )

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.llm.registry"):
        service = build_embedding_service(config, build_llm_registry(config))
        again = build_embedding_service(config, build_llm_registry(config))

    assert service is not None
    assert again is not None
    compat = [r for r in caplog.records if "back-compat" in r.getMessage().lower()]
    assert len(compat) == 1
    assert "[llm.openai]" in compat[0].getMessage()


# ---------------------------------------------------------------------------
# v0.3.46+ — explicit ``[llm.embedding].provider = "openrouter"`` must
# build a working embedding provider. Pre-fix the requested provider was
# silently dropped because ``openrouter`` was missing from
# ``_EMBEDDING_CAPABLE_PROVIDERS``, leaving the fallback chain empty and
# logging "No embedding-capable provider available".


def test_build_embedding_service_supports_openrouter_when_requested_explicitly(
    tmp_path,
) -> None:
    from openbiliclaw.config import EmbeddingConfig
    from openbiliclaw.llm.openrouter_provider import OpenRouterProvider

    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(
                api_key="openrouter-chat-key",
                model="openai/gpt-5-nano",
                base_url="https://openrouter.ai/api/v1",
                http_referer="https://openbiliclaw.local",
                x_title="OpenBiliClaw",
            ),
            embedding=EmbeddingConfig(
                provider="openrouter",
                model="google/gemini-embedding-2-preview",
                api_key="dedicated-openrouter-embedding-key",
                base_url="https://openrouter.ai/api/v1",
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is not None, (
        "openrouter must build when explicitly requested via [llm.embedding]"
    )
    assert isinstance(service._provider, OpenRouterProvider)
    assert service._provider.name == "openrouter"
    assert service._model == "google/gemini-embedding-2-preview"
    # Dedicated embedding key wins over the chat-side key.
    assert service._provider._client.api_key == "dedicated-openrouter-embedding-key"
    # Attribution headers from [llm.openrouter] flow into the embedding
    # provider so dashboard accounting matches chat traffic.
    assert service._provider._extra_headers() == {
        "HTTP-Referer": "https://openbiliclaw.local",
        "X-Title": "OpenBiliClaw",
    }


def test_build_embedding_service_openrouter_requires_explicit_model(
    tmp_path,
) -> None:
    """OpenRouter routes embeddings by ``<vendor>/<model>`` slug — there
    is no safe default. Refuse to build (and log the standard "no
    embedding-capable provider" WARNING) instead of 404ing at first
    embed call."""
    from openbiliclaw.config import EmbeddingConfig

    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            embedding=EmbeddingConfig(
                provider="openrouter",
                model="",  # no model — must refuse
                api_key="openrouter-embedding-key",
                base_url="https://openrouter.ai/api/v1",
            ),
        ),
        data_dir=str(tmp_path),
    )

    service = build_embedding_service(config, build_llm_registry(config))

    assert service is None


def test_emit_embedding_compat_warning_fires_once_per_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The migration WARNING must fire exactly once per provider per
    process — runtime_context rebuilds embedding on every PUT
    /api/config, and we don't want to spam the log on each save."""
    import logging

    from openbiliclaw.llm import registry as registry_mod

    registry_mod._embedding_compat_warned.clear()

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.llm.registry"):
        registry_mod._emit_embedding_compat_warning("openai")
        registry_mod._emit_embedding_compat_warning("openai")
        registry_mod._emit_embedding_compat_warning("openai")

    compat = [r for r in caplog.records if "back-compat" in r.getMessage().lower()]
    assert len(compat) == 1
    assert "[llm.openai]" in compat[0].getMessage()
    assert "openai" in registry_mod._embedding_compat_warned


def test_openai_provider_supports_embedding_flag_is_set() -> None:
    """``supports_embedding`` must be True for providers with a working
    embeddings endpoint and False for those that don't. This is the
    canonical signal used by ``build_embedding_service`` — replacing the
    fragile ``hasattr(provider, "embed")`` check."""
    from openbiliclaw.llm.claude_provider import ClaudeProvider
    from openbiliclaw.llm.gemini_provider import gemini_sdk_available
    from openbiliclaw.llm.ollama_provider import OllamaProvider
    from openbiliclaw.llm.openai_provider import DeepSeekProvider, OpenAIProvider
    from openbiliclaw.llm.openrouter_provider import OpenRouterProvider

    # Have a working /v1/embeddings backend
    assert OpenAIProvider.supports_embedding is True
    assert OllamaProvider.supports_embedding is True

    # Inherit from OpenAIProvider but their backend has no embeddings route
    assert DeepSeekProvider.supports_embedding is False
    assert OpenRouterProvider.supports_embedding is False

    # No embeddings API at all
    assert ClaudeProvider.supports_embedding is False

    if gemini_sdk_available():
        from openbiliclaw.llm.gemini_provider import GeminiProvider

        assert GeminiProvider.supports_embedding is True


@pytest.mark.asyncio
async def test_registry_complete_does_not_fallback_when_disabled() -> None:
    openai = FakeProvider("openai", errors=[LLMProviderError("down")])
    claude = FakeProvider("claude", responses=[LLMResponse(content="ok", provider="claude")])
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=False,
                openai=LLMProviderConfig(api_key="openai-key"),
                claude=LLMProviderConfig(api_key="claude-key"),
            )
        ),
        provider_overrides={
            "openai": openai,
            "claude": claude,
        },
        fallback_order=["openai", "claude"],
    )

    with pytest.raises(LLMFallbackError):
        await registry.complete([{"role": "user", "content": "hi"}])

    assert openai.call_count == 1
    assert claude.call_count == 0


@pytest.mark.asyncio
async def test_registry_falls_back_on_retryable_errors() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=True,
                fallback_provider="claude",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMProviderError("down")]),
            "claude": FakeProvider(
                "claude",
                responses=[LLMResponse(content="ok", provider="claude")],
            ),
        },
        fallback_order=["openai", "claude"],
    )

    response = await registry.complete([{"role": "user", "content": "hi"}])

    assert response.provider == "claude"
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_registry_does_not_auto_fallback_without_explicit_fallback_provider() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=True,
                fallback_provider="",
                openai=LLMProviderConfig(api_key="openai-key"),
                claude=LLMProviderConfig(api_key="claude-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMProviderError("down")]),
            "claude": FakeProvider(
                "claude",
                responses=[LLMResponse(content="ok", provider="claude")],
            ),
        },
        fallback_order=["openai", "claude"],
    )

    with pytest.raises(LLMFallbackError):
        await registry.complete([{"role": "user", "content": "hi"}])

    assert registry._fallback_order() == ["openai"]
    assert registry.get("claude").call_count == 0


@pytest.mark.asyncio
async def test_registry_does_not_fallback_on_response_error() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=True,
                fallback_provider="claude",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMResponseError("bad response")]),
            "claude": FakeProvider(
                "claude",
                responses=[LLMResponse(content="ok", provider="claude")],
            ),
        },
        fallback_order=["openai", "claude"],
    )

    with pytest.raises(LLMResponseError):
        await registry.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_registry_health_check_all() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=True,
                fallback_provider="ollama",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", health=True),
            "ollama": FakeProvider("ollama", health=False),
        },
        fallback_order=["openai", "ollama"],
    )

    results = await registry.health_check_all()

    assert results["openai"].available is True
    assert results["openai"].is_default is True
    assert results["ollama"].available is False


@pytest.mark.asyncio
async def test_registry_temporarily_cools_down_rate_limited_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr("openbiliclaw.llm.base.time.monotonic", lambda: clock["now"])

    openai = FakeProvider("openai", errors=[LLMRateLimitError("limited")])
    claude = FakeProvider("claude")
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                fallback_enabled=True,
                fallback_provider="claude",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": openai,
            "claude": claude,
        },
        fallback_order=["openai", "claude"],
    )

    first = await registry.complete([{"role": "user", "content": "hi"}])
    second = await registry.complete([{"role": "user", "content": "hi again"}])
    clock["now"] += 61
    third = await registry.complete([{"role": "user", "content": "welcome back"}])

    assert first.provider == "claude"
    assert second.provider == "claude"
    assert third.provider == "openai"
    assert openai.call_count == 2


@pytest.mark.asyncio
async def test_registry_complete_provider_uses_exact_provider_and_model() -> None:
    openai = FakeProvider("openai")
    claude = FakeProvider("claude", responses=[LLMResponse(content="ok", provider="claude")])
    registry = LLMRegistry()
    registry.register(openai, default=True)
    registry.register(claude)

    response = await registry.complete_provider(
        "CLAUDE",
        [{"role": "user", "content": "hi"}],
        model="claude-sonnet-4.5",
    )

    assert response.provider == "claude"
    assert openai.call_count == 0
    assert claude.call_count == 1
    assert claude.models == ["claude-sonnet-4.5"]


@pytest.mark.asyncio
async def test_registry_complete_provider_refuses_embedding_only_provider() -> None:
    registry = LLMRegistry()
    registry.register(FakeProvider("openai"), default=True)
    ollama = FakeProvider("ollama")
    registry.register(ollama, chat_capable=False)

    assert registry.is_chat_capable("ollama") is False
    assert registry.is_chat_capable("missing") is False
    with pytest.raises(LLMFallbackError):
        await registry.complete_provider("ollama", [{"role": "user", "content": "hi"}])

    assert ollama.call_count == 0


@pytest.mark.asyncio
async def test_registry_complete_provider_rate_limit_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr("openbiliclaw.llm.base.time.monotonic", lambda: clock["now"])

    openai = FakeProvider("openai")
    claude = FakeProvider("claude", errors=[LLMRateLimitError("limited")])
    registry = LLMRegistry()
    registry.register(openai, default=True)
    registry.register(claude)

    with pytest.raises(LLMRateLimitError):
        await registry.complete_provider("claude", [{"role": "user", "content": "hi"}])
    with pytest.raises(LLMRateLimitError):
        await registry.complete_provider("claude", [{"role": "user", "content": "again"}])

    assert openai.call_count == 0
    assert claude.call_count == 1


@pytest.mark.asyncio
async def test_embedding_only_ollama_is_excluded_from_chat_fallback() -> None:
    """Regression: when [llm.embedding] provider="ollama" but the user
    never configured a chat model, the registry registers Ollama so the
    embedding service can reach it — but the chat fallback chain MUST
    skip it. Otherwise a primary cloud LLM failure cascades to Ollama,
    which only has bge-m3 on disk, returning 404 from /api/chat and the
    user sees 'All providers failed (openai, ollama)'.
    """
    from openbiliclaw.config import EmbeddingConfig

    cfg = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            # Ollama: NO chat model configured. Empty model + non-default.
            ollama=LLMProviderConfig(
                api_key="ollama",
                model="",  # ← critical: no chat model
                base_url="http://localhost:11434/v1",
            ),
            # Embedding wants Ollama → forces registration even though
            # the user never set up chat.
            embedding=EmbeddingConfig(provider="ollama", model="bge-m3"),
        )
    )

    openai_fake = FakeProvider("openai", errors=[LLMProviderError("primary failed")])
    ollama_fake = FakeProvider(
        "ollama",
        # If the bug is back, the chain will reach this — and we'd want
        # to assert that it WASN'T called. We don't queue any responses;
        # if reached it'd raise IndexError.
    )

    registry = build_llm_registry(
        cfg,
        provider_overrides={"openai": openai_fake, "ollama": ollama_fake},
    )

    # Sanity: both providers ARE registered (embedding service still
    # needs to find ollama).
    assert "openai" in registry.available_providers
    assert "ollama" in registry.available_providers

    # ...but the chat fallback should NOT include ollama.
    chat_chain = registry._fallback_order()
    assert "ollama" not in chat_chain, (
        f"embedding-only ollama leaked into chat fallback: {chat_chain}"
    )

    # End-to-end: chat call with a failing primary should raise the
    # primary error (not silently fall through to ollama and 404).
    with pytest.raises(LLMProviderError):
        await registry.complete([{"role": "user", "content": "hi"}])
    # Verify ollama was never called.
    assert ollama_fake.call_count == 0


@pytest.mark.asyncio
async def test_ollama_with_explicit_chat_model_is_chat_capable() -> None:
    """Counterpart to the embedding-only test: when the user configures
    [llm.ollama] model = "llama3", Ollama IS chat-capable and SHOULD
    appear in the chat fallback. (We had to make sure the previous fix
    didn't accidentally exclude every Ollama from chat.)
    """
    cfg = Config(
        llm=LLMConfig(
            default_provider="openai",
            fallback_enabled=True,
            fallback_provider="ollama",
            openai=LLMProviderConfig(api_key="openai-key"),
            ollama=LLMProviderConfig(
                api_key="ollama",
                model="llama3",  # ← explicit chat model
                base_url="http://localhost:11434/v1",
            ),
        )
    )

    registry = build_llm_registry(
        cfg,
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMProviderError("primary failed")]),
            "ollama": FakeProvider(
                "ollama", responses=[LLMResponse(content="ok", provider="ollama")]
            ),
        },
    )
    chat_chain = registry._fallback_order()
    assert chat_chain == ["openai", "ollama"]

    response = await registry.complete([{"role": "user", "content": "hi"}])
    assert response.provider == "ollama"


@pytest.mark.asyncio
async def test_ollama_named_as_fallback_provider_is_chat_capable_without_model() -> None:
    """Regression: ``[llm].fallback_provider = "ollama"`` is an explicit
    request to use local Ollama as the chat fallback, even when the user
    never separately filled ``[llm.ollama] model``.

    Pre-fix, ``_ollama_is_chat_capable`` only honoured ``[llm.ollama]
    model`` / ``default_provider`` / per-module overrides, so an Ollama
    named *only* as the fallback provider got tagged embedding-only and
    was silently dropped from the chat fallback chain. The primary
    failure then raised ``LLMFallbackError`` instead of routing to
    Ollama — fallback never fired and there was no warning. This is the
    exact config shape from the bug report: default cloud provider +
    fallback_provider=ollama + embedding=ollama + empty [llm.ollama]
    model.
    """
    cfg = Config(
        llm=LLMConfig(
            default_provider="openai",
            fallback_enabled=True,
            fallback_provider="ollama",
            openai=LLMProviderConfig(api_key="openai-key"),
            # No explicit chat model — Ollama is named ONLY as the
            # fallback provider. It still must be chat-capable.
            ollama=LLMProviderConfig(
                api_key="ollama",
                model="",
                base_url="http://localhost:11434/v1",
            ),
            # Embedding also points at Ollama — this is the exact shape
            # that used to tag the provider embedding-only.
            embedding=EmbeddingConfig(provider="ollama", model="bge-m3"),
        )
    )

    registry = build_llm_registry(
        cfg,
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMProviderError("primary failed")]),
            "ollama": FakeProvider(
                "ollama", responses=[LLMResponse(content="ok", provider="ollama")]
            ),
        },
    )

    assert registry.is_chat_capable("ollama") is True
    assert registry._fallback_order() == ["openai", "ollama"]

    response = await registry.complete([{"role": "user", "content": "hi"}])
    assert response.provider == "ollama"
