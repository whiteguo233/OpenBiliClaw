"""Generic Web adapter with a formal public lane and a legacy browser lane.

``feed`` / ``sitemap`` / ``listing`` recipes use anonymous bounded HTTP and
deterministic parsers.  The older ``web_extract`` strategy remains available
for callers that explicitly opted into browser + LLM extraction, but it is not
part of the formal public-Web discovery contract or scheduled producer.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.browser import BrowserManager
from openbiliclaw.sources.llm_extractor import extract_content_from_page
from openbiliclaw.sources.protocol import SourceFetchResult, SourceFetchStatus
from openbiliclaw.sources.web_public import (
    WEB_MAX_SITEMAP_ENTRIES,
    HTMLListingPlan,
    SafeWebTransport,
    WebFetchError,
    WebParseError,
    classify_html_access_wall,
    parse_html_detail,
    parse_html_listing,
    parse_sitemap,
    parse_web_feed,
    validate_html_listing_plan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

_SITEMAP_CHILD_OFFSET = "sitemap_child_offset"
_SITEMAP_ENTRY_OFFSET = "sitemap_entry_offset"
_SITEMAP_ROOT_KIND = "sitemap_root_kind"
_SITEMAP_TOPOLOGY = "sitemap_topology"
_SITEMAP_CHILD_TOPOLOGY = "sitemap_child_topology"
_SITEMAP_RECOVERY_ACTIVE = "sitemap_recovery_active"
_SITEMAP_RECOVERY_TOPOLOGY = "sitemap_recovery_topology"
_SITEMAP_RECOVERY_REMAINING = "sitemap_recovery_remaining"
_SITEMAP_RECOVERY_CHILD_TOPOLOGY = "sitemap_recovery_child_topology"
_SITEMAP_RECOVERY_CHILD_REMAINING = "sitemap_recovery_child_remaining"


class WebSourceAdapter:
    """Generic web content adapter using browser + LLM extraction.

    Recipe config keys:
        url_template: URL pattern, may contain ``{query}`` placeholder.
        query: Search query (substituted into url_template).
        url: Direct URL to fetch (used when url_template is not set).
    """

    def __init__(
        self,
        *,
        llm_service: Any,
        browser_executable: str = "",
        browser_headed: bool = False,
        browser_cdp_url: str = "",
        public_transport_factory: Any = SafeWebTransport,
        request_timeout_seconds: float = 15.0,
        max_response_bytes: int = 2_097_152,
        max_redirects: int = 5,
    ) -> None:
        self._llm_service = llm_service
        self._browser_executable = browser_executable
        self._browser_headed = browser_headed
        self._browser_cdp_url = browser_cdp_url
        self._public_transport_factory = public_transport_factory
        self._request_timeout_seconds = request_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects

    @property
    def source_type(self) -> str:
        return "web"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch content while preserving the legacy adapter protocol."""
        if recipe.strategy in {"feed", "sitemap", "listing"}:
            return (await self.fetch_result(recipe, profile, limit=limit)).items
        return await self._fetch_browser_legacy(recipe, profile, limit=limit)

    async def fetch_result(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
        *,
        state: dict[str, Any] | None = None,
    ) -> SourceFetchResult:
        """Return a structured outcome for one formal public-Web recipe."""
        del profile  # Public following is deterministic and profile-independent.
        if recipe.strategy not in {"feed", "sitemap", "listing"}:
            return SourceFetchResult(
                status="failed",
                scope_complete=False,
                error_code="unsupported_strategy",
                detail=f"unsupported public Web strategy: {recipe.strategy}",
            )
        config = recipe.config or {}
        entry_url = str(config.get("entry_url") or "").strip()
        allowed_origins = [str(value) for value in config.get("allowed_origins", ())]
        if not entry_url or not allowed_origins:
            return SourceFetchResult(
                status="failed",
                scope_complete=False,
                error_code="invalid_recipe",
                detail="public Web recipe is missing entry_url or allowed_origins",
            )
        bounded_limit = min(max(1, int(limit)), max(1, int(config.get("max_items", 20))))
        listing_plan: HTMLListingPlan | None = None
        if recipe.strategy == "listing":
            raw_listing = config.get("listing")
            raw_plan: Mapping[str, Any] = raw_listing if isinstance(raw_listing, dict) else {}
            try:
                listing_plan = validate_html_listing_plan(
                    HTMLListingPlan(
                        **{
                            field_name: str(raw_plan.get(field_name) or "")
                            for field_name in HTMLListingPlan.__dataclass_fields__
                        }
                    )
                )
            except WebParseError as exc:
                return SourceFetchResult(
                    status="failed",
                    scope_complete=False,
                    error_code=exc.code,
                    detail=str(exc),
                )
        committed = state or {}
        transport = self._public_transport_factory(
            allowed_origins=allowed_origins,
            timeout_seconds=self._request_timeout_seconds,
            default_max_bytes=self._max_response_bytes,
            max_redirects=self._max_redirects,
        )
        try:
            async with transport:
                if recipe.strategy == "feed":
                    return await self._fetch_feed(
                        transport,
                        entry_url=entry_url,
                        allowed_origins=allowed_origins,
                        limit=bounded_limit,
                        state=committed,
                    )
                if recipe.strategy == "listing":
                    assert listing_plan is not None
                    return await self._fetch_listing(
                        transport,
                        entry_url=entry_url,
                        allowed_origins=allowed_origins,
                        config=config,
                        plan=listing_plan,
                        limit=bounded_limit,
                        state=committed,
                    )
                return await self._fetch_sitemap(
                    transport,
                    entry_url=entry_url,
                    allowed_origins=allowed_origins,
                    config=config,
                    limit=bounded_limit,
                    state=committed,
                )
        except WebFetchError as exc:
            return _fetch_error_result(exc)
        except WebParseError as exc:
            return SourceFetchResult(
                status="degraded",
                scope_complete=False,
                error_code=exc.code,
                detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - stable public outcome boundary
            logger.warning("public Web recipe failed: recipe=%s", recipe.id, exc_info=True)
            return SourceFetchResult(
                status="failed",
                scope_complete=False,
                error_code="unexpected_error",
                detail=str(exc)[:240],
            )

    async def _fetch_feed(
        self,
        transport: Any,
        *,
        entry_url: str,
        allowed_origins: list[str],
        limit: int,
        state: dict[str, Any],
    ) -> SourceFetchResult:
        validator_url = str(state.get("validator_url") or "")
        artifact = await transport.fetch(
            entry_url,
            expected="feed",
            etag=str(state.get("etag") or "") if validator_url else "",
            last_modified=str(state.get("last_modified") or "") if validator_url else "",
            validator_url=validator_url,
        )
        if artifact.not_modified:
            return _artifact_result(artifact, status="not_modified", items=[])
        items = parse_web_feed(
            artifact.body,
            content_type=artifact.content_type,
            base_url=artifact.final_url,
            allowed_origins=allowed_origins,
            limit=limit,
        )
        return _artifact_result(
            artifact,
            status="ok" if items else "empty",
            items=items,
        )

    async def _fetch_listing(
        self,
        transport: Any,
        *,
        entry_url: str,
        allowed_origins: list[str],
        config: dict[str, Any],
        plan: HTMLListingPlan,
        limit: int,
        state: dict[str, Any],
    ) -> SourceFetchResult:
        validator_url = str(state.get("validator_url") or "")
        artifact = await transport.fetch(
            entry_url,
            expected="html",
            etag=str(state.get("etag") or "") if validator_url else "",
            last_modified=str(state.get("last_modified") or "") if validator_url else "",
            validator_url=validator_url,
        )
        if artifact.not_modified:
            return _artifact_result(artifact, status="not_modified", items=[])
        wall = classify_html_access_wall(artifact.text)
        if wall:
            return _artifact_result(
                artifact,
                status="login_required" if wall == "login_required" else "degraded",
                items=[],
                scope_complete=False,
                error_code=wall,
            )
        items = parse_html_listing(
            artifact.body,
            base_url=artifact.final_url,
            allowed_origins=allowed_origins,
            plan=plan,
            limit=limit,
        )
        for item in items:
            item.source_strategy = "web-listing"
        detail_limit = min(len(items), max(0, int(config.get("detail_fetch_limit", 0))))
        detail_error = ""
        for item in items[:detail_limit]:
            try:
                detail_artifact = await transport.fetch(item.content_url, expected="html")
                if detail_artifact.not_modified:
                    continue
                wall = classify_html_access_wall(detail_artifact.text)
                if wall:
                    detail_error = wall
                    break
                detail = parse_html_detail(
                    detail_artifact.body,
                    page_url=detail_artifact.final_url,
                    allowed_origins=allowed_origins,
                )
                if detail is not None:
                    _merge_web_detail(item, detail, source_strategy="web-listing")
            except (WebFetchError, WebParseError) as exc:
                if isinstance(exc, WebFetchError) and exc.code in {
                    "rate_limited",
                    "robots_unavailable",
                }:
                    raise
                detail_error = getattr(exc, "code", "detail_error")
                break
        return _artifact_result(
            artifact,
            status="degraded" if detail_error else "ok" if items else "empty",
            items=items,
            scope_complete=not detail_error,
            error_code=detail_error,
        )

    async def _fetch_sitemap(
        self,
        transport: Any,
        *,
        entry_url: str,
        allowed_origins: list[str],
        config: dict[str, Any],
        limit: int,
        state: dict[str, Any],
    ) -> SourceFetchResult:
        raw_cursor = state.get("cursor")
        cursor: dict[str, Any] = raw_cursor if isinstance(raw_cursor, dict) else {}
        raw_retry_cursor = state.get("retry_cursor")
        retry_cursor: dict[str, Any] = (
            raw_retry_cursor if isinstance(raw_retry_cursor, dict) else {}
        )
        traversal_cursor = retry_cursor or cursor
        recovery_active = bool(retry_cursor.get(_SITEMAP_RECOVERY_ACTIVE, False))
        cursor_active = bool(retry_cursor) or any(
            max(0, int(traversal_cursor.get(key, 0) or 0)) > 0
            for key in (_SITEMAP_CHILD_OFFSET, _SITEMAP_ENTRY_OFFSET)
        )
        known_root_kind = str(traversal_cursor.get(_SITEMAP_ROOT_KIND) or "")
        validator_url = (
            str(state.get("validator_url") or "")
            if known_root_kind == "urlset" and not cursor_active
            else ""
        )
        artifact = await transport.fetch(
            entry_url,
            expected="sitemap",
            etag=str(state.get("etag") or "") if validator_url else "",
            last_modified=str(state.get("last_modified") or "") if validator_url else "",
            validator_url=validator_url,
        )
        if artifact.not_modified:
            return _artifact_result(
                artifact,
                status="not_modified",
                items=[],
                cursor=cursor,
            )
        sitemap = parse_sitemap(
            artifact.body,
            base_url=artifact.final_url,
            allowed_origins=allowed_origins,
            limit=WEB_MAX_SITEMAP_ENTRIES,
        )
        urls = [entry.url for entry in sitemap.entries]
        topology_size = len(sitemap.entries)
        topology_material = "\x00".join(
            [
                sitemap.kind,
                *(value for entry in sitemap.entries for value in (entry.url, entry.last_modified)),
            ]
        )
        topology_fingerprint = hashlib.sha256(topology_material.encode("utf-8")).hexdigest()[:24]
        cursor_topology_material = "\x00".join([sitemap.kind, *urls])
        cursor_topology_fingerprint = hashlib.sha256(
            cursor_topology_material.encode("utf-8")
        ).hexdigest()[:24]
        stored_cursor_topology = str(traversal_cursor.get(_SITEMAP_TOPOLOGY) or "")
        cursor_topology_changed = bool(
            stored_cursor_topology and stored_cursor_topology != cursor_topology_fingerprint
        )
        stored_topology = str(retry_cursor.get(_SITEMAP_RECOVERY_TOPOLOGY) or "")
        topology_changed = recovery_active and stored_topology != topology_fingerprint
        if topology_changed or cursor_topology_changed:
            # An entry offset is only meaningful within the exact root/child
            # topology that produced it.  Start the new proof at a stable
            # boundary instead of applying a stale child-local offset to a
            # different sitemap snapshot.
            traversal_cursor = {}
        recovery_remaining = (
            topology_size
            if topology_changed
            else max(
                0,
                int(retry_cursor.get(_SITEMAP_RECOVERY_REMAINING, topology_size) or 0),
            )
        )
        page_error = ""
        child_offset = 0
        next_child_offset = 0
        selected_child_count = 0
        child_topology_parts: list[str] = []
        child_cursor_topology_parts: list[str] = []
        if sitemap.kind == "sitemapindex":
            urls = []
            # A recovery proof advances one child at a time.  This makes the
            # root counter a proof over distinct children even when the normal
            # max_pages windows overlap after wrapping.
            max_pages = (
                min(len(sitemap.entries), 1)
                if recovery_active
                else min(len(sitemap.entries), max(1, int(config.get("max_pages", 1))))
            )
            child_offset = max(0, int(traversal_cursor.get(_SITEMAP_CHILD_OFFSET, 0) or 0))
            if sitemap.entries:
                child_offset %= len(sitemap.entries)
            selected_children = list(sitemap.entries[child_offset : child_offset + max_pages])
            if len(selected_children) < max_pages and child_offset:
                selected_children.extend(sitemap.entries[: max_pages - len(selected_children)])
            selected_child_count = len(selected_children)
            for child in selected_children:
                child_topology_parts.extend(("child", child.url, child.last_modified))
                child_cursor_topology_parts.extend(("child", child.url))
                try:
                    child_artifact = await transport.fetch(child.url, expected="sitemap")
                    child_map = parse_sitemap(
                        child_artifact.body,
                        base_url=child_artifact.final_url,
                        allowed_origins=allowed_origins,
                        limit=WEB_MAX_SITEMAP_ENTRIES,
                    )
                    if child_map.kind != "urlset":
                        page_error = page_error or "nested_sitemap_index"
                        child_topology_parts.append("nested_sitemap_index")
                        continue
                    child_topology_parts.extend(
                        value
                        for entry in child_map.entries
                        for value in (entry.url, entry.last_modified)
                    )
                    child_cursor_topology_parts.extend(entry.url for entry in child_map.entries)
                    urls.extend(entry.url for entry in child_map.entries)
                except (WebFetchError, WebParseError) as exc:
                    if isinstance(exc, WebFetchError) and exc.code in {
                        "rate_limited",
                        "robots_unavailable",
                    }:
                        raise
                    error_code = getattr(exc, "code", "sitemap_child_error")
                    page_error = page_error or error_code
                    child_topology_parts.extend(("error", error_code))
                    child_cursor_topology_parts.extend(("error", error_code))
                    continue
            if sitemap.entries and selected_child_count:
                next_child_offset = (child_offset + selected_child_count) % len(sitemap.entries)
        if (
            not urls
            and not page_error
            and not recovery_active
            and (sitemap.kind == "urlset" or not sitemap.entries)
        ):
            return _artifact_result(
                artifact,
                status="empty",
                items=[],
                cursor={
                    _SITEMAP_CHILD_OFFSET: next_child_offset,
                    _SITEMAP_ENTRY_OFFSET: 0,
                    _SITEMAP_ROOT_KIND: sitemap.kind,
                    _SITEMAP_TOPOLOGY: cursor_topology_fingerprint,
                },
            )

        child_topology_fingerprint = ""
        child_topology_changed = False
        child_recovery_remaining = 0
        child_cursor_topology_fingerprint = ""
        child_cursor_topology_changed = False
        if sitemap.kind == "sitemapindex":
            child_topology_fingerprint = hashlib.sha256(
                "\x00".join(child_topology_parts).encode("utf-8")
            ).hexdigest()[:24]
            stored_child_topology = str(retry_cursor.get(_SITEMAP_RECOVERY_CHILD_TOPOLOGY) or "")
            child_topology_changed = recovery_active and (
                not stored_child_topology or stored_child_topology != child_topology_fingerprint
            )
            child_cursor_topology_fingerprint = hashlib.sha256(
                "\x00".join(child_cursor_topology_parts).encode("utf-8")
            ).hexdigest()[:24]
            stored_child_cursor_topology = str(traversal_cursor.get(_SITEMAP_CHILD_TOPOLOGY) or "")
            child_cursor_topology_changed = bool(
                stored_child_cursor_topology
                and stored_child_cursor_topology != child_cursor_topology_fingerprint
            )
            child_recovery_remaining = (
                len(urls)
                if child_topology_changed
                else max(
                    0,
                    int(retry_cursor.get(_SITEMAP_RECOVERY_CHILD_REMAINING, len(urls)) or 0),
                )
            )
        offset = (
            0
            if topology_changed
            or cursor_topology_changed
            or child_topology_changed
            or child_cursor_topology_changed
            else max(0, int(traversal_cursor.get(_SITEMAP_ENTRY_OFFSET, 0) or 0))
            if urls
            else 0
        )
        if urls:
            offset %= len(urls)
        detail_budget = min(limit, max(0, int(config.get("detail_fetch_limit", limit))))
        selected = urls[offset : offset + detail_budget]
        items: list[DiscoveredContent] = []
        detail_error = page_error
        for url in selected:
            try:
                detail_artifact = await transport.fetch(url, expected="html")
                wall = classify_html_access_wall(detail_artifact.text)
                if wall:
                    detail_error = detail_error or wall
                    continue
                detail = parse_html_detail(
                    detail_artifact.body,
                    page_url=detail_artifact.final_url,
                    allowed_origins=allowed_origins,
                )
                if detail is None:
                    detail_error = detail_error or "detail_missing_title"
                    continue
                detail.source_strategy = "web-sitemap"
                items.append(detail)
            except (WebFetchError, WebParseError) as exc:
                if isinstance(exc, WebFetchError) and exc.code in {
                    "rate_limited",
                    "robots_unavailable",
                }:
                    raise
                detail_error = detail_error or getattr(exc, "code", "detail_error")
                continue
        reached_page_end = not urls or offset + len(selected) >= len(urls)
        next_offset = 0 if reached_page_end else offset + len(selected)
        if sitemap.kind == "sitemapindex" and not reached_page_end:
            next_child_offset = child_offset
        next_cursor = {
            _SITEMAP_CHILD_OFFSET: next_child_offset,
            _SITEMAP_ENTRY_OFFSET: next_offset,
            _SITEMAP_ROOT_KIND: sitemap.kind,
            _SITEMAP_TOPOLOGY: cursor_topology_fingerprint,
        }
        if sitemap.kind == "sitemapindex" and next_child_offset == child_offset and next_offset > 0:
            next_cursor[_SITEMAP_CHILD_TOPOLOGY] = child_cursor_topology_fingerprint

        effective_error = detail_error
        next_retry_cursor: dict[str, Any] = {}
        if detail_error:
            # Restart the clean-sweep proof after the most recent failed
            # window. Every current root row/child must subsequently complete
            # without another error before validators may advance.
            next_retry_cursor = {
                **next_cursor,
                _SITEMAP_RECOVERY_ACTIVE: True,
                _SITEMAP_RECOVERY_TOPOLOGY: topology_fingerprint,
                _SITEMAP_RECOVERY_REMAINING: topology_size,
            }
            if sitemap.kind == "sitemapindex":
                next_retry_cursor.update(
                    {
                        _SITEMAP_RECOVERY_CHILD_TOPOLOGY: child_topology_fingerprint,
                        _SITEMAP_RECOVERY_CHILD_REMAINING: len(urls),
                    }
                )
        elif recovery_active:
            completed_units = len(selected) if sitemap.kind == "urlset" else 0
            if sitemap.kind == "sitemapindex":
                child_recovery_remaining = max(0, child_recovery_remaining - len(selected))
                if child_recovery_remaining == 0:
                    completed_units = selected_child_count
                    # A child clean sweep may finish after wrapping from its
                    # suffix back to its prefix. Advance to the next child at
                    # that proof boundary, even though this individual window
                    # did not reach the physical end of the child URL list.
                    if sitemap.entries and selected_child_count:
                        next_cursor[_SITEMAP_CHILD_OFFSET] = (
                            child_offset + selected_child_count
                        ) % len(sitemap.entries)
                        next_cursor[_SITEMAP_ENTRY_OFFSET] = 0
                        next_cursor.pop(_SITEMAP_CHILD_TOPOLOGY, None)
                elif reached_page_end:
                    # Reaching the physical end of a partially recovered child
                    # is not permission to advance the root cursor. Wrap this
                    # same child until every URL in its clean-sweep proof has
                    # succeeded.
                    next_cursor[_SITEMAP_CHILD_OFFSET] = child_offset
                    next_cursor[_SITEMAP_ENTRY_OFFSET] = 0
                    next_cursor.pop(_SITEMAP_CHILD_TOPOLOGY, None)
            recovery_remaining = max(0, recovery_remaining - completed_units)
            if recovery_remaining:
                effective_error = "sitemap_recovery_pending"
                next_retry_cursor = {
                    **next_cursor,
                    _SITEMAP_RECOVERY_ACTIVE: True,
                    _SITEMAP_RECOVERY_TOPOLOGY: topology_fingerprint,
                    _SITEMAP_RECOVERY_REMAINING: recovery_remaining,
                }
                if sitemap.kind == "sitemapindex" and child_recovery_remaining:
                    next_retry_cursor.update(
                        {
                            _SITEMAP_RECOVERY_CHILD_TOPOLOGY: child_topology_fingerprint,
                            _SITEMAP_RECOVERY_CHILD_REMAINING: child_recovery_remaining,
                        }
                    )
        return _artifact_result(
            artifact,
            status="degraded" if effective_error else "ok" if items else "empty",
            items=items,
            scope_complete=not effective_error,
            error_code=effective_error,
            cursor=next_cursor,
            retry_cursor=next_retry_cursor,
        )

    async def _fetch_browser_legacy(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        """Preserve the explicitly requested browser + LLM experiment path."""
        url = self._build_url(recipe)
        if not url:
            logger.warning("WebSourceAdapter: no URL for recipe %s", recipe.id)
            return []

        browser = BrowserManager(
            executable=self._browser_executable,
            headed=self._browser_headed,
            cdp_url=self._browser_cdp_url,
        )

        if not browser.is_available:
            logger.warning(
                "WebSourceAdapter: agent-browser not available, skipping recipe %s",
                recipe.id,
            )
            return []

        try:
            snapshot = await browser.get_page_snapshot(url)
        except Exception:
            logger.exception("WebSourceAdapter: failed to fetch %s", url)
            return []
        finally:
            with suppress(Exception):
                await browser.close()

        items = await extract_content_from_page(
            snapshot.text,
            source_platform=recipe.source_type,
            llm_service=self._llm_service,
            base_url=url,
        )

        # Apply recipe source_type and URL/ID backfill from captured anchors.
        for item in items:
            if not item.source_platform:
                item.source_platform = recipe.source_type
            if not item.content_url:
                matched = _match_anchor_by_title(snapshot.anchors, item.title)
                if matched:
                    item.content_url = matched
            if item.content_url and (not item.content_id or item.content_id == item.title[:32]):
                derived = _extract_content_id(item.content_url)
                if derived:
                    item.content_id = derived

        return items[:limit]

    @staticmethod
    def _build_url(recipe: SourceRecipe) -> str:
        """Build the target URL from recipe config."""
        config = recipe.config or {}
        url_template = str(config.get("url_template", "") or "")
        query = str(config.get("query", "") or "")
        url = str(config.get("url", "") or "")

        if url_template and query:
            return url_template.replace("{query}", query)
        if url:
            return url
        if url_template:
            return url_template
        return ""


def _artifact_result(
    artifact: Any,
    *,
    status: SourceFetchStatus,
    items: list[DiscoveredContent],
    scope_complete: bool = True,
    error_code: str = "",
    cursor: dict[str, Any] | None = None,
    retry_cursor: dict[str, Any] | None = None,
) -> SourceFetchResult:
    return SourceFetchResult(
        items=items,
        status=status,
        scope_complete=scope_complete,
        etag=str(artifact.etag or ""),
        last_modified=str(artifact.last_modified or ""),
        validator_url=str(artifact.final_url or ""),
        cursor=dict(cursor or {}),
        retry_cursor=dict(retry_cursor or {}),
        error_code=error_code,
    )


def _fetch_error_result(exc: WebFetchError) -> SourceFetchResult:
    status: SourceFetchStatus
    if exc.code == "rate_limited":
        status = "rate_limited"
    elif exc.code == "access_denied":
        status = "login_required"
    else:
        status = "failed"
    return SourceFetchResult(
        status=status,
        scope_complete=False,
        error_code=exc.code,
        retry_after_seconds=max(0, int(exc.retry_after_seconds or 0)),
        detail=str(exc),
    )


def _merge_web_detail(
    item: DiscoveredContent,
    detail: DiscoveredContent,
    *,
    source_strategy: str,
) -> None:
    """Enrich a listing row while keeping its canonical clickable identity."""
    item.content_id = detail.content_id or item.content_id
    item.content_url = detail.content_url or item.content_url
    item.title = detail.title or item.title
    item.description = detail.description or item.description
    item.body_text = detail.body_text or item.body_text
    item.author_name = detail.author_name or item.author_name
    item.up_name = detail.up_name or item.up_name
    item.published_at = detail.published_at or item.published_at
    item.published_label = detail.published_label or item.published_label
    item.content_type = "article"
    item.source_platform = "web"
    item.source_strategy = source_strategy


def _match_anchor_by_title(
    anchors: list[tuple[str, str]],
    title: str,
) -> str:
    """Return the href of the anchor whose text best matches ``title``.

    Matching is deliberately simple: case-insensitive substring either way
    (anchor text contains the title, or the title contains the anchor
    text). For cards on xiaohongshu / v2ex / zhihu this is sufficient —
    the anchor's visible text IS the card title.
    """
    if not title or not anchors:
        return ""
    needle = title.strip().lower()
    if not needle:
        return ""
    # Prefer exact-substring hits first, then partial overlap, so a card
    # whose full title is a prefix of a longer anchor still wins.
    best_exact = ""
    best_partial = ""
    for text, href in anchors:
        candidate = text.strip().lower()
        if not candidate:
            continue
        if (candidate == needle or needle in candidate) and not best_exact:
            best_exact = href
        elif candidate in needle and not best_partial:
            best_partial = href
    return best_exact or best_partial


def _extract_content_id(url: str) -> str:
    """Pull the last non-empty path segment out of ``url``.

    Works for xiaohongshu (``/explore/{note_id}``, ``/discovery/item/{id}``),
    v2ex (``/t/{topic_id}``), zhihu (``/question/{id}``), etc. Returns ""
    when no usable segment is found — callers should keep the original ID.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        path = urlparse(url).path.strip("/")
    except Exception:
        return ""
    if not path:
        return ""
    last = path.rsplit("/", 1)[-1]
    return last
