"""GitHub public-repository and starred-event normalization."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.event_format import build_event
from openbiliclaw.sources.github_client import (
    GITHUB_MAX_PER_PAGE,
    GitHubAPIError,
    validate_github_username,
)

GitHubStarredFetchStatus = Literal["ok", "empty", "degraded"]

_GITHUB_PRIVATE_SCOPE_RE = re.compile(r"(?i)(?:is:private|visibility:private|private:true)")
_GITHUB_PUBLIC_QUERY_SUFFIX = "in:name,description,readme is:public fork:false"


def github_public_repository_query(value: object) -> str:
    """Build the server-owned public-only repository search query."""

    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = _GITHUB_PRIVATE_SCOPE_RE.sub(" ", text)
    text = " ".join(text.split()).strip()
    return f"{text} {_GITHUB_PUBLIC_QUERY_SUFFIX}" if text else ""


@dataclass(frozen=True)
class GitHubRepositoryRecord:
    """Strict, public-only subset of one official repository row."""

    repository_id: int
    content_id: str
    node_id: str
    name: str
    full_name: str
    html_url: str
    description: str
    owner_login: str
    owner_id: int | None
    owner_node_id: str
    created_at: str
    updated_at: str
    pushed_at: str
    favorite_count: int
    forks_count: int | None
    open_issues_count: int | None
    watchers_count: int | None
    language: str
    topics: tuple[str, ...]
    license_key: str
    license_name: str
    license_spdx_id: str
    is_fork: bool | None
    archived: bool | None
    disabled: bool | None
    visibility: str


@dataclass(frozen=True)
class GitHubStarredFetchResult:
    """Bounded public-star fetch with affirmative completeness evidence."""

    events: list[dict[str, Any]]
    status: GitHubStarredFetchStatus
    scope_complete: bool
    affirmative_empty: bool
    terminal_evidence: str
    pages_fetched: int
    rows_seen: int
    duplicates: int
    rejected_private: int
    rejected_malformed: int
    item_cap_reached: bool
    page_cap_reached: bool
    next_page: int | None
    error_code: str
    retry_after_seconds: int | None

    @property
    def completeness(self) -> dict[str, object]:
        """JSON-safe metadata for bootstrap/task diagnostics."""

        return {
            "scope_complete": self.scope_complete,
            "affirmative_empty": self.affirmative_empty,
            "terminal_evidence": self.terminal_evidence,
            "pages_fetched": self.pages_fetched,
            "rows_seen": self.rows_seen,
            "rows_accepted": len(self.events),
            "duplicates": self.duplicates,
            "rejected_private": self.rejected_private,
            "rejected_malformed": self.rejected_malformed,
            "item_cap_reached": self.item_cap_reached,
            "page_cap_reached": self.page_cap_reached,
            "next_page": self.next_page,
            "error_code": self.error_code,
            "retry_after_seconds": self.retry_after_seconds,
        }


def _text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(char for char in value.strip() if ord(char) >= 32 and ord(char) != 127)
    return text[:max_length]


def _required_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_non_negative_int(value)


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _timestamp(value: object) -> str:
    """Validate an authoritative GitHub instant and canonicalize it to UTC."""

    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _string_list(value: object, *, item_limit: int, item_length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _text(raw, max_length=item_length)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= item_limit:
            break
    return tuple(result)


def _public_repository_url(value: object, *, full_name: str) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path.strip("/").casefold() != full_name.casefold()
    ):
        return ""
    return raw


def _repository_record(row: object) -> GitHubRepositoryRecord | None:
    if not isinstance(row, dict):
        return None
    # Public-only is fail closed: a missing/stringified ``private`` flag is not
    # silently interpreted as public, and visibility cannot contradict it.
    if row.get("private") is not False:
        return None
    visibility = _text(row.get("visibility"), max_length=24).casefold()
    if visibility and visibility != "public":
        return None
    repository_id = _required_non_negative_int(row.get("id"))
    stars = _required_non_negative_int(row.get("stargazers_count"))
    if repository_id is None or repository_id <= 0 or stars is None:
        return None
    owner = row.get("owner")
    if not isinstance(owner, dict):
        return None
    owner_login = _text(owner.get("login"), max_length=100)
    full_name = _text(row.get("full_name"), max_length=256)
    name = _text(row.get("name"), max_length=128)
    if not owner_login or not full_name or "/" not in full_name:
        return None
    full_name_owner, _, full_name_repo = full_name.partition("/")
    if (
        full_name_owner.casefold() != owner_login.casefold()
        or not full_name_repo
        or "/" in full_name_repo
    ):
        return None
    if name and name.casefold() != full_name_repo.casefold():
        return None
    if not name:
        name = full_name_repo
    html_url = _public_repository_url(row.get("html_url"), full_name=full_name)
    created_at = _timestamp(row.get("created_at"))
    if not html_url or not created_at:
        return None
    forks_count = _optional_non_negative_int(row.get("forks_count"))
    open_issues_count = _optional_non_negative_int(row.get("open_issues_count"))
    watchers_count = _optional_non_negative_int(row.get("watchers_count"))
    # Optional counters are omitted when the schema drifts instead of being
    # coerced into fake zeros. The required star counter is handled above.
    license_payload = row.get("license")
    license_row = license_payload if isinstance(license_payload, dict) else {}
    return GitHubRepositoryRecord(
        repository_id=repository_id,
        content_id=f"repository:{repository_id}",
        node_id=_text(row.get("node_id"), max_length=160),
        name=name,
        full_name=full_name,
        html_url=html_url,
        description=_text(row.get("description"), max_length=1_000),
        owner_login=owner_login,
        owner_id=_optional_non_negative_int(owner.get("id")),
        owner_node_id=_text(owner.get("node_id"), max_length=160),
        created_at=created_at,
        updated_at=_timestamp(row.get("updated_at")),
        pushed_at=_timestamp(row.get("pushed_at")),
        favorite_count=stars,
        forks_count=forks_count,
        open_issues_count=open_issues_count,
        watchers_count=watchers_count,
        language=_text(row.get("language"), max_length=80),
        topics=_string_list(row.get("topics"), item_limit=20, item_length=80),
        license_key=_text(license_row.get("key"), max_length=80),
        license_name=_text(license_row.get("name"), max_length=160),
        license_spdx_id=_text(license_row.get("spdx_id"), max_length=80),
        is_fork=_optional_bool(row.get("fork")),
        archived=_optional_bool(row.get("archived")),
        disabled=_optional_bool(row.get("disabled")),
        visibility=visibility or "public",
    )


def _record_metadata(repository: GitHubRepositoryRecord) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_platform": "github",
        "content_type": "repository",
        "content_id": repository.content_id,
        "repository_id": str(repository.repository_id),
        "repository_full_name": repository.full_name,
        "author_name": repository.owner_login,
        "created_at": repository.created_at,
        "favorite_count": repository.favorite_count,
        "engagement_available": ["favorite"],
        "visibility": repository.visibility,
    }
    optional_strings = {
        "repository_node_id": repository.node_id,
        "owner_node_id": repository.owner_node_id,
        "updated_at": repository.updated_at,
        "pushed_at": repository.pushed_at,
        "language": repository.language,
    }
    metadata.update({key: value for key, value in optional_strings.items() if value})
    if repository.owner_id is not None:
        metadata["owner_id"] = repository.owner_id
    for key, value in (
        ("forks_count", repository.forks_count),
        ("open_issues_count", repository.open_issues_count),
        ("watchers_count", repository.watchers_count),
    ):
        if value is not None:
            metadata[key] = value
    if repository.topics:
        metadata["topics"] = list(repository.topics)
    license_metadata = {
        key: value
        for key, value in (
            ("key", repository.license_key),
            ("name", repository.license_name),
            ("spdx_id", repository.license_spdx_id),
        )
        if value
    }
    if license_metadata:
        metadata["license"] = license_metadata
    for key, value in (
        ("fork", repository.is_fork),
        ("archived", repository.archived),
        ("disabled", repository.disabled),
    ):
        if value is not None:
            metadata[key] = value
    return metadata


def github_repository_metadata(row: object) -> dict[str, Any]:
    """Return a bounded, JSON-safe provenance subset for one public repo."""

    repository = _repository_record(row)
    return _record_metadata(repository) if repository is not None else {}


def github_repository_to_content(
    row: object,
    *,
    strategy: str,
    source_keyword_id: int | None = None,
) -> DiscoveredContent | None:
    """Normalize one public GitHub repository into discovery content."""

    repository = _repository_record(row)
    if repository is None:
        return None
    tags = list(repository.topics)
    if repository.language and repository.language.casefold() not in {
        value.casefold() for value in tags
    }:
        tags.append(repository.language)
    return DiscoveredContent(
        bvid=repository.content_id,
        content_id=repository.content_id,
        content_url=repository.html_url,
        source_platform="github",
        source_strategy=str(strategy or "").strip(),
        content_type="repository",
        title=repository.full_name,
        author_name=repository.owner_login,
        body_text=repository.description,
        description=repository.description,
        cover_url="",
        published_at=repository.created_at,
        tags=tags,
        favorite_count=repository.favorite_count,
        engagement_available=["favorite"],
        source_metadata=_record_metadata(repository),
        score_threshold=0.0,
        source_keyword_id=source_keyword_id,
    )


def github_starred_to_event(
    row: object,
    *,
    username: object,
) -> dict[str, Any] | None:
    """Map one ``star+json`` wrapper to an authoritative favorite event."""

    if not isinstance(row, dict):
        return None
    try:
        normalized_username = validate_github_username(username)
    except ValueError:
        return None
    if not normalized_username:
        return None
    starred_at = _timestamp(row.get("starred_at"))
    repository = _repository_record(row.get("repo"))
    if not starred_at or repository is None:
        return None
    metadata = _record_metadata(repository)
    metadata.update(
        {
            "github_username": normalized_username,
            "starred_at": starred_at,
            # Shared event-time consumers read this canonical key. It is the
            # star instant, never repository creation or discovery time.
            "timestamp": starred_at,
            "import_source": "github_public_starred",
        }
    )
    return build_event(
        event_type="favorite",
        source_platform="github",
        title=repository.full_name,
        url=repository.html_url,
        author=repository.owner_login,
        metadata=metadata,
    )


def _positive_int(value: object, *, name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"GitHub {name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"GitHub {name} must be at most {maximum}")
    return value


async def fetch_github_public_starred_events(
    client: Any,
    *,
    username: object,
    limit: int,
    per_page: int = GITHUB_MAX_PER_PAGE,
    max_pages: int = 10,
    timeout_seconds: float | None = None,
) -> GitHubStarredFetchResult:
    """Fetch bounded public star pages, preserving accepted partial results."""

    normalized_username = validate_github_username(username)
    if not normalized_username:
        raise ValueError("GitHub username is required")
    target = _positive_int(limit, name="limit")
    page_size = _positive_int(per_page, name="per_page", maximum=GITHUB_MAX_PER_PAGE)
    page_budget = _positive_int(max_pages, name="max_pages", maximum=100)
    events: list[dict[str, Any]] = []
    seen_content_ids: set[str] = set()
    seen_pages: set[int] = set()
    page_number = 1
    pages_fetched = 0
    rows_seen = 0
    duplicates = 0
    rejected_private = 0
    rejected_malformed = 0
    item_cap_reached = False
    page_cap_reached = False
    next_page: int | None = None
    terminal_evidence = ""
    error_code = ""
    retry_after_seconds: int | None = None
    deadline = (
        asyncio.get_running_loop().time() + max(0.001, float(timeout_seconds))
        if timeout_seconds is not None
        else None
    )

    while pages_fetched < page_budget:
        try:
            if deadline is None:
                page = await client.get_starred_repositories(
                    normalized_username,
                    page=page_number,
                    per_page=min(page_size, target),
                )
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                page = await asyncio.wait_for(
                    client.get_starred_repositories(
                        normalized_username,
                        page=page_number,
                        per_page=min(page_size, target),
                    ),
                    timeout=remaining,
                )
        except TimeoutError as exc:
            if pages_fetched == 0:
                raise GitHubAPIError(
                    "timeout",
                    "GitHub public starred-repository request timed out",
                ) from exc
            error_code = "timeout"
            terminal_evidence = "partial_timeout"
            break
        except GitHubAPIError as exc:
            if pages_fetched == 0:
                raise
            error_code = exc.code
            retry_after_seconds = exc.retry_after_seconds
            terminal_evidence = "partial_error"
            break
        pages_fetched += 1
        seen_pages.add(page_number)
        rows_seen += len(page.items)
        for row in page.items:
            repository = row.get("repo") if isinstance(row, dict) else None
            if isinstance(repository, dict) and repository.get("private") is True:
                rejected_private += 1
                continue
            event = github_starred_to_event(row, username=normalized_username)
            if event is None:
                rejected_malformed += 1
                continue
            content_id = str(event.get("content_id") or "").strip()
            if not content_id:
                rejected_malformed += 1
                continue
            if content_id in seen_content_ids:
                duplicates += 1
                continue
            seen_content_ids.add(content_id)
            if len(events) < target:
                events.append(event)
            else:
                item_cap_reached = True

        next_page = page.next_page
        if next_page is None:
            terminal_evidence = "link_exhausted"
            break
        if len(events) >= target:
            item_cap_reached = True
            terminal_evidence = "item_cap"
            break
        if pages_fetched >= page_budget:
            page_cap_reached = True
            terminal_evidence = "page_cap"
            break
        if next_page <= page_number or next_page in seen_pages:
            error_code = "schema_changed"
            terminal_evidence = "partial_error"
            break
        page_number = next_page

    if not terminal_evidence:
        # Defensive fallback; a loop should terminate through one of the
        # branches above, never imply completeness by simply ending.
        page_cap_reached = True
        terminal_evidence = "page_cap"
    has_rejections = rejected_private > 0 or rejected_malformed > 0
    scope_complete = (
        terminal_evidence == "link_exhausted"
        and not has_rejections
        and not item_cap_reached
        and not page_cap_reached
        and not error_code
    )
    affirmative_empty = scope_complete and rows_seen == 0
    if affirmative_empty:
        status: GitHubStarredFetchStatus = "empty"
        terminal_evidence = "affirmative_empty"
    elif scope_complete:
        status = "ok"
    else:
        status = "degraded"
        if terminal_evidence == "link_exhausted" and has_rejections:
            terminal_evidence = "link_exhausted_with_rejections"
    return GitHubStarredFetchResult(
        events=events,
        status=status,
        scope_complete=scope_complete,
        affirmative_empty=affirmative_empty,
        terminal_evidence=terminal_evidence,
        pages_fetched=pages_fetched,
        rows_seen=rows_seen,
        duplicates=duplicates,
        rejected_private=rejected_private,
        rejected_malformed=rejected_malformed,
        item_cap_reached=item_cap_reached,
        page_cap_reached=page_cap_reached,
        next_page=next_page,
        error_code=error_code,
        retry_after_seconds=retry_after_seconds,
    )
