from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.api.models import RecommendationOut
from openbiliclaw.discovery.candidate_pool import (
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.sources.github import (
    fetch_github_public_starred_events,
    github_repository_metadata,
    github_repository_to_content,
    github_starred_to_event,
)
from openbiliclaw.sources.github_client import GitHubAPIError, GitHubStarredPage
from openbiliclaw.storage.database import Database

_FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _repository() -> dict[str, Any]:
    payload = _fixture("search_repositories_page.json")
    assert isinstance(payload, dict)
    items = payload["items"]
    assert isinstance(items, list)
    row = items[0]
    assert isinstance(row, dict)
    return copy.deepcopy(row)


def _starred() -> dict[str, Any]:
    payload = _fixture("starred_repositories_page.json")
    assert isinstance(payload, list)
    row = payload[0]
    assert isinstance(row, dict)
    return copy.deepcopy(row)


def _starred_variant(repository_id: int, full_name: str, *, starred_at: str) -> dict[str, Any]:
    row = _starred()
    repository = row["repo"]
    assert isinstance(repository, dict)
    owner, name = full_name.split("/", 1)
    repository.update(
        {
            "id": repository_id,
            "full_name": full_name,
            "name": name,
            "html_url": f"https://github.com/{full_name}",
            "owner": {"id": repository_id + 100, "login": owner},
        }
    )
    row["starred_at"] = starred_at
    return row


def test_repository_normalization_uses_typed_identity_and_only_star_engagement() -> None:
    item = github_repository_to_content(
        _repository(), strategy="github-search", source_keyword_id=17
    )

    assert item is not None
    assert item.content_id == "repository:1175278883"
    assert item.bvid == "repository:1175278883"
    assert item.item_key == "github:repository:1175278883"
    assert item.content_type == "repository"
    assert item.source_platform == "github"
    assert item.source_strategy == "github-search"
    assert item.content_url == "https://github.com/whiteguo233/OpenBiliClaw"
    assert item.title == "whiteguo233/OpenBiliClaw"
    assert item.author_name == "whiteguo233"
    assert item.published_at == "2026-03-07T13:46:10Z"
    assert item.favorite_count == 3109
    assert item.engagement_available == ["favorite"]
    assert item.source_keyword_id == 17
    assert item.cover_url == ""
    assert item.tags == [
        "ai-agent",
        "content-discovery",
        "local-first",
        "python",
    ]
    assert item.view_count == item.like_count == item.comment_count == item.share_count == 0
    assert item.danmaku_count == item.reply_count == item.retweet_count == 0
    assert item.source_metadata == github_repository_metadata(_repository())


def test_repository_source_metadata_is_allowlisted_and_bounded() -> None:
    row = _repository()
    row["secret"] = "must-not-cross-the-normalizer"
    row["permissions"] = {"admin": True}
    owner = row["owner"]
    assert isinstance(owner, dict)
    owner["email"] = "private@example.com"

    item = github_repository_to_content(row, strategy="github-search")

    assert item is not None
    serialized = json.dumps(item.source_metadata, ensure_ascii=False)
    assert "must-not-cross-the-normalizer" not in serialized
    assert "private@example.com" not in serialized
    assert "secret" not in item.source_metadata
    assert "permissions" not in item.source_metadata
    assert len(serialized.encode("utf-8")) < 16_384


def test_repository_provenance_survives_candidate_cache_and_recommendation_rows(
    tmp_path: Path,
) -> None:
    item = github_repository_to_content(
        _repository(), strategy="github-search", source_keyword_id=17
    )
    assert item is not None
    write = discovered_content_to_candidate_write(
        item,
        raw_payload={"source_metadata": {"secret": "must-not-shadow-normalizer"}},
    )
    assert write.raw_payload["source_metadata"] == item.source_metadata

    db = Database(tmp_path / "github-provenance.db")
    db.initialize()
    assert db.enqueue_discovery_candidates([write]) == 1
    candidate_row = db.conn.execute(
        "SELECT * FROM discovery_candidates WHERE candidate_key = ?",
        (write.candidate_key,),
    ).fetchone()
    assert candidate_row is not None

    restored = row_to_discovered_content(dict(candidate_row))
    assert restored.source_metadata == item.source_metadata
    assert restored.source_metadata["repository_node_id"] == "R_kgDORg1VIw"
    assert restored.engagement_available == ["favorite"]

    restored.relevance_score = 0.9
    db.cache_content(restored.bvid, **restored.to_cache_kwargs())
    cached_row = db.conn.execute(
        "SELECT * FROM content_cache WHERE item_key = ?",
        (item.item_key,),
    ).fetchone()
    assert cached_row is not None
    assert json.loads(str(cached_row["source_metadata"])) == item.source_metadata

    legacy_kwargs = restored.to_cache_kwargs()
    legacy_kwargs["source_metadata"] = {}
    db.cache_content(restored.bvid, **legacy_kwargs)
    preserved = db.conn.execute(
        "SELECT source_metadata FROM content_cache WHERE item_key = ?",
        (item.item_key,),
    ).fetchone()
    assert preserved is not None
    assert json.loads(str(preserved["source_metadata"])) == item.source_metadata

    engine = object.__new__(RecommendationEngine)
    cached_item = engine._rows_to_discovered([dict(cached_row)])[0]  # noqa: SLF001
    assert cached_item.source_metadata == item.source_metadata

    (backfill_item,) = ContentDiscoveryEngine(database=db)._load_cached_backfill(  # noqa: SLF001
        limit=1,
        exclude_bvids=set(),
        source_platforms={"github"},
    )
    assert backfill_item.content_type == "repository"
    assert backfill_item.favorite_count == 3109
    assert backfill_item.source_metadata == item.source_metadata

    db.insert_recommendation(
        restored.bvid,
        item_key=restored.item_key,
        confidence=0.9,
        expression="值得看看",
        topic="开源项目",
    )
    recommendation_rows = db.get_recommendations(limit=1)
    assert len(recommendation_rows) == 1
    assert json.loads(str(recommendation_rows[0]["source_metadata"])) == item.source_metadata
    payload = RecommendationOut(
        id=int(recommendation_rows[0]["id"]),
        bvid=str(recommendation_rows[0]["bvid"]),
        source_metadata=item.source_metadata,
    ).model_dump()
    assert payload["source_metadata"]["repository_node_id"] == "R_kgDORg1VIw"


def test_legacy_content_cache_gains_source_metadata_column(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-source-metadata.db"
    database = Database(database_path)
    database.initialize()
    database.cache_content("BV1legacy", title="legacy")
    database.conn.execute("ALTER TABLE content_cache DROP COLUMN source_metadata")
    database.conn.commit()
    database.close()

    migrated = Database(database_path)
    migrated.initialize()

    columns = {
        str(row["name"]): row
        for row in migrated.conn.execute("PRAGMA table_info(content_cache)").fetchall()
    }
    assert columns["source_metadata"]["dflt_value"] == "'{}'"
    row = migrated.conn.execute(
        "SELECT source_metadata FROM content_cache WHERE bvid = 'BV1legacy'"
    ).fetchone()
    assert row is not None
    assert row["source_metadata"] == "{}"
    migrated.close()


def test_repository_metadata_preserves_provenance_without_remapping_counts() -> None:
    metadata = github_repository_metadata(_repository())

    assert metadata == {
        "source_platform": "github",
        "content_type": "repository",
        "content_id": "repository:1175278883",
        "repository_id": "1175278883",
        "repository_full_name": "whiteguo233/OpenBiliClaw",
        "author_name": "whiteguo233",
        "created_at": "2026-03-07T13:46:10Z",
        "favorite_count": 3109,
        "engagement_available": ["favorite"],
        "visibility": "public",
        "repository_node_id": "R_kgDORg1VIw",
        "owner_node_id": "MDQ6VXNlcjMzNTAxNzE=",
        "updated_at": "2026-08-30T17:59:28Z",
        "pushed_at": "2026-08-29T03:51:03Z",
        "language": "Python",
        "owner_id": 3350171,
        "forks_count": 151,
        "open_issues_count": 26,
        "watchers_count": 3109,
        "topics": ["ai-agent", "content-discovery", "local-first", "python"],
        "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
        "fork": False,
        "archived": False,
        "disabled": False,
    }
    assert "share_count" not in metadata
    assert "comment_count" not in metadata
    assert "like_count" not in metadata


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.pop("private"),
        lambda row: row.__setitem__("private", True),
        lambda row: row.__setitem__("private", "false"),
        lambda row: row.__setitem__("visibility", "private"),
        lambda row: row.__setitem__("id", "1175278883"),
        lambda row: row.__setitem__("stargazers_count", None),
        lambda row: row.__setitem__("created_at", "2026-03-07 13:46:10"),
        lambda row: row.__setitem__("html_url", "https://evil.example/repository"),
        lambda row: row.__setitem__("owner", ["whiteguo233"]),
        lambda row: row.__setitem__("full_name", "other/OpenBiliClaw"),
    ],
)
def test_repository_normalization_rejects_private_or_malformed_rows(mutation: Any) -> None:
    row = _repository()
    mutation(row)
    assert github_repository_to_content(row, strategy="github-search") is None
    assert github_repository_metadata(row) == {}


def test_repository_normalization_never_stringifies_nested_external_values() -> None:
    row = _repository()
    row["description"] = {"text": "nested"}
    row["topics"] = "ai-agent"
    owner = row["owner"]
    assert isinstance(owner, dict)
    owner["node_id"] = ["nested"]
    row["license"] = ["MIT"]

    item = github_repository_to_content(row, strategy="github-search")
    metadata = github_repository_metadata(row)

    assert item is not None
    assert item.body_text == ""
    assert item.tags == ["Python"]
    assert "owner_node_id" not in metadata
    assert "license" not in metadata
    assert "['nested']" not in json.dumps(metadata)


def test_repository_owner_name_is_preserved_verbatim_from_authoritative_row() -> None:
    row = _repository()
    row["name"] = "renovate-config"
    row["full_name"] = "renovate[bot]/renovate-config"
    row["html_url"] = "https://github.com/renovate[bot]/renovate-config"
    owner = row["owner"]
    assert isinstance(owner, dict)
    owner["login"] = "renovate[bot]"

    item = github_repository_to_content(row, strategy="github-search")

    assert item is not None
    assert item.author_name == "renovate[bot]"


def test_starred_wrapper_becomes_canonical_favorite_at_authoritative_star_time() -> None:
    event = github_starred_to_event(_starred(), username="octocat")

    assert event is not None
    assert event["event_type"] == "favorite"
    assert event["source_platform"] == "github"
    assert event["source_confidence"] == "exact"
    assert event["content_id"] == "repository:1083253848"
    assert event["title"] == "sindresorhus/parse-sse"
    assert event["url"] == "https://github.com/sindresorhus/parse-sse"
    metadata = event["metadata"]
    assert metadata["author"] == "sindresorhus"
    assert metadata["author_name"] == "sindresorhus"
    assert metadata["timestamp"] == "2026-07-09T22:59:29Z"
    assert metadata["starred_at"] == "2026-07-09T22:59:29Z"
    assert metadata["created_at"] == "2025-10-25T16:42:52Z"
    assert metadata["favorite_count"] == 92
    assert metadata["engagement_available"] == ["favorite"]
    assert metadata["import_source"] == "github_public_starred"
    assert "satisfaction" not in event


@pytest.mark.parametrize(
    "row",
    [
        _repository(),  # bare repo: no authoritative starred_at wrapper
        {"starred_at": "invalid", "repo": _repository()},
        {"starred_at": "2026-01-01T00:00:00Z", "repo": {"private": True}},
        {"starred_at": "2026-01-01T00:00:00Z", "repo": None},
        "not-a-row",
    ],
)
def test_starred_event_rejects_bare_private_or_malformed_rows(row: object) -> None:
    assert github_starred_to_event(row, username="octocat") is None


class _FakeStarredClient:
    def __init__(self, pages: dict[int, GitHubStarredPage | GitHubAPIError]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    async def get_starred_repositories(
        self, username: str, *, page: int, per_page: int
    ) -> GitHubStarredPage:
        self.calls.append((username, page, per_page))
        result = self.pages[page]
        if isinstance(result, GitHubAPIError):
            raise result
        return result


def _page(items: list[dict[str, Any]], page: int, next_page: int | None) -> GitHubStarredPage:
    return GitHubStarredPage(
        items=items,
        page=page,
        per_page=100,
        next_page=next_page,
        last_page=next_page,
        next_url=(
            f"https://api.github.com/users/octocat/starred?page={next_page}"
            if next_page is not None
            else ""
        ),
        last_url="",
        scope_complete=next_page is None,
    )


@pytest.mark.asyncio
async def test_public_star_fetch_follows_link_pages_dedupes_and_proves_complete() -> None:
    first = _starred_variant(1, "alice/one", starred_at="2026-01-03T00:00:00Z")
    duplicate = copy.deepcopy(first)
    second = _starred_variant(2, "bob/two", starred_at="2026-01-02T00:00:00Z")
    client = _FakeStarredClient({1: _page([first], 1, 2), 2: _page([duplicate, second], 2, None)})

    result = await fetch_github_public_starred_events(
        client, username="octocat", limit=10, per_page=100, max_pages=3
    )

    assert [event["content_id"] for event in result.events] == ["repository:1", "repository:2"]
    assert client.calls == [("octocat", 1, 10), ("octocat", 2, 10)]
    assert result.status == "ok"
    assert result.scope_complete is True
    assert result.terminal_evidence == "link_exhausted"
    assert result.pages_fetched == 2
    assert result.rows_seen == 3
    assert result.duplicates == 1
    assert result.completeness["rows_accepted"] == 2


@pytest.mark.asyncio
async def test_public_star_fetch_requires_affirmative_empty_link_exhaustion() -> None:
    client = _FakeStarredClient({1: _page([], 1, None)})

    result = await fetch_github_public_starred_events(client, username="octocat", limit=10)

    assert result.events == []
    assert result.status == "empty"
    assert result.scope_complete is True
    assert result.affirmative_empty is True
    assert result.terminal_evidence == "affirmative_empty"


@pytest.mark.asyncio
async def test_public_star_fetch_marks_item_and_page_caps_degraded() -> None:
    first = _starred_variant(1, "alice/one", starred_at="2026-01-03T00:00:00Z")
    second = _starred_variant(2, "bob/two", starred_at="2026-01-02T00:00:00Z")
    item_cap = await fetch_github_public_starred_events(
        _FakeStarredClient({1: _page([first, second], 1, None)}),
        username="octocat",
        limit=1,
    )
    page_cap = await fetch_github_public_starred_events(
        _FakeStarredClient({1: _page([first], 1, 2)}),
        username="octocat",
        limit=10,
        max_pages=1,
    )

    assert item_cap.status == "degraded"
    assert item_cap.item_cap_reached is True
    assert item_cap.scope_complete is False
    assert page_cap.status == "degraded"
    assert page_cap.page_cap_reached is True
    assert page_cap.terminal_evidence == "page_cap"


@pytest.mark.asyncio
async def test_public_star_fetch_rejects_private_and_malformed_without_claiming_complete() -> None:
    private = _starred()
    private_repo = private["repo"]
    assert isinstance(private_repo, dict)
    private_repo["private"] = True
    malformed = _starred()
    malformed_repo = malformed["repo"]
    assert isinstance(malformed_repo, dict)
    malformed_repo.pop("private")

    result = await fetch_github_public_starred_events(
        _FakeStarredClient({1: _page([private, malformed], 1, None)}),
        username="octocat",
        limit=10,
    )

    assert result.events == []
    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.affirmative_empty is False
    assert result.rejected_private == 1
    assert result.rejected_malformed == 1
    assert result.terminal_evidence == "link_exhausted_with_rejections"


@pytest.mark.asyncio
async def test_later_page_rate_limit_preserves_accepted_partial_rows() -> None:
    first = _starred_variant(1, "alice/one", starred_at="2026-01-03T00:00:00Z")
    rate_limit = GitHubAPIError("rate_limited", "limited", status_code=429, retry_after_seconds=45)
    result = await fetch_github_public_starred_events(
        _FakeStarredClient({1: _page([first], 1, 2), 2: rate_limit}),
        username="octocat",
        limit=10,
    )

    assert [event["content_id"] for event in result.events] == ["repository:1"]
    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.terminal_evidence == "partial_error"
    assert result.error_code == "rate_limited"
    assert result.retry_after_seconds == 45


@pytest.mark.asyncio
async def test_later_page_timeout_preserves_accepted_partial_rows() -> None:
    first = _starred_variant(1, "alice/one", starred_at="2026-01-03T00:00:00Z")

    class _SlowSecondPageClient:
        async def get_starred_repositories(
            self,
            username: str,
            *,
            page: int,
            per_page: int,
        ) -> GitHubStarredPage:
            del username, per_page
            if page == 1:
                return _page([first], 1, 2)
            await asyncio.sleep(0.2)
            return _page([], 2, None)

    result = await fetch_github_public_starred_events(
        _SlowSecondPageClient(),
        username="octocat",
        limit=10,
        timeout_seconds=0.05,
    )

    assert [event["content_id"] for event in result.events] == ["repository:1"]
    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.terminal_evidence == "partial_timeout"
    assert result.error_code == "timeout"


@pytest.mark.asyncio
async def test_first_page_failure_is_not_misreported_as_empty() -> None:
    rate_limit = GitHubAPIError("rate_limited", "limited", status_code=429)
    with pytest.raises(GitHubAPIError) as exc_info:
        await fetch_github_public_starred_events(
            _FakeStarredClient({1: rate_limit}), username="octocat", limit=10
        )
    assert exc_info.value.code == "rate_limited"


@pytest.mark.asyncio
async def test_non_monotonic_link_page_preserves_rows_but_degrades_scope() -> None:
    first = _starred_variant(1, "alice/one", starred_at="2026-01-03T00:00:00Z")
    result = await fetch_github_public_starred_events(
        _FakeStarredClient({1: _page([first], 1, 1)}),
        username="octocat",
        limit=10,
    )

    assert len(result.events) == 1
    assert result.status == "degraded"
    assert result.scope_complete is False
    assert result.error_code == "schema_changed"
