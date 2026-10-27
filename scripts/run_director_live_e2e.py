"""Opt-in real-HTTP director acceptance against an isolated live-data snapshot.

Only the recommendation director uses the temporary Codex CLI transport. The
source SQLite database is opened read-only for a consistent online backup. All
API writes, profile ownership fences and injected test feedback stay in a private
temporary directory. Reports contain counts, fixed verdicts and hashes, never
profile text, candidates, responses, auth material or exception messages.

This exercises production HTTP handlers / SQLite / SoulEngine / recommendation
runtime, not discovery, permanent profile learning, Jev or the app's background
startup recovery. Uvicorn lifespan is deliberately off; external-worker flags
prevent background feedback consumption and recommendation proxying.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import httpx
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from director_codex_transport import CodexCLIService  # noqa: E402

from openbiliclaw.api.app import create_app  # noqa: E402
from openbiliclaw.config import (  # noqa: E402
    Config,
    load_config,
    publication_date_preference_for_source,
    source_date_preferences,
)
from openbiliclaw.llm.base import LLMRegistry  # noqa: E402
from openbiliclaw.memory.manager import MemoryManager  # noqa: E402
from openbiliclaw.recommendation.curator import PoolCurator  # noqa: E402
from openbiliclaw.recommendation.director.policy import DIRECTOR_POLICY_VERSION  # noqa: E402
from openbiliclaw.recommendation.engine import RecommendationEngine  # noqa: E402
from openbiliclaw.soul.engine import SoulEngine  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402
from openbiliclaw.storage.migration import _snapshot_sqlite  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import FastAPI

    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.profile import SoulProfile

_MEMORY_FILES = (
    "soul.json",
    "preference.json",
    "awareness.json",
    "insight.json",
    "profile_overrides.json",
    "speculative_state.json",
    "discovery_runtime.json",
)
_TEST_FEEDBACK = "本次隔离验收：延续当前主题，避免已出现的内容，优先不同角度的具体案例。"


class AcceptanceFailure(RuntimeError):  # noqa: N818 - fixed acceptance verdict, not provider error
    """A fixed public acceptance code, never content-bearing exception text."""


def _require(condition: object, code: str) -> None:
    if not condition:
        raise AcceptanceFailure(code)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def response_shape(content: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Count structural defects without copying any model-supplied string."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return {"json_parse_valid": False}
    result: dict[str, Any] = {"json_parse_valid": True, "json_object": isinstance(data, dict)}
    if not isinstance(data, dict):
        return result
    screens = data.get("screens", [])
    screens = screens if isinstance(screens, list) else []
    selected: list[str] = []
    lengths: list[int] = []
    for screen in screens:
        refs: list[str] = []
        pairs = screen.get("pairs", []) if isinstance(screen, dict) else []
        for pair in pairs if isinstance(pairs, list) else []:
            values = pair.get("card_refs", []) if isinstance(pair, dict) else []
            if isinstance(values, list):
                refs.extend(ref for ref in values if isinstance(ref, str))
        lengths.append(len(refs))
        selected.extend(refs)
    completion = data.get("completion", {})
    omissions = completion.get("omissions", []) if isinstance(completion, dict) else []
    omitted: list[str] = []
    omission_reasons: Counter[str] = Counter()
    known_reasons = {"off_intent", "redundant", "style_mismatch", "insufficient_evidence"}
    for group in omissions if isinstance(omissions, list) else []:
        values = group.get("refs", []) if isinstance(group, dict) else []
        if isinstance(values, list):
            omitted.extend(ref for ref in values if isinstance(ref, str))
            reason = group.get("reason") if isinstance(group, dict) else None
            safe_reason = (
                reason if isinstance(reason, str) and reason in known_reasons else "invalid"
            )
            omission_reasons[safe_reason] += sum(isinstance(ref, str) for ref in values)
    allowed = {card.get("ref") for card in candidates}
    result.update(
        screen_card_counts=lengths,
        unknown_ref_count=sum(ref not in allowed for ref in selected + omitted),
        duplicate_ref_count=len(selected) - len(set(selected)),
        selected_omitted_overlap_count=len(set(selected) & set(omitted)),
        omitted_count=len(omitted),
        omission_reason_ref_counts=dict(omission_reasons),
    )
    return result


class SafeRecordingService:
    """Observe the actual prompt/response boundary without retaining its text."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.records: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        payload = json.loads(kwargs["user_input"])
        context = payload.get("session_context", {})
        profile = payload.get("profile_context", {})
        stable = profile.get("stable", {})
        feedback = context.get("real_feedback", [])
        recent = context.get("recent_screens", [])
        record = {
            "input_sha256": _hash(payload),
            "system_sha256": _hash(kwargs["system_instruction"]),
            "input_bytes": len(kwargs["user_input"].encode()),
            "candidate_count": len(payload.get("untrusted_candidates", [])),
            "profile_present": bool(profile),
            "profile_interest_count": len(stable.get("interests", [])),
            "profile_dislike_count": len(stable.get("disliked_topics", [])),
            "recent_screen_count": len(recent),
            "recent_card_count": sum(len(screen.get("cards", [])) for screen in recent),
            "feedback_count": len(feedback),
            "feedback_kind_counts": dict(Counter(event.get("kind", "") for event in feedback)),
            "requested_horizon": payload.get("horizon_screens"),
            "background": str(kwargs.get("caller", "")).endswith(".preplan"),
        }
        self.records.append(record)
        start = time.monotonic()
        try:
            response: LLMResponse = await self.service.complete_structured_task(**kwargs)
            record.update(response_sha256=_hash(response.content), usage=response.usage)
            record["response_shape"] = response_shape(
                response.content, payload.get("untrusted_candidates", [])
            )
            return response
        except BaseException as exc:
            record["error_class"] = type(exc).__name__
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - start, 3)


def snapshot_data(source: Path, destination: Path) -> dict[str, Any]:
    """Copy a consistent DB and an allowlist of non-auth memory files."""
    source = source.resolve()
    destination = destination.resolve()
    _require(
        source != destination
        and source not in destination.parents
        and destination not in source.parents,
        "unsafe_snapshot_target",
    )
    _require(not destination.exists(), "snapshot_target_exists")
    _require((source / "openbiliclaw.db").is_file(), "source_database_missing")
    _require((source / "memory" / "soul.json").is_file(), "source_profile_missing")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    _snapshot_sqlite(source / "openbiliclaw.db", destination / "openbiliclaw.db")
    (destination / "memory").mkdir(mode=0o700, exist_ok=True)
    copied: list[str] = []
    for name in _MEMORY_FILES:
        path = source / "memory" / name
        if path.is_file():
            _require(source in path.resolve().parents, "memory_symlink_outside_source")
            shutil.copyfile(path, destination / "memory" / name)
            copied.append(name)
    return {
        "source_open_mode": "sqlite_readonly_online_backup",
        "source_data_path_sha256": _hash(str(source)),
        "memory_files": copied,
        "database_bytes": (destination / "openbiliclaw.db").stat().st_size,
        "private_snapshot_deleted_on_exit": True,
    }


def isolated_config(source: Config, destination: Path, budget_seconds: float) -> Config:
    """Preserve serving policy, but never carry provider credentials into config."""
    config = Config(data_dir=str(destination))
    config.sources = copy.deepcopy(source.sources)
    config.network = copy.deepcopy(source.network)
    config.scheduler = copy.deepcopy(source.scheduler)
    config.discovery = copy.deepcopy(source.discovery)
    config.soul = copy.deepcopy(source.soul)
    config.director = copy.deepcopy(source.director)
    config.director.mode = "enforce"
    config.director.strategy = "agent_only"
    config.director.planning_budget_seconds = budget_seconds
    config.director.planning_horizon_screens = 3
    config.discovery.semantic_filter.mode = "off"
    config.api.auth.enabled = False
    return config


class Application:
    """One actual application instance rooted wholly inside the private copy."""

    def __init__(self, config: Config, service: SafeRecordingService) -> None:
        self.database = Database(config.data_path / "openbiliclaw.db")
        self.memory = MemoryManager(config.data_path, database=self.database)
        self.memory.initialize()
        # Profile reads and owner fencing are real. Long-term learning is out
        # of this acceptance scope and an empty registry cannot call a model.
        self.soul = SoulEngine(
            LLMRegistry(),
            self.memory,
            database=self.database,
            profile_consolidation_enabled=False,
            llm_concurrency_gate=getattr(service, "concurrency_gate", None),
        )
        preference = publication_date_preference_for_source(config.sources.bilibili)
        self.database.set_publication_date_preference(preference)
        self.database.set_source_publication_date_preferences(source_date_preferences(config))

        def self_info() -> dict[str, object] | None:
            info = self.memory.load_discovery_runtime_state().get("xhs_self_info")
            return info if isinstance(info, dict) else None

        self.engine = RecommendationEngine(
            service,
            self.database,
            curator=PoolCurator(self.database, publication_preference=preference),
            xhs_self_info_provider=self_info,
        )
        self.app = create_app(
            database=self.database,
            memory_manager=self.memory,
            soul_engine=self.soul,
            recommendation_engine=self.engine,
        )
        self.runtime = self.app.state.runtime_context.director_runtime
        _require(self.runtime is not None, "director_runtime_missing")

    async def close(self) -> None:
        await self.runtime.aclose()
        # We do not run ASGI lifespan; explicitly close allocated task owners.
        try:
            await self.app.state.chat_reply_scheduler.close()
            await self.app.state.feedback_batch_scheduler.close()
            await self.app.state.runtime_context.task_registry.cancel_all()
        finally:
            self.database.close()


@asynccontextmanager
async def serve_http(app: FastAPI, port: int = 0) -> AsyncIterator[str]:
    """Real loopback TCP HTTP, with no ASGITransport or handler replacement."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", log_level="critical", access_log=False, lifespan="off"
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise AcceptanceFailure("http_server_start_failed")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            sock.close()


@asynccontextmanager
async def private_workspace(cleanup: Callable[[], Awaitable[None]]) -> AsyncIterator[Path]:
    """Stop model/process/database owners before removing private source data."""
    with tempfile.TemporaryDirectory(prefix="director-live-e2e-") as temp:
        try:
            yield Path(temp)
        finally:
            await cleanup()


def _batch_summary(application: Application, stream: str) -> dict[str, Any]:
    batch = application.runtime.store.restore(stream)
    _require(batch is not None, "committed_batch_missing")
    trace = json.loads(batch.trace_json)
    planning = trace.get("planning", {})
    completion = planning.get("completion") or {}
    return {
        "batch_sha256": _hash(batch.batch_ref),
        "plan_sha256": _hash(batch.plan_id) if batch.plan_id else None,
        "item_count": len(batch.items),
        "item_sequence_sha256": _hash([row["item_key"] for row in batch.items]),
        "requested_strategy": batch.requested_strategy,
        "realized_strategy": batch.realized_strategy,
        "execution_binding": batch.execution_binding,
        "experience_step_seq": batch.experience_step_seq,
        "state_revision": batch.state_revision,
        "planning_source": planning.get("source"),
        "planning_reason": planning.get("reason_code"),
        "planning_calls": planning.get("calls"),
        "initial_screen_counts": completion.get("initial_screen_counts"),
        "coverage_reason": completion.get("reason"),
    }


def _foreground_calls(service: SafeRecordingService) -> int:
    return sum(not record["background"] for record in service.records)


def _verify_delivery(application: Application, stream: str, body: dict[str, Any]) -> set[str]:
    """Validate what HTTP actually delivered against the immutable committed order."""
    batch = application.runtime.store.restore(stream)
    _require(batch is not None, "committed_batch_missing")
    items = body.get("items")
    director = body.get("director")
    _require(
        isinstance(items, list)
        and bool(items)
        and all(isinstance(row, dict) for row in items)
        and isinstance(director, dict)
        and director.get("batch_ref") == batch.batch_ref
        and director.get("stream_ref") == stream,
        "http_delivery_differs_from_committed_batch",
    )
    items = cast("list[dict[str, Any]]", items)
    keys = [row.get("item_key") for row in items]
    _require(
        keys == [row["item_key"] for row in batch.items]
        and all(row.get("batch_ref") == batch.batch_ref for row in items)
        and all(
            delivered.get("id") == committed.get("recommendation_id", committed.get("id", 0))
            for delivered, committed in zip(items, batch.items, strict=True)
        ),
        "http_delivery_differs_from_committed_batch",
    )
    return set(cast("list[str]", keys))


def _future_plan_checkpoint(application: Application, stream: str) -> tuple[str, int]:
    """A missing plan or a consumed tombstone cannot establish persistence."""
    stored = application.runtime.store.load_plan(stream)
    _require(stored is not None and bool(stored.plan), "future_plan_missing")
    screens = stored.plan.get("screens")
    _require(isinstance(screens, list) and bool(screens), "future_plan_missing")
    return _hash(stored.model_dump(mode="json")), len(screens)


async def exercise(
    application: Application,
    service: SafeRecordingService,
    report: dict[str, Any],
    *,
    base_url: str,
    stream: str,
    limit: int,
    budget_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any], frozenset[str]]:
    """Three NEXT screens, genuine durable ACK/click/comment ingress and replay."""
    seen: set[str] = set()
    checks = report["checks"]
    screens = report["screens"]
    async with httpx.AsyncClient(
        base_url=base_url, trust_env=False, timeout=budget_seconds + 40
    ) as client:

        async def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            result = await client.request(method, path, **kwargs)
            report["http_requests"].append(
                {"method": method, "path": path.split("?")[0], "status": result.status_code}
            )
            _require(result.status_code == 200, "http_status_not_200")
            body = result.json()
            _require(isinstance(body, dict), "http_response_not_object")
            return dict(body)

        async def generate(index: int) -> tuple[dict[str, Any], dict[str, Any]]:
            payload = {"stream_ref": stream, "request_id": f"screen-{index}", "limit": limit}
            body = await request("POST", "/api/recommendations/director/generate", json=payload)
            summary = _batch_summary(application, stream)
            screens.append(summary)
            _require(summary["realized_strategy"] == "agent_only", "screen_not_agent_only")
            _require(summary["item_count"] > 0, "empty_generated_screen")
            keys = _verify_delivery(application, stream, body)
            _require(not (keys & seen), "duplicate_across_screens")
            _require(len(keys) == len(body["items"]), "duplicate_within_screen")
            seen.update(keys)
            return body, payload

        empty = await request("GET", f"/api/recommendations/director/restore?stream_ref={stream}")
        _require(empty["items"] == [], "new_stream_not_empty")
        checks.append("new_stream_restore_does_not_generate")
        first, first_payload = await generate(1)
        _require(service.records and service.records[0]["profile_present"], "profile_not_in_prompt")
        calls = _foreground_calls(service)
        restored = await request(
            "GET", f"/api/recommendations/director/restore?stream_ref={stream}"
        )
        replay = await request("POST", "/api/recommendations/director/generate", json=first_payload)
        _verify_delivery(application, stream, restored)
        _verify_delivery(application, stream, replay)
        _require(first["items"] == restored["items"] == replay["items"], "replay_order_changed")
        _require(_foreground_calls(service) == calls, "restore_or_replay_called_model")
        checks.append("exact_restore_and_idempotent_generate_without_foreground_model")
        batch_ref = first["director"]["batch_ref"]
        visible = [row["item_key"] for row in first["items"][:2]]
        ack = {"batch_ref": batch_ref, "ack_id": "visible-1", "item_keys": visible}
        receipt = await request("POST", "/api/recommendations/director/present", json=ack)
        duplicate = await request("POST", "/api/recommendations/director/present", json=ack)
        _require(receipt == duplicate and receipt["item_keys"] == visible, "ack_not_idempotent")
        session = application.runtime.store.session(stream)
        _require(len(session.recent_screens) == 1, "actual_presentation_missing")
        _require(len(session.recent_screens[0].cards) == len(visible), "unseen_cards_in_context")
        checks.append("actual_visible_subset_ack_is_idempotent")
        try:
            _, future_count = _future_plan_checkpoint(application, stream)
        except AcceptanceFailure as exc:
            if str(exc) != "future_plan_missing":
                raise
            raise AcceptanceFailure("first_plan_has_no_future_screen") from None
        report["future_screens_before_weak_click"] = future_count
        row = first["items"][0]
        click = {
            "recommendation_id": row["id"],
            "batch_ref": batch_ref,
            "bvid": row["bvid"],
            "request_id": stream + "-click-1",
        }
        await request("POST", "/api/recommendation-click", json=click)
        duplicate_click = await request("POST", "/api/recommendation-click", json=click)
        _require(duplicate_click["duplicate"], "click_not_idempotent")
        second, _ = await generate(2)
        _require(screens[-1]["planning_source"] == "cached", "weak_click_discarded_future_plan")
        checks.append("ack_and_weak_click_keep_cached_future_screen")
        row = second["items"][0]
        feedback = {
            "recommendation_id": row["id"],
            "batch_ref": second["director"]["batch_ref"],
            "feedback_type": "comment",
            "note": _TEST_FEEDBACK,
            "request_id": stream + "-explicit-feedback-1",
        }
        await request("POST", "/api/feedback", json=feedback)
        duplicate_feedback = await request("POST", "/api/feedback", json=feedback)
        _require(duplicate_feedback["duplicate"], "feedback_not_idempotent")
        calls = len(service.records)
        third, third_payload = await generate(3)
        _require(screens[-1]["planning_source"] == "fresh", "explicit_feedback_did_not_replan")
        new_records = [record for record in service.records[calls:] if not record["background"]]
        _require(bool(new_records), "replan_did_not_call_model")
        _require(
            any(
                record["profile_present"]
                and record["recent_screen_count"] >= 2
                and record["feedback_kind_counts"].get("explicit_request", 0) > 0
                and record["feedback_kind_counts"].get("presented", 0) > 0
                and record["feedback_kind_counts"].get("click", 0) > 0
                for record in new_records
            ),
            "profile_or_short_term_context_missing_from_replan",
        )
        checks.append("explicit_feedback_replans_with_profile_and_actual_history")
        checks.append("three_next_screens_have_no_duplicate_content")
        return third, third_payload, frozenset(seen)


async def verify_restart_and_stay(
    application: Application,
    service: SafeRecordingService,
    report: dict[str, Any],
    *,
    base_url: str,
    previous: dict[str, Any],
    previous_payload: dict[str, Any],
    prior_item_keys: frozenset[str],
    budget_seconds: float,
) -> None:
    stream = previous_payload["stream_ref"]
    async with httpx.AsyncClient(
        base_url=base_url, trust_env=False, timeout=budget_seconds + 40
    ) as client:
        seen = set(prior_item_keys)
        calls = _foreground_calls(service)
        restored = await client.get(
            "/api/recommendations/director/restore", params={"stream_ref": stream}
        )
        replay = await client.post("/api/recommendations/director/generate", json=previous_payload)
        report["http_requests"].extend(
            [
                {
                    "method": "GET",
                    "path": "/api/recommendations/director/restore",
                    "status": restored.status_code,
                },
                {
                    "method": "POST",
                    "path": "/api/recommendations/director/generate",
                    "status": replay.status_code,
                },
            ]
        )
        _require(restored.status_code == replay.status_code == 200, "restart_http_failed")
        _verify_delivery(application, stream, restored.json())
        _verify_delivery(application, stream, replay.json())
        _require(
            restored.json()["items"] == replay.json()["items"] == previous["items"],
            "restart_lost_exact_committed_screen",
        )
        _require(_foreground_calls(service) == calls, "restart_restore_or_replay_called_model")
        report["checks"].append(
            "runtime_reconstruction_restores_exact_screen_without_foreground_model"
        )
        prior_batch = application.runtime.store.restore(stream)
        resumed = await client.post(
            "/api/recommendations/director/generate",
            json={**previous_payload, "request_id": "screen-4"},
        )
        report["http_requests"].append(
            {
                "method": "POST",
                "path": "/api/recommendations/director/generate",
                "status": resumed.status_code,
            }
        )
        _require(resumed.status_code == 200, "cached_next_http_failed")
        summary = _batch_summary(application, stream)
        report["screens"].append(summary)
        keys = _verify_delivery(application, stream, resumed.json())
        _require(summary["realized_strategy"] == "agent_only", "cached_next_not_agent_only")
        _require(
            summary["planning_source"] == "cached"
            and summary["planning_calls"] == 0
            and _foreground_calls(service) == calls,
            "reopened_future_plan_not_executed_from_cache",
        )
        _require(
            summary["experience_step_seq"] == prior_batch.experience_step_seq + 1,
            "cached_next_did_not_advance_chapter",
        )
        _require(not keys & seen, "duplicate_across_screens")
        seen.update(keys)
        report["checks"].append(
            "reopened_future_plan_executes_cached_next_without_foreground_model"
        )
        before = application.runtime.store.restore(stream)
        result = await client.post(
            "/api/recommendations/director/generate",
            json={**previous_payload, "request_id": "stay-1", "intent": "STAY"},
        )
        report["http_requests"].append(
            {
                "method": "POST",
                "path": "/api/recommendations/director/generate",
                "status": result.status_code,
            }
        )
        _require(result.status_code == 200, "stay_http_failed")
        keys = _verify_delivery(application, stream, result.json())
        _require(not keys & seen, "stay_repeated_previously_delivered_content")
        summary = _batch_summary(application, stream)
        report["screens"].append(summary)
        after = application.runtime.store.restore(stream)
        _require(summary["realized_strategy"] == "agent_only", "stay_not_agent_only")
        _require(
            after.experience_step_seq == before.experience_step_seq
            and after.parent_batch_ref == before.batch_ref,
            "stay_advanced_chapter_or_lost_parent",
        )
        _require(summary["item_count"] > 0, "stay_screen_empty")
        report["checks"].append("stay_keeps_experience_step_and_records_parent")
        report["checks"].append("stay_has_no_content_from_any_delivered_screen")


def _save(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace, *, service: Any | None = None) -> dict[str, Any]:
    """Explicit opt-in, bounded live requests; optional injected service is test-only."""
    _require(args.real, "real_opt_in_required")
    _require(0 < args.budget_seconds <= 60, "budget_out_of_bounds")
    _require(1 <= args.limit <= 20, "limit_out_of_bounds")
    _require(0 <= args.port <= 65535, "port_out_of_bounds")
    _require(1 <= args.serve_seconds <= 1800, "serve_duration_out_of_bounds")
    _require(not args.output.exists(), "output_already_exists")
    report: dict[str, Any] = {
        "schema": "director-live-e2e.v1",
        "policy_version": DIRECTOR_POLICY_VERSION,
        "code_sha256": {
            name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/run_director_live_e2e.py",
                "scripts/director_codex_transport.py",
                "src/openbiliclaw/recommendation/director/planner.py",
                "src/openbiliclaw/recommendation/director/policy.py",
                "src/openbiliclaw/recommendation/director/horizon_models.py",
                "src/openbiliclaw/recommendation/director/planner_refs.py",
            )
            if (PROJECT_ROOT / name).is_file()
        },
        "started_at": datetime.now(UTC).isoformat(),
        "transport": "codex-cli" if service is None else "injected_test_service",
        "model": "gpt-6-luna",
        "mode": "interactive_serve" if args.serve else "scripted_http_acceptance",
        "planning_budget_seconds": args.budget_seconds,
        "budget_class": "production_default"
        if args.budget_seconds == 20
        else "diagnostic_override",
        "real_source_snapshot": True,
        "source_configuration": "config_toml_plus_local_and_environment",
        "feedback_is_isolated_acceptance_action": True,
        "long_term_learning_and_discovery": "excluded",
        "jev": "not_used_agent_only",
        "asgi_background_lifespan": "disabled",
        "http_requests": [],
        "screens": [],
        "checks": [],
        "passed": False,
    }
    transport = service or CodexCLIService(binary=args.codex_binary)
    recording = SafeRecordingService(transport)
    report["model_calls"] = recording.records
    if isinstance(transport, CodexCLIService):
        report["transport_records"] = transport.records
    application: Application | None = None

    async def close_application() -> None:
        nonlocal application
        if application is not None:
            current, application = application, None
            await current.close()

    try:
        _require((args.source_root / "config.toml").is_file(), "source_config_missing")
        with patch.dict(os.environ, {"OPENBILICLAW_PROJECT_ROOT": str(args.source_root.resolve())}):
            # load_config delegates with ensure_default_file=False; its public
            # signature only accepts config_path and consult_environment.
            source_config = load_config()
            source_config.data_dir = str(source_config.data_path)
        async with private_workspace(close_application) as root:
            report["snapshot"] = snapshot_data(source_config.data_path, root / "data")
            config = isolated_config(source_config, root / "data", args.budget_seconds)
            env = {
                "OPENBILICLAW_PROJECT_ROOT": str(root),
                "OPENBILICLAW_FULL_WORKER": "1",
                "OPENBILICLAW_RECOMMENDATION_ONLY": "1",
            }
            with (
                patch.dict(os.environ, env),
                patch("openbiliclaw.config.load_config", return_value=config),
            ):
                application = Application(config, recording)
                profile = await application.soul.get_profile()
                candidates = await application.engine.load_director_candidates(
                    cast("SoulProfile", profile)
                )
                report["initial_shortlist_count"] = len(candidates.items)
                _require(len(candidates.items) >= args.limit * 3, "insufficient_live_inventory")
                async with serve_http(application.app, args.port) as base_url:
                    if args.serve:
                        report["listening_url"] = base_url
                        report["passed"] = None
                        _save(args.output, report)
                        print(
                            json.dumps(
                                {"listening_url": base_url, "serve_seconds": args.serve_seconds}
                            ),
                            flush=True,
                        )
                        await asyncio.sleep(args.serve_seconds)
                        report["checks"].append(
                            "interactive_server_window_completed_not_an_acceptance_verdict"
                        )
                    else:
                        previous, previous_payload, prior_item_keys = await exercise(
                            application,
                            recording,
                            report,
                            base_url=base_url,
                            stream="e2e-" + uuid.uuid4().hex,
                            limit=args.limit,
                            budget_seconds=args.budget_seconds,
                        )
                if not args.serve:
                    before, future_count = _future_plan_checkpoint(
                        application, previous_payload["stream_ref"]
                    )
                    report["future_screens_before_restart"] = future_count
                    await close_application()
                    application = Application(config, recording)
                    after, restored_future_count = _future_plan_checkpoint(
                        application, previous_payload["stream_ref"]
                    )
                    report["future_screens_after_restart"] = restored_future_count
                    _require(before == after, "runtime_reconstruction_lost_future_plan")
                    report["checks"].append("future_plan_survives_sqlite_reopen")
                    async with serve_http(application.app, args.port) as base_url:
                        await verify_restart_and_stay(
                            application,
                            recording,
                            report,
                            base_url=base_url,
                            previous=previous,
                            previous_payload=previous_payload,
                            prior_item_keys=prior_item_keys,
                            budget_seconds=args.budget_seconds,
                        )
                    report["passed"] = True
                await close_application()
    except BaseException as exc:
        report["error_class"] = type(exc).__name__
        if isinstance(exc, AcceptanceFailure):
            report["failure_code"] = str(exc)
        report["passed"] = False
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        _save(args.output, report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--real", action="store_true")
    result.add_argument("--source-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--codex-binary", default="codex")
    result.add_argument("--budget-seconds", type=float, default=20)
    result.add_argument("--limit", type=int, default=6)
    result.add_argument("--serve", action="store_true")
    result.add_argument("--port", type=int, default=0)
    result.add_argument("--serve-seconds", type=int, default=600)
    return result


def main() -> None:
    # A live-data test must not print library logs containing real titles,
    # profile hypotheses, URLs or exception bodies. Safe report only.
    logging.disable(logging.CRITICAL)
    args = parser().parse_args()
    try:
        report = asyncio.run(run(args))
    except AcceptanceFailure as exc:
        print(json.dumps({"passed": False, "failure_code": str(exc)}))
        raise SystemExit(2) from None
    except Exception as exc:
        print(json.dumps({"passed": False, "error_class": type(exc).__name__}))
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "failure_code": report.get("failure_code"),
                "model_calls": len(report["model_calls"]),
            }
        )
    )
    raise SystemExit(0 if report["passed"] is not False else 1)


if __name__ == "__main__":
    main()
