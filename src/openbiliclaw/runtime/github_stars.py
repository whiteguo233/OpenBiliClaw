"""Rate-limit-safe GitHub repository star count lookup."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

import httpx

from openbiliclaw.network import outbound_httpx_kwargs

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

ProjectStatsSource = Literal["github", "cache", "unavailable"]


class GitHubStarCountService:
    """Fetch and cache a public repository's star count without retry storms.

    Browser clients call the local API instead of GitHub directly. The service
    keeps an in-memory and optional on-disk cache, uses ETag revalidation, and
    backs off after rate limits or network failures. Callers always receive a
    usable snapshot shape; upstream failures never need to become HTTP errors.
    """

    def __init__(
        self,
        *,
        repository: str = "whiteguo233/OpenBiliClaw",
        cache_path: Path | None = None,
        ttl_seconds: float = 12 * 60 * 60,
        failure_backoff_seconds: float = 15 * 60,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.time,
        token: str | None = None,
    ) -> None:
        self._repository = repository
        self._cache_path = cache_path
        self._ttl_seconds = ttl_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
        self._client_factory = client_factory or self._make_client
        self._clock = clock
        if token is not None:
            self._token = token.strip()
        else:
            self._token = os.environ.get(
                "OPENBILICLAW_GITHUB_TOKEN",
                os.environ.get("GITHUB_TOKEN", ""),
            ).strip()
        self._count: int | None = None
        self._etag = ""
        self._fetched_at = 0.0
        self._retry_at = 0.0
        self._cache_loaded = False
        self._lock = asyncio.Lock()

    @staticmethod
    def _make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=5.0, verify=True, **outbound_httpx_kwargs())

    def _snapshot(self, *, source: ProjectStatsSource, stale: bool) -> dict[str, object]:
        return {
            "github_stars": self._count,
            "stale": stale,
            "source": source,
        }

    def _cached_snapshot(self, now: float) -> dict[str, object]:
        if self._count is None:
            return self._snapshot(source="unavailable", stale=True)
        return self._snapshot(
            source="cache",
            stale=now - self._fetched_at >= self._ttl_seconds,
        )

    def _load_cache(self) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if self._cache_path is None:
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            count = payload.get("github_stars")
            fetched_at = payload.get("fetched_at")
            retry_at = payload.get("retry_at", 0)
            etag = payload.get("etag", "")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                self._count = count
            if isinstance(fetched_at, (int, float)) and not isinstance(fetched_at, bool):
                self._fetched_at = max(0.0, float(fetched_at))
            if isinstance(retry_at, (int, float)) and not isinstance(retry_at, bool):
                self._retry_at = max(0.0, float(retry_at))
            if isinstance(etag, str):
                self._etag = etag
        except (OSError, ValueError, TypeError):
            logger.debug("Could not read GitHub star count cache", exc_info=True)

    def _save_cache(self) -> None:
        if self._cache_path is None:
            return
        payload = {
            "github_stars": self._count,
            "fetched_at": self._fetched_at,
            "retry_at": self._retry_at,
            "etag": self._etag,
        }
        temporary_path = self._cache_path.with_suffix(f"{self._cache_path.suffix}.tmp")
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(self._cache_path)
        except OSError:
            logger.debug("Could not persist GitHub star count cache", exc_info=True)

    def _defer_retry(self, response: httpx.Response | None, now: float) -> None:
        retry_at = now + self._failure_backoff_seconds
        if response is not None:
            retry_after = response.headers.get("Retry-After", "").strip()
            rate_limit_reset = response.headers.get("X-RateLimit-Reset", "").strip()
            with suppress(ValueError):
                retry_at = max(retry_at, now + max(0.0, float(retry_after)))
            with suppress(ValueError):
                retry_at = max(retry_at, float(rate_limit_reset))
        # Do not allow a malformed upstream header to suppress refresh forever.
        self._retry_at = min(retry_at, now + 24 * 60 * 60)
        self._save_cache()

    async def _try_shields_star_count(self, now: float) -> int | None:
        """Fallback to the shields.io badge service for an approximate count.

        GitHub's unauthenticated REST API is per-IP rate limited; shields.io's
        badge JSON is a stable, cached endpoint that isn't subject to that
        budget.  The count is rounded (e.g. ``"1.9k"``), which is fine for a
        star counter.  Returns the parsed count, or ``None`` on any failure so
        callers keep the cached snapshot.
        """
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    f"https://img.shields.io/github/stars/{self._repository}.json"
                )
            if response.status_code != 200:
                return None
            value = str((response.json() or {}).get("value", "")).strip()
            count = self._parse_shields_value(value)
            if count is None or count < 0:
                return None
        except (httpx.HTTPError, OSError, ValueError, AttributeError):
            logger.debug("shields.io star count fallback failed", exc_info=True)
            return None
        self._count = count
        self._fetched_at = now
        self._retry_at = 0.0
        self._save_cache()
        return count

    @staticmethod
    def _parse_shields_value(value: str) -> int | None:
        """Parse a shields.io badge value like ``"1.9k"`` / ``"16.7k"`` / ``"1673"``."""
        text = value.strip().lower().replace(",", "")
        if not text:
            return None
        try:
            if text.endswith("k"):
                return int(float(text[:-1]) * 1_000)
            if text.endswith("m"):
                return int(float(text[:-1]) * 1_000_000)
            return int(float(text))
        except ValueError:
            return None

    async def get_snapshot(self) -> dict[str, object]:
        """Return the freshest available count without surfacing upstream errors."""
        self._load_cache()
        now = self._clock()
        if self._count is not None and now - self._fetched_at < self._ttl_seconds:
            return self._cached_snapshot(now)

        async with self._lock:
            now = self._clock()
            if self._count is not None and now - self._fetched_at < self._ttl_seconds:
                return self._cached_snapshot(now)

            # Primary: GitHub REST API (exact, ETag, token-aware). Skip it while
            # inside a rate-limit backoff window — the shields.io fallback below
            # still runs, so a token isn't required for freshness.
            if now >= self._retry_at:
                headers = {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "OpenBiliClaw",
                }
                if self._token:
                    headers["Authorization"] = f"Bearer {self._token}"
                if self._etag:
                    headers["If-None-Match"] = self._etag
                try:
                    async with self._client_factory() as client:
                        response = await client.get(
                            f"https://api.github.com/repos/{self._repository}",
                            headers=headers,
                        )
                except (httpx.HTTPError, OSError):
                    logger.debug("GitHub star count request failed", exc_info=True)
                    self._defer_retry(None, now)
                else:
                    if response.status_code == 304 and self._count is not None:
                        self._fetched_at = now
                        self._retry_at = 0.0
                        self._save_cache()
                        return self._snapshot(source="github", stale=False)

                    if response.status_code == 200:
                        try:
                            count: Any = response.json().get("stargazers_count")
                        except (ValueError, AttributeError):
                            count = None
                        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                            self._count = count
                            self._etag = response.headers.get("ETag", "")
                            self._fetched_at = now
                            self._retry_at = 0.0
                            self._save_cache()
                            return self._snapshot(source="github", stale=False)

                    self._defer_retry(response, now)

            # Fallback: shields.io badge (not subject to the API's per-IP rate
            # limit), so a token isn't required for freshness.
            if await self._try_shields_star_count(now) is not None:
                return self._snapshot(source="github", stale=False)

            return self._cached_snapshot(now)
