"""Real loopback HTTP acceptance harness tests; external model seam is local."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openbiliclaw.config import Config
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.profile import OnionProfile
from tests.test_director_runtime import FakeAgent, seed_database


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("scripts.run_director_live_e2e")


def args(tmp_path, **changes):
    return argparse.Namespace(
        **{
            "real": True,
            "source_root": tmp_path / "source",
            "output": tmp_path / "report.json",
            "codex_binary": "NEVER_EXECUTED",
            "budget_seconds": 5,
            "limit": 4,
            "serve": False,
            "port": 0,
            "serve_seconds": 1,
            **changes,
        }
    )


def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    data = root / "data"
    data.mkdir()
    (root / "config.toml").write_text(f"data_dir = {json.dumps(str(data))}\n")
    database = seed_database(data / "openbiliclaw.db", count=60)
    memory = MemoryManager(data, database=database)
    # seed_database already initialized this Database; initializing it again
    # would leave the seed connection pending GC / a late WAL checkpoint.
    memory.get_layer("soul").data.update(OnionProfile().to_dict())
    memory.get_layer("soul").save()
    database.close()
    return Config(data_dir=str(data)), data


async def test_live_opt_in_checked_before_source_or_external_service(runner, tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not load source")

    monkeypatch.setattr(runner, "load_config", forbidden)
    with pytest.raises(runner.AcceptanceFailure, match="real_opt_in_required"):
        await runner.run(args(tmp_path, real=False))
    assert not (tmp_path / "report.json").exists()


async def test_production_http_loop_reopens_sqlite_without_mutating_source(
    runner, tmp_path, monkeypatch
):
    _, data = source(tmp_path)
    monkeypatch.delenv("OPENBILICLAW_DATA_DIR", raising=False)
    before = hashlib.sha256((data / "openbiliclaw.db").read_bytes()).hexdigest()
    agent = FakeAgent()
    report = await runner.run(args(tmp_path), service=agent)
    assert report["passed"], report
    assert report["transport"] == "injected_test_service"
    assert len(report["screens"]) == 5
    assert all(s["realized_strategy"] == "agent_only" for s in report["screens"])
    assert report["screens"][1]["planning_source"] == "cached"
    assert report["screens"][2]["planning_source"] == "fresh"
    assert report["screens"][3]["planning_source"] == "cached"
    assert "future_plan_survives_sqlite_reopen" in report["checks"]
    assert "reopened_future_plan_executes_cached_next_without_foreground_model" in report["checks"]
    assert "stay_keeps_experience_step_and_records_parent" in report["checks"]
    assert "stay_has_no_content_from_any_delivered_screen" in report["checks"]
    assert before == hashlib.sha256((data / "openbiliclaw.db").read_bytes()).hexdigest()
    saved = (tmp_path / "report.json").read_text()
    assert "Synthetic card" not in saved
    assert runner._TEST_FEEDBACK not in saved
    assert "raw_response" not in saved
    assert str(data) not in saved


async def test_real_http_fallback_is_never_reported_as_agent_success(runner, tmp_path, monkeypatch):
    config, _ = source(tmp_path)
    monkeypatch.setattr(runner, "load_config", lambda *_args, **_kwargs: config)
    agent = FakeAgent()
    agent.fail = True
    report = await runner.run(args(tmp_path), service=agent)
    assert report["passed"] is False
    assert report["failure_code"] == "screen_not_agent_only"
    assert report["screens"][0]["realized_strategy"] == "deterministic"
    assert report["screens"][0]["planning_reason"] == "planner_unavailable"


async def test_absent_future_plan_cannot_pass_restart_acceptance(runner, tmp_path):
    source(tmp_path)

    class ShortReplanAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.foreground_calls = 0

        async def complete_structured_task(self, **kwargs):
            if kwargs.get("caller", "").endswith(".preplan"):
                raise RuntimeError("synthetic background unavailable")
            self.foreground_calls += 1
            response = await super().complete_structured_task(**kwargs)
            if self.foreground_calls == 2:
                output = json.loads(response.content)
                output["screens"] = output["screens"][:1]
                selected = {
                    ref for pair in output["screens"][0]["pairs"] for ref in pair["card_refs"]
                }
                candidates = json.loads(kwargs["user_input"])["untrusted_candidates"]
                output["completion"] = {
                    "reason": "supply_exhausted",
                    "omissions": [
                        {
                            "refs": [
                                card["ref"] for card in candidates if card["ref"] not in selected
                            ],
                            "reason": "off_intent",
                        }
                    ],
                }
                response.content = json.dumps(output)
            return response

    report = await runner.run(args(tmp_path), service=ShortReplanAgent())
    assert report["passed"] is False
    assert report["failure_code"] == "future_plan_missing"
    assert "future_plan_survives_sqlite_reopen" not in report["checks"]


async def test_first_single_screen_plan_stops_before_click_or_next_without_misattribution(
    runner, tmp_path
):
    source(tmp_path)

    class SingleScreenAgent(FakeAgent):
        async def complete_structured_task(self, **kwargs):
            if kwargs.get("caller", "").endswith(".preplan"):
                raise RuntimeError("synthetic background unavailable")
            response = await super().complete_structured_task(**kwargs)
            output = json.loads(response.content)
            output["screens"] = output["screens"][:1]
            selected = {ref for pair in output["screens"][0]["pairs"] for ref in pair["card_refs"]}
            candidates = json.loads(kwargs["user_input"])["untrusted_candidates"]
            output["completion"] = {
                "reason": "supply_exhausted",
                "omissions": [
                    {
                        "refs": [card["ref"] for card in candidates if card["ref"] not in selected],
                        "reason": "off_intent",
                    }
                ],
            }
            response.content = json.dumps(output)
            return response

    report = await runner.run(args(tmp_path), service=SingleScreenAgent())
    assert report["passed"] is False
    assert report["failure_code"] == "first_plan_has_no_future_screen"
    assert len(report["screens"]) == 1
    assert sum(not call["background"] for call in report["model_calls"]) == 1
    assert not any(row["path"] == "/api/recommendation-click" for row in report["http_requests"])
    assert "actual_visible_subset_ack_is_idempotent" in report["checks"]


@pytest.mark.parametrize("plan", [None, {}, {"screens": []}])
def test_no_future_and_consumed_tombstones_are_not_persistence_evidence(runner, plan):
    stored = SimpleNamespace(plan=plan) if plan is not None else None
    application = SimpleNamespace(
        runtime=SimpleNamespace(store=SimpleNamespace(load_plan=lambda _stream: stored))
    )
    with pytest.raises(runner.AcceptanceFailure, match="future_plan_missing"):
        runner._future_plan_checkpoint(application, "fixture-stream")


@pytest.mark.parametrize("corrupt_at", ["screen-3", "stay-1"])
async def test_http_delivery_loss_cannot_hide_behind_successful_store_commit(
    runner, tmp_path, monkeypatch, corrupt_at
):
    source(tmp_path)
    original = httpx.AsyncClient.request
    corrupt = False

    async def lossy_response(client, method, url, **kwargs):
        nonlocal corrupt
        result = await original(client, method, url, **kwargs)
        if (kwargs.get("json") or {}).get("request_id") == corrupt_at:
            corrupt = True
        if corrupt and result.status_code == 200 and "/director/" in str(url):
            data = result.json()
            if "items" in data:
                data["items"] = data["items"][:1]
                return httpx.Response(200, json=data, request=result.request)
        return result

    monkeypatch.setattr(httpx.AsyncClient, "request", lossy_response)
    report = await runner.run(args(tmp_path), service=FakeAgent())
    assert report["passed"] is False
    assert report["failure_code"] == "http_delivery_differs_from_committed_batch"


async def test_recording_boundary_keeps_counts_and_hashes_not_private_text(runner):
    class Service:
        async def complete_structured_task(self, **kwargs):
            return LLMResponse(content="PRIVATE_MODEL_ANSWER", model="fixture", provider="fixture")

    service = runner.SafeRecordingService(Service())
    payload = {
        "profile_context": {"stable": {"interests": ["PRIVATE_PROFILE"]}},
        "untrusted_candidates": [{"title": "PRIVATE_TITLE"}],
        "session_context": {
            "recent_screens": [{"cards": [{"title": "PRIVATE_HISTORY"}]}],
            "real_feedback": [{"kind": "click", "note": "PRIVATE_NOTE"}],
        },
    }
    await service.complete_structured_task(
        user_input=json.dumps(payload), system_instruction="PRIVATE_SYSTEM"
    )
    serialized = json.dumps(service.records)
    assert "PRIVATE" not in serialized
    assert service.records[0]["profile_interest_count"] == 1
    assert service.records[0]["recent_screen_count"] == 1
    assert service.records[0]["feedback_kind_counts"] == {"click": 1}


def test_snapshot_allowlist_never_copies_auth_material(runner, tmp_path):
    _, data = source(tmp_path)
    (data / "memory" / "credentials.json").write_text('"SECRET"')
    (data / "cookie.txt").write_text("SECRET")
    destination = tmp_path / "private" / "data"
    result = runner.snapshot_data(data, destination)
    assert result["source_open_mode"] == "sqlite_readonly_online_backup"
    assert not (destination / "memory" / "credentials.json").exists()
    assert not (destination / "cookie.txt").exists()
    assert (destination / "memory" / "soul.json").exists()


def test_snapshot_rejects_nested_live_data_destination(runner, tmp_path):
    _, data = source(tmp_path)
    with pytest.raises(runner.AcceptanceFailure, match="unsafe_snapshot_target"):
        runner.snapshot_data(data, data / "test")


def test_snapshot_rejects_existing_or_parent_destination(runner, tmp_path):
    _, data = source(tmp_path)
    with pytest.raises(runner.AcceptanceFailure, match="unsafe_snapshot_target"):
        runner.snapshot_data(data, data.parent)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "openbiliclaw.db").write_text("KEEP")
    with pytest.raises(runner.AcceptanceFailure, match="snapshot_target_exists"):
        runner.snapshot_data(data, existing)
    assert (existing / "openbiliclaw.db").read_text() == "KEEP"


def test_shape_diagnostics_identify_conflicting_refs_without_text(runner):
    shape = runner.response_shape(
        json.dumps(
            {
                "screens": [{"pairs": [{"card_refs": ["a", "a", "wrong"]}]}],
                "completion": {"omissions": [{"refs": ["a", "b"], "note": "PRIVATE"}]},
            }
        ),
        [{"ref": "a"}, {"ref": "b"}],
    )
    assert shape["screen_card_counts"] == [3]
    assert shape["unknown_ref_count"] == 1
    assert shape["duplicate_ref_count"] == 1
    assert shape["selected_omitted_overlap_count"] == 1
    assert shape["omitted_count"] == 2
    assert "PRIVATE" not in json.dumps(shape)


def test_omission_reason_diagnostics_only_keep_whitelisted_labels(runner):
    shape = runner.response_shape(
        json.dumps(
            {
                "completion": {
                    "omissions": [
                        {"refs": ["a", "b"], "reason": "off_intent"},
                        {"refs": ["c"], "reason": "PRIVATE_UNTRUSTED_REASON"},
                    ]
                }
            }
        ),
        [{"ref": "a"}, {"ref": "b"}, {"ref": "c"}],
    )
    assert shape["omission_reason_ref_counts"] == {"off_intent": 2, "invalid": 1}
    assert "PRIVATE" not in json.dumps(shape)


@pytest.mark.parametrize("content", ["{", "null", "{}", '{"screens":[{"pairs":null}]}'])
def test_shape_diagnostics_do_not_replace_malformed_model_result_with_observer_error(
    runner, content
):
    assert isinstance(runner.response_shape(content, []), dict)


async def test_private_workspace_closes_owners_before_deleting_data(runner):
    root = None
    checked = False

    async def cleanup():
        nonlocal checked
        assert root is not None and root.exists()
        checked = True

    with pytest.raises(RuntimeError, match="synthetic-failure"):
        async with runner.private_workspace(cleanup) as root:
            raise RuntimeError("synthetic-failure")
    assert checked
    assert root is not None and not root.exists()


async def test_existing_artifact_is_not_overwritten(runner, tmp_path):
    output = tmp_path / "report.json"
    output.write_text("ORIGINAL")
    with pytest.raises(runner.AcceptanceFailure, match="output_already_exists"):
        await runner.run(args(tmp_path))
    assert output.read_text() == "ORIGINAL"


async def test_missing_source_config_produces_safe_failure_artifact(runner, tmp_path):
    report = await runner.run(args(tmp_path), service=FakeAgent())
    assert report["passed"] is False
    assert report["failure_code"] == "source_config_missing"
    assert report["model_calls"] == []
    assert (
        json.loads((tmp_path / "report.json").read_text())["failure_code"]
        == "source_config_missing"
    )
