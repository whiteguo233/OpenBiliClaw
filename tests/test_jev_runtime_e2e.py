"""Real API process, browser, hot reload and queue; only Jev inference is synthetic."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from scripts.jev_fake_backend import FakeJevBackend

from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.profile import InterestTag, OnionProfile, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Page

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]
NEGATIVE = "只屏蔽承诺保证收益的广告；风险分析和批判不属于此项"


@pytest.fixture
def chromium_page() -> Iterator[Page]:
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser.new_page(viewport={"width": 1440, "height": 1000})
        finally:
            browser.close()


@pytest.fixture
def real_jev_app(tmp_path: Path) -> Iterator[tuple[str, Path, subprocess.Popen]]:
    """Boot the unmodified production factory with isolated config and no real keys."""
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.llm.deepseek.api_key = "synthetic-chat-key"
    cfg.llm.deepseek.model = "deepseek-chat"
    cfg.llm.deepseek.base_url = "http://127.0.0.1:1"
    cfg.llm.default_provider = "deepseek"
    cfg.llm.default_chain = ["deepseek"]
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.base_url = "http://127.0.0.1:1"
    for entry in fields(cfg.sources):
        source = getattr(cfg.sources, entry.name)
        if hasattr(source, "enabled"):
            source.enabled = entry.name == "xiaohongshu"
    cfg.scheduler.pool_source_shares = {"xiaohongshu": 1}
    cfg.scheduler.eval_min_batch_size = 1
    cfg.discovery.candidate_eval_concurrency = 1
    cfg.scheduler.auto_update_enabled = False
    cfg.network.mode = "direct"
    save_config(cfg, tmp_path / "config.toml")
    # Pass no inherited service credentials or worker-mode flags to this child.
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "TMPDIR"}}
    env.update(PYTHONPATH=str(ROOT / "src"), OPENBILICLAW_PROJECT_ROOT=str(tmp_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "openbiliclaw.api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=url, trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    assert process.poll() is None, "isolated API exited during startup"
                    try:
                        if client.get("/api/config").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("isolated API startup timed out")
            yield url, tmp_path, process
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_browser_save_hot_reload_and_ingest_use_saved_jev_connection(
    real_jev_app: tuple[str, Path, subprocess.Popen], chromium_page: Page
) -> None:
    """The actual config transaction changes the running evaluator, not just TOML."""
    url, directory, _ = real_jev_app
    page = chromium_page
    with FakeJevBackend(model="jev-1.14.0", max_state_question_bytes=12_000) as backend:
        page.goto(f"{url}/web/")
        page.get_by_role("button", name="设置", exact=True).click()
        page.get_by_role("tab", name="高级功能").click()
        form = page.locator("#jevConnectionSettings")
        form.get_by_label("启用 Jev 服务连接", exact=True).check()
        form.get_by_label("API URL（完整接口地址）", exact=True).fill(backend.url)
        form.get_by_label("固定模型 ID", exact=True).fill("jev-1.14.0")
        form.get_by_label("Token", exact=True).fill("synthetic-test-key")
        page.locator("#evalScorer").select_option("jev")
        # Tighten an official model's standard window through the actual form.
        form.locator("summary").click()
        form.get_by_label("手动设置所选模型的上下文上限", exact=True).check()
        form.get_by_label("总请求 token 上限", exact=True).fill("64000")
        form.get_by_label("state＋单题 token 上限", exact=True).fill("16000")
        form.get_by_label("传输最大尝试次数", exact=True).fill("1")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/config") and response.request.method == "PUT"
            )
        ) as saving:
            page.get_by_role("button", name="保存配置").click()
        assert saving.value.status == 202, saving.value.text()

        with httpx.Client(base_url=url, trust_env=False, timeout=5) as client:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = client.get("/api/config/apply-status").json()
                assert status["state"] != "failed", status
                if status["state"] == "applied" and status["applied_revision"] >= 1:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("real config hot reload did not complete")
            read = client.get("/api/config?reveal_keys=true")
            assert "synthetic-test-key" not in read.text
            assert read.json()["discovery"]["eval_scorer"] == "jev"
            saved = load_config(directory / "config.toml", consult_environment=False)
            assert saved.decision.providers.jev.endpoint_url == backend.url
            assert saved.decision.providers.jev.model == "jev-1.14.0"
            assert saved.decision.providers.jev.api_key == "synthetic-test-key"

            memory = MemoryManager(directory / "data")
            memory.initialize()
            profile = OnionProfile.from_legacy(
                SoulProfile(
                    preferences=PreferenceLayer(
                        interests=[InterestTag("算法与数据结构", "编程", 0.9)],
                        disliked_topics=[NEGATIVE],
                    )
                )
            )
            memory.get_layer("soul").data.update(profile.to_dict())
            memory.save_all()
            paragraphs = [f"背景段落{i}讲解算法设计的通用原理。" for i in range(350)]
            paragraphs[200] = "如果用于生产，必须先备份；否则不适用。"
            original = "\n".join(paragraphs)
            bodies = ["讲解排序算法的基本概念。", "链表的通用原理。", original]
            response = client.post(
                "/api/sources/xhs/observed-urls",
                json={
                    "page_type": "search",
                    "notes": [
                        {
                            "url": f"https://www.xiaohongshu.com/explore/jevsynthetic{i}",
                            "title": f"算法与数据结构原理 {i}",
                            "description": body,
                            "author": "合成测试作者",
                        }
                        for i, body in enumerate(bodies)
                    ],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["enqueued"] == 3
            db = Database(directory / "data/openbiliclaw.db")
            db.initialize()
            try:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    cached = db.get_cached_content()
                    if len(cached) >= 3:
                        break
                    time.sleep(0.1)
                assert len(cached) == 3, db.count_discovery_candidates_by_status()
                assert all(row["relevance_score"] == 0.75 for row in cached)
                assert db.count_discovery_candidates_by_status().get("pending_eval", 0) == 0
                rows = db.get_discovery_candidates_by_ids([1, 2, 3])
                assert len(rows) == 3
                assert rows[2]["description"] == original
            finally:
                db.close()

        seen: list[str] = []
        partial = False
        for request, record in zip(backend.requests, backend.records, strict=True):
            assert record["status"] == 200
            assert record["max_state_question_bytes"] <= 12_000
            assert request["model"] == "jev-1.14.0"
            assert NEGATIVE in str(request["state"])
            candidates = request["state"]["untrusted_candidates"]
            assert len(request["questions"]) == 10 * len(candidates)
            for candidate in candidates:
                content = candidate["content"]
                seen.append(content["content_id"])
                partial |= content["text_coverage"]["is_partial"]
                if content["text_coverage"]["is_partial"]:
                    text = "".join(span["text"] for span in content["text_spans"].values())
                    assert "如果用于生产，必须先备份；" in text
                    assert "否则不适用。" in text
        assert sorted(seen) == [f"jevsynthetic{i}" for i in range(3)]
        assert partial
        page.reload()
        page.get_by_role("button", name="设置", exact=True).click()
        page.get_by_role("tab", name="高级功能").click()
        assert page.locator("#evalScorer").input_value() == "jev"
        assert form.get_by_label("Token", exact=True).input_value() == ""
        artifacts = ROOT / "output/playwright"
        artifacts.mkdir(parents=True, exist_ok=True)
        form.screenshot(path=str(artifacts / "jev-runtime-settings.png"))
        print(
            json.dumps(
                {
                    "backend": "local_fake",
                    "real_api_process": True,
                    "hot_reload": "applied",
                    "candidates_cached": 3,
                    "original_preserved": True,
                    "requests": backend.records,
                },
                ensure_ascii=False,
            )
        )


def test_process_restart_reuses_saved_jev_model_endpoint_and_capacity(
    real_jev_app: tuple[str, Path, subprocess.Popen],
) -> None:
    """A fresh production process can evaluate with the persisted custom model."""
    url, directory, first_process = real_jev_app
    model = "synthetic-restart-v1"
    with FakeJevBackend(
        model=model, max_request_bytes=30_000, max_state_question_bytes=15_000
    ) as backend:
        with httpx.Client(base_url=url, trust_env=False, timeout=5) as client:
            response = client.put(
                "/api/config",
                json={
                    "decision": {
                        "providers": {
                            "jev": {
                                "enabled": True,
                                "model": model,
                                "endpoint_url": backend.url,
                                "api_key": "synthetic-test-key",
                                "max_attempts": 1,
                                "context_limits": {
                                    model: {
                                        "max_request_tokens": 40_000,
                                        "max_state_question_tokens": 20_000,
                                    }
                                },
                            }
                        }
                    },
                    "discovery": {"eval_scorer": "jev"},
                },
            )
            assert response.status_code == 202, response.text
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = client.get("/api/config/apply-status").json()
                assert status["state"] != "failed", status
                if status["state"] == "applied":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("config hot reload did not complete before restart")

        # Persist only a synthetic profile through the public memory seam.
        db = Database(directory / "data/openbiliclaw.db")
        db.initialize()
        try:
            memory = MemoryManager(directory / "data", database=db)
            memory.initialize()
            memory.get_layer("soul").data.update(OnionProfile().to_dict())
            memory.save_all()
        finally:
            db.close()
        first_process.terminate()
        first_process.wait(timeout=10)

        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "TMPDIR"}}
        env.update(PYTHONPATH=str(ROOT / "src"), OPENBILICLAW_PROJECT_ROOT=str(directory))
        with (directory / "restart.log").open("w") as log:
            restarted = subprocess.Popen(
                first_process.args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            try:
                with httpx.Client(base_url=url, trust_env=False, timeout=5) as client:
                    deadline = time.monotonic() + 40
                    while time.monotonic() < deadline:
                        assert restarted.poll() is None, "API exited during restart"
                        try:
                            read = client.get("/api/config")
                            if read.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.1)
                    else:
                        pytest.fail("API restart timed out")
                    assert "synthetic-test-key" not in read.text
                    status = read.json()["discovery"]["jev_provider"]
                    assert status["model"] == model
                    assert status["endpoint_url"] == backend.url
                    assert status["context_budget"]["max_state_question_bytes"] == 15_000
                    response = client.post(
                        "/api/sources/xhs/observed-urls",
                        json={
                            "page_type": "search",
                            "notes": [
                                {
                                    "url": "https://www.xiaohongshu.com/explore/jevsyntheticrestart",
                                    "title": "算法基础教学",
                                    "description": "算法与数据结构的通用概念。",
                                }
                            ],
                        },
                    )
                    assert response.status_code == 200 and response.json()["enqueued"] == 1
                    db = Database(directory / "data/openbiliclaw.db")
                    db.initialize()
                    try:
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            cached = db.get_cached_content()
                            if cached:
                                break
                            time.sleep(0.1)
                        assert len(cached) == 1, db.count_discovery_candidates_by_status()
                        assert cached[0]["relevance_score"] == 0.75
                    finally:
                        db.close()
                    assert len(backend.requests) == 1
                    assert backend.requests[0]["model"] == model
                    assert len(backend.requests[0]["questions"]) == 10
            finally:
                restarted.terminate()
                try:
                    restarted.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    restarted.kill()
                    restarted.wait(timeout=5)
