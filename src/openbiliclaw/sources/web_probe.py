"""Read-only authoring probe for typed public-Web discovery recipes.

The probe deliberately shares the exact-origin, anonymous transport and
deterministic parsers used by formal Web discovery.  It never persists a
recipe: returned recipe payloads still pass through ``build_web_recipe`` when
the user explicitly saves one via ``POST /api/sources``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias
from urllib.parse import unquote, urljoin, urlsplit

from openbiliclaw.sources.platforms import infer_source_platform_from_url
from openbiliclaw.sources.web_public import (
    WEB_DEFAULT_MAX_BYTES,
    HTMLListingPlan,
    SafeWebTransport,
    WebFetchArtifact,
    WebFetchError,
    WebParseError,
    WebSitemap,
    classify_html_access_wall,
    discover_feed_urls,
    infer_html_listing_plan,
    parse_html_detail,
    parse_sitemap,
    parse_web_feed_page,
)
from openbiliclaw.sources.web_recipe import build_web_recipe

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

WebProbeStatus = Literal["ready", "no_match"]
WebProbeStrategy = Literal["feed", "sitemap", "listing"]
WebProbeDiscoveryMethod = Literal["direct", "html_alternate", "well_known", "html_structure"]
WebProbeConfidence = Literal["high", "medium"]
WebProbeTransportFactory: TypeAlias = Callable[..., SafeWebTransport]

WEB_PROBE_MAX_CANDIDATES = 4
WEB_PROBE_MAX_ALTERNATE_FEEDS = 2
WEB_PROBE_MAX_FETCHES = 4
WEB_PROBE_MAX_SAMPLES = 3


@dataclass(frozen=True, slots=True)
class WebProbeSampleItem:
    """Small, normalized preview row returned by URL probing."""

    title: str
    content_url: str
    author_name: str = ""
    published_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WebProbeCandidate:
    """One compiler-ready recipe suggestion backed by observed evidence."""

    strategy: WebProbeStrategy
    entry_url: str
    discovery_method: WebProbeDiscoveryMethod
    confidence: WebProbeConfidence
    detail: str
    sample_items: tuple[WebProbeSampleItem, ...]
    recipe: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "entry_url": self.entry_url,
            "discovery_method": self.discovery_method,
            "confidence": self.confidence,
            "detail": self.detail,
            "sample_items": [item.to_dict() for item in self.sample_items],
            "recipe": {
                "name": str(self.recipe["name"]),
                "strategy": str(self.recipe["strategy"]),
                "config": dict(self.recipe["config"]),
                "target_share": int(self.recipe["target_share"]),
                "enabled": bool(self.recipe["enabled"]),
            },
        }


@dataclass(frozen=True, slots=True)
class WebProbeResult:
    """Stable response for the read-only public-Web authoring probe."""

    status: WebProbeStatus
    detail: str
    requested_url: str
    final_url: str
    page_title: str
    candidates: tuple[WebProbeCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "page_title": self.page_title,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(slots=True)
class _FetchBudget:
    remaining: int = WEB_PROBE_MAX_FETCHES
    deadline: float = 0.0

    async def fetch(
        self,
        transport: SafeWebTransport,
        url: str,
        *,
        expected: Literal["auto", "feed", "sitemap", "html"],
    ) -> WebFetchArtifact:
        if self.remaining <= 0:
            raise WebFetchError("probe_budget_exhausted", "Web probe request budget was exhausted")
        self.remaining -= 1
        remaining_seconds = self.deadline - asyncio.get_running_loop().time()
        if remaining_seconds <= 0:
            raise WebFetchError("timeout", "The public Web probe exceeded its total deadline")
        try:
            async with asyncio.timeout(remaining_seconds):
                return await transport.fetch(url, expected=expected)
        except TimeoutError as exc:
            raise WebFetchError(
                "timeout", "The public Web probe exceeded its total deadline"
            ) from exc


def _validated_target(
    url: str, *, allow_dedicated_feed: bool = False
) -> tuple[str, tuple[str, ...]]:
    compiled = build_web_recipe(
        {
            "name": "Web URL probe",
            "strategy": "feed",
            "config": {"entry_url": url},
        },
        allow_dedicated_feed=allow_dedicated_feed,
    )
    config = compiled["config"]
    return str(config["entry_url"]), tuple(str(value) for value in config["allowed_origins"])


def _page_name(page_title: str, url: str, strategy: WebProbeStrategy) -> str:
    host = str(urlsplit(url).hostname or "Public Web")
    base = " ".join((page_title or host).split())[:96].strip() or "Public Web"
    suffix = {"feed": "Feed", "sitemap": "Sitemap", "listing": "Listing"}[strategy]
    return f"{base} · {suffix}"[:120]


def _listing_config(plan: HTMLListingPlan) -> dict[str, str]:
    return {
        key: value
        for key, value in asdict(plan).items()
        if isinstance(value, str) and value.strip()
    }


def _recipe_payload(
    *,
    strategy: WebProbeStrategy,
    entry_url: str,
    page_title: str,
    listing_plan: HTMLListingPlan | None = None,
    content_origins: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"entry_url": entry_url}
    if content_origins is not None:
        config["content_origins"] = list(content_origins)
    if listing_plan is not None:
        config["listing"] = _listing_config(listing_plan)
    payload: dict[str, Any] = {
        "name": _page_name(page_title, entry_url, strategy),
        "strategy": strategy,
        "config": config,
        "target_share": 4,
        "enabled": True,
    }
    # Keep suggestions honest: any probe/code drift that produces a recipe the
    # save compiler rejects must fail locally instead of reaching the client.
    build_web_recipe(payload, allow_dedicated_feed=strategy == "feed")
    return payload


def _sample_item(item: DiscoveredContent) -> WebProbeSampleItem:
    return WebProbeSampleItem(
        title=str(item.title or "")[:300],
        content_url=str(item.content_url or ""),
        author_name=str(item.author_name or item.up_name or "")[:160],
        published_at=str(item.published_at or ""),
    )


def _url_title(url: str) -> str:
    path = unquote(urlsplit(url).path).rstrip("/")
    leaf = path.rsplit("/", 1)[-1] if path else str(urlsplit(url).hostname or "")
    return " ".join(leaf.replace("-", " ").replace("_", " ").split())[:300]


def _sitemap_samples(sitemap: WebSitemap) -> tuple[WebProbeSampleItem, ...]:
    if sitemap.kind != "urlset":
        return ()
    return tuple(
        WebProbeSampleItem(title=_url_title(entry.url), content_url=entry.url)
        for entry in sitemap.entries[:WEB_PROBE_MAX_SAMPLES]
    )


def _feed_candidate(
    *,
    artifact: WebFetchArtifact,
    allowed_origins: tuple[str, ...],
    method: Literal["direct", "html_alternate"],
    page_title: str,
    observe_content_origins: bool = False,
) -> WebProbeCandidate | None:
    try:
        page = parse_web_feed_page(
            artifact.body,
            content_type=artifact.content_type,
            base_url=artifact.final_url,
            allowed_origins=allowed_origins,
            observe_content_origins=observe_content_origins,
            limit=WEB_PROBE_MAX_SAMPLES,
        )
    except WebParseError:
        return None
    if not page.items and page.unapproved_content_origins and not observe_content_origins:
        return None
    return WebProbeCandidate(
        strategy="feed",
        entry_url=artifact.final_url,
        discovery_method=method,
        confidence="high",
        detail=(
            "The submitted URL is a supported RSS, Atom, or JSON Feed."
            if method == "direct"
            else "The page advertises a supported RSS, Atom, or JSON Feed."
        ),
        sample_items=tuple(_sample_item(item) for item in page.items[:WEB_PROBE_MAX_SAMPLES]),
        recipe=_recipe_payload(
            strategy="feed",
            entry_url=artifact.final_url,
            page_title=page_title,
            content_origins=page.content_origins if observe_content_origins else None,
        ),
    )


def _sitemap_candidate(
    *,
    artifact: WebFetchArtifact,
    allowed_origins: tuple[str, ...],
    method: Literal["direct", "well_known"],
    page_title: str,
) -> WebProbeCandidate | None:
    try:
        sitemap = parse_sitemap(
            artifact.body,
            base_url=artifact.final_url,
            allowed_origins=allowed_origins,
            limit=WEB_PROBE_MAX_SAMPLES,
        )
    except WebParseError:
        return None
    return WebProbeCandidate(
        strategy="sitemap",
        entry_url=artifact.final_url,
        discovery_method=method,
        confidence="high",
        detail=(
            "The submitted URL is a supported sitemap."
            if method == "direct"
            else "A supported same-origin /sitemap.xml was found."
        ),
        sample_items=_sitemap_samples(sitemap),
        recipe=_recipe_payload(
            strategy="sitemap",
            entry_url=artifact.final_url,
            page_title=page_title,
        ),
    )


def _is_html(artifact: WebFetchArtifact) -> bool:
    media_type = artifact.content_type.split(";", 1)[0].strip().casefold()
    prefix = artifact.body.lstrip()[:256].lower()
    return media_type in {"text/html", "application/xhtml+xml"} or prefix.startswith(
        (b"<!doctype html", b"<html", b"<head", b"<body")
    )


def _safe_page_title(
    artifact: WebFetchArtifact,
    allowed_origins: tuple[str, ...],
) -> str:
    try:
        item = parse_html_detail(
            artifact.body,
            page_url=artifact.final_url,
            allowed_origins=allowed_origins,
        )
    except (WebFetchError, WebParseError):
        return ""
    return str(item.title if item is not None else "")[:300]


def _fetch_failure_detail(exc: WebFetchError) -> str:
    messages = {
        "robots_denied": "The site's robots policy does not allow this public probe.",
        "robots_unavailable": (
            "The site's robots.txt could not be read, so the public probe could not proceed."
        ),
        "rate_limited": "The site rate-limited this public probe; try again later.",
        "access_denied": "The site denied anonymous access to this URL.",
        "timeout": "The public URL probe timed out.",
    }
    return messages.get(
        exc.code,
        "The URL could not be inspected within the public-Web safety policy.",
    )


async def probe_public_web_url(
    url: str,
    *,
    timeout_seconds: float = 15,
    max_response_bytes: int = WEB_DEFAULT_MAX_BYTES,
    max_redirects: int = 5,
    transport_factory: WebProbeTransportFactory = SafeWebTransport,
    allow_dedicated_feed: bool = False,
    observe_feed_scopes: bool = False,
) -> WebProbeResult:
    """Suggest bounded public-Web recipes for one user-approved URL.

    The function performs no database writes, carries no credentials, permits
    at most two explicitly advertised feed origins in addition to ``url``,
    and uses at most four target fetches. Each origin has a bounded robots
    check and every request/redirect still passes public DNS/socket validation.
    """

    requested_url, allowed_origins = _validated_target(
        url, allow_dedicated_feed=allow_dedicated_feed
    )
    total_timeout_seconds = max(0.1, float(timeout_seconds))
    budget = _FetchBudget(
        deadline=asyncio.get_running_loop().time() + total_timeout_seconds,
    )
    try:
        async with transport_factory(
            allowed_origins=allowed_origins,
            timeout_seconds=total_timeout_seconds,
            default_max_bytes=max_response_bytes,
            max_redirects=max_redirects,
        ) as transport:
            try:
                artifact = await budget.fetch(transport, requested_url, expected="auto")
            except WebFetchError as exc:
                return WebProbeResult(
                    status="no_match",
                    detail=_fetch_failure_detail(exc),
                    requested_url=requested_url,
                    final_url=requested_url,
                    page_title="",
                    candidates=(),
                )

            direct_feed = _feed_candidate(
                artifact=artifact,
                allowed_origins=allowed_origins,
                method="direct",
                page_title="",
                observe_content_origins=observe_feed_scopes,
            )
            if direct_feed is not None:
                return WebProbeResult(
                    status="ready",
                    detail="Found one supported public feed.",
                    requested_url=requested_url,
                    final_url=artifact.final_url,
                    page_title="",
                    candidates=(direct_feed,),
                )
            if infer_source_platform_from_url(requested_url):
                return WebProbeResult(
                    status="no_match",
                    detail="This platform URL must be an explicit supported public feed.",
                    requested_url=requested_url,
                    final_url=artifact.final_url,
                    page_title="",
                    candidates=(),
                )
            direct_sitemap = _sitemap_candidate(
                artifact=artifact,
                allowed_origins=allowed_origins,
                method="direct",
                page_title="",
            )
            if direct_sitemap is not None:
                return WebProbeResult(
                    status="ready",
                    detail="Found one supported public sitemap.",
                    requested_url=requested_url,
                    final_url=artifact.final_url,
                    page_title="",
                    candidates=(direct_sitemap,),
                )
            if not _is_html(artifact):
                return WebProbeResult(
                    status="no_match",
                    detail="No supported feed, sitemap, or static listing was detected.",
                    requested_url=requested_url,
                    final_url=artifact.final_url,
                    page_title="",
                    candidates=(),
                )

            try:
                access_wall = classify_html_access_wall(artifact.body)
            except WebParseError:
                access_wall = "challenge_page"
            if access_wall:
                return WebProbeResult(
                    status="no_match",
                    detail=(
                        "The page requires login and cannot be probed anonymously."
                        if access_wall == "login_required"
                        else "The page returned an access challenge instead of public content."
                    ),
                    requested_url=requested_url,
                    final_url=artifact.final_url,
                    page_title="",
                    candidates=(),
                )

            page_title = _safe_page_title(artifact, allowed_origins)
            candidates: list[WebProbeCandidate] = []

            def append_candidate(candidate: WebProbeCandidate) -> None:
                identity = (candidate.strategy, candidate.entry_url)
                if any(
                    (existing.strategy, existing.entry_url) == identity for existing in candidates
                ):
                    return
                candidates.append(candidate)

            try:
                alternate_urls = discover_feed_urls(
                    artifact.body,
                    page_url=artifact.final_url,
                    allowed_origins=allowed_origins,
                    limit=WEB_PROBE_MAX_ALTERNATE_FEEDS,
                    allow_cross_origin=observe_feed_scopes,
                )
            except WebParseError:
                alternate_urls = []
            for alternate_url in alternate_urls:
                try:
                    feed_url, feed_origins = _validated_target(
                        alternate_url, allow_dedicated_feed=allow_dedicated_feed
                    )
                    if feed_origins == allowed_origins:
                        alternate_artifact = await budget.fetch(
                            transport, feed_url, expected="feed"
                        )
                    else:
                        async with transport_factory(
                            allowed_origins=feed_origins,
                            timeout_seconds=total_timeout_seconds,
                            default_max_bytes=max_response_bytes,
                            max_redirects=max_redirects,
                        ) as feed_transport:
                            alternate_artifact = await budget.fetch(
                                feed_transport, feed_url, expected="feed"
                            )
                except (WebFetchError, ValueError):
                    continue
                candidate = _feed_candidate(
                    artifact=alternate_artifact,
                    allowed_origins=feed_origins,
                    method="html_alternate",
                    page_title=page_title,
                    observe_content_origins=observe_feed_scopes,
                )
                if candidate is not None:
                    append_candidate(candidate)

            well_known_url = urljoin(f"{allowed_origins[0]}/", "sitemap.xml")
            try:
                sitemap_artifact = await budget.fetch(
                    transport,
                    well_known_url,
                    expected="sitemap",
                )
            except WebFetchError:
                pass
            else:
                candidate = _sitemap_candidate(
                    artifact=sitemap_artifact,
                    allowed_origins=allowed_origins,
                    method="well_known",
                    page_title=page_title,
                )
                if candidate is not None:
                    append_candidate(candidate)

            try:
                inferred = infer_html_listing_plan(
                    artifact.body,
                    base_url=artifact.final_url,
                    allowed_origins=allowed_origins,
                    minimum_items=2,
                    sample_limit=WEB_PROBE_MAX_SAMPLES,
                )
            except WebParseError:
                inferred = None
            if inferred is not None:
                plan, items = inferred
                append_candidate(
                    WebProbeCandidate(
                        strategy="listing",
                        entry_url=artifact.final_url,
                        discovery_method="html_structure",
                        confidence="medium",
                        detail=(
                            "Repeated static HTML structure produced at least two "
                            "distinct same-origin items."
                        ),
                        sample_items=tuple(
                            _sample_item(item) for item in items[:WEB_PROBE_MAX_SAMPLES]
                        ),
                        recipe=_recipe_payload(
                            strategy="listing",
                            entry_url=artifact.final_url,
                            page_title=page_title,
                            listing_plan=plan,
                        ),
                    )
                )

            bounded_candidates = tuple(candidates[:WEB_PROBE_MAX_CANDIDATES])
            return WebProbeResult(
                status="ready" if bounded_candidates else "no_match",
                detail=(
                    f"Found {len(bounded_candidates)} supported public discovery option(s)."
                    if bounded_candidates
                    else "No supported feed, sitemap, or static listing was detected."
                ),
                requested_url=requested_url,
                final_url=artifact.final_url,
                page_title=page_title,
                candidates=bounded_candidates,
            )
    except WebFetchError as exc:
        return WebProbeResult(
            status="no_match",
            detail=_fetch_failure_detail(exc),
            requested_url=requested_url,
            final_url=requested_url,
            page_title="",
            candidates=(),
        )
