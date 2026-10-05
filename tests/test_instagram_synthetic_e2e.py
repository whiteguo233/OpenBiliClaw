"""Synthetic browser transport; real task API, ingress and memory storage."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from openbiliclaw.config import Config, save_config


def test_synthetic_instagram_import_is_nonempty_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config()
    config.data_dir = str(tmp_path / "data")
    config.scheduler.enabled = False
    config.scheduler.source_incremental_enabled = False
    config.sources.instagram.enabled = True
    save_config(config, tmp_path / "config.toml")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "scripts/instagram_synthetic_e2e.py"
    run = runpy.run_path(str(script))["run_ingress"]
    report = run(tmp_path)
    assert report["evidence"] == "synthetic-browser-result"
    assert report["inserted"] == 5
    assert report["after_duplicate_callback"] == 5
    assert report["after_second_task"] == 5
    assert report["event_types"] == {"like": 2, "favorite": 2, "follow": 1}
    assert report["account_mismatch_status"] == 409
    assert report["after_account_mismatch"] == 5
    assert report["after_smoke_new_items"] == 5
    with pytest.raises(ValueError, match="existing database"):
        run(tmp_path)


def test_stored_instagram_profile_rebuild_needs_no_bilibili_login(tmp_path: Path) -> None:
    config = Config()
    config.data_dir = str(tmp_path / "data")
    config.scheduler.enabled = False
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "synthetic-test-not-a-real-key"
    save_config(config, tmp_path / "config.toml")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "openbiliclaw.cli", "rebuild-profile", "--source", "instagram"],
        env={
            **os.environ,
            "OPENBILICLAW_PROJECT_ROOT": str(tmp_path),
            "PYTHONPATH": str(root / "src"),
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 1
    assert "没有找到事件" in result.stdout
    assert "B 站认证" not in result.stdout
