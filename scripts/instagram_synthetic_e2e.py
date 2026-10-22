"""Inject synthetic browser results into an explicitly isolated, fresh project.

No Instagram requests or credentials. Optional profile/discovery runs are separate
CLI commands, so this report cannot be mistaken for upstream E2E evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import load_config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.sources.instagram_tasks import InstagramTaskQueue
from openbiliclaw.storage.database import Database


def synthetic_items() -> list[dict[str, Any]]:
    """Normalized protocol fixtures, not captured Instagram memberships."""
    rows = []
    for index, (scope, title) in enumerate(
        [
            ("instagram_liked", "机器人视觉导航实测：遮挡与低光环境下的失败案例"),
            ("instagram_liked", "开源语言模型本地部署：推理延迟与显存测量"),
            ("instagram_saved", "机械臂抓取实验：标定步骤、代码与复现实验"),
            ("instagram_saved", "技术摄影教程：光线、曝光和后期处理示范"),
        ],
        1,
    ):
        rows.append(
            {
                "scope": scope,
                "id": str(9900000000000000 + index),
                "code": f"SYNTHETIC_{index}",
                "content_type": "post",
                "url": f"https://www.instagram.com/p/SYNTHETIC_{index}/",
                "title": f"[SYNTHETIC] {title}",
                "description": f"合成测试样本：{title}",
                "author_id": "9900000000000099",
                "author_name": "synthetic_test_creator",
                "published_at": "2026-09-29T12:00:00Z",
            }
        )
    rows.append(
        {
            "scope": "instagram_following",
            "id": "9900000000000099",
            "content_type": "user",
            "author_id": "9900000000000099",
            "author_name": "synthetic_test_creator",
            "title": "合成机器人技术作者",
            "url": "https://www.instagram.com/synthetic_test_creator/",
        }
    )
    return rows


def run_ingress(root: Path) -> dict[str, Any]:
    """Exercise production result callbacks and inspect public memory reads."""
    root = root.resolve()
    if Path(os.environ.get("OPENBILICLAW_PROJECT_ROOT", "/")).resolve() != root:
        raise ValueError("Set OPENBILICLAW_PROJECT_ROOT to the isolated project first")
    config = load_config()
    data_dir = Path(config.data_dir).resolve()
    if data_dir != root / "data" or config.scheduler.enabled:
        raise ValueError("Requires isolated root/data and disabled scheduler")
    db_path = data_dir / "openbiliclaw.db"
    if db_path.exists():
        raise ValueError("Refusing to inject synthetic events into an existing database")
    data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(db_path)
    database.initialize()
    memory = MemoryManager(data_dir, database=database)
    queue = InstagramTaskQueue(database)
    report: dict[str, Any] = {"evidence": "synthetic-browser-result"}
    with TestClient(
        create_app(memory_manager=memory, database=database, soul_engine=object())
    ) as client:
        for round_index in range(2):
            task_id = queue.enqueue_with_id(
                "bootstrap_events",
                {
                    "profile_update": True,
                    "smoke_only": False,
                    "purpose": "fetch",
                },
                daily_budget=0,
            )
            assert task_id
            claim_response = client.get("/api/sources/instagram/next-task")
            assert claim_response.status_code == 200, claim_response.text
            claim = claim_response.json()
            payload = {
                "task_id": task_id,
                "claim_token": claim["claim_token"],
                "status": "partial",
                "error": "instagram_liked:page_cap_reached",
                "account_id": "9900000000000001",
                "items": synthetic_items(),
                "scope_complete": {
                    "instagram_liked": False,
                    "instagram_saved": True,
                    "instagram_following": True,
                },
                "debug": {
                    "identity_verified": True,
                    "response_observed": True,
                    "terminal_evidence": "accepted_nonterminal_scope_page",
                },
            }
            response = client.post("/api/sources/instagram/task-result", json=payload)
            assert response.status_code == 200, response.text
            count = len(memory.query_events(limit=100))
            if round_index == 0:
                report["inserted"] = count
                duplicate = client.post("/api/sources/instagram/task-result", json=payload)
                assert duplicate.status_code == 200 and duplicate.json()["ignored"]
                report["after_duplicate_callback"] = len(memory.query_events(limit=100))
            else:
                report["after_second_task"] = count
        events = memory.query_events(limit=100)
        report["event_types"] = dict(Counter(event["event_type"] for event in events))
        assert all(event.get("url") for event in events)
        report["partial_rows_retained"] = len(events)
        for smoke_only in (False, True):
            task_id = queue.enqueue_with_id(
                "bootstrap_events",
                {
                    "profile_update": True,
                    "smoke_only": smoke_only,
                    "purpose": "smoke" if smoke_only else "fetch",
                },
                daily_budget=0,
            )
            claim_response = client.get("/api/sources/instagram/next-task")
            assert claim_response.status_code == 200, claim_response.text
            claim = claim_response.json()
            payload = {**payload, "task_id": task_id, "claim_token": claim["claim_token"]}
            if not smoke_only:
                payload["account_id"] = "9900000000000002"
            else:
                payload["account_id"] = "9900000000000001"
                new_items = synthetic_items()
                for item in new_items:
                    item["id"] = str(int(item["id"]) + 100)
                    if "code" in item:
                        item["code"] += "_NEW"
                        item["url"] = f"https://www.instagram.com/p/{item['code']}/"
                payload["items"] = new_items
            response = client.post("/api/sources/instagram/task-result", json=payload)
            if not smoke_only:
                report["account_mismatch_status"] = response.status_code
                assert response.json()["detail"] == "instagram_account_switch_not_supported"
                report["after_account_mismatch"] = len(memory.query_events(limit=100))
            else:
                assert response.status_code == 200, response.text
                report["after_smoke_new_items"] = len(memory.query_events(limit=100))
    database.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_ingress(args.root), ensure_ascii=False, indent=2))
