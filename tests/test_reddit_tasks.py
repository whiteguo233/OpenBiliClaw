from __future__ import annotations

import json
import os
import subprocess

import openbiliclaw.sources.reddit_tasks as reddit_tasks
from openbiliclaw.sources.reddit_tasks import (
    RedditCommandStatus,
    RedditTaskQueue,
    build_reddit_command,
    parse_reddit_command_output,
    probe_reddit_command_backend,
    recent_reddit_related_urls,
    recent_reddit_subreddits,
    reddit_items_to_contents,
    reddit_items_to_events,
    sync_rdt_credential_from_cookie_header,
)


def test_parse_reddit_command_output_accepts_json_wrappers() -> None:
    output = """
{
  "items": [
    {
      "id": "abc123",
      "title": "Local-first agents",
      "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
      "subreddit": "LocalLLaMA",
      "author": "agent_builder",
      "score": 42,
      "num_comments": 7,
      "selftext": "A practical write-up."
    }
  ]
}
"""

    items = parse_reddit_command_output(output)

    assert items == [
        {
            "id": "abc123",
            "title": "Local-first agents",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
            "subreddit": "LocalLLaMA",
            "author": "agent_builder",
            "score": 42,
            "num_comments": 7,
            "selftext": "A practical write-up.",
        }
    ]


def test_parse_reddit_command_output_flattens_rdt_read_payload() -> None:
    output = """
{
  "ok": true,
  "schema_version": "1",
  "data": [
    {
      "data": {
        "children": [
          {
            "kind": "t3",
            "data": {
              "id": "abc123",
              "title": "Local-first agents",
              "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
              "subreddit": "LocalLLaMA",
              "author": "agent_builder"
            }
          }
        ]
      }
    },
    {
      "data": {
        "children": [
          {
            "kind": "t1",
            "data": {
              "id": "def456",
              "body": "Detailed comment",
              "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/def456/",
              "subreddit": "LocalLLaMA",
              "author": "commenter"
            }
          }
        ]
      }
    }
  ]
}
"""

    items = parse_reddit_command_output(output)

    assert [item["id"] for item in items] == ["abc123", "def456"]
    assert items[0]["title"] == "Local-first agents"
    assert items[1]["body"] == "Detailed comment"


def test_reddit_items_to_contents_sets_platform_strategy_and_text_fields() -> None:
    contents = reddit_items_to_contents(
        [
            {
                "id": "abc123",
                "title": "Local-first agents",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "author": "agent_builder",
                "score": "42",
                "num_comments": "7",
                "selftext": "A practical write-up.",
            }
        ],
        strategy="reddit-search",
        source_keyword_ids={"agents": 12},
    )

    assert len(contents) == 1
    item = contents[0]
    assert item.source_platform == "reddit"
    assert item.source_strategy == "reddit-search"
    assert item.content_type == "post"
    assert item.content_id == "t3_abc123"
    assert item.content_url.endswith("/local_first_agents/")
    assert item.author_name == "u/agent_builder"
    assert item.tags == ["r/LocalLLaMA"]
    assert item.like_count == 42
    assert item.comment_count == 7
    assert item.body_text == "A practical write-up."
    assert item.score_threshold == 0.6
    assert item.source_keyword_id == 12


def test_reddit_items_to_events_marks_discovered_rows_as_fetch_only_views() -> None:
    events = reddit_items_to_events(
        [
            {
                "id": "abc123",
                "title": "Local-first agents",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "author": "agent_builder",
            }
        ],
        import_source="reddit_search_smoke",
    )

    assert events[0]["event_type"] == "view"
    assert events[0]["metadata"]["source_platform"] == "reddit"
    assert events[0]["metadata"]["content_id"] == "t3_abc123"
    assert events[0]["metadata"]["subreddit"] == "LocalLLaMA"
    assert events[0]["metadata"]["import_source"] == "reddit_search_smoke"
    assert events[0]["metadata"]["signal_strength"] == 0.25


def test_reddit_items_to_events_maps_bootstrap_scopes_to_profile_signals() -> None:
    events = reddit_items_to_events(
        [
            {
                "scope": "reddit_saved",
                "id": "saved1",
                "title": "Saved agent essay",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/saved1/title/",
                "subreddit": "LocalLLaMA",
                "author": "essay_author",
                "selftext": "Long-form technical context.",
            },
            {
                "scope": "reddit_upvoted",
                "id": "liked1",
                "title": "Useful benchmark comment",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/liked1/title/",
                "subreddit": "LocalLLaMA",
                "author": "benchmarker",
            },
            {
                "scope": "reddit_subscribed",
                "content_type": "subreddit",
                "id": "LocalLLaMA",
                "title": "r/LocalLLaMA",
                "url": "https://www.reddit.com/r/LocalLLaMA/",
                "subreddit": "LocalLLaMA",
                "public_description": "Local LLM discussion.",
            },
        ],
        import_source="reddit_bootstrap_events",
    )

    assert [event["event_type"] for event in events] == ["favorite", "like", "follow"]
    assert [event["metadata"]["scope"] for event in events] == [
        "reddit_saved",
        "reddit_upvoted",
        "reddit_subscribed",
    ]
    assert events[0]["metadata"]["signal_strength"] == 0.9
    assert events[1]["metadata"]["signal_strength"] == 0.75
    assert events[2]["metadata"]["signal_strength"] == 0.65
    assert events[2]["metadata"]["source_platform"] == "reddit"
    assert events[2]["context"].startswith("在Reddit关注了")


def test_probe_reddit_command_backend_reports_missing_without_side_effects() -> None:
    def missing_which(name: str) -> str | None:
        assert name in {"opencli", "rdt"}
        return None

    status = probe_reddit_command_backend("auto", which=missing_which)

    assert status == RedditCommandStatus(
        backend="",
        state="missing",
        message="未安装 opencli 或 rdt，无法使用 Reddit 登录态命令后端。",
    )


def test_probe_reddit_command_backend_accepts_missing_which_callable() -> None:
    status = probe_reddit_command_backend("auto", which=lambda _name: None)

    assert status.state == "missing"
    assert status.backend == ""


def test_probe_reddit_command_backend_reports_rdt_missing_credential_without_status(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(reddit_tasks, "_rdt_credential_file", lambda: tmp_path / "credential.json")

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        raise AssertionError("missing credential must not run rdt status")

    status = probe_reddit_command_backend("rdt", which=lambda name: f"/tmp/{name}", runner=runner)

    assert status.backend == "rdt"
    assert status.state == "login_required"
    assert "插件" in status.message


def test_probe_reddit_command_backend_reports_rdt_timeout_as_login_required(
    monkeypatch,
) -> None:
    monkeypatch.setattr(reddit_tasks, "_rdt_saved_credential_state", lambda: ("present", ""))

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout)

    status = probe_reddit_command_backend("rdt", which=lambda name: f"/tmp/{name}", runner=runner)

    assert status.backend == "rdt"
    assert status.state == "login_required"
    assert "rdt login" in status.message


def test_sync_rdt_credential_from_cookie_header_writes_rdt_shape(tmp_path, monkeypatch) -> None:
    credential_file = tmp_path / "rdt" / "credential.json"
    monkeypatch.setattr(reddit_tasks, "_rdt_credential_file", lambda: credential_file)

    result = sync_rdt_credential_from_cookie_header(
        "reddit_session=rs; loid=loid; csrf_token=csrf",
        source="test-extension",
    )

    assert result.ok is True
    assert result.has_cookie is True
    assert result.credential_file == credential_file
    assert result.cookie_names == ("csrf_token", "loid", "reddit_session")
    data = json.loads(credential_file.read_text(encoding="utf-8"))
    assert data["cookies"]["reddit_session"] == "rs"
    assert data["cookies"]["loid"] == "loid"
    assert data["modhash"] == "csrf"
    assert data["source"] == "openbiliclaw:test-extension"
    assert isinstance(data["saved_at"], float)


def test_sync_rdt_credential_from_cookie_header_rejects_missing_session(
    tmp_path,
    monkeypatch,
) -> None:
    credential_file = tmp_path / "rdt" / "credential.json"
    monkeypatch.setattr(reddit_tasks, "_rdt_credential_file", lambda: credential_file)

    result = sync_rdt_credential_from_cookie_header("loid=loid", source="test-extension")

    assert result.ok is True
    assert result.has_cookie is False
    assert result.error_code == "missing_reddit_session"
    assert not credential_file.exists()


def test_probe_reddit_command_backend_accepts_rdt_status_envelope(monkeypatch) -> None:
    monkeypatch.setattr(reddit_tasks, "_rdt_saved_credential_state", lambda: ("present", ""))

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"ok": true, "data": {"authenticated": true, "username": "agent"}}',
            stderr="",
        )

    status = probe_reddit_command_backend("rdt", which=lambda name: f"/tmp/{name}", runner=runner)

    assert status == RedditCommandStatus("rdt", "ready", "rdt 已登录 (agent)。")


def test_default_which_finds_command_next_to_active_python(
    tmp_path,
    monkeypatch,
) -> None:
    script_dir = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    script_dir.mkdir()
    script = script_dir / "rdt"
    script.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(reddit_tasks.shutil, "which", lambda _name: None)
    monkeypatch.setattr(reddit_tasks.sys, "prefix", str(tmp_path))

    assert reddit_tasks._default_which("rdt") == str(script)


def test_subprocess_run_uses_bundled_rdt_cli_when_console_script_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(reddit_tasks, "_default_which", lambda _name: None)

    completed = reddit_tasks._subprocess_run(["rdt", "--version"], timeout=5)

    assert completed.returncode == 0
    assert "rdt, version" in completed.stdout


def test_build_reddit_command_uses_real_rdt_cli_syntax() -> None:
    assert build_reddit_command("rdt", mode="search", query="local agents", limit=5) == [
        "rdt",
        "search",
        "local agents",
        "-n",
        "5",
        "--json",
    ]
    assert build_reddit_command("rdt", mode="hot", subreddit="all", limit=5) == [
        "rdt",
        "all",
        "-n",
        "5",
        "--json",
    ]
    assert build_reddit_command("rdt", mode="hot", subreddit="LocalLLaMA", limit=5) == [
        "rdt",
        "sub",
        "LocalLLaMA",
        "--sort",
        "hot",
        "-n",
        "5",
        "--json",
    ]
    assert build_reddit_command("rdt", mode="subreddit", subreddit="r/LocalLLaMA", limit=5) == [
        "rdt",
        "sub",
        "LocalLLaMA",
        "-n",
        "5",
        "--json",
    ]
    assert build_reddit_command(
        "rdt",
        mode="related",
        query="https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/",
        limit=7,
    ) == ["rdt", "read", "abc123", "-n", "7", "--json"]


def test_reddit_task_queue_claims_and_merges_extension_results(tmp_path) -> None:
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "reddit.db")
    db.initialize()
    queue = RedditTaskQueue(db)

    task_id = queue.enqueue_with_id(
        "search",
        {"keywords": ["local agents"], "max_items_per_keyword": 5},
        daily_budget=10,
    )

    assert task_id
    task = queue.next_pending()
    assert task is not None
    assert task["id"] == task_id
    assert task["type"] == "search"
    assert task["status"] == "in_progress"

    added = queue.merge_result(
        task_id,
        items=[
            {
                "id": "abc123",
                "title": "Local-first agents",
                "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "search_keyword": "local agents",
            },
            {
                "id": "abc123",
                "title": "Local-first agents duplicate",
                "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
            },
        ],
        scope_counts={"reddit_search": 2},
        complete=True,
    )

    assert len(added) == 1
    stored = queue.get(task_id)
    assert stored is not None
    assert stored["status"] == "completed"
    assert recent_reddit_subreddits(db, limit=3) == ["LocalLLaMA"]
    assert recent_reddit_related_urls(db, limit=3) == [
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/"
    ]
