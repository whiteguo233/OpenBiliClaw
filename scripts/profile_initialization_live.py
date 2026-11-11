#!/usr/bin/env python3
"""Opt-in four-stage initialization against real public sources and free models.

No responses or events are replaced. Artifacts exclude config, SDK, database and
server logs. Run on an isolated runner; --root must not already exist.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx

from openbiliclaw.config import Config, save_config
from openbiliclaw.llm.sponsored_profiles import CHAT_MODELS
from openbiliclaw.runtime.sponsored_bundle import ensure_runtime


def run(root: Path, model: str, username: str, platform: str) -> None:
    root.mkdir(parents=True, exist_ok=False)
    evidence = root / "evidence"
    evidence.mkdir()
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    cfg = Config(data_dir=str(root / "data"))
    cfg.llm.instance_routing = True
    cfg.llm.default_chain = [
        "siliconflow-free" if model == CHAT_MODELS[0] else "siliconflow-free-qwen35-4b"
    ]
    cfg.llm.embedding.provider = "siliconflow_free"
    cfg.llm.embedding.model = "BAAI/bge-m3"
    cfg.llm.embedding.output_dimensionality = 1024
    cfg.scheduler.auto_update_enabled = False
    cfg.scheduler.pool_target_count = 20
    cfg.scheduler.copy_ready_target_count = 20
    cfg.network.mode = "direct"
    for descriptor in fields(cfg.sources):
        source = getattr(cfg.sources, descriptor.name)
        if hasattr(source, "enabled"):
            source.enabled = False
    source = getattr(cfg.sources, platform)
    source.enabled = True
    source.username = username
    source.bootstrap_limit = 40
    if platform == "github":
        source.source_modes = ("search", "ranked", "latest")
    save_config(cfg, root / "config.toml")
    ensure_runtime()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    process: subprocess.Popen[Any] | None = None
    log = (root / "server.log").open("w")
    started = time.monotonic()
    report: dict[str, Any] = {
        "model": model,
        "platform": platform,
        "public_username": username,
        "mocked_responses": False,
        "configuration": {"default_chain": cfg.llm.default_chain},
        "stage_history": [],
        "ok": False,
    }

    def save() -> None:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def start() -> None:
        nonlocal process
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
                "--log-level",
                "warning",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(180):
            assert process.poll() is None, "backend exited during startup"
            try:
                response = httpx.get(origin + "/api/auth/status", trust_env=False, timeout=2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise AssertionError("backend startup timed out")

    def stop() -> None:
        nonlocal process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        process = None

    try:
        from playwright.sync_api import sync_playwright

        start()
        with httpx.Client(base_url=origin, timeout=240, trust_env=False) as client:
            with sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                page.goto(origin + "/web/", timeout=90000)
                page.wait_for_selector(".init-onboarding", timeout=90000)
                source_selector = f'input[data-init-source="{platform}"]'
                for checkbox in page.locator("input[data-init-source]").all():
                    if (
                        checkbox.get_attribute("data-init-source") != platform
                        and checkbox.is_checked()
                    ):
                        checkbox.uncheck()
                page.locator(source_selector).check()
                username_field = (
                    "#initGitHubUsername" if platform == "github" else "#initBangumiUsername"
                )
                page.locator(username_field).fill(username)
                page.locator("#initLlmConcurrency").fill("1")
                prereq_deadline = time.monotonic() + 240
                while time.monotonic() < prereq_deadline:
                    status = client.get("/api/init-status").json()
                    report["prerequisites"] = status.get("prerequisites")
                    save()
                    if status.get("can_start"):
                        break
                    time.sleep(3)
                else:
                    report["failed_status"] = status
                    raise AssertionError("live initialization prerequisites did not become ready")
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/init") and response.request.method == "POST"
                    ),
                    timeout=90000,
                ) as posted:
                    page.locator('[data-init-action="start"]').click()
                response = posted.value
                report["start_http_status"] = response.status
                report["start_response"] = response.json()
                save()
                assert response.status == 202, "real GUI initialization request was rejected"
                run_id = response.json()["run_id"]
                deadline = time.monotonic() + 1500
                previous = None
                while time.monotonic() < deadline:
                    status = client.get("/api/init-status").json()
                    current = [(s.get("n"), s.get("status")) for s in status.get("stages", [])]
                    if current != previous:
                        report["stage_history"].append(
                            {
                                "seconds": round(time.monotonic() - started, 2),
                                "stages": status.get("stages"),
                            }
                        )
                        previous = current
                        save()
                        print(model, platform, current, flush=True)
                    if not status.get("running") and status.get("run_id") == run_id:
                        break
                    time.sleep(3)
                report["final_status"] = status
                save()
                assert status.get("initialized") and not status.get("running"), (
                    "init did not finish"
                )
                assert not status.get("partial_success"), "four-stage init only partially succeeded"
                assert all(s.get("status") == "ok" for s in status["stages"]), (
                    "a stage did not pass"
                )
                response = client.get("/api/profile-summary")
                response.raise_for_status()
                profile = response.json()
                required = (
                    "core_traits",
                    "deep_needs",
                    "values",
                    "motivational_drivers",
                    "cognitive_style",
                    "current_phase",
                    "likes",
                )
                report["profile_counts"] = {key: len(profile.get(key) or []) for key in required}
                report["cognition_counts"] = {
                    key: len(profile.get(key) or [])
                    for key in ("recent_awareness", "active_insights")
                }
                assert profile["initialized"] and all(report["profile_counts"].values()), (
                    "empty profile blocks"
                )
                assert profile["current_phase"] != "暂无明确自述"
                for key in ("core_traits", "deep_needs", "values", "motivational_drivers"):
                    assert all("待确认" in value for value in profile[key]), (
                        "hypotheses lost uncertainty"
                    )
                assert not profile["mbti"].get("type"), "passive history invented MBTI"
                saved = json.loads((root / "data/memory/soul.json").read_text())
                assert saved["core"]["core_traits"] == profile["core_traits"]
                assert saved["values_layer"]["values"] == profile["values"]
                with sqlite3.connect(root / "data/openbiliclaw.db") as db:
                    db.row_factory = sqlite3.Row
                    rows = [
                        dict(row)
                        for row in db.execute("SELECT event_type, title, metadata FROM events")
                    ]
                    report["real_collected_events"] = len(rows)
                    assert rows, "no source events were durably ingested"
                    usage = [
                        dict(row)
                        for row in db.execute("SELECT provider, model, caller FROM llm_usage")
                    ]
                report["usage"] = usage
                chat_usage = [row for row in usage if row["model"] in CHAT_MODELS]
                assert chat_usage and {row["model"] for row in chat_usage} == {model}
                assert any(row["caller"] == "soul.profile_build" for row in chat_usage)
                assert all(
                    row["provider"] in {"sponsored", "siliconflow_free"} for row in chat_usage
                )
                (evidence / "profile-summary.json").write_text(
                    json.dumps(profile, ensure_ascii=False, indent=2)
                )
                page.locator("#profileBtn").click()
                page.wait_for_function(
                    "() => document.querySelector('#profileDetails')?.innerText.includes('待确认')",
                    timeout=60000,
                )
                for key in ("core_traits", "deep_needs", "values", "motivational_drivers"):
                    assert profile[key][0] in page.locator("#profileDetails").inner_text()
                assert (
                    "仅凭观看和收藏无法可靠判断 MBTI"
                    in page.locator("#profileDetails").inner_text()
                )
                page.screenshot(path=str(evidence / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(origin + "/m/#/profile", timeout=60000)
                page.wait_for_function(
                    "() => document.querySelector('#app')?.innerText.includes('待确认')",
                    timeout=60000,
                )
                assert profile["core_traits"][0] in page.locator("#app").inner_text()
                page.screenshot(path=str(evidence / "mobile.png"), full_page=True)
                browser.close()
            result = subprocess.run(
                [sys.executable, "-m", "openbiliclaw.cli", "profile"],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, "CLI profile read failed"
            assert "待确认" in result.stdout
            (evidence / "cli.txt").write_text(result.stdout[result.stdout.index("╭") :])
            stop()
            start()
            restarted = client.get("/api/profile-summary").json()
            assert all(restarted[key] == profile[key] for key in required), (
                "profile changed across restart"
            )
            report["restart_profile_persisted"] = True
            report["ok"] = True
            save()
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        # No upstream payloads / credentials / raw logs in the public artifact.
        report["error"] = (
            str(exc)
            if isinstance(exc, AssertionError)
            else "See error type; raw server logs remain private to this runner."
        )
        save()
        raise
    finally:
        stop()
        log.close()
        save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=CHAT_MODELS, required=True)
    parser.add_argument("--platform", choices=("github", "bangumi"), default="github")
    parser.add_argument("--username", default="whiteguo233")
    args = parser.parse_args()
    run(args.root.resolve(), args.model, args.username, args.platform)


if __name__ == "__main__":
    main()
