"""Server-side X (Twitter) read client — a thin async wrapper over ``twitter-cli``.

Discovery for the X source is server-side cookie replay (like Bilibili /
Douyin-direct, NOT an XHS-style stub). This client harvests the user's
``auth_token`` + ``ct0`` cookies and drives ``twitter_cli.client.TwitterClient``'s
**synchronous** read methods.

Design contract (see ``docs/plans/2026-06-08-x-twitter-source-plan.md`` Task 6):

* **Lazy import.** ``twitter_cli`` (and its ``curl_cffi`` transitive dep) is
  imported *inside* the network seam, never at module top. Importing this
  module on a non-X install (where the ``openbiliclaw[x]`` extra is absent)
  must not fail. ``tests/test_x_client.py`` regresses this.
* **Async wrapper.** ``twitter_cli`` reads are synchronous (curl_cffi), so the
  public ``search`` / ``for_you`` / ``user_tweets`` (discovery) and ``likes`` /
  ``bookmarks`` (init preference backfill) coroutines run them via
  :func:`asyncio.to_thread`.
* **Return shape.** Each public method returns ``list[dict]`` — the output of
  ``twitter_cli.serialization.tweet_to_dict``. Keys are camelCase/JSON-safe:
  ``id`` (the rest_id), ``text``, ``author`` (``{id, name, screenName,
  profileImageUrl, verified}``), ``metrics`` (``{likes, retweets, replies,
  quotes, views, bookmarks}``), ``createdAt`` / ``createdAtISO``, ``media``,
  ``urls``, ``isRetweet``, ``retweetedBy``, ``lang``, ``score``, and optional
  ``articleTitle`` / ``articleText`` (long-form note_tweet) / ``quotedTweet``.
  Task 7's ``normalize_tweet`` consumes these dicts.
* **Typed errors.** Underlying ``TwitterAPIError`` / ``AuthenticationError`` map
  onto a small hierarchy so Task 10's source-health machine can branch cleanly:
  missing cookie → :class:`XMissingCookieError` (``missing_cookie``); 401 →
  :class:`XAuthError` (``expired_cookie``); 403 → :class:`XBlockedError`
  (``blocked``); 429 → :class:`XRateLimitError` (``rate_limited``).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime here
    from twitter_cli.models import Tweet


class XClientError(RuntimeError):
    """Base class for all X client failures."""


class XMissingCookieError(XClientError):
    """No usable cookie (``auth_token`` and/or ``ct0`` missing).

    Raised lazily on first use — before any ``twitter_cli`` import — so the
    disabled / unconfigured path never touches the optional dependency.
    """


class XAuthError(XClientError):
    """Authentication failed (HTTP 401 / ``AuthenticationError``) — cookie expired."""


class XBlockedError(XClientError):
    """Request blocked (HTTP 403) — account/region/endpoint forbidden."""


class XRateLimitError(XClientError):
    """Rate limited (HTTP 429) — back off and retry later."""


def _parse_cookie(cookie: str) -> tuple[str, str]:
    """Pull ``auth_token`` and ``ct0`` out of a cookie header string.

    Accepts the usual ``"auth_token=...; ct0=...; other=..."`` form. Raises
    :class:`XMissingCookieError` if either value is absent or empty.
    """
    pairs: dict[str, str] = {}
    for chunk in (cookie or "").split(";"):
        name, sep, value = chunk.partition("=")
        if not sep:
            continue
        pairs[name.strip()] = value.strip()
    auth_token = pairs.get("auth_token", "")
    ct0 = pairs.get("ct0", "")
    if not auth_token or not ct0:
        missing = [k for k, v in (("auth_token", auth_token), ("ct0", ct0)) if not v]
        raise XMissingCookieError(f"x cookie missing required field(s): {', '.join(missing)}")
    return auth_token, ct0


class XClient:
    """Async wrapper over ``twitter_cli.client.TwitterClient`` read methods.

    The cookie is parsed lazily (on first call), so constructing an ``XClient``
    is cheap and never imports ``twitter_cli``.
    """

    def __init__(self, cookie: str) -> None:
        self._cookie = cookie or ""

    # -- internal helpers -------------------------------------------------

    def _auth_pair(self) -> tuple[str, str]:
        """Lazily parse and return ``(auth_token, ct0)`` from the cookie."""
        return _parse_cookie(self._cookie)

    def _client(self) -> Any:
        """Build a ``twitter_cli`` client (lazy import lives here)."""
        from twitter_cli.client import TwitterClient

        auth_token, ct0 = self._auth_pair()
        return TwitterClient(auth_token, ct0)

    # -- network seams (synchronous; monkeypatched in tests) --------------
    #
    # These are the only place ``twitter_cli`` is driven. Tests replace them
    # so no network call (and no real cookie) is needed.

    def _raw_search(self, query: str, *, count: int, product: str) -> list[Tweet]:
        return list(self._client().fetch_search(query, count=count, product=product))

    def _raw_for_you(self, *, count: int) -> list[Tweet]:
        # home_timeline is the "For You" feed; fetch_following_feed is chronological.
        return list(self._client().fetch_home_timeline(count=count))

    def _raw_user_tweets(self, handle: str, *, count: int) -> list[Tweet]:
        client = self._client()
        user_id = client.resolve_user_id(handle)
        return list(client.fetch_user_tweets(user_id, count=count))

    def _raw_likes(self, *, count: int) -> list[Tweet]:
        # The authenticated user's own Likes timeline. fetch_user_likes needs a
        # user_id, so resolve "me" first (one extra read, init-time only).
        client = self._client()
        me = client.fetch_me()
        user_id = str(getattr(me, "id", "") or "")
        if not user_id:
            raise XClientError("could not resolve authenticated user id for likes")
        return list(client.fetch_user_likes(user_id, count=count))

    def _raw_bookmarks(self, *, count: int) -> list[Tweet]:
        return list(self._client().fetch_bookmarks(count=count))

    # -- public async API -------------------------------------------------

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        """Search X. Returns up to ``limit`` ``tweet_to_dict`` dicts."""
        tweets = await self._run(self._raw_search, query, count=limit, product=product)
        return self._serialize(tweets, limit)

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]:
        """Fetch the "For You" home timeline. Returns ``tweet_to_dict`` dicts."""
        tweets = await self._run(self._raw_for_you, count=limit)
        return self._serialize(tweets, limit)

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]:
        """Fetch a creator's recent tweets by handle. Returns ``tweet_to_dict`` dicts."""
        tweets = await self._run(self._raw_user_tweets, handle, count=limit)
        return self._serialize(tweets, limit)

    async def likes(self, *, limit: int) -> list[dict[str, Any]]:
        """Fetch the authenticated user's own liked tweets (init preference backfill).

        Unlike ``search`` / ``for_you`` (discovery), this reads the user's *own*
        historical engagement to seed the soul profile — the X analogue of B站
        favorites backfill. Returns ``tweet_to_dict`` dicts.
        """
        tweets = await self._run(self._raw_likes, count=limit)
        return self._serialize(tweets, limit)

    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]:
        """Fetch the authenticated user's own bookmarked tweets (init preference backfill).

        Returns ``tweet_to_dict`` dicts.
        """
        tweets = await self._run(self._raw_bookmarks, count=limit)
        return self._serialize(tweets, limit)

    # -- plumbing ---------------------------------------------------------

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> list[Tweet]:
        """Run a sync seam off-thread, mapping twitter_cli errors to ours.

        ``XMissingCookieError`` propagates unchanged (raised before any import).
        """
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except XClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize twitter_cli surface
            raise _map_exception(exc) from exc

    @staticmethod
    def _serialize(tweets: list[Tweet], limit: int) -> list[dict[str, Any]]:
        from twitter_cli.serialization import tweet_to_dict

        capped = tweets[:limit] if limit and limit > 0 else tweets
        return [tweet_to_dict(t) for t in capped]


def _map_exception(exc: Exception) -> XClientError:
    """Translate a ``twitter_cli`` exception into the local typed hierarchy."""
    # Imported lazily so a non-X install can still import this module to map
    # nothing (the except path only runs once twitter_cli is in play).
    from twitter_cli.client import TwitterAPIError
    from twitter_cli.exceptions import AuthenticationError

    if isinstance(exc, TwitterAPIError):
        status = getattr(exc, "status_code", None)
        if status == 401:
            return XAuthError(str(exc))
        if status == 403:
            return XBlockedError(str(exc))
        if status == 429:
            return XRateLimitError(str(exc))
        return XClientError(str(exc))
    if isinstance(exc, AuthenticationError):
        # No status_code on AuthenticationError; treat as an expired/invalid cookie.
        return XAuthError(str(exc))
    return XClientError(str(exc))
