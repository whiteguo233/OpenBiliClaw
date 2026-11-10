"""Verification frames offer explicit consent without promoting ordinary embeds."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database


def verification(**changes):
    return {
        "title": "请稍候…",
        "elements": [{"tag": "h2", "text": "正在进行安全验证"}],
        "resource_failures": [{"resource_type": "Document", "status": 403}],
        "blocked_navigation_origins": [],
        "blocked_frame_origins": ["https://challenges.cloudflare.com"],
        **changes,
    }


def test_only_affirmative_verification_frames_are_suggested():
    from openbiliclaw.sources.browser_workflow import browser_login_origins

    assert browser_login_origins(verification()) == ["https://challenges.cloudflare.com"]
    assert browser_login_origins(verification(title="Normal article")) == []
    assert browser_login_origins(verification(resource_failures=[])) == []
    assert (
        browser_login_origins(
            verification(
                blocked_frame_origins=[], blocked_resource_origins=["https://cdn.example.com"]
            )
        )
        == []
    )
    assert browser_login_origins({"blocked_navigation_origins": ["https://login.example.com"]}) == [
        "https://login.example.com"
    ]


def test_suggestions_are_bounded_exact_public_origins():
    from openbiliclaw.sources.browser_workflow import browser_login_origins

    invalid = [
        None,
        {},
        "http://127.0.0.1",
        "https://user:secret@example.com",
        "https://a.example/path",
    ]
    origins = [f"https://login{i}.example.com" for i in range(12)]
    assert (
        browser_login_origins(verification(blocked_navigation_origins=invalid + origins + origins))
        == origins[:8]
    )
    assert (
        browser_login_origins(
            verification(
                blocked_navigation_origins="https://login.example.com", blocked_frame_origins={}
            )
        )
        == []
    )


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "scope.db")
    db.initialize()
    yield WorkflowService(db)
    db.close()


def pending(service):
    return service.store.save_draft(
        {
            "entry_url": "https://example.com/",
            "origin": "https://example.com",
            "name": "Example",
            "intent": "Read latest articles",
            "source_id": "",
            "executor": "ego",
            "session_id": "scope-test",
            "workflow": None,
            "status": "needs_login",
            "trial_success": False,
        }
    )


async def test_observation_records_frame_suggestion_without_authorizing_it(service):
    draft = pending(service)
    session = SimpleNamespace(snapshot=AsyncMock(return_value=verification()), close=AsyncMock())
    executor = SimpleNamespace(open_session=AsyncMock(return_value=session))
    service._executor_factory = lambda name: executor
    _, observation = await service.observe_session(draft, interactive=True)
    assert observation["suggested_login_origins"] == ["https://challenges.cloudflare.com"]
    saved = service.store.get_draft(draft["draft_id"])
    assert saved["suggested_login_origins"] == observation["suggested_login_origins"]
    assert not saved.get("login_origins")
    assert executor.open_session.call_args.args[0].auth.login_origins == ()
    assert service.list_sources() == []


def test_takeover_refresh_and_actions_refresh_scopes_without_auto_consent(service):
    draft = pending(service)
    page = verification()
    session = SimpleNamespace(
        snapshot=AsyncMock(side_effect=lambda: dict(page)),
        act=AsyncMock(side_effect=lambda action: dict(page)),
        close=AsyncMock(),
    )
    observed = []

    async def observe(value, *, interactive):
        observed.append(dict(value))
        return session, dict(page)

    service.observe_session = observe
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        path = f"/api/custom-sources/drafts/{draft['draft_id']}"
        first = client.post(path + "/takeover", json={}).json()
        assert first["suggested_login_origins"] == ["https://challenges.cloudflare.com"]
        assert first["blocked_navigation_origins"] == []
        assert not observed[-1].get("login_origins")
        assert (
            client.post(
                path + "/takeover", json={"login_origin": "https://unknown.example.com"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                path + "/takeover", json={"login_origin": "https://challenges.cloudflare.com"}
            ).status_code
            == 200
        )
        assert observed[-1]["login_origins"] == ["https://challenges.cloudflare.com"]
        assert observed[-1]["origin"] == "https://example.com"
        page.update(title="Real articles", blocked_frame_origins=["https://ads.example.com"])
        assert client.post(path + "/takeover", json={}).json()["suggested_login_origins"] == []
        assert client.get(path).json()["suggested_login_origins"] == []
        page.update(verification(blocked_frame_origins=["https://verify.example.com"]))
        assert client.post(path + "/action", json={"action": "key", "key": "Tab"}).json()[
            "suggested_login_origins"
        ] == ["https://verify.example.com"]
        assert client.get(path).json()["suggested_login_origins"] == ["https://verify.example.com"]
        assert client.post(path + "/activate", json={}).status_code == 422
        assert service.list_sources() == []
