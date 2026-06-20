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

import httpx

logger = logging.getLogger(__name__)


class BilibiliAPIError(RuntimeError):
    """Raised when a Bilibili API request returns an application error."""


class BilibiliAuthExpiredError(BilibiliAPIError):
    """Raised when Bilibili reports the current Cookie is logged out."""


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
    # A single challenged keyword (transient churn) must NOT zero out the
    # whole search round + the explore strategy that shares this cooldown.
    # Only trip the process-wide cooldown after this many *consecutive*
    # keyword-level v_voucher exhaustions; any success resets the streak.
    _SEARCH_VOUCHER_BLOCK_THRESHOLD: ClassVar[int] = 3
    _search_cooldown_until: ClassVar[float] = 0.0
    _search_cooldown_level: ClassVar[int] = 0
    _search_voucher_block_streak: ClassVar[int] = 0
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

    def __init__(self, cookie: str = "", *, min_request_interval: float = 0.2) -> None:
        self._cookie = cookie
        self._min_request_interval = min_request_interval
        self._last_request_at = 0.0
        self._cached_wbi_keys: tuple[str, str] | None = None
        self._wbi_keys_fetched_at: float = 0.0
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
            raise BilibiliAPIError(str(exc)) from exc

        payload = _json_object(resp.json())
        code = int(payload.get("code", 0))
        if code != 0:
            message = str(payload.get("message", "Bilibili API request failed"))
            if path == "/x/web-interface/nav" and code == -101:
                detail = (
                    f"Bilibili session expired on {path} (-101): {message}. "
                    "Please re-authenticate in the browser or keep the extension "
                    "online to sync a fresh Cookie."
                )
                logger.warning("%s", detail)
                raise BilibiliAuthExpiredError(detail)
            raise BilibiliAPIError(message)
        return _json_object(payload.get("data", {}))

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
            max_items: Maximum number of history items to fetch.
                0 means fetch all available history.

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
        if max_items > 0:
            return items[:max_items]
        return items

    async def get_favorites(self, media_id: int, *, max_items: int = 0) -> list[dict[str, Any]]:
        """Get content from a favorites folder with pagination support.

        Args:
            media_id: Favorites folder media ID.
            max_items: Maximum items to fetch (0 = fetch all).

        Returns:
            List of favorite item dicts.
        """
        items: list[dict[str, Any]] = []
        page = 1
        page_size = 20
        max_pages = 100
        consecutive_empty_pages = 0
        total_count = 0
        exit_reason = "unknown"

        while page <= max_pages:
            try:
                img_key, sub_key = await self._get_wbi_keys()
                data = await self._get_json(
                    "/x/v3/fav/resource/list",
                    params=self._sign_wbi_params(
                        {"media_id": media_id, "pn": page, "ps": page_size},
                        img_key=img_key,
                        sub_key=sub_key,
                    ),
                    headers={
                        "Referer": "https://space.bilibili.com/favlist",
                        "Origin": "https://space.bilibili.com",
                    },
                )
            except BilibiliAPIError as exc:
                cause = exc.__cause__
                if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 412:
                    logger.warning(
                        "收藏夹 %d 第 %d 页请求被拒绝 (412) — "
                        "刷新 WBI keys 并重试",
                        media_id, page,
                    )
                    self._cached_wbi_keys = None
                    continue
                logger.warning("收藏夹 %d 第 %d 页请求失败: %s", media_id, page, exc)
                if page == 1:
                    exit_reason = f"first_page_error_{type(exc).__name__}"
                    break
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 3:
                    exit_reason = f"consecutive_errors_{consecutive_empty_pages}"
                    break
                page += 1
                continue

            batch = _json_list(data.get("medias", []))
            info = _json_object(data.get("info", {}))
            total_count = info.get("media_count", 0)

            logger.debug(
                "收藏夹 %d 第 %d 页: 返回 %d 条 (累计 %d/%d)",
                media_id, page, len(batch), len(items), total_count,
            )

            if not batch:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 3:
                    exit_reason = f"consecutive_empty_{consecutive_empty_pages}_page_{page}"
                    logger.info(
                        "收藏夹 %d 分页结束 @ 第 %d 页: 连续 %d 次空响应 (累计 %d/%d) → 原因: %s",
                        media_id, page, consecutive_empty_pages, len(items), total_count, exit_reason,
                    )
                    break
                logger.debug("收藏夹 %d 第 %d 页为空, 继续尝试下一页... (%d/3)", media_id, page, consecutive_empty_pages)
                page += 1
                continue

            consecutive_empty_pages = 0
            items.extend(batch)

            if max_items > 0 and len(items) >= max_items:
                exit_reason = f"max_items_reached_{len(items)}"
                logger.info(
                    "收藏夹 %d 分页结束 @ 第 %d 页: 达到上限 %d (累计 %d/%d) → 原因: %s",
                    media_id, page, max_items, len(items), total_count, exit_reason,
                )
                break

            if len(items) >= total_count and total_count > 0:
                exit_reason = f"total_count_reached_{len(items)}"
                logger.info(
                    "收藏夹 %d 分页结束 @ 第 %d 页: 已拉取全部声明数量 (累计 %d/%d) → 原因: %s",
                    media_id, page, len(items), total_count, exit_reason,
                )
                break

            page += 1

        if page > max_pages:
            exit_reason = f"max_pages_limit_{max_pages}"
            logger.warning(
                "⚠️ 收藏夹 %d 达到最大页数限制 %d (累计 %d/%d) → 原因: %s",
                media_id, max_pages, len(items), total_count, exit_reason,
            )

        result = items[:max_items] if max_items > 0 else items
        if total_count > 0 and len(result) < total_count:
            logger.warning(
                "⚠️ 收藏夹 %d 数据不完整: 声明 %d 条, 实际获取 %d 条 (%.1f%%). 退出原因: %s. 可能是 B站 API 限制或网络问题.",
                media_id, total_count, len(result), (len(result) / total_count * 100), exit_reason,
            )
        return result

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

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[FavoriteFolderWithItems]:
        """Get favorite folders and fetch each folder's items within budget."""
        folders = await self.get_favorite_folders()
        logger.info("发现 %d 个收藏夹元数据", len(folders))
        aggregated: list[FavoriteFolderWithItems] = []
        total_items = 0
        for idx, folder in enumerate(folders[:max_folders], 1):
            try:
                items = await self.get_favorites(folder.media_id)
                limited_items = items[:max_items_per_folder]
                is_truncated = len(items) > len(limited_items) or folder.media_count > len(limited_items)
                total_items += len(limited_items)
                logger.info(
                    "收藏夹 [%d/%d] '%s': 声明 %d 条 → 实际拉取 %d 条 → 截断后 %d 条%s",
                    idx,
                    min(len(folders), max_folders),
                    folder.title,
                    folder.media_count,
                    len(items),
                    len(limited_items),
                    " (⚠️ 已截断)" if is_truncated else "",
                )
                aggregated.append(
                    FavoriteFolderWithItems(
                        folder=folder,
                        items=limited_items,
                        truncated=is_truncated,
                    )
                )
            except Exception as exc:
                logger.warning("收藏夹 [%d] '%s' 拉取失败: %s", idx, folder.title, exc, exc_info=True)
        logger.info("收藏夹遍历完成: 共处理 %d 个收藏夹, 累计 %d 条内容", len(aggregated), total_items)
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

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()