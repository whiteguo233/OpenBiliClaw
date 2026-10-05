from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from openbiliclaw.sources.github_client import (
    GITHUB_API_VERSION,
    GITHUB_JSON_MEDIA_TYPE,
    GITHUB_STAR_MEDIA_TYPE,
    GITHUB_TOKEN_ENV,
    GITHUB_USER_AGENT,
    GitHubAPIError,
    GitHubClient,
    github_user_id,
    github_user_login,
    parse_github_link_header,
    resolve_github_access_token,
    resolve_github_bootstrap_identity,
    validate_github_access_token,
    validate_github_username,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "github"


def _fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_search_uses_versioned_official_api_without_ambient_auth_or_cookies() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/search/repositories"
        assert request.headers["Accept"] == GITHUB_JSON_MEDIA_TYPE
        assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
        assert request.headers["User-Agent"] == GITHUB_USER_AGENT
        assert request.headers.get("Authorization") is None
        assert request.headers.get("Cookie") is None
        assert dict(request.url.params) == {
            "q": "local first agent",
            "order": "desc",
            "page": "1",
            "per_page": "1",
            "sort": "stars",
        }
        return httpx.Response(
            200,
            json=_fixture("search_repositories_page.json"),
            headers={
                "Link": (
                    "<https://api.github.com/search/repositories?q=x&page=2&per_page=1>; "
                    'rel="next", '
                    "<https://api.github.com/search/repositories?q=x&page=9&per_page=1>; "
                    'rel="last"'
                )
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=transport,
        headers={"Authorization": "Bearer ambient-secret"},
        cookies={"user_session": "ambient-cookie"},
    ) as http_client:
        client = GitHubClient(http_client=http_client, request_interval_seconds=0)
        page = await client.search_repositories(
            " local first agent ", sort="stars", page=1, per_page=1
        )

    assert len(seen) == 1
    assert page.total_count == 9
    assert page.incomplete_results is False
    assert page.next_page == 2
    assert page.last_page == 9
    assert page.scope_complete is False


@pytest.mark.asyncio
async def test_token_is_explicit_and_can_be_disabled_for_anonymous_fallback() -> None:
    authorizations: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers.get("Authorization"))
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octocat", "id": 1})
        return httpx.Response(200, json=_fixture("search_empty.json"))

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(
            http_client=http_client,
            token="  github_pat_explicit  ",
            request_interval_seconds=0,
        )
        assert client.has_token is client.has_access_token is True
        assert github_user_login(await client.get_user()) == "octocat"
        client.disable_token()
        assert client.has_access_token is False
        await client.search_repositories("agent")

    assert authorizations == ["Bearer github_pat_explicit", None]


@pytest.mark.asyncio
async def test_get_user_without_explicit_token_fails_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(http_client=http_client, request_interval_seconds=0)
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.get_user()

    assert exc_info.value.code == "unauthorized"
    assert calls == 0


def test_access_token_resolution_is_pinned_to_openbiliclaw_env_only() -> None:
    generic_only = {
        "GITHUB_TOKEN": "github-actions-token",
        "GH_TOKEN": "github-cli-token",
        "CUSTOM_GITHUB_PAT": "custom-token",
    }
    assert resolve_github_access_token(environment=generic_only) == ("", "none")
    assert resolve_github_access_token(token_env="GITHUB_TOKEN", environment=generic_only) == (
        "",
        "none",
    )
    assert resolve_github_access_token(token_env="CUSTOM_GITHUB_PAT", environment=generic_only) == (
        "",
        "none",
    )

    explicit = {**generic_only, GITHUB_TOKEN_ENV: "source-token"}
    assert resolve_github_access_token(
        "config-token", token_env="GITHUB_TOKEN", environment=explicit
    ) == ("source-token", "env")
    assert resolve_github_access_token("config-token", environment={}) == (
        "config-token",
        "config",
    )


def test_username_and_token_validation_are_structural_and_fail_closed() -> None:
    assert validate_github_username("  Octo-Cat  ") == "Octo-Cat"
    assert validate_github_access_token("  github_pat_123  ") == "github_pat_123"
    assert validate_github_username(None) == ""
    assert validate_github_access_token(None) == ""
    for invalid in ("-owner", "owner-", "owner--name", "owner/name", "x" * 40):
        with pytest.raises(ValueError):
            validate_github_username(invalid)
    for invalid_token in ("space token", "line\nbreak", "x" * 513, ["token"]):
        with pytest.raises(ValueError):
            validate_github_access_token(invalid_token)


def test_user_identity_extractors_require_authoritative_types() -> None:
    assert github_user_login({"login": "octocat", "id": 1}) == "octocat"
    assert github_user_id({"login": "octocat", "id": 1}) == 1
    with pytest.raises(GitHubAPIError, match="login") as login_error:
        github_user_login({"login": ["octocat"], "id": 1})
    with pytest.raises(GitHubAPIError, match="numeric id") as id_error:
        github_user_id({"login": "octocat", "id": "1"})
    assert login_error.value.code == id_error.value.code == "schema_changed"


@pytest.mark.asyncio
async def test_bootstrap_identity_marks_public_scope_accepted_not_verified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat"
        return httpx.Response(200, json={"login": "OctoCat", "id": 1})

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        identity = await resolve_github_bootstrap_identity(
            GitHubClient(http_client=http_client, request_interval_seconds=0),
            username="octocat",
        )

    assert (identity.login, identity.user_id, identity.evidence) == ("OctoCat", 1, "accepted")


@pytest.mark.asyncio
async def test_bootstrap_identity_matches_token_and_public_username_by_durable_id() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "renamed-user", "id": 7})
        return httpx.Response(200, json={"login": "old-name", "id": 7})

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        identity = await resolve_github_bootstrap_identity(
            GitHubClient(
                http_client=http_client,
                token="token",
                request_interval_seconds=0,
            ),
            username="old-name",
        )

    assert paths == ["/user", "/users/old-name"]
    assert (identity.login, identity.user_id, identity.evidence) == (
        "renamed-user",
        7,
        "verified",
    )


@pytest.mark.asyncio
async def test_bootstrap_identity_stops_on_durable_id_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "token-user", "id": 7})
        return httpx.Response(200, json={"login": "other-user", "id": 8})

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(GitHubAPIError) as exc_info:
            await resolve_github_bootstrap_identity(
                GitHubClient(
                    http_client=http_client,
                    token="token",
                    request_interval_seconds=0,
                ),
                username="other-user",
            )

    assert exc_info.value.code == "identity_mismatch"


@pytest.mark.asyncio
async def test_bootstrap_identity_requires_token_or_explicit_username() -> None:
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    ) as http_client:
        with pytest.raises(GitHubAPIError) as exc_info:
            await resolve_github_bootstrap_identity(
                GitHubClient(http_client=http_client, request_interval_seconds=0)
            )
    assert exc_info.value.code == "identity_required"


@pytest.mark.asyncio
async def test_starred_endpoint_requests_timestamp_wrapper_and_uses_link_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat/starred"
        assert request.headers["Accept"] == GITHUB_STAR_MEDIA_TYPE
        assert dict(request.url.params) == {
            "sort": "created",
            "direction": "desc",
            "page": "1",
            "per_page": "100",
        }
        return httpx.Response(
            200,
            json=_fixture("starred_repositories_page.json"),
            headers={
                "Link": (
                    "<https://api.github.com/users/octocat/starred?page=2&per_page=100>; "
                    'rel="next", '
                    "<https://api.github.com/users/octocat/starred?page=3&per_page=100>; "
                    'rel="last"'
                )
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        page = await GitHubClient(
            http_client=http_client, request_interval_seconds=0
        ).get_starred_repositories("octocat")

    assert len(page.items) == 1
    assert page.next_page == 2
    assert page.last_page == 3
    assert page.scope_complete is False


@pytest.mark.asyncio
async def test_starred_endpoint_accepts_authenticated_user_link_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat/starred"
        return httpx.Response(
            200,
            json=_fixture("starred_repositories_page.json"),
            headers={
                "Link": (
                    "<https://api.github.com/user/123/starred?page=2&per_page=5>; "
                    'rel="next", '
                    "<https://api.github.com/user/123/starred?page=3&per_page=5>; "
                    'rel="last"'
                )
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        page = await GitHubClient(
            http_client=http_client, request_interval_seconds=0
        ).get_starred_repositories("octocat")

    assert len(page.items) == 1
    assert page.next_page == 2
    assert page.last_page == 3
    assert page.scope_complete is False


def test_link_parser_accepts_authenticated_starred_canonical_path_variant() -> None:
    links = parse_github_link_header(
        '<https://api.github.com/user/3350171/starred?page=2&per_page=5>; rel="next", '
        '<https://api.github.com/user/3350171/starred?page=3&per_page=5>; rel="last"',
        expected_path="/users/whiteguo233/starred",
    )
    assert "page=2" in links["next"]
    assert "page=3" in links["last"]


def test_link_parser_rejects_cross_host_path_drift_and_ambiguity() -> None:
    valid = parse_github_link_header(
        '<https://api.github.com/search/repositories?q=ai&page=2>; rel="next"',
        expected_path="/search/repositories",
    )
    assert valid["next"].endswith("page=2")
    for link in (
        '<https://evil.example/search/repositories?page=2>; rel="next"',
        '<http://api.github.com/search/repositories?page=2>; rel="next"',
        '<https://api.github.com/user?page=2>; rel="next"',
        "not-a-link",
        (
            '<https://api.github.com/search/repositories?page=2>; rel="next", '
            '<https://api.github.com/search/repositories?page=3>; rel="next"'
        ),
    ):
        with pytest.raises(GitHubAPIError):
            parse_github_link_header(link, expected_path="/search/repositories")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fixture_name", "headers", "expected_code"),
    [
        (401, "bad_credentials.json", {}, "unauthorized"),
        (401, "requires_authentication.json", {}, "unauthorized"),
        (403, "rate_limited.json", {}, "rate_limited"),
        (429, "rate_limited.json", {"Retry-After": "37"}, "rate_limited"),
        (403, None, {}, "forbidden"),
        (404, None, {}, "not_found"),
        (422, None, {}, "invalid_request"),
        (304, None, {}, "not_modified"),
        (503, None, {}, "upstream_error"),
    ],
)
async def test_status_taxonomy_is_stable_and_never_leaks_body(
    status: int,
    fixture_name: str | None,
    headers: dict[str, str],
    expected_code: str,
) -> None:
    payload = _fixture(fixture_name) if fixture_name else {"message": "unsafe upstream body"}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload, headers=headers)
    )
    async with httpx.AsyncClient(base_url="https://api.github.com", transport=transport) as client:
        github = GitHubClient(
            http_client=client,
            request_interval_seconds=0,
            max_transient_retries=0,
        )
        with pytest.raises(GitHubAPIError) as exc_info:
            await github.search_repositories("agent")

    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == status
    assert "unsafe upstream body" not in str(exc_info.value)
    if status == 429:
        assert exc_info.value.retry_after_seconds == 37


@pytest.mark.asyncio
async def test_rate_limit_403_uses_reset_and_retry_after_without_calling_it_unauthorized() -> None:
    reset = int(time.time()) + 120
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            403,
            json=_fixture("rate_limited.json"),
            headers={
                "Retry-After": "30",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset),
            },
        )
    )
    async with httpx.AsyncClient(base_url="https://api.github.com", transport=transport) as client:
        with pytest.raises(GitHubAPIError) as exc_info:
            await GitHubClient(http_client=client, request_interval_seconds=0).search_repositories(
                "agent"
            )

    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.rate_limit_reset_at == reset
    assert 118 <= (exc_info.value.retry_after_seconds or 0) <= 120


@pytest.mark.asyncio
@pytest.mark.parametrize("first_failure", ["timeout", "503"])
async def test_transient_failure_retries_once_then_succeeds(first_failure: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first_failure == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(503)
        return httpx.Response(200, json=_fixture("search_empty.json"))

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        page = await GitHubClient(
            http_client=http_client,
            request_interval_seconds=0,
            transient_retry_delay_seconds=0,
        ).search_repositories("agent")

    assert calls == 2
    assert page.items == []
    assert page.scope_complete is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, text="<html>challenge</html>"), "invalid_content_type"),
        (
            httpx.Response(
                200,
                content=b"<html>challenge</html>",
                headers={"Content-Type": "application/json"},
            ),
            "invalid_json",
        ),
        (
            httpx.Response(
                200,
                content=b"{not-json}",
                headers={"Content-Type": "application/json"},
            ),
            "invalid_json",
        ),
    ],
)
async def test_success_response_checks_content_type_magic_and_json(
    response: httpx.Response, expected_code: str
) -> None:
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda request: response),
    ) as http_client:
        with pytest.raises(GitHubAPIError) as exc_info:
            await GitHubClient(
                http_client=http_client, request_interval_seconds=0
            ).search_repositories("agent")
    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
async def test_success_body_size_is_bounded_before_json_normalization() -> None:
    oversized = b'[{"padding":"' + (b"x" * 2_000) + b'"}]'
    response = httpx.Response(
        200,
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda request: response),
    ) as http_client:
        with pytest.raises(GitHubAPIError) as exc_info:
            await GitHubClient(
                http_client=http_client,
                request_interval_seconds=0,
                max_response_bytes=1_024,
            ).search_repositories("agent")
    assert exc_info.value.code == "response_too_large"


@pytest.mark.asyncio
async def test_search_preserves_incomplete_flag_and_enforces_1000_result_ceiling() -> None:
    payload = {"total_count": 5_000, "incomplete_results": True, "items": []}
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as http_client:
        page = await GitHubClient(
            http_client=http_client, request_interval_seconds=0
        ).search_repositories("agent", page=10, per_page=100)
        with pytest.raises(ValueError, match="1,000"):
            await GitHubClient(
                http_client=http_client, request_interval_seconds=0
            ).search_repositories("agent", page=11, per_page=100)

    assert page.incomplete_results is True
    assert page.search_capped is True
    assert page.scope_complete is False


@pytest.mark.asyncio
async def test_client_applies_explicit_timeout_and_request_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    timeout_extensions: list[object] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        timeout_extensions.append(request.extensions.get("timeout"))
        return httpx.Response(200, json=_fixture("search_empty.json"))

    monkeypatch.setattr("openbiliclaw.sources.github_client.asyncio.sleep", fake_sleep)
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(
            http_client=http_client,
            request_interval_seconds=1.0,
            transient_retry_delay_seconds=0,
        )
        await client.search_repositories("first")
        await client.search_repositories("second")

    assert sleeps and 0.9 <= sleeps[0] <= 1.0
    assert len(timeout_extensions) == 2
    assert all(isinstance(value, dict) for value in timeout_extensions)
