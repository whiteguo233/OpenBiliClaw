"""TikTok web API backend with a guest identity (signed requests).

This module is the second TikTok backend next to the yt-dlp one in
``sources/tiktok.py``. It speaks to TikTok's own web API endpoints directly:

  - ``/api/recommend/item_list/``  — anonymous For-You feed; also mints the
    guest identity's real ``msToken`` via ``set-cookie`` (bootstrap path)
  - ``/api/user/detail/``          — handle → ``secUid`` + profile
  - ``/api/post/item_list/``       — creator uploads by ``secUid``
  - ``/api/challenge/detail/``     — hashtag name → challenge id
  - ``/api/challenge/item_list/``  — hashtag videos by challenge id
  - ``/api/search/item/full/``     — keyword search (gated for guests; reserved)

Every request is signed with the vendored pure-Python signer
(:mod:`openbiliclaw.sources.tiktok_sign`, X-Dynosaur / X-Gnarly) and sent
through ``curl_cffi`` with Chrome TLS impersonation, because TikTok's edge
rejects plain-OpenSSL clients. ``msToken`` is either the real token TikTok
minted for this session or empty — it is never fabricated (a forged token
gets every response gated: 0-byte body + ``tt_orcas_res: 1``).

Real-network verification (2026-10-03, datacenter proxy exit, guest
identity, no login cookie): feed / user detail / creator posts / hashtag
detail + listing all returned real items; keyword search returned the
gated empty body and therefore requires a login cookie.

Availability contract used by the router: every listing method returns
``None`` when the *backend* is unavailable (transport exhausted or the
request stayed gated after one identity re-bootstrap) and ``[]`` when the
request succeeded but simply had no items. Only ``None`` triggers the
yt-dlp fallback in ``auto`` mode.

Compliance notes: this backend reads public metadata only, with a guest
identity TikTok itself issues; it sends no fabricated tokens, downloads no
media, and is throttled by the producer budgets like every other source.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from urllib.parse import urlencode

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.published_time import normalize_published_time
from openbiliclaw.sources import tiktok_sign
from openbiliclaw.sources.tiktok import (
    TiktokClient,
    normalize_tiktok_handle,
    normalize_tiktok_tag,
)

logger = logging.getLogger(__name__)

_TIKTOK_BASE_URL = "https://www.tiktok.com"

# A real desktop Chrome UA. ``browser_version`` in the base params and the
# user_agent passed to the signer must equal the UA actually sent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Header TikTok sets on gated responses: HTTP 200 with a 0-byte body and
# ``tt_orcas_res: 1``. This is an identity/parameter verdict, not a transport
# failure — retrying the same request unchanged is pointless.
_GATED_HEADER = "tt_orcas_res"
_GATED_VALUE = "1"

# TikTok's edge resets TLS connections from datacenter IPs at a high rate
# (measured ~5/6 during the 2026-10-03 spike), so every request retries
# transport errors a few times before giving up.
_DEFAULT_MAX_ATTEMPTS = 5
_REQUEST_TIMEOUT_SECONDS = 30.0

# TikTok's web API reports ``video.duration`` in milliseconds. A genuine
# TikTok cannot exceed 60 minutes, so anything above two hours must be ms —
# the same heuristic ``normalize_tiktok_video`` applies to yt-dlp entries.
_DURATION_MILLISECONDS_THRESHOLD_SECONDS = 7200

# Base parameter set proven against the live API in the 2026-10-03 spike.
# ``device_id`` is mandatory: requests without it get the gated empty body.
# region / priority_region / tz_name should match the proxy egress; the
# defaults match the verified Tokyo exit.
_BASE_PARAMS: dict[str, str] = {
    "aid": "1988",
    "app_language": "en",
    "app_name": "tiktok_web",
    "browser_language": "en-US",
    "browser_name": "Mozilla",
    "browser_online": "true",
    "browser_platform": "MacIntel",
    "channel": "tiktok_web",
    "cookie_enabled": "true",
    "device_platform": "web_pc",
    "focus_state": "true",
    "history_len": "4",
    "is_fullscreen": "false",
    "is_page_visible": "true",
    "language": "en",
    "os": "mac",
    "priority_region": "JP",
    "referer": "",
    "region": "JP",
    "root_referer": "https://www.tiktok.com/",
    "screen_height": "1080",
    "screen_width": "1920",
    "tz_name": "Asia/Tokyo",
    "webcast_language": "en",
}

_SEARCH_WEB_SEARCH_CODE = (
    '{"tiktok":{"client_params_x":{"search_engine":'
    '{"ies_mt_user_live_video_card_use_params":1,'
    '"mt_user_general_video_card_use_params":1}},"search_server":{}}}'
)


def _new_device_id() -> str:
    """A 19-digit decimal device id (leading digit non-zero), per identity."""
    return str(random.randint(1, 9)) + "".join(str(random.randint(0, 9)) for _ in range(18))


@dataclass(frozen=True)
class TiktokWebResponse:
    """Minimal response shape the client layer consumes (test-friendly)."""

    status_code: int
    body: bytes
    headers: dict[str, str]

    @property
    def gated(self) -> bool:
        """True for TikTok's gate verdict: empty body + ``tt_orcas_res: 1``."""
        return not self.body and self.headers.get(_GATED_HEADER, "").strip() == _GATED_VALUE


class TiktokWebTransportError(RuntimeError):
    """Raised when every transport attempt for one request failed."""


# ---------------------------------------------------------------------------
# Guest identity
# ---------------------------------------------------------------------------


@dataclass
class TiktokWebIdentity:
    """Guest identity lifecycle: session, cookies, msToken bootstrap.

    Bootstrap sends one anonymous ``/api/recommend/item_list/`` request;
    TikTok's response ``set-cookie`` mints the real 128-character ``msToken``
    the signer needs downstream. An optional login cookie (from
    ``resolve_tiktok_cookie``) is loaded into the same jar and unlocks
    surfaces the guest identity cannot reach (keyword search today).
    """

    user_agent: str = DEFAULT_USER_AGENT
    # Proxy policy mirrors the yt-dlp backend: ``None`` = inherit environment
    # (system mode), ``""`` = force direct, a URL = pin that proxy.
    proxy: str | None = None
    login_cookie: str = ""
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    request_timeout: float = _REQUEST_TIMEOUT_SECONDS
    device_id: str = field(default_factory=_new_device_id)
    # Geo parameters should match the proxy egress; a mismatch is a prime
    # suspect when TikTok gates every response (empty body + orcas).
    region: str = "JP"
    tz_name: str = "Asia/Tokyo"
    # Test seam: builds the curl_cffi session. Production default None.
    session_factory: Any = None
    _session: Any = field(default=None, init=False, repr=False)
    _bootstrapped: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Fail fast at construction so the runtime can fall back to the
        # yt-dlp backend instead of discovering the missing dependency on
        # the first request.
        try:
            import curl_cffi  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "TikTok web backend requires curl-cffi (pip install curl-cffi)"
            ) from exc

    def _create_session(self) -> Any:
        if self.session_factory is not None:
            return self.session_factory()
        from curl_cffi import requests as crequests

        kwargs: dict[str, Any] = {}
        if self.proxy is not None:
            kwargs["proxy"] = self.proxy
        session = crequests.Session(**kwargs)
        session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Referer": "https://www.tiktok.com/",
                "Origin": "https://www.tiktok.com",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        return session

    def _ensure_session(self) -> Any:
        if self._session is None:
            self._session = self._create_session()
            self._load_login_cookie(self._session)
        return self._session

    def _load_login_cookie(self, session: Any) -> None:
        cookie = self.login_cookie.strip()
        if not cookie:
            return
        for pair in cookie.split(";"):
            name, separator, value = pair.partition("=")
            if separator and name.strip():
                session.cookies.set(name.strip(), value.strip(), domain=".tiktok.com")
        logger.info("tiktok web identity: login cookie loaded (search unlocked)")

    def ms_token(self) -> str:
        """The session's real ``msToken`` (never fabricated), or ``""``."""
        if self._session is None:
            return ""
        try:
            return str(self._session.cookies.get("msToken") or "")
        except Exception:  # pragma: no cover - defensive
            return ""

    def base_params(self, from_page: str) -> dict[str, str]:
        """The proven base parameter set for one endpoint family."""
        return {
            **_BASE_PARAMS,
            "browser_version": self.user_agent,
            "device_id": self.device_id,
            "from_page": from_page,
            "priority_region": self.region,
            "region": self.region,
            "tz_name": self.tz_name,
        }

    def _get_with_retries(self, path: str, url: str) -> TiktokWebResponse:
        """GET *url* with transport retries (TLS resets are expected).

        A gated response is returned as-is for the caller to interpret (it is
        not a transport problem and must not be retried unchanged).
        """
        session = self._ensure_session()
        attempts = max(1, int(self.max_attempts))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = session.get(url, impersonate="chrome", timeout=self.request_timeout)
                return TiktokWebResponse(
                    status_code=int(response.status_code),
                    body=bytes(response.content),
                    headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                )
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "tiktok web %s transport attempt %d/%d failed: %s",
                    path,
                    attempt + 1,
                    attempts,
                    exc,
                )
        raise TiktokWebTransportError(
            f"tiktok web {path}: all {attempts} transport attempts failed"
        ) from last_error

    def signed_get(self, path: str, params: dict[str, str]) -> TiktokWebResponse:
        """One signed GET (X-Dynosaur / msToken / X-Bogus / X-Gnarly)."""
        # The session must exist before signing: ``ms_token()`` reads the jar.
        self._ensure_session()
        query, _parameters = tiktok_sign.sign(
            list(params.items()), self.user_agent, ms_token=self.ms_token()
        )
        return self._get_with_retries(path, f"{_TIKTOK_BASE_URL}{path}?{query}")

    def unsigned_get(self, path: str, params: dict[str, str]) -> TiktokWebResponse:
        """One plain GET — for endpoints TikTok serves without the webmssdk
        signature (the passport session heartbeat the login probe uses)."""
        query = urlencode(params)
        return self._get_with_retries(path, f"{_TIKTOK_BASE_URL}{path}?{query}")

    def ensure_bootstrapped(self) -> bool:
        """Mint the guest identity once (one anonymous feed request)."""
        if self._bootstrapped:
            return True
        try:
            response = self.signed_get(
                "/api/recommend/item_list/",
                {**self.base_params("fyp"), "count": "10", "itemType": "0"},
            )
        except Exception as exc:
            logger.warning("tiktok web bootstrap transport failed: %s", exc)
            return False
        if not response.body:
            logger.warning(
                "tiktok web bootstrap refused: empty body (orcas=%s)",
                response.headers.get(_GATED_HEADER, "-"),
            )
            return False
        self._bootstrapped = True
        logger.info(
            "tiktok web identity bootstrapped (msToken %s)",
            "minted" if self.ms_token() else "absent",
        )
        return True

    def invalidate(self) -> None:
        """Force a re-bootstrap on the next request (gated response seen)."""
        self._bootstrapped = False


# ---------------------------------------------------------------------------
# Login probe (optional cookie)
# ---------------------------------------------------------------------------

# TikTok's own session heartbeat: unsigned, cookie-only, and the lightest
# endpoint that answers "is this session alive" (DTK uses it the same way).
LOGIN_PROBE_PATH = "/passport/token/beat/web/"
_LOGIN_PROBE_PARAMS = {"aid": "1459", "device_platform": "web", "scene": "active"}


@dataclass(frozen=True)
class TiktokAuthStatus:
    """Structured TikTok auth status (mirrors ``DouyinAuthStatus``)."""

    has_cookie: bool
    authenticated: bool
    network_error: bool = False
    message: str = ""


def _probe_tiktok_sync(
    cookie: str,
    *,
    proxy: str | None = None,
    session_factory: Any = None,
) -> TiktokAuthStatus:
    """Blocking probe body; runs in the executor from ``probe_tiktok_login``.

    Verdict mapping (the probe transport seam is faked in tests, this mapping
    is the code under test):

    * transport exhausted or gated (0-byte + orcas) -> ``network_error``:
      risk control / a flaky proxy is not an expired cookie.
    * HTTP 401 / 403 -> a real ``failed`` verdict: the platform refused the
      session itself.
    * HTTP 200 + JSON ``{"message": "success"}`` -> authenticated.
    * HTTP 200 + JSON with any other explicit ``message`` -> the platform
      answered and rejected the session: ``failed``.
    * anything else (unparseable / empty 200) -> ``network_error``: a round
      trip that could not conclude says nothing about the cookie.
    """
    identity = TiktokWebIdentity(
        proxy=proxy,
        login_cookie=cookie,
        session_factory=session_factory,
    )
    try:
        response = identity.unsigned_get(LOGIN_PROBE_PATH, dict(_LOGIN_PROBE_PARAMS))
    except Exception as exc:
        return TiktokAuthStatus(
            has_cookie=True,
            authenticated=False,
            network_error=True,
            message=f"TikTok 登录态探测失败（网络 / 代理 / 风控）：{exc}",
        )

    if response.gated:
        return TiktokAuthStatus(
            has_cookie=True,
            authenticated=False,
            network_error=True,
            message="TikTok 登录态探测被风控拦截（空响应），暂时无法判定。",
        )
    if response.status_code in {401, 403}:
        return TiktokAuthStatus(
            has_cookie=True,
            authenticated=False,
            message="TikTok 拒绝了该 Cookie（缺失、无效或已过期）。",
        )
    if response.status_code == 200 and response.body:
        try:
            payload = json.loads(response.body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            note = str(payload.get("message") or "").strip()
            if note == "success":
                return TiktokAuthStatus(
                    has_cookie=True,
                    authenticated=True,
                    message="TikTok Cookie 有效，会话存活（passport beat 确认）。",
                )
            if note:
                return TiktokAuthStatus(
                    has_cookie=True,
                    authenticated=False,
                    message=f"TikTok 拒绝了该 Cookie（passport beat: {note}）。",
                )
    return TiktokAuthStatus(
        has_cookie=True,
        authenticated=False,
        network_error=True,
        message=f"TikTok 登录态探测结果不明（HTTP {response.status_code}），暂时无法判定。",
    )


async def probe_tiktok_login(
    cookie: str,
    *,
    proxy: str | None = None,
    session_factory: Any = None,
) -> TiktokAuthStatus:
    """Probe whether *cookie* is a live TikTok session (passport beat).

    Network failures are reported via ``network_error`` rather than as a
    logged-out verdict, so a flaky proxy never looks like an expired cookie.
    """
    normalized = cookie.strip()
    if not normalized:
        return TiktokAuthStatus(
            has_cookie=False,
            authenticated=False,
            message="尚未配置 TikTok Cookie。",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_probe_tiktok_sync, normalized, proxy=proxy, session_factory=session_factory),
    )


# ---------------------------------------------------------------------------
# itemStruct parsing
# ---------------------------------------------------------------------------


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _duration_seconds(value: Any) -> int:
    duration = _to_int(value)
    if duration > _DURATION_MILLISECONDS_THRESHOLD_SECONDS:
        return duration // 1000
    return duration


def parse_tiktok_item(
    item: Any,
    *,
    source_strategy: str = "",
    require_video: bool = False,
) -> DiscoveredContent | None:
    """Map one web API ``itemStruct`` to ``DiscoveredContent``.

    Field equivalents of ``normalize_tiktok_video`` (yt-dlp shape):
    ``id`` / ``desc`` / ``createTime`` (epoch seconds) /
    ``author{uniqueId,nickname}`` /
    ``stats{playCount,diggCount,commentCount,shareCount,collectCount}`` /
    ``video{duration(ms),cover,originCover}``. The share URL falls back to
    ``https://www.tiktok.com/@{uniqueId}/video/{id}``.
    """
    if not isinstance(item, dict):
        return None
    video_id = _first_text(item.get("id"), item.get("aweme_id"))
    description = _first_text(item.get("desc"))
    if not video_id or not description:
        return None
    if require_video and not isinstance(item.get("video"), dict):
        # Search responses mix card types (user / live / challenge cards);
        # only video cards carry a ``video`` object.
        return None

    author = _as_dict(item.get("author"))
    stats = _as_dict(item.get("stats"))
    video = _as_dict(item.get("video"))

    unique_id = _first_text(author.get("uniqueId"))
    nickname = _first_text(author.get("nickname"), unique_id)

    share_url = _first_text(item.get("shareUrl"))
    if not share_url.startswith("http"):
        share_url = f"{_TIKTOK_BASE_URL}/@{unique_id or 'tiktok'}/video/{video_id}"

    published = normalize_published_time(_to_int(item.get("createTime")))

    return DiscoveredContent(
        content_id=video_id,
        content_url=share_url,
        source_platform="tiktok",
        title=description,
        author_name=nickname,
        up_name=nickname,
        cover_url=_first_text(video.get("cover"), video.get("originCover")),
        duration=_duration_seconds(video.get("duration")),
        view_count=_to_int(stats.get("playCount")),
        like_count=_to_int(stats.get("diggCount")),
        comment_count=_to_int(stats.get("commentCount")),
        share_count=_to_int(stats.get("shareCount")),
        collect_count=_to_int(stats.get("collectCount")),
        description=description[:300],
        source_strategy=source_strategy,
        published_at=published.published_at,
        published_label=published.published_label,
        source_metadata={"tiktok_aweme_id": video_id},
    )


def _parse_item_list(value: Any, *, source_strategy: str = "") -> list[DiscoveredContent]:
    if not isinstance(value, list):
        return []
    results: list[DiscoveredContent] = []
    for item in value:
        content = parse_tiktok_item(item, source_strategy=source_strategy)
        if content is not None:
            results.append(content)
    return results


# ---------------------------------------------------------------------------
# Async web client
# ---------------------------------------------------------------------------


@dataclass
class TiktokWebClient:
    """Async TikTok discovery client backed by the signed web API.

    Method contract: ``None`` = backend unavailable (transport exhausted or
    still gated after one identity re-bootstrap), ``[]`` = request succeeded
    with no items. Only ``None`` should trigger a backend fallback.
    """

    identity: TiktokWebIdentity
    _sec_uid_cache: dict[str, str] = field(default_factory=dict)
    _challenge_id_cache: dict[str, str] = field(default_factory=dict)

    # -- blocking core (executor) ------------------------------------------

    def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        """Signed GET → decoded JSON object, or ``None`` when unavailable.

        A gated (0-byte + orcas) response triggers exactly one identity
        re-bootstrap; if the retry is still gated the backend degrades for
        this call instead of hammering the gate.
        """
        identity = self.identity
        if not identity.ensure_bootstrapped():
            return None
        response: TiktokWebResponse | None = None
        for attempt in range(2):
            try:
                response = identity.signed_get(path, params)
            except Exception as exc:
                logger.warning("tiktok web %s transport failed: %s", path, exc)
                return None
            if not response.gated:
                break
            if attempt == 0:
                identity.invalidate()
                if identity.ensure_bootstrapped():
                    logger.info("tiktok web %s gated; re-bootstrapped identity, retrying", path)
                    continue
            logger.warning("tiktok web %s stayed gated after re-bootstrap; degrading", path)
            return None
        if response is None or not response.body:
            logger.warning("tiktok web %s returned an empty body", path)
            return None
        try:
            data = json.loads(response.body)
        except ValueError as exc:
            logger.warning("tiktok web %s returned non-JSON body: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def _feed_items(self, limit: int) -> list[DiscoveredContent] | None:
        data = self._request_json(
            "/api/recommend/item_list/",
            {
                **self.identity.base_params("fyp"),
                "count": str(max(1, limit)),
                "itemType": "0",
            },
        )
        if data is None:
            return None
        return _parse_item_list(data.get("itemList"))

    def _author_profile(self, unique_id: str) -> dict[str, Any] | None:
        data = self._request_json(
            "/api/user/detail/",
            {**self.identity.base_params("user"), "secUid": "", "uniqueId": unique_id},
        )
        if data is None:
            return None
        user_info = data.get("userInfo")
        return user_info if isinstance(user_info, dict) else {}

    def _resolve_sec_uid(self, handle: str) -> str | None:
        """handle → secUid. ``""`` = user not found; ``None`` = backend down."""
        cached = self._sec_uid_cache.get(handle)
        if cached:
            return cached
        profile = self._author_profile(handle)
        if profile is None:
            return None
        user = _as_dict(profile.get("user"))
        sec_uid = _first_text(user.get("secUid"))
        if sec_uid:
            self._sec_uid_cache[handle] = sec_uid
        return sec_uid

    def _user_video_items(self, handle: str, limit: int) -> list[DiscoveredContent] | None:
        sec_uid = self._resolve_sec_uid(handle)
        if sec_uid is None:
            return None
        if not sec_uid:
            logger.info("tiktok web: no secUid for handle %r (unknown user?)", handle)
            return []
        data = self._request_json(
            "/api/post/item_list/",
            {
                **self.identity.base_params("user"),
                "secUid": sec_uid,
                "cursor": "0",
                "count": str(max(1, limit)),
                "coverFormat": "2",
                "needPinnedItemIds": "true",
                "locate_item_id": "",
                "post_item_list_request_type": "0",
            },
        )
        if data is None:
            return None
        return _parse_item_list(data.get("itemList"))

    def _resolve_challenge_id(self, tag: str) -> str | None:
        """tag → challenge id. ``""`` = unknown tag; ``None`` = backend down."""
        cached = self._challenge_id_cache.get(tag.casefold())
        if cached:
            return cached
        data = self._request_json(
            "/api/challenge/detail/",
            {**self.identity.base_params("hashtag"), "challengeName": tag},
        )
        if data is None:
            return None
        info = _as_dict(data.get("challengeInfo"))
        challenge = _as_dict(info.get("challenge"))
        challenge_id = _first_text(challenge.get("id"))
        if challenge_id:
            self._challenge_id_cache[tag.casefold()] = challenge_id
        return challenge_id

    def _tag_video_items(self, tag: str, limit: int) -> list[DiscoveredContent] | None:
        challenge_id = self._resolve_challenge_id(tag)
        if challenge_id is None:
            return None
        if not challenge_id:
            logger.info("tiktok web: no challenge id for tag %r (unknown tag?)", tag)
            return []
        data = self._request_json(
            "/api/challenge/item_list/",
            {
                **self.identity.base_params("hashtag"),
                "challengeID": challenge_id,
                "cursor": "0",
                "count": str(max(1, limit)),
                "coverFormat": "2",
            },
        )
        if data is None:
            return None
        return _parse_item_list(data.get("itemList"))

    def _search_items(self, keyword: str, limit: int) -> list[DiscoveredContent]:
        """Keyword search (reserved; guest identity is gated upstream).

        Verified 2026-10-03: ``/api/search/item/full/`` returns the gated
        empty body for guest identities even with a real msToken — TikTok
        requires a browser session that has searched before (login cookie).
        Failure therefore degrades to ``[]`` with a warning, never to a
        backend-down signal: there is no yt-dlp search fallback either.
        """
        data = self._request_json(
            "/api/search/item/full/",
            {
                **self.identity.base_params("search"),
                "keyword": keyword,
                "cursor": "0",
                "count": str(max(1, limit)),
                "web_search_code": _SEARCH_WEB_SEARCH_CODE,
            },
        )
        if data is None:
            logger.warning(
                "tiktok web search(%r) unavailable; keyword search needs a login "
                "cookie ([sources.tiktok].cookie_env / data/tiktok_cookie.json)",
                keyword,
            )
            return []
        raw = data.get("item_list") or data.get("itemList")
        if not isinstance(raw, list):
            return []
        results: list[DiscoveredContent] = []
        for item in raw:
            # Search mixes card types; keep only video items.
            content = parse_tiktok_item(item, require_video=True)
            if content is not None:
                results.append(content)
        return results

    # -- async surface -------------------------------------------------------

    async def get_feed(self, *, limit: int = 12) -> list[DiscoveredContent] | None:
        """Anonymous For-You feed (also the identity bootstrap endpoint)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._feed_items, max(1, limit)))

    async def get_author_profile(self, unique_id: str) -> dict[str, Any] | None:
        """Public profile for one handle (``userInfo`` dict)."""
        handle = normalize_tiktok_handle(unique_id)
        if not handle:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._author_profile, handle))

    async def get_user_videos(
        self, handle: str, *, limit: int = 10
    ) -> list[DiscoveredContent] | None:
        """One creator's recent uploads (handle → secUid → post list)."""
        normalized = normalize_tiktok_handle(handle)
        if not normalized:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._user_video_items, normalized, max(1, limit))
        )

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[DiscoveredContent] | None:
        """One hashtag's video listing (tag → challenge id → item list)."""
        normalized = normalize_tiktok_tag(tag)
        if not normalized:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._tag_video_items, normalized, max(1, limit))
        )

    async def search_videos(self, keyword: str, *, limit: int = 20) -> list[DiscoveredContent]:
        """Keyword search (reserved; needs a login cookie to be useful)."""
        text = str(keyword or "").strip()
        if not text:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._search_items, text, max(1, limit)))


# ---------------------------------------------------------------------------
# Backend router
# ---------------------------------------------------------------------------

_TIKTOK_MODES = ("auto", "web", "ytdlp")


@dataclass
class TiktokRouterClient:
    """Dispatch TikTok calls between the web API and yt-dlp backends.

    ``mode`` semantics (``[sources.tiktok].mode``):

    - ``auto`` (default): web backend first; a backend-level failure
      (``None``) falls back to yt-dlp for that call. After
      ``max_web_failures`` consecutive backend failures the web backend is
      parked for the process lifetime and yt-dlp serves everything.
    - ``web``: web backend only; failures surface as empty results.
    - ``ytdlp``: yt-dlp only (the pre-web-backend behavior).
    """

    web: TiktokWebClient | None = None
    ytdlp: TiktokClient = field(default_factory=TiktokClient)
    mode: str = "auto"
    max_web_failures: int = 3
    _web_failures: int = field(default=0, init=False, repr=False)

    def _web_usable(self) -> bool:
        return (
            self.web is not None
            and self.mode in {"auto", "web"}
            and self._web_failures < max(1, self.max_web_failures)
        )

    def _record_web_result(self, result: Any) -> None:
        if result is None:
            self._web_failures += 1
            if self._web_failures == max(1, self.max_web_failures):
                logger.warning(
                    "tiktok router: web backend parked after %d consecutive failures",
                    self._web_failures,
                )
        else:
            self._web_failures = 0

    @property
    def search_available(self) -> bool:
        """Whether keyword search can work: web backend + a login cookie.

        Guest identities are gated on ``/api/search/item/full/`` upstream
        (verified 2026-10-03), so search is only meaningful with a configured
        login cookie, and only the web backend has a search surface at all.
        """
        return (
            self.web is not None
            and self.mode in {"auto", "web"}
            and bool(self.web.identity.login_cookie.strip())
        )

    async def search_videos(self, keyword: str, *, limit: int = 20) -> list[Any]:
        """Keyword search — web backend only (yt-dlp has no search surface).

        Returns ``[]`` when the web backend is unusable: there is no yt-dlp
        fallback to fail over to, so a backend failure and an empty result
        are the same outcome here.
        """
        if not self._web_usable() or self.web is None:
            return []
        result = await self.web.search_videos(keyword, limit=limit)
        return list(result)

    async def get_feed(self, *, limit: int = 12) -> list[DiscoveredContent] | None:
        """For-You feed — web backend only (yt-dlp has no feed surface)."""
        if not self._web_usable() or self.web is None:
            return None
        result = await self.web.get_feed(limit=limit)
        self._record_web_result(result)
        return result

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> list[Any]:
        if self._web_usable() and self.web is not None:
            result = await self.web.get_user_videos(handle, limit=limit)
            self._record_web_result(result)
            if result is not None:
                return result
            if self.mode == "web":
                return []
            logger.info("tiktok router: web backend failed for user %r; yt-dlp fallback", handle)
        return await self.ytdlp.get_user_videos(handle, limit=limit)

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[Any]:
        if self._web_usable() and self.web is not None:
            result = await self.web.get_tag_videos(tag, limit=limit)
            self._record_web_result(result)
            if result is not None:
                return result
            if self.mode == "web":
                return []
            logger.info("tiktok router: web backend failed for tag %r; yt-dlp fallback", tag)
        return await self.ytdlp.get_tag_videos(tag, limit=limit)
