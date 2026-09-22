"""Offline evaluation harness for the Sponsored ``soul.consolidation`` pilot.

The harness drives the closed-source runtime exactly like production does and
validates that every response stays inside the consolidation output contract.
It is deliberately provider-agnostic: point it at the Rust mock binary for a
smoke test, or at the official runtime (with a wrapped sponsored key) to
measure real model quality before enabling the feature for users.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.json_utils import parse_llm_json_tolerant

if TYPE_CHECKING:
    from openbiliclaw.llm.sponsored_provider import SponsoredProvider

PILOT_TASK = "soul.consolidation"
_CONSOLIDATION_SCOPES = ("likes", "dislikes")


@dataclass(frozen=True)
class PilotFixture:
    name: str
    payload: dict[str, Any]


@dataclass
class PilotResult:
    name: str
    ok: bool
    latency_seconds: float = 0.0
    content: str = ""
    model: str = ""
    usage: dict[str, int] | None = None
    parsed: dict[str, Any] | None = None
    error: str = ""


def _like_member(name: str, weight: float, category: str) -> dict[str, Any]:
    return {"name": name, "weight": weight, "category": category}


def default_fixtures() -> list[PilotFixture]:
    """Small synthetic fixtures; replace with real anonymized clusters for eval."""

    return [
        PilotFixture(
            name="merge-near-synonyms",
            payload={
                "likes_clusters": [
                    {
                        "cluster_id": "c1",
                        "known_distinct_pairs": [],
                        "members": [
                            _like_member("搞笑", 1.0, "娱乐"),
                            _like_member("娱乐搞笑", 0.9, "娱乐"),
                        ],
                    }
                ],
                "dislikes_clusters": [],
            },
        ),
        PilotFixture(
            name="keep-parent-child",
            payload={
                "likes_clusters": [
                    {
                        "cluster_id": "c2",
                        "known_distinct_pairs": [["篮球", "NBA"]],
                        "members": [
                            _like_member("篮球", 1.0, "体育"),
                            _like_member("NBA", 0.8, "体育"),
                        ],
                    }
                ],
                "dislikes_clusters": [],
            },
        ),
        PilotFixture(
            name="dislike-synonyms",
            payload={
                "likes_clusters": [],
                "dislikes_clusters": [
                    {
                        "cluster_id": "d1",
                        "known_distinct_pairs": [],
                        "members": ["标题党", "标题党视频"],
                    }
                ],
            },
        ),
    ]


def load_fixtures(path: str) -> list[PilotFixture]:
    """Load fixtures from a JSON array of ``{name, payload}`` objects."""

    with open(path, encoding="utf-8") as handle:
        raw = json.loads(handle.read())
    if not isinstance(raw, list):
        raise ValueError("fixtures file must contain a JSON array")
    fixtures: list[PilotFixture] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"fixture #{index} must be an object")
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError(f"fixture #{index} has no object payload")
        fixtures.append(
            PilotFixture(name=str(item.get("name") or f"fixture-{index}"), payload=dict(payload))
        )
    if not fixtures:
        raise ValueError("fixtures file is empty")
    return fixtures


def validate_consolidation_output(content: str) -> dict[str, Any]:
    """Parse and shape-check one consolidation response."""

    parsed = parse_llm_json_tolerant(content)
    if not isinstance(parsed, dict):
        raise ValueError("consolidation output is not a JSON object")
    for scope in _CONSOLIDATION_SCOPES:
        entries = parsed.get(scope)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ValueError(f"{scope} must be a list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{scope} entries must be objects")
            if not str(entry.get("cluster_id", "")).strip():
                raise ValueError(f"{scope} entry is missing cluster_id")
    return parsed


async def run_pilot_eval(
    provider: SponsoredProvider,
    fixtures: Sequence[PilotFixture] | None = None,
) -> dict[str, Any]:
    """Execute every fixture through the Sponsored path and summarize results."""

    selected = list(fixtures) if fixtures is not None else default_fixtures()
    results: list[PilotResult] = []
    for fixture in selected:
        started = time.perf_counter()
        result = PilotResult(name=fixture.name, ok=False)
        try:
            response = await provider.execute_task(
                caller=PILOT_TASK,
                user_payload=fixture.payload,
            )
            result.latency_seconds = time.perf_counter() - started
            result.content = response.content
            result.model = response.model
            result.usage = response.usage
            result.parsed = validate_consolidation_output(response.content)
            result.ok = True
        except Exception as exc:  # noqa: BLE001 - eval must report every failure
            result.latency_seconds = time.perf_counter() - started
            error_code = getattr(exc, "code", "")
            result.error = f"{error_code or type(exc).__name__}: {exc}"
        results.append(result)

    passed = sum(1 for result in results if result.ok)
    latencies = [result.latency_seconds for result in results if result.ok]
    return {
        "task": PILOT_TASK,
        "fixtures": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "avg_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "results": [
            {
                "name": result.name,
                "ok": result.ok,
                "latency_seconds": round(result.latency_seconds, 3),
                "model": result.model,
                "usage": result.usage,
                "parsed": result.parsed,
                "content": result.content,
                "error": result.error,
            }
            for result in results
        ],
    }
