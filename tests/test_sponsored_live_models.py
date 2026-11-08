"""Real-model test profiles stay bound to policy, including BGE's text-only space."""

from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.sponsored_errors import SponsoredProtocolError, SponsoredUnsupportedTaskError
from openbiliclaw.llm.sponsored_live_profile import (
    BGE_M3_MODEL,
    GRANTED_CHAT_MODELS,
    granted_test_registry,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider


@pytest.mark.parametrize("model", GRANTED_CHAT_MODELS)
async def test_granted_profile_uses_its_signed_embedding_model_and_denies_image_vectors(
    model: str,
) -> None:
    registry = granted_test_registry(model)
    assert {c.model for c in registry} == {model, BGE_M3_MODEL}
    assert bool(registry.get("llm.vision")) == (model == "Qwen/Qwen3.5-4B")

    class Encoder(SponsoredProvider):
        async def execute_task(self, **kwargs: Any) -> LLMResponse:
            assert kwargs["task_id"] == "llm.embedding_text"
            return LLMResponse(model=BGE_M3_MODEL, raw={"embedding": [0.5] * 1024})

    provider = Encoder(["unused"], registry=registry)
    assert len(await provider.embedding("算法与数据结构")) == 1024
    with pytest.raises(SponsoredUnsupportedTaskError):
        await provider.embedding("data:image/png;base64,aW1hZ2U=", image=True)
    assert provider._process is None


async def test_granted_embedding_rejects_a_different_models_vector() -> None:
    class WrongModel(SponsoredProvider):
        async def execute_task(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(model="other-model", raw={"embedding": [0.5] * 1024})

    provider = WrongModel(["unused"], registry=granted_test_registry(GRANTED_CHAT_MODELS[0]))
    with pytest.raises(SponsoredProtocolError):
        await provider.embedding("项目内容")


def test_granted_profile_refuses_unapproved_model_names() -> None:
    with pytest.raises(ValueError):
        granted_test_registry("unreviewed/model")
