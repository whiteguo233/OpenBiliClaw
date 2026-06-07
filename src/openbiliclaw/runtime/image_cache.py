"""Local cover-image disk cache: shared key primitives + cleanup.

The image proxy (``GET /api/image-proxy``) caches successfully fetched cover
images under ``data/image-cache/`` so covers keep loading after an upstream
CDN's signed URL token expires. This matters most for Xiaohongshu, whose
``sns-webpic-qc.xhscdn.com`` URLs carry a short-lived ``{timestamp}/{token}``
prefix — once it expires the only durable copy of the cover is the cached one.

This module owns the cache-key normalization (single source of truth, also
imported by :mod:`openbiliclaw.api.app`) and the consumption-aware cleanup that
bounds disk growth without deleting covers that can never be re-fetched.

Cleanup rules (unioned), see :func:`cleanup_image_cache`:

* **Consumed + unsaved** — covers of content whose ``pool_status`` is terminal
  (the user has seen / passed / it aged out) and that is not in favorites or
  watch-later are evicted. Re-fetchable covers (Bilibili etc., stable URLs) are
  always safe — they re-download on next view.
* **Un-refetchable protection** — covers carrying a rotating token (XHS) are
  protected from consumption eviction by default; the cache is their only copy.
* **Aged orphans** — cached files no live content row references are removed
  once older than ``max_age_days`` (bounded-growth backstop / degraded mode).
"""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

# Resolved lazily (and cached) under the configured data dir — NOT a relative
# "data/image-cache", which resolved against the process CWD (the read-only
# install dir on packaged Windows) instead of the user's data dir.
_CACHE_DIR: Path | None = None


def _resolve_cache_dir() -> Path:
    """Locate the image cache under the configured data dir.

    Uses ``Config.data_path`` (which honours ``OPENBILICLAW_PROJECT_ROOT`` and a
    custom ``data_dir``), so the cache lives with the user's data — e.g.
    ``%LOCALAPPDATA%/OpenBiliClaw/data/image-cache`` — not next to the packaged
    executable. Falls back to the env-aware project root if config can't load yet.
    """
    try:
        from openbiliclaw.config import load_config

        return load_config().data_path / "image-cache"
    except Exception:  # noqa: BLE001 — config not ready → still env-aware fallback
        from openbiliclaw.config import _project_root

        return _project_root() / "data" / "image-cache"

# XHS CDN signed URL: https://sns-webpic-qc.xhscdn.com/{ts:12}/{token:hex}/{path}
# The {ts}/{token} prefix rotates on every regeneration; {path} is stable.
_XHS_TOKEN_RE = re.compile(r"(https?://[^/]*xhscdn\.com)/\d{12}/[0-9a-f]+/(.*)")

# content_cache.pool_status values that mean "the user is done with this item":
# it has been surfaced and acted on, or aged out. ``fresh`` (pending) and
# ``suppressed`` (temporarily hidden, may revive to fresh) are intentionally
# excluded — their covers are still needed.
CONSUMED_POOL_STATUSES: frozenset[str] = frozenset(
    {"shown", "feedbacked", "stale", "purged_by_dislike"}
)

_VALID_IMAGE_EXTS: frozenset[str] = frozenset({"jpeg", "jpg", "png", "webp", "avif", "gif"})


def _https_normalize(url: str) -> str:
    """Mirror the frontend ``normalizeCoverUrl``: protocol-relative / http -> https.

    The browser applies this before building the proxy URL, so the cache is
    keyed on the https form. Cleanup reads raw ``content_cache.cover_url`` (which
    may be ``//…`` or ``http://…``) and must apply the same step to match.
    """
    u = (url or "").strip()
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("http://"):
        return f"https://{u[len('http://') :]}"
    return u


def normalize_cache_url(url: str) -> str:
    """Normalize a cover URL to a stable cache identity (https + token-stripped)."""
    u = _https_normalize(url)
    m = _XHS_TOKEN_RE.match(u)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return u


def image_cache_key(url: str) -> str:
    """SHA-256 of the normalized URL — the cache filename stem."""
    return hashlib.sha256(normalize_cache_url(url).encode()).hexdigest()


def is_refetchable(url: str) -> bool:
    """Whether the cover can be re-fetched after eviction.

    False only for URLs carrying a rotating/expiring token (XHS) — the cached
    copy is their sole durable source, so cleanup must not delete them.
    """
    return _XHS_TOKEN_RE.match(_https_normalize(url)) is None


def image_cache_dir() -> Path:
    """Return the cache directory (resolved once, under the data dir), creating it."""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = _resolve_cache_dir()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def image_cache_extension(content_type: str) -> str:
    """Map a ``Content-Type`` to a cache file extension (defaults to ``jpg``)."""
    ext = content_type.split("/")[-1].split(";")[0].strip().lower()
    return ext if ext in _VALID_IMAGE_EXTS else "jpg"


@dataclass
class CleanupResult:
    """Outcome of one :func:`cleanup_image_cache` pass."""

    removed: int = 0
    freed_bytes: int = 0
    removed_consumed: int = 0
    removed_aged_orphans: int = 0
    protected_unrefetchable: int = 0


class CoverLifecycleSource(Protocol):
    """Minimal database surface required by :func:`cleanup_image_cache`."""

    def iter_cover_lifecycle(self) -> Iterable[tuple[str, str, bool]]:
        """Yield ``(cover_url, pool_status, is_saved)`` for every cached candidate."""
        ...


def cleanup_image_cache(
    *,
    database: CoverLifecycleSource | None = None,
    max_age_days: int = 30,
    consumed_statuses: Iterable[str] = CONSUMED_POOL_STATUSES,
    protect_unrefetchable: bool = True,
    cache_dir: Path | None = None,
    now: float | None = None,
) -> CleanupResult:
    """Prune cached cover images.

    Args:
        database: source of cover lifecycle rows. When ``None`` (degraded mode)
            only the aged-orphan backstop runs.
        max_age_days: aged-orphan cutoff for files no content row references.
        consumed_statuses: ``pool_status`` values treated as consumed.
        protect_unrefetchable: keep covers that cannot be re-fetched (XHS tokens)
            even when their content is consumed + unsaved.
        cache_dir: override the cache directory (tests).
        now: override the current epoch seconds (tests).

    Returns:
        A :class:`CleanupResult` with counts and freed bytes.
    """
    directory = cache_dir if cache_dir is not None else image_cache_dir()
    result = CleanupResult()
    current = time.time() if now is None else now
    cutoff = current - max_age_days * 86400

    files = [p for p in directory.glob("*.*") if p.is_file()]
    if not files:
        return result

    # Aggregate per cache key across all referencing content rows.
    needed: set[str] = set()  # some row is saved or still pending -> never evict
    consumed_only: set[str] = set()  # every referencing row is consumed + unsaved
    unrefetchable: set[str] = set()
    referenced: set[str] = set()
    consumed = frozenset(consumed_statuses)
    if database is not None:
        with suppress(Exception):
            for cover_url, status, is_saved in database.iter_cover_lifecycle():
                if not cover_url:
                    continue
                key = image_cache_key(cover_url)
                referenced.add(key)
                if not is_refetchable(cover_url):
                    unrefetchable.add(key)
                if is_saved or status not in consumed:
                    needed.add(key)
                else:
                    consumed_only.add(key)

    def _unlink(path: Path) -> int | None:
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            return None
        return size

    for path in files:
        key = path.stem
        if key not in referenced:
            # Orphan: no content row points here. Remove once aged out.
            with suppress(OSError):
                if path.stat().st_mtime >= cutoff:
                    continue
            size = _unlink(path)
            if size is not None:
                result.removed += 1
                result.freed_bytes += size
                result.removed_aged_orphans += 1
            continue
        if key in needed:
            # Still pending or saved (favorite / watch-later) — always keep.
            continue
        if key not in consumed_only:
            continue
        if protect_unrefetchable and key in unrefetchable:
            result.protected_unrefetchable += 1
            continue
        size = _unlink(path)
        if size is not None:
            result.removed += 1
            result.freed_bytes += size
            result.removed_consumed += 1

    return result


# ── Cover fetch (shared by the proxy route and the prefetch sweep) ──────────
#
# The whitelist + redirect + size/type validation below is the SSRF-protection
# boundary for every server-side image fetch. It lives here (single source of
# truth) so both ``api.app``'s ``/api/image-proxy`` route and RefreshRuntime's
# prefetch sweep share identical security checks. Failures raise CoverFetchError
# carrying the HTTP status the proxy exposes; the route maps it to HTTPException.

ALLOWED_IMAGE_HOST_SUFFIXES: tuple[str, ...] = (
    "hdslb.com",
    "xhscdn.com",
    "pstatp.com",
    "douyinpic.com",
    "douyinvod.com",
    "ytimg.com",
    "ggpht.com",
)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_FETCH_TIMEOUT_SECONDS = 10.0
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_UPSTREAM_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    ),
}


class CoverFetchError(Exception):
    """A cover could not be fetched. ``status_code`` mirrors the proxy's HTTP semantics."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def is_allowed_image_host(hostname: str) -> bool:
    """Domain-boundary whitelist match (``host == suffix`` or ``*.suffix``)."""
    host = hostname.rstrip(".").lower()
    return any(
        host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_IMAGE_HOST_SUFFIXES
    )


def is_allowed_image_url(url: str) -> bool:
    """Cheap pre-check (no network) used to skip non-proxyable URLs.

    Accepts the protocol-relative ``//host/…`` and ``http://`` forms stored in
    ``content_cache.cover_url`` by normalizing to https first (mirrors the cache key).
    """
    try:
        parsed = httpx.URL(_https_normalize(url))
    except httpx.InvalidURL:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.host
        and not parsed.userinfo
        and is_allowed_image_host(parsed.host)
    )


def _parse_image_url(raw_url: str) -> httpx.URL:
    # Normalize //host and http:// to https first so the prefetch path (which reads
    # raw content_cache.cover_url) and the proxy path (already-normalized) agree, and
    # the fetched bytes cache under the same key the proxy looks up.
    try:
        parsed = httpx.URL(_https_normalize(raw_url))
    except httpx.InvalidURL as exc:
        raise CoverFetchError(400, "Invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise CoverFetchError(400, "Invalid URL")
    if parsed.userinfo:
        raise CoverFetchError(400, "Invalid URL")
    if not is_allowed_image_host(parsed.host):
        raise CoverFetchError(403, "Domain not in whitelist")
    return parsed


def _validate_content_headers(headers: httpx.Headers) -> str:
    content_type = str(headers.get("content-type", "")).strip()
    if not content_type.lower().startswith("image/"):
        raise CoverFetchError(400, "Not an image")
    content_length = headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise CoverFetchError(502, "Invalid upstream content length") from exc
        if size > MAX_IMAGE_BYTES:
            raise CoverFetchError(413, "Image too large")
    return content_type


async def _send_with_redirects(client: httpx.AsyncClient, url: httpx.URL) -> httpx.Response:
    current = url
    seen: set[str] = set()
    for _ in range(_MAX_REDIRECTS + 1):
        current = _parse_image_url(str(current))
        current_key = str(current)
        if current_key in seen:
            raise CoverFetchError(502, "Redirect loop")
        seen.add(current_key)
        request = client.build_request("GET", current_key, headers=_UPSTREAM_HEADERS)
        response = await client.send(request, stream=True)
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location", "").strip()
            await response.aclose()
            if not location:
                raise CoverFetchError(502, "Invalid redirect")
            current = current.join(location)
            continue
        return response
    raise CoverFetchError(502, "Too many redirects")


async def _read_bounded(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise CoverFetchError(413, "Image too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_cover_bytes(url: str) -> tuple[bytes, str]:
    """Fetch a whitelisted cover image, returning ``(data, content_type)``.

    Enforces scheme/host whitelist, manual redirect revalidation (max 3 hops),
    ``image/*`` content type, and a 10MB ceiling (rejected before reading the
    body when ``Content-Length`` says so, and during the read otherwise). Raises
    :class:`CoverFetchError` (400/403/413/502/504) on any failure.
    """
    parsed = _parse_image_url(url)
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await _send_with_redirects(client, parsed)
            try:
                if response.status_code < 200 or response.status_code >= 300:
                    raise CoverFetchError(502, "Upstream request failed")
                content_type = _validate_content_headers(response.headers)
                data = await _read_bounded(response)
            finally:
                await response.aclose()
    except httpx.TimeoutException as exc:
        raise CoverFetchError(504, "Upstream request timed out") from exc
    except httpx.HTTPError as exc:
        raise CoverFetchError(502, "Upstream request failed") from exc
    return data, content_type


def save_image_bytes(url: str, data: bytes, content_type: str) -> None:
    """Persist fetched cover bytes to the disk cache (best-effort)."""
    path = image_cache_dir() / f"{image_cache_key(url)}.{image_cache_extension(content_type)}"
    with suppress(OSError):
        path.write_bytes(data)


def is_cover_cached(url: str) -> bool:
    """True if a non-empty cached copy of this cover already exists on disk."""
    for candidate in image_cache_dir().glob(f"{image_cache_key(url)}.*"):
        with suppress(OSError):
            if candidate.stat().st_size > 0:
                return True
    return False


def select_prefetch_targets(urls: Iterable[str], *, max_fetch: int) -> list[str]:
    """Pick which candidate cover URLs are worth prefetching right now.

    Keeps only whitelisted, not-yet-cached URLs (deduped, preserving input order),
    sorts un-refetchable (XHS rotating-token) covers first because they expire while
    re-fetchable ones do not, and caps the result at ``max_fetch``.
    """
    todo: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        if not is_allowed_image_url(url) or is_cover_cached(url):
            continue
        todo.append(url)
    # is_refetchable False (XHS token) sorts before True → fragile covers first.
    todo.sort(key=is_refetchable)
    return todo[:max_fetch]


async def prefetch_cover(url: str) -> bool:
    """Fetch + cache a cover while its CDN token is still fresh (best-effort).

    Returns True only when a new cache entry was written. Never raises — prefetch
    is opportunistic, so any whitelist / network / upstream failure is swallowed.
    """
    if not is_allowed_image_url(url) or is_cover_cached(url):
        return False
    try:
        data, content_type = await fetch_cover_bytes(url)
    except Exception:
        return False
    save_image_bytes(url, data, content_type)
    return True
