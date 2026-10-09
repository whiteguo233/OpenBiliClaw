"""Instagram durable task, completion, and bootstrap event regressions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.sources.bootstrap_state import (
    default_source_bootstrap_state,
    normalize_source_bootstrap_state,
    source_bootstrap_state_key,
)
from openbiliclaw.sources.instagram_tasks import (
    INSTAGRAM_BOOTSTRAP_SCOPES,
    InstagramTaskQueue,
    instagram_account_key,
    instagram_bootstrap_item_key,
    instagram_bootstrap_items_to_events,
    normalize_instagram_failure_code,
)
from openbiliclaw.sources.source_bootstrap import enqueue_instagram_bootstrap
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "instagram.db")
    database.initialize()
    return database


def _media(*, scope: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "3712345678901234567",
        "code": "DAb_cd-123",
        "content_type": "reel",
        "url": "https://www.instagram.com/reel/DAb_cd-123/",
        "description": "Robotics reel",
        "cover_url": "https://scontent-lax3-2.cdninstagram.com/image.jpg",
        "author_id": "25025320",
        "author_name": "openai",
        "published_at": "2026-08-10T12:30:00Z",
    }
    if scope:
        item["scope"] = scope
    return item


def _following() -> dict[str, Any]:
    return {
        "scope": "instagram_following",
        "id": "25025320",
        "content_type": "user",
        "url": "https://www.instagram.com/openai/",
        "author_id": "25025320",
        "author_name": "openai",
    }


def _affirmative_debug(**extra: Any) -> dict[str, Any]:
    return {
        "response_observed": True,
        "terminal_evidence": "recognized_envelope_has_next_page_false",
        **extra,
    }


def test_instagram_discover_queue_preserves_provenance_and_candidates(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {
            "mode": "topic",
            "topic": "technology",
            "query": "technology",
            "source_keyword_id": 41,
            "max_items": 20,
            "max_pages": 3,
        },
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["mode"] == "topic"
    assert claimed["topic"] == "technology"
    assert claimed["source_keyword_id"] == 41
    claim_token = str(claimed["claim_token"])

    staged = queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=claim_token,
        items=[_media()],
        debug=_affirmative_debug(
            mode="topic",
            query="technology",
            source_keyword_id=41,
        ),
    )

    assert staged["status"] == "ok"
    assert staged["mode"] == "topic"
    assert staged["query"] == "technology"
    assert staged["source_keyword_id"] == 41
    assert staged["candidates"][0]["source_platform"] == "instagram"
    assert staged["candidates"][0]["source_keyword_id"] == 41
    assert staged["_openbiliclaw_terminal_status"] == "ok"
    assert queue.complete(task_id, claim_token=claim_token) is True


def test_instagram_zero_rows_require_affirmative_empty_evidence(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "technology", "max_items": 20, "max_pages": 3},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])

    with pytest.raises(ValueError, match="affirmative response evidence"):
        queue.stage_final_result(
            task_id,
            terminal_status="empty",
            claim_token=token,
            items=[],
            debug={"response_observed": False},
        )

    staged = queue.stage_final_result(
        task_id,
        terminal_status="empty",
        claim_token=token,
        items=[],
        debug=_affirmative_debug(),
    )
    assert staged["status"] == "empty"


def test_instagram_bootstrap_requires_numeric_current_account_and_full_scope_vector(
    tmp_path: Path,
) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "scopes": list(INSTAGRAM_BOOTSTRAP_SCOPES),
            "max_items_per_scope": 300,
            "max_pages_per_scope": 20,
        },
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])

    with pytest.raises(ValueError, match="numeric current account id"):
        queue.merge_result(
            task_id,
            claim_token=token,
            items=[_media(scope="instagram_liked")],
            account_id="not-a-number",
        )

    queue.merge_result(
        task_id,
        claim_token=token,
        items=[
            _media(scope="instagram_liked"),
            {**_media(scope="instagram_saved"), "id": "3712345678901234568"},
            _following(),
        ],
        account_id="123456789",
    )
    with pytest.raises(ValueError, match="every scope complete"):
        queue.stage_final_result(
            task_id,
            terminal_status="ok",
            claim_token=token,
            scope_complete={
                "instagram_liked": True,
                "instagram_saved": True,
                "instagram_following": False,
            },
            account_id="123456789",
            debug=_affirmative_debug(identity_verified=True),
        )

    staged = queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=token,
        scope_complete={scope: True for scope in INSTAGRAM_BOOTSTRAP_SCOPES},
        account_id="123456789",
        debug=_affirmative_debug(identity_verified=True),
    )
    assert staged["scope_counts"] == {
        "instagram_liked": 1,
        "instagram_saved": 1,
        "instagram_following": 1,
    }
    # The raw current-account id is used only to derive the opaque partition
    # key.  It must not be retained as its own canonical result field (media
    # ids may legitimately contain the same digit sequence as a substring).
    assert "account_id" not in staged
    assert staged["account_key"] == instagram_account_key("123456789")


def test_instagram_partial_retains_rows_without_claiming_complete(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id("bootstrap_events", {}, daily_budget=0)
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])
    staged = queue.stage_final_result(
        task_id,
        terminal_status="partial",
        claim_token=token,
        items=[_media(scope="instagram_liked")],
        scope_complete={
            "instagram_liked": True,
            "instagram_saved": False,
            "instagram_following": False,
        },
        account_id="123456789",
        debug=_affirmative_debug(status="rate_limited"),
    )

    assert staged["status"] == "partial"
    assert len(staged["items"]) == 1
    assert staged["scope_complete"]["instagram_saved"] is False
    assert queue.complete_staged_result(task_id, claim_token=token) is True
    assert queue.get(task_id)["status"] == "completed"  # type: ignore[index]


def test_instagram_first_final_wins_and_claim_token_is_mandatory(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "technology"},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])

    with pytest.raises(PermissionError, match="task_claim_conflict"):
        queue.merge_result(task_id, claim_token="wrong-token", items=[_media()])

    first = queue.stage_final_result(
        task_id,
        terminal_status="ok",
        claim_token=token,
        items=[_media()],
        debug=_affirmative_debug(),
    )
    late = queue.stage_final_result(
        task_id,
        terminal_status="failed",
        claim_token=token,
        error="late_failure",
        debug={"status": "challenge_required"},
    )
    assert late == first
    assert (
        queue.merge_result(
            task_id,
            claim_token=token,
            items=[{**_media(), "id": "3712345678901234569"}],
        )
        == []
    )


def test_instagram_queue_allows_one_fresh_cross_extension_lease(tmp_path: Path) -> None:
    database = _database(tmp_path)
    queue = InstagramTaskQueue(database)
    first = queue.enqueue_with_id("discover", {"mode": "topic", "topic": "ai"}, daily_budget=0)
    second = queue.enqueue_with_id(
        "discover", {"mode": "creator", "username": "openai"}, daily_budget=0
    )
    assert first is not None and second is not None
    assert queue.next_pending()["id"] == first  # type: ignore[index]
    assert queue.next_pending() is None
    database.conn.execute(
        "UPDATE instagram_tasks SET claimed_at='2000-01-01 00:00:00' WHERE id=?",
        (first,),
    )
    database.conn.commit()
    assert queue.next_pending()["id"] == first  # type: ignore[index]


def test_instagram_discover_admission_budgets_are_mode_specific(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    assert (
        queue.enqueue_with_id("discover", {"mode": "topic", "topic": "ai"}, daily_budget=1)
        is not None
    )
    assert (
        queue.enqueue_with_id("discover", {"mode": "topic", "topic": "robotics"}, daily_budget=1)
        is None
    )
    assert (
        queue.enqueue_with_id("discover", {"mode": "creator", "username": "openai"}, daily_budget=1)
        is not None
    )


def test_instagram_task_storage_rejects_raw_secrets_and_unknown_debug(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai", "cookie": "secret", "headers": {"x": "y"}},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])
    queue.stage_final_result(
        task_id,
        terminal_status="empty",
        claim_token=token,
        items=[],
        debug={
            **_affirmative_debug(status="challenge_required"),
            "cookie": "secret",
            "headers": {"authorization": "secret"},
            "raw_body": "secret",
        },
    )
    persisted = json.dumps(queue.get(task_id), ensure_ascii=False).casefold()
    assert "secret" not in persisted
    assert "cookie" not in persisted
    assert "raw_body" not in persisted
    assert "authorization" not in persisted
    assert "challenge_required" in persisted


def test_instagram_failure_diagnostics_are_frozen_machine_codes(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai"},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None
    token = str(claimed["claim_token"])

    secret = "sessionid=raw-secret; raw response: permission denied"
    with pytest.raises(ValueError, match="machine error code"):
        queue.stage_final_result(
            task_id,
            terminal_status="failed",
            claim_token=token,
            error=secret,
        )
    with pytest.raises(ValueError, match="debug failure code"):
        queue.stage_final_result(
            task_id,
            terminal_status="failed",
            claim_token=token,
            error="challenge_required",
            debug={"failures": [secret]},
        )

    unstaged = queue.get(task_id)
    assert unstaged is not None and unstaged["result_json"] is None
    persisted_unstaged = json.dumps(unstaged, ensure_ascii=False).casefold()
    assert "sessionid" not in persisted_unstaged
    assert "raw-secret" not in persisted_unstaged

    staged = queue.stage_final_result(
        task_id,
        terminal_status="failed",
        claim_token=token,
        error="instagram_saved:http_401",
        debug={
            "failures": ["instagram_saved:http_401", "challenge_required"],
            "sessionid": "raw-secret",
            "raw_response": "permission denied",
        },
    )
    serialized = json.dumps(staged, ensure_ascii=False).casefold()
    assert staged["error"] == "instagram_saved:http_401"
    assert staged["debug"]["failures"] == [
        "instagram_saved:http_401",
        "challenge_required",
    ]
    assert "sessionid" not in serialized
    assert "raw-secret" not in serialized
    assert "raw_response" not in serialized
    assert "permission denied" not in serialized


def test_instagram_failed_terminal_rejects_accepted_rows(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai"},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None

    with pytest.raises(ValueError, match="use partial"):
        queue.stage_final_result(
            task_id,
            terminal_status="failed",
            claim_token=str(claimed["claim_token"]),
            items=[_media()],
            error="rate_limited",
        )
    stored = queue.get(task_id)
    assert stored is not None and stored["result_json"] is None


def test_instagram_accepts_frozen_durable_partial_merge_evidence(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    task_id = queue.enqueue_with_id(
        "discover",
        {"mode": "topic", "topic": "ai"},
        daily_budget=0,
    )
    assert task_id is not None
    claimed = queue.next_pending()
    assert claimed is not None

    staged = queue.stage_final_result(
        task_id,
        terminal_status="partial",
        claim_token=str(claimed["claim_token"]),
        items=[_media()],
        error="task_idle_timeout",
        debug={
            "response_observed": True,
            "terminal_evidence": "durable_partial_progress_merged",
            "authenticated_topic_observed": True,
        },
    )

    assert staged["status"] == "partial"
    assert staged["items"]
    assert staged["debug"]["terminal_evidence"] == "durable_partial_progress_merged"
    assert staged["debug"]["authenticated_topic_observed"] is True
    assert normalize_instagram_failure_code("instagram_liked:http_error") == (
        "instagram_liked:http_error"
    )


def test_likes_progress_stall_retains_precise_machine_diagnostic() -> None:
    assert normalize_instagram_failure_code("instagram_liked:progress_stalled") == (
        "instagram_liked:progress_stalled"
    )


def test_instagram_events_are_account_partitioned_and_map_only_fixed_scopes() -> None:
    key = instagram_account_key("123456789")
    liked = _media(scope="instagram_liked")
    saved = {**_media(scope="instagram_saved"), "id": "3712345678901234568"}
    events = instagram_bootstrap_items_to_events(
        [liked, liked, saved, _following(), {**liked, "scope": "unknown"}],
        account_key=key,
    )

    assert [event["event_type"] for event in events] == ["like", "favorite", "follow"]
    assert all(event["metadata"]["account_key"] == key for event in events)
    assert instagram_bootstrap_item_key(liked, account_key=key).startswith(
        f"{key}:instagram_liked:reel:"
    )
    assert instagram_bootstrap_items_to_events([liked], account_key="raw-account-id") == []


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "search", "query": "ai"},
        {"mode": "creator", "username": "invalid username"},
        {"mode": "topic", "topic": ""},
    ],
)
def test_instagram_discover_task_payload_is_strict(tmp_path: Path, payload: dict[str, Any]) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    with pytest.raises(ValueError):
        queue.enqueue_with_id("discover", payload, daily_budget=0)


def test_instagram_bootstrap_scopes_cannot_be_narrowed_or_expanded(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    with pytest.raises(ValueError, match="fixed"):
        queue.enqueue_with_id(
            "bootstrap_events",
            {"scopes": ["instagram_liked", "instagram_saved"]},
            daily_budget=0,
        )
    with pytest.raises(ValueError, match="init-only"):
        queue.enqueue_with_id(
            "bootstrap_events",
            {"incremental": True},
            daily_budget=0,
        )


def test_instagram_bootstrap_freezes_bounded_request_interval(tmp_path: Path) -> None:
    queue = InstagramTaskQueue(_database(tmp_path))
    default_id = queue.enqueue_with_id("bootstrap_events", {}, daily_budget=0)
    assert default_id is not None
    assert queue.get(default_id)["request_interval_ms"] == 3_000  # type: ignore[index]

    low_id = queue.enqueue_with_id("bootstrap_events", {"request_interval_ms": 1}, daily_budget=0)
    high_id = queue.enqueue_with_id(
        "bootstrap_events", {"request_interval_ms": 99_999}, daily_budget=0
    )
    assert low_id is not None and high_id is not None
    assert queue.get(low_id)["request_interval_ms"] == 1_000  # type: ignore[index]
    assert queue.get(high_id)["request_interval_ms"] == 30_000  # type: ignore[index]


def test_instagram_bootstrap_registry_freezes_config_owned_caps(tmp_path: Path) -> None:
    database = _database(tmp_path)
    result = enqueue_instagram_bootstrap(
        database,
        config=SimpleNamespace(bootstrap_limit=42, request_interval_seconds=4),
        force=True,
        profile_update=True,
    )

    assert result.created is True
    assert result.task_id is not None
    task = InstagramTaskQueue(database).get(result.task_id)
    assert task is not None
    assert task["scopes"] == list(INSTAGRAM_BOOTSTRAP_SCOPES)
    assert task["max_items_per_scope"] == 42
    assert task["max_pages_per_scope"] == 20
    assert task["request_interval_ms"] == 4_000
    assert task["profile_update"] is True


def test_instagram_bootstrap_state_has_account_partition_and_seen_projection() -> None:
    state = default_source_bootstrap_state()
    assert source_bootstrap_state_key("instagram") == "instagram_seen_item_keys"
    assert source_bootstrap_state_key("ig") == "instagram_seen_item_keys"
    assert state["instagram_seen_item_keys"] == []
    assert state["instagram_account_key"] == ""
    assert (
        normalize_source_bootstrap_state({"instagram_account_key": "123456789"})[
            "instagram_account_key"
        ]
        == ""
    )
