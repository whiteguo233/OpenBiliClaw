"""The checked-in demo remains executable without credentials or network."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from scripts.replay_director_pairs import _request

from openbiliclaw.decision import JevDecisionProvider
from openbiliclaw.recommendation.director import PairComposer


@pytest.mark.parametrize("fail_middle", [False, True])
async def test_composer_consumes_real_jev_adapter_over_mock_transport(fail_middle: bool) -> None:
    bodies: list[dict] = []

    def handle(sent: httpx.Request) -> httpx.Response:
        body = json.loads(sent.content)
        bodies.append(body)
        assert sent.url == "https://api.typesafe.ai/v1/systemone"
        assert sent.headers["authorization"] == "Bearer synthetic-test-only"
        assert body["model"] == "jev-1.13.0"
        if fail_middle and len(bodies) == 2:
            return httpx.Response(503, text="synthetic remote body must not leak")
        choice = "p1"
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "pair": {
                        "type": "choice",
                        "choice": choice,
                        "probabilities": {
                            key: float(key == choice)
                            for key in body["questions"]["pair"]["criteria"]
                        },
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 11, "output_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
        provider = JevDecisionProvider(api_key="synthetic-test-only", client=client, max_attempts=1)
        request = _request()
        proposal = await PairComposer(provider).prepare_next(request)
        assert proposal.status == "COMPLETE"
        assert proposal.provider_calls == len(bodies) == 3
        assert proposal.realized_strategy == ("mixed" if fail_middle else "jev_recurrent")
        prefix: list[str] = []
        for body, trace in zip(bodies, proposal.pairs, strict=True):
            assert body["state"]["tentative_prefix"] == prefix
            assert body["state"]["session_context"] == request.session.prompt_context(fast=True)
            assert trace.evidence_json is not None
            evidence = json.loads(trace.evidence_json)
            assert evidence["provider"] == "jev"
            assert evidence["input_digest"] == trace.decision_input_digest
            assert evidence["request_id"] == trace.decision_request_id
            prefix.extend(trace.selected_refs)
        if fail_middle:
            middle = json.loads(proposal.pairs[1].evidence_json or "{}")
            assert middle["http_status"] == 503
            assert middle["status"] == "UNAVAILABLE"
        assert "synthetic remote body" not in proposal.model_dump_json()
        assert "synthetic-test-only" not in proposal.model_dump_json()
        assert not client.is_closed  # Composer does not own the borrowed provider/client.
        await provider.aclose()


def test_synthetic_pair_replay_runs_and_separates_feedback_from_prefix() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/replay_director_pairs.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    data = json.loads(completed.stdout)
    assert data["synthetic_only"] is True
    assert data["network_calls"] == 0
    assert not completed.stderr
    for scenario in data["scenarios"].values():
        assert scenario["status"] == "COMPLETE"
        assert scenario["tentative"] is True
        assert scenario["provider_calls"] == 3
        assert scenario["actual_event_counts"] == [1, 1, 1]
        refs: list[str] = []
        for pair in scenario["pairs"]:
            assert pair["prefix"] == refs
            assert len(pair["selected"]) == 2
            refs.extend(pair["selected"])
        assert len(set(refs)) == 6
    normal = data["scenarios"]["sequential_choices"]
    fallback = data["scenarios"]["middle_pair_abstention"]
    assert normal["realized_strategy"] == "jev_recurrent"
    assert fallback["realized_strategy"] == "mixed"
    assert fallback["pairs"][1]["reason"] == "none_suitable"
    assert fallback["pairs"][1]["origin"] == "deterministic"
