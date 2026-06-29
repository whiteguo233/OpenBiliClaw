from __future__ import annotations

from openbiliclaw.sources.reddit_tasks import (
    RedditCommandStatus,
    RedditTaskQueue,
    parse_reddit_command_output,
    probe_reddit_command_backend,
    recent_reddit_related_urls,
    recent_reddit_subreddits,
    reddit_items_to_contents,
    reddit_items_to_events,
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


def test_probe_reddit_command_backend_accepts_none_which() -> None:
    status = probe_reddit_command_backend("auto", which=None)

    assert status.state == "missing"
    assert status.backend == ""


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
