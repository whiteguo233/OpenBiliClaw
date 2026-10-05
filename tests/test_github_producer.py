from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.github_producer import (
    GitHubDiscoveryProducer,
    _persist_token_rejection,
    _token_fingerprint,
    clear_github_token_rejection,
    github_source_status,
    persist_github_cooldown,
)
from openbiliclaw.runtime.keyword_fetch import ClaimedKeyword
from openbiliclaw.sources.github_client import GitHubAPIError, GitHubSearchPage
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "github.db")
    database.initialize()
    return database


class _Soul:
    async def get_profile(self) -> dict[str, object]:
        return {"preferences": {"interests": [{"name": "local agent"}]}}


def _repo(repository_id: int, *, private: bool = False) -> dict[str, object]:
    return {
        "id": repository_id,
        "node_id": f"R_{repository_id}",
        "name": f"repo-{repository_id}",
        "full_name": f"alice/repo-{repository_id}",
        "html_url": f"https://github.com/alice/repo-{repository_id}",
        "description": "Public developer tool",
        "private": private,
        "visibility": "private" if private else "public",
        "owner": {"id": 9, "node_id": "U_9", "login": "alice"},
        "created_at": "2026-08-01T02:03:04Z",
        "updated_at": "2026-08-02T02:03:04Z",
        "pushed_at": "2026-08-03T02:03:04Z",
        "stargazers_count": repository_id * 10,
        "topics": ["agents", "open-source"],
        "language": "Python",
    }


def _page(
    *items: dict[str, object],
    incomplete: bool = False,
    next_page: int | None = None,
    page: int = 1,
    search_capped: bool = False,
) -> GitHubSearchPage:
    return GitHubSearchPage(
        items=[dict(item) for item in items],
        total_count=len(items),
        incomplete_results=incomplete,
        page=page,
        per_page=100,
        next_page=next_page,
        last_page=next_page,
        next_url="",
        last_url="",
        scope_complete=not incomplete and next_page is None and not search_capped,
        search_capped=search_capped,
    )


@dataclass
class _Client:
    responses: list[object]
    has_access_token: bool = False

    def __post_init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.disabled = False

    async def search_repositories(self, query: str, **kwargs: object) -> GitHubSearchPage:
        self.calls.append({"query": query, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, GitHubSearchPage)
        return response

    def disable_access_token(self) -> None:
        self.disabled = True
        self.has_access_token = False


class _Pipeline:
    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.enqueued: list[tuple[list[Any], str]] = []
        self.drains: list[int] = []

    def pool_full_for_source(self, source_family: str) -> bool:
        assert source_family == "github"
        return False

    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.append((items, source_context))
        return len(items) if self.accept else 0

    async def drain_pending(self, *, profile: Any, batch_size: int) -> dict[str, int]:
        del profile
        self.drains.append(batch_size)
        return {"drained": batch_size}


class _Keywords:
    def __init__(self, keyword: str = "agent framework") -> None:
        self.keyword = keyword
        self.claimed_platforms: list[str] = []
        self.used: list[int] = []
        self.failed: list[int] = []
        self.rolled_back: list[int] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
        del n
        self.claimed_platforms.append(platform)
        return [ClaimedKeyword(id=7, keyword=self.keyword)]

    def mark_used(self, claimed: list[ClaimedKeyword]) -> None:
        self.used.extend(item.id for item in claimed)

    def mark_failed(self, claimed: list[ClaimedKeyword]) -> None:
        self.failed.extend(item.id for item in claimed)

    def rollback(self, claimed: ClaimedKeyword) -> None:
        self.rolled_back.append(claimed.id)


@pytest.mark.asyncio
async def test_producer_runs_public_repository_modes_and_preserves_identity(db: Database) -> None:
    client = _Client([_page(_repo(1)), _page(_repo(2)), _page(_repo(3))])
    pipeline = _Pipeline()
    keywords = _Keywords("agent is:private framework")
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "ok"
    assert result["discovered"] == 3
    assert result["enqueued"] == 3
    assert keywords.claimed_platforms == ["github"]
    assert keywords.used == [7]
    assert "is:private" not in str(client.calls[0]["query"]).casefold()
    assert all("is:public" in str(call["query"]) for call in client.calls)
    assert client.calls[1]["sort"] == "stars"
    assert "created:>=" in str(client.calls[2]["query"])
    items = [item for group, _context in pipeline.enqueued for item in group]
    assert {item.item_key for item in items} == {
        "github:repository:1",
        "github:repository:2",
        "github:repository:3",
    }
    assert all(item.content_type == "repository" and item.cover_url == "" for item in items)
    assert all(item.engagement_available == ["favorite"] for item in items)
    assert items[0].source_metadata["repository_node_id"] == "R_1"
    assert items[0].source_metadata["language"] == "Python"
    assert items[0].source_metadata["topics"] == ["agents", "open-source"]
    assert items[0].source_keyword_id == 7
    assert producer.consumed_today("search") == 1
    assert producer.consumed_today("ranked") == 1
    assert producer.consumed_today("latest") == 1
    assert pipeline.drains == [3]


@pytest.mark.asyncio
async def test_incomplete_search_keeps_rows_without_advancing_cursor(db: Database) -> None:
    client = _Client([_page(_repo(10), incomplete=True, next_page=2)])
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["mode_results"] == {"search": "incomplete_results"}
    state = db.conn.execute(
        "SELECT cursor FROM github_discovery_state WHERE state_key LIKE 'search:%'"
    ).fetchone()
    assert state is None


@pytest.mark.asyncio
async def test_terminal_search_cap_is_reported_as_partial(db: Database) -> None:
    client = _Client([_page(_repo(10), page=10, search_capped=True)])
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("ranked",),
        min_interval_minutes=0,
    )
    producer._ensure_tables()
    producer._set_cursor("ranked", 10)

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "partial"
    assert result["mode_results"] == {"ranked": "search_capped"}
    cursor = db.conn.execute(
        "SELECT cursor FROM github_discovery_state WHERE state_key = 'ranked'"
    ).fetchone()
    assert cursor is not None
    assert cursor[0] == 1


@pytest.mark.asyncio
async def test_rate_limit_keeps_previous_mode_rows_and_persists_cooldown(db: Database) -> None:
    client = _Client(
        [
            _page(_repo(11)),
            GitHubAPIError("rate_limited", "slow down", retry_after_seconds=120),
        ]
    )
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search", "ranked"),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=2)
    next_result = await producer.produce_if_due(limit=2, force=True)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["mode_results"]["ranked"] == "rate_limited"
    assert next_result["reason"] == "rate_limited"


@pytest.mark.asyncio
async def test_formal_producer_obeys_cooldown_persisted_by_other_github_path(
    db: Database,
) -> None:
    persist_github_cooldown(db, 120)
    client = _Client([_page(_repo(111))])
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=1, force=True)

    assert result["reason"] == "rate_limited"
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejected_optional_token_degrades_to_anonymous_public_modes(db: Database) -> None:
    client = _Client(
        [GitHubAPIError("unauthorized", "bad token"), _page(_repo(12))],
        has_access_token=True,
    )
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="not-a-real-secret",
        enabled=True,
        source_modes=("search", "ranked"),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=2)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert client.disabled is True
    note = str(
        db.conn.execute(
            "SELECT note FROM github_discovery_state WHERE state_key = 'token_rejected'"
        ).fetchone()[0]
    )
    assert "not-a-real-secret" not in note


def test_source_status_matches_rejection_to_current_token_fingerprint(db: Database) -> None:
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=object(),
        client=object(),
    )
    producer._ensure_tables()
    _persist_token_rejection(db, _token_fingerprint("old-token"))

    rejected = github_source_status(db, enabled=True, access_token="old-token")
    rotated = github_source_status(db, enabled=True, access_token="new-token")

    assert rejected["token_state"] == "rejected"
    assert "PAT 被拒绝" in str(rejected["detail"])
    assert rotated["token_state"] == "ok"
    assert "PAT 被拒绝" not in str(rotated["detail"])

    clear_github_token_rejection(db)
    marker = db.conn.execute(
        "SELECT 1 FROM github_discovery_state WHERE state_key = 'token_rejected'"
    ).fetchone()
    assert marker is None


def test_source_status_only_aggregates_currently_enabled_modes(db: Database) -> None:
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=object(),
        client=object(),
    )
    producer._ensure_tables()
    db.conn.executemany(
        """
        INSERT INTO github_discovery_runs(mode, reason, error_code)
        VALUES (?, ?, ?)
        """,
        [
            ("search", "ok", ""),
            ("ranked", "error", "upstream_error"),
        ],
    )
    db.conn.commit()

    search_only = github_source_status(
        db,
        enabled=True,
        source_modes=("search",),
    )
    with_new_mode = github_source_status(
        db,
        enabled=True,
        source_modes=("search", "latest"),
    )
    latest_only = github_source_status(
        db,
        enabled=True,
        source_modes=("latest",),
    )

    assert search_only["state"] == "ready"
    assert search_only["modes"] == {"search": {"reason": "ok", "error_code": ""}}
    assert with_new_mode["state"] == "partial"
    assert with_new_mode["modes"] == {
        "search": {"reason": "ok", "error_code": ""},
        "latest": {"reason": "unverified", "error_code": ""},
    }
    assert latest_only["state"] == "unverified"
    assert "modes" not in latest_only


@pytest.mark.asyncio
async def test_private_or_malformed_rows_are_not_admitted(db: Database) -> None:
    keywords = _Keywords()
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client([_page(_repo(13, private=True))]),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "partial"
    assert result["discovered"] == 0
    assert result["mode_results"] == {"search": "rejected_rows"}
    assert keywords.failed == []
    assert keywords.rolled_back == [7]
    cursor = db.conn.execute(
        "SELECT cursor FROM github_discovery_state WHERE state_key LIKE 'search:%'"
    ).fetchone()
    assert cursor is None


@pytest.mark.parametrize("keyword", ["is:private", "visibility:private", "private:true"])
@pytest.mark.asyncio
async def test_private_only_keyword_is_failed_without_api_call_or_stuck_lease(
    db: Database,
    keyword: str,
) -> None:
    keywords = _Keywords(keyword)
    client = _Client([])
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "empty"
    assert result["discovered"] == 0
    assert client.calls == []
    assert keywords.failed == [7]
    assert keywords.rolled_back == []


@pytest.mark.asyncio
async def test_mixed_valid_and_rejected_rows_preserve_public_result_as_partial(
    db: Database,
) -> None:
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client([_page(_repo(130), _repo(131, private=True))]),
        enabled=True,
        source_modes=("ranked",),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=2)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["mode_results"] == {"ranked": "rejected_rows"}
    assert (
        db.conn.execute(
            "SELECT cursor FROM github_discovery_state WHERE state_key = 'ranked'"
        ).fetchone()
        is None
    )


@pytest.mark.asyncio
async def test_budget_and_keyword_yield_count_only_pipeline_retention(db: Database) -> None:
    pipeline = _Pipeline(accept=False)
    keywords = _Keywords()
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client([_page(_repo(14))]),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=1)

    assert result["discovered"] == 1
    assert result["enqueued"] == 0
    assert producer.consumed_today("search") == 0
    assert keywords.used == []
    assert keywords.rolled_back == [7]


@pytest.mark.asyncio
async def test_invalid_stale_page_resets_once(db: Database) -> None:
    client = _Client(
        [
            GitHubAPIError("invalid_request", "page out of range"),
            _page(_repo(15)),
        ]
    )
    producer = GitHubDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        source_modes=("ranked",),
        min_interval_minutes=0,
    )
    producer._ensure_tables()
    producer._set_cursor("ranked", 5)

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "ok"
    assert [call["page"] for call in client.calls] == [5, 1]
