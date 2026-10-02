"""Safe, deterministic primitives for anonymous public-Web discovery.

This module is deliberately separate from :mod:`web_adapter`, whose browser
and LLM extraction path has a different trust model.  Public-Web recipes use
exact origins, anonymous ``GET`` requests, bounded response bodies, and
deterministic RSS/Atom/JSON Feed, sitemap, and HTML metadata extraction.

DNS validation is performed before every request and redirect.  The default
socket backend connects to the validated literal address while retaining the
original hostname for HTTP Host and TLS SNI, so policy validation and socket
resolution cannot diverge.  Injected test transports remain responsible for
their own connection behavior.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import math
import re
import socket
from bisect import bisect_left
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Literal, TypeAlias
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpcore
import httpx

from openbiliclaw import __version__
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.published_time import normalize_published_time

WEB_PROJECT_URL = "https://github.com/whiteguo233/OpenBiliClaw"
WEB_ROBOTS_PRODUCT_TOKEN = "OpenBiliClaw"
WEB_USER_AGENT = f"{WEB_ROBOTS_PRODUCT_TOKEN}/{__version__} (+{WEB_PROJECT_URL})"
WEB_DEFAULT_MAX_BYTES = 2_000_000
WEB_ABSOLUTE_MAX_BYTES = 5_000_000
WEB_MAX_URL_LENGTH = 8_192
WEB_MAX_REDIRECTS = 3
WEB_MAX_FEED_ENTRIES = 1_000
WEB_MAX_SITEMAP_ENTRIES = 50_000
WEB_MAX_HTML_NODES = 25_000
WEB_MAX_HTML_DEPTH = 256
WEB_MAX_LISTING_SCOPES = 512
# Selector lookup is indexed once per listing.  Text extraction can still
# overlap when item scopes are nested, so charge that separate workload and
# fail closed before an adversarial tree turns bounded input into quadratic
# CPU work.
WEB_MAX_LISTING_TEXT_NODE_VISITS = 200_000
WEB_MAX_LISTING_TEXT_CHAR_VISITS = 16_000_000
WEB_MAX_LISTING_INFERENCE_PLANS = 8
WEB_MAX_LISTING_INFERENCE_SIGNATURES = 2_048
WEB_MAX_LISTING_INFERENCE_NODES = 4_096
WEB_ROBOTS_MAX_BYTES = 512_000
WEB_MAX_ROBOTS_LINES = 10_000
WEB_MAX_ROBOTS_RULES = 2_048
WEB_MAX_ROBOTS_RULE_OCTETS = 256_000
WEB_MAX_ROBOTS_WILDCARDS = 4_096
WEB_MAX_ROBOTS_WILDCARDS_PER_RULE = 64
WEB_MAX_DNS_ADDRESSES = 16

_XML_CONTENT_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    }
)
_JSON_CONTENT_TYPES = frozenset({"application/feed+json", "application/json", "text/json"})
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream", "text/plain"})
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HIDDEN_TEXT_TAGS = frozenset({"script", "style", "template", "noscript"})
_BLOCK_TEXT_TAGS = frozenset(
    {"article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p"}
)
_SIMPLE_SELECTOR_RE = re.compile(
    r"^(?P<tag>[A-Za-z][A-Za-z0-9_-]*|\*)?"
    r"(?P<suffix>(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*)$"
)
_SELECTOR_SUFFIX_RE = re.compile(r"([.#])([A-Za-z_][A-Za-z0-9_-]*)")
_SITEMAP_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROBOTS_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

WebExpectedContent = Literal["auto", "feed", "sitemap", "html"]
WebSitemapKind = Literal["urlset", "sitemapindex"]
WebHTMLAccessWall = Literal["login_required", "challenge_page", ""]
DNSResolver: TypeAlias = Callable[[str, int], Awaitable[Sequence[str]]]


class WebFetchError(RuntimeError):
    """Stable, body-free error returned by the safe public-Web transport."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message[:240])
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class WebParseError(RuntimeError):
    """Stable error for a malformed, unsafe, or changed discovery envelope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:240])
        self.code = code


@dataclass(frozen=True, slots=True)
class WebFetchArtifact:
    """Bounded response artifact suitable for deterministic extraction."""

    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False
    redirect_chain: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Decode the bounded body as UTF-8, replacing malformed fragments."""

        return self.body.decode("utf-8-sig", errors="replace")


@dataclass(frozen=True, slots=True)
class WebSitemapEntry:
    """A URL reference from a sitemap.

    ``last_modified`` is scheduling metadata only.  It is intentionally not a
    publication timestamp and is never copied into a discovered content item.
    """

    url: str
    last_modified: str = ""


@dataclass(frozen=True, slots=True)
class WebSitemap:
    """A bounded sitemap URL set or child-sitemap index."""

    kind: WebSitemapKind
    entries: tuple[WebSitemapEntry, ...]


@dataclass(frozen=True, slots=True)
class HTMLListingPlan:
    """Restricted deterministic selectors for one static HTML listing."""

    item_selector: str = ""
    link_selector: str = "a"
    title_selector: str = ""
    summary_selector: str = ""
    author_selector: str = ""
    published_selector: str = ""
    published_attribute: str = ""
    empty_selector: str = ""


@dataclass(frozen=True, slots=True)
class _RobotsRule:
    allow: bool
    segments: tuple[str, ...]
    leading_wildcard: bool
    trailing_wildcard: bool
    anchored: bool
    specificity: int
    wildcard_count: int


@dataclass(frozen=True, slots=True)
class _RobotsGroup:
    agents: tuple[str, ...]
    rules: tuple[_RobotsRule, ...]
    request_delay_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class _RobotsPolicy:
    """Bounded RFC 9309-style allow/disallow policy for one origin."""

    groups: tuple[_RobotsGroup, ...]

    def _selected_groups(self, product_token: str) -> list[_RobotsGroup]:
        token = product_token.casefold()
        specific: list[tuple[int, _RobotsGroup]] = []
        wildcard: list[_RobotsGroup] = []
        for group in self.groups:
            agent_matches = [
                len(agent) for agent in group.agents if agent != "*" and token.startswith(agent)
            ]
            if agent_matches:
                specific.append((max(agent_matches), group))
            elif "*" in group.agents:
                wildcard.append(group)
        if specific:
            best_agent_length = max(length for length, _group in specific)
            return [group for length, group in specific if length == best_agent_length]
        return wildcard

    def can_fetch(self, product_token: str, url: str) -> bool:
        selected = self._selected_groups(product_token)
        if not selected:
            return True
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        target = _normalize_robots_octets(target)
        rule_matches = [
            rule for group in selected for rule in group.rules if _robots_rule_matches(rule, target)
        ]
        if not rule_matches:
            return True
        longest = max(rule.specificity for rule in rule_matches)
        # RFC 9309 resolves equally specific allow/disallow rules in favour of
        # Allow.  This still fails closed for every strictly longer Disallow.
        return any(rule.allow for rule in rule_matches if rule.specificity == longest)

    def request_delay(self, product_token: str) -> float:
        """Return the most conservative supported crawl pacing directive."""

        selected = self._selected_groups(product_token)
        return max((group.request_delay_seconds for group in selected), default=0.0)


def validate_html_listing_plan(plan: HTMLListingPlan) -> HTMLListingPlan:
    """Validate the restricted listing selector DSL without fetching a page."""

    if not plan.item_selector.strip():
        raise WebParseError(
            "invalid_selector",
            "Web listing recipes require an explicit item selector",
        )
    _compile_selector(plan.item_selector)
    _compile_selector(plan.link_selector)
    for raw_selector in (
        plan.title_selector,
        plan.summary_selector,
        plan.author_selector,
        plan.published_selector,
        plan.empty_selector,
    ):
        if raw_selector.strip():
            _compile_selector(raw_selector)
    published_attribute = plan.published_attribute.strip().casefold()
    if published_attribute and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", published_attribute) is None:
        raise WebParseError("invalid_selector", "Web listing attribute is unsupported")
    return plan


@dataclass(frozen=True, slots=True)
class _Origin:
    scheme: str
    host: str
    port: int

    @property
    def text(self) -> str:
        return f"{self.scheme}://{self.host}"


@dataclass(frozen=True, slots=True)
class _RawResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to addresses produced by the already-validated resolver.

    HTTP keeps the original hostname for the Host header and TLS SNI, while
    the TCP socket receives a validated literal address.  This closes the
    resolver/socket TOCTOU gap that a DNS-rebinding response could otherwise
    exploit between policy validation and ``connect(2)``.
    """

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.AnyIOBackend()
        self._pins: dict[tuple[str, int], tuple[str, ...]] = {}

    def pin(self, host: str, port: int, addresses: Sequence[str]) -> None:
        """Replace one origin's connection candidates after validation."""

        self._pins[(host.casefold(), int(port))] = tuple(dict.fromkeys(addresses))

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._pins.get((host.casefold(), int(port)), ())
        if not addresses:
            raise OSError("Web origin has no validated DNS pin")
        last_error: BaseException | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # pragma: no cover - failover depends on network
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise OSError("Unix sockets are forbidden for public Web discovery")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose socket backend consumes validated address pins."""

    def __init__(self, backend: _PinnedNetworkBackend) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            network_backend=backend,
            max_connections=10,
            # A fresh socket must consume the DNS pin validated for this
            # request.  Keeping an origin connection indefinitely would make
            # a later public-address change invisible to the socket layer.
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
        )


def _invalid_url(message: str = "URL is not a supported public Web URL") -> WebFetchError:
    return WebFetchError("invalid_url", message)


def _ascii_hostname(value: str) -> str:
    hostname = value.rstrip(".").casefold()
    if not hostname or len(hostname) > 253 or "%" in hostname:
        raise _invalid_url()
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid_url() from exc
    labels = ascii_hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or re.fullmatch(r"[a-z0-9-]+", label) is None
        for label in labels
    ):
        raise _invalid_url()
    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        return ascii_hostname
    raise _invalid_url()


def _normalize_request_url(raw_url: object) -> tuple[str, _Origin]:
    value = str(raw_url or "").strip()
    if (
        not value
        or len(value) > WEB_MAX_URL_LENGTH
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise _invalid_url()
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.casefold()
        if scheme not in {"http", "https"}:
            raise _invalid_url()
        if parts.username is not None or parts.password is not None:
            raise _invalid_url()
        hostname = _ascii_hostname(str(parts.hostname or ""))
        port = parts.port
    except ValueError as exc:
        raise _invalid_url() from exc
    expected_port = 443 if scheme == "https" else 80
    if port not in {None, expected_port}:
        raise _invalid_url()
    path = parts.path or "/"
    if not path.startswith("/"):
        raise _invalid_url()
    origin = _Origin(scheme, hostname, expected_port)
    normalized = urlunsplit((scheme, hostname, path, parts.query, ""))
    return normalized, origin


def _normalize_recipe_origin(raw_origin: object) -> _Origin:
    value = str(raw_origin or "").strip()
    normalized, origin = _normalize_request_url(value)
    parts = urlsplit(normalized)
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise WebFetchError("invalid_recipe", "Allowed Web origins must not include a path")
    return origin


def _allowed_origin_set(allowed_origins: Collection[str]) -> frozenset[_Origin]:
    origins = frozenset(_normalize_recipe_origin(value) for value in allowed_origins)
    if not origins:
        raise WebFetchError("invalid_recipe", "At least one exact Web origin is required")
    return origins


def _require_allowed_origin(origin: _Origin, allowed: Collection[_Origin]) -> None:
    if origin not in allowed:
        raise WebFetchError("origin_not_allowed", "URL origin is not allowed by this Web recipe")


def canonicalize_web_url(
    raw_url: object,
    *,
    allowed_origins: Collection[str] | None = None,
) -> str:
    """Return a stable, clickable public URL for identity and deduplication.

    The canonical form strips fragments and known analytics query parameters,
    sorts remaining query pairs, normalizes an IDNA host, and rejects userinfo,
    IP literals, non-HTTP schemes, and non-standard ports.  If supplied, the
    recipe's exact origin allowlist is enforced.
    """

    normalized, origin = _normalize_request_url(raw_url)
    if allowed_origins is not None:
        _require_allowed_origin(origin, _allowed_origin_set(allowed_origins))
    parts = urlsplit(normalized)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def web_content_id(canonical_url: object) -> str:
    """Return a cross-recipe URL-hash identity for a canonical Web item."""

    value = canonicalize_web_url(canonical_url)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"url-{digest}"


async def _system_resolver(host: str, port: int) -> Sequence[str]:
    def resolve() -> list[str]:
        addresses: list[str] = []
        for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            address = str(result[4][0])
            if address not in addresses:
                addresses.append(address)
        return addresses

    return await asyncio.to_thread(resolve)


def _is_public_ipv4(address: ipaddress.IPv4Address) -> bool:
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _embedded_ipv4_addresses(address: ipaddress.IPv6Address) -> tuple[ipaddress.IPv4Address, ...]:
    embedded: list[ipaddress.IPv4Address] = []
    if address.ipv4_mapped is not None:
        embedded.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        embedded.append(address.sixtofour)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    nat64_networks = (
        ipaddress.IPv6Network("64:ff9b::/96"),
        ipaddress.IPv6Network("64:ff9b:1::/48"),
        ipaddress.IPv6Network("::/96"),
    )
    if any(address in network for network in nat64_networks):
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF))
    return tuple(dict.fromkeys(embedded))


def _is_global_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return _is_public_ipv4(address)
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_site_local
        or address.is_unspecified
    ):
        return False
    return all(_is_public_ipv4(item) for item in _embedded_ipv4_addresses(address))


def _normalize_robots_octets(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if index + 2 < len(value) and value[index] == "%":
            pair = value[index + 1 : index + 3]
            if re.fullmatch(r"[0-9A-Fa-f]{2}", pair):
                decoded = chr(int(pair, 16))
                result.append(decoded if decoded in _ROBOTS_UNRESERVED else f"%{pair.upper()}")
                index += 3
                continue
        character = value[index]
        if ord(character) <= 0x7F:
            result.append(character)
        else:
            result.extend(f"%{octet:02X}" for octet in character.encode("utf-8"))
        index += 1
    return "".join(result)


def _robots_octet_length(value: str) -> int:
    return len(re.sub(r"%[0-9A-F]{2}", "x", value).replace("*", ""))


def _robots_rule_matches(rule: _RobotsRule, target: str) -> bool:
    """Match an REP ``*`` glob using bounded, non-backtracking searches."""

    segments = rule.segments
    if not segments:
        # ``$`` is an exact empty target, whereas ``*$`` (or ``*``) lets the
        # wildcard consume the complete target.  Request targets always have
        # at least ``/``, but keeping the matcher exact avoids an allow bypass.
        return rule.leading_wildcard or rule.trailing_wildcard or not target
    position = 0
    first_index = 0
    if not rule.leading_wildcard:
        first = segments[0]
        if not target.startswith(first):
            return False
        position = len(first)
        first_index = 1
    for index, segment in enumerate(segments[first_index:], start=first_index):
        is_last = index == len(segments) - 1
        if is_last and rule.anchored and not rule.trailing_wildcard:
            start = len(target) - len(segment)
            if start < position or not target.endswith(segment):
                return False
            position = len(target)
            continue
        found = target.find(segment, position)
        if found < 0:
            return False
        position = found + len(segment)
    return not rule.anchored or rule.trailing_wildcard or position == len(target)


def _compile_robots_rule(value: str, *, allow: bool) -> _RobotsRule | None:
    pattern = _normalize_robots_octets(value.strip())
    if not pattern:
        return None
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    leading_wildcard = body.startswith("*")
    trailing_wildcard = body.endswith("*")
    wildcard_count = body.count("*")
    segments = tuple(segment for segment in body.split("*") if segment)
    # Wildcards and the terminal marker do not contribute to RFC match
    # specificity; literal octets do.  Python strings are sufficient here
    # because both robots text and request targets are decoded deterministically.
    specificity = _robots_octet_length(body)
    return _RobotsRule(
        allow=allow,
        segments=segments,
        leading_wildcard=leading_wildcard,
        trailing_wildcard=trailing_wildcard,
        anchored=anchored,
        specificity=specificity,
        wildcard_count=wildcard_count,
    )


def _parse_robots_policy(document: bytes) -> _RobotsPolicy:
    """Parse a bounded robots file with longest-match and ``*``/``$`` rules."""

    groups: list[_RobotsGroup] = []
    agents: list[str] = []
    rules: list[_RobotsRule] = []
    request_delay_seconds = 0.0
    total_rule_count = 0
    total_rule_octets = 0
    total_wildcards = 0

    def finish_group() -> None:
        nonlocal agents, rules, request_delay_seconds
        if agents:
            groups.append(
                _RobotsGroup(
                    tuple(dict.fromkeys(agents)),
                    tuple(rules),
                    request_delay_seconds,
                )
            )
        agents = []
        rules = []
        request_delay_seconds = 0.0

    text = document.decode("utf-8-sig", errors="replace")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if line_number > WEB_MAX_ROBOTS_LINES or len(raw_line) > 8_192:
            raise WebFetchError("robots_unavailable", "robots.txt is too complex")
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, raw_value = line.split(":", 1)
        field = field_name.strip().casefold()
        value = raw_value.strip()
        if field == "user-agent":
            if rules:
                finish_group()
            token = value.casefold()
            if token and re.fullmatch(r"\*|[a-z0-9_-]+", token):
                agents.append(token)
            continue
        if field == "crawl-delay" and agents:
            try:
                delay = float(value)
            except ValueError:
                continue
            if math.isfinite(delay) and 0 <= delay <= 60:
                request_delay_seconds = max(request_delay_seconds, delay)
            continue
        if field == "request-rate" and agents:
            match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", value)
            if match:
                requests, seconds = (int(part) for part in match.groups())
                if 0 < requests <= 10_000 and 0 < seconds <= 86_400:
                    request_delay_seconds = max(
                        request_delay_seconds,
                        min(60.0, seconds / requests),
                    )
            continue
        if field not in {"allow", "disallow"} or not agents:
            continue
        rule = _compile_robots_rule(value, allow=field == "allow")
        if rule is not None:
            total_rule_count += 1
            total_rule_octets += rule.specificity
            total_wildcards += rule.wildcard_count
            if (
                total_rule_count > WEB_MAX_ROBOTS_RULES
                or total_rule_octets > WEB_MAX_ROBOTS_RULE_OCTETS
                or total_wildcards > WEB_MAX_ROBOTS_WILDCARDS
                or rule.wildcard_count > WEB_MAX_ROBOTS_WILDCARDS_PER_RULE
            ):
                raise WebFetchError("robots_unavailable", "robots.txt is too complex")
            rules.append(rule)
    finish_group()
    return _RobotsPolicy(tuple(groups))


def _media_type(headers: Mapping[str, str]) -> str:
    return str(headers.get("content-type", "")).split(";", 1)[0].strip().casefold()


def _retry_after_seconds(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return min(86_400, max(0, int(raw)))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    seconds = math.ceil((retry_at - datetime.now(UTC)).total_seconds())
    return min(86_400, max(0, seconds))


def _safe_validator(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) > 1_024 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return ""
    return raw


def _expected_accept(expected: WebExpectedContent) -> str:
    if expected == "feed":
        return (
            "application/atom+xml, application/rss+xml, "
            "application/feed+json, application/xml;q=0.9"
        )
    if expected == "sitemap":
        return "application/xml, text/xml;q=0.9"
    if expected == "html":
        return "text/html, application/xhtml+xml;q=0.9"
    return (
        "application/atom+xml, application/rss+xml, application/feed+json, "
        "application/xml, text/html;q=0.8"
    )


def _sniff_expected(body: bytes, expected: WebExpectedContent) -> bool:
    prefix = body.lstrip()[:256].lower()
    if expected == "feed":
        return prefix.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf", b"{"))
    if expected == "sitemap":
        return prefix.startswith((b"<?xml", b"<urlset", b"<sitemapindex"))
    if expected == "html":
        return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
    return (
        _sniff_expected(body, "feed")
        or _sniff_expected(body, "sitemap")
        or _sniff_expected(body, "html")
    )


def _validate_response_content_type(
    content_type: str, body: bytes, expected: WebExpectedContent
) -> None:
    if expected == "feed":
        accepted = _XML_CONTENT_TYPES | _JSON_CONTENT_TYPES
    elif expected == "sitemap":
        accepted = _XML_CONTENT_TYPES
    elif expected == "html":
        accepted = _HTML_CONTENT_TYPES
    else:
        accepted = _XML_CONTENT_TYPES | _JSON_CONTENT_TYPES | _HTML_CONTENT_TYPES
    if content_type in accepted:
        return
    if content_type in _GENERIC_CONTENT_TYPES and _sniff_expected(body, expected):
        return
    raise WebFetchError(
        "unsupported_content_type",
        "Web source returned an unsupported content type",
    )


class SafeWebTransport:
    """Anonymous bounded HTTP transport for an exact set of public origins."""

    def __init__(
        self,
        *,
        allowed_origins: Collection[str],
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: DNSResolver | None = None,
        timeout_seconds: float = 15.0,
        default_max_bytes: int = WEB_DEFAULT_MAX_BYTES,
        max_redirects: int = WEB_MAX_REDIRECTS,
        respect_robots: bool = True,
    ) -> None:
        self._allowed_origins = _allowed_origin_set(allowed_origins)
        self._resolver = resolver or _system_resolver
        self._default_max_bytes = min(WEB_ABSOLUTE_MAX_BYTES, max(1, int(default_max_bytes)))
        self._max_redirects = min(10, max(0, int(max_redirects)))
        self._respect_robots = bool(respect_robots)
        self._robots_cache: dict[_Origin, _RobotsPolicy | bool] = {}
        self._robots_request_locks: dict[_Origin, asyncio.Lock] = {}
        self._robots_last_request_at: dict[_Origin, float] = {}
        timeout = max(0.1, float(timeout_seconds))
        self._total_timeout_seconds = timeout
        self._pinned_backend = _PinnedNetworkBackend() if transport is None else None
        effective_transport = (
            _PinnedAsyncHTTPTransport(self._pinned_backend)
            if self._pinned_backend is not None
            else transport
        )
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            trust_env=False,
            transport=effective_transport,
        )

    async def __aenter__(self) -> SafeWebTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned HTTP connection pool."""

        await self._http.aclose()

    async def fetch(
        self,
        url: str,
        *,
        expected: WebExpectedContent = "auto",
        etag: str = "",
        last_modified: str = "",
        validator_url: str = "",
        max_bytes: int | None = None,
    ) -> WebFetchArtifact:
        """Fetch one public resource after validating every redirect hop."""

        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await self._fetch_within_deadline(
                    url,
                    expected=expected,
                    etag=etag,
                    last_modified=last_modified,
                    validator_url=validator_url,
                    max_bytes=max_bytes,
                )
        except TimeoutError as exc:
            raise WebFetchError(
                "timeout", "Web source request exceeded its total deadline"
            ) from exc

    async def _fetch_within_deadline(
        self,
        url: str,
        *,
        expected: WebExpectedContent,
        etag: str,
        last_modified: str,
        validator_url: str,
        max_bytes: int | None,
    ) -> WebFetchArtifact:
        """Perform DNS, robots, redirects and body streaming under one deadline."""

        if expected not in {"auto", "feed", "sitemap", "html"}:
            raise ValueError(f"unsupported expected Web content: {expected}")
        requested_url, requested_origin = _normalize_request_url(url)
        _require_allowed_origin(requested_origin, self._allowed_origins)
        body_limit = min(
            WEB_ABSOLUTE_MAX_BYTES,
            max(1, int(max_bytes if max_bytes is not None else self._default_max_bytes)),
        )
        base_headers = {
            "Accept": _expected_accept(expected),
            "Accept-Encoding": "identity",
            "User-Agent": WEB_USER_AGENT,
        }
        safe_etag = _safe_validator(etag)
        safe_last_modified = _safe_validator(last_modified)
        conditional_url = ""
        if safe_etag or safe_last_modified:
            if validator_url:
                conditional_url, validator_origin = _normalize_request_url(validator_url)
                _require_allowed_origin(validator_origin, self._allowed_origins)
            else:
                conditional_url = requested_url

        current_url = requested_url
        redirect_chain = [current_url]
        seen: set[str] = set()
        for redirect_count in range(self._max_redirects + 1):
            normalized, origin = _normalize_request_url(current_url)
            _require_allowed_origin(origin, self._allowed_origins)
            if normalized in seen:
                raise WebFetchError("redirect_loop", "Web source returned a redirect loop")
            seen.add(normalized)
            await self._validate_dns(origin)
            if self._respect_robots:
                policy = await self._require_robots_permission(normalized, origin)
                await self._pace_robots_request(origin, policy)
            headers = dict(base_headers)
            if normalized == conditional_url:
                if safe_etag:
                    headers["If-None-Match"] = safe_etag
                if safe_last_modified:
                    headers["If-Modified-Since"] = safe_last_modified
            response = await self._bounded_request(
                normalized,
                headers=headers,
                max_bytes=body_limit,
            )
            status = response.status_code
            if status in _REDIRECT_STATUSES:
                location = str(response.headers.get("location", "")).strip()
                if not location:
                    raise WebFetchError(
                        "invalid_redirect",
                        "Web source returned a redirect without a valid location",
                        status_code=status,
                    )
                if redirect_count >= self._max_redirects:
                    raise WebFetchError(
                        "too_many_redirects",
                        "Web source exceeded the safe redirect limit",
                        status_code=status,
                    )
                current_url = urljoin(normalized, location)
                next_url, next_origin = _normalize_request_url(current_url)
                _require_allowed_origin(next_origin, self._allowed_origins)
                current_url = next_url
                redirect_chain.append(current_url)
                continue
            if status == 304:
                if normalized != conditional_url:
                    raise WebFetchError(
                        "unexpected_not_modified",
                        "Web source returned 304 for a resource without matching validators",
                        status_code=status,
                    )
                return WebFetchArtifact(
                    requested_url=requested_url,
                    final_url=normalized,
                    status_code=status,
                    content_type=_media_type(response.headers),
                    body=b"",
                    etag=str(response.headers.get("etag", "") or safe_etag),
                    last_modified=str(
                        response.headers.get("last-modified", "") or safe_last_modified
                    ),
                    not_modified=True,
                    redirect_chain=tuple(redirect_chain),
                )
            self._raise_for_status(response)
            content_type = _media_type(response.headers)
            _validate_response_content_type(content_type, response.body, expected)
            return WebFetchArtifact(
                requested_url=requested_url,
                final_url=normalized,
                status_code=status,
                content_type=content_type,
                body=response.body,
                etag=str(response.headers.get("etag", "")),
                last_modified=str(response.headers.get("last-modified", "")),
                redirect_chain=tuple(redirect_chain),
            )
        raise WebFetchError("too_many_redirects", "Web source exceeded the safe redirect limit")

    async def _require_robots_permission(self, url: str, origin: _Origin) -> _RobotsPolicy | bool:
        policy = self._robots_cache.get(origin)
        if policy is None:
            policy = await self._load_robots_policy(origin)
            self._robots_cache[origin] = policy
        if policy is False:
            raise WebFetchError(
                "robots_denied",
                "Web source robots policy denies anonymous discovery",
            )
        if policy is not True and not policy.can_fetch(WEB_ROBOTS_PRODUCT_TOKEN, url):
            raise WebFetchError(
                "robots_denied",
                "Web source robots policy denies this URL",
            )
        return policy

    async def _pace_robots_request(
        self,
        origin: _Origin,
        policy: _RobotsPolicy | bool,
    ) -> None:
        if isinstance(policy, bool):
            return
        delay = policy.request_delay(WEB_ROBOTS_PRODUCT_TOKEN)
        if delay <= 0:
            return
        lock = self._robots_request_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            remaining = self._robots_last_request_at.get(origin, 0.0) + delay - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._robots_last_request_at[origin] = loop.time()

    async def _load_robots_policy(
        self,
        origin: _Origin,
    ) -> _RobotsPolicy | bool:
        """Load one exact origin's robots policy; network ambiguity fails closed."""

        current_url = f"{origin.text}/robots.txt"
        seen: set[str] = set()
        headers = {
            "Accept": "text/plain, text/*;q=0.8, */*;q=0.1",
            "Accept-Encoding": "identity",
            "User-Agent": WEB_USER_AGENT,
        }
        for redirect_count in range(self._max_redirects + 1):
            try:
                normalized, current_origin = _normalize_request_url(current_url)
                _require_allowed_origin(current_origin, self._allowed_origins)
                if normalized in seen:
                    raise WebFetchError("robots_unavailable", "robots.txt returned a redirect loop")
                seen.add(normalized)
                await self._validate_dns(current_origin)
                response = await self._bounded_request(
                    normalized,
                    headers=headers,
                    max_bytes=WEB_ROBOTS_MAX_BYTES,
                )
            except WebFetchError as exc:
                if exc.code == "robots_unavailable":
                    raise
                raise WebFetchError(
                    "robots_unavailable",
                    "robots.txt could not be fetched safely",
                    status_code=exc.status_code,
                    retry_after_seconds=exc.retry_after_seconds,
                ) from exc
            if response.status_code in _REDIRECT_STATUSES:
                location = str(response.headers.get("location", "")).strip()
                if not location or redirect_count >= self._max_redirects:
                    raise WebFetchError(
                        "robots_unavailable",
                        "robots.txt exceeded the safe redirect policy",
                    )
                current_url = urljoin(normalized, location)
                continue
            if response.status_code in {404, 410}:
                return True
            if response.status_code in {401, 403}:
                return False
            if response.status_code < 200 or response.status_code >= 300:
                raise WebFetchError(
                    "robots_unavailable",
                    "robots.txt returned a temporary upstream failure",
                    status_code=response.status_code,
                    retry_after_seconds=_retry_after_seconds(response.headers.get("retry-after")),
                )
            media_type = _media_type(response.headers)
            if media_type not in {"", "text/plain", "application/octet-stream"}:
                raise WebFetchError(
                    "robots_unavailable",
                    "robots.txt returned an unsupported content type",
                )
            return _parse_robots_policy(response.body)
        raise WebFetchError("robots_unavailable", "robots.txt could not be resolved")

    async def _validate_dns(self, origin: _Origin) -> None:
        try:
            addresses = tuple(
                dict.fromkeys(
                    str(address) for address in await self._resolver(origin.host, origin.port)
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise WebFetchError("dns_error", "Web source hostname could not be resolved") from exc
        if not addresses:
            raise WebFetchError("dns_error", "Web source hostname could not be resolved")
        if len(addresses) > WEB_MAX_DNS_ADDRESSES:
            raise WebFetchError("dns_error", "Web source returned too many DNS addresses")
        if any(not _is_global_address(str(address)) for address in addresses):
            raise WebFetchError(
                "blocked_address",
                "Web source resolved to a non-public network address",
            )
        if self._pinned_backend is not None:
            self._pinned_backend.pin(origin.host, origin.port, addresses)

    async def _bounded_request(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
    ) -> _RawResponse:
        self._http.cookies.clear()
        request = httpx.Request("GET", url, headers=headers)
        streamed: httpx.Response | None = None
        try:
            # Passing an explicit Request to ``send`` avoids merging the
            # client's cookie jar into an arbitrary-origin request.
            streamed = await self._http.send(request, stream=True, follow_redirects=False)
            self._http.cookies.clear()
            status = int(streamed.status_code)
            response_headers = {key.casefold(): value for key, value in streamed.headers.items()}
            if status == 304 or status in _REDIRECT_STATUSES or not 200 <= status < 300:
                return _RawResponse(status, response_headers, b"")
            content_encoding = str(streamed.headers.get("content-encoding", "")).strip().casefold()
            if content_encoding not in {"", "identity"}:
                raise WebFetchError(
                    "unsupported_content_encoding",
                    "Web source returned unsupported response compression",
                )
            declared = str(streamed.headers.get("content-length", "")).strip()
            try:
                declared_length = int(declared) if declared else 0
            except ValueError:
                declared_length = 0
            if declared_length > max_bytes:
                raise WebFetchError(
                    "response_too_large",
                    "Web source response exceeded the safe size limit",
                )
            body = bytearray()
            if streamed.is_stream_consumed:
                loaded = streamed.content
                if len(loaded) > max_bytes:
                    raise WebFetchError(
                        "response_too_large",
                        "Web source response exceeded the safe size limit",
                    )
                body.extend(loaded)
            else:
                async for chunk in streamed.aiter_raw():
                    if len(chunk) > max_bytes - len(body):
                        raise WebFetchError(
                            "response_too_large",
                            "Web source response exceeded the safe size limit",
                        )
                    body.extend(chunk)
            return _RawResponse(status, response_headers, bytes(body))
        except httpx.TimeoutException as exc:
            raise WebFetchError("timeout", "Web source request timed out") from exc
        except httpx.HTTPError as exc:
            raise WebFetchError("network_error", "Web source network request failed") from exc
        finally:
            if streamed is not None:
                await streamed.aclose()
            self._http.cookies.clear()

    @staticmethod
    def _raise_for_status(response: _RawResponse) -> None:
        status = response.status_code
        if status == 429:
            raise WebFetchError(
                "rate_limited",
                "Web source rate limited this client",
                status_code=status,
                retry_after_seconds=_retry_after_seconds(response.headers.get("retry-after")),
            )
        if status in {401, 403}:
            raise WebFetchError(
                "access_denied", "Web source denied anonymous access", status_code=status
            )
        if status == 404:
            raise WebFetchError(
                "not_found", "Web source resource was not found", status_code=status
            )
        if status >= 500:
            raise WebFetchError(
                "upstream_error",
                "Web source is temporarily unavailable",
                status_code=status,
            )
        if not 200 <= status < 300:
            raise WebFetchError(
                "http_error", f"Web source returned HTTP {status}", status_code=status
            )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _HIDDEN_TEXT_TAGS:
            self._hidden_depth += 1
        elif not self._hidden_depth and normalized in _BLOCK_TEXT_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _HIDDEN_TEXT_TAGS and self._hidden_depth:
            self._hidden_depth -= 1
        elif not self._hidden_depth and normalized in _BLOCK_TEXT_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def _plain_text(value: object, *, limit: int = 4_000) -> str:
    raw = html.unescape(str(value or ""))
    if "<" not in raw:
        return " ".join(raw.split())[:limit].strip()
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        text = " ".join("".join(parser.parts).split())
    except (ValueError, RecursionError):
        text = " ".join(re.sub(r"<[^>]+>", " ", raw).split())
    return text[:limit].strip()


def _decode_document(document: bytes | str, *, max_bytes: int = WEB_ABSOLUTE_MAX_BYTES) -> str:
    if isinstance(document, bytes):
        if len(document) > max_bytes:
            raise WebParseError("document_too_large", "Web document exceeded the parser limit")
        return document.decode("utf-8-sig", errors="replace")
    encoded = document.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        raise WebParseError("document_too_large", "Web document exceeded the parser limit")
    return document.lstrip("\ufeff")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()


def _safe_xml_root(
    document: bytes | str, *, expected_roots: Collection[str]
) -> ElementTree.Element:
    raw = document if isinstance(document, bytes) else document.encode("utf-8", errors="strict")
    if len(raw) > WEB_ABSOLUTE_MAX_BYTES:
        raise WebParseError("document_too_large", "Web XML exceeded the parser limit")
    if b"\x00" in raw or raw.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
    ):
        raise WebParseError("unsafe_xml", "Web XML must use UTF-8 without entity declarations")
    try:
        decoded = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise WebParseError("unsafe_xml", "Web XML must use strict UTF-8") from exc
    declaration = decoded[:256].casefold()
    encoding_match = re.search(r"<\?xml[^>]*\bencoding\s*=\s*['\"]([^'\"]+)", declaration)
    if encoding_match is not None and encoding_match.group(1).replace("_", "-") not in {
        "utf-8",
        "utf8",
    }:
        raise WebParseError("unsafe_xml", "Web XML must declare UTF-8 encoding")
    lowered = decoded.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise WebParseError("unsafe_xml", "Web XML contains unsupported declarations")
    try:
        root = ElementTree.fromstring(decoded)
    except (ElementTree.ParseError, ValueError) as exc:
        raise WebParseError("invalid_document", "Web source returned invalid XML") from exc
    if _local_name(str(root.tag)) not in expected_roots:
        raise WebParseError("schema_changed", "Web XML returned an unexpected root element")
    return root


def _element_text(element: ElementTree.Element) -> str:
    return _plain_text(" ".join(element.itertext()))


def _children_named(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local_name(str(child.tag)) == name]


def _first_child_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        for child in _children_named(element, name):
            value = _element_text(child)
            if value:
                return value
    return ""


def _feed_link(entry: ElementTree.Element) -> str:
    links = _children_named(entry, "link")
    scored: list[tuple[int, int, str]] = []
    for position, link in enumerate(links):
        href = str(link.attrib.get("href") or "").strip() or _element_text(link)
        if not href:
            continue
        rel = str(link.attrib.get("rel") or "").strip().casefold()
        media_type = str(link.attrib.get("type") or "").split(";", 1)[0].strip().casefold()
        if rel == "alternate" and media_type in {"", "text/html", "application/xhtml+xml"}:
            score = 3
        elif rel in {"", "alternate"}:
            score = 2
        elif rel == "self":
            score = 0
        else:
            score = 1
        scored.append((score, -position, href))
    if scored:
        return max(scored)[2]
    guid = next(iter(_children_named(entry, "guid")), None)
    if guid is not None and str(guid.attrib.get("isPermaLink") or "true").casefold() != "false":
        return _element_text(guid)
    return _first_child_text(entry, "id")


def _feed_author(entry: ElementTree.Element) -> str:
    for name in ("author", "creator"):
        for element in _children_named(entry, name):
            nested = _first_child_text(element, "name")
            value = nested or _element_text(element)
            if value:
                return value[:160]
    return ""


def _discovered_web_item(
    *,
    content_url: str,
    title: str,
    strategy: str,
    description: str = "",
    author: str = "",
    published_value: object = None,
    published_label: object = None,
) -> DiscoveredContent:
    published = normalize_published_time(published_value, label=published_label)
    clean_description = _plain_text(description)
    clean_author = _plain_text(author, limit=160)
    return DiscoveredContent(
        content_id=web_content_id(content_url),
        content_url=content_url,
        source_platform="web",
        source_strategy=strategy,
        title=_plain_text(title, limit=300),
        author_name=clean_author,
        up_name=clean_author,
        description=clean_description,
        body_text=clean_description,
        published_at=published.published_at,
        published_label=published.published_label,
        content_type="article",
    )


def _parse_xml_feed(
    document: bytes | str,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    limit: int,
) -> list[DiscoveredContent]:
    root = _safe_xml_root(document, expected_roots={"feed", "rdf", "rss"})
    items: list[DiscoveredContent] = []
    seen_urls: set[str] = set()
    source_row_count = 0
    for entry in root.iter():
        if _local_name(str(entry.tag)) not in {"entry", "item"}:
            continue
        source_row_count += 1
        raw_url = _feed_link(entry)
        if not raw_url.strip():
            continue
        try:
            content_url = canonicalize_web_url(
                urljoin(base_url, raw_url), allowed_origins=allowed_origins
            )
        except WebFetchError:
            continue
        title = _first_child_text(entry, "title")
        if not title or content_url in seen_urls:
            continue
        description = _first_child_text(entry, "content", "encoded", "summary", "description")
        published_value = _first_child_text(entry, "published", "pubdate", "updated")
        items.append(
            _discovered_web_item(
                content_url=content_url,
                title=title,
                strategy="web-feed",
                description=description,
                author=_feed_author(entry),
                published_value=published_value,
            )
        )
        seen_urls.add(content_url)
        if len(items) >= limit:
            break
        if len(seen_urls) >= WEB_MAX_FEED_ENTRIES:
            break
    if source_row_count and not items:
        raise WebParseError(
            "schema_changed",
            "Web feed contained entries but none were usable within the recipe scope",
        )
    return items


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_feed_author(item: Mapping[str, Any]) -> str:
    authors = item.get("authors")
    if isinstance(authors, list):
        for author in authors[:10]:
            name = str(_mapping(author).get("name") or "").strip()
            if name:
                return name
    return str(_mapping(item.get("author")).get("name") or "").strip()


def _parse_json_feed(
    document: bytes | str,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    limit: int,
) -> list[DiscoveredContent]:
    text = _decode_document(document)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise WebParseError("invalid_document", "Web source returned invalid JSON Feed") from exc
    if not isinstance(payload, Mapping):
        raise WebParseError("schema_changed", "Web JSON Feed root is not an object")
    version = str(payload.get("version") or "")
    feed_items = payload.get("items")
    if "jsonfeed.org/version/" not in version or not isinstance(feed_items, list):
        raise WebParseError("schema_changed", "Web JSON Feed shape is unsupported")
    items: list[DiscoveredContent] = []
    seen_urls: set[str] = set()
    source_row_count = 0
    for raw_item in feed_items[:WEB_MAX_FEED_ENTRIES]:
        source_row_count += 1
        if not isinstance(raw_item, Mapping):
            raise WebParseError("schema_changed", "Web JSON Feed item is not an object")
        raw_url = str(raw_item.get("url") or raw_item.get("external_url") or "").strip()
        if not raw_url:
            raw_id = str(raw_item.get("id") or "").strip()
            if urlsplit(raw_id).scheme.casefold() in {"http", "https"}:
                raw_url = raw_id
        if not raw_url:
            continue
        try:
            content_url = canonicalize_web_url(
                urljoin(base_url, raw_url), allowed_origins=allowed_origins
            )
        except WebFetchError:
            continue
        title = _plain_text(raw_item.get("title"), limit=300)
        if not title or content_url in seen_urls:
            continue
        description = (
            raw_item.get("content_text")
            or raw_item.get("content_html")
            or raw_item.get("summary")
            or ""
        )
        items.append(
            _discovered_web_item(
                content_url=content_url,
                title=title,
                strategy="web-feed",
                description=str(description),
                author=_json_feed_author(raw_item),
                published_value=raw_item.get("date_published") or raw_item.get("date_modified"),
            )
        )
        seen_urls.add(content_url)
        if len(items) >= limit:
            break
    if source_row_count and not items:
        raise WebParseError(
            "schema_changed",
            "Web JSON Feed contained entries but none were usable within the recipe scope",
        )
    return items


def parse_web_feed(
    document: bytes | str,
    *,
    content_type: str,
    base_url: str,
    allowed_origins: Collection[str],
    limit: int = 20,
) -> list[DiscoveredContent]:
    """Parse a bounded RSS, Atom, or JSON Feed into Web candidates."""

    canonicalize_web_url(base_url, allowed_origins=allowed_origins)
    bounded_limit = min(WEB_MAX_FEED_ENTRIES, max(1, int(limit)))
    media_type = content_type.split(";", 1)[0].strip().casefold()
    raw = document if isinstance(document, bytes) else document.encode("utf-8", errors="replace")
    prefix = raw.lstrip()[:256].lower()
    if media_type in _HTML_CONTENT_TYPES:
        raise WebParseError("unsupported_content_type", "Expected a feed but received HTML")
    if media_type in _JSON_CONTENT_TYPES or (
        media_type in _GENERIC_CONTENT_TYPES and prefix.startswith(b"{")
    ):
        return _parse_json_feed(
            document,
            base_url=base_url,
            allowed_origins=allowed_origins,
            limit=bounded_limit,
        )
    if media_type in _XML_CONTENT_TYPES or (
        media_type in _GENERIC_CONTENT_TYPES
        and prefix.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf"))
    ):
        return _parse_xml_feed(
            document,
            base_url=base_url,
            allowed_origins=allowed_origins,
            limit=bounded_limit,
        )
    raise WebParseError("unsupported_content_type", "Web source is not a supported feed")


def _normalized_sitemap_lastmod(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if _SITEMAP_DATE_RE.fullmatch(raw):
        try:
            date.fromisoformat(raw)
        except ValueError:
            return ""
        return raw
    normalized = normalize_published_time(raw)
    return normalized.published_at


def parse_sitemap(
    document: bytes | str,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    limit: int = 200,
) -> WebSitemap:
    """Parse one bounded sitemap without treating ``lastmod`` as publication."""

    canonicalize_web_url(base_url, allowed_origins=allowed_origins)
    root = _safe_xml_root(document, expected_roots={"urlset", "sitemapindex"})
    root_name = _local_name(str(root.tag))
    kind: WebSitemapKind = "urlset" if root_name == "urlset" else "sitemapindex"
    child_name = "url" if kind == "urlset" else "sitemap"
    bounded_limit = min(WEB_MAX_SITEMAP_ENTRIES, max(1, int(limit)))
    entries: list[WebSitemapEntry] = []
    seen: set[str] = set()
    source_row_count = 0
    for child in root:
        if _local_name(str(child.tag)) != child_name:
            continue
        source_row_count += 1
        raw_url = _first_child_text(child, "loc")
        if not raw_url.strip():
            continue
        try:
            url = canonicalize_web_url(urljoin(base_url, raw_url), allowed_origins=allowed_origins)
        except WebFetchError:
            continue
        if url in seen:
            continue
        entries.append(
            WebSitemapEntry(
                url=url,
                last_modified=_normalized_sitemap_lastmod(_first_child_text(child, "lastmod")),
            )
        )
        seen.add(url)
        if len(entries) >= bounded_limit:
            break
    if source_row_count and not entries:
        raise WebParseError(
            "schema_changed",
            "Web sitemap contained entries but none were usable within the recipe scope",
        )
    return WebSitemap(kind=kind, entries=tuple(entries))


@dataclass(slots=True)
class _HTMLNode:
    tag: str
    attrs: dict[str, str]
    parent: _HTMLNode | None = field(default=None, repr=False)
    children: list[_HTMLNode] = field(default_factory=list, repr=False)
    content: list[str | _HTMLNode] = field(default_factory=list, repr=False)


class _HTMLTreeParser(HTMLParser):
    def __init__(self, *, max_nodes: int = WEB_MAX_HTML_NODES) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("document", {})
        self._stack: list[_HTMLNode] = [self.root]
        self._node_count = 1
        self._max_nodes = min(WEB_MAX_HTML_NODES, max(1, int(max_nodes)))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._add_node(tag, attrs, is_void=tag.casefold() in _VOID_HTML_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._add_node(tag, attrs, is_void=True)

    def _add_node(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        is_void: bool,
    ) -> None:
        if len(self._stack) > WEB_MAX_HTML_DEPTH:
            raise WebParseError("document_too_complex", "Web HTML exceeded the safe nesting depth")
        self._node_count += 1
        if self._node_count > self._max_nodes:
            raise WebParseError("document_too_complex", "Web HTML exceeded the safe node limit")
        normalized_attrs = {
            key.casefold(): str(value or "")[:8_192]
            for key, value in attrs
            if key and len(key) <= 128
        }
        node = _HTMLNode(tag.casefold(), normalized_attrs, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        self._stack[-1].content.append(node)
        if not is_void:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].content.append(data[:100_000])


@dataclass(frozen=True, slots=True)
class _SimpleSelector:
    tag: str
    element_id: str
    classes: frozenset[str]


def _compile_selector(selector: str) -> tuple[_SimpleSelector, ...]:
    raw = selector.strip()
    if not raw or len(raw) > 256:
        raise WebParseError("invalid_selector", "Web listing contains an invalid selector")
    parts = raw.split()
    if len(parts) > 8:
        raise WebParseError("invalid_selector", "Web listing selector is too complex")
    compiled: list[_SimpleSelector] = []
    for part in parts:
        match = _SIMPLE_SELECTOR_RE.fullmatch(part)
        if match is None or not (match.group("tag") or match.group("suffix")):
            raise WebParseError("invalid_selector", "Web listing selector is unsupported")
        element_id = ""
        classes: set[str] = set()
        for prefix, name in _SELECTOR_SUFFIX_RE.findall(match.group("suffix")):
            if prefix == "#":
                if element_id:
                    raise WebParseError("invalid_selector", "Web listing selector is unsupported")
                element_id = name
            else:
                classes.add(name)
        compiled.append(
            _SimpleSelector(
                tag=(match.group("tag") or "*").casefold(),
                element_id=element_id,
                classes=frozenset(classes),
            )
        )
    return tuple(compiled)


def _node_matches(node: _HTMLNode, selector: _SimpleSelector) -> bool:
    if selector.tag != "*" and node.tag != selector.tag:
        return False
    if selector.element_id and node.attrs.get("id") != selector.element_id:
        return False
    node_classes = frozenset(node.attrs.get("class", "").split())
    return selector.classes.issubset(node_classes)


def _walk_html(node: _HTMLNode) -> Sequence[_HTMLNode]:
    output: list[_HTMLNode] = []
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        output.append(current)
        stack.extend(reversed(current.children))
    return output


class _HTMLSelectorIndex:
    """Precompute bounded descendant-selector matches for one HTML tree.

    A listing may contain hundreds of nested item scopes.  Re-walking each
    subtree for every field makes that shape quadratic even though the parser
    caps the total number of nodes.  This index evaluates each selector in
    ``O(nodes * selector_parts)`` and answers scoped first/all queries by
    preorder interval and binary search.
    """

    def __init__(
        self,
        root: _HTMLNode,
        selectors: Collection[tuple[_SimpleSelector, ...]],
    ) -> None:
        self._root = root
        self._nodes: list[_HTMLNode] = []
        self._starts: dict[int, int] = {}
        self._ends: dict[int, int] = {}
        stack: list[tuple[_HTMLNode, bool]] = [(root, False)]
        while stack:
            node, exiting = stack.pop()
            identity = id(node)
            if exiting:
                self._ends[identity] = len(self._nodes)
                continue
            self._starts[identity] = len(self._nodes)
            self._nodes.append(node)
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node.children))

        self._matches: dict[tuple[_SimpleSelector, ...], tuple[int, ...]] = {}
        for selector in dict.fromkeys(selectors):
            self._matches[selector] = self._match_positions(selector)

    def _match_positions(self, selector: tuple[_SimpleSelector, ...]) -> tuple[int, ...]:
        # prefix_counts[k] is the number of strict ancestors that complete
        # selector[0:k+1].  Delaying increments until all parts are evaluated
        # prevents one element from satisfying two descendant components.
        prefix_counts = [0] * len(selector)
        positions: list[int] = []
        stack: list[tuple[_HTMLNode, tuple[int, ...] | None]] = [(self._root, None)]
        while stack:
            node, matched_parts = stack.pop()
            if matched_parts is not None:
                for index in matched_parts:
                    prefix_counts[index] -= 1
                continue

            matches_here = tuple(
                index
                for index, part in enumerate(selector)
                if _node_matches(node, part) and (index == 0 or prefix_counts[index - 1] > 0)
            )
            if len(selector) - 1 in matches_here:
                positions.append(self._starts[id(node)])
            for index in matches_here:
                prefix_counts[index] += 1
            stack.append((node, matches_here))
            stack.extend((child, None) for child in reversed(node.children))
        return tuple(positions)

    def all(
        self,
        scope: _HTMLNode,
        selector: tuple[_SimpleSelector, ...],
        *,
        include_scope: bool = False,
    ) -> list[_HTMLNode]:
        """Return selector matches in document order inside ``scope``."""

        positions = self._matches[selector]
        start = self._starts[id(scope)] + (0 if include_scope else 1)
        end = self._ends[id(scope)]
        first = bisect_left(positions, start)
        after = bisect_left(positions, end, lo=first)
        return [self._nodes[position] for position in positions[first:after]]

    def first(
        self,
        scope: _HTMLNode,
        selector: tuple[_SimpleSelector, ...] | None,
        *,
        include_scope: bool = False,
    ) -> _HTMLNode | None:
        """Return the first selector match in document order inside ``scope``."""

        if selector is None:
            return None
        positions = self._matches[selector]
        start = self._starts[id(scope)] + (0 if include_scope else 1)
        end = self._ends[id(scope)]
        match_index = bisect_left(positions, start)
        if match_index >= len(positions) or positions[match_index] >= end:
            return None
        return self._nodes[positions[match_index]]


@dataclass(slots=True)
class _HTMLTextBudget:
    """Deterministic aggregate work limit for overlapping listing text."""

    node_visits: int = 0
    character_visits: int = 0

    def charge_node(self) -> None:
        self.node_visits += 1
        if self.node_visits > WEB_MAX_LISTING_TEXT_NODE_VISITS:
            raise WebParseError(
                "document_too_complex",
                "Web listing text scopes overlapped beyond the safe work limit",
            )

    def charge_text(self, value: str) -> None:
        self.character_visits += len(value)
        if self.character_visits > WEB_MAX_LISTING_TEXT_CHAR_VISITS:
            raise WebParseError(
                "document_too_complex",
                "Web listing text exceeded the safe work limit",
            )


def _node_text(
    node: _HTMLNode,
    *,
    limit: int = 4_000,
    budget: _HTMLTextBudget | None = None,
) -> str:
    parts: list[str] = []
    stack: list[str | _HTMLNode] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if budget is not None:
                budget.charge_text(current)
            parts.append(current)
            continue
        if budget is not None:
            budget.charge_node()
        if current.tag in _HIDDEN_TEXT_TAGS and current is not node:
            continue
        stack.extend(reversed(current.content))
    return _plain_text(" ".join(parts), limit=limit)


def _parse_html_tree(
    document: bytes | str,
    *,
    max_nodes: int = WEB_MAX_HTML_NODES,
) -> _HTMLNode:
    parser = _HTMLTreeParser(max_nodes=max_nodes)
    try:
        parser.feed(_decode_document(document))
        parser.close()
    except WebParseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise WebParseError("invalid_document", "Web source returned invalid HTML") from exc
    return parser.root


def _optional_selector(raw: str) -> tuple[_SimpleSelector, ...] | None:
    return _compile_selector(raw) if raw.strip() else None


def discover_feed_urls(
    document: bytes | str,
    *,
    page_url: str,
    allowed_origins: Collection[str],
    limit: int = 8,
) -> list[str]:
    """Return deterministic RSS/Atom/JSON Feed autodiscovery links."""

    canonicalize_web_url(page_url, allowed_origins=allowed_origins)
    root = _parse_html_tree(document)
    urls: list[str] = []
    seen: set[str] = set()
    for node in _walk_html(root):
        if node.tag != "link":
            continue
        rel = frozenset(node.attrs.get("rel", "").casefold().split())
        media_type = node.attrs.get("type", "").split(";", 1)[0].strip().casefold()
        if "alternate" not in rel or media_type not in (_XML_CONTENT_TYPES | _JSON_CONTENT_TYPES):
            continue
        raw_href = node.attrs.get("href", "").strip()
        if not raw_href:
            continue
        try:
            url = canonicalize_web_url(
                urljoin(page_url, raw_href),
                allowed_origins=allowed_origins,
            )
        except WebFetchError:
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= min(32, max(1, int(limit))):
            break
    return urls


def classify_html_access_wall(document: bytes | str) -> WebHTMLAccessWall:
    """Classify strong login/challenge evidence in one bounded HTML page.

    The result intentionally matches the formal Web adapter's fail-closed
    semantics.  It returns only a stable code and never includes response
    text, credentials, or network details.
    """

    root = _parse_html_tree(document)
    visible_text = " ".join(_node_text(root, limit=100_000).casefold().split())
    structural_challenge = False
    public_content_evidence = False
    for node in _walk_html(root):
        if node.tag == "link":
            rel = frozenset(node.attrs.get("rel", "").casefold().split())
            media_type = node.attrs.get("type", "").split(";", 1)[0].strip().casefold()
            if "alternate" in rel and media_type in (_XML_CONTENT_TYPES | _JSON_CONTENT_TYPES):
                public_content_evidence = True
        if node.tag in {"article", "main"}:
            public_content_evidence = True
        class_and_id = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}".casefold()
        if "cf-chl-" in class_and_id or "cloudflare-challenge" in class_and_id:
            structural_challenge = True

    login_context_tokens = ("login", "log-in", "signin", "sign-in", "auth")
    for node in _walk_html(root):
        if node.tag != "input":
            continue
        if not any(
            node.attrs.get(attribute, "").strip().casefold() == "password"
            for attribute in ("type", "name")
        ):
            continue
        form = node.parent
        while form is not None and form.tag != "form":
            form = form.parent
        if form is None:
            continue
        form_identity = " ".join(
            (
                form.attrs.get("id", ""),
                form.attrs.get("class", ""),
                form.attrs.get("action", ""),
                form.attrs.get("aria-label", ""),
                _node_text(form, limit=2_000),
            )
        ).casefold()
        style = form.attrs.get("style", "").replace(" ", "").casefold()
        hidden = "hidden" in form.attrs or "display:none" in style or "visibility:hidden" in style
        if not hidden and (
            any(token in form_identity for token in login_context_tokens)
            or not public_content_evidence
        ):
            return "login_required"

    if structural_challenge or any(
        marker in visible_text
        for marker in (
            "cloudflare challenge",
            "verify you are human",
            "enable javascript and cookies to continue",
        )
    ):
        return "challenge_page"
    return ""


def parse_html_listing(
    document: bytes | str,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    plan: HTMLListingPlan,
    limit: int = 20,
) -> list[DiscoveredContent]:
    """Extract a static listing with a deliberately small CSS subset.

    Selectors support descendant combinators and simple ``tag``, ``.class``,
    and ``#id`` components.  Attribute selectors and pseudo-classes are
    rejected so recipes stay auditable and predictable.
    """

    canonicalize_web_url(base_url, allowed_origins=allowed_origins)
    return _parse_html_listing_root(
        _parse_html_tree(document),
        base_url=base_url,
        allowed_origins=allowed_origins,
        plan=plan,
        limit=limit,
    )


def _parse_html_listing_root(
    root: _HTMLNode,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    plan: HTMLListingPlan,
    limit: int,
) -> list[DiscoveredContent]:
    """Execute one validated listing plan against an already bounded tree."""

    item_selector = _optional_selector(plan.item_selector)
    link_selector = _compile_selector(plan.link_selector)
    title_selector = _optional_selector(plan.title_selector)
    summary_selector = _optional_selector(plan.summary_selector)
    author_selector = _optional_selector(plan.author_selector)
    published_selector = _optional_selector(plan.published_selector)
    empty_selector = _optional_selector(plan.empty_selector)
    published_attribute = plan.published_attribute.strip().casefold()
    if published_attribute and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", published_attribute) is None:
        raise WebParseError("invalid_selector", "Web listing attribute is unsupported")

    selectors = {
        selector
        for selector in (
            item_selector,
            link_selector,
            title_selector,
            summary_selector,
            author_selector,
            published_selector,
            empty_selector,
        )
        if selector is not None
    }
    selector_index = _HTMLSelectorIndex(root, selectors)
    scopes = selector_index.all(root, item_selector) if item_selector is not None else [root]
    if not scopes:
        if empty_selector is not None and selector_index.first(root, empty_selector) is not None:
            return []
        raise WebParseError("schema_changed", "Web listing item selector matched no content")
    if len(scopes) > WEB_MAX_LISTING_SCOPES:
        raise WebParseError("document_too_complex", "Web listing matched too many item scopes")

    items: list[DiscoveredContent] = []
    seen_urls: set[str] = set()
    text_budget = _HTMLTextBudget()
    bounded_limit = min(200, max(1, int(limit)))
    for scope in scopes:
        link_node = selector_index.first(scope, link_selector, include_scope=True)
        if link_node is None:
            continue
        raw_url = link_node.attrs.get("href", "").strip()
        if not raw_url:
            continue
        try:
            content_url = canonicalize_web_url(
                urljoin(base_url, raw_url), allowed_origins=allowed_origins
            )
        except WebFetchError:
            continue
        if content_url in seen_urls:
            continue
        title_node = selector_index.first(scope, title_selector, include_scope=True)
        title = _node_text(title_node or link_node, limit=300, budget=text_budget)
        if not title:
            continue
        summary_node = selector_index.first(scope, summary_selector, include_scope=True)
        author_node = selector_index.first(scope, author_selector, include_scope=True)
        published_node = selector_index.first(scope, published_selector, include_scope=True)
        published_value = ""
        if published_node is not None:
            published_value = (
                published_node.attrs.get(published_attribute, "")
                if published_attribute
                else _node_text(published_node, limit=128, budget=text_budget)
            )
        items.append(
            _discovered_web_item(
                content_url=content_url,
                title=title,
                strategy="web-html-list",
                description=(
                    _node_text(summary_node, budget=text_budget) if summary_node is not None else ""
                ),
                author=(
                    _node_text(author_node, limit=160, budget=text_budget)
                    if author_node is not None
                    else ""
                ),
                published_value=published_value,
            )
        )
        seen_urls.add(content_url)
        if len(items) >= bounded_limit:
            break
    if not items:
        raise WebParseError("schema_changed", "Web listing produced no valid same-origin links")
    return items


def infer_html_listing_plan(
    document: bytes | str,
    *,
    base_url: str,
    allowed_origins: Collection[str],
    minimum_items: int = 2,
    sample_limit: int = 3,
) -> tuple[HTMLListingPlan, tuple[DiscoveredContent, ...]] | None:
    """Infer one conservative static-listing plan from repeated HTML structure.

    This is an authoring helper, not a general-purpose scraper.  It considers
    only repeated ``article`` containers or repeated, content-like class names
    on ``article``/``li``/``div``/``section`` elements.  Every proposed plan is
    then executed by :func:`parse_html_listing`; inference succeeds only when
    that existing parser produces at least two distinct same-origin items.

    The candidate and selector counts are fixed so a bounded HTML response
    cannot turn URL probing into unbounded selector search.
    """

    required = min(10, max(2, int(minimum_items)))
    bounded_sample_limit = min(10, max(required, int(sample_limit)))
    canonicalize_web_url(base_url, allowed_origins=allowed_origins)
    root = _parse_html_tree(document, max_nodes=WEB_MAX_LISTING_INFERENCE_NODES)
    all_nodes = _walk_html(root)
    candidate_counts: dict[str, int] = {}
    content_class_tokens = frozenset(
        {"article", "card", "entry", "item", "news", "post", "result", "story", "teaser"}
    )
    navigation_class_tokens = frozenset(
        {"breadcrumb", "footer", "menu", "nav", "pagination", "sidebar"}
    )

    def _class_tokens(value: str) -> frozenset[str]:
        # Compare lexical tokens, not substrings: ``navigation-story`` is
        # intentionally different from ``nav-story`` and ``menu-item``.
        return frozenset(re.findall(r"[a-z0-9]+", value.casefold()))

    def _inside_navigation_chrome(node: _HTMLNode) -> bool:
        current: _HTMLNode | None = node
        while current is not None:
            if current.tag in {"aside", "footer", "header", "nav"}:
                return True
            ancestor_tokens: set[str] = set()
            for class_name in current.attrs.get("class", "").split()[:16]:
                ancestor_tokens.update(_class_tokens(class_name))
            if ancestor_tokens & navigation_class_tokens:
                return True
            current = current.parent
        return False

    def _candidate_selectors(node: _HTMLNode) -> tuple[str, ...]:
        if node.tag not in {"article", "li", "div", "section"}:
            return ()
        selectors: list[str] = ["article"] if node.tag == "article" else []
        for class_name in node.attrs.get("class", "").split()[:16]:
            if _SIMPLE_SELECTOR_RE.fullmatch(f".{class_name}") is None:
                continue
            class_tokens = _class_tokens(class_name)
            if class_tokens & navigation_class_tokens:
                continue
            if node.tag != "article" and not class_tokens & content_class_tokens:
                continue
            selectors.append(f"{node.tag}.{class_name}")
        return tuple(dict.fromkeys(selectors))

    unsafe_selectors: set[str] = set()
    known_selectors: set[str] = set()
    for node in all_nodes:
        selectors = _candidate_selectors(node)
        if not selectors:
            continue
        new_selectors = set(selectors) - known_selectors
        if len(known_selectors) + len(new_selectors) > WEB_MAX_LISTING_INFERENCE_SIGNATURES:
            raise WebParseError(
                "document_too_complex",
                "Web listing exposed too many structural signatures to infer safely",
            )
        known_selectors.update(new_selectors)
        if _inside_navigation_chrome(node):
            unsafe_selectors.update(selectors)
            continue
        for selector in selectors:
            candidate_counts[selector] = candidate_counts.get(selector, 0) + 1

    def _selector_rank(selector: str) -> tuple[int, int, str]:
        tag, _separator, class_name = selector.partition(".")
        content_like = bool(_class_tokens(class_name) & content_class_tokens)
        if tag == "article" and class_name and content_like:
            priority = 0
        elif tag == "article" and class_name:
            priority = 1
        elif tag == "article":
            priority = 2
        else:
            priority = 3
        # Prefer a narrower repeated signature, then lexical order for stable
        # output across runs and Python hash seeds.
        return (priority, candidate_counts[selector], selector)

    item_selectors = sorted(
        (
            selector
            for selector, count in candidate_counts.items()
            if required <= count <= WEB_MAX_LISTING_SCOPES and selector not in unsafe_selectors
        ),
        key=_selector_rank,
    )[:WEB_MAX_LISTING_INFERENCE_PLANS]
    link_selectors = ("h1 a", "h2 a", "h3 a", "a")
    attempts = 0
    for item_selector in item_selectors:
        for link_selector in link_selectors:
            if attempts >= WEB_MAX_LISTING_INFERENCE_PLANS:
                return None
            attempts += 1
            plan = HTMLListingPlan(
                item_selector=item_selector,
                link_selector=link_selector,
                title_selector=link_selector,
            )
            try:
                items = _parse_html_listing_root(
                    root,
                    base_url=base_url,
                    allowed_origins=allowed_origins,
                    plan=plan,
                    limit=bounded_sample_limit,
                )
            except WebParseError as exc:
                if exc.code == "document_too_complex":
                    return None
                continue
            if len(items) >= required:
                return plan, tuple(items[:bounded_sample_limit])
    return None


def _meta_value(root: _HTMLNode, *keys: str) -> str:
    wanted = {key.casefold() for key in keys}
    for node in _walk_html(root):
        if node.tag != "meta":
            continue
        identity = (
            node.attrs.get("property") or node.attrs.get("name") or node.attrs.get("itemprop") or ""
        ).casefold()
        if identity in wanted:
            value = node.attrs.get("content", "").strip()
            if value:
                return value
    return ""


def _link_value(root: _HTMLNode, rel_name: str) -> str:
    for node in _walk_html(root):
        if node.tag != "link":
            continue
        rel = frozenset(node.attrs.get("rel", "").casefold().split())
        if rel_name in rel and node.attrs.get("href", "").strip():
            return node.attrs["href"].strip()
    return ""


def _jsonld_types(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset({value.rsplit("/", 1)[-1].casefold()})
    if isinstance(value, list):
        return frozenset(
            str(item).rsplit("/", 1)[-1].casefold() for item in value if isinstance(item, str)
        )
    return frozenset()


def _jsonld_article(root: _HTMLNode) -> Mapping[str, Any]:
    article_types = frozenset({"article", "newsarticle", "blogposting"})
    script_count = 0
    for node in _walk_html(root):
        if node.tag != "script":
            continue
        media_type = node.attrs.get("type", "").split(";", 1)[0].strip().casefold()
        if media_type != "application/ld+json":
            continue
        script_count += 1
        if script_count > 8:
            break
        raw = "".join(value for value in node.content if isinstance(value, str)).strip()
        if not raw or len(raw) > 100_000:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, RecursionError):
            continue
        stack: list[object] = [payload]
        visited = 0
        while stack and visited < 1_000:
            value = stack.pop()
            visited += 1
            if isinstance(value, Mapping):
                if _jsonld_types(value.get("@type")) & article_types:
                    return value
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return {}


def _jsonld_string(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("@id") or value.get("url") or value.get("name") or "").strip()
    return ""


def _jsonld_author(value: object) -> str:
    if isinstance(value, list):
        names = [_jsonld_string(item) for item in value[:5]]
        return ", ".join(name for name in names if name)
    return _jsonld_string(value)


def _html_title(root: _HTMLNode) -> str:
    for node in _walk_html(root):
        if node.tag == "title":
            value = _node_text(node, limit=300)
            if value:
                return value
    return ""


def parse_html_detail(
    document: bytes | str,
    *,
    page_url: str,
    allowed_origins: Collection[str],
) -> DiscoveredContent | None:
    """Extract bounded article metadata from one public HTML detail page."""

    fallback_url = canonicalize_web_url(page_url, allowed_origins=allowed_origins)
    root = _parse_html_tree(document)
    structured = _jsonld_article(root)

    raw_canonical = (
        _link_value(root, "canonical")
        or _meta_value(root, "og:url")
        or _jsonld_string(structured.get("url"))
        or _jsonld_string(structured.get("mainEntityOfPage"))
    )
    content_url = fallback_url
    if raw_canonical:
        try:
            content_url = canonicalize_web_url(
                urljoin(page_url, raw_canonical), allowed_origins=allowed_origins
            )
        except WebFetchError:
            content_url = fallback_url

    title = (
        _meta_value(root, "og:title", "twitter:title")
        or _jsonld_string(structured.get("headline"))
        or _jsonld_string(structured.get("name"))
        or _html_title(root)
    )
    title = _plain_text(title, limit=300)
    if not title:
        return None
    description = _meta_value(
        root, "og:description", "twitter:description", "description"
    ) or _jsonld_string(structured.get("description"))
    author = _jsonld_author(structured.get("author")) or _meta_value(root, "author")
    published_value = _meta_value(
        root, "article:published_time", "datepublished", "date"
    ) or _jsonld_string(structured.get("datePublished"))
    return _discovered_web_item(
        content_url=content_url,
        title=title,
        strategy="web-detail",
        description=description,
        author=author,
        published_value=published_value,
    )
