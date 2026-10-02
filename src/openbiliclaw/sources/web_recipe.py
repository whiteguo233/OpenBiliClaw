"""Typed, bounded recipes for anonymous public-Web discovery.

The generic ``source_recipes`` table predates formal Web discovery and accepts
adapter-shaped dictionaries.  Public Web is a materially different trust
boundary: a recipe is also a network allowlist.  This module therefore owns
the only supported conversion from an API/tool payload to a ``web`` recipe.
Runtime cursors, conditional-request validators and health fields are never
accepted here; they belong to the backend-owned Web state ledger.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from openbiliclaw.sources.platforms import infer_source_platform_from_url
from openbiliclaw.sources.web_public import (
    HTMLListingPlan,
    WebParseError,
    validate_html_listing_plan,
)

WEB_RECIPE_STRATEGIES = frozenset({"feed", "sitemap", "listing"})
WEB_RECIPE_PLAN_VERSION = 1
WEB_RECIPE_INTERVAL_RANGE = (5, 10_080)
WEB_RECIPE_MAX_ITEMS_RANGE = (1, 50)
WEB_RECIPE_MAX_PAGES_RANGE = (1, 5)
WEB_RECIPE_DETAIL_RANGE = (0, 20)
WEB_RECIPE_SHARE_RANGE = (1, 20)

_TOP_LEVEL_FIELDS = frozenset({"name", "strategy", "config", "target_share", "enabled"})
_SERVER_OWNED_FIELDS = frozenset(
    {
        "id",
        "source_type",
        "created_by",
        "created_at",
        "last_fetched_at",
        "status",
        "state",
    }
)
_CONFIG_FIELDS = frozenset(
    {
        "entry_url",
        "allowed_origins",
        "interval_minutes",
        "max_items",
        "max_pages",
        "detail_fetch_limit",
        "listing",
    }
)
_LISTING_FIELDS = (
    "item_selector",
    "link_selector",
    "title_selector",
    "summary_selector",
    "author_selector",
    "published_selector",
    "published_attribute",
    "empty_selector",
)
_RESERVED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home",
    ".lan",
)


class WebRecipeValidationError(ValueError):
    """Raised when a public Web recipe would exceed its declared authority."""


def is_compiled_public_web_recipe(recipe: Mapping[str, Any]) -> bool:
    """Return whether *recipe* has the backend-compiled public-Web shape.

    Older generic extractor rows also used ``source_type="web"`` but have a
    ``web_extract`` strategy and no typed plan marker.  Runtime readiness and
    scheduling must never mistake those rows for an explicit network
    allowlist compiled by :func:`build_web_recipe`.
    """

    if recipe.get("source_type") != "web":
        return False
    strategy = recipe.get("strategy")
    if not isinstance(strategy, str) or strategy not in WEB_RECIPE_STRATEGIES:
        return False
    config = recipe.get("config")
    if not isinstance(config, Mapping):
        return False
    plan_version = config.get("plan_version")
    if (
        isinstance(plan_version, bool)
        or not isinstance(plan_version, int)
        or plan_version != WEB_RECIPE_PLAN_VERSION
    ):
        return False
    entry_url = config.get("entry_url")
    origins = config.get("allowed_origins")
    if not isinstance(entry_url, str) or not entry_url.strip():
        return False
    if (
        isinstance(origins, (str, bytes))
        or not isinstance(origins, Sequence)
        or not origins
        or any(not isinstance(origin, str) or not origin.strip() for origin in origins)
    ):
        return False
    numeric_fields = (
        ("interval_minutes", WEB_RECIPE_INTERVAL_RANGE),
        ("max_items", WEB_RECIPE_MAX_ITEMS_RANGE),
        ("max_pages", WEB_RECIPE_MAX_PAGES_RANGE),
        ("detail_fetch_limit", WEB_RECIPE_DETAIL_RANGE),
    )
    for field, (lower, upper) in numeric_fields:
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            return False
    if int(config["detail_fetch_limit"]) > int(config["max_items"]):
        return False
    if strategy == "sitemap" and int(config["detail_fetch_limit"]) < 1:
        return False
    if strategy == "listing":
        listing = config.get("listing")
        if not isinstance(listing, Mapping):
            return False
        if not all(
            isinstance(listing.get(field), str) and str(listing[field]).strip()
            for field in ("item_selector", "link_selector")
        ):
            return False
    return True


def build_web_recipe(
    payload: Mapping[str, Any],
    *,
    recipe_id: str | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    """Validate and compile a client payload into a backend-owned Web recipe.

    ``recipe_id`` and ``created_by`` are trusted call-site inputs, never values
    read from ``payload``.  Callers updating an existing row pass its original
    values so a client cannot change recipe ownership or identity.
    """

    if not isinstance(payload, Mapping):
        raise WebRecipeValidationError("web recipe payload must be an object")
    payload_keys = {str(key) for key in payload}
    owned = sorted(payload_keys & _SERVER_OWNED_FIELDS)
    unknown = sorted(payload_keys - _TOP_LEVEL_FIELDS - _SERVER_OWNED_FIELDS)
    if owned or unknown:
        details = ", ".join([*owned, *unknown])
        raise WebRecipeValidationError(f"client-owned fields are not allowed: {details}")

    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 120:
        raise WebRecipeValidationError("name must contain 1..120 characters")

    strategy = str(payload.get("strategy") or "").strip().lower()
    if strategy not in WEB_RECIPE_STRATEGIES:
        allowed = ", ".join(sorted(WEB_RECIPE_STRATEGIES))
        raise WebRecipeValidationError(f"strategy must be one of: {allowed}")

    raw_config = payload.get("config")
    if not isinstance(raw_config, Mapping):
        raise WebRecipeValidationError("config must be an object")
    config_keys = {str(key) for key in raw_config}
    unknown_config = sorted(config_keys - _CONFIG_FIELDS)
    if unknown_config:
        raise WebRecipeValidationError(
            "client-owned config fields are not allowed: " + ", ".join(unknown_config)
        )

    entry_url = _normalize_public_url(raw_config.get("entry_url"), field="entry_url")
    entry_origin = _origin_for(entry_url)
    raw_origins = raw_config.get("allowed_origins", ())
    if isinstance(raw_origins, str) or not isinstance(raw_origins, Sequence):
        raise WebRecipeValidationError("allowed_origins must be a list of public origins")
    if len(raw_origins) > 8:
        raise WebRecipeValidationError("allowed_origins may contain at most 8 origins")
    allowed_origins = [entry_origin]
    for raw_origin in raw_origins:
        normalized = _normalize_public_url(raw_origin, field="allowed_origins")
        normalized_parts = urlsplit(normalized)
        try:
            raw_fragment = urlsplit(str(raw_origin or "").strip()).fragment
        except ValueError as exc:
            raise WebRecipeValidationError(
                "allowed_origins must contain exact origins without paths"
            ) from exc
        if normalized_parts.path not in {"", "/"} or normalized_parts.query or raw_fragment:
            raise WebRecipeValidationError(
                "allowed_origins must contain exact origins without paths"
            )
        origin = _origin_for(normalized)
        if origin not in allowed_origins:
            allowed_origins.append(origin)

    interval_minutes = _bounded_int(
        raw_config.get("interval_minutes", 30),
        field="interval_minutes",
        bounds=WEB_RECIPE_INTERVAL_RANGE,
    )
    max_items = _bounded_int(
        raw_config.get("max_items", 20),
        field="max_items",
        bounds=WEB_RECIPE_MAX_ITEMS_RANGE,
    )
    max_pages = _bounded_int(
        raw_config.get("max_pages", 1),
        field="max_pages",
        bounds=WEB_RECIPE_MAX_PAGES_RANGE,
    )
    detail_fetch_limit = _bounded_int(
        raw_config.get("detail_fetch_limit", min(max_items, 10)),
        field="detail_fetch_limit",
        bounds=WEB_RECIPE_DETAIL_RANGE,
    )
    if detail_fetch_limit > max_items:
        raise WebRecipeValidationError("detail_fetch_limit cannot exceed max_items")
    raw_listing = raw_config.get("listing", {})
    if not isinstance(raw_listing, Mapping):
        raise WebRecipeValidationError("listing must be an object")
    unknown_listing = sorted({str(key) for key in raw_listing} - set(_LISTING_FIELDS))
    if unknown_listing:
        raise WebRecipeValidationError(
            "listing contains unsupported fields: " + ", ".join(unknown_listing)
        )
    listing: dict[str, str] = {}
    for field_name in _LISTING_FIELDS:
        value = str(raw_listing.get(field_name) or "").strip()
        if len(value) > 256:
            raise WebRecipeValidationError(f"listing.{field_name} must be at most 256 characters")
        if value:
            listing[field_name] = value
    if strategy == "listing":
        if not listing.get("link_selector"):
            listing["link_selector"] = "a"
        try:
            validate_html_listing_plan(HTMLListingPlan(**listing))
        except WebParseError as exc:
            raise WebRecipeValidationError(f"listing plan is invalid: {exc}") from exc
    if strategy == "sitemap" and detail_fetch_limit < 1:
        raise WebRecipeValidationError("detail_fetch_limit must be at least 1 for sitemap")
    target_share = _bounded_int(
        payload.get("target_share", 4),
        field="target_share",
        bounds=WEB_RECIPE_SHARE_RANGE,
    )
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WebRecipeValidationError("enabled must be a boolean")

    compiled_config: dict[str, Any] = {
        "entry_url": entry_url,
        "allowed_origins": allowed_origins,
        "interval_minutes": interval_minutes,
        "max_items": max_items,
        "max_pages": max_pages,
        "detail_fetch_limit": detail_fetch_limit,
    }
    if listing:
        compiled_config["listing"] = listing
    compiled_config["plan_version"] = WEB_RECIPE_PLAN_VERSION
    return {
        "id": str(recipe_id or uuid.uuid4()),
        "source_type": "web",
        "name": name,
        "strategy": strategy,
        "config": compiled_config,
        "target_share": target_share,
        "enabled": enabled,
        "created_by": str(created_by or "user"),
    }


def web_recipe_payload_from_existing(
    existing: Mapping[str, Any],
    changes: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge an update into editable fields, rejecting identity/state writes."""

    disallowed = {str(key) for key in changes} - _TOP_LEVEL_FIELDS
    if disallowed:
        raise WebRecipeValidationError(
            "client-owned fields are not allowed: " + ", ".join(sorted(disallowed))
        )
    existing_config = existing.get("config")
    editable_config = (
        {key: value for key, value in existing_config.items() if key in _CONFIG_FIELDS}
        if isinstance(existing_config, Mapping)
        else existing_config
    )
    merged = {
        "name": existing.get("name"),
        "strategy": existing.get("strategy"),
        "config": editable_config,
        "target_share": existing.get("target_share", 4),
        "enabled": existing.get("enabled", True),
    }
    merged.update(changes)
    return merged


def _bounded_int(value: object, *, field: str, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool):
        raise WebRecipeValidationError(f"{field} must be an integer")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise WebRecipeValidationError(f"{field} must be an integer") from exc
    lower, upper = bounds
    if result < lower or result > upper:
        raise WebRecipeValidationError(f"{field} must be between {lower} and {upper}")
    return result


def _normalize_public_url(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 4096
        or "\\" in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise WebRecipeValidationError(f"{field} must be a public http(s) URL")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise WebRecipeValidationError(f"{field} must be a public http(s) URL") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise WebRecipeValidationError(f"{field} must be a public http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise WebRecipeValidationError(f"{field} must not contain userinfo")
    expected_port = 443 if scheme == "https" else 80
    if port not in {None, expected_port}:
        raise WebRecipeValidationError(f"{field} must use the default port for its scheme")

    host = parsed.hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise WebRecipeValidationError(f"{field} must use a public DNS hostname, not an IP")
    if "." not in host or host == "localhost" or host.endswith(_RESERVED_HOST_SUFFIXES):
        raise WebRecipeValidationError(f"{field} must use a public DNS hostname")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise WebRecipeValidationError(f"{field} contains an invalid hostname") from exc
    # IDNA mapping can materially change the security class of a hostname:
    # circled/full-width labels may normalize to an IP literal or a reserved
    # local suffix.  Re-run every public-host invariant on the actual ASCII
    # authority that will be persisted and requested.
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise WebRecipeValidationError(f"{field} must use a public DNS hostname, not an IP")
    if (
        "." not in ascii_host
        or ascii_host == "localhost"
        or ascii_host.endswith(_RESERVED_HOST_SUFFIXES)
    ):
        raise WebRecipeValidationError(f"{field} must use a public DNS hostname")
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or re.fullmatch(r"[a-z0-9-]+", label) is None
        for label in labels
    ):
        raise WebRecipeValidationError(f"{field} contains an invalid hostname")
    if infer_source_platform_from_url(f"{scheme}://{ascii_host}"):
        raise WebRecipeValidationError(
            f"{field} belongs to a dedicated source adapter, not generic Web"
        )

    normalized = SplitResult(
        scheme=scheme,
        netloc=ascii_host,
        path=parsed.path,
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(normalized)


def _origin_for(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
