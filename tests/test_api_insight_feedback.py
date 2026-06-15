"""End-to-end tests for POST /api/insights/feedback.

Closes the loop that was previously implemented but unwired: a user
confirm/reject on an insight card now routes through
``SoulEngine.update_from_feedback`` and calibrates the stored hypothesis
(confirm → confidence ≥0.75 + validated; reject → ≤0.35 + unvalidated).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine

if TYPE_CHECKING:
    from pathlib import Path


class _FakeRegistry:
    """Minimal LLM stand-in — update_from_feedback makes no LLM calls."""

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
        return LLMResponse(content="[]", provider="openai")


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


_HYP = "用户可能通过深度内容获得掌控感。"


def _build_client(tmp_path: Path) -> tuple[TestClient, MemoryManager]:
    from openbiliclaw.soul.profile import InsightHypothesis, OnionProfile

    memory = MemoryManager(tmp_path / "data")
    memory.initialize()
    memory.get_layer("insight").data.update(
        {
            "hypotheses": [
                {
                    "hypothesis": _HYP,
                    "evidence": ["最近连续浏览高信息密度内容。"],
                    "confidence": 0.62,
                    "validated": False,
                    "created_at": "2026-06-14",
                }
            ]
        }
    )
    # Seed the soul-layer snapshot too: get_profile() (profile-summary + delight)
    # reads active_insights from here, not from the insight layer. Lets us assert
    # the calibration propagates to what the UI / recommender actually consume.
    prof = OnionProfile()
    prof.active_insights = [
        InsightHypothesis(
            hypothesis=_HYP,
            evidence=["最近连续浏览高信息密度内容。"],
            confidence=0.62,
            validated=False,
            created_at="2026-06-14",
        )
    ]
    memory.get_layer("soul").data.update(prof.to_dict())
    memory.get_layer("soul").save()
    engine = SoulEngine(llm=_FakeRegistry(), memory=memory)
    app = create_app(memory_manager=memory, database=memory._database, soul_engine=engine)
    return TestClient(app), memory


def _summary_insight(client: TestClient) -> dict:
    summary = client.get("/api/profile-summary").json()
    return next(i for i in summary["active_insights"] if i["hypothesis"] == _HYP)


def _stored_hypothesis(memory: MemoryManager) -> dict:
    return memory.get_layer("insight").data["hypotheses"][0]


def test_insight_feedback_reject_soft_invalidates(tmp_path: Path) -> None:
    client, memory = _build_client(tmp_path)

    resp = client.post(
        "/api/insights/feedback",
        json={"hypothesis": "用户可能通过深度内容获得掌控感。", "signal": "reject"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["matched"] is True
    assert body["validated"] is False
    assert body["confidence"] <= 0.35
    # Persisted: the soft-invalidation actually landed in the insight layer.
    stored = _stored_hypothesis(memory)
    assert stored["validated"] is False
    assert stored["confidence"] <= 0.35
    # And it propagated to the soul-layer snapshot that profile-summary /
    # delight read — not just the insight layer (regression guard for the
    # gap the real-browser E2E surfaced).
    surfaced = _summary_insight(client)
    assert surfaced["confidence"] <= 0.35
    assert surfaced["validated"] is False
    # And a feedback event was logged.
    assert memory.query_events(event_types=["feedback"])


def test_insight_feedback_confirm_validates_and_raises_confidence(tmp_path: Path) -> None:
    client, memory = _build_client(tmp_path)

    resp = client.post(
        "/api/insights/feedback",
        json={"hypothesis": "用户可能通过深度内容获得掌控感。", "signal": "confirm"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["validated"] is True
    assert body["confidence"] >= 0.75
    stored = _stored_hypothesis(memory)
    assert stored["validated"] is True
    assert stored["confidence"] >= 0.75
    surfaced = _summary_insight(client)
    assert surfaced["confidence"] >= 0.75
    assert surfaced["validated"] is True


def test_insight_feedback_unknown_hypothesis_reports_no_match(tmp_path: Path) -> None:
    client, memory = _build_client(tmp_path)

    resp = client.post(
        "/api/insights/feedback",
        json={"hypothesis": "完全不存在的假设", "signal": "confirm"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is False
    # Untouched original hypothesis.
    assert _stored_hypothesis(memory)["validated"] is False


def test_insight_feedback_rejects_bad_signal(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)

    resp = client.post(
        "/api/insights/feedback",
        json={"hypothesis": "用户可能通过深度内容获得掌控感。", "signal": "meh"},
    )

    assert resp.status_code == 422


def test_insight_feedback_requires_hypothesis(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)

    resp = client.post(
        "/api/insights/feedback",
        json={"hypothesis": "   ", "signal": "confirm"},
    )

    assert resp.status_code == 422
