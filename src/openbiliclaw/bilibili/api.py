"""Bilibili API Client.

Primary interface for interacting with Bilibili, prioritizing the official
and reverse-engineered API for speed and efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar, cast
from urllib.parse import quote, urlencode, urlparse
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)


def _cookie_value(raw_cookie: str, name: str) -> str:
    """Extract one exact name from a browser-style Cookie request header.

    Chrome may include non-RFC segments that make ``SimpleCookie`` stop parsing
    the remainder of an otherwise valid request header. Ignoring malformed or
    unrelated segments matches browser request semantics without accepting
    partial cookie-name matches.
    """
    for segment in raw_cookie.split(";"):
        key, separator, value = segment.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""


class BilibiliAPIError(RuntimeError):
    """Raised when a Bilibili API request returns an application error."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class BilibiliAuthExpiredError(BilibiliAPIError):
    """Raised when Bilibili reports the current Cookie is logged out."""


class BilibiliFavoriteDuplicateError(BilibiliAPIError):
    """Raised only when the favorite resource-deal endpoint reports a duplicate."""


def _json_object(value: Any) -> dict[str, Any]:
    """Coerce a JSON value into an object for strict typing.

    Returns an empty dict when *value* is ``None`` (common when B站
    returns ``"data": null`` under rate-limiting or for empty ranking
    regions), mirroring :func:`_json_list`'s null-handling.
    """
    if value is None:
        return {}
    return cast("dict[str, Any]", value)


def _json_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of objects for strict typing.

    Returns an empty list when *value* is ``None`` (common when B站
    returns ``"result": null`` under rate-limiting).
    """
    if value is None:
        return []
    return cast("list[dict[str, Any]]", value)


@dataclass
class VideoInfo:
    """Basic video information from Bilibili."""

    bvid: str = ""
    aid: int = 0
    title: str = ""
    description: str = ""
    duration: int = 0  # seconds
    cover_url: str = ""
    up_name: str = ""
    up_mid: int = 0
    view_count: int = 0
    like_count: int = 0
    coin_count: int = 0
    favorite_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    tags: list[str] | None = None
    pub_date: str = ""
    # Part ("P1") id — the key for danmaku / subtitle endpoints. Already
    # present in the /x/web-interface/view payload, so reading it costs
    # nothing extra.
    cid: int = 0


@dataclass
class NavInfo:
    """Basic authenticated user info from the nav endpoint."""

    is_login: bool = False
    uname: str = ""
    mid: int = 0


@dataclass
class FavoriteFolder:
    """Favorite folder metadata."""

    media_id: int
    title: str
    media_count: int = 0


@dataclass
class FavoriteFolderWithItems:
    """Favorite folder plus fetched items."""

    folder: FavoriteFolder
    items: list[dict[str, Any]]
    truncated: bool = False


@dataclass
class FollowingUser:
    """Basic followed user info."""

    mid: int
    uname: str
    sign: str = ""


@dataclass
class CommentInfo:
    """Basic comment info."""

    mid: int
    uname: str
    message: str
    like_count: int = 0


class BilibiliAPIClient:
    """Client for Bilibili's web API.

    This is the primary data access layer (API-first approach).
    For operations not supported by the API, use BilibiliBrowser.
    """

    _BASE_URL = "https://api.bilibili.com"
    _SEARCH_WEB_LOCATION = 1430654
    # A v_voucher exhaustion is usually recoverable WBI-key churn / mild
    # rate limiting, so it gets a short, escalating back-off. A genuine
    # HTTP 412 is an explicit IP-level block and gets the longer hard
    # cooldown instead (see ``_SEARCH_COOLDOWN_412_SECONDS``).
    _SEARCH_COOLDOWN_BASE_SECONDS: ClassVar[float] = 180.0
    _SEARCH_COOLDOWN_412_SECONDS: ClassVar[float] = 600.0
    _SEARCH_COOLDOWN_MAX_SECONDS: ClassVar[float] = 1800.0
    _SEARCH_DOM_FALLBACK_SECONDS: ClassVar[float] = 180.0
    # A single challenged keyword (transient churn) must NOT zero out the
    # whole search round + the explore strategy that shares this cooldown.
    # Only trip the process-wide cooldown after this many *consecutive*
    # keyword-level v_voucher exhaustions; any success resets the streak.
    _SEARCH_VOUCHER_BLOCK_THRESHOLD: ClassVar[int] = 3
    _search_cooldown_until: ClassVar[float] = 0.0
    _search_cooldown_level: ClassVar[int] = 0
    _search_voucher_block_streak: ClassVar[int] = 0
    _search_dom_fallback_until: ClassVar[float] = 0.0
    _WBI_MIXIN_KEY_ENC_TAB = [
        46,
        47,
        18,
        2,
        53,
        8,
        23,
        32,
        15,
        50,
        10,
        31,
        58,
        3,
        45,
        35,
        27,
        43,
        5,
        49,
        33,
        9,
        42,
        19,
        29,
        28,
        14,
        39,
        12,
        38,
        41,
        13,
        37,
        48,
        7,
        16,
        24,
        55,
        40,
        61,
        26,
        17,
        0,
        1,
        60,
        51,
        30,
        4,
        22,
        25,
        54,
        21,
        56,
        59,
        6,
        63,
        57,
        62,
        11,
        36,
        20,
        34,
        44,
        52,
    ]

    _WBI_KEY_TTL: float = 300.0  # Refresh WBI keys every 5 minutes

    def __init__(
        self,
        cookie: str = "",
        *,
        min_request_interval: float = 0.2,
        proxy: str | None = None,
    ) -> None:
        self._cookie = cookie
        self._min_request_interval = min_request_interval
        self._proxy = proxy or None
        self._last_request_at = 0.0
        self._cached_wbi_keys: tuple[str, str] | None = None
        self._wbi_keys_fetched_at: float = 0.0
        self._favorite_folder_locks: dict[str, asyncio.Lock] = {}
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com",
            },
            timeout=30.0,
            # B站 is a CN domain: direct connection always works, while an
            # inherited proxy (httpx trust_env reads env vars AND the OS
            # system proxy — Clash & co.) routes through an exit IP that
            # B站 risk-controls, showing logged-in users as "not logged
            # in". Never inherit; [bilibili].proxy opts back in for
            # networks that genuinely require one.
            trust_env=False,
            proxy=self._proxy,
        )
        if cookie:
            self._client.headers["Cookie"] = cookie

    @property
    def is_authenticated(self) -> bool:
        """Whether we have a valid authentication cookie."""
        return bool(self._cookie)

    async def _respect_rate_limit(self) -> None:
        """Wait to keep a minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_request_interval - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_at = time.monotonic()

    @classmethod
    def search_cooldown_remaining(cls) -> float:
        """Seconds remaining in the process-wide Bilibili search cooldown."""
        return max(0.0, cls._search_cooldown_until - time.monotonic())

    @classmethod
    def search_dom_fallback_remaining(cls) -> float:
        """Seconds remaining while rendered-page search fallback is preferred."""
        return max(0.0, cls._search_dom_fallback_until - time.monotonic())

    @classmethod
    def _activate_search_dom_fallback(cls, *, seconds: float | None = None) -> float:
        """Ask the extension-search producer to try DOM search soon.

        This signal is intentionally weaker than the global cooldown: API
        search may keep probing, but the browser extension can backfill via a
        rendered search page while the API path looks degraded.
        """
        duration = cls._SEARCH_DOM_FALLBACK_SECONDS if seconds is None else seconds
        cls._search_dom_fallback_until = max(
            cls._search_dom_fallback_until,
            time.monotonic() + duration,
        )
        return duration

    @classmethod
    def _activate_search_cooldown(cls, *, base_seconds: float | None = None) -> float:
        """Back off all search clients after repeated v_voucher/412 blocks.

        ``base_seconds`` overrides the per-step base (412 blocks pass the
        longer hard-cooldown base); the escalation multiplier and absolute
        ceiling are shared across both causes.
        """
        cls._search_cooldown_level = min(cls._search_cooldown_level + 1, 3)
        base = cls._SEARCH_COOLDOWN_BASE_SECONDS if base_seconds is None else base_seconds
        duration = min(
            base * cls._search_cooldown_level,
            cls._SEARCH_COOLDOWN_MAX_SECONDS,
        )
        cls._search_cooldown_until = max(
            cls._search_cooldown_until,
            time.monotonic() + duration,
        )
        cls._activate_search_dom_fallback(seconds=duration)
        return duration

    @classmethod
    def _record_voucher_block(cls) -> float:
        """Record one keyword exhausting its v_voucher retries.

        Returns the cooldown duration if this block crossed the
        consecutive-failure threshold (the whole search path now backs
        off), or ``0.0`` if search stays live and only this one keyword is
        dropped — a lone challenged keyword is usually transient WBI churn,
        not an IP-level block, and must not strand the search round +
        explore for the full cooldown.
        """
        cls._search_voucher_block_streak += 1
        if cls._search_voucher_block_streak >= cls._SEARCH_VOUCHER_BLOCK_THRESHOLD:
            return cls._activate_search_cooldown()
        return 0.0

    @classmethod
    def _reset_search_cooldown_backoff(cls) -> None:
        """Reset escalation + the v_voucher streak once search succeeds again."""
        cls._search_cooldown_level = 0
        cls._search_voucher_block_streak = 0

    @staticmethod
    def _sanitized_http_error(
        method: str,
        path: str,
        exc: httpx.HTTPError,
    ) -> BilibiliAPIError:
        """Map transport failures without exposing request or response payloads."""
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            if status_code in {412, 429}:
                code = -status_code
                return BilibiliAPIError(
                    f"Bilibili API {method} rate limited on {path} (code {code})",
                    code=code,
                )
        return BilibiliAPIError(f"Bilibili API {method} failed on {path}")

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request and return the decoded `data` payload."""
        await self._respect_rate_limit()
        try:
            resp = await self._client.get(
                f"{self._BASE_URL}{path}",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._sanitized_http_error("GET", path, exc) from exc

        payload = _json_object(resp.json())
        code = int(payload.get("code", 0))
        if code == -101:
            detail = (
                f"Bilibili session expired on {path} (-101). "
                "Please re-authenticate in the browser or keep the extension "
                "online to sync a fresh Cookie."
            )
            logger.warning("%s", detail)
            raise BilibiliAuthExpiredError(detail, code=code)
        if code != 0:
            message = str(payload.get("message", "Bilibili API request failed"))
            raise BilibiliAPIError(message, code=code)
        return _json_object(payload.get("data", {}))

    def _csrf_token(self) -> str:
        """Return the CSRF token after validating the authenticated Cookie."""
        session = _cookie_value(self._cookie, "SESSDATA")
        csrf = _cookie_value(self._cookie, "bili_jct")
        if not session or not csrf:
            raise BilibiliAuthExpiredError(
                "Bilibili login required for native save",
                code=-101,
            )
        return csrf

    async def _post_json(self, path: str, *, data: dict[str, Any]) -> dict[str, Any]:
        """Perform an authenticated form POST and return its decoded data object."""
        self._csrf_token()
        await self._respect_rate_limit()
        try:
            resp = await self._client.post(f"{self._BASE_URL}{path}", data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._sanitized_http_error("POST", path, exc) from exc

        try:
            payload = resp.json()
        except ValueError as exc:
            raise BilibiliAPIError(f"Bilibili API returned invalid JSON on {path}") from exc
        if not isinstance(payload, dict):
            raise BilibiliAPIError(f"Bilibili API returned invalid data on {path}")

        raw_code = payload.get("code", 0)
        try:
            code = int(raw_code)
        except (TypeError, ValueError) as exc:
            raise BilibiliAPIError(f"Bilibili API returned invalid code on {path}") from exc
        if code == -101:
            raise BilibiliAuthExpiredError(
                f"Bilibili login required for native save (code {code})",
                code=code,
            )
        if code != 0:
            raise BilibiliAPIError(
                f"Bilibili API request failed on {path} (code {code})",
                code=code,
            )

        result = payload.get("data", {})
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise BilibiliAPIError(f"Bilibili API returned invalid data on {path}")
        return cast("dict[str, Any]", result)

    async def _get_wbi_keys(self) -> tuple[str, str]:
        """Fetch and cache the WBI image/sub keys used for signed search requests.

        Keys are refreshed after :attr:`_WBI_KEY_TTL` seconds because B站
        rotates them periodically — stale keys cause search to return an
        empty ``v_voucher`` response instead of actual results.
        """
        if (
            self._cached_wbi_keys is not None
            and (time.monotonic() - self._wbi_keys_fetched_at) < self._WBI_KEY_TTL
        ):
            return self._cached_wbi_keys

        await self._respect_rate_limit()
        try:
            resp = await self._client.get(f"{self._BASE_URL}/x/web-interface/nav")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(str(exc)) from exc

        payload = _json_object(resp.json())
        data = _json_object(payload.get("data", {}))
        wbi_img = _json_object(data.get("wbi_img", {}))
        img_key = self._extract_wbi_key_component(str(wbi_img.get("img_url", "")))
        sub_key = self._extract_wbi_key_component(str(wbi_img.get("sub_url", "")))
        if not img_key or not sub_key:
            raise BilibiliAPIError("Missing wbi keys in nav response")
        self._cached_wbi_keys = (img_key, sub_key)
        self._wbi_keys_fetched_at = time.monotonic()
        return self._cached_wbi_keys

    @staticmethod
    def _extract_wbi_key_component(url: str) -> str:
        """Return the key segment from a WBI image URL."""
        path = urlparse(url).path
        filename = path.rsplit("/", 1)[-1]
        return filename.rsplit(".", 1)[0]

    @classmethod
    def _build_wbi_mixin_key(cls, img_key: str, sub_key: str) -> str:
        """Build the mixed key used by Bilibili WBI request signing."""
        merged = img_key + sub_key
        return "".join(merged[index] for index in cls._WBI_MIXIN_KEY_ENC_TAB)[:32]

    @classmethod
    def _sign_wbi_params(
        cls,
        params: dict[str, object],
        *,
        img_key: str,
        sub_key: str,
    ) -> dict[str, str]:
        """Sign search params using Bilibili's WBI algorithm."""
        mixin_key = cls._build_wbi_mixin_key(img_key, sub_key)
        signed_params = {**params, "wts": int(time.time())}
        ordered_items = sorted(signed_params.items())
        sanitized = {key: re.sub(r"[!'()*]", "", str(value)) for key, value in ordered_items}
        query = urlencode(sanitized)
        sanitized["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return sanitized

    async def get_nav_info(self) -> NavInfo:
        """Get the current login state from Bilibili nav API."""
        data = await self._get_json("/x/web-interface/nav")
        return NavInfo(
            is_login=bool(data.get("isLogin", False)),
            uname=str(data.get("uname", "")),
            mid=int(data.get("mid", 0)),
        )

    async def get_video_info(self, bvid: str) -> VideoInfo:
        """Get video information by BV ID.

        Args:
            bvid: Bilibili video BV ID.

        Returns:
            VideoInfo dataclass.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/view",
            params={"bvid": bvid},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        data = _json_object(payload.get("data"))
        stat = _json_object(data.get("stat", {}))
        owner = _json_object(data.get("owner", {}))

        return VideoInfo(
            bvid=data.get("bvid", bvid),
            aid=data.get("aid", 0),
            title=data.get("title", ""),
            description=data.get("desc", ""),
            duration=data.get("duration", 0),
            cover_url=data.get("pic", ""),
            up_name=owner.get("name", ""),
            up_mid=owner.get("mid", 0),
            view_count=stat.get("view", 0),
            like_count=stat.get("like", 0),
            coin_count=stat.get("coin", 0),
            favorite_count=stat.get("favorite", 0),
            share_count=stat.get("share", 0),
            danmaku_count=stat.get("danmaku", 0),
            pub_date=data.get("pubdate", ""),
            cid=int(data.get("cid", 0) or 0),
        )

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, Any]]:
        """Search for videos by keyword.

        Args:
            keyword: Search query.
            page: Page number.
            page_size: Results per page.

        Returns:
            List of search result dicts.
        """
        cooldown_remaining = self.search_cooldown_remaining()
        if cooldown_remaining > 0:
            logger.info(
                "Bilibili search cooldown active (%.0fs left) — skipping query=%r",
                cooldown_remaining,
                keyword,
            )
            return []

        # v0.3.55+: 3 attempts with exponential backoff (was 2 with 1.5s
        # linear). Production logs (2026-05-05) showed 141 v_voucher
        # challenges in 43 minutes; with only 1 retry, ~9 full search
        # rounds returned 0 results because keywords got challenged twice
        # and we gave up. The new schedule (1.5s / 5s / 15s = ~21s total
        # per keyword) lets the WBI key churn settle without immediately
        # surrendering. Steady-state cost is zero — retries don't fire
        # when keys are healthy.
        #
        # Fast-fail once a storm is suspected: the first keyword to fail in
        # a fresh round gets the full retry budget so transient churn can
        # settle, but once one keyword has already fully exhausted
        # (streak>0) we drop to a single quick probe — confirming a real
        # storm in a few fast attempts instead of hammering B站 with doomed
        # ~21s retry chains per keyword (which would only deepen the block).
        max_attempts = 1 if type(self)._search_voucher_block_streak > 0 else 3
        backoff_schedule = (1.5, 5.0, 15.0)
        for attempt in range(max_attempts):
            try:
                img_key, sub_key = await self._get_wbi_keys()
                data = await self._get_json(
                    "/x/web-interface/wbi/search/type",
                    params=self._sign_wbi_params(
                        {
                            "keyword": keyword,
                            "search_type": "video",
                            "page": page,
                            "page_size": page_size,
                            "order": order,
                            "web_location": self._SEARCH_WEB_LOCATION,
                        },
                        img_key=img_key,
                        sub_key=sub_key,
                    ),
                    headers={
                        "Referer": (
                            f"https://search.bilibili.com/all?keyword={quote(keyword, safe='')}"
                        ),
                        "Origin": "https://search.bilibili.com",
                    },
                )
            except BilibiliAPIError as exc:
                cause = exc.__cause__
                if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 412:
                    # 412 is an explicit IP-level block — back off hard and
                    # immediately (no streak threshold), with the longer base.
                    duration = self._activate_search_cooldown(
                        base_seconds=self._SEARCH_COOLDOWN_412_SECONDS
                    )
                    logger.warning(
                        "Bilibili search blocked with 412 for query=%r — "
                        "cooling down search for %.0fs",
                        keyword,
                        duration,
                    )
                    return []
                self._activate_search_dom_fallback()
                raise

            # Detect v_voucher-only response (stale WBI keys or rate limit)
            if "v_voucher" in data and data.get("result") is None:
                if attempt < max_attempts - 1:
                    delay = backoff_schedule[attempt]
                    logger.info(
                        "Search v_voucher challenge (attempt %d/%d) for query=%r — "
                        "refreshing WBI keys, retry in %.1fs",
                        attempt + 1,
                        max_attempts,
                        keyword,
                        delay,
                    )
                    self._cached_wbi_keys = None
                    await asyncio.sleep(delay)
                    continue
                # Final attempt also got v_voucher. Record the block; only
                # trip the shared cooldown once consecutive keyword failures
                # cross the threshold — a lone challenged keyword just gets
                # dropped so the rest of the round (and explore) stays live.
                self._activate_search_dom_fallback()
                duration = self._record_voucher_block()
                if duration > 0:
                    logger.warning(
                        "Search v_voucher storm confirmed (%d consecutive blocked "
                        "queries, latest=%r) — cooling down search for %.0fs "
                        "(likely WBI storm or IP rate limit)",
                        type(self)._search_voucher_block_streak,
                        keyword,
                        duration,
                    )
                else:
                    logger.info(
                        "Search v_voucher challenge persisted for query=%r "
                        "(streak %d/%d) — dropping this keyword; search stays live",
                        keyword,
                        type(self)._search_voucher_block_streak,
                        self._SEARCH_VOUCHER_BLOCK_THRESHOLD,
                    )
                return []

            results = _json_list(data.get("result", []))
            self._reset_search_cooldown_backoff()
            if not results:
                logger.debug("Search returned empty result for query=%r", keyword)
            return results
        return []

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        """Get the authenticated user's watch history.

        Requires valid authentication cookie.

        Args:
            max_items: Maximum number of history items to fetch. 0 means fetch all.

        Returns:
            List of history item dicts.
        """
        if not self.is_authenticated:
            logger.warning("Cannot fetch history without authentication.")
            return []

        items: list[dict[str, Any]] = []
        cursor_params: dict[str, Any] = {"type": "archive"}
        while max_items == 0 or len(items) < max_items:
            data = await self._get_json(
                "/x/web-interface/history/cursor",
                params=cursor_params,
            )
            batch = _json_list(data.get("list", []))
            if not batch:
                break
            items.extend(batch)
            cursor = _json_object(data.get("cursor", {}))
            next_max = cursor.get("max")
            next_view_at = cursor.get("view_at")
            if not next_max or not next_view_at:
                break
            cursor_params = {
                "type": "archive",
                "max": next_max,
                "view_at": next_view_at,
            }
        return items if max_items == 0 else items[:max_items]

    async def get_favorites(
        self,
        media_id: int,
        *,
        max_items: int = 20,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Get content from a favorites folder.

        Args:
            media_id: Favorites folder media ID.
            max_items: Maximum number of favorite items to fetch.
            page_size: Page size for the Bilibili resource list endpoint.

        Returns:
            List of favorite item dicts.
        """
        item_limit = max(0, int(max_items))
        if item_limit <= 0:
            return []

        effective_page_size = max(1, min(int(page_size), 20))
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < item_limit:
            data = await self._get_json(
                "/x/v3/fav/resource/list",
                params={"media_id": media_id, "pn": page, "ps": effective_page_size},
            )
            batch = _json_list(data.get("medias", []))
            if not batch:
                break
            items.extend(batch)
            has_more = data.get("has_more")
            if has_more is not None:
                if not bool(has_more):
                    break
            elif len(batch) < effective_page_size:
                break
            page += 1
        return items[:item_limit]

    async def get_favorite_folders(self) -> list[FavoriteFolder]:
        """Get the authenticated user's favorite folder metadata."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/v3/fav/folder/created/list-all",
            params={"up_mid": nav.mid},
        )
        folders = _json_list(data.get("list", []))
        return [
            FavoriteFolder(
                media_id=int(folder.get("id", 0)),
                title=str(folder.get("title", "")),
                media_count=int(folder.get("media_count", 0)),
            )
            for folder in folders
        ]

    async def ensure_favorite_folder(self, title: str) -> FavoriteFolder:
        """Return an exact-title favorite folder, creating it when absent."""
        csrf = self._csrf_token()
        lock = self._favorite_folder_locks.setdefault(title, asyncio.Lock())
        async with lock:
            for folder in await self.get_favorite_folders():
                if folder.title == title:
                    if folder.media_id <= 0:
                        raise BilibiliAPIError("Bilibili returned an invalid favorite folder id")
                    return folder

            data = await self._post_json(
                "/x/v3/fav/folder/add",
                data={
                    "title": title,
                    "intro": "",
                    "privacy": 0,
                    "csrf": csrf,
                },
            )
            raw_media_id = data.get("id")
            try:
                if isinstance(raw_media_id, bool) or not isinstance(raw_media_id, (int, str)):
                    raise ValueError
                media_id = int(raw_media_id)
            except (TypeError, ValueError) as exc:
                raise BilibiliAPIError("Bilibili returned an invalid favorite folder id") from exc
            if media_id <= 0:
                raise BilibiliAPIError("Bilibili returned an invalid favorite folder id")
            return FavoriteFolder(media_id=media_id, title=title)

    async def add_video_to_favorite(self, bvid: str, media_id: int) -> None:
        """Add a Bilibili video to an existing favorite folder."""
        csrf = self._csrf_token()
        aid = await self._resolve_aid(bvid)
        try:
            await self._post_json(
                "/x/v3/fav/resource/deal",
                data={
                    "rid": aid,
                    "type": 2,
                    "add_media_ids": str(media_id),
                    "del_media_ids": "",
                    "csrf": csrf,
                },
            )
        except BilibiliAPIError as exc:
            if exc.code == 11201:
                raise BilibiliFavoriteDuplicateError(
                    "Bilibili favorite already contains this video (code 11201)",
                    code=11201,
                ) from exc
            raise

    async def add_video_to_watch_later(self, bvid: str) -> None:
        """Add a Bilibili video to the authenticated account's watch-later list."""
        csrf = self._csrf_token()
        aid = await self._resolve_aid(bvid)
        await self._post_json(
            "/x/v2/history/toview/add",
            data={"aid": aid, "csrf": csrf},
        )

    async def _resolve_aid(self, bvid: str) -> int:
        """Resolve a BV ID through the application-code-aware view endpoint."""
        data = await self._get_json("/x/web-interface/view", params={"bvid": bvid})
        aid = data.get("aid")
        if isinstance(aid, bool) or not isinstance(aid, int) or aid <= 0:
            raise BilibiliAPIError("Bilibili returned an invalid video aid")
        return aid

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[FavoriteFolderWithItems]:
        """Get favorite folders and fetch each folder's items within budget."""
        folders = await self.get_favorite_folders()
        folder_limit = max(0, int(max_items_per_folder))
        folder_count = max(0, int(max_folders))
        if folder_count <= 0 or folder_limit <= 0:
            return []

        remaining_total: int | None
        if max_total_items is None:
            remaining_total = None
        else:
            remaining_total = max(0, int(max_total_items))
            if remaining_total <= 0:
                return []

        aggregated: list[FavoriteFolderWithItems] = []
        for folder in folders[:folder_count]:
            if remaining_total is not None and remaining_total <= 0:
                break
            current_limit = folder_limit
            if remaining_total is not None:
                current_limit = min(current_limit, remaining_total)
            limited_items = await self.get_favorites(folder.media_id, max_items=current_limit)
            aggregated.append(
                FavoriteFolderWithItems(
                    folder=folder,
                    items=limited_items,
                    truncated=folder.media_count > len(limited_items),
                )
            )
            if remaining_total is not None:
                remaining_total -= len(limited_items)
        return aggregated

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        """Get the authenticated user's following list."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/relation/followings",
            params={"vmid": nav.mid, "pn": page, "ps": page_size},
        )
        users = _json_list(data.get("list", []))
        return [
            FollowingUser(
                mid=int(user.get("mid", 0)),
                uname=str(user.get("uname", "")),
                sign=str(user.get("sign", "")),
            )
            for user in users
        ]

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        """Get related/recommended videos for a given video.

        Args:
            bvid: Source video BV ID.

        Returns:
            List of related video dicts.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/archive/related",
            params={"bvid": bvid},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        return _json_list(payload.get("data", []))

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        """Get ranking/trending videos.

        Args:
            rid: Region ID (0 for all).

        Returns:
            List of ranking item dicts.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/ranking/v2",
            params={"rid": rid, "type": "all"},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        data = _json_object(payload.get("data", {}))
        return _json_list(data.get("list", []))

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        """Get the top comments for a video."""
        video = await self.get_video_info(bvid)
        data = await self._get_json(
            "/x/v2/reply/main",
            params={"oid": video.aid, "type": 1, "mode": 3, "ps": limit},
        )
        replies = _json_list(data.get("replies", []))
        comments = [
            CommentInfo(
                mid=int(reply.get("mid", 0)),
                uname=str(_json_object(reply.get("member", {})).get("uname", "")),
                message=str(_json_object(reply.get("content", {})).get("message", "")),
                like_count=int(reply.get("like", 0)),
            )
            for reply in replies
        ]
        return comments[:limit]

    async def get_danmaku_texts(self, cid: int, *, limit: int = 3000) -> list[str]:
        """Fetch raw danmaku strings for one video part.

        Uses the plain XML endpoint (``comment.bilibili.com/{cid}.xml``), which
        needs no credentials and no WBI signing — unlike the protobuf segment
        API. Goes through this client so it inherits the shared rate limit and
        the ``trust_env=False`` CN-direct policy (pitfall rule 1).

        Returns ``[]`` on any failure: danmaku are an optional enrichment
        signal and must never break the caller.
        """
        part_id = int(cid or 0)
        if part_id <= 0:
            return []

        await self._respect_rate_limit()
        try:
            response = await self._client.get(
                f"https://comment.bilibili.com/{part_id}.xml"
            )
            if response.status_code >= 400:
                logger.debug("danmaku HTTP %s for cid=%s", response.status_code, part_id)
                return []
            # httpx transparently inflates the deflate-encoded body.
            root = ElementTree.fromstring(response.text)
        except (httpx.HTTPError, ElementTree.ParseError, ValueError):
            logger.debug("danmaku fetch/parse failed for cid=%s", part_id, exc_info=True)
            return []

        texts: list[str] = []
        for node in root.iter("d"):
            text = (node.text or "").strip()
            if text:
                texts.append(text)
            if len(texts) >= max(1, int(limit)):
                break
        return texts

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
