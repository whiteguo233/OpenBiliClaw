"""Network-free synthetic replay of recurrent pairs and a mid-screen fallback.

Run with the project environment: python scripts/replay_director_pairs.py
Outputs JSON to stdout; reads no config, credentials, database or real history.
Fixture probabilities are invented one-hot test values, not quality evidence.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.decision import (  # noqa: E402
    DecisionEvidence,
    DecisionRequest,
    ScriptedDecisionProvider,
)
from openbiliclaw.recommendation.director import (  # noqa: E402
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    PairComposer,
    RealFeedback,
    ScreenConstraints,
    TopicQuota,
)


class _SyntheticChoices:
    """Adapt explicit fixture choices to the actual bounded option menu."""

    def __init__(self, choices: tuple[str, ...]) -> None:
        self._choices = iter(choices)
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request.model_copy(deep=True))
        question = request.questions["pair"]
        assert question.type == "choice"
        choice = next(self._choices)
        assert choice in question.criteria, "synthetic fixture no longer fits the policy"
        # Use the real response parser through the existing network-free fake.
        provider = ScriptedDecisionProvider(
            [
                {
                    "model": "synthetic-replay.v1",
                    "answers": {
                        "pair": {
                            "type": "choice",
                            "choice": choice,
                            "probabilities": {
                                key: float(key == choice) for key in question.criteria
                            },
                            "confidence": 1.0,
                        }
                    },
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }
            ],
            model="synthetic-replay.v1",
        )
        return await provider.evaluate(request)

    async def aclose(self) -> None:
        pass


def _request() -> CompositionRequest:
    titles = (
        ("本地 AI 工具上手", "ai"),
        ("一个能运行的小项目", "ai"),
        ("终端效率技巧", "tools"),
        ("轻量工具演示", "tools"),
        ("周末徒步路线", "outdoors"),
        ("散步摄影练习", "outdoors"),
        ("纸艺入门体验", "craft"),
        ("家庭手作小案例", "craft"),
    )
    return CompositionRequest(
        request_id="synthetic-replay",
        session=CompositionSession(
            session_ref="synthetic-session",
            stream_ref="synthetic-stream",
            state_revision=2,
            feedback_cursor="synthetic-event-1",
            configuration_revision=1,
            explicit_intent="今天看能马上上手的内容，穿插一点轻松探索。",
            real_feedback=(
                RealFeedback(
                    event_ref="synthetic-event-1",
                    batch_ref="synthetic-previous-batch",
                    kind="explicit_request",
                    description="今天先别给我原理长讲。",
                ),
            ),
        ),
        candidate_set_ref="synthetic-candidate-set",
        candidate_revision=1,
        feature_schema_version="synthetic-features.v1",
        direction_ref="synthetic-direction",
        direction="上手体验 → 实践延伸 → 轻松探索",
        cards=tuple(
            CompositionCard(
                ref=f"c{index}",
                item_key=f"synthetic:{index}",
                title=title,
                topic_group=topic,
                baseline_score=1.0 - index / 10,
            )
            for index, (title, topic) in enumerate(titles)
        ),
        constraints=ScreenConstraints(
            version="synthetic-constraints.v1",
            topic_quotas=(
                TopicQuota(topic_group="ai", maximum=2),
                TopicQuota(topic_group="outdoors", minimum=1),
            ),
        ),
        target_count=6,
    )


async def replay() -> dict[str, object]:
    """Return two reproducible traces; neither is a real Jev or serving result."""
    scenarios: dict[str, object] = {}
    for name, choices in (
        ("sequential_choices", ("p3", "p1", "p0")),
        ("middle_pair_abstention", ("p3", "none_suitable", "p0")),
    ):
        provider = _SyntheticChoices(choices)
        request = _request()
        proposal = await PairComposer(provider).prepare_next(request)
        by_ref = {card.ref: card.title for card in request.cards}
        scenarios[name] = {
            "status": proposal.status,
            "tentative": proposal.tentative,
            "realized_strategy": proposal.realized_strategy,
            "provider_calls": proposal.provider_calls,
            "pairs": [
                {
                    "prefix": list(trace.prefix_refs),
                    "selected": list(trace.selected_refs),
                    "titles": [by_ref[ref] for ref in trace.selected_refs],
                    "origin": trace.origin,
                    "reason": trace.reason_code,
                }
                for trace in proposal.pairs
            ],
            "actual_event_counts": [
                len(call.state["session_context"]["real_feedback"])  # type: ignore[index, arg-type]
                for call in provider.requests
            ],
        }
    return {"synthetic_only": True, "network_calls": 0, "scenarios": scenarios}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(replay()), ensure_ascii=False, sort_keys=True, indent=2))
