"""Read-only client for GitHub's versioned official REST API."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from openbiliclaw import __version__
from openbiliclaw.network import outbound_httpx_kwargs, outbound_proxy_mode
from openbiliclaw.sources.platforms import (
    OVERSEAS_DIRECT_MODE_ERROR_SUFFIX,
    PLATFORM_GITHUB,
    requires_overseas_network,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
GITHUB_PROJECT_URL = "https://github.com/whiteguo233/OpenBiliClaw"
GITHUB_USER_AGENT = f"whiteguo233/OpenBiliClaw/{__version__} ({GITHUB_PROJECT_URL})"
GITHUB_TOKEN_ENV = "OPENBILICLAW_GITHUB_TOKEN"
GITHUB_JSON_MEDIA_TYPE = "application/vnd.github+json"
GITHUB_STAR_MEDIA_TYPE = "application/vnd.github.star+json"
GITHUB_MAX_PER_PAGE = 100
GITHUB_SEARCH_RESULT_LIMIT = 1_000
GITHUB_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
GITHUB_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

GitHubCredentialOrigin = Literal["env", "config", "none"]
GitHubIdentityEvidence = Literal["verified", "accepted"]
_GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_LINK_VALUE_RE = re.compile(r"^\s*<([^>]+)>\s*((?:;[^,]*)*)\s*$")
_LINK_PARAM_RE = re.compile(r";\s*([^=;\s]+)\s*=\s*(?:\"([^\"]*)\"|([^;\s]+))")
_TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class GitHubIdentity:
    """Verified identity returned by ``GET /user`` for a supplied token."""

    login: str
    user_id: int
    # ``verified`` comes only from token-authenticated GET /user. A public
    # username existence probe is accepted scope, never proof of ownership.
    evidence: GitHubIdentityEvidence = "verified"


@dataclass(frozen=True)
class GitHubSearchPage:
    """Validated repository-search page plus authoritative Link evidence."""

    items: list[dict[str, Any]]
    total_count: int
    incomplete_results: bool
    page: int
    per_page: int
    next_page: int | None
    last_page: int | None
    next_url: str
    last_url: str
    scope_complete: bool
    search_capped: bool


@dataclass(frozen=True)
class GitHubStarredPage:
    """Validated public starred-repository page plus Link evidence."""

    items: list[dict[str, Any]]
    page: int
    per_page: int
    next_page: int | None
    last_page: int | None
    next_url: str
    last_url: str
    scope_complete: bool


class GitHubAPIError(RuntimeError):
    """Stable, body-safe failure raised by :class:`GitHubClient`."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        rate_limit_reset_at: int | None = None,
    ) -> None:
        super().__init__(message[:240])
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit_reset_at = rate_limit_reset_at


@dataclass(frozen=True)
class _GitHubJSONResponse:
    payload: object
    links: dict[str, str]


def validate_github_username(value: object) -> str:
    """Validate an explicitly configured public ``github.com`` username."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("GitHub username must be a string")
    username = value.strip()
    if not username:
        return ""
    if len(username) > 39 or _GITHUB_LOGIN_RE.fullmatch(username) is None or "--" in username:
        raise ValueError("GitHub username is not a valid github.com login")
    return username


def validate_github_access_token(value: object) -> str:
    """Structurally validate a token without logging or probing its value."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("GitHub access token must be a string")
    token = value.strip()
    if not token:
        return ""
    if len(token) > 512:
        raise ValueError("GitHub access token must be at most 512 characters")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in token):
        raise ValueError("GitHub access token contains an unsupported character")
    return token


def resolve_github_access_token(
    config_token: object = "",
    token_env: str = GITHUB_TOKEN_ENV,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, GitHubCredentialOrigin]:
    """Resolve the source PAT from its explicit env var, then config.

    The generic GitHub Actions ``GITHUB_TOKEN`` is deliberately never read as
    a fallback: a CI-scoped workflow token is not an OpenBiliClaw account
    credential and may point at a different identity or permission scope.
    """

    resolved_environment = os.environ if environment is None else environment
    # ``token_env`` remains in the call signature for config/runtime
    # compatibility, but the frozen source contract permits exactly one env
    # credential name. In particular, never reinterpret GITHUB_TOKEN,
    # GH_TOKEN, or an arbitrary config value as this user's source identity.
    _ = token_env
    env_token = validate_github_access_token(resolved_environment.get(GITHUB_TOKEN_ENV, ""))
    if env_token:
        return env_token, "env"
    configured = validate_github_access_token(config_token)
    if configured:
        return configured, "config"
    return "", "none"


def github_user_login(payload: Mapping[str, Any]) -> str:
    """Extract the authoritative login from a GitHub user response."""

    raw = payload.get("login")
    try:
        login = validate_github_username(raw)
    except ValueError as exc:
        raise GitHubAPIError(
            "schema_changed", "GitHub user response contains an invalid login"
        ) from exc
    if not login:
        raise GitHubAPIError("schema_changed", "GitHub user response is missing a login")
    return login


def github_user_id(payload: Mapping[str, Any]) -> int:
    """Extract the positive durable numeric id from a GitHub user response."""

    user_id = payload.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise GitHubAPIError("schema_changed", "GitHub user response is missing a numeric id")
    return user_id


def parse_github_link_header(
    value: object,
    *,
    expected_path: str = "",
) -> dict[str, str]:
    """Parse GitHub's RFC Link header and reject unsafe pagination targets."""

    if value is None:
        return {}
    if not isinstance(value, str):
        raise GitHubAPIError("schema_changed", "GitHub pagination Link header is malformed")
    raw = value.strip()
    if not raw:
        return {}
    entries = re.split(r",\s*(?=<)", raw)
    links: dict[str, str] = {}
    normalized_expected_path = expected_path.rstrip("/") or "/"
    for entry in entries:
        match = _LINK_VALUE_RE.fullmatch(entry)
        if match is None:
            raise GitHubAPIError("schema_changed", "GitHub pagination Link header is malformed")
        target = match.group(1).strip()
        parameters = {
            key.casefold(): quoted if quoted is not None else bare
            for key, quoted, bare in _LINK_PARAM_RE.findall(match.group(2))
        }
        relations = str(parameters.get("rel") or "").split()
        if not relations:
            continue
        try:
            parsed = urlsplit(target)
            port = parsed.port
        except ValueError as exc:
            raise GitHubAPIError(
                "schema_changed", "GitHub pagination Link target is malformed"
            ) from exc
        target_path = parsed.path.rstrip("/") or "/"
        path_allowed = not expected_path or target_path == normalized_expected_path
        if not path_allowed and normalized_expected_path.endswith("/starred"):
            # GitHub's authenticated starred endpoint may emit pagination links
            # under the canonical /user/<id>/starred path even when the original
            # request used /users/<username>/starred. Both are safe public
            # GitHub API resource paths; keep rejecting every other drift.
            path_allowed = bool(
                re.fullmatch(r"/(?:user|users)/[^/]+/starred", target_path)
                and re.fullmatch(r"/(?:user|users)/[^/]+/starred", normalized_expected_path)
            )
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() != "api.github.com"
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.fragment
            or not path_allowed
        ):
            raise GitHubAPIError(
                "schema_changed", "GitHub API returned an unsafe pagination Link target"
            )
        for relation in relations:
            normalized_relation = relation.casefold()
            if normalized_relation in links and links[normalized_relation] != target:
                raise GitHubAPIError(
                    "schema_changed", "GitHub API returned ambiguous pagination links"
                )
            links[normalized_relation] = target
    return links


def _link_page(links: Mapping[str, str], relation: str) -> int | None:
    target = links.get(relation)
    if not target:
        return None
    values = parse_qs(urlsplit(target).query, keep_blank_values=True).get("page")
    if values is None or len(values) != 1:
        raise GitHubAPIError("schema_changed", "GitHub pagination link is missing a page")
    try:
        page = int(values[0])
    except (TypeError, ValueError) as exc:
        raise GitHubAPIError("schema_changed", "GitHub pagination page is malformed") from exc
    if page <= 0:
        raise GitHubAPIError("schema_changed", "GitHub pagination page must be positive")
    return page


def _page_number(value: object, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"GitHub {name} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(f"GitHub {name} must be an integer")
    if number <= 0:
        raise ValueError(f"GitHub {name} must be positive")
    return number


def _per_page(value: object) -> int:
    per_page = _page_number(value, name="per_page")
    if per_page > GITHUB_MAX_PER_PAGE:
        raise ValueError(f"GitHub per_page must be at most {GITHUB_MAX_PER_PAGE}")
    return per_page


def _response_mime_type(response: httpx.Response) -> str:
    content_type = str(response.headers.get("Content-Type", ""))
    return content_type.split(";", 1)[0].strip().casefold()


def _bounded_header_int(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError:
        return None
    return number if number >= 0 else None


def _rate_limit_wait(response: httpx.Response) -> tuple[int | None, int | None]:
    """Resolve a bounded wait from Retry-After and X-RateLimit-Reset."""

    now = datetime.now(UTC)
    candidates: list[int] = []
    retry_after = str(response.headers.get("Retry-After") or "").strip()
    if retry_after:
        try:
            candidates.append(max(0, int(retry_after)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_at = None
            if retry_at is not None:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                candidates.append(max(0, math.ceil((retry_at - now).total_seconds())))
    reset_at = _bounded_header_int(response.headers.get("X-RateLimit-Reset"))
    if reset_at is not None:
        candidates.append(max(0, math.ceil(reset_at - now.timestamp())))
    wait = min(86_400, max(candidates)) if candidates else None
    return wait, reset_at


async def _read_bounded_body(response: httpx.Response, *, limit: int) -> bytes:
    content_length = _bounded_header_int(response.headers.get("Content-Length"))
    if content_length is not None and content_length > limit:
        raise GitHubAPIError(
            "response_too_large",
            "GitHub API response exceeded the safe body limit",
            status_code=response.status_code,
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise GitHubAPIError(
                "response_too_large",
                "GitHub API response exceeded the safe body limit",
                status_code=response.status_code,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _error_message_from_json(body: bytes, response: httpx.Response) -> str:
    if not body or _response_mime_type(response) != "application/json":
        return ""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    return message.strip()[:240] if isinstance(message, str) else ""


class GitHubClient:
    """Minimal serial, paced client for public repository reads and identity."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        token: object = None,
        request_interval_seconds: float = 0.1,
        transient_retry_delay_seconds: float = 0.25,
        max_transient_retries: int = 1,
        timeout: httpx.Timeout | float = GITHUB_REQUEST_TIMEOUT,
        max_response_bytes: int = GITHUB_MAX_RESPONSE_BYTES,
    ) -> None:
        self._owns_http_client = http_client is None
        self._timeout = timeout
        self._http = http_client or httpx.AsyncClient(
            base_url=GITHUB_API_BASE_URL,
            timeout=timeout,
            follow_redirects=False,
            **outbound_httpx_kwargs(),
        )
        self._outbound_mode = outbound_proxy_mode() if self._owns_http_client else ""
        self._token = validate_github_access_token(token) or None
        self._request_interval_seconds = max(0.0, float(request_interval_seconds))
        self._transient_retry_delay_seconds = max(0.0, float(transient_retry_delay_seconds))
        self._max_transient_retries = min(3, max(0, int(max_transient_retries)))
        self._max_response_bytes = max(1_024, int(max_response_bytes))
        self._request_lock = asyncio.Lock()
        self._last_request_started_at = 0.0

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    @property
    def has_access_token(self) -> bool:
        """Whether requests currently carry the explicitly supplied PAT."""

        return self._token is not None

    @property
    def has_token(self) -> bool:
        """Compatibility alias for :attr:`has_access_token`."""

        return self.has_access_token

    def disable_access_token(self) -> None:
        """Drop a rejected PAT so public discovery can continue anonymously."""

        self._token = None

    def disable_token(self) -> None:
        """Compatibility alias for :meth:`disable_access_token`."""

        self.disable_access_token()

    async def get_user(self) -> dict[str, Any]:
        """Return the account verified by the configured PAT (``GET /user``)."""

        if self._token is None:
            raise GitHubAPIError("unauthorized", "GitHub GET /user requires an access token")
        response = await self._request_json("GET", "/user")
        return self._mapping_payload(response.payload, endpoint="GET /user")

    async def get_user_profile(self, username: object) -> dict[str, Any]:
        """Fetch one public user profile (existence evidence, not ownership)."""

        normalized = validate_github_username(username)
        if not normalized:
            raise ValueError("GitHub username is required")
        response = await self._request_json("GET", f"/users/{quote(normalized, safe='')}")
        return self._mapping_payload(response.payload, endpoint="GET /users/{username}")

    async def get_public_user(self, username: object) -> dict[str, Any]:
        """Alias for :meth:`get_user_profile`."""

        return await self.get_user_profile(username)

    async def search_repositories(
        self,
        query: object,
        *,
        sort: str = "",
        order: str = "desc",
        page: int = 1,
        per_page: int = 30,
    ) -> GitHubSearchPage:
        """Search public repositories through ``GET /search/repositories``."""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("GitHub repository search query is required")
        normalized_sort = str(sort or "").strip().casefold()
        if normalized_sort not in {"", "stars", "forks", "help-wanted-issues", "updated"}:
            raise ValueError(f"unsupported GitHub repository sort: {sort!r}")
        normalized_order = str(order or "").strip().casefold()
        if normalized_order not in {"asc", "desc"}:
            raise ValueError(f"unsupported GitHub repository order: {order!r}")
        page_number = _page_number(page, name="page")
        page_size = _per_page(per_page)
        if (page_number - 1) * page_size >= GITHUB_SEARCH_RESULT_LIMIT:
            raise ValueError("GitHub repository search cannot page beyond 1,000 results")
        params: dict[str, str | int] = {
            "q": query.strip(),
            "order": normalized_order,
            "page": page_number,
            "per_page": page_size,
        }
        if normalized_sort:
            params["sort"] = normalized_sort
        response = await self._request_json("GET", "/search/repositories", params=params)
        payload = self._mapping_payload(response.payload, endpoint="GET /search/repositories")
        total_count = payload.get("total_count")
        incomplete_results = payload.get("incomplete_results")
        items = payload.get("items")
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or not isinstance(incomplete_results, bool)
            or not isinstance(items, list)
            or not all(isinstance(row, dict) for row in items)
        ):
            raise GitHubAPIError(
                "schema_changed", "GitHub repository search response shape changed"
            )
        next_page = _link_page(response.links, "next")
        last_page = _link_page(response.links, "last")
        search_capped = total_count > GITHUB_SEARCH_RESULT_LIMIT
        if next_page is not None and (next_page - 1) * page_size >= GITHUB_SEARCH_RESULT_LIMIT:
            next_page = None
            search_capped = True
        scope_complete = not incomplete_results and next_page is None and not search_capped
        return GitHubSearchPage(
            items=[dict(row) for row in items],
            total_count=total_count,
            incomplete_results=incomplete_results,
            page=page_number,
            per_page=page_size,
            next_page=next_page,
            last_page=last_page,
            next_url=response.links.get("next", ""),
            last_url=response.links.get("last", ""),
            scope_complete=scope_complete,
            search_capped=search_capped,
        )

    async def get_starred_repositories(
        self,
        username: object,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> GitHubStarredPage:
        """Read a user's public stars with authoritative ``starred_at`` wrappers."""

        normalized = validate_github_username(username)
        if not normalized:
            raise ValueError("GitHub username is required")
        page_number = _page_number(page, name="page")
        page_size = _per_page(per_page)
        path = f"/users/{quote(normalized, safe='')}/starred"
        response = await self._request_json(
            "GET",
            path,
            params={
                "sort": "created",
                "direction": "desc",
                "page": page_number,
                "per_page": page_size,
            },
            accept=GITHUB_STAR_MEDIA_TYPE,
        )
        items = response.payload
        if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
            raise GitHubAPIError(
                "schema_changed", "GitHub starred-repositories response shape changed"
            )
        next_page = _link_page(response.links, "next")
        last_page = _link_page(response.links, "last")
        return GitHubStarredPage(
            items=[dict(row) for row in items],
            page=page_number,
            per_page=page_size,
            next_page=next_page,
            last_page=last_page,
            next_url=response.links.get("next", ""),
            last_url=response.links.get("last", ""),
            scope_complete=next_page is None,
        )

    async def _pace(self) -> None:
        now = time.monotonic()
        remaining = self._request_interval_seconds - (now - self._last_request_started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_started_at = time.monotonic()

    def _network_failure_message(self, message: str) -> str:
        if self._outbound_mode != "direct" or not requires_overseas_network(PLATFORM_GITHUB):
            return message
        return f"{message}{OVERSEAS_DIRECT_MODE_ERROR_SUFFIX}"

    def _headers(self, *, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": GITHUB_USER_AGENT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        accept: str = GITHUB_JSON_MEDIA_TYPE,
    ) -> _GitHubJSONResponse:
        async with self._request_lock:
            response: httpx.Response | None = None
            for attempt in range(self._max_transient_retries + 1):
                await self._pace()
                request = self._http.build_request(
                    method,
                    path,
                    params=params,
                    headers=self._headers(accept=accept),
                    timeout=self._timeout,
                )
                # An injected client may carry ambient cookies or a default
                # Authorization header. Neither is evidence for this source.
                request.headers.pop("Cookie", None)
                request.headers.pop("Authorization", None)
                if self._token is not None:
                    request.headers["Authorization"] = f"Bearer {self._token}"
                try:
                    response = await self._http.send(request, stream=True)
                except httpx.TimeoutException as exc:
                    if attempt < self._max_transient_retries:
                        await self._transient_delay()
                        continue
                    raise GitHubAPIError(
                        "timeout",
                        self._network_failure_message("GitHub API request timed out"),
                    ) from exc
                except httpx.HTTPError as exc:
                    if attempt < self._max_transient_retries:
                        await self._transient_delay()
                        continue
                    raise GitHubAPIError(
                        "network_error",
                        self._network_failure_message("GitHub API network request failed"),
                    ) from exc
                if (
                    response.status_code in _TRANSIENT_STATUS_CODES
                    and attempt < self._max_transient_retries
                ):
                    await response.aclose()
                    response = None
                    await self._transient_delay()
                    continue
                break
            if response is None:
                raise GitHubAPIError(
                    "network_error", self._network_failure_message("GitHub API request failed")
                )
            try:
                return await self._decode_response(response)
            finally:
                await response.aclose()

    async def _decode_response(self, response: httpx.Response) -> _GitHubJSONResponse:
        status = int(response.status_code)
        retry_after_seconds, reset_at = _rate_limit_wait(response)
        if status == 304:
            raise GitHubAPIError(
                "not_modified", "GitHub API resource was unchanged", status_code=status
            )
        if status == 401:
            raise GitHubAPIError(
                "unauthorized",
                "GitHub rejected the access token (missing, invalid, or expired)",
                status_code=status,
            )
        if status == 429:
            raise GitHubAPIError(
                "rate_limited",
                "GitHub API rate limited this client",
                status_code=status,
                retry_after_seconds=retry_after_seconds or 60,
                rate_limit_reset_at=reset_at,
            )
        if status == 403:
            body = await _read_bounded_body(response, limit=min(65_536, self._max_response_bytes))
            error_message = _error_message_from_json(body, response).casefold()
            rate_limited = (
                response.headers.get("X-RateLimit-Remaining", "").strip() == "0"
                or bool(response.headers.get("Retry-After", "").strip())
                or "rate limit" in error_message
                or "abuse detection" in error_message
            )
            if rate_limited:
                raise GitHubAPIError(
                    "rate_limited",
                    "GitHub API rate limited this client",
                    status_code=status,
                    retry_after_seconds=retry_after_seconds or 60,
                    rate_limit_reset_at=reset_at,
                )
            raise GitHubAPIError(
                "forbidden", "GitHub API denied this read request", status_code=status
            )
        if status == 404:
            raise GitHubAPIError("not_found", "GitHub resource was not found", status_code=status)
        if status in (400, 422):
            raise GitHubAPIError(
                "invalid_request", "GitHub API rejected the request", status_code=status
            )
        if status >= 500:
            raise GitHubAPIError(
                "upstream_error",
                "GitHub API is temporarily unavailable",
                status_code=status,
            )
        if status < 200 or status >= 300:
            raise GitHubAPIError(
                "http_error", f"GitHub API returned HTTP {status}", status_code=status
            )
        if _response_mime_type(response) != "application/json":
            raise GitHubAPIError(
                "invalid_content_type",
                "GitHub API returned a non-JSON success response",
                status_code=status,
            )
        body = await _read_bounded_body(response, limit=self._max_response_bytes)
        if not body or body.lstrip()[:1] not in {b"{", b"["}:
            raise GitHubAPIError(
                "invalid_json", "GitHub API returned invalid JSON", status_code=status
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitHubAPIError(
                "invalid_json", "GitHub API returned invalid JSON", status_code=status
            ) from exc
        links = parse_github_link_header(
            response.headers.get("Link"), expected_path=response.request.url.path
        )
        return _GitHubJSONResponse(payload=payload, links=links)

    async def _transient_delay(self) -> None:
        if self._transient_retry_delay_seconds:
            await asyncio.sleep(self._transient_retry_delay_seconds)

    @staticmethod
    def _mapping_payload(payload: object, *, endpoint: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GitHubAPIError("schema_changed", f"GitHub {endpoint} response shape changed")
        return dict(payload)


async def resolve_github_access_token_identity(
    token: object,
    *,
    request_interval_seconds: float = 0.1,
) -> GitHubIdentity:
    """Live-validate a PAT via ``GET /user`` and return its durable identity."""

    normalized = validate_github_access_token(token)
    if not normalized:
        raise ValueError("GitHub access token is required")
    async with GitHubClient(
        token=normalized,
        request_interval_seconds=request_interval_seconds,
    ) as client:
        payload = await client.get_user()
    return GitHubIdentity(login=github_user_login(payload), user_id=github_user_id(payload))


async def resolve_github_bootstrap_identity(
    client: GitHubClient,
    *,
    username: object = "",
) -> GitHubIdentity:
    """Resolve the canonical account scope for public-star bootstrap.

    A token-authenticated ``GET /user`` is the only ownership proof. When a
    configured public username is also present, its independently fetched
    numeric id must match the token identity; a mismatch stops bootstrap.
    Without a token, an explicit username is required and returned with
    ``evidence="accepted"`` after ``GET /users/{username}`` confirms that the
    public account exists. That accepted scope is deliberately not labelled
    verified ownership.
    """

    normalized_username = validate_github_username(username)
    if client.has_access_token:
        authenticated_payload = await client.get_user()
        authenticated = GitHubIdentity(
            login=github_user_login(authenticated_payload),
            user_id=github_user_id(authenticated_payload),
            evidence="verified",
        )
        if not normalized_username:
            return authenticated
        public_payload = await client.get_user_profile(normalized_username)
        public_id = github_user_id(public_payload)
        if public_id != authenticated.user_id:
            raise GitHubAPIError(
                "identity_mismatch",
                "Configured GitHub username does not match the access token identity",
            )
        return authenticated
    if not normalized_username:
        raise GitHubAPIError(
            "identity_required",
            "GitHub bootstrap requires an access token or explicit public username",
        )
    public_payload = await client.get_user_profile(normalized_username)
    return GitHubIdentity(
        login=github_user_login(public_payload),
        user_id=github_user_id(public_payload),
        evidence="accepted",
    )
