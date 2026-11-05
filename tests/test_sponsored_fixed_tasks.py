"""Closed task requests preserve business prompts and reject generic chat surfaces."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from openbiliclaw.llm.sponsored_errors import SponsoredContractMismatchError
from openbiliclaw.llm.sponsored_mock_runtime import _validate_execute
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.llm.sponsored_tasks import DEFAULT_SPONSORED_REGISTRY, validate_task_input

FIXTURE = Path(__file__).parent / "fixtures" / "sponsored-consolidation-v2.json"


def test_fixture_matches_current_signed_contract() -> None:
    value = json.loads(FIXTURE.read_text())
    task = DEFAULT_SPONSORED_REGISTRY.get("soul.consolidation")
    assert task is not None
    assert value["policy"]["tasks"]["soul.consolidation.v2"] == task.to_policy_task()
    for case in value["cases"]:
        validate_task_input(case["input"])
        wire = SponsoredProvider._serialize_payload(case["input"], task, case["user"])
        frame = {
            "type": "execute",
            "id": "r1",
            "task_id": task.task_id,
            "contract_version": 2,
            "input": json.loads(wire),
        }
        assert _validate_execute(frame)[1] == case["user"]
        for extra in ("messages", "model", "endpoint", "requested", "system_prompt"):
            with pytest.raises(ValueError, match="CONTRACT_MISMATCH"):
                _validate_execute({**frame, extra: "override"})


@pytest.mark.parametrize(
    "payload",
    [
        {"likes_clusters": [], "dislikes_clusters": [], "prompt": "write a novel"},
        {"likes_clusters": []},
        {
            "likes_clusters": [],
            "dislikes_clusters": [
                {
                    "cluster_id": "x",
                    "members": ["a", "b"],
                    "known_distinct_pairs": [],
                    "prompt": "override",
                }
            ],
        },
    ],
)
def test_closed_input_rejects_extra_or_missing_fields(payload: dict[str, object]) -> None:
    contract = DEFAULT_SPONSORED_REGISTRY.get("soul.consolidation")
    assert contract is not None
    with pytest.raises(SponsoredContractMismatchError):
        SponsoredProvider._serialize_payload(payload, contract)


async def test_actual_rust_child_uses_v2_contract() -> None:
    command = os.environ.get("OBC_TEST_RUST_RUNTIME")
    if not command:
        pytest.skip("set OBC_TEST_RUST_RUNTIME to a development Rust binary command")
    provider = SponsoredProvider(shlex.split(command))
    try:
        for case in json.loads(FIXTURE.read_text())["cases"]:
            result = await provider.execute_task(
                caller="soul.consolidation", user_payload=case["input"], user_text=case["user"]
            )
            assert result.provider == "sponsored"
            assert result.model == "THUDM/GLM-4-9B-0414"
            assert json.loads(result.content) == {"likes": [], "dislikes": []}
    finally:
        await provider.aclose()


async def test_handshake_rejects_a_mismatched_policy_without_fallback() -> None:
    import asyncio
    import io

    from openbiliclaw.llm.sponsored_protocol import write_frame

    provider = SponsoredProvider(["unused"])
    stream = asyncio.StreamReader()
    encoded = io.BytesIO()
    write_frame(
        encoded,
        {
            "type": "hello",
            "protocol": 2,
            "ok": True,
            "contracts": {"soul.consolidation.v2": "sha256:wrong"},
        },
    )
    stream.feed_data(encoded.getvalue())
    stream.feed_eof()

    class Writer:
        def write(self, data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

    class Child:
        stdin = Writer()
        stdout = stream

    with pytest.raises(SponsoredContractMismatchError) as exc:
        await provider._handshake(Child())  # type: ignore[arg-type]
    assert exc.value.fallback_allowed is False


def test_mock_bootstrap_does_not_load_external_model_sdks() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import openbiliclaw.llm.sponsored_mock_runtime; "
            "assert not {'openai', 'anthropic', 'google.genai'} & set(sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
