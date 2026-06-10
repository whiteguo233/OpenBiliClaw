"""X (Twitter) discovery strategies — fetch-only producers.

Three strategies drive the server-side ``XClient`` (cookie replay over
``twitter-cli``) and normalize the raw ``tweet_to_dict`` dicts into
:class:`DiscoveredContent`:

``XSearchStrategy``
    LLM generates X-flavored search keyword(s) from the Soul profile (reusing
    the ``xhs_keyword_gen`` approach) → ``XClient.search`` → ``normalize_tweet``.
    An explicit ``query`` (from a recipe / subscription) short-circuits keyword
    generation.

``XForYouStrategy``
    Reads the user's "For You" home timeline (``XClient.for_you``).

``XCreatorStrategy``
    Reads a creator's recent tweets by handle (``XClient.user_tweets``).

These strategies are **fetch-only**: they return normalized candidates and do
NOT score / write ``content_cache``. The shared mixed-source evaluator owns
that downstream (per the unified-pool spec). ``normalize_tweet`` → ``None``
items (tombstones / unavailable tweets) are dropped.

Lazy-import note: this module never imports ``twitter_cli`` at module load.
``XClient`` is referenced only for type hints (``TYPE_CHECKING``); the concrete
client is injected by the runtime on the enabled path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.discovery.x_normalize import normalize_tweet
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

# Source-strategy tags carried on every normalized item (and used by the
# producer / pool to attribute X candidates to the right sub-strategy).
SEARCH_STRATEGY_TAG = "x-search"
FEED_STRATEGY_TAG = "x-feed"
CREATOR_STRATEGY_TAG = "x-creator"


class SupportsXRead(Protocol):
    """The subset of :class:`XClient` the strategies drive (tests inject fakes)."""

    async def search(
        self, query: str, *, limit: int, product: str = "Top"
    ) -> list[dict[str, Any]]: ...

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]: ...

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]: ...


class SupportsStructuredTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object: ...


# X-flavored keyword generation. Mirrors ``xhs_keyword_gen``: a byte-static
# system prompt (prompt-cache convention) with all per-call data in the user
# message.
_KEYWORDS_SYSTEM_PROMPT = """\
你要为 X(Twitter)内容发现生成一组适合 X 搜索的关键词。

规则：
1. 输出必须是严格 JSON，不要附带解释。
2. query 是 1-4 个词的短语，适合直接在 X 搜索框输入。
3. 优先英文(X 上英文内容更多),技术/小众话题尤其如此;华语圈话题可用中文。
4. 数量 3 到 6 个,覆盖用户画像中不同兴趣领域。
5. 避免过于宽泛的单词,带上限定词。

输出格式：
{"keywords": ["rust async runtime", "machine learning papers", ...]}
"""


def _parse_keywords(content: str, *, count: int) -> list[str]:
    payload = parse_llm_json_tolerant(content)
    if payload is None:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("x keyword LLM returned non-JSON: %r", content[:200])
            return []
    if not isinstance(payload, dict):
        return []
    raw_keywords = payload.get("keywords", [])
    if not isinstance(raw_keywords, list):
        return []

    seen: set[str] = set()
    keywords: list[str] = []
    for item in raw_keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        keywords.append(text)
        if len(keywords) >= count:
            break
    return keywords


def _normalize_raw(
    raw_tweets: list[dict[str, Any]],
    *,
    source_strategy: str,
) -> list[DiscoveredContent]:
    """Normalize raw ``tweet_to_dict`` dicts, dropping tombstones + dupes."""
    seen: set[str] = set()
    out: list[DiscoveredContent] = []
    for raw in raw_tweets:
        content = normalize_tweet(raw, source_strategy=source_strategy)
        if content is None:
            continue
        key = content.content_id
        if key in seen:
            continue
        seen.add(key)
        out.append(content)
    return out


# ── XSearchStrategy ──────────────────────────────────────────────────


@dataclass
class XSearchStrategy:
    """Discover X content by (LLM-generated) keyword search."""

    client: SupportsXRead
    llm_service: SupportsStructuredTask | None = None
    keywords_per_run: int = 4
    product: str = "Top"
    last_intermediates: dict[str, object] = field(default_factory=dict)

    async def discover(
        self,
        profile: SoulProfile,
        *,
        limit: int = 20,
        query: str = "",
        **_: object,
    ) -> list[DiscoveredContent]:
        explicit = (query or "").strip()
        keywords = [explicit] if explicit else await self._generate_keywords(profile)
        self.last_intermediates = {"keywords": list(keywords)}
        if not keywords:
            return []

        seen: set[str] = set()
        results: list[DiscoveredContent] = []
        for keyword in keywords:
            raw = await self.client.search(keyword, limit=limit, product=self.product)
            for content in _normalize_raw(raw, source_strategy=SEARCH_STRATEGY_TAG):
                if content.content_id in seen:
                    continue
                seen.add(content.content_id)
                results.append(content)
                if len(results) >= limit:
                    return results
        return results

    async def _generate_keywords(self, profile: SoulProfile) -> list[str]:
        if self.llm_service is None:
            return []
        interests = list(profile.preferences.interests)
        if not interests:
            return []
        interests.sort(key=lambda t: t.weight, reverse=True)
        interest_tuples = [(t.name, t.category, t.weight) for t in interests if t.name]
        if not interest_tuples:
            return []

        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=_KEYWORDS_SYSTEM_PROMPT,
                user_input=_build_keyword_user_prompt(interest_tuples, self.keywords_per_run),
                temperature=0.8,
                max_tokens=512,
                caller="discovery.x.keyword_gen",
            )
        except Exception as exc:  # noqa: BLE001 - degrade to "nothing this cycle"
            logger.warning("x keyword LLM call failed: %s", exc)
            return []

        content = getattr(response, "content", response)
        text = str(content).strip()
        return _parse_keywords(text, count=self.keywords_per_run)


def _build_keyword_user_prompt(
    interest_tags: list[tuple[str, str, float]],
    count: int,
) -> str:
    lines = ["用户兴趣画像（name | category | weight）："]
    for name, category, weight in interest_tags[:15]:
        cat = category or "-"
        lines.append(f"- {name} | {cat} | {weight:.2f}")
    lines.append(f"\n请输出 {count} 个适合 X 搜索的关键词。")
    return "\n".join(lines)


# ── XForYouStrategy ──────────────────────────────────────────────────


@dataclass
class XForYouStrategy:
    """Discover X content from the user's "For You" home timeline."""

    client: SupportsXRead

    async def discover(
        self,
        profile: SoulProfile,
        *,
        limit: int = 20,
        **_: object,
    ) -> list[DiscoveredContent]:
        raw = await self.client.for_you(limit=limit)
        return _normalize_raw(raw, source_strategy=FEED_STRATEGY_TAG)[:limit]


# ── XCreatorStrategy ─────────────────────────────────────────────────


@dataclass
class XCreatorStrategy:
    """Discover X content from a subscribed creator's recent tweets."""

    client: SupportsXRead

    async def discover(
        self,
        profile: SoulProfile,
        *,
        limit: int = 20,
        handle: str = "",
        **_: object,
    ) -> list[DiscoveredContent]:
        target = (handle or "").strip()
        if not target:
            return []
        raw = await self.client.user_tweets(target, limit=limit)
        return _normalize_raw(raw, source_strategy=CREATOR_STRATEGY_TAG)[:limit]
