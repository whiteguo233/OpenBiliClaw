"""Comment filtering shared by the action layer and the discovery route.

The discovery route decides which comments are worth leaving a *plan* for;
the engine decides which comments are worth *answering*. Both must apply the
same bounds, otherwise every plan is built from comments the engine then
rejects (or the route plans a comment the engine would have answered).

The only difference is what each caller can know: the route has no ledger, so
it passes no ``answered`` set and no fish ``mid``; the engine passes both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.bilibili.api import CommentInfo

    from .config import FishConfig

# A one-character comment ("好", "顶") gives the model nothing to answer, and
# a 500+ character wall of text is not a comment thread anymore. Both bounds
# match the reply budget the publisher already enforces.
MIN_COMMENT_CHARS = 2
MAX_COMMENT_CHARS = 500


def usable_comments(
    comments: list[CommentInfo],
    *,
    config: FishConfig,
    answered: frozenset[str] = frozenset(),
    bvid: str = "",
    fish_mid: int = 0,
) -> list[CommentInfo]:
    """Return the comments the fish may answer, in upstream order.

    Args:
        comments: Comments fetched from one video, newest page first.
        config: Current lab config (minimum likes, blocked-word-agnostic).
        answered: ``bvid:rpid`` keys already present in the ledger.
        bvid: The video the comments belong to, used for ``answered``.
        fish_mid: The fish's own mid, excluded so it never answers itself.
    """

    usable: list[CommentInfo] = []
    seen: set[int] = set()
    for item in comments:
        rpid = int(getattr(item, "rpid", 0) or 0)
        if rpid <= 0 or rpid in seen:
            continue
        seen.add(rpid)
        text = str(item.message or "").strip()
        if not (MIN_COMMENT_CHARS <= len(text) <= MAX_COMMENT_CHARS):
            continue
        if int(item.like_count or 0) < config.min_comment_likes:
            continue
        if fish_mid and int(item.mid or 0) == fish_mid:
            continue
        if bvid and f"{bvid}:{rpid}" in answered:
            continue
        usable.append(item)
    return usable
