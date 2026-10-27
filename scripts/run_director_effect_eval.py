"""Opt-in current-policy Agent evaluation over synthetic Chinese fixtures.

Uses the configured recommendation model with a single pinned route and no
fallback, production LLMService/HorizonPlanner, and isolated temporary memory.
Never writes real config, profile, inventory or feedback. Raw output contains
synthetic fixture text only; errors are recorded by class, not credential-bearing
exception strings. This is not an online satisfaction or real-Jev experiment.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from director_effect_cases import build_cases, score_selection  # noqa: E402

from openbiliclaw.config import load_config  # noqa: E402
from openbiliclaw.llm import build_llm_registry  # noqa: E402
from openbiliclaw.llm.service import (  # noqa: E402
    LLMService,
    ModuleOverride,
    module_overrides_from_config,
)
from openbiliclaw.memory.manager import MemoryManager  # noqa: E402
from openbiliclaw.recommendation.director.composer import PairComposer  # noqa: E402
from openbiliclaw.recommendation.director.planner import HorizonPlanner  # noqa: E402
from openbiliclaw.recommendation.director.policy import DIRECTOR_POLICY_VERSION  # noqa: E402

if TYPE_CHECKING:
    from director_codex_transport import CodexCLIService

    from openbiliclaw.llm.base import LLMResponse


class RecordingLLM:
    """Record actual transport outputs without replacing or judging the model."""

    def __init__(self, service: LLMService | CodexCLIService) -> None:
        self.service = service
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        record: dict[str, Any] = {
            "prompt_sha256": hashlib.sha256(kwargs["system_instruction"].encode()).hexdigest(),
            "input_sha256": hashlib.sha256(kwargs["user_input"].encode()).hexdigest(),
            "input_bytes": len(kwargs["user_input"].encode()),
            "requested_max_tokens": kwargs["max_tokens"],
        }
        self.calls.append(record)
        start = time.monotonic()
        try:
            response = await self.service.complete_structured_task(**kwargs)
            record.update(
                model=response.model,
                provider=response.provider,
                instance_id=response.instance_id,
                usage=response.usage,
                raw_response=response.content,
            )
            return response
        except BaseException as exc:
            record["error_class"] = type(exc).__name__
            # Exception strings/response bodies may expose endpoint credentials.
            # Keep only causal class names and numeric HTTP status for diagnosis.
            chain: list[dict[str, Any]] = []
            cause: BaseException | None = exc
            seen: set[int] = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                item: dict[str, Any] = {"class": type(cause).__name__}
                status = getattr(cause, "status_code", None)
                if isinstance(status, int):
                    item["http_status"] = status
                if type(cause).__name__ == "LLMResponseError":
                    message = str(cause)
                    if "returned reasoning but no final content" in message:
                        item["response_problem"] = "reasoning_without_final_content"
                    elif "returned empty content" in message:
                        item["response_problem"] = "empty_content"
                    for reason in ("length", "stop", "content_filter", "tool_calls", "unknown"):
                        if f"finish_reason={reason})" in message:
                            item["finish_reason"] = reason
                chain.append(item)
                cause = cause.__cause__ or cause.__context__
            record["error_chain"] = chain
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - start, 3)


def _save(path: Path, report: dict[str, Any]) -> None:
    # Generated evaluation artifact; path is exclusive to this invocation.
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.real:
        raise ValueError("--real is required for billable external model requests")
    if not 1 <= args.repeats <= 2 or not 0 < args.budget_seconds <= 60:
        raise ValueError("bounded eval requires 1..2 repeats and <=60 seconds per call")
    if args.output.exists():
        raise ValueError("output exists; use a new artifact path")
    conditions = args.conditions.split(",")
    if "legacy_prompt" in conditions:
        raise ValueError(
            "legacy_prompt is incompatible with the current completion contract; "
            "use a frozen prior report or a historical checkout for the legacy policy"
        )
    if conditions != ["screen_policy"]:
        raise ValueError("current single-arm evaluation only accepts --conditions screen_policy")
    transport = getattr(args, "transport", "configured")
    effort = getattr(args, "codex_reasoning_effort", "low")
    if effort not in {"low", "medium"}:
        raise ValueError("CLI evaluation reasoning effort must be low or medium")
    if transport != "codex-cli" and effort != "low":
        raise ValueError("--codex-reasoning-effort only applies to codex-cli transport")
    cli_service = None
    if transport == "codex-cli":
        from director_codex_transport import CodexCLIService

        if args.instance:
            raise ValueError("--instance is only used with configured transport")
        cli_service = CodexCLIService(
            binary=getattr(args, "codex_binary", "codex"), reasoning_effort=effort
        )
        route_metadata = {
            "instance": "temporary-codex-cli",
            "model": cli_service.model,
            "reasoning_effort": cli_service.reasoning_effort,
            "fallback_enabled": False,
            "model_verification": "explicit_cli_flag_not_server_echo",
            "cli_version": subprocess.check_output(
                [cli_service.binary, "--version"], text=True
            ).strip(),
            "cli_binary": cli_service.binary,
        }
        production_budget = None  # No real application configuration is read.
    else:
        if args.config_root is None:
            raise ValueError("--config-root is required for configured transport")
        os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(args.config_root.resolve())
        config = copy.deepcopy(load_config())
        # Local memory-only route pin: changing models would confound repeated cases.
        overrides = module_overrides_from_config(config)
        recommendation = overrides.get("recommendation")
        route = (
            list(recommendation.chain)
            if recommendation is not None and recommendation.custom_chain
            else list(config.llm.default_chain)
        )
        if not route:
            route = [config.llm.default_provider]
        selected = args.instance or route[0]
        if selected not in route:
            raise ValueError("test instance must already belong to the recommendation route")
        if selected not in config.llm.instances or not config.llm.instances[selected].enabled:
            raise ValueError("configured primary recommendation instance is unavailable")
        config.llm.instances = {selected: config.llm.instances[selected]}
        config.llm.default_chain = [selected]
        config.llm.default_provider = selected
        config.llm.fallback_provider = ""
        registry = build_llm_registry(config)
        route_metadata = {
            "instance": selected,
            "model": config.llm.instances[selected].model,
            "reasoning_effort": config.llm.instances[selected].reasoning_effort,
            "fallback_enabled": False,
        }
        production_budget = config.director.planning_budget_seconds
    cases = [case for case in build_cases() if not args.case or case.case_id == args.case]
    if not cases:
        raise ValueError("unknown fixture case")
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(
            (PROJECT_ROOT / "scripts/director_effect_cases.py").read_bytes()
        ).hexdigest(),
        "synthetic_content_and_profile": True,
        "live_model": True,
        "online_user_satisfaction_test": False,
        "jev_tested": False,
        "wire_attempts_instrumented": False,
        "cost_totals_complete": False,
        "transport": transport,
        "route": route_metadata,
        "max_tokens_and_temperature_enforced": transport != "codex-cli",
        "cli_latency_includes_startup": transport == "codex-cli",
        "planning_budget_seconds": args.budget_seconds,
        "production_planning_budget_seconds": production_budget,
        "evaluation_design": "current_policy_single_arm",
        "policy_version": DIRECTOR_POLICY_VERSION,
        "comparison": "Current policy with its strict completion contract only. Compare frozen v1 reports separately; this is not a concurrent A/B. The deterministic pair baseline is NOT production MMR.",
        "repeats": args.repeats,
        "horizon_screens": args.horizon,
        "selection_metric_note": (
            "Per-screen valid_selection includes matching target_count; a legally approved "
            "short tail may be false. plan_valid and planning_completion report the actual "
            "production contract. Semantic quality is not certified."
        ),
        "cases": [
            {
                "case_id": c.case_id,
                "title": c.title,
                "input": c.request.model_dump(mode="json"),
                "preregistered_labels": c.labels,
            }
            for c in cases
        ],
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _save(args.output, report)
    with tempfile.TemporaryDirectory(prefix="director-effect-eval-") as temp:
        service = cli_service or LLMService(
            registry=registry,
            memory=MemoryManager(Path(temp)),
            module_overrides={
                "recommendation": ModuleOverride(chain=(selected,), custom_chain=True)
            },
            concurrency=1,
        )
        for case in cases:
            baseline = await PairComposer().prepare_next(case.request)
            report["results"].append(
                {
                    "case_id": case.case_id,
                    "condition": "deterministic_pair_baseline",
                    "screen_refs": [list(baseline.card_refs)],
                    "first_screen_metrics": score_selection(case, baseline.card_refs),
                    "network_calls": 0,
                }
            )
            for repeat in range(args.repeats):
                for condition in conditions:
                    print(
                        json.dumps(
                            {
                                "event": "starting",
                                "case": case.case_id,
                                "condition": condition,
                                "repeat": repeat,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    recorder = RecordingLLM(service)
                    cli_record_start = len(cli_service.records) if cli_service is not None else 0
                    started = time.monotonic()
                    planning = await HorizonPlanner(recorder).plan(
                        case.request,
                        horizon_screens=args.horizon,
                        budget_seconds=args.budget_seconds,
                    )
                    entry: dict[str, Any] = {
                        "case_id": case.case_id,
                        "condition": condition,
                        "repeat": repeat,
                        "reason": planning.reason_code,
                        "calls": recorder.calls,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "plan_valid": planning.plan is not None,
                    }
                    if cli_service is not None:
                        attempts = copy.deepcopy(cli_service.records[cli_record_start:])
                        entry["cli_transport_attempts"] = attempts
                        # Legacy field names the last attempt only. Retain every
                        # validation-correction call separately, never hide cost.
                        if attempts:
                            entry["cli_transport"] = attempts[-1]
                    if planning.plan is not None:
                        screens = planning.plan.screens
                        refs = tuple(ref for screen in screens for ref in screen.card_refs)
                        entry.update(
                            planning_completion=planning.plan.planning_completion.model_dump(
                                mode="json"
                            )
                            if planning.plan.planning_completion is not None
                            else None,
                            screens=[screen.model_dump(mode="json") for screen in screens],
                            first_screen_metrics=score_selection(case, screens[0].card_refs),
                            each_screen_metrics=[
                                score_selection(case, screen.card_refs) for screen in screens
                            ],
                            all_screens_metrics=score_selection(
                                case, refs, expected_count=len(refs)
                            ),
                        )
                    report["results"].append(entry)
                    _save(args.output, report)
                    print(
                        json.dumps(
                            {
                                k: v
                                for k, v in entry.items()
                                if k
                                not in {
                                    "calls",
                                    "screens",
                                    "each_screen_metrics",
                                    "all_screens_metrics",
                                }
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if planning.reason_code in {
                        "planner_unavailable",
                        "planner_timeout",
                        "late_planner_result",
                    }:
                        # Do not let provider cooldown contaminate later cases
                        # or hammer a failing endpoint.
                        report["stopped_early"] = {
                            "reason": planning.reason_code,
                            "case_id": case.case_id,
                            "condition": condition,
                            "remaining_evaluations_not_run": True,
                        }
                        report["finished_at"] = datetime.now(UTC).isoformat()
                        _save(args.output, report)
                        return report
    # No real user database was opened; client sockets are process-local and
    # reclaimed on CLI exit. No provider probing or implicit retry loop here.
    report["finished_at"] = datetime.now(UTC).isoformat()
    _save(args.output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--transport", choices=("configured", "codex-cli"), default="configured")
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--codex-reasoning-effort", choices=("low", "medium"), default="low")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", default="")
    parser.add_argument("--instance", default="")
    parser.add_argument("--conditions", default="screen_policy")
    parser.add_argument(
        "--legacy-ref", help="Deprecated and ignored; use frozen historical reports"
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--horizon", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--budget-seconds", type=float, default=20.0)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
