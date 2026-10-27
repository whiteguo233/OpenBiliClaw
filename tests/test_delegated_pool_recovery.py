"""A repaired model must reach the child that drains durable candidates."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.proc import (
    ChildProcessSupervisor,
    ManagedChild,
    backend_child_env,
    backend_config_generation,
)
from openbiliclaw.storage.database import Database

# Replace only model I/O. SQLite, candidate scoring/admission, expression-copy
# and the parent/child process boundary are real, with no external requests.
_WORKER = """
import asyncio
from pathlib import Path
from openbiliclaw.config import load_config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.storage.database import Database
from tests.test_discovery_candidate_pipeline import _ScoringLLM
from tests.test_recommendation_engine import _CopyReadyExpressionLLM
from tests.test_search_strategy import _build_profile

cfg = load_config()
model = cfg.llm.deepseek.model
db = Database(cfg.data_path / 'openbiliclaw.db')
db.initialize()
llm = _ScoringLLM([{
    'score': 0.9, 'reason': 'fit', 'topic_group': 'tech', 'style_key': 'deep_dive',
    'temporal_class': 'evergreen', 'temporal_confidence': 0.95,
    'temporal_reason': 'timeless knowledge', 'temporal_validity_mode': 'none',
    'temporal_scope': 'none', 'temporal_state': 'unknown',
}])
if model == 'broken-model':
    async def fail(**kwargs):
        raise RuntimeError('model is unavailable')
    llm.complete_structured_task = fail
pipeline = DiscoveryCandidatePipeline(
    database=db, discovery_engine=ContentDiscoveryEngine(llm_service=llm, database=db),
    pool_target_count=10,
)
async def run():
    profile = _build_profile()
    await pipeline.drain_pending(profile=profile, batch_size=1, flush=True)
    engine = RecommendationEngine(llm=_CopyReadyExpressionLLM(), database=db)
    await engine.drain_pending_expression_copy(profile=profile, limit=10)
    (cfg.data_path / ('ready-' + model)).touch()
    while True:
        await asyncio.sleep(3600)
asyncio.run(run())
"""


@pytest.mark.asyncio
async def test_model_repair_resumes_real_candidate_admission_and_available_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.llm.deepseek.api_key = "test-not-a-secret"
    cfg.llm.deepseek.model = "broken-model"
    save_config(cfg, tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    db = Database(cfg.data_path / "openbiliclaw.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVRECOVERY",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVRECOVERY",
                title="Timeless technology",
            )
        ]
    )
    repo = Path(__file__).resolve().parents[1]
    env = {
        **backend_child_env(app),
        "OPENBILICLAW_PROJECT_ROOT": str(tmp_path),
        "PYTHONPATH": os.pathsep.join([str(repo / "src"), str(repo)]),
    }
    processes: list[subprocess.Popen[bytes]] = []

    with (tmp_path / "worker.log").open("wb") as log:

        def spawn() -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                [sys.executable, "-c", _WORKER],
                cwd=repo,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
            return process

        supervisor = ChildProcessSupervisor(
            [ManagedChild("discovery-worker", spawn)],
            config_generation_provider=lambda: backend_config_generation(app),
        )

        def wait_for_model(model: str) -> None:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert processes[-1].poll() is None, (tmp_path / "worker.log").read_text()
                if (cfg.data_path / f"ready-{model}").exists():
                    return
                time.sleep(0.02)
            raise AssertionError((tmp_path / "worker.log").read_text())

        try:
            supervisor.check_once()
            await asyncio.to_thread(wait_for_model, "broken-model")
            assert db.count_pool_readiness()["available"] == 0
            assert db.count_discovery_candidates_by_status()["pending_eval"] == 1

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8420"
            ) as client:
                response = await client.put(
                    "/api/config", json={"llm": {"deepseek": {"model": "working-model"}}}
                )
                assert response.status_code == 202
                for _ in range(200):
                    status = (await client.get("/api/config/apply-status")).json()
                    if status["state"] == "applied":
                        break
                    await asyncio.sleep(0.01)
                assert status["state"] == "applied", status
            supervisor.check_once()
            await asyncio.to_thread(wait_for_model, "working-model")

            assert len(processes) == 2
            assert processes[0].poll() is not None
            assert db.count_discovery_candidates_by_status()["cached"] == 1
            assert db.count_pool_readiness()["available"] == 1
        finally:
            supervisor.stop()
            db.close()
