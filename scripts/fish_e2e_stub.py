#!/usr/bin/env python3
"""End-to-end smoke for the Blue Fish Lab without real credentials.

Runs the *real* stack — FastAPI app, runtime context, comment-thread discovery
route, candidate pipeline, engine, `BilibiliAPIClient` over HTTP, SQLite,
ledger, plans and the daily report — against a local stub that answers the
handful of B站 endpoints the fish touches, plus an OpenAI-compatible stub for
the single model call.

What it proves, in order:

1. the real client logs the fish account in through `/x/web-interface/nav`
   and the isolation rule accepts a different account;
2. before a soul profile exists the discovery route exits quietly with 0;
3. with a profile it turns a comment mention into an evaluable candidate and
   leaves a reply plan behind;
4. the reply round consumes that plan and does **not** fetch the comments
   again;
5. owner notices work through the comment @ / private message / both channels;
6. the ledger, the daily report and the owner's recommendation row all stay
   consistent.

Usage:
    python scripts/fish_e2e_stub.py            # ephemeral data dir
    python scripts/fish_e2e_stub.py --keep     # print the dir and keep it
    python scripts/fish_e2e_stub.py --port 8951
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

SEED = "BV1xx411c7mD"
MENTIONED = "BV1yy411c7mE"
TITLE = "为什么你的 vlog 没人看"
FISH_COOKIE = "SESSDATA=FISH; bili_jct=csrf; DedeUserID=222"
OWNER_COOKIE = "SESSDATA=OWNER; bili_jct=csrf; DedeUserID=111"
DECISION = {
    "action": "reply",
    "target_rpid": 1001,
    "reply": "那个 0.5 秒的空镜我也倒回去看了两遍",
    "reason": "有具体细节可接",
    # The notice call parses the same payload; the fake answers both tasks.
    "notice": "这个院子才 55 平，我截图截了三张。",
}


class StubState:
    """What the stub saw. Assertions read this, not the app's logs."""

    def __init__(self) -> None:
        self.comments: list[dict[str, str]] = []
        self.messages: list[dict[str, str]] = []
        self.comment_fetches = 0
        self.view_bvids: list[str] = []
        self.llm_calls = 0


def _nav(cookie: str) -> dict[str, Any]:
    if "FISH" in cookie.upper():
        return {"code": 0, "data": {"isLogin": True, "mid": 222, "uname": "大肥鱼"}}
    return {"code": 0, "data": {"isLogin": True, "mid": 111, "uname": "主人"}}


def _view(bvid: str) -> dict[str, Any]:
    aid = 1001 if bvid == SEED else 2002
    return {
        "code": 0,
        "data": {
            "aid": aid,
            "bvid": bvid,
            "title": TITLE if bvid == SEED else f"{MENTIONED} 的标题",
            "desc": "简介",
            "owner": {"name": "UP主", "mid": 9},
            "stat": {
                "view": 50_000,
                "like": 120,
                "favorite": 10,
                "danmaku": 5,
                "reply": 3,
                "share": 1,
            },
            "pubdate": int(time.time()) - 3 * 86400,
            "cid": 7,
            "tid": 0,
            "duration": 300,
            "pic": "https://i0.hdslb.com/bfs/archive/x.jpg",
        },
    }


def _replies() -> dict[str, Any]:
    now = int(time.time())
    return {
        "code": 0,
        "data": {
            "replies": [
                {
                    "rpid": 1001,
                    "oid": 1001,
                    "root": 1001,
                    "parent": 0,
                    "like": 42,
                    "ctime": now - 3600,
                    "member": {"uname": "小白", "mid": 501},
                    "content": {"message": "这个转场我反复看了三遍"},
                },
                {
                    "rpid": 1002,
                    "oid": 1001,
                    "root": 1002,
                    "parent": 0,
                    "like": 18,
                    "ctime": now - 1800,
                    "member": {"uname": "阿橘", "mid": 502},
                    "content": {"message": f"建议先看 {MENTIONED}，讲得更细"},
                },
            ],
            "cursor": {"is_end": True, "next": 0},
        },
    }


def _make_handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # noqa: ANN002 - quiet stub
            return

        def _send(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path, _, query = self.path.partition("?")
            params = parse_qs(query)
            if path == "/x/web-interface/nav":
                return self._send(_nav(self.headers.get("cookie", "")))
            if path == "/x/web-interface/view":
                bvid = (params.get("bvid") or [""])[0]
                state.view_bvids.append(bvid)
                return self._send(_view(bvid))
            if path == "/x/v2/reply/main":
                state.comment_fetches += 1
                return self._send(_replies())
            if path == "/x/msgfeed/reply":
                return self._send({"code": 0, "data": {"items": []}})
            if path == "/x/msgfeed/unread":
                return self._send({"code": 0, "data": {}})
            return self._send({"code": 0, "data": {}})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = self.path.partition("?")[0]
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            if path == "/v1/chat/completions":
                state.llm_calls += 1
                return self._send(
                    {
                        "id": "chatcmpl-1",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "deepseek-chat",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(DECISION, ensure_ascii=False),
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 10,
                            "total_tokens": 20,
                        },
                    }
                )
            form = {key: values[0] for key, values in parse_qs(raw).items()}
            if path == "/x/v2/reply/add":
                state.comments.append(form)
                return self._send({"code": 0, "data": {"rpid": 9001, "rpid_str": "9001"}})
            if path == "/web_im/v1/web_im/send_msg":
                state.messages.append(form)
                return self._send({"code": 0, "data": {"msg_key": 1}})
            return self._send({"code": 0, "data": {}})

    return Handler


def _expect(condition: bool, message: str, failures: list[str]) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


async def _run(port: int, data_dir: Path) -> int:
    base = f"http://127.0.0.1:{port}"
    state = StubState()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    (data_dir / "config.toml").write_text(
        '[llm]\ndefault_provider = "deepseek"\n\n'
        '[llm.deepseek]\napi_key = "sk-test"\nmodel = "deepseek-chat"\n'
        f'base_url = "{base}/v1"\n\n'
        f'[bilibili]\ncookie = "{OWNER_COOKIE}"\n',
        encoding="utf-8",
    )
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(data_dir)
    os.environ["OPENBILICLAW_FISH_LOOP_DISABLED"] = "1"

    from openbiliclaw.bilibili import api as bili_api

    bili_api.BilibiliAPIClient._BASE_URL = base
    bili_api.BilibiliAPIClient._VC_BASE_URL = base

    import httpx

    from openbiliclaw.api.app import create_app

    app = create_app()
    ctx = app.state.runtime_context
    ctx.database.cache_content(
        SEED,
        title=TITLE,
        description="一支讲 vlog 节奏的片子",
        up_name="UP主",
        source_platform="bilibili",
        view_count=50_000,
        like_count=120,
        published_at="2026-10-17T10:00:00Z",
    )
    recommendation_id = ctx.database.insert_recommendation(
        SEED, confidence=0.9, topic="剪辑", expression="镜头语言"
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)
    failures: list[str] = []

    config = await client.put(
        "/api/fish/config",
        json={"enabled": True, "dry_run": False, "min_interval_minutes": 5, "daily_limit": 5},
    )
    _expect(config.status_code == 200, "config saved", failures)

    account = await client.post("/api/fish/account", json={"cookie": FISH_COOKIE})
    payload = account.json()
    _expect(
        account.status_code == 200 and payload.get("ok") is True,
        f"fish account accepted ({payload.get('message')})",
        failures,
    )

    skipped = await ctx.run_discovery_route(strategies=["comment_thread"], limit=3)
    _expect(skipped == 0, "discovery route exits 0 before the soul profile exists", failures)

    items = await ctx.discovery_engine.produce_candidates(
        SimpleNamespace(), strategies=["comment_thread"], limit=3
    )
    _expect(
        [item.bvid for item in items] == [MENTIONED],
        f"comment mention became a candidate ({[item.bvid for item in items]})",
        failures,
    )
    inserted = ctx.candidate_pipeline.enqueue_candidates(items) if items else 0
    _expect(inserted >= 1, "candidate enqueued into the eval pipeline", failures)

    plans_path = data_dir / "data" / "fish" / "plans.json"
    plans = (
        json.loads(plans_path.read_text(encoding="utf-8"))["plans"] if plans_path.exists() else []
    )
    _expect(
        bool(plans) and plans[-1]["bvid"] == SEED and len(plans[-1]["comments"]) == 2,
        "reply plan left behind with the comment snapshot",
        failures,
    )

    fetches_before = state.comment_fetches
    run = (await client.post("/api/fish/run", json={"dry_run": False})).json()
    _expect(run.get("status") == "published", f"reply published ({run.get('status')})", failures)
    _expect(
        state.comment_fetches == fetches_before,
        "reply reused the plan snapshot (no second comment fetch)",
        failures,
    )
    _expect(
        bool(state.comments)
        and state.comments[-1]["message"].startswith("@小白 ")
        and state.comments[-1]["root"] == "1001"
        and state.comments[-1]["parent"] == "1001",
        "comment posted as a thread reply with the @ prefix",
        failures,
    )

    await client.put("/api/fish/config", json={"notify_owner": True, "notify_channel": "dm"})
    run = (await client.post("/api/fish/run", json={"dry_run": False})).json()
    dm_text = state.messages[-1]["msg[content]"] if state.messages else ""
    _expect(run.get("status") == "notified", f"DM notice sent ({run.get('status')})", failures)
    _expect(
        bool(state.messages)
        and state.messages[-1]["msg[receiver_id]"] == "111"
        and DECISION["notice"] in dm_text
        and f"https://www.bilibili.com/video/{SEED}" in dm_text,
        "DM carries the generated line and the video link",
        failures,
    )

    await client.put(
        "/api/fish/config",
        json={"notify_channel": "both", "notify_daily_limit": 2},
    )
    run = (await client.post("/api/fish/run", json={"dry_run": False})).json()
    status = (await client.get("/api/fish/status")).json()
    deliveries = [row.get("delivery") for row in status["recent"]]
    _expect(
        run.get("status") == "notified", f"both-channel notice sent ({run.get('status')})", failures
    )
    _expect(deliveries[:1] == ["comment+dm"], f"ledger records delivery ({deliveries})", failures)

    report = status.get("today_report") or ""
    _expect(
        report.count("\n- ") == 3 and "一条测试" not in report,
        f"daily report has three bullets ({report.count(chr(10) + '- ')})",
        failures,
    )

    row = next(
        (
            item
            for item in ctx.database.get_recommendations(limit=10)
            if int(item["id"]) == recommendation_id
        ),
        {},
    )
    _expect(
        not row.get("feedback_type") and bool(row),
        "owner recommendation row untouched by the fish",
        failures,
    )
    _expect(
        state.llm_calls == 3, f"one model call per reply / notice ({state.llm_calls})", failures
    )

    await client.aclose()
    server.shutdown()
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nE2E OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8941)
    parser.add_argument("--keep", action="store_true", help="keep and print the temp data dir")
    args = parser.parse_args()
    data_dir = Path(tempfile.mkdtemp(prefix="fish-e2e-"))
    print(f"data dir: {data_dir}")
    try:
        return asyncio.run(_run(args.port, data_dir))
    finally:
        if not args.keep:
            import shutil

            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
