"""Preview, run a local fake Jev, or explicitly call the real service.

Run from the checkout with PYTHONPATH=src. Live mode requires a local Jev
credential and --confirm-free-credits after checking the account balance.
--config reads the saved endpoint, pinned model, limits and evaluator settings.
No account creation, top-up, production config or production database is used.
Reports contain counts, native usage and sanitized failure codes only.
--fake needs no key: real loopback HTTP, synthetic answers/usage, temporary DBs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.config import Config, ConfigError, JevEvaluationConfig, load_config
from openbiliclaw.decision.context_budget import (
    JevContextBudget,
    JevContextError,
    resolve_jev_context,
)
from openbiliclaw.decision.jev_provider import JevDecisionProvider
from openbiliclaw.decision.models import DecisionEvidence, DecisionRequest
from openbiliclaw.decision.registry import build_decision_provider
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    import httpx

    from openbiliclaw.decision.base import DecisionProvider

# Both direct-file and ``python -m scripts...`` invocation use this checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.jev_fake_backend import SYNTHETIC_KEY, FakeJevBackend  # noqa: E402


class RecordingProvider:
    """Capture the actual typed request and cap the entire run at six POSTs."""

    def __init__(
        self, model: str, native: DecisionProvider | None, budget: JevContextBudget
    ) -> None:
        self.model = model
        self.native = native
        self.budget = budget
        self.calls: list[dict[str, Any]] = []
        self.stopped = False

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        size = self.budget.measure(request.state, request.questions)
        state = request.state
        assert isinstance(state, dict)
        candidates = state["untrusted_candidates"]
        assert isinstance(candidates, list)
        coverage = []
        for candidate in candidates:
            assert isinstance(candidate, dict)
            content = candidate["content"]
            assert isinstance(content, dict)
            coverage.append(content["text_coverage"])
        row = {
            "candidates": len(candidates),
            "questions": len(request.questions),
            "request_bytes": size.request_bytes,
            "max_state_question_bytes": size.max_state_question_bytes,
            "coverage": coverage,
        }
        if self.native is None or self.stopped or len(self.calls) >= 6:
            result = DecisionEvidence(
                status="UNAVAILABLE",
                reason_code="preview_or_run_stopped",
                request_id=request.request_id,
                purpose=request.purpose,
                question_schema_version=request.question_schema_version,
                input_digest=request.input_digest,
                requested_model=self.model,
                latency_ms=0.0,
                attempts=0,
            )
        else:
            result = await self.native.evaluate(request)
            self.stopped = result.status != "OK"
        row.update(
            status=result.status,
            reason=result.reason_code,
            actual_model=result.actual_model,
            usage=result.usage.model_dump() if result.usage else None,
            attempts=result.attempts,
        )
        self.calls.append(row)
        return result

    async def aclose(self) -> None:
        if self.native is not None:
            await self.native.aclose()


async def run(
    model: str,
    live: bool,
    *,
    local_client: httpx.AsyncClient | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    if live and local_client is not None:
        raise ValueError("live and local fake modes are mutually exclusive")
    if config is not None and (
        local_client is not None or model != config.decision.providers.jev.model
    ):
        raise ConfigError("configured acceptance requires the exact configured model")
    settings = config.decision.providers.jev if config is not None else None
    evaluation = config.discovery.jev_evaluation if config is not None else JevEvaluationConfig()
    budget = resolve_jev_context(model, settings.context_limits if settings else None)
    effective_budget = budget.with_byte_guards(
        max_request_bytes=evaluation.max_request_bytes,
        max_state_question_bytes=evaluation.max_state_question_bytes,
    )
    native: DecisionProvider | None = None
    if live and config is not None:
        if not config.decision.providers.jev.enabled:
            raise ConfigError("configured Jev connection is disabled")
        # Preserve the saved destination/key rules. Bound acceptance spending
        # without changing the operator's file or increasing their timeout.
        decision = replace(
            config.decision,
            providers=replace(
                config.decision.providers,
                jev=replace(
                    config.decision.providers.jev,
                    max_attempts=1,
                    timeout_seconds=min(config.decision.providers.jev.timeout_seconds, 15),
                ),
            ),
        )
        native = build_decision_provider(decision)
    elif live or local_client is not None:
        native = JevDecisionProvider(
            api_key=SYNTHETIC_KEY if local_client is not None else os.environ["TYPESAFE_API_KEY"],
            model=model,
            max_attempts=1,
            timeout_seconds=15,
            client=local_client,
        )
    provider = RecordingProvider(model, native, budget)
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag("算法与数据结构", "编程", 0.9)],
            disliked_topics=["承诺保证收益的广告；讨论风险和批判分析不属于此项"],
        )
    )
    body = "\n".join(f"背景段落{i}：介绍算法设计的通用概念。" for i in range(320))
    reports = []
    try:
        # Different budgets intentionally evaluate separate isolated cohorts.
        # Repeated evaluation is a TEST comparison, not production behavior.
        for name, single_cap in (
            ("standard", effective_budget.max_state_question_bytes),
            ("smaller_budget", min(12_000, effective_budget.max_state_question_bytes)),
        ):
            with tempfile.TemporaryDirectory(prefix="jev-single-pass-") as directory:
                db = Database(Path(directory) / "synthetic.db")
                db.initialize()
                try:
                    evaluator = JevContentEvaluator(
                        provider,
                        model=model,
                        context_budget=budget,
                        config=replace(
                            evaluation,
                            max_state_question_bytes=single_cap,
                            max_calls=min(3, evaluation.max_calls),
                            budget_seconds=min(60, evaluation.budget_seconds),
                        ),
                    )
                    engine = ContentDiscoveryEngine(
                        database=db, eval_scorer="jev", jev_evaluator=evaluator
                    )
                    pipeline = DiscoveryCandidatePipeline(
                        database=db, discovery_engine=engine, pool_target_count=10
                    )
                    db.enqueue_discovery_candidates(
                        [
                            DiscoveryCandidateWrite(
                                candidate_key=f"bilibili:BVJEVSYNTH{i}",
                                source_platform="bilibili",
                                source_strategy="search",
                                content_id=f"BVJEVSYNTH{i}",
                                title=f"算法与数据结构通用原理 {i}",
                                body_text=body if i == 2 else "讲解排序算法的基本概念。",
                            )
                            for i in range(3)
                        ]
                    )
                    claim = pipeline.claim_batch(limit=3)
                    assert claim is not None
                    start = len(provider.calls)
                    outcome = await pipeline.evaluate_claim(claim, profile)
                    receipt = await pipeline.complete_claim(outcome, admission_limit=10)
                    missing = db.count_discovery_candidates_by_status().get("pending_eval", 0)
                    current = db.get_discovery_candidates_by_ids([row["id"] for row in claim.rows])
                    preserved = (
                        next(
                            row["body_text"]
                            for row in current
                            if row["content_id"] == "BVJEVSYNTH2"
                        )
                        == body
                    )
                    reports.append(
                        {
                            "case": name,
                            "max_request_bytes": effective_budget.max_request_bytes,
                            "max_state_question_bytes": single_cap,
                            "pipeline": receipt,
                            "pending": missing,
                            "original_preserved": preserved,
                            "requests": provider.calls[start:],
                        }
                    )
                finally:
                    db.close()
            if provider.stopped:
                break
    finally:
        await provider.aclose()
    passed = (
        (live or local_client is not None)
        and len(reports) == 2
        and not provider.stopped
        and all(
            row["pending"] == 0
            and row["original_preserved"]
            and row["pipeline"]["evaluated"] == 3
            and sum(request["candidates"] for request in row["requests"]) == 3
            and all(request["status"] == "OK" for request in row["requests"])
            for row in reports
        )
    )
    preview_valid = len(reports) == 2 and all(
        sum(request["candidates"] for request in row["requests"]) == 3 for row in reports
    )
    return {
        "status": "passed"
        if passed
        else "incomplete"
        if live or local_client is not None
        else "preview"
        if preview_valid
        else "failed",
        "live": live,
        "backend": "local_fake"
        if local_client is not None
        else "configured"
        if live and config is not None
        else "official"
        if live
        else "preview",
        # A configured endpoint may be a relay or a synthetic service. Its
        # provenance cannot be inferred from successful native HTTP alone.
        "usage_is_synthetic": True
        if local_client is not None
        else None
        if live and config is not None
        else False,
        "model": model,
        "context_budget": asdict(effective_budget),
        "cases": reports,
    }


async def run_fake(model: str) -> dict[str, Any]:
    """Run both isolated budget cohorts over real loopback HTTP, never remote."""
    with FakeJevBackend(model=model) as backend:
        async with backend.client() as client:
            report = await run(model, False, local_client=client)
        report["local_http"] = list(backend.records)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Pinned model; must match --config when provided")
    parser.add_argument("--config", type=Path, help="Read only this saved local TOML connection")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument(
        "--fake", action="store_true", help="Use a loopback-only deterministic backend"
    )
    parser.add_argument("--confirm-free-credits", action="store_true")
    args = parser.parse_args()
    if args.live and (
        not args.confirm_free_credits
        or (args.config is None and not os.environ.get("TYPESAFE_API_KEY"))
    ):
        print(
            json.dumps(
                {
                    "status": "not_attempted",
                    "reason": "requires_confirmed_free_credits_and_local_key",
                }
            )
        )
        return 2
    logging.disable(logging.CRITICAL)
    try:
        if args.fake and args.config is not None:
            raise ConfigError("fake mode never reads saved connection settings")
        config = None
        if args.config is not None:
            if not args.config.is_file():
                raise ConfigError("explicit configuration file must exist")
            config = load_config(args.config, consult_environment=False)
        model = args.model or (config.decision.providers.jev.model if config else "jev-1.13.0")
        report = asyncio.run(run_fake(model) if args.fake else run(model, args.live, config=config))
    except (ConfigError, JevContextError):
        print(json.dumps({"status": "not_attempted", "reason": "invalid_jev_configuration"}))
        return 2
    except Exception:
        print(json.dumps({"status": "failed", "reason": "runner_error"}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["status"] not in {"passed", "preview"})


if __name__ == "__main__":
    raise SystemExit(main())
