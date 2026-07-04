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

from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.discovery.x_normalize import normalize_tweet
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.task_options import without_core_memory_kwargs

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


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    """Strip + dedupe caller-injected keywords (unified planner injection)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


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
        queries: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
        **_: object,
    ) -> list[DiscoveredContent]:
        explicit = (query or "").strip()
        if explicit:
            keywords = [explicit]
        elif queries is not None:
            # Unified keyword planner injection: search each supplied keyword,
            # skipping internal LLM keyword generation.
            keywords = _dedupe_keywords(queries)
        else:
            keywords = await self._generate_keywords(profile)
        self.last_intermediates = {"keywords": list(keywords)}
        if not keywords:
            return []

        seen: set[str] = set()
        results: list[DiscoveredContent] = []
        for keyword in keywords:
            # P1.8 yield provenance: the id of the word currently being searched
            # (unified planner injection). ``None`` when unmapped / not injected.
            keyword_id = keyword_ids.get(keyword) if keyword_ids else None
            raw = await self.client.search(keyword, limit=limit, product=self.product)
            for content in _normalize_raw(raw, source_strategy=SEARCH_STRATEGY_TAG):
                if content.content_id in seen:
                    continue
                content.source_keyword_id = keyword_id
                seen.add(content.content_id)
                results.append(content)
                if len(results) >= limit:
                    return results
        return results

    async def _generate_keywords(self, profile: SoulProfile) -> list[str]:
        if not profile.preferences.interests:
            return []
        keywords = await self._llm_keywords(profile)
        # Deterministic fallback when LLM is unavailable / fails / returns
        # nothing — so the unified planner (and the legacy path) never loses X
        # to a transient failure (mirrors B站/YouTube/抖音).
        return keywords or _x_interest_fallback(profile, self.keywords_per_run)

    async def _llm_keywords(self, profile: SoulProfile) -> list[str]:
        if self.llm_service is None:
            return []
        try:
            complete_structured = self.llm_service.complete_structured_task
            response = await complete_structured(
                system_instruction=_KEYWORDS_SYSTEM_PROMPT,
                user_input=_build_keyword_user_prompt(profile, self.keywords_per_run),
                temperature=0.8,
                max_tokens=512,
                caller="discovery.x.keyword_gen",
                **without_core_memory_kwargs(complete_structured),
            )
        except Exception as exc:  # noqa: BLE001 - degrade to fallback
            logger.warning("x keyword LLM call failed: %s", exc)
            return []

        content = getattr(response, "content", response)
        text = str(content).strip()
        return _parse_keywords(text, count=self.keywords_per_run)


def _build_keyword_user_prompt(profile: SoulProfile, count: int) -> str:
    # Same canonical structured profile every other discovery prompt sees
    # (B站 / YouTube query-gen, all-platform evaluation) — no divergent
    # representation. Deterministic dump keeps the prompt-cache prefix stable.
    summary = build_profile_summary(profile)
    return (
        "<profile_summary>\n"
        + json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n</profile_summary>\n\n"
        + "请基于上面画像里的兴趣（interests / interest_domains），结合 disliked_topics 避雷，"
        + f"输出 {count} 个适合 X 搜索的关键词。"
    )


def _x_interest_fallback(profile: SoulProfile, count: int) -> list[str]:
    """Deterministic interest-name keywords (mirrors B站/YouTube/抖音 fallback)."""
    ranked = sorted(
        profile.preferences.interests, key=lambda tag: float(tag.weight or 0.0), reverse=True
    )
    seen: set[str] = set()
    out: list[str] = []
    for tag in ranked:
        name = str(tag.name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= count:
            break
    return out


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
