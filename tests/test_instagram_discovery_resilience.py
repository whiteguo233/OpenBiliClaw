"""Regression cases from real public-page discovery failures."""

from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.runtime.instagram_producer import InstagramDiscoveryProducer


def test_explicit_instagram_init_can_build_producer_without_enabling_scheduler(tmp_path) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.runtime.instagram_producer import build_instagram_discovery_producer
    from openbiliclaw.storage.database import Database

    config = Config()
    config.sources.instagram.enabled = True
    config.scheduler.enabled = False
    db = Database(tmp_path / "manual-init.db")
    db.initialize()
    kwargs = {"config": config, "database": db, "soul_engine": object()}
    assert build_instagram_discovery_producer(**kwargs) is None
    assert build_instagram_discovery_producer(**kwargs, manual=True) is not None
    assert config.scheduler.enabled is False
    config.sources.instagram.enabled = False
    assert build_instagram_discovery_producer(**kwargs, manual=True) is None


@pytest.mark.asyncio
async def test_unavailable_topic_does_not_starve_the_next_valid_topic() -> None:
    payloads: list[dict[str, Any]] = []

    class Queue:
        def enqueue_with_id(self, _kind: str, payload: dict[str, Any], **_: Any) -> str:
            payloads.append(payload)
            return str(len(payloads))

        def get(self, task_id: str) -> dict[str, Any]:
            import json

            result = (
                {"status": "failed", "error": "public_page_unavailable", "items": []}
                if task_id == "1"
                else {
                    "status": "ok",
                    "items": [
                        {
                            "id": "3712345678901234567",
                            "code": "DAb_cd-123",
                            "content_type": "post",
                            "title": "Music lesson",
                            "url": "https://www.instagram.com/p/DAb_cd-123/",
                        }
                    ],
                }
            )
            return {
                "status": "failed" if task_id == "1" else "completed",
                "result_json": json.dumps(result),
            }

    producer = InstagramDiscoveryProducer(
        database=object(), task_queue=Queue(), soul_engine=object(), wait_seconds=0
    )
    profile = SimpleNamespace(preferences=SimpleNamespace(interests=["anime", "music"]))

    contents, _claims, reason = await producer._run_topics(profile, 3)

    assert [payload["topic"] for payload in payloads] == ["animation", "music"]
    assert len(contents) == 1
    assert reason == "partial"


def test_profile_topics_resolve_observed_public_routes_and_dedupe() -> None:
    from openbiliclaw.runtime.instagram_producer import _profile_keywords

    profile = SimpleNamespace(
        preferences=SimpleNamespace(interests=["动漫", "anime", "音乐", "人工智能", "游戏"])
    )
    assert _profile_keywords(profile, 5) == ["animation", "music", "technology", "gaming"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "partial"])
@pytest.mark.parametrize(
    "error", ["rate_limited", "challenge_required", "login_required", "response_rows_rejected"]
)
async def test_upstream_safety_or_schema_failures_still_stop_the_lane(
    error: str, status: str
) -> None:
    import json

    payloads: list[dict[str, Any]] = []

    class Queue:
        def enqueue_with_id(self, _kind: str, payload: dict[str, Any], **_: Any) -> str:
            payloads.append(payload)
            return "blocked"

        def get(self, _task_id: str) -> dict[str, Any]:
            return {
                "status": "failed" if status == "failed" else "completed",
                "result_json": json.dumps(
                    {
                        "status": status,
                        "items": [
                            {
                                "id": "3712345678901234567",
                                "code": "DAb_cd-123",
                                "content_type": "post",
                                "url": "https://www.instagram.com/p/DAb_cd-123/",
                            }
                        ]
                        if status == "partial"
                        else [],
                        "error": error,
                    }
                ),
            }

    producer = InstagramDiscoveryProducer(
        database=object(), task_queue=Queue(), soul_engine=object(), wait_seconds=0
    )
    profile = SimpleNamespace(preferences=SimpleNamespace(interests=["animation", "music"]))
    contents, _claims, reason = await producer._run_topics(profile, 3)
    assert len(payloads) == 1
    assert len(contents) == (1 if status == "partial" else 0)
    assert reason == error
