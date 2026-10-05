from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

WORKTREE = Path(__file__).resolve().parents[1]


def _run_typescript(expression: str) -> Any:
    script = f"""
import {{
  buildSafeNativeSaveE2EResult,
  isAuthorizedNativeSaveE2ERequest,
}} from './extension/src/background/e2e-runner.ts';
const value = {expression};
process.stdout.write(JSON.stringify(value));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=WORKTREE,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_native_save_e2e_authorization_requires_exact_six_platform_mapping() -> None:
    requests = [
        {
            "allow_state_changing": True,
            "platform": "youtube",
            "action": "favorite",
            "content_id": "dQw4w9WgXcQ",
            "expected_target": "OpenBiliClaw",
        },
        {
            "allow_state_changing": True,
            "platform": "youtube",
            "action": "watch_later",
            "content_id": "dQw4w9WgXcQ",
            "expected_target": "YouTube Watch Later",
        },
        {
            "allow_state_changing": True,
            "platform": "xiaohongshu",
            "action": "favorite",
            "content_id": "64a1b2c3d4e5f6789012abcd",
            "expected_target": "小红书收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "xiaohongshu",
            "action": "watch_later",
            "content_id": "64a1b2c3d4e5f6789012abcd",
            "expected_target": "小红书收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "douyin",
            "action": "favorite",
            "content_id": "7234567890123456789",
            "expected_target": "抖音收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "douyin",
            "action": "watch_later",
            "content_id": "7234567890123456789",
            "expected_target": "抖音收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "twitter",
            "action": "favorite",
            "content_id": "1812345678901234567",
            "expected_target": "X Bookmarks",
        },
        {
            "allow_state_changing": True,
            "platform": "twitter",
            "action": "watch_later",
            "content_id": "1812345678901234567",
            "expected_target": "X Bookmarks",
        },
        {
            "allow_state_changing": True,
            "platform": "zhihu",
            "action": "favorite",
            "content_id": "answer:123456789",
            "expected_target": "知乎收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "zhihu",
            "action": "watch_later",
            "content_id": "answer:123456789",
            "expected_target": "知乎收藏",
        },
        {
            "allow_state_changing": True,
            "platform": "reddit",
            "action": "favorite",
            "content_id": "t3_public1",
            "expected_target": "Reddit Saved",
        },
        {
            "allow_state_changing": True,
            "platform": "reddit",
            "action": "watch_later",
            "content_id": "t3_public1",
            "expected_target": "Reddit Saved",
        },
    ]

    assert (
        _run_typescript(
            f"{json.dumps(requests, ensure_ascii=False)}.map(isAuthorizedNativeSaveE2ERequest)"
        )
        == [True] * 12
    )


def test_native_save_e2e_authorization_rejects_missing_mismatched_or_secret_input() -> None:
    base = {
        "allow_state_changing": True,
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
    }
    invalid = [
        {**base, "allow_state_changing": False},
        {key: value for key, value in base.items() if key != "allow_state_changing"},
        {key: value for key, value in base.items() if key != "platform"},
        {key: value for key, value in base.items() if key != "action"},
        {key: value for key, value in base.items() if key != "content_id"},
        {key: value for key, value in base.items() if key != "expected_target"},
        {**base, "expected_target": "OpenBiliClaw"},
        {**base, "platform": "bilibili"},
        {**base, "action": "delete"},
        {**base, "content_id": "https://reddit.com/post?token=secret"},
        {**base, "account_id": "private-user"},
        {**base, "cookie": "session=secret"},
        {**base, "token": "secret"},
        {**base, "html": "<main>private</main>"},
        {**base, "response_body": '{"private":true}'},
        {**base, "content_url": "https://reddit.com/r/test/comments/public?token=secret"},
    ]

    assert _run_typescript(
        f"{json.dumps(invalid, ensure_ascii=False)}.map(isAuthorizedNativeSaveE2ERequest)"
    ) == [False] * len(invalid)


def test_native_save_e2e_result_schema_records_only_safe_fields() -> None:
    request = {
        "allow_state_changing": True,
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
    }
    result = _run_typescript(
        "buildSafeNativeSaveE2EResult("
        f"{json.dumps(request, ensure_ascii=False)}, "
        "{task_status: 'already_synced', error_code: ''})"
    )

    assert result == {
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
        "task_status": "already_synced",
        "error_code": "",
    }
    assert set(result) == {
        "platform",
        "action",
        "content_id",
        "expected_target",
        "task_status",
        "error_code",
    }


def test_native_save_e2e_result_rejects_secret_or_raw_payload_fields() -> None:
    request = {
        "allow_state_changing": True,
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
    }
    unsafe_results = [
        {"task_status": "synced", "error_code": "", "account_id": "private-user"},
        {"task_status": "synced", "error_code": "", "cookie": "session=secret"},
        {"task_status": "synced", "error_code": "", "token": "secret"},
        {"task_status": "synced", "error_code": "", "html": "<main>private</main>"},
        {"task_status": "synced", "error_code": "", "response_body": "private"},
        {
            "task_status": "synced",
            "error_code": "",
            "url": "https://reddit.com/post?token=secret",
        },
    ]

    expression = (
        f"{json.dumps(unsafe_results, ensure_ascii=False)}"
        f".map((result) => buildSafeNativeSaveE2EResult({json.dumps(request)}, result))"
    )
    assert _run_typescript(expression) == [None] * len(unsafe_results)


def test_backend_native_save_e2e_models_cover_six_platforms_and_both_actions() -> None:
    from openbiliclaw.api.models import ExtensionNativeSaveE2EAuthorizationIn

    rows = [
        ("youtube", "favorite", "dQw4w9WgXcQ", "OpenBiliClaw"),
        ("youtube", "watch_later", "dQw4w9WgXcQ", "YouTube Watch Later"),
        ("xiaohongshu", "favorite", "64a1b2c3d4e5f6789012abcd", "小红书收藏"),
        ("xiaohongshu", "watch_later", "64a1b2c3d4e5f6789012abcd", "小红书收藏"),
        ("douyin", "favorite", "7234567890123456789", "抖音收藏"),
        ("douyin", "watch_later", "7234567890123456789", "抖音收藏"),
        ("twitter", "favorite", "1812345678901234567", "X Bookmarks"),
        ("twitter", "watch_later", "1812345678901234567", "X Bookmarks"),
        ("zhihu", "favorite", "answer:123456789", "知乎收藏"),
        ("zhihu", "watch_later", "answer:123456789", "知乎收藏"),
        ("reddit", "favorite", "t3_public1", "Reddit Saved"),
        ("reddit", "watch_later", "t3_public1", "Reddit Saved"),
    ]

    for platform, action, content_id, expected_target in rows:
        authorization = ExtensionNativeSaveE2EAuthorizationIn(
            allow_state_changing=True,
            platform=platform,
            action=action,
            content_id=content_id,
            expected_target=expected_target,
        )
        assert authorization.model_dump() == {
            "allow_state_changing": True,
            "platform": platform,
            "action": action,
            "content_id": content_id,
            "expected_target": expected_target,
        }


def test_backend_native_save_e2e_models_fail_closed_for_mixed_or_unsafe_payloads() -> None:
    from openbiliclaw.api.models import (
        ExtensionE2EResultIn,
        ExtensionE2ERunIn,
        ExtensionNativeSaveE2EAuthorizationIn,
    )

    authorization = {
        "allow_state_changing": True,
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
    }

    with pytest.raises(ValidationError):
        ExtensionNativeSaveE2EAuthorizationIn(**authorization, account_id="private-user")
    with pytest.raises(ValidationError):
        ExtensionNativeSaveE2EAuthorizationIn(
            **{**authorization, "expected_target": "OpenBiliClaw"}
        )
    with pytest.raises(ValidationError):
        ExtensionE2ERunIn(
            allow_state_changing=True,
            native_save_authorization=authorization,
            platforms=["reddit"],
            actions={"reddit": ["favorite"]},
        )
    with pytest.raises(ValidationError):
        ExtensionE2EResultIn(
            run_id="e2e-test",
            token="callback-token",
            native_save_result={
                "platform": "reddit",
                "action": "favorite",
                "content_id": "t3_public1",
                "expected_target": "Reddit Saved",
                "task_status": "failed",
                "error_code": "secret_token_abc123",
            },
        )
    with pytest.raises(ValidationError):
        ExtensionE2EResultIn(
            run_id="e2e-test",
            token="callback-token",
            native_save_result={
                "platform": "reddit",
                "action": "favorite",
                "content_id": "t3_public1",
                "expected_target": "Reddit Saved",
                "task_status": "synced",
                "error_code": "",
                "url": "https://reddit.com/?token=secret",
            },
        )


@pytest.mark.parametrize(
    "error_code",
    [
        "native_content_not_ready",
        "native_control_not_found",
        "native_dialog_not_opened",
        "native_target_not_found",
        "native_request_rejected",
        "native_confirmation_not_observed",
    ],
)
def test_backend_native_save_e2e_models_accept_safe_stage_codes(error_code: str) -> None:
    from openbiliclaw.api.models import ExtensionE2EResultIn

    result = ExtensionE2EResultIn(
        run_id="e2e-test",
        token="callback-token",
        native_save_result={
            "platform": "reddit",
            "action": "favorite",
            "content_id": "t3_public1",
            "expected_target": "Reddit Saved",
            "task_status": "failed",
            "error_code": error_code,
        },
    )

    assert result.native_save_result is not None
    assert result.native_save_result.error_code == error_code


def test_backend_native_save_e2e_run_publishes_only_dedicated_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    import openbiliclaw.api.app as app_module
    from openbiliclaw.api.app import create_app
    from openbiliclaw.saved_sync.models import NativeSaveRoute, SavedItemInput

    async def _instant_timeout(awaitable: object, timeout: float) -> object:
        del timeout
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> bool:
            self.events.append(event)
            return True

    class FakeMemoryManager:
        def query_events(self, **_kwargs: object) -> list[dict[str, object]]:
            return []

    class FakeSavedSyncService:
        def validate_native_save_selection(
            self, list_kind: str, item_key: str
        ) -> tuple[SavedItemInput, NativeSaveRoute]:
            assert list_kind == "watch_later"
            assert item_key == "youtube:dQw4w9WgXcQ"
            return (
                SavedItemInput(
                    source_platform="youtube",
                    content_id="dQw4w9WgXcQ",
                    content_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    content_type="video",
                ),
                NativeSaveRoute(
                    requested_action="watch_later",
                    resolved_action="watch_later",
                    resolved_target="YouTube Watch Later",
                ),
            )

    monkeypatch.setattr(app_module.asyncio, "wait_for", _instant_timeout)
    hub = FakeEventHub()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=object(),
        runtime_event_hub=hub,
    )
    app.state.auth_gate.is_trusted_local = lambda request: True
    app.state.runtime_context.saved_sync_service = FakeSavedSyncService()

    response = TestClient(app).post(
        "/api/extension/e2e/run",
        json={
            "allow_state_changing": True,
            "timeout_seconds": 5,
            "native_save_authorization": {
                "allow_state_changing": True,
                "platform": "youtube",
                "action": "watch_later",
                "content_id": "dQw4w9WgXcQ",
                "expected_target": "YouTube Watch Later",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["native_save_result"] is None
    assert hub.events[0]["platforms"] == []
    assert hub.events[0]["actions"] == {}
    assert hub.events[0]["native_save_authorization"] == {
        "allow_state_changing": True,
        "platform": "youtube",
        "action": "watch_later",
        "content_id": "dQw4w9WgXcQ",
        "expected_target": "YouTube Watch Later",
    }
    execution_deadline = hub.events[0]["native_save_execution_deadline_ms"]
    callback_deadline = hub.events[0]["native_save_callback_deadline_ms"]
    assert isinstance(execution_deadline, int)
    assert isinstance(callback_deadline, int)
    assert 900 <= callback_deadline - execution_deadline <= 1100


def test_backend_native_save_e2e_rejects_unverified_saved_identity_before_publish() -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> bool:
            self.events.append(event)
            return True

    class MissingSavedSyncService:
        def validate_native_save_selection(self, list_kind: str, item_key: str) -> object:
            del list_kind, item_key
            raise ValueError("membership missing")

    hub = FakeEventHub()
    app = create_app(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        runtime_event_hub=hub,
    )
    app.state.auth_gate.is_trusted_local = lambda request: True
    app.state.runtime_context.saved_sync_service = MissingSavedSyncService()

    response = TestClient(app).post(
        "/api/extension/e2e/run",
        json={
            "allow_state_changing": True,
            "timeout_seconds": 5,
            "native_save_authorization": {
                "allow_state_changing": True,
                "platform": "twitter",
                "action": "favorite",
                "content_id": "1812345678901234567",
                "expected_target": "X Bookmarks",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "native_save_authorization_not_saved_content"
    assert hub.events == []


@pytest.mark.parametrize(
    ("platform", "content_type", "content_id", "content_url"),
    [
        ("youtube", "video", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("youtube", "video", "dQw4w9WgXcQ", "https://youtube.com/shorts/dQw4w9WgXcQ/"),
        ("youtube", "video", "dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ/"),
        (
            "xiaohongshu",
            "note",
            "64a1b2c3d4e5f6789012abcd",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd",
        ),
        (
            "xiaohongshu",
            "video",
            "64a1b2c3d4e5f6789012abcd",
            "https://xiaohongshu.com/discovery/item/64a1b2c3d4e5f6789012abcd/",
        ),
        (
            "xiaohongshu",
            "note",
            "64a1b2c3d4e5f6789012abcd",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd"
            "?xsec_token=public-note-token&xsec_source=pc_feed",
        ),
        (
            "douyin",
            "aweme",
            "7234567890123456789",
            "https://www.douyin.com/video/7234567890123456789",
        ),
        ("douyin", "video", "7234567890123456789", "https://douyin.com/video/7234567890123456789/"),
        ("twitter", "tweet", "1812345678901234567", "https://x.com/i/status/1812345678901234567"),
        (
            "twitter",
            "status",
            "1812345678901234567",
            "https://twitter.com/openbiliclaw/status/1812345678901234567/",
        ),
        ("zhihu", "question", "question:123", "https://www.zhihu.com/question/123/"),
        (
            "zhihu",
            "answer",
            "answer:456",
            "https://www.zhihu.com/question/123/answer/456/",
        ),
        ("zhihu", "article", "article:789", "https://zhuanlan.zhihu.com/p/789"),
        ("reddit", "post", "t3_public1", "https://redd.it/public1"),
        (
            "reddit",
            "post",
            "t3_public1",
            "https://www.reddit.com/r/test/comments/public1/title/",
        ),
        (
            "reddit",
            "comment",
            "t1_comment1",
            "https://reddit.com/r/test/comments/public1/title/comment1/",
        ),
    ],
)
def test_native_save_e2e_preflight_accepts_executor_equivalent_canonical_urls(
    platform: str,
    content_type: str,
    content_id: str,
    content_url: str,
) -> None:
    from openbiliclaw.api.app import _native_save_e2e_content_id_from_url

    assert _native_save_e2e_content_id_from_url(platform, content_type, content_url) == content_id


@pytest.mark.parametrize(
    ("platform", "content_type", "content_url"),
    [
        ("youtube", "video", "https://www.youtube.com/channel/x?v=dQw4w9WgXcQ"),
        ("youtube", "video", "https://youtu.be/dQw4w9WgXcQ/extra"),
        ("youtube", "video", "https://m.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("youtube", "video", "https://youtube.com/watch?v=dQw4w9WgXcQ&v=dQw4w9WgXcQ"),
        (
            "xiaohongshu",
            "note",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd/extra",
        ),
        (
            "xiaohongshu",
            "note",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd?token=secret",
        ),
        (
            "xiaohongshu",
            "note",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd?xsec_token=",
        ),
        (
            "xiaohongshu",
            "note",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd"
            "?xsec_token=public-note-token&xsec_token=duplicate",
        ),
        (
            "xiaohongshu",
            "note",
            "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6789012abcd"
            "?xsec_token=public-note-token&xsec_source=",
        ),
        ("douyin", "video", "https://www.douyin.com/video/7234567890123456789/extra"),
        ("douyin", "video", "https://www.iesdouyin.com/video/7234567890123456789"),
        (
            "twitter",
            "tweet",
            "https://x.com/arbitrary/nested/status/1812345678901234567",
        ),
        ("twitter", "tweet", "https://x.com/i/status/1812345678901234567?token=secret"),
        ("zhihu", "answer", "https://www.zhihu.com/question/123/answer/456/extra"),
        ("zhihu", "article", "https://evil.zhihu.com/p/789"),
        ("reddit", "post", "https://redd.it/public1/extra"),
        ("reddit", "comment", "https://reddit.com/r/test/comments/public1/title"),
        ("reddit", "post", "https://reddit.com/r/test/comments/public1/title#private"),
        ("reddit", "post", "https://user:pass@reddit.com/r/test/comments/public1/title"),
        ("reddit", "post", "https://reddit.com:443/r/test/comments/public1/title"),
    ],
)
def test_native_save_e2e_preflight_rejects_non_executor_urls(
    platform: str,
    content_type: str,
    content_url: str,
) -> None:
    from openbiliclaw.api.app import _native_save_e2e_content_id_from_url

    assert _native_save_e2e_content_id_from_url(platform, content_type, content_url) == ""


def test_native_save_e2e_preflight_requires_exact_fallback_resolved_action() -> None:
    from openbiliclaw.api.app import _native_save_e2e_membership_matches
    from openbiliclaw.api.models import ExtensionNativeSaveE2EAuthorizationIn
    from openbiliclaw.saved_sync.models import NativeSaveRoute, SavedItemInput

    authorization = ExtensionNativeSaveE2EAuthorizationIn(
        allow_state_changing=True,
        platform="reddit",
        action="watch_later",
        content_id="t3_public1",
        expected_target="Reddit Saved",
    )
    item = SavedItemInput(
        source_platform="reddit",
        content_id="t3_public1",
        content_url="https://reddit.com/r/test/comments/public1/title/",
        content_type="post",
    )
    wrong_route = NativeSaveRoute(
        requested_action="watch_later",
        resolved_action="watch_later",
        resolved_target="Reddit Saved",
    )

    assert not _native_save_e2e_membership_matches(authorization, item, wrong_route)


def test_backend_native_save_e2e_callback_requires_exact_authorization_correlation() -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.api.models import ExtensionNativeSaveE2EAuthorizationIn

    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    app.state.auth_gate.is_trusted_local = lambda request: True
    authorization = ExtensionNativeSaveE2EAuthorizationIn(
        allow_state_changing=True,
        platform="reddit",
        action="favorite",
        content_id="t3_public1",
        expected_target="Reddit Saved",
    )
    event = SimpleNamespace(set=lambda: None)
    state = SimpleNamespace(
        token="callback-token",
        native_save_authorization=authorization,
        extension_result=None,
        event=event,
    )
    app.state.extension_e2e_runs["e2e-native"] = state
    client = TestClient(app)

    mismatch = client.post(
        "/api/extension/e2e/result",
        json={
            "run_id": "e2e-native",
            "token": "callback-token",
            "native_save_result": {
                "platform": "reddit",
                "action": "favorite",
                "content_id": "t3_different",
                "expected_target": "Reddit Saved",
                "task_status": "synced",
                "error_code": "",
            },
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == "native_save_result_mismatch"
    assert state.extension_result is None

    accepted = client.post(
        "/api/extension/e2e/result",
        json={
            "run_id": "e2e-native",
            "token": "callback-token",
            "native_save_result": {
                "platform": "reddit",
                "action": "favorite",
                "content_id": "t3_public1",
                "expected_target": "Reddit Saved",
                "task_status": "already_synced",
                "error_code": "",
            },
        },
    )
    assert accepted.status_code == 200
    assert state.extension_result.native_save_result.model_dump() == {
        "platform": "reddit",
        "action": "favorite",
        "content_id": "t3_public1",
        "expected_target": "Reddit Saved",
        "task_status": "already_synced",
        "error_code": "",
    }
