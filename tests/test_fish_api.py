"""Blue Fish Lab HTTP surface — routes, clamping and credential isolation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture()
async def client(tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    # A minimal but valid LLM section keeps the app out of degraded mode, so
    # the fish routes are exercised through the normal request path.
    (tmp_path / "config.toml").write_text(
        '[llm]\ndefault_provider = "deepseek"\n\n'
        '[llm.deepseek]\napi_key = "sk-test"\nmodel = "deepseek-chat"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_FISH_LOOP_DISABLED", "1")
    from openbiliclaw.api.app import create_app

    app = create_app()
    # httpx's ASGITransport presents a loopback peer, which is what the
    # credential gate requires for write routes.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as http:
        yield http


async def test_config_defaults_are_returned(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/config")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["config"]["enabled"] is False
    assert body["config"]["dry_run"] is True


async def test_config_update_persists_and_clamps(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/api/fish/config",
        json={
            "enabled": True,
            "dry_run": False,
            "max_chars": 99_999,
            "daily_limit": 2,
            "notify_owner": True,
            "notify_daily_limit": 99,
            "owner_name": "主" * 40,
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert config["enabled"] is True
    assert config["daily_limit"] == 2
    assert config["max_chars"] == 300
    assert config["notify_owner"] is True
    assert config["notify_daily_limit"] == 3
    assert len(config["owner_name"]) == 30

    again = (await client.get("/api/fish/config")).json()["config"]
    assert again == config


async def test_config_update_accepts_nested_payload(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/fish/config", json={"config": {"persona_name": "小蓝"}})

    assert response.status_code == 200
    assert response.json()["config"]["persona_name"] == "小蓝"


async def test_empty_cookie_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/fish/account", json={"cookie": "   "})

    assert response.status_code == 400
    assert response.json()["ok"] is False


async def test_ledger_starts_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/ledger")

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_status_reports_config_and_account(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/status")

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["enabled"] is False
    assert body["account"]["authenticated"] is False
    assert body["state"]["level"] == 1


async def test_run_without_login_is_refused(client: httpx.AsyncClient) -> None:
    await client.put("/api/fish/config", json={"enabled": True, "dry_run": False})

    response = await client.post("/api/fish/run", json={"dry_run": False})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == "not_logged_in"


async def test_config_file_is_written_under_fish_home(client: httpx.AsyncClient, tmp_path) -> None:
    await client.put("/api/fish/config", json={"persona_name": "小蓝"})

    path = tmp_path / "data" / "fish" / "config.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["persona_name"] == "小蓝"


async def test_fish_cookie_never_touches_the_main_account_file(
    client: httpx.AsyncClient, tmp_path
) -> None:
    await client.post("/api/fish/account", json={"cookie": "  "})

    assert not (tmp_path / "data" / "fish" / "bilibili_cookie.json").exists()
    assert not (tmp_path / "data" / "bilibili_cookie.json").exists()


async def test_fish_publish_never_touches_the_owners_profile(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The fish's feedback is split from the owner's feedback.

    Posting a comment must not project recommendation feedback, must not fire
    the cognition hook, and must leave the recommendation actionable — the
    owner's profile and recommendation list simply do not learn from the fish.
    """
    from openbiliclaw.api.app import create_app
    from openbiliclaw.bilibili.api import BilibiliAPIClient, CommentInfo
    from openbiliclaw.fish.account import FishAccount, FishAccountStatus
    from openbiliclaw.fish.engine import BlueFishEngine
    from openbiliclaw.soul.engine import SoulEngine

    (tmp_path / "config.toml").write_text(
        '[llm]\ndefault_provider = "deepseek"\n\n'
        '[llm.deepseek]\napi_key = "sk-test"\nmodel = "deepseek-chat"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_FISH_LOOP_DISABLED", "1")

    cognitions: list[tuple[str, str, str]] = []
    posted: list[str] = []

    def _cognition(self: SoulEngine, *, feedback_type: str, title: str, note: str = "") -> None:
        cognitions.append((feedback_type, title, note))

    async def _account_status(
        self: FishAccount, *, main_mid: int = 0, main_cookie: str = ""
    ) -> FishAccountStatus:
        return FishAccountStatus(
            has_cookie=True,
            authenticated=True,
            mid=222,
            uname="大肥鱼",
            message="",
            isolated=True,
        )

    async def _comments(self: BilibiliAPIClient, bvid: str, limit: int = 20) -> list[CommentInfo]:
        return [
            CommentInfo(
                mid=501,
                uname="小白",
                message="这个转场我反复看了三遍",
                like_count=42,
                rpid=1001,
            )
        ]

    async def _post(
        self: BilibiliAPIClient,
        bvid: str,
        *,
        message: str,
        root: int | None = None,
        parent: int | None = None,
    ) -> dict[str, object]:
        posted.append(message)
        return {"rpid": 9001}

    async def _close(self: BilibiliAPIClient) -> None:
        return None

    async def _ask_model(
        self: BlueFishEngine, messages: list[dict[str, str]], *, config: object
    ) -> tuple[str, int, str, str]:
        return "reply", 1001, "那个空镜确实值得倒回去看", "有具体细节"

    monkeypatch.setattr(SoulEngine, "record_immediate_feedback_cognition", _cognition)
    monkeypatch.setattr(FishAccount, "status", _account_status)
    monkeypatch.setattr(FishAccount, "load_cookie", lambda self: "SESSION=fish-only")
    monkeypatch.setattr(BilibiliAPIClient, "get_video_comments", _comments)
    monkeypatch.setattr(BilibiliAPIClient, "post_comment", _post)
    monkeypatch.setattr(BilibiliAPIClient, "close", _close)
    monkeypatch.setattr(BlueFishEngine, "_ask_model", _ask_model)

    app = create_app()
    ctx = app.state.runtime_context
    recommendation_id = ctx.database.insert_recommendation(
        "BV1xx411c7mD",
        confidence=0.9,
        topic="剪辑",
        expression="你可能会喜欢这条的镜头语言",
    )

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    )
    enabled = await client.put("/api/fish/config", json={"enabled": True, "dry_run": False})
    assert enabled.status_code == 200

    response = await client.post("/api/fish/run", json={"dry_run": False})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "published", body
    assert body["ok"] is True
    assert posted == ["@小白 那个空镜确实值得倒回去看"]
    assert body["record"]["recommendation_id"] == recommendation_id
    assert "memory_recorded" not in body["record"]

    # Nothing reached the owner's side of the feedback line.
    assert cognitions == []
    row = next(
        item
        for item in ctx.database.get_recommendations(limit=10)
        if int(item["id"]) == recommendation_id
    )
    assert not row.get("feedback_type")
    assert not row.get("feedback_note")
    actionable = ctx.database.get_recommendations(limit=10, exclude_processed=True)
    assert any(int(item["id"]) == recommendation_id for item in actionable)

    # The fish's own ledger is the only record of the action.
    ledger = (tmp_path / "data" / "fish" / "ledger.jsonl").read_text(encoding="utf-8")
    assert f'"recommendation_id": {recommendation_id}' in ledger
    await client.aclose()


# ── QR login ───────────────────────────────────────────────────────────


def _qr_cookie_url(cookie: str = "SESSDATA=fish%2Csess; bili_jct=csrf; DedeUserID=222") -> str:
    return f"https://passport.biligame.com/crossDomain?{cookie.replace('; ', '&')}"


def _patch_qr(
    monkeypatch,
    *,
    poll_status: str = "confirmed",
    isolate: bool = True,
    message: str = "",
) -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.fish.account import FishAccount, FishAccountStatus

    async def _generate(self):  # type: ignore[no-untyped-def]
        return {
            "qrcode_key": "key-1",
            "url": "https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=key-1",
        }

    async def _poll(self, qrcode_key: str):  # type: ignore[no-untyped-def]
        return {
            "status": poll_status,
            "message": message,
            "url": _qr_cookie_url() if poll_status == "confirmed" else "",
            "qrcode_key": qrcode_key,
        }

    async def _status(self, *, main_mid: int = 0, main_cookie: str = ""):  # type: ignore[no-untyped-def]
        return FishAccountStatus(
            has_cookie=True,
            authenticated=True,
            mid=222,
            uname="大肥鱼",
            isolated=isolate,
            message=message,
        )

    monkeypatch.setattr(BilibiliAPIClient, "generate_qrcode", _generate)
    monkeypatch.setattr(BilibiliAPIClient, "poll_qrcode", _poll)
    monkeypatch.setattr(FishAccount, "status", _status)


async def test_qr_login_returns_a_scannable_svg(client, monkeypatch) -> None:
    _patch_qr(monkeypatch)

    response = await client.post("/api/fish/account/qrcode")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["qrcode_key"] == "key-1"
    assert body["qrcode_image"].startswith("data:image/svg+xml;base64,")
    assert body["expires_in"] == 180


async def test_qr_login_saves_an_isolated_account(client, tmp_path, monkeypatch) -> None:
    _patch_qr(monkeypatch)

    response = await client.get("/api/fish/account/qrcode/poll", params={"qrcode_key": "key-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "logged_in"
    assert payload["account"]["mid"] == 222
    cookie_file = tmp_path / "data" / "fish" / "bilibili_cookie.json"
    assert cookie_file.exists()
    assert "SESSDATA=fish,sess" in cookie_file.read_text(encoding="utf-8")


async def test_qr_login_rejects_the_owner_account(client, tmp_path, monkeypatch) -> None:
    _patch_qr(monkeypatch, isolate=False, message="大肥鱼必须是一个独立的小号")

    response = await client.get("/api/fish/account/qrcode/poll", params={"qrcode_key": "key-1"})

    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == "rejected"
    assert "独立" in payload["message"]
    assert not (tmp_path / "data" / "fish" / "bilibili_cookie.json").exists()


async def test_qr_login_pending_writes_no_cookie(client, tmp_path, monkeypatch) -> None:
    _patch_qr(monkeypatch, poll_status="pending")

    response = await client.get("/api/fish/account/qrcode/poll", params={"qrcode_key": "key-1"})

    assert response.json()["status"] == "pending"
    assert not (tmp_path / "data" / "fish" / "bilibili_cookie.json").exists()
