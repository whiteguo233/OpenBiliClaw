#!/usr/bin/env python3
"""Inventory one platform source's concrete registration points.

This is a static registration inventory.  A green report proves that declared
source keys are present at the expected integration seams; it does not prove
normalizer semantics, upstream compatibility, browser behaviour, or real E2E
completion.

Usage::

    PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" scripts/audit_platform_source.py \
      --contract docs/source.toml --check --json
    PYTHONPATH="$PWD/src" "$SOURCE_SKILL_PYTHON" scripts/audit_platform_source.py \
      --contract docs/source.toml --diff-base origin/main --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal, cast

Status = Literal["PASS", "MISSING", "MANUAL", "N/A"]
IntegrationLevel = Literal["full", "discovery-only", "capability-increment", "audit-only"]

DISCLAIMER = (
    "registration inventory only; PASS does not prove semantic correctness or real E2E completion"
)
STATUS_ORDER: tuple[Status, ...] = ("PASS", "MISSING", "MANUAL", "N/A")
INTEGRATION_LEVELS: tuple[IntegrationLevel, ...] = (
    "full",
    "discovery-only",
    "capability-increment",
    "audit-only",
)
SURFACE_KEYS = (
    "cli",
    "setup",
    "desktop",
    "mobile",
    "extension_popup",
    "source_status",
    "credentials",
    "recommendation",
)
EXTENSION_TASKS = ("none", "identity-only", "browser-task")
IMAGE_MODES = ("none", "direct", "proxy")
DEEP_LINK_MODES = ("none", "browser-fallback", "native")
REFRESH_MODES = ("none", "init-only", "on-demand", "init-and-on-demand", "incremental")
AUTH_MODES = (
    "anonymous",
    "login-required",
    "anonymous-with-optional-credentials",
    "capability-specific",
)
TRANSPORT_KINDS = (
    "official-api",
    "private-api",
    "browser-task",
    "browser-page",
    "external-cli",
    "sdk",
    "hybrid",
    "web-http",
)
TRANSPORT_OWNERS = ("backend", "browser", "extension", "external-cli", "shared", "none")
HOST_POLICIES = ("static", "recipe-allowlist")
VERIFY_ACTION_VALUES = (
    "live_probe",
    "passive_health",
    "browser_heartbeat",
    "local_file",
    "none",
)
LOGIN_STATE_PATHS = ("none", "callback", "credential")
ENGAGEMENT_KEYS = ("view", "like", "favorite", "comment", "share", "danmaku")
ENGAGEMENT_AVAILABILITY = ("mapped", "unavailable")
SMOKE_SINK_KEYS = (
    "task",
    "task_result",
    "seen",
    "affinity",
    "snapshot",
    "schedule",
    "event_ingress",
    "memory",
    "profile",
)
SMOKE_SINK_POLICIES = ("allowed", "forbidden")
CAPABILITY_AUTH_MODES = ("anonymous", "optional-credential", "login-required")
ROUTE_KEY_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
KNOWN_MUTATING_ACTIONS = frozenset(
    {
        "bookmark",
        "collect",
        "comment",
        "delete",
        "dislike",
        "downvote",
        "edit",
        "favorite",
        "follow",
        "like",
        "block",
        "join",
        "leave",
        "mute",
        "pin",
        "post",
        "publish",
        "rate",
        "remove",
        "reply",
        "report",
        "repost",
        "retweet",
        "save",
        "share",
        "star",
        "subscribe",
        "unfollow",
        "unlike",
        "upload",
        "upvote",
        "vote",
        "watch",
        "later",
    }
)
SAFE_ACTION_RE = re.compile(
    r"(?:"
    r"snapshot|scroll|search|hot|related|feed|ranked|latest|"
    r"read-(?:identity|public-(?:collection|feed|profile|item|page))|"
    r"fetch-public-(?:collection|feed|profile|item|page|search|ranked|latest)|"
    r"navigate-public-(?:item|page|profile)|"
    r"open-public-link|(?:open|close)-share-panel|copy-public-link"
    r")\Z"
)


class ContractError(ValueError):
    """The TOML contract is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class TransportContract:
    kind: str
    owner: str
    entrypoints: tuple[str, ...]
    route_aliases: tuple[str, ...]
    fallback_owner: str
    capability_routes: dict[str, str]
    requires_overseas_network: bool
    routed_by_network_mode: bool
    network_policy: str


@dataclass(frozen=True)
class IdentityContract:
    item_id: str
    url: str
    dedupe: str
    account_scope: str


@dataclass(frozen=True)
class AuthContract:
    mode: str
    credential_kinds: tuple[str, ...]
    verify_action: str
    write_path: str
    account_resolution: str
    identity_evidence: str
    capability_modes: dict[str, str]
    capability_required: dict[str, bool]
    login_cookie_names: tuple[str, ...]
    login_state_path: str


@dataclass(frozen=True)
class UpstreamContract:
    success_content_types: tuple[str, ...]
    pagination: str
    terminal_evidence: str
    terminal_policy: str
    partial_policy: str
    publication_time_policy: str


@dataclass(frozen=True)
class DiscoverContract:
    modes: tuple[str, ...]
    search_generation: str
    budget: str
    cursor: str


@dataclass(frozen=True)
class ProfileContract:
    signals: bool
    incremental: bool
    refresh_mode: str


@dataclass(frozen=True)
class ExtensionContract:
    task: str
    hosts: tuple[str, ...]
    task_marker: bool
    background: bool
    early_response: bool
    cookie_sync: bool


@dataclass(frozen=True)
class MediaContract:
    image: str
    image_hosts: tuple[str, ...]
    deep_link: str
    native_save: bool


@dataclass(frozen=True)
class E2EContract:
    safe_actions: tuple[str, ...]
    mutating_actions: tuple[str, ...]
    safe_assertions: dict[str, str]
    safe_postconditions: dict[str, str]


@dataclass(frozen=True)
class EventContract:
    strategy_prefixes: tuple[str, ...]
    mappings: str
    scope_caps: str


@dataclass(frozen=True)
class TaskContract:
    lease: str
    idle_deadline: str
    absolute_deadline: str
    retry: str
    buffer: str


@dataclass(frozen=True)
class SmokeContract:
    storage_scope: str
    sinks: dict[str, str]


@dataclass(frozen=True)
class PlatformContract:
    schema_version: int
    canonical_slug: str
    display_name: str
    integration_level: IntegrationLevel
    host_policy: str
    aliases: tuple[str, ...]
    hosts: tuple[str, ...]
    content_types: tuple[str, ...]
    transport: TransportContract
    identity: IdentityContract
    auth: AuthContract
    upstream: UpstreamContract
    discover: DiscoverContract
    profile: ProfileContract
    extension: ExtensionContract
    surfaces: dict[str, bool]
    engagement: dict[str, str]
    media: MediaContract
    e2e: E2EContract
    events: EventContract
    task: TaskContract
    smoke: SmokeContract
    exclusions: dict[str, str]
    exclusion_tests: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Evidence:
    path: str
    line: int
    excerpt: str
    changed_since_base: bool | None = None


@dataclass(frozen=True)
class AuditResult:
    capability: str
    label: str
    status: Status
    required: bool
    detail: str
    evidence: tuple[Evidence, ...] = ()


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContractError(f"missing TOML table [{key}]")
    return value


def _text(data: dict[str, Any], key: str, *, context: str = "contract") -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _boolean(data: dict[str, Any], key: str, *, context: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ContractError(f"{context}.{key} must be true or false")
    return value


def _strings(
    data: dict[str, Any],
    key: str,
    *,
    context: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{context}.{key} must be an array of strings")
    result = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if not allow_empty and not result:
        raise ContractError(f"{context}.{key} must not be empty")
    return result


def _string_map(data: dict[str, Any], key: str, *, context: str) -> dict[str, str]:
    value = data.get(key)
    if not isinstance(value, dict) or not value:
        raise ContractError(f"{context}.{key} must be a non-empty TOML table")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if (
            not isinstance(raw_key, str)
            or not raw_key.strip()
            or not isinstance(raw_value, str)
            or not raw_value.strip()
        ):
            raise ContractError(f"{context}.{key} keys and values must be non-empty strings")
        result[raw_key.strip()] = raw_value.strip()
    return result


def _bool_map(data: dict[str, Any], key: str, *, context: str) -> dict[str, bool]:
    value = data.get(key)
    if not isinstance(value, dict) or not value:
        raise ContractError(f"{context}.{key} must be a non-empty TOML table")
    result: dict[str, bool] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip() or not isinstance(raw_value, bool):
            raise ContractError(f"{context}.{key} keys must be strings and values booleans")
        result[raw_key.strip()] = raw_value
    return result


def _string_list_table(data: dict[str, Any], key: str) -> dict[str, tuple[str, ...]]:
    table = _table(data, key)
    result: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_value in table.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ContractError(f"{key} keys must be non-empty strings")
        if not isinstance(raw_value, list) or any(not isinstance(item, str) for item in raw_value):
            raise ContractError(f"{key}.{raw_key} must be an array of test references")
        references = tuple(dict.fromkeys(item.strip() for item in raw_value if item.strip()))
        if not references:
            raise ContractError(f"{key}.{raw_key} must not be empty")
        for reference in references:
            if reference.count("::") != 1:
                raise ContractError(
                    f"{key}.{raw_key} must use repo/path::exact_test_name: {reference!r}"
                )
            relative, test_name = reference.split("::", 1)
            candidate = Path(relative)
            if not relative or not test_name or candidate.is_absolute() or ".." in candidate.parts:
                raise ContractError(
                    f"{key}.{raw_key} test references must stay inside the repository: "
                    f"{reference!r}"
                )
            if not (relative.startswith("tests/") or relative.startswith("extension/tests/")):
                raise ContractError(
                    f"{key}.{raw_key} references must live under tests/ or extension/tests/: "
                    f"{reference!r}"
                )
            if candidate.suffix == ".py" and not test_name.startswith("test_"):
                raise ContractError(
                    f"{key}.{raw_key} Python references must name a top-level test_* node: "
                    f"{reference!r}"
                )
        result[raw_key.strip()] = references
    return result


def _choice(value: str, choices: tuple[str, ...], *, field: str) -> str:
    if value not in choices:
        rendered = ", ".join(choices)
        raise ContractError(f"{field} must be one of: {rendered}")
    return value


_COMMON_MULTI_LABEL_PUBLIC_SUFFIXES = frozenset(
    {"co.uk", "org.uk", "com.cn", "net.cn", "org.cn", "com.au", "co.jp"}
)
_WILDCARD_DNS_SUFFIXES = frozenset({"nip.io", "sslip.io", "localtest.me", "lvh.me", "vcap.me"})


def _validate_hostname(value: str, *, field: str) -> None:
    """Reject broad permissions and local/network targets from host contracts."""
    if value != value.lower() or any(token in value for token in ("://", "/", "@", ":", "*")):
        raise ContractError(f"{field} must contain lowercase DNS hostnames only: {value!r}")
    try:
        ip_address(value.strip("[]"))
    except ValueError:
        pass
    else:
        raise ContractError(f"{field} must not contain IP literals: {value!r}")
    labels = value.split(".")
    if (
        len(labels) < 2
        or value in _COMMON_MULTI_LABEL_PUBLIC_SUFFIXES
        or value in _WILDCARD_DNS_SUFFIXES
        or value == "localhost"
        or value.endswith((".localhost", ".local", ".internal"))
        or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in labels
        )
    ):
        raise ContractError(f"{field} must contain a concrete public DNS hostname: {value!r}")


def _require_exclusion(exclusions: dict[str, str], key: str, condition: bool) -> None:
    if condition and key not in exclusions:
        raise ContractError(f"[{key}] is disabled; add a non-empty [exclusions] reason for {key}")


def _action_tokens(action: str) -> frozenset[str]:
    return frozenset(token for token in re.split(r"[^a-z0-9]+", action.lower()) if token)


def _active_capabilities(
    discover: DiscoverContract,
    profile: ProfileContract,
    extension: ExtensionContract,
    media: MediaContract,
) -> set[str]:
    active = {"discover"} if discover.modes else set()
    if profile.signals:
        active.update(("profile", "bootstrap"))
    if profile.incremental:
        active.add("incremental")
    if extension.task == "identity-only":
        active.add("identity")
    if extension.cookie_sync:
        active.add("cookie-sync")
    if media.native_save:
        active.add("native-save")
    return active


def load_contract(path: Path) -> PlatformContract:
    """Parse and validate a platform-source contract using Python 3.11 tomllib."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ContractError(f"cannot read contract {path}: {exc}") from exc

    schema_version = raw.get("schema_version")
    if schema_version not in {1, 2}:
        raise ContractError("schema_version must be 1 or 2")
    slug = _text(raw, "canonical_slug")
    if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", slug) is None:
        raise ContractError("canonical_slug must be a lowercase source key")
    display_name = _text(raw, "display_name")
    level = cast(
        "IntegrationLevel",
        _choice(
            _text(raw, "integration_level"),
            INTEGRATION_LEVELS,
            field="integration_level",
        ),
    )
    host_policy = (
        "static"
        if schema_version == 1
        else _choice(
            _text(raw, "host_policy"),
            HOST_POLICIES,
            field="host_policy",
        )
    )
    aliases = _strings(raw, "aliases", context="contract", allow_empty=True)
    hosts = _strings(
        raw,
        "hosts",
        context="contract",
        allow_empty=host_policy == "recipe-allowlist",
    )
    if host_policy == "recipe-allowlist" and hosts:
        raise ContractError("host_policy='recipe-allowlist' requires hosts=[]")
    for host in hosts:
        _validate_hostname(host, field="hosts")
    content_types = _strings(raw, "content_types", context="contract")

    transport_raw = _table(raw, "transport")
    transport = TransportContract(
        kind=_choice(
            _text(transport_raw, "kind", context="transport"),
            TRANSPORT_KINDS,
            field="transport.kind",
        ),
        owner=_choice(
            _text(transport_raw, "owner", context="transport"),
            TRANSPORT_OWNERS,
            field="transport.owner",
        ),
        entrypoints=_strings(transport_raw, "entrypoints", context="transport"),
        route_aliases=_strings(
            transport_raw,
            "route_aliases",
            context="transport",
            allow_empty=True,
        ),
        fallback_owner=_choice(
            _text(transport_raw, "fallback_owner", context="transport"),
            TRANSPORT_OWNERS,
            field="transport.fallback_owner",
        ),
        capability_routes=_string_map(
            transport_raw,
            "capability_routes",
            context="transport",
        ),
        requires_overseas_network=_boolean(
            transport_raw,
            "requires_overseas_network",
            context="transport",
        ),
        routed_by_network_mode=_boolean(
            transport_raw,
            "routed_by_network_mode",
            context="transport",
        ),
        network_policy=_text(transport_raw, "network_policy", context="transport"),
    )
    for entrypoint in transport.entrypoints:
        candidate = Path(entrypoint)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ContractError(
                f"transport.entrypoints must stay inside the repository: {entrypoint!r}"
            )
        normalized = candidate.as_posix()
        if not (
            (normalized.startswith("src/openbiliclaw/") and candidate.suffix == ".py")
            or (
                normalized.startswith("extension/src/")
                and candidate.suffix in {".ts", ".js", ".mjs"}
            )
        ):
            raise ContractError(
                "transport.entrypoints is fail-closed to implementation source files under "
                f"src/openbiliclaw/ or extension/src/: {entrypoint!r}"
            )
    if transport.kind == "web-http" and schema_version != 2:
        raise ContractError("transport.kind='web-http' requires schema_version=2")
    if transport.kind == "web-http" and host_policy != "recipe-allowlist":
        raise ContractError("transport.kind='web-http' requires host_policy='recipe-allowlist'")
    invalid_route_aliases = [
        alias for alias in transport.route_aliases if ROUTE_KEY_RE.fullmatch(alias) is None
    ]
    if invalid_route_aliases:
        raise ContractError(
            "transport.route_aliases must be ASCII route keys "
            f"([a-z][a-z0-9_-]{{0,63}}): {invalid_route_aliases}"
        )

    identity_raw = _table(raw, "identity")
    identity = IdentityContract(
        item_id=_text(identity_raw, "item_id", context="identity"),
        url=_text(identity_raw, "url", context="identity"),
        dedupe=_text(identity_raw, "dedupe", context="identity"),
        account_scope=_text(identity_raw, "account_scope", context="identity"),
    )

    auth_raw = _table(raw, "auth")
    auth = AuthContract(
        mode=_choice(
            _text(auth_raw, "mode", context="auth"),
            AUTH_MODES,
            field="auth.mode",
        ),
        credential_kinds=_strings(
            auth_raw,
            "credential_kinds",
            context="auth",
            allow_empty=True,
        ),
        verify_action=_choice(
            _text(auth_raw, "verify_action", context="auth"),
            VERIFY_ACTION_VALUES,
            field="auth.verify_action",
        ),
        write_path=_choice(
            _text(auth_raw, "write_path", context="auth"),
            ("unified", "config-only", "none"),
            field="auth.write_path",
        ),
        account_resolution=_text(auth_raw, "account_resolution", context="auth"),
        identity_evidence=_text(auth_raw, "identity_evidence", context="auth"),
        capability_modes=_string_map(auth_raw, "capability_modes", context="auth"),
        capability_required=_bool_map(
            auth_raw,
            "capability_required",
            context="auth",
        ),
        login_cookie_names=_strings(
            auth_raw,
            "login_cookie_names",
            context="auth",
            allow_empty=True,
        ),
        login_state_path=_choice(
            _text(auth_raw, "login_state_path", context="auth"),
            LOGIN_STATE_PATHS,
            field="auth.login_state_path",
        ),
    )
    for capability, mode in auth.capability_modes.items():
        _choice(mode, CAPABILITY_AUTH_MODES, field=f"auth.capability_modes.{capability}")
    capability_key_sets = {
        "transport.capability_routes": set(transport.capability_routes),
        "auth.capability_modes": set(auth.capability_modes),
        "auth.capability_required": set(auth.capability_required),
    }
    if len({frozenset(value) for value in capability_key_sets.values()}) != 1:
        raise ContractError(
            "transport.capability_routes, auth.capability_modes, and auth.capability_required "
            f"must declare exactly the same capability keys: {capability_key_sets}"
        )

    upstream_raw = _table(raw, "upstream")
    upstream = UpstreamContract(
        success_content_types=_strings(
            upstream_raw,
            "success_content_types",
            context="upstream",
        ),
        pagination=_text(upstream_raw, "pagination", context="upstream"),
        terminal_evidence=_text(upstream_raw, "terminal_evidence", context="upstream"),
        terminal_policy=_text(upstream_raw, "terminal_policy", context="upstream"),
        partial_policy=_text(upstream_raw, "partial_policy", context="upstream"),
        publication_time_policy=_text(
            upstream_raw,
            "publication_time_policy",
            context="upstream",
        ),
    )

    discover_raw = _table(raw, "discover")
    discover = DiscoverContract(
        modes=_strings(discover_raw, "modes", context="discover", allow_empty=True),
        search_generation=_text(
            discover_raw,
            "search_generation",
            context="discover",
        ),
        budget=_text(discover_raw, "budget", context="discover"),
        cursor=_text(discover_raw, "cursor", context="discover"),
    )
    profile_raw = _table(raw, "profile")
    profile = ProfileContract(
        signals=_boolean(profile_raw, "signals", context="profile"),
        incremental=_boolean(profile_raw, "incremental", context="profile"),
        refresh_mode=_choice(
            _text(profile_raw, "refresh_mode", context="profile"),
            REFRESH_MODES,
            field="profile.refresh_mode",
        ),
    )
    extension_raw = _table(raw, "extension")
    extension = ExtensionContract(
        task=_choice(
            _text(extension_raw, "task", context="extension"),
            EXTENSION_TASKS,
            field="extension.task",
        ),
        hosts=_strings(extension_raw, "hosts", context="extension", allow_empty=True),
        task_marker=_boolean(extension_raw, "task_marker", context="extension"),
        background=_boolean(extension_raw, "background", context="extension"),
        early_response=_boolean(extension_raw, "early_response", context="extension"),
        cookie_sync=_boolean(extension_raw, "cookie_sync", context="extension"),
    )
    surfaces_raw = _table(raw, "surfaces")
    surfaces = {key: _boolean(surfaces_raw, key, context="surfaces") for key in SURFACE_KEYS}
    engagement_raw = _table(raw, "engagement")
    engagement = {
        key: _choice(
            _text(engagement_raw, key, context="engagement"),
            ENGAGEMENT_AVAILABILITY,
            field=f"engagement.{key}",
        )
        for key in ENGAGEMENT_KEYS
    }
    media_raw = _table(raw, "media")
    media = MediaContract(
        image=_choice(
            _text(media_raw, "image", context="media"),
            IMAGE_MODES,
            field="media.image",
        ),
        image_hosts=_strings(media_raw, "image_hosts", context="media", allow_empty=True),
        deep_link=_choice(
            _text(media_raw, "deep_link", context="media"),
            DEEP_LINK_MODES,
            field="media.deep_link",
        ),
        native_save=_boolean(media_raw, "native_save", context="media"),
    )
    for host in extension.hosts:
        _validate_hostname(host, field="extension.hosts")
        if not any(host == canonical or host.endswith(f".{canonical}") for canonical in hosts):
            raise ContractError(
                f"extension.hosts entry {host!r} must belong to one of canonical hosts {hosts}"
            )
    for host in media.image_hosts:
        _validate_hostname(host, field="media.image_hosts")
    e2e_raw = _table(raw, "e2e")
    e2e = E2EContract(
        safe_actions=_strings(e2e_raw, "safe_actions", context="e2e"),
        mutating_actions=_strings(
            e2e_raw,
            "mutating_actions",
            context="e2e",
            allow_empty=True,
        ),
        safe_assertions=_string_map(
            e2e_raw,
            "safe_assertions",
            context="e2e",
        ),
        safe_postconditions=_string_map(
            e2e_raw,
            "safe_postconditions",
            context="e2e",
        ),
    )
    events_raw = _table(raw, "events")
    events = EventContract(
        strategy_prefixes=_strings(
            events_raw,
            "strategy_prefixes",
            context="events",
            allow_empty=True,
        ),
        mappings=_text(events_raw, "mappings", context="events"),
        scope_caps=_text(events_raw, "scope_caps", context="events"),
    )
    task_raw = _table(raw, "task")
    task = TaskContract(
        lease=_text(task_raw, "lease", context="task"),
        idle_deadline=_text(task_raw, "idle_deadline", context="task"),
        absolute_deadline=_text(task_raw, "absolute_deadline", context="task"),
        retry=_text(task_raw, "retry", context="task"),
        buffer=_text(task_raw, "buffer", context="task"),
    )
    smoke_raw = _table(raw, "smoke")
    smoke = SmokeContract(
        storage_scope=_choice(
            _text(smoke_raw, "storage_scope", context="smoke"),
            ("isolated-only",),
            field="smoke.storage_scope",
        ),
        sinks=_string_map(smoke_raw, "sinks", context="smoke"),
    )
    exclusions_raw = _table(raw, "exclusions")
    exclusions: dict[str, str] = {}
    for key, value in exclusions_raw.items():
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"exclusions.{key} must be a non-empty reason")
        exclusions[str(key)] = value.strip()
    exclusion_tests = _string_list_table(raw, "exclusion_tests")

    if level in {"full", "discovery-only"} and not discover.modes:
        raise ContractError(f"integration_level={level} requires at least one discover mode")
    if level == "full" and not profile.signals:
        raise ContractError("integration_level=full requires profile.signals=true")
    if level == "discovery-only" and profile.signals:
        raise ContractError("integration_level=discovery-only requires profile.signals=false")
    if profile.incremental and not profile.signals:
        raise ContractError("profile.incremental=true requires profile.signals=true")
    if profile.incremental != (profile.refresh_mode == "incremental"):
        raise ContractError(
            "profile.incremental must be true exactly when profile.refresh_mode='incremental'"
        )
    if not profile.signals and profile.refresh_mode != "none":
        raise ContractError("profile.signals=false requires profile.refresh_mode='none'")
    if auth.login_cookie_names and auth.login_state_path == "none":
        raise ContractError(
            "auth.login_cookie_names requires auth.login_state_path='callback' or 'credential'"
        )
    if auth.mode == "login-required" and auth.login_state_path == "none":
        raise ContractError(
            "auth.mode='login-required' requires a callback or credential login_state_path"
        )
    extension_capability_enabled = extension.task != "none" or any(
        (
            extension.task_marker,
            extension.background,
            extension.early_response,
            extension.cookie_sync,
        )
    )
    if extension_capability_enabled and not extension.hosts:
        raise ContractError(
            "extension.hosts must not be empty when any extension capability is enabled"
        )
    if extension.task == "none" and any(
        (extension.task_marker, extension.background, extension.early_response)
    ):
        raise ContractError(
            "task_marker/background/early_response must be false when extension.task='none'; "
            "cookie_sync is independent and may remain enabled"
        )
    if auth.verify_action == "browser_heartbeat" and (
        not extension.cookie_sync
        or auth.login_state_path != "callback"
        or not auth.login_cookie_names
    ):
        raise ContractError(
            "auth.verify_action='browser_heartbeat' requires extension.cookie_sync=true, "
            "auth.login_state_path='callback', and at least one real login cookie name"
        )
    if media.image == "proxy" and not media.image_hosts:
        raise ContractError("media.image_hosts must not be empty when media.image='proxy'")
    overlap = set(e2e.safe_actions) & set(e2e.mutating_actions)
    if overlap:
        raise ContractError(f"e2e safe_actions and mutating_actions overlap: {sorted(overlap)}")
    unknown_safe_actions = sorted(
        action
        for action in e2e.safe_actions
        if not action.isascii() or SAFE_ACTION_RE.fullmatch(action) is None
    )
    unsafe_safe_actions = sorted(
        action for action in unknown_safe_actions if _action_tokens(action) & KNOWN_MUTATING_ACTIONS
    )
    if unsafe_safe_actions:
        raise ContractError(
            "e2e.safe_actions contains known upstream mutators; move them to "
            f"e2e.mutating_actions: {unsafe_safe_actions}"
        )
    remaining_unknown_safe_actions = sorted(set(unknown_safe_actions) - set(unsafe_safe_actions))
    if remaining_unknown_safe_actions:
        raise ContractError(
            "e2e.safe_actions is fail-closed: use a known read-only action or move the "
            "platform-specific/unknown action to e2e.mutating_actions: "
            f"{remaining_unknown_safe_actions}"
        )
    safe_action_keys = set(e2e.safe_actions)
    safe_assertion_keys = set(e2e.safe_assertions)
    if safe_assertion_keys != safe_action_keys:
        missing = sorted(safe_action_keys - safe_assertion_keys)
        extra = sorted(safe_assertion_keys - safe_action_keys)
        raise ContractError(
            "e2e.safe_assertions must have exactly one machine assertion per safe action; "
            f"missing={missing}, extra={extra}"
        )
    invalid_safe_assertions = sorted(
        action
        for action, assertion in e2e.safe_assertions.items()
        if assertion != "upstream-state-unchanged"
    )
    if invalid_safe_assertions:
        raise ContractError(
            "e2e.safe_assertions values must be exactly 'upstream-state-unchanged'; "
            f"invalid={invalid_safe_assertions}"
        )
    safe_postcondition_keys = set(e2e.safe_postconditions)
    if safe_postcondition_keys != safe_action_keys:
        missing = sorted(safe_action_keys - safe_postcondition_keys)
        extra = sorted(safe_postcondition_keys - safe_action_keys)
        raise ContractError(
            "e2e.safe_postconditions must have exactly one non-empty entry per safe action; "
            f"missing={missing}, extra={extra}"
        )
    if media.native_save and not any(
        "save" in _action_tokens(action) for action in e2e.mutating_actions
    ):
        raise ContractError(
            "media.native_save=true requires an explicit save/platform-save entry in "
            "e2e.mutating_actions"
        )
    smoke_sink_keys = set(smoke.sinks)
    expected_smoke_sink_keys = set(SMOKE_SINK_KEYS)
    if smoke_sink_keys != expected_smoke_sink_keys:
        raise ContractError(
            "smoke.sinks must classify every projection sink exactly once; "
            f"missing={sorted(expected_smoke_sink_keys - smoke_sink_keys)}, "
            f"extra={sorted(smoke_sink_keys - expected_smoke_sink_keys)}"
        )
    invalid_smoke_sinks = sorted(
        key for key, policy in smoke.sinks.items() if policy not in SMOKE_SINK_POLICIES
    )
    if invalid_smoke_sinks:
        raise ContractError(
            f"smoke.sinks values must be allowed or forbidden; invalid={invalid_smoke_sinks}"
        )
    derived_smoke_writes = sorted(
        key
        for key in SMOKE_SINK_KEYS
        if key not in {"task", "task_result"} and smoke.sinks[key] == "allowed"
    )
    if derived_smoke_writes:
        raise ContractError(
            "smoke_only may write only diagnostic task/task_result records; derived "
            f"projection sinks must be forbidden: {derived_smoke_writes}"
        )
    expected_task_policy = "allowed" if extension.task == "browser-task" else "forbidden"
    if any(smoke.sinks[key] != expected_task_policy for key in ("task", "task_result")):
        raise ContractError(
            "smoke.sinks task/task_result must be allowed exactly for "
            f"extension.task='browser-task' (expected {expected_task_policy})"
        )

    active_capabilities = _active_capabilities(discover, profile, extension, media)
    for field, declared in (
        ("transport.capability_routes", transport.capability_routes),
        ("auth.capability_modes", auth.capability_modes),
    ):
        declared_capabilities = set(declared)
        if declared_capabilities != active_capabilities:
            missing_capabilities = sorted(active_capabilities - declared_capabilities)
            extra_capabilities = sorted(declared_capabilities - active_capabilities)
            raise ContractError(
                f"{field} must exactly match enabled capabilities; "
                f"missing={missing_capabilities}, extra={extra_capabilities}"
            )
    required_capabilities = {
        capability for capability, required in auth.capability_required.items() if required
    }
    intrinsically_required = set()
    if discover.modes:
        intrinsically_required.add("discover")
    if profile.signals:
        intrinsically_required.update(("profile", "bootstrap"))
    if profile.incremental:
        intrinsically_required.add("incremental")
    if media.native_save:
        intrinsically_required.add("native-save")
    missing_required = sorted(intrinsically_required - required_capabilities)
    if missing_required:
        raise ContractError(
            "auth.capability_required cannot demote an enabled core product capability: "
            f"{missing_required}"
        )
    browser_task_routes = [
        capability
        for capability, route in transport.capability_routes.items()
        if "browser-task" in re.split(r"[^a-z0-9_-]+", route.lower())
    ]
    if browser_task_routes and extension.task != "browser-task":
        raise ContractError(
            "transport.capability_routes declares browser-task ownership while "
            f"extension.task={extension.task!r}: {sorted(browser_task_routes)}"
        )
    if extension.task == "browser-task" and not browser_task_routes:
        raise ContractError(
            "extension.task='browser-task' requires at least one capability route owned by "
            "browser-task"
        )
    required_modes = {
        auth.capability_modes[capability]
        for capability, required in auth.capability_required.items()
        if required
    }
    if not required_modes:
        raise ContractError("auth.capability_required must mark at least one capability required")
    if "login-required" in required_modes and auth.login_state_path == "none":
        raise ContractError(
            "every required login-required capability needs a callback or credential "
            "login_state_path"
        )
    if auth.mode == "anonymous" and required_modes != {"anonymous"}:
        raise ContractError(
            "auth.mode='anonymous' requires every required capability to use mode='anonymous'"
        )
    if auth.mode == "login-required" and required_modes != {"login-required"}:
        raise ContractError(
            "auth.mode='login-required' requires every required capability to be login-required"
        )
    if auth.mode == "anonymous-with-optional-credentials" and (
        "login-required" in required_modes or "optional-credential" not in required_modes
    ):
        raise ContractError(
            "auth.mode='anonymous-with-optional-credentials' requires at least one required "
            "optional-credential capability and no required login-required capability"
        )
    has_public_and_login = "login-required" in required_modes and bool(
        required_modes & {"anonymous", "optional-credential"}
    )
    if has_public_and_login and auth.mode != "capability-specific":
        raise ContractError(
            "mixed required public/login-required capabilities require "
            "auth.mode='capability-specific'"
        )
    if auth.mode == "capability-specific" and not has_public_and_login:
        raise ContractError(
            "auth.mode='capability-specific' requires genuinely mixed required public and "
            "login-required capability modes"
        )

    _require_exclusion(exclusions, "discover.formal", not discover.modes)
    _require_exclusion(exclusions, "search.integration", "search" not in discover.modes)
    _require_exclusion(exclusions, "profile.signals", not profile.signals)
    _require_exclusion(exclusions, "profile.incremental", not profile.incremental)
    _require_exclusion(exclusions, "profile.refresh-mode", profile.refresh_mode == "none")
    _require_exclusion(exclusions, "extension.task", extension.task == "none")
    _require_exclusion(exclusions, "extension.task-marker", not extension.task_marker)
    _require_exclusion(exclusions, "extension.background", not extension.background)
    _require_exclusion(exclusions, "extension.early-response", not extension.early_response)
    _require_exclusion(exclusions, "extension.cookie-sync", not extension.cookie_sync)
    _require_exclusion(exclusions, "media.image", media.image == "none")
    _require_exclusion(exclusions, "media.deep-link", media.deep_link != "native")
    _require_exclusion(exclusions, "media.native-save", not media.native_save)
    for surface, enabled in surfaces.items():
        _require_exclusion(exclusions, f"surface.{surface}", not enabled)
    for metric, availability in engagement.items():
        _require_exclusion(
            exclusions,
            f"engagement.{metric}",
            availability == "unavailable",
        )

    emitted_n_a: set[str] = set()
    conditions = {
        "discover.formal": not discover.modes,
        "search.integration": "search" not in discover.modes,
        "profile.signals": not profile.signals,
        "profile.incremental": not profile.incremental,
        "profile.refresh-mode": profile.refresh_mode == "none",
        "extension.task": extension.task == "none",
        "extension.task-marker": not extension.task_marker,
        "extension.background": not extension.background,
        "extension.early-response": not extension.early_response,
        "extension.cookie-sync": not extension.cookie_sync,
        "media.image": media.image == "none",
        "media.deep-link": media.deep_link != "native",
        "media.native-save": not media.native_save,
    }
    emitted_n_a.update(capability for capability, enabled in conditions.items() if enabled)
    emitted_n_a.update(f"surface.{surface}" for surface, enabled in surfaces.items() if not enabled)
    emitted_n_a.update(
        f"engagement.{metric}"
        for metric, availability in engagement.items()
        if availability == "unavailable"
    )
    exclusion_keys = set(exclusions)
    if exclusion_keys != emitted_n_a:
        raise ContractError(
            "[exclusions] keys must exactly match capabilities that emit N/A; "
            f"missing={sorted(emitted_n_a - exclusion_keys)}, "
            f"stale={sorted(exclusion_keys - emitted_n_a)}"
        )
    exclusion_test_keys = set(exclusion_tests)
    if exclusion_test_keys != emitted_n_a:
        raise ContractError(
            "[exclusion_tests] keys must exactly match capabilities that emit N/A; "
            f"missing={sorted(emitted_n_a - exclusion_test_keys)}, "
            f"stale={sorted(exclusion_test_keys - emitted_n_a)}"
        )
    for capability in sorted(emitted_n_a):
        if not exclusion_tests[capability]:
            raise ContractError(f"{capability} needs at least one exact exclusion test reference")

    return PlatformContract(
        schema_version=schema_version,
        canonical_slug=slug,
        display_name=display_name,
        integration_level=level,
        host_policy=host_policy,
        aliases=aliases,
        hosts=hosts,
        content_types=content_types,
        transport=transport,
        identity=identity,
        auth=auth,
        upstream=upstream,
        discover=discover,
        profile=profile,
        extension=extension,
        surfaces=surfaces,
        engagement=engagement,
        media=media,
        e2e=e2e,
        events=events,
        task=task,
        smoke=smoke,
        exclusions=exclusions,
        exclusion_tests=exclusion_tests,
    )


def find_repo_root(contract_path: Path) -> Path:
    """Find the fixture or real repository containing *contract_path*."""
    for candidate in (contract_path.parent, *contract_path.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src").is_dir():
            return candidate
    raise ContractError(
        f"cannot find repository root above {contract_path}; expected pyproject.toml and src/"
    )


class Inventory:
    """Concrete file/line probes for one source contract."""

    def __init__(
        self,
        root: Path,
        contract: PlatformContract,
        changed_files: set[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.contract = contract
        self.changed_files = changed_files
        self._text_cache: dict[str, tuple[Path, str] | None] = {}
        self._source_files_cache: tuple[str, ...] | None = None
        self._producer_files_cache: tuple[str, ...] | None = None
        self._source_test_cache: dict[str, Evidence | None] = {}
        self._family_rule_cache: dict[tuple[str, str], Evidence] | None = None
        self._family_value_owner_cache: dict[tuple[str, str], set[str]] | None = None
        self._test_reference_cache: dict[tuple[str, str], Evidence | None] = {}
        self._validate_family_ownership()

    @staticmethod
    def _path_key_match(stem: str, key: str) -> bool:
        normalized_stem = stem.lower().replace("-", "_")
        normalized_key = key.lower().replace("-", "_")
        return (
            re.search(
                rf"(?:^|_){re.escape(normalized_key)}(?:_|$)",
                normalized_stem,
            )
            is not None
        )

    def _route_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys((self.contract.canonical_slug, *self.contract.transport.route_aliases))
        )

    def _uses_dynamic_workflows(self) -> bool:
        """Select the portable workflow registry by declared implementation seams.

        Instance IDs are allocated at runtime, so a fixed canonical-family key
        in a planner/auth dictionary cannot prove (or disprove) their wiring.
        This adapter only applies when the contract names both shared seams;
        each check below still requires concrete implementation evidence.
        """
        return {
            "src/openbiliclaw/sources/workflow_service.py",
            "src/openbiliclaw/runtime/custom_workflow_producer.py",
        }.issubset(self.contract.transport.entrypoints)

    def _evidence(self, path: Path, line: int, excerpt: str) -> Evidence:
        rel = path.relative_to(self.root).as_posix()
        changed = None if self.changed_files is None else rel in self.changed_files
        # Registration evidence needs the repository-relative path and line,
        # never the source text.  Even an implementation file can contain a
        # developer token, fixture credential, private URL, or account ID; a
        # denylist cannot make arbitrary excerpts safe for JSON/CI output.
        _ = excerpt
        return Evidence(rel, line, "<source excerpt omitted>", changed)

    def _text(self, relative: str) -> tuple[Path, str] | None:
        if relative in self._text_cache:
            return self._text_cache[relative]
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ContractError(
                f"repository path must be relative and traversal-free: {relative!r}"
            )
        path = (self.root / candidate).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ContractError(
                f"repository path resolves outside the repository (including symlinks): {relative!r}"
            ) from exc
        if not path.is_file():
            self._text_cache[relative] = None
            return None
        try:
            loaded = (path, path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            self._text_cache[relative] = None
            return None
        self._text_cache[relative] = loaded
        return loaded

    def _family_value_owners(self) -> dict[tuple[str, str], set[str]]:
        """Return exact owners from direct entries in ``SOURCE_FAMILY_RULES``."""
        if self._family_value_owner_cache is not None:
            return self._family_value_owner_cache
        loaded = self._text("src/openbiliclaw/sources/platforms.py")
        if loaded is None:
            self._family_value_owner_cache = {}
            return self._family_value_owner_cache
        _, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            self._family_value_owner_cache = {}
            return self._family_value_owner_cache
        constants: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            constants[target.id] = node.value.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.target.id] = node.value.value

        def string_value(node: ast.expr) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constants.get(node.id)
            return None

        rules: ast.expr | None = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SOURCE_FAMILY_RULES"
                for target in node.targets
            ):
                rules = node.value
                break
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "SOURCE_FAMILY_RULES"
            ):
                rules = node.value
                break
        if not isinstance(rules, (ast.Tuple, ast.List)):
            self._family_value_owner_cache = {}
            return self._family_value_owner_cache

        owners: dict[tuple[str, str], set[str]] = {}
        for walked in rules.elts:
            if not isinstance(walked, ast.Call) or self._call_name(walked.func) != (
                "SourceFamilyRule"
            ):
                continue
            keywords = {keyword.arg: keyword.value for keyword in walked.keywords if keyword.arg}
            family_node = keywords.get("family")
            if family_node is None:
                continue
            family = string_value(family_node)
            if family is None:
                continue
            for field in ("platform_aliases", "url_hosts", "source_prefixes"):
                field_node = keywords.get(field)
                if field_node is None:
                    continue
                for child in ast.walk(field_node):
                    if not isinstance(child, ast.expr):
                        continue
                    value = string_value(child)
                    if value is not None:
                        owners.setdefault((field, value), set()).add(family)
        self._family_value_owner_cache = owners
        return owners

    def _validate_family_ownership(self) -> None:
        owners = self._family_value_owners()
        expected: list[tuple[str, str]] = [
            ("platform_aliases", self.contract.canonical_slug),
            *(("platform_aliases", value) for value in self.contract.aliases),
            *(("platform_aliases", value) for value in self.contract.transport.route_aliases),
            *(("url_hosts", value) for value in self.contract.hosts),
            *(("source_prefixes", value) for value in self.contract.events.strategy_prefixes),
        ]
        for field, value in expected:
            value_owners = owners.get((field, value), set())
            # A pre-implementation audit is supposed to turn an entirely new
            # family (or one missing registration row) into canonical.registry
            # MISSING.  Ownership validation is only the fail-fast guard for a
            # value already claimed by another family or by multiple families.
            if not value_owners:
                continue
            if value_owners != {self.contract.canonical_slug}:
                raise ContractError(
                    f"{field} value {value!r} must belong only to SourceFamilyRule "
                    f"family={self.contract.canonical_slug!r}; owners={sorted(value_owners)}"
                )

    def _file(self, relative: str) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        first = source.splitlines()[0] if source.splitlines() else "<empty file>"
        return self._evidence(path, 1, first)

    def _regex(self, relative: str, pattern: str, flags: int = 0) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        match = re.search(pattern, source, flags)
        if match is None:
            return None
        line = source.count("\n", 0, match.start()) + 1
        excerpt = source.splitlines()[line - 1]
        return self._evidence(path, line, excerpt)

    def _word(self, relative: str, value: str, *, ignore_case: bool = False) -> Evidence | None:
        flags = re.IGNORECASE if ignore_case else 0
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])"
        return self._regex(relative, pattern, flags)

    def _literal(self, relative: str, value: str) -> Evidence | None:
        return self._regex(relative, rf"[\"']{re.escape(value)}[\"']")

    def _first_word(self, relatives: tuple[str, ...], value: str) -> Evidence | None:
        for relative in relatives:
            found = self._word(relative, value)
            if found is not None:
                return found
        return None

    def _first_file(self, relatives: tuple[str, ...]) -> Evidence | None:
        for relative in relatives:
            found = self._file(relative)
            if found is not None:
                return found
        return None

    def _first_key_word(
        self,
        relatives: tuple[str, ...],
        keys: tuple[str, ...],
    ) -> Evidence | None:
        for key in keys:
            found = self._first_word(relatives, key)
            if found is not None:
                return found
        return None

    def _first_regex(self, relatives: tuple[str, ...], pattern: str) -> Evidence | None:
        for relative in relatives:
            found = self._regex(relative, pattern, re.MULTILINE)
            if found is not None:
                return found
        return None

    def _assignment_value(self, relative: str, name: str, value: str) -> Evidence | None:
        assigned = self._named_assignment(relative, name)
        if assigned is None:
            return None
        path, source, node = assigned
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and child.value == value:
                line = int(getattr(child, "lineno", getattr(node, "lineno", 1)))
                return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _assignment_key(self, relative: str, name: str, key: str) -> Evidence | None:
        assigned = self._named_assignment(relative, name)
        if assigned is None:
            return None
        path, source, node = assigned
        if not isinstance(node, ast.Dict):
            return None
        for dict_key in node.keys:
            if isinstance(dict_key, ast.Constant) and dict_key.value == key:
                line = int(getattr(dict_key, "lineno", getattr(node, "lineno", 1)))
                return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _assignment_dict_value(
        self,
        relative: str,
        name: str,
        key: str,
        value: str,
    ) -> Evidence | None:
        """Find an exact string value for *key* in one named Python dict."""
        assigned = self._named_assignment(relative, name)
        if assigned is None:
            return None
        path, source, node = assigned
        if not isinstance(node, ast.Dict):
            return None
        for dict_key, dict_value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(dict_key, ast.Constant)
                and dict_key.value == key
                and isinstance(dict_value, ast.Constant)
                and dict_value.value == value
            ):
                line = int(getattr(dict_value, "lineno", getattr(node, "lineno", 1)))
                return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _named_assignment(self, relative: str, name: str) -> tuple[Path, str, ast.expr] | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets
            ):
                return path, source, node.value
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                return path, source, node.value
        return None

    def _function_assignment_value(
        self,
        relative: str,
        function_name: str,
        assignment_name: str,
        value: str,
    ) -> Evidence | None:
        """Find a literal in a direct assignment owned by one top-level function."""
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            ),
            None,
        )
        if function is None:
            return None
        for node in function.body:
            assigned: ast.expr | None = None
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == assignment_name
                    for target in node.targets
                )
                or (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == assignment_name
                )
            ):
                assigned = node.value
            if assigned is None:
                continue
            for child in ast.walk(assigned):
                if isinstance(child, ast.Constant) and child.value == value:
                    line = int(getattr(child, "lineno", getattr(node, "lineno", 1)))
                    return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _dict_string(self, relative: str, name: str, key: str) -> tuple[str, Evidence] | None:
        assigned = self._named_assignment(relative, name)
        if assigned is None:
            return None
        path, source, node = assigned
        if not isinstance(node, ast.Dict):
            return None
        for item_key, item_value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(item_key, ast.Constant)
                and item_key.value == key
                and isinstance(item_value, ast.Constant)
                and isinstance(item_value.value, str)
                and item_value.value
            ):
                line = int(getattr(item_value, "lineno", getattr(node, "lineno", 1)))
                return item_value.value, self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _dict_tuple_contains(
        self, relative: str, name: str, key: str, value: str
    ) -> Evidence | None:
        assigned = self._named_assignment(relative, name)
        if assigned is None:
            return None
        path, source, node = assigned
        if not isinstance(node, ast.Dict):
            return None
        for item_key, item_value in zip(node.keys, node.values, strict=True):
            if not isinstance(item_key, ast.Constant) or item_key.value != key:
                continue
            for child in ast.walk(item_value):
                if isinstance(child, ast.Constant) and child.value == value:
                    line = int(getattr(child, "lineno", getattr(node, "lineno", 1)))
                    return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _bootstrap_record(self) -> tuple[str, str, str, Evidence] | None:
        relative = "src/openbiliclaw/sources/source_bootstrap.py"
        assigned = self._named_assignment(relative, "_BOOTSTRAP_TASK_TABLES")
        if assigned is None:
            return None
        path, source, node = assigned
        keys = set(self._route_keys())
        for item in getattr(node, "elts", []):
            if not isinstance(item, (ast.Tuple, ast.List)) or len(item.elts) != 3:
                continue
            values = [
                child.value if isinstance(child, ast.Constant) else None for child in item.elts
            ]
            scheduler, table, task_type = values
            if (
                isinstance(scheduler, str)
                and scheduler in keys
                and isinstance(table, str)
                and table == f"{scheduler}_tasks"
                and isinstance(task_type, str)
                and task_type
            ):
                return (
                    scheduler,
                    table,
                    task_type,
                    self._evidence(path, item.lineno, source.splitlines()[item.lineno - 1]),
                )
        return None

    def _function_return_dict_key(
        self, relative: str, function_name: str, key: str
    ) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or (
                function.name != function_name
            ):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                    continue
                for item in node.value.keys:
                    if isinstance(item, ast.Constant) and item.value == key:
                        return self._evidence(
                            path, item.lineno, source.splitlines()[item.lineno - 1]
                        )
        return None

    def _task_table_member(self, table: str) -> Evidence | None:
        relative = "src/openbiliclaw/sources/task_result_protocol.py"
        assigned = self._named_assignment(relative, "_TASK_TABLES")
        if assigned is None:
            return None
        path, source, node = assigned
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and child.value == table:
                line = int(getattr(child, "lineno", getattr(node, "lineno", 1)))
                return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _task_spec(self, source_key: str, table: str, task_type: str) -> Evidence | None:
        relative = "src/openbiliclaw/runtime/source_incremental_sync.py"
        assigned = self._named_assignment(relative, "_TASK_SPECS")
        if assigned is None:
            return None
        path, source, node = assigned
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        callable_names: set[str] = set()
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callable_names.add(item.name)
            elif isinstance(item, (ast.Import, ast.ImportFrom)):
                callable_names.update(
                    alias.asname or alias.name.rsplit(".", 1)[-1] for alias in item.names
                )
        if not isinstance(node, ast.Dict):
            return None
        for item_key, item_value in zip(node.keys, node.values, strict=True):
            if not isinstance(item_key, ast.Constant) or item_key.value != source_key:
                continue
            if not isinstance(item_value, ast.Tuple) or len(item_value.elts) != 3:
                return None
            first, second, enqueue = item_value.elts
            if (
                isinstance(first, ast.Constant)
                and first.value == table
                and isinstance(second, ast.Constant)
                and second.value == task_type
                and isinstance(enqueue, (ast.Name, ast.Attribute))
                and (isinstance(enqueue, ast.Attribute) or enqueue.id in callable_names)
            ):
                return self._evidence(
                    path, item_value.lineno, source.splitlines()[item_value.lineno - 1]
                )
        return None

    @staticmethod
    def _call_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    def _family_rule_values(self) -> dict[tuple[str, str], Evidence]:
        """Return values belonging to this slug's single SourceFamilyRule AST node."""
        if self._family_rule_cache is not None:
            return self._family_rule_cache
        relative = "src/openbiliclaw/sources/platforms.py"
        loaded = self._text(relative)
        if loaded is None:
            self._family_rule_cache = {}
            return self._family_rule_cache
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            self._family_rule_cache = {}
            return self._family_rule_cache

        constants: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if not isinstance(node.value.value, str):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.target.id] = node.value.value

        def string_value(node: ast.expr) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constants.get(node.id)
            return None

        rules: ast.expr | None = None
        for top_level in tree.body:
            if isinstance(top_level, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SOURCE_FAMILY_RULES"
                for target in top_level.targets
            ):
                rules = top_level.value
                break
            if (
                isinstance(top_level, ast.AnnAssign)
                and isinstance(top_level.target, ast.Name)
                and top_level.target.id == "SOURCE_FAMILY_RULES"
            ):
                rules = top_level.value
                break
        if not isinstance(rules, (ast.Tuple, ast.List)):
            self._family_rule_cache = {}
            return self._family_rule_cache

        values: dict[tuple[str, str], Evidence] = {}
        for node in rules.elts:
            if not isinstance(node, ast.Call) or self._call_name(node.func) != "SourceFamilyRule":
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            family_node = keywords.get("family")
            if family_node is None or string_value(family_node) != self.contract.canonical_slug:
                continue
            family_line = int(getattr(family_node, "lineno", getattr(node, "lineno", 1)))
            values[("family", self.contract.canonical_slug)] = self._evidence(
                path,
                family_line,
                source.splitlines()[family_line - 1],
            )
            for field in ("platform_aliases", "url_hosts", "source_prefixes"):
                field_node = keywords.get(field)
                if field_node is None:
                    continue
                for child in ast.walk(field_node):
                    if not isinstance(child, ast.expr):
                        continue
                    item = string_value(child)
                    if item is None:
                        continue
                    line = int(getattr(child, "lineno", getattr(field_node, "lineno", 1)))
                    values[(field, item)] = self._evidence(
                        path,
                        line,
                        source.splitlines()[line - 1],
                    )
            for field in ("requires_overseas_network", "routed_by_network_mode"):
                field_node = keywords.get(field)
                if field_node is None:
                    actual = False
                    line = int(getattr(node, "lineno", 1))
                elif isinstance(field_node, ast.Constant) and isinstance(field_node.value, bool):
                    actual = field_node.value
                    line = int(getattr(field_node, "lineno", getattr(node, "lineno", 1)))
                else:
                    continue
                values[(field, str(actual).lower())] = self._evidence(
                    path,
                    line,
                    source.splitlines()[line - 1],
                )
            break
        self._family_rule_cache = values
        return values

    def _family_rule_value(self, field: str, value: str) -> Evidence | None:
        return self._family_rule_values().get((field, value))

    def _section_literal(self, relative: str, anchor: str, value: str) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        start = source.find(anchor)
        if start < 0:
            return None
        end = source.find(";", start)
        if end < 0:
            end = min(len(source), start + 12_000)
        section = source[start : end + 1]
        match = re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])",
            section,
        )
        if match is None:
            return None
        offset = start + match.start()
        line = source.count("\n", 0, offset) + 1
        return self._evidence(path, line, source.splitlines()[line - 1])

    def _pass_or_missing(
        self,
        capability: str,
        label: str,
        requirements: list[tuple[str, Evidence | None]],
        *,
        required: bool = True,
    ) -> AuditResult:
        evidence = tuple(item for _, item in requirements if item is not None)
        missing = [name for name, item in requirements if item is None]
        if missing:
            return AuditResult(
                capability,
                label,
                "MISSING",
                required,
                "missing concrete registration: " + "; ".join(missing),
                evidence,
            )
        return AuditResult(
            capability,
            label,
            "PASS",
            required,
            "all declared concrete registration points were found",
            evidence,
        )

    @staticmethod
    def _capability_test_tokens(capability: str) -> tuple[str, ...]:
        leaf = capability.rsplit(".", 1)[-1]
        return tuple(token for token in re.split(r"[^a-z0-9]+", leaf.lower()) if token)

    @staticmethod
    def _node_has_skip_marker(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in {"skip", "skipif", "xfail"}:
                return True
            if isinstance(child, ast.Name) and child.id in {"skip", "skipif", "xfail"}:
                return True
        return False

    @staticmethod
    def _node_has_assertion(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                return True
            if not isinstance(child, ast.Call):
                continue
            called = Inventory._call_name(child.func)
            if called.startswith("assert") or called in {"raises", "fail"}:
                return True
        return False

    def _test_reference_evidence(self, reference: str, capability: str) -> Evidence | None:
        cache_key = (reference, capability)
        if cache_key in self._test_reference_cache:
            return self._test_reference_cache[cache_key]
        relative, test_name = reference.split("::", 1)
        loaded = self._text(relative)
        if loaded is None:
            self._test_reference_cache[cache_key] = None
            return None
        path, source = loaded
        required_tokens = self._capability_test_tokens(capability)
        if path.suffix == ".py":
            try:
                tree = ast.parse(source)
            except SyntaxError:
                self._test_reference_cache[cache_key] = None
                return None
            module_skipped = any(
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "pytestmark"
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
                and self._node_has_skip_marker(node)
                for node in tree.body
            )
            if module_skipped:
                self._test_reference_cache[cache_key] = None
                return None
            lines = source.splitlines()
            for node in tree.body:
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == test_name
                ):
                    if self._node_has_skip_marker(node):
                        self._test_reference_cache[cache_key] = None
                        return None
                    end = int(getattr(node, "end_lineno", node.lineno))
                    block = "\n".join(lines[node.lineno - 1 : end]).lower()
                    normalized = re.sub(r"[^a-z0-9]+", " ", block)
                    source_keys = (
                        self.contract.canonical_slug,
                        *self.contract.aliases,
                    )
                    source_bound = any(
                        self._path_key_match(path.stem, key) or self._path_key_match(test_name, key)
                        for key in source_keys
                    ) or (
                        re.search(
                            rf"\b{re.escape(self.contract.canonical_slug.lower())}\b",
                            normalized,
                        )
                        is not None
                    )
                    if not source_bound or not self._node_has_assertion(node):
                        self._test_reference_cache[cache_key] = None
                        return None
                    if any(
                        re.search(rf"\b{re.escape(token)}\b", normalized) is None
                        for token in required_tokens
                    ):
                        self._test_reference_cache[cache_key] = None
                        return None
                    evidence = self._evidence(
                        path,
                        node.lineno,
                        lines[node.lineno - 1],
                    )
                    self._test_reference_cache[cache_key] = evidence
                    return evidence
            self._test_reference_cache[cache_key] = None
            return None
        offset = self._javascript_asserting_test_offset(source, test_name=test_name)
        if offset is None:
            self._test_reference_cache[cache_key] = None
            return None
        normalized = re.sub(r"[^a-z0-9]+", " ", test_name.lower())
        source_keys = (self.contract.canonical_slug, *self.contract.aliases)
        source_bound = any(
            self._path_key_match(path.stem, key) or self._path_key_match(test_name, key)
            for key in source_keys
        ) or (
            re.search(
                rf"\b{re.escape(self.contract.canonical_slug.lower())}\b",
                normalized,
            )
            is not None
        )
        if not source_bound:
            self._test_reference_cache[cache_key] = None
            return None
        if any(
            re.search(rf"\b{re.escape(token)}\b", normalized) is None for token in required_tokens
        ):
            self._test_reference_cache[cache_key] = None
            return None
        line = source.count("\n", 0, offset) + 1
        evidence = self._evidence(path, line, source.splitlines()[line - 1])
        self._test_reference_cache[cache_key] = evidence
        return evidence

    def _n_a(
        self,
        capability: str,
        label: str,
        origin: str,
        exclusion: str = "",
    ) -> AuditResult:
        reason = exclusion or self.contract.exclusions.get(capability, "")
        references = self.contract.exclusion_tests.get(capability, ())
        resolved = [
            (reference, self._test_reference_evidence(reference, capability))
            for reference in references
        ]
        evidence = tuple(item for _, item in resolved if item is not None)
        unresolved = [reference for reference, item in resolved if item is None]
        if not reason or not references or unresolved:
            missing: list[str] = []
            if not reason:
                missing.append("non-empty exclusion reason")
            if not references:
                missing.append("exclusion test reference")
            missing.extend(f"resolvable exact test {reference}" for reference in unresolved)
            return AuditResult(
                capability,
                label,
                "MISSING",
                True,
                "N/A gate missing: " + "; ".join(missing),
                evidence,
            )
        detail = (
            f"contract explicitly declares {origin}; exclusion: {reason}; exact test node exists, "
            "but this inventory does not execute it—record executed evidence in the acceptance ledger"
        )
        return AuditResult(capability, label, "N/A", False, detail, evidence)

    def _source_files(self) -> tuple[str, ...]:
        if self._source_files_cache is not None:
            return self._source_files_cache
        base = self.root / "src/openbiliclaw/sources"
        if not base.is_dir():
            return ()
        keys = (self.contract.canonical_slug, *self.contract.aliases)
        content_keys = (
            self.contract.canonical_slug,
            *(alias for alias in self.contract.aliases if len(alias) >= 3),
        )
        matches: list[str] = []
        for path in sorted(base.glob("*.py")):
            relative = path.relative_to(self.root).as_posix()
            if any(self._path_key_match(path.stem, key) for key in keys):
                matches.append(relative)
                continue
            loaded = self._text(relative)
            if loaded is None:
                continue
            _, source = loaded
            if any(re.search(rf"[\"']{re.escape(key)}[\"']", source) for key in content_keys):
                matches.append(relative)
        self._source_files_cache = tuple(matches)
        return self._source_files_cache

    def _producer_files(self) -> tuple[str, ...]:
        if self._producer_files_cache is not None:
            return self._producer_files_cache
        base = self.root / "src/openbiliclaw/runtime"
        if not base.is_dir():
            return ()
        keys = (self.contract.canonical_slug, *self.contract.aliases)
        content_keys = (
            self.contract.canonical_slug,
            *(alias for alias in self.contract.aliases if len(alias) >= 3),
        )
        matches: list[str] = []
        for path in sorted(base.glob("*producer.py")):
            relative = path.relative_to(self.root).as_posix()
            loaded = self._text(relative)
            if loaded is None:
                continue
            _, source = loaded
            if (
                relative in self.contract.transport.entrypoints
                or any(self._path_key_match(path.stem, key) for key in keys)
                or any(re.search(rf"[\"']{re.escape(key)}[\"']", source) for key in content_keys)
            ):
                matches.append(relative)
        self._producer_files_cache = tuple(matches)
        return self._producer_files_cache

    @staticmethod
    def _strip_javascript_comments(source: str) -> str:
        """Replace JS comments with spaces while preserving offsets and strings."""
        chars = list(source)
        index = 0
        quote = ""
        escaped = False
        while index < len(chars):
            char = chars[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                index += 1
                continue
            if char in {'"', "'", "`"}:
                quote = char
                index += 1
                continue
            if char == "/" and index + 1 < len(chars) and chars[index + 1] == "/":
                end = source.find("\n", index + 2)
                end = len(chars) if end < 0 else end
                for offset in range(index, end):
                    chars[offset] = " "
                index = end
                continue
            if char == "/" and index + 1 < len(chars) and chars[index + 1] == "*":
                end = source.find("*/", index + 2)
                end = len(chars) - 2 if end < 0 else end
                for offset in range(index, min(end + 2, len(chars))):
                    if chars[offset] != "\n":
                        chars[offset] = " "
                index = min(end + 2, len(chars))
                continue
            index += 1
        return "".join(chars)

    @staticmethod
    def _strip_javascript_strings(source: str) -> str:
        """Replace quoted/template literal contents with spaces, preserving offsets."""
        chars = list(source)
        quote = ""
        escaped = False
        for index, char in enumerate(source):
            if quote:
                if char != "\n":
                    chars[index] = " "
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {'"', "'", "`"}:
                quote = char
                chars[index] = " "
        return "".join(chars)

    @classmethod
    def _javascript_asserting_test_offset(
        cls,
        source: str,
        required_token: str = "",
        test_name: str = "",
    ) -> int | None:
        """Return an asserting ``test/it`` call offset, binding proof to that call body."""
        cleaned = cls._strip_javascript_comments(source)
        has_node_assert = (
            re.search(
                r"\bimport\s+assert\s+from\s+([\"'])node:assert(?:/strict)?\1",
                cleaned,
            )
            is not None
            or re.search(
                r"\b(?:const|let|var)\s+assert\s*=\s*require\(\s*([\"'])"
                r"(?:node:)?assert(?:/strict)?\1\s*\)",
                cleaned,
            )
            is not None
        )
        if not has_node_assert:
            return None
        code_only = cls._strip_javascript_strings(cleaned)
        for match in re.finditer(r"(?<![.A-Za-z0-9_$])(?:test|it)\s*\(", code_only):
            opening = cleaned.find("(", match.start())
            depth = 0
            quote = ""
            escaped = False
            end = len(cleaned)
            for index in range(opening, len(cleaned)):
                char = cleaned[index]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = ""
                    continue
                if char in {'"', "'", "`"}:
                    quote = char
                    continue
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            block = cleaned[match.start() : end]
            code_block = code_only[match.start() : end]
            if (
                test_name
                and re.match(
                    rf"(?:test|it)\s*\(\s*([\"'`]){re.escape(test_name)}\1",
                    block,
                )
                is None
            ):
                continue
            if re.search(r"\bskip\s*:\s*true\b", code_block):
                continue
            if required_token and required_token not in block:
                continue
            if re.search(r"\bassert\.[A-Za-z_]\w*\s*\(", code_block):
                return match.start()
        return None

    def _source_specific_test(self, required_token: str = "") -> Evidence | None:
        if required_token in self._source_test_cache:
            return self._source_test_cache[required_token]
        test_root = self.root / "tests"
        if not test_root.is_dir():
            return None
        normalized = self.contract.canonical_slug.replace("-", "_")
        aliases = tuple(alias.replace("-", "_") for alias in self.contract.aliases)
        for path in sorted(test_root.rglob("test_*")):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".js", ".mjs"}:
                continue
            relative = path.relative_to(self.root).as_posix()
            loaded = self._text(relative)
            if loaded is None:
                continue
            _, source = loaded
            named = self._path_key_match(path.stem, normalized) or any(
                alias and self._path_key_match(path.stem, alias) for alias in aliases
            )
            if path.suffix != ".py":
                key_bound = named or any(
                    re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(key)}(?![A-Za-z0-9_-])",
                        source,
                        re.IGNORECASE,
                    )
                    for key in (normalized, *aliases)
                    if key
                )
                offset = self._javascript_asserting_test_offset(source, required_token)
                if key_bound and offset is not None:
                    lineno = source.count("\n", 0, max(offset, 0)) + 1
                    evidence = self._evidence(path, lineno, source.splitlines()[lineno - 1])
                    self._source_test_cache[required_token] = evidence
                    return evidence
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith("test_"):
                    continue
                source_bound = (
                    named
                    or self._path_key_match(node.name, normalized)
                    or any(alias and self._path_key_match(node.name, alias) for alias in aliases)
                )
                if not source_bound:
                    continue
                decorator_names = {self._call_name(item) for item in node.decorator_list}
                if decorator_names & {"skip", "skipif", "xfail"}:
                    continue
                end = int(getattr(node, "end_lineno", node.lineno))
                block = "\n".join(lines[node.lineno - 1 : end])
                if required_token and required_token not in block:
                    continue
                has_assertion = any(
                    isinstance(child, ast.Assert) for child in ast.walk(node)
                ) or any(
                    isinstance(child, ast.Call)
                    and (
                        self._call_name(child.func).lower().startswith("assert")
                        or self._call_name(child.func) == "raises"
                    )
                    for child in ast.walk(node)
                )
                if not has_assertion:
                    continue
                token_line = next(
                    (
                        index
                        for index in range(node.lineno, end + 1)
                        if not required_token or required_token in lines[index - 1]
                    ),
                    node.lineno,
                )
                evidence = self._evidence(path, token_line, lines[token_line - 1])
                self._source_test_cache[required_token] = evidence
                return evidence
        self._source_test_cache[required_token] = None
        return None

    def _canonical_registry(self) -> AuditResult:
        slug = self.contract.canonical_slug
        constant = "PLATFORM_" + re.sub(r"[^A-Za-z0-9]", "_", slug).upper()
        family_values = self._family_rule_values()
        requirements: list[tuple[str, Evidence | None]] = [
            (
                f"{constant}={slug!r} in canonical platforms registry",
                self._assignment_value("src/openbiliclaw/sources/platforms.py", constant, slug),
            ),
            (
                "SourceFamilyRule family for canonical slug",
                self._family_rule_value("family", slug),
            ),
        ]
        if self.contract.host_policy == "recipe-allowlist":
            has_static_hosts = any(field == "url_hosts" for field, _value in family_values)
            requirements.append(
                (
                    "recipe-allowlist family has no static url_hosts",
                    None if has_static_hosts else self._family_rule_value("family", slug),
                )
            )
        for alias in self.contract.aliases:
            requirements.append(
                (
                    f"declared alias {alias!r} in canonical registry",
                    self._family_rule_value("platform_aliases", alias),
                )
            )
        for host in self.contract.hosts:
            requirements.append(
                (
                    f"declared host {host!r} in canonical registry",
                    self._family_rule_value("url_hosts", host),
                )
            )
        for prefix in self.contract.events.strategy_prefixes:
            requirements.append(
                (
                    f"declared strategy prefix {prefix!r} in canonical registry",
                    self._family_rule_value("source_prefixes", prefix),
                )
            )
        for field, expected in (
            ("requires_overseas_network", self.contract.transport.requires_overseas_network),
            ("routed_by_network_mode", self.contract.transport.routed_by_network_mode),
        ):
            requirements.append(
                (
                    f"family-scoped network flag {field}={expected}",
                    self._family_rule_value(field, str(expected).lower()),
                )
            )
        return self._pass_or_missing(
            "canonical.registry",
            "canonical slug, aliases, hosts, and strategy prefixes",
            requirements,
        )

    def _transport(self) -> AuditResult:
        requirements: list[tuple[str, Evidence | None]] = []
        for entrypoint in self.contract.transport.entrypoints:
            requirements.append((f"transport entrypoint {entrypoint}", self._file(entrypoint)))
        route_locations = (
            "src/openbiliclaw/api/app.py",
            "src/openbiliclaw/cli.py",
            *self.contract.transport.entrypoints,
        )
        for alias in self.contract.transport.route_aliases:
            requirements.append(
                (
                    f"declared transport route alias {alias!r}",
                    self._first_word(route_locations, alias),
                )
            )
        return self._pass_or_missing(
            "transport.implementation",
            f"{self.contract.transport.owner}-owned {self.contract.transport.kind} transport",
            requirements,
        )

    def _content_types(self) -> AuditResult:
        candidates = tuple(
            dict.fromkeys((*self._source_files(), *self.contract.transport.entrypoints))
        )
        requirements = [
            (
                f"content type {content_type!r} in source normalization/transport",
                self._first_word(candidates, content_type),
            )
            for content_type in self.contract.content_types
        ]
        return self._pass_or_missing(
            "content-types.registration",
            "declared content-type normalization coverage",
            requirements,
        )

    def _class_field(self, relative: str, class_name: str, field: str) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != class_name:
                continue
            for item in node.body:
                target: ast.expr | None = None
                if isinstance(item, ast.AnnAssign):
                    target = item.target
                elif isinstance(item, ast.Assign) and len(item.targets) == 1:
                    target = item.targets[0]
                if isinstance(target, ast.Name) and target.id == field:
                    return self._evidence(path, item.lineno, source.splitlines()[item.lineno - 1])
        return None

    def _constructor_keyword(
        self,
        relative: str,
        constructor: str,
        keyword: str,
    ) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._call_name(node.func) != constructor:
                continue
            for item in node.keywords:
                if item.arg == keyword:
                    line = int(getattr(item.value, "lineno", node.lineno))
                    return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _route_decorator(
        self,
        relative: str,
        method: str,
        route: str,
    ) -> Evidence | None:
        """Find a concrete FastAPI-style route decorator outside a dead ``if False``."""
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def statically_dead(node: ast.AST) -> bool:
            current = node
            while current in parents:
                current = parents[current]
                if (
                    isinstance(current, ast.If)
                    and isinstance(current.test, ast.Constant)
                    and current.test.value is False
                ):
                    return True
            return False

        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if statically_dead(function):
                continue
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or self._call_name(decorator.func) != method:
                    continue
                if not decorator.args:
                    continue
                first = decorator.args[0]
                if isinstance(first, ast.Constant) and first.value == route:
                    line = int(getattr(decorator, "lineno", function.lineno))
                    return self._evidence(path, line, source.splitlines()[line - 1])
        return None

    def _route_handler_word(
        self,
        relative: str,
        method: str,
        route: str,
        value: str,
    ) -> Evidence | None:
        """Find *value* inside the function owning one exact route decorator."""
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        lines = source.splitlines()
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])")
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owns_route = any(
                isinstance(decorator, ast.Call)
                and self._call_name(decorator.func) == method
                and bool(decorator.args)
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.args[0].value == route
                for decorator in function.decorator_list
            )
            if not owns_route:
                continue
            end = int(getattr(function, "end_lineno", function.lineno))
            for lineno in range(function.lineno, end + 1):
                if pattern.search(lines[lineno - 1]) is not None:
                    return self._evidence(path, lineno, lines[lineno - 1])
        return None

    def _dynamic_status_assembly(self) -> Evidence | None:
        """Find provider-registry iteration feeding SourcesStatusResponse(**items)."""
        relative = "src/openbiliclaw/api/app.py"
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            provider_iteration = False
            item_maps: set[str] = set()
            response: ast.Call | None = None
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "items"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "SOURCE_AUTH_PROVIDERS"
                ):
                    provider_iteration = True
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                            item_maps.add(target.value.id)
                if isinstance(node, ast.Call) and self._call_name(node.func) == (
                    "SourcesStatusResponse"
                ):
                    response = node
            if not provider_iteration or response is None:
                continue
            expanded = {
                item.value.id
                for item in response.keywords
                if item.arg is None and isinstance(item.value, ast.Name)
            }
            if item_maps & expanded:
                return self._evidence(
                    path,
                    response.lineno,
                    source.splitlines()[response.lineno - 1],
                )
        return None

    def _config(self) -> AuditResult:
        slug = self.contract.canonical_slug
        return self._pass_or_missing(
            "config.registration",
            "config model and config.example.toml",
            [
                (
                    "SourcesConfig exact source field",
                    self._class_field("src/openbiliclaw/config.py", "SourcesConfig", slug),
                ),
                (
                    f"[sources.{slug}] example section",
                    self._regex(
                        "config.example.toml", rf"^\[sources\.{re.escape(slug)}\]", re.MULTILINE
                    ),
                ),
                (
                    "SourcesConfigOut exact source field",
                    self._class_field("src/openbiliclaw/api/models.py", "SourcesConfigOut", slug),
                ),
                (
                    "SourcesConfigOut exact constructor keyword",
                    self._constructor_keyword(
                        "src/openbiliclaw/api/app.py", "SourcesConfigOut", slug
                    ),
                ),
            ],
        )

    def _api_models(self) -> AuditResult:
        slug = self.contract.canonical_slug
        return self._pass_or_missing(
            "api.models",
            "API request/response models",
            [
                (
                    f"SourcesStatusResponse.{slug} exact field",
                    self._class_field(
                        "src/openbiliclaw/api/models.py", "SourcesStatusResponse", slug
                    ),
                ),
                (
                    f"SourcesCredentialsResponse.{slug} exact field",
                    self._class_field(
                        "src/openbiliclaw/api/models.py", "SourcesCredentialsResponse", slug
                    ),
                ),
                (
                    f"SourcesConfigOut.{slug} exact field",
                    self._class_field("src/openbiliclaw/api/models.py", "SourcesConfigOut", slug),
                ),
            ],
        )

    def _api_status(self) -> AuditResult:
        slug = self.contract.canonical_slug
        return self._pass_or_missing(
            "api.source-status",
            "GET /api/sources/status concrete source coverage",
            [
                (
                    "status route",
                    self._first_regex(
                        ("src/openbiliclaw/api/app.py",),
                        r"[\"']/api/sources/status[\"']",
                    ),
                ),
                (
                    "exact source-auth provider key feeding status roster",
                    self._assignment_key(
                        "src/openbiliclaw/api/source_auth/providers.py",
                        "SOURCE_AUTH_PROVIDERS",
                        slug,
                    ),
                ),
                (
                    f"SourcesStatusResponse.{slug} exact field",
                    self._class_field(
                        "src/openbiliclaw/api/models.py", "SourcesStatusResponse", slug
                    ),
                ),
                (
                    "SOURCE_AUTH_PROVIDERS dynamic SourcesStatusResponse assembly",
                    self._dynamic_status_assembly(),
                ),
            ],
        )

    def _api_credentials(self) -> AuditResult:
        slug = self.contract.canonical_slug
        return self._pass_or_missing(
            "api.credentials",
            "GET /api/sources/credentials concrete source coverage",
            [
                (
                    "credentials route",
                    self._first_regex(
                        ("src/openbiliclaw/api/app.py",),
                        r"[\"']/api/sources/credentials[\"']",
                    ),
                ),
                (
                    f"SourcesCredentialsResponse.{slug} exact field",
                    self._class_field(
                        "src/openbiliclaw/api/models.py", "SourcesCredentialsResponse", slug
                    ),
                ),
                (
                    f"SourcesCredentialsResponse exact {slug!r} constructor keyword",
                    self._constructor_keyword(
                        "src/openbiliclaw/api/app.py", "SourcesCredentialsResponse", slug
                    ),
                ),
                (
                    "concrete credential descriptor",
                    self._assignment_key(
                        "src/openbiliclaw/api/source_auth/write.py", "CREDENTIAL_SPECS", slug
                    ),
                ),
            ],
        )

    def _source_auth(self) -> list[AuditResult]:
        if self._uses_dynamic_workflows():
            return self._dynamic_workflow_auth()
        slug = self.contract.canonical_slug
        provider = self._pass_or_missing(
            "source-auth.provider",
            "source-auth provider registry",
            [
                (
                    "SOURCE_AUTH_PROVIDERS key",
                    self._assignment_key(
                        "src/openbiliclaw/api/source_auth/providers.py",
                        "SOURCE_AUTH_PROVIDERS",
                        slug,
                    ),
                )
            ],
        )
        verify_requirements: list[tuple[str, Evidence | None]] = [
            (
                "VERIFY_ACTIONS exact action",
                self._assignment_dict_value(
                    "src/openbiliclaw/api/source_auth/verify.py",
                    "VERIFY_ACTIONS",
                    slug,
                    self.contract.auth.verify_action,
                ),
            )
        ]
        if self.contract.auth.verify_action == "browser_heartbeat":
            heartbeat = self._dict_string(
                "src/openbiliclaw/api/source_auth/verify.py",
                "_BROWSER_HEARTBEAT_PREFIXES",
                slug,
            )
            prefix = heartbeat[0] if heartbeat is not None else ""
            allowed_prefixes = {
                slug,
                *self.contract.aliases,
                *self.contract.transport.route_aliases,
            }
            owned_heartbeat = heartbeat if prefix in allowed_prefixes else None
            verify_requirements.extend(
                [
                    (
                        "source-specific browser-heartbeat handler registry",
                        owned_heartbeat[1] if owned_heartbeat is not None else None,
                    ),
                    (
                        "source-specific login-state database getter",
                        self._regex(
                            "src/openbiliclaw/storage/database.py",
                            rf"def\s+get_{re.escape(prefix)}_login_state\s*\(",
                        )
                        if prefix
                        else None,
                    ),
                    (
                        "extension runtime-stream heartbeat event handler",
                        self._word(
                            "extension/src/background/cookie-sync.ts",
                            f"{prefix}_login_state_sync_requested",
                        )
                        if prefix
                        else None,
                    ),
                    (
                        "source-specific browser-heartbeat round-trip regression",
                        self._browser_heartbeat_roundtrip_test(slug, prefix)
                        if owned_heartbeat is not None
                        else None,
                    ),
                ]
            )
        verify = self._pass_or_missing(
            "source-auth.verify",
            f"verify action ({self.contract.auth.verify_action})",
            verify_requirements,
        )
        write_requirements: list[tuple[str, Evidence | None]] = [
            (
                "CREDENTIAL_SPECS key",
                self._assignment_key(
                    "src/openbiliclaw/api/source_auth/write.py", "CREDENTIAL_SPECS", slug
                ),
            )
        ]
        if self.contract.auth.write_path == "unified":
            write_requirements.append(
                (
                    "unified /credential route",
                    self._first_regex(
                        ("src/openbiliclaw/api/app.py",),
                        r"/api/sources/\{slug\}/credential",
                    ),
                )
            )
        elif self.contract.auth.write_path == "config-only":
            write_requirements.append(
                (
                    "PUT /api/config credential write path",
                    self._route_decorator(
                        "src/openbiliclaw/api/app.py",
                        "put",
                        "/api/config",
                    ),
                )
            )
            write_requirements.append(
                (
                    "concrete source handling in config route module",
                    self._route_handler_word(
                        "src/openbiliclaw/api/app.py",
                        "put",
                        "/api/config",
                        slug,
                    ),
                )
            )
        write = self._pass_or_missing(
            "source-auth.write",
            f"credential write contract ({self.contract.auth.write_path})",
            write_requirements,
        )
        results = [provider, verify, write]
        if self.contract.auth.mode == "capability-specific":
            results.append(
                self._pass_or_missing(
                    "source-auth.capability-readiness",
                    "per-capability backend/status/setup/init readiness",
                    [
                        (
                            "shared SourceCapabilityAuth model",
                            self._first_regex(
                                ("src/openbiliclaw/api/source_auth/contract.py",),
                                r"class\s+SourceCapabilityAuth\s*\(",
                            ),
                        ),
                        (
                            "SourceAuthContract capabilities field",
                            self._first_regex(
                                ("src/openbiliclaw/api/source_auth/contract.py",),
                                r"capabilities\s*:\s*dict\[str,\s*SourceCapabilityAuth\]",
                            ),
                        ),
                        (
                            "source-specific capability mode registry",
                            self._first_regex(
                                ("src/openbiliclaw/api/source_auth/providers.py",),
                                rf"{re.escape(slug.upper())}_CAPABILITY_AUTH_MODES",
                            ),
                        ),
                        (
                            "status provider capability projection",
                            self._first_regex(
                                ("src/openbiliclaw/api/source_auth/providers.py",),
                                r"capabilities=capabilities",
                            ),
                        ),
                        (
                            "guided-init source_capabilities projection",
                            self._first_regex(
                                ("src/openbiliclaw/api/models.py",),
                                r"source_capabilities\s*:\s*dict",
                            ),
                        ),
                        (
                            "backend bootstrap capability gate",
                            self._first_regex(
                                ("src/openbiliclaw/api/app.py",),
                                rf"{re.escape(slug)}_capability_readiness",
                            ),
                        ),
                        (
                            "shared frontend capability renderer",
                            self._first_regex(
                                ("src/openbiliclaw/web/shared/source-status.js",),
                                r"describeCapabilityReadiness",
                            ),
                        ),
                        (
                            "setup capability admission gate",
                            self._first_regex(
                                ("src/openbiliclaw/web/setup/index.html",),
                                r"source_capabilities",
                            ),
                        ),
                    ],
                )
            )
        return results

    def _dynamic_workflow_auth(self) -> list[AuditResult]:
        service = "src/openbiliclaw/sources/workflow_service.py"
        api = "src/openbiliclaw/api/custom_sources.py"
        executor = "src/openbiliclaw/sources/workflow_executors.py"
        return [
            self._pass_or_missing(
                "source-auth.provider",
                "per-instance workflow health projection",
                [
                    ("persistent source registry", self._word(service, "CustomSourceStore")),
                    ("workflow login state projection", self._word(service, "login_required")),
                    ("instance list API", self._route_decorator(api, "get", "")),
                ],
            ),
            self._pass_or_missing(
                "source-auth.verify",
                "per-workflow login trial and explicit resume",
                [
                    (
                        "read-only trial API",
                        self._route_decorator(api, "post", "/drafts/{draft_id}/trial"),
                    ),
                    (
                        "manual resume API",
                        self._route_decorator(api, "post", "/drafts/{draft_id}/resume"),
                    ),
                    ("runtime auth prerequisite", self._regex(executor, r"if\s+auth\.required")),
                    (
                        "login expiry pauses claims",
                        self._regex(
                            "src/openbiliclaw/sources/workflow_store.py",
                            r'workflow\["status"\]\s*==\s*"login_required"',
                        ),
                    ),
                ],
            ),
            self._pass_or_missing(
                "source-auth.write",
                "unified backend manual session takeover",
                [
                    (
                        "human takeover API",
                        self._route_decorator(api, "post", "/drafts/{draft_id}/takeover"),
                    ),
                    (
                        "manual input API",
                        self._route_decorator(api, "post", "/drafts/{draft_id}/action"),
                    ),
                    (
                        "interactive-only input guard",
                        self._regex(executor, r"if not self\.interactive"),
                    ),
                    ("opaque per-workflow session", self._word(service, "session_id")),
                ],
            ),
        ]

    def _source_policy(self) -> AuditResult:
        slug = self.contract.canonical_slug
        relative = "src/openbiliclaw/runtime/source_policy.py"
        return self._pass_or_missing(
            "runtime.source-policy",
            "source enablement and pool-share policy",
            [
                ("SOURCE_ORDER entry", self._assignment_value(relative, "SOURCE_ORDER", slug)),
                (
                    "DEFAULT_SOURCE_ENABLED entry",
                    self._assignment_key(relative, "DEFAULT_SOURCE_ENABLED", slug),
                ),
                (
                    "DEFAULT_POOL_SOURCE_SHARES entry",
                    self._assignment_key(relative, "DEFAULT_POOL_SOURCE_SHARES", slug),
                ),
            ],
        )

    def _shared_source_keys(self) -> AuditResult:
        slug = self.contract.canonical_slug
        relative = "src/openbiliclaw/web/shared/source-status.js"
        return self._pass_or_missing(
            "shared.source-keys",
            "shared SOURCE_KEYS and label registry",
            [
                (
                    "concrete slug in SOURCE_KEYS",
                    self._section_literal(relative, "const SOURCE_KEYS", slug),
                ),
                (
                    "concrete slug in SOURCE_LABELS",
                    self._section_literal(relative, "const SOURCE_LABELS", slug),
                ),
            ],
        )

    def _cli(self) -> AuditResult:
        slug = self.contract.canonical_slug
        command = self._route_decorator(
            "src/openbiliclaw/cli.py", "command", f"fetch-{slug}"
        ) or self._route_decorator("src/openbiliclaw/cli.py", "command", f"discover-{slug}")
        return self._pass_or_missing(
            "cli.registration",
            "source-specific CLI smoke registration",
            [("exact fetch-<source> or discover-<source> command", command)],
        )

    def _tests(self) -> AuditResult:
        return self._pass_or_missing(
            "tests.source-specific",
            "source-specific regression tests",
            [("source-named test/function", self._source_specific_test())],
        )

    def _docs(self) -> list[AuditResult]:
        slug = self.contract.canonical_slug
        module_path = f"docs/modules/{slug}.md"
        module_evidence = self._word(module_path, slug, ignore_case=True)
        if module_evidence is None:
            module_evidence = self._word(module_path, self.contract.display_name, ignore_case=True)
        module = self._pass_or_missing(
            "docs.module",
            "platform module documentation",
            [(module_path, module_evidence)],
        )
        changelog_evidence = self._word("docs/changelog.md", slug, ignore_case=True)
        if changelog_evidence is None:
            changelog_evidence = self._word(
                "docs/changelog.md", self.contract.display_name, ignore_case=True
            )
        changelog = self._pass_or_missing(
            "docs.changelog",
            "changelog entry",
            [("concrete source name in docs/changelog.md", changelog_evidence)],
        )
        return [module, changelog]

    def _discover(self) -> list[AuditResult]:
        if not self.contract.discover.modes:
            return [
                self._n_a(
                    "discover.formal",
                    "formal discover producer/runtime registration",
                    "discover.modes=[]",
                    self.contract.exclusions["discover.formal"],
                )
            ]
        slug = self.contract.canonical_slug
        producers = self._producer_files()
        if self._uses_dynamic_workflows():
            return self._dynamic_workflow_discover()
        producer_evidence = self._file(producers[0]) if producers else None
        formal = self._pass_or_missing(
            "discover.formal",
            "formal producer, scheduler, and runtime context",
            [
                ("source-specific producer", producer_evidence),
                (
                    "refresh scheduler registration",
                    self._word("src/openbiliclaw/runtime/refresh.py", slug),
                ),
                (
                    "API runtime producer registration",
                    self._word("src/openbiliclaw/api/runtime_context.py", slug),
                ),
            ],
        )
        mode_requirements: list[tuple[str, Evidence | None]] = []
        for mode in self.contract.discover.modes:
            mode_requirements.append((f"discover mode {mode!r}", self._first_word(producers, mode)))
        modes = self._pass_or_missing(
            "discover.modes", "declared discover modes in producer", mode_requirements
        )
        return [formal, modes]

    def _dynamic_workflow_discover(self) -> list[AuditResult]:
        producer = "src/openbiliclaw/runtime/custom_workflow_producer.py"
        service = "src/openbiliclaw/sources/workflow_service.py"
        requirements: list[tuple[str, Evidence | None]] = []
        for mode in self.contract.discover.modes:
            if mode == "browser-workflow":
                evidence = self._word(service, "compile_browser_workflow")
            elif mode == "search":
                evidence = self._regex(producer, r'workflow\["query_mode"\]\s*==\s*"interest"')
            else:
                evidence = self._first_word(("src/openbiliclaw/sources/web_recipe.py",), mode)
            requirements.append((f"discover mode {mode!r}", evidence))
        return [
            self._pass_or_missing(
                "discover.formal",
                "dynamic producer and runtime registry",
                [
                    ("instance producer", self._word(producer, "CustomWorkflowProducer")),
                    (
                        "canonical content family",
                        self._regex(
                            service,
                            rf'source_platform\s*=\s*["\']{re.escape(self.contract.canonical_slug)}["\']',
                        ),
                    ),
                    (
                        "live runtime registry",
                        self._word(
                            "src/openbiliclaw/api/runtime_context.py", "CustomWorkflowProducer"
                        ),
                    ),
                    (
                        "live scheduler registry",
                        self._word(
                            "src/openbiliclaw/runtime/refresh.py", "custom_source_producer_provider"
                        ),
                    ),
                ],
            ),
            self._pass_or_missing(
                "discover.modes", "declared HTTP/browser workflow modes", requirements
            ),
        ]

    def _search(self) -> list[AuditResult]:
        if "search" not in self.contract.discover.modes:
            return [
                self._n_a(
                    "search.integration",
                    "search keyword generation, claim, and provenance",
                    "discover.modes excludes 'search'",
                )
            ]
        if self._uses_dynamic_workflows():
            return self._dynamic_workflow_search()
        slug = self.contract.canonical_slug
        planner_file = "src/openbiliclaw/runtime/keyword_planner.py"
        planner = self._pass_or_missing(
            "search.planner",
            "merged keyword planner platform/style registration",
            [
                (
                    "_PLANNER_PLATFORMS entry",
                    self._assignment_value(planner_file, "_PLANNER_PLATFORMS", slug),
                ),
                (
                    "_PLATFORM_QUERY_STYLES entry",
                    self._assignment_key(planner_file, "_PLATFORM_QUERY_STYLES", slug),
                ),
            ],
        )
        prompts_file = "src/openbiliclaw/llm/prompts.py"
        prompt_mapping = self._assignment_key(prompts_file, "PLATFORM_SUPPLY_ADVANTAGES", slug)
        prompt_schema = self._regex(
            prompts_file,
            rf"platform[^\n]{{0,160}}{re.escape(slug)}|{re.escape(slug)}[^\n]{{0,160}}platform",
            re.IGNORECASE,
        )
        prompts = self._pass_or_missing(
            "search.prompts",
            "prompt supply advantage and allowed platform schema",
            [
                ("PLATFORM_SUPPLY_ADVANTAGES entry", prompt_mapping),
                ("prompt platform allow-list/schema", prompt_schema),
            ],
        )
        constant = "PLATFORM_" + re.sub(r"[^A-Za-z0-9]", "_", slug).upper()
        claim_pattern = rf"\.claim\(\s*(?:[\"']{re.escape(slug)}[\"']|{constant})"
        producers = self._producer_files()
        claim = self._pass_or_missing(
            "search.keyword-claim",
            "KeywordFetchCoordinator.claim(<slug>)",
            [("concrete source claim", self._first_regex(producers, claim_pattern))],
        )
        provenance = self._pass_or_missing(
            "search.provenance-tests",
            "source_keyword_id propagation regression",
            [
                (
                    "source-specific test asserts source_keyword_id",
                    self._source_specific_test("source_keyword_id"),
                )
            ],
        )
        inspiration = self._pass_or_missing(
            "search.inspiration-axis",
            "keyword inspiration axis materialization regression",
            [
                (
                    "source-specific inspiration test",
                    self._inspiration_materialization_test(),
                )
            ],
        )
        return [planner, prompts, claim, provenance, inspiration]

    def _dynamic_workflow_search(self) -> list[AuditResult]:
        planner = "src/openbiliclaw/runtime/keyword_planner.py"
        inspiration = "src/openbiliclaw/runtime/inspiration_pipeline.py"
        producer = "src/openbiliclaw/runtime/custom_workflow_producer.py"
        return [
            self._pass_or_missing(
                "search.planner",
                "active instance keyword registry",
                [
                    (
                        "active workflow registry query",
                        self._word(planner, "_custom_keyword_sources"),
                    ),
                    (
                        "merged instance planner keys",
                        self._regex(
                            planner,
                            r"return\s+\(\*_PLANNER_PLATFORMS,\s*\*self\._custom_keyword_sources\(\)\)",
                        ),
                    ),
                    ("source intent context", self._word(planner, "source_intent")),
                ],
            ),
            self._pass_or_missing(
                "search.prompts",
                "instance scope in both keyword prompt tracks",
                [
                    (
                        "regular source context",
                        self._regex(planner, r"\*\*self\._platform_context\(platform\)"),
                    ),
                    (
                        "exploration source context",
                        self._regex(inspiration, r"_host\._platform_context\(platform\)"),
                    ),
                    (
                        "claimable source-scoped prompt regression",
                        self._python_named_test(
                            "tests/test_custom_source_runtime.py",
                            "dynamic_planner",
                            ("run_once", "source_intent", "coordinator.claim", "private-session"),
                        ),
                    ),
                ],
            ),
            self._pass_or_missing(
                "search.keyword-claim",
                "KeywordFetchCoordinator.claim(instance source_id)",
                [
                    (
                        "instance claim",
                        self._regex(producer, r"coordinator\.claim\(\s*self\.source_id"),
                    ),
                    (
                        "regular/exploration tracks",
                        self._regex(producer, r"keyword_kind\s*=\s*kind"),
                    ),
                    (
                        "failed claim rollback",
                        self._regex(producer, r"coordinator\.rollback\(keyword\)"),
                    ),
                ],
            ),
            self._pass_or_missing(
                "search.provenance-tests",
                "instance keyword candidate provenance regression",
                [
                    (
                        "producer source_keyword_id regression",
                        self._python_named_test(
                            "tests/test_custom_source_contract.py",
                            "keyword",
                            ("source_keyword_id", "produce_if_due"),
                        ),
                    ),
                ],
            ),
            self._pass_or_missing(
                "search.inspiration-axis",
                "instance exploration materializes durable keyword rows",
                [
                    (
                        "explore ledger and claim regression",
                        self._python_named_test(
                            "tests/test_custom_source_runtime.py",
                            "custom_explore",
                            (
                                "_generate_custom_explore",
                                "ledger[source_id]",
                                'keyword_kind="explore"',
                                "coordinator.claim",
                            ),
                        ),
                    ),
                ],
            ),
        ]

    def _inspiration_materialization_test(self) -> Evidence | None:
        """Find one test that calls the stage and asserts ledger plus inserted rows."""
        relative = "tests/test_inspiration_pipeline.py"
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        constants: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value

        def string_value(node: ast.expr) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constants.get(node.id)
            return None

        slug = self.contract.canonical_slug
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or not (
                function.name.startswith("test_")
            ):
                continue
            ledger_name = ""
            stage_has_slug = False
            for walked in ast.walk(function):
                if not isinstance(walked, ast.Assign) or len(walked.targets) != 1:
                    continue
                awaited = (
                    walked.value.value if isinstance(walked.value, ast.Await) else walked.value
                )
                if not isinstance(awaited, ast.Call) or self._call_name(awaited.func) != (
                    "_run_inspiration_stage"
                ):
                    continue
                if isinstance(walked.targets[0], ast.Name):
                    ledger_name = walked.targets[0].id
                stage_has_slug = bool(awaited.args) and any(
                    string_value(child) == slug
                    for child in ast.walk(awaited.args[0])
                    if isinstance(child, ast.expr)
                )
            if not ledger_name or not stage_has_slug:
                continue
            ledger_assert = False
            inserted_assert = False
            for walked in ast.walk(function):
                if not isinstance(walked, ast.Assert) or not isinstance(walked.test, ast.Compare):
                    continue
                compare_nodes = (walked.test.left, *walked.test.comparators)
                if any(
                    isinstance(item, ast.Name) and item.id == ledger_name for item in compare_nodes
                ):
                    for item in compare_nodes:
                        if not isinstance(item, ast.Dict):
                            continue
                        ledger_assert = any(
                            string_value(key) == slug
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, int)
                            and value.value > 0
                            for key, value in zip(item.keys, item.values, strict=True)
                            if key is not None
                        )
                if any(
                    isinstance(child, ast.Attribute) and child.attr == "inserted"
                    for child in ast.walk(walked.test)
                ):
                    for child in ast.walk(walked.test):
                        if not isinstance(child, (ast.Tuple, ast.List)) or len(child.elts) < 2:
                            continue
                        inserted_assert = inserted_assert or (
                            string_value(child.elts[0]) == slug
                            and isinstance(child.elts[1], (ast.List, ast.Tuple))
                            and bool(child.elts[1].elts)
                        )
            if ledger_assert and inserted_assert:
                return self._evidence(
                    path,
                    function.lineno,
                    source.splitlines()[function.lineno - 1],
                )
        return None

    def _python_named_test(
        self,
        relative: str,
        name_token: str,
        body_tokens: tuple[str, ...] = (),
    ) -> Evidence | None:
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        lines = source.splitlines()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_") or not self._path_key_match(node.name, name_token):
                continue
            decorator_names = {self._call_name(item) for item in node.decorator_list}
            if decorator_names & {"skip", "skipif", "xfail"}:
                continue
            end = int(getattr(node, "end_lineno", node.lineno))
            block = "\n".join(lines[node.lineno - 1 : end]).lower()
            if any(token.lower() not in block for token in body_tokens):
                continue
            has_assertion = any(isinstance(child, ast.Assert) for child in ast.walk(node)) or any(
                isinstance(child, ast.Call)
                and (
                    self._call_name(child.func).lower().startswith("assert")
                    or self._call_name(child.func) == "raises"
                )
                for child in ast.walk(node)
            )
            if not has_assertion:
                continue
            return self._evidence(path, node.lineno, lines[node.lineno - 1])
        return None

    def _browser_heartbeat_roundtrip_test(self, slug: str, prefix: str) -> Evidence | None:
        relative = "tests/test_source_auth_contract.py"
        loaded = self._text(relative)
        if loaded is None:
            return None
        path, source = loaded
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        lines = source.splitlines()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_") or "browser_heartbeat" not in node.name:
                continue
            start = min(
                (int(getattr(item, "lineno", node.lineno)) for item in node.decorator_list),
                default=node.lineno,
            )
            end = int(getattr(node, "end_lineno", node.lineno))
            block = "\n".join(lines[start - 1 : end])
            if slug not in block or prefix not in block:
                continue
            if "login_state_sync_requested" not in block or "verification" not in block:
                continue
            if not any(isinstance(child, ast.Assert) for child in ast.walk(node)):
                continue
            return self._evidence(path, node.lineno, lines[node.lineno - 1])
        return None

    def _bootstrap_registries(self) -> AuditResult:
        record = self._bootstrap_record()
        requirements: list[tuple[str, Evidence | None]] = [
            ("exact _BOOTSTRAP_TASK_TABLES (source, <source>_tasks, task_type) triple", None)
        ]
        if record is not None:
            scheduler, table, _, record_evidence = record
            requirements[0] = (requirements[0][0], record_evidence)
            state_path = "src/openbiliclaw/sources/bootstrap_state.py"
            scheduler_state = self._dict_string(
                state_path, "SOURCE_BOOTSTRAP_STATE_KEYS", scheduler
            )
            canonical_state = self._dict_string(
                state_path,
                "SOURCE_BOOTSTRAP_STATE_KEYS",
                self.contract.canonical_slug,
            )
            state_key = scheduler_state[0] if scheduler_state is not None else ""
            requirements.extend(
                [
                    (
                        f"SOURCE_BOOTSTRAP_STATE_KEYS[{scheduler!r}]",
                        scheduler_state[1] if scheduler_state is not None else None,
                    ),
                    (
                        "canonical alias maps to the same bootstrap state key",
                        canonical_state[1]
                        if canonical_state is not None and canonical_state[0] == state_key
                        else None,
                    ),
                    (
                        "default_source_bootstrap_state preserves the durable state key",
                        self._function_return_dict_key(
                            state_path, "default_source_bootstrap_state", state_key
                        )
                        if state_key
                        else None,
                    ),
                    (
                        "normalize_source_bootstrap_state preserves the durable state key",
                        self._function_return_dict_key(
                            state_path, "normalize_source_bootstrap_state", state_key
                        )
                        if state_key
                        else None,
                    ),
                    (
                        f"_TASK_TABLES contains exact {table!r}",
                        self._task_table_member(table),
                    ),
                    (
                        "source-specific bootstrap registry regression",
                        self._python_named_test(
                            "tests/test_source_bootstrap.py", scheduler, (table,)
                        ),
                    ),
                ]
            )
        return self._pass_or_missing(
            "profile.bootstrap-registries",
            "browser-task bootstrap, durable state, and task-result registries",
            requirements,
        )

    def _incremental_registry_result(self) -> AuditResult:
        relative = "src/openbiliclaw/runtime/source_incremental_sync.py"
        record = self._bootstrap_record()
        requirements: list[tuple[str, Evidence | None]] = [
            ("matching browser bootstrap task triple", record[3] if record else None)
        ]
        if record is not None:
            scheduler, table, task_type, _ = record
            interval = self._dict_string(relative, "_SOURCE_INTERVAL_FIELDS", scheduler)
            requirements.extend(
                [
                    (
                        f"SOURCE_ORDER exact {scheduler!r} member",
                        self._assignment_value(relative, "SOURCE_ORDER", scheduler),
                    ),
                    (
                        "_TASK_SPECS exact table/task_type and callable enqueue helper",
                        self._task_spec(scheduler, table, task_type),
                    ),
                    (
                        "_SOURCE_CONFIG_ALIASES exact canonical alias for scheduler key",
                        self._dict_tuple_contains(
                            relative,
                            "_SOURCE_CONFIG_ALIASES",
                            scheduler,
                            self.contract.canonical_slug,
                        ),
                    ),
                    (
                        "_SOURCE_INTERVAL_FIELDS exact non-empty scheduler field",
                        interval[1] if interval is not None else None,
                    ),
                    (
                        "source-specific incremental four-table regression",
                        self._python_named_test(
                            "tests/test_source_incremental_sync.py",
                            scheduler,
                            ("_TASK_SPECS",),
                        ),
                    ),
                ]
            )
        return self._pass_or_missing(
            "profile.incremental",
            "post-init incremental four-table registration and regression",
            requirements,
        )

    def _profile(self) -> list[AuditResult]:
        if not self.contract.profile.signals:
            signals = self._n_a(
                "profile.signals",
                "bootstrap/profile signal wiring",
                "profile.signals=false",
                self.contract.exclusions["profile.signals"],
            )
        else:
            source_files = self._source_files()
            source_evidence = self._file(source_files[0]) if source_files else None
            route_keys = self._route_keys()
            flag_pattern = "(?:" + "|".join(re.escape(key) for key in route_keys) + ")"
            callback_requirements: list[tuple[str, Evidence | None]] = []
            if self.contract.auth.login_state_path == "callback":
                callback_leaves: set[str] = set()
                if self.contract.extension.task == "identity-only":
                    callback_leaves.add("identity")
                if self.contract.extension.cookie_sync or (
                    self.contract.auth.verify_action == "browser_heartbeat"
                ):
                    callback_leaves.add("login-state")
                if not callback_leaves:
                    callback_leaves.add("login-state")
                for callback_leaf in sorted(callback_leaves):
                    callback_evidence = next(
                        (
                            evidence
                            for key in route_keys
                            if (
                                evidence := self._function_assignment_value(
                                    "src/openbiliclaw/api/app.py",
                                    "create_app",
                                    "_init_write_allowlist",
                                    f"/api/sources/{key}/{callback_leaf}",
                                )
                            )
                            is not None
                        ),
                        None,
                    )
                    callback_requirements.append(
                        (
                            f"guided-init exact {callback_leaf} callback allowlist",
                            callback_evidence,
                        )
                    )
            signals = self._pass_or_missing(
                "profile.signals",
                "bootstrap events and init/profile wiring",
                [
                    ("source event normalizer/importer", source_evidence),
                    (
                        "InitPrerequisites _PLATFORM_SOURCE_FIELDS entry",
                        self._assignment_value(
                            "src/openbiliclaw/runtime/init_prereqs.py",
                            "_PLATFORM_SOURCE_FIELDS",
                            self.contract.canonical_slug,
                        ),
                    ),
                    (
                        "guided-init API registration",
                        self._word("src/openbiliclaw/api/app.py", self.contract.canonical_slug),
                    ),
                    (
                        "CLI exact --yes-<source> init option",
                        self._regex(
                            "src/openbiliclaw/cli.py",
                            rf"[\"']--yes-{flag_pattern}[\"']",
                        ),
                    ),
                    (
                        "CLI exact --no-<source> init option",
                        self._regex(
                            "src/openbiliclaw/cli.py",
                            rf"[\"']--no-{flag_pattern}[\"']",
                        ),
                    ),
                    ("source-specific profile/bootstrap test", self._source_specific_test()),
                    *callback_requirements,
                ],
            )
        if not self.contract.profile.incremental:
            incremental = self._n_a(
                "profile.incremental",
                "post-init incremental sync",
                "profile.incremental=false",
                self.contract.exclusions["profile.incremental"],
            )
        else:
            incremental = self._incremental_registry_result()
        results = [signals, incremental]
        if self.contract.profile.signals and self.contract.extension.task == "browser-task":
            results.append(self._bootstrap_registries())
        return results

    def _extension(self) -> AuditResult:
        task = self.contract.extension.task
        if task == "none":
            return self._n_a(
                "extension.task",
                "browser-extension source task/identity",
                "extension.task='none'",
                self.contract.exclusions["extension.task"],
            )
        route_keys = self._route_keys()
        source_files = tuple(f"extension/src/content/{key}.ts" for key in route_keys)
        source_file = next(
            (relative for relative in source_files if self._file(relative) is not None),
            source_files[0],
        )
        route_pattern = "(?:" + "|".join(re.escape(key) for key in route_keys) + ")"
        requirements: list[tuple[str, Evidence | None]] = [
            ("source content script", self._first_file(source_files)),
            (
                "extension build entry",
                self._first_key_word(("extension/scripts/build.mjs",), route_keys),
            ),
            (
                "Chrome manifest source registration",
                self._first_key_word(("extension/manifest.json",), route_keys),
            ),
            (
                "Firefox manifest source registration",
                self._first_key_word(("extension/manifest.firefox.json",), route_keys),
            ),
        ]
        for host in self.contract.extension.hosts:
            requirements.extend(
                [
                    (f"Chrome manifest host {host}", self._word("extension/manifest.json", host)),
                    (
                        f"Firefox manifest host {host}",
                        self._word("extension/manifest.firefox.json", host),
                    ),
                ]
            )
        if task == "identity-only":
            identity_clients = (
                "extension/src/background/service-worker.ts",
                source_file,
            )
            identity_endpoint = next(
                (
                    evidence
                    for key in route_keys
                    if (
                        evidence := self._route_decorator(
                            "src/openbiliclaw/api/app.py",
                            "post",
                            f"/api/sources/{key}/identity",
                        )
                    )
                    is not None
                ),
                None,
            )
            requirements.extend(
                [
                    (
                        "extension identity callback client",
                        self._first_regex(
                            identity_clients,
                            rf"/sources/{route_pattern}/identity",
                        ),
                    ),
                    (
                        "backend POST identity endpoint",
                        identity_endpoint,
                    ),
                ]
            )
        else:
            task_client: Evidence | None
            if self.contract.extension.background:
                dispatcher_files = (
                    *(f"extension/src/background/{key}-task-dispatcher.ts" for key in route_keys),
                    "extension/src/background/service-worker.ts",
                )
                task_client = self._first_key_word(dispatcher_files, route_keys)
            else:
                task_client = self._first_file(
                    tuple(f"extension/src/content/{key}/task-executor.ts" for key in route_keys)
                )
            endpoint_evidence: tuple[Evidence, Evidence, Evidence] | None = None
            for route_key in route_keys:
                candidate = (
                    self._route_decorator(
                        "src/openbiliclaw/api/app.py",
                        "get",
                        f"/api/sources/{route_key}/next-task",
                    ),
                    self._route_decorator(
                        "src/openbiliclaw/api/app.py",
                        "post",
                        f"/api/sources/{route_key}/task-result",
                    ),
                    self._route_decorator(
                        "src/openbiliclaw/api/app.py",
                        "post",
                        f"/api/sources/{route_key}/kick",
                    ),
                )
                if all(item is not None for item in candidate):
                    endpoint_evidence = cast("tuple[Evidence, Evidence, Evidence]", candidate)
                    break
            next_task, task_result, kick = endpoint_evidence or (None, None, None)
            requirements.extend(
                [
                    (
                        "browser-task claim/dispatch client",
                        task_client,
                    ),
                    (
                        "browser-task executor",
                        self._first_file(
                            tuple(
                                f"extension/src/content/{key}/task-executor.ts"
                                for key in route_keys
                            )
                        ),
                    ),
                    (
                        "backend GET next-task decorator on one complete route key",
                        next_task,
                    ),
                    (
                        "backend POST task-result decorator on the same route key",
                        task_result,
                    ),
                    (
                        "backend POST kick decorator on the same route key",
                        kick,
                    ),
                ]
            )
        requirements.append(
            (
                "source-specific extension regression",
                self._extension_test_evidence(),
            )
        )
        return self._pass_or_missing(
            "extension.task", f"extension {task} registration", requirements
        )

    def _extension_features(self) -> list[AuditResult]:
        extension = self.contract.extension
        route_keys = self._route_keys()
        if extension.task_marker:
            task_marker = self._pass_or_missing(
                "extension.task-marker",
                "task-mode marker/isolation registration",
                [
                    (
                        "source task-mode module or marker",
                        self._first_file(
                            tuple(f"extension/src/content/{key}/task-mode.ts" for key in route_keys)
                        )
                        or self._first_key_word(
                            tuple(f"extension/src/content/{key}.ts" for key in route_keys),
                            route_keys,
                        ),
                    )
                ],
            )
        else:
            task_marker = self._n_a(
                "extension.task-marker",
                "task-mode marker/isolation registration",
                "extension.task_marker=false",
                self.contract.exclusions["extension.task-marker"],
            )
        if extension.background:
            background = self._pass_or_missing(
                "extension.background",
                "background/service-worker source registration",
                [
                    (
                        "concrete source in service worker",
                        self._first_key_word(
                            ("extension/src/background/service-worker.ts",),
                            route_keys,
                        ),
                    )
                ],
            )
        else:
            background = self._n_a(
                "extension.background",
                "background/service-worker source registration",
                "extension.background=false",
                self.contract.exclusions["extension.background"],
            )
        if extension.early_response:
            early_response = AuditResult(
                "extension.early-response",
                "early-response capture/replay",
                "MANUAL",
                False,
                (
                    "contract declares extension.early_response=true; inspect built MAIN-world ordering, "
                    "buffer bounds, and replay under a real early response"
                ),
            )
        else:
            early_response = self._n_a(
                "extension.early-response",
                "early-response capture/replay",
                "extension.early_response=false",
                self.contract.exclusions["extension.early-response"],
            )
        if extension.cookie_sync:
            cookie_requirements: list[tuple[str, Evidence | None]] = [
                (
                    "cookie-sync source registration",
                    self._first_key_word(
                        ("extension/src/background/cookie-sync.ts",),
                        route_keys,
                    ),
                )
            ]
            for host in extension.hosts:
                cookie_requirements.append(
                    (
                        f"cookie-sync host {host}",
                        self._word("extension/src/background/cookie-sync.ts", host),
                    )
                )
            cookie_sync = self._pass_or_missing(
                "extension.cookie-sync",
                "login-cookie state synchronization",
                cookie_requirements,
            )
        else:
            cookie_sync = self._n_a(
                "extension.cookie-sync",
                "login-cookie state synchronization",
                "extension.cookie_sync=false",
                self.contract.exclusions["extension.cookie-sync"],
            )
        return [task_marker, background, early_response, cookie_sync]

    def _extension_test_evidence(self) -> Evidence | None:
        root = self.root / "extension/tests"
        if not root.is_dir():
            return None
        keys = self._route_keys()
        for path in sorted(root.glob("*")):
            if path.is_file() and any(self._path_key_match(path.stem, key) for key in keys):
                relative = path.relative_to(self.root).as_posix()
                loaded = self._text(relative)
                if loaded is None:
                    continue
                _, source = loaded
                offset = self._javascript_asserting_test_offset(source)
                if offset is None:
                    continue
                lineno = source.count("\n", 0, offset) + 1
                return self._evidence(path, lineno, source.splitlines()[lineno - 1])
        return None

    def _surface_result(self, surface: str) -> AuditResult:
        label = f"{surface.replace('_', ' ')} product surface"
        if not self.contract.surfaces[surface]:
            return self._n_a(
                f"surface.{surface}",
                label,
                f"surfaces.{surface}=false",
                self.contract.exclusions[f"surface.{surface}"],
            )
        if self._uses_dynamic_workflows():
            return self._dynamic_workflow_surface(surface)
        slug = self.contract.canonical_slug
        if surface == "cli":
            result = self._cli()
            return AuditResult(
                f"surface.{surface}", label, result.status, True, result.detail, result.evidence
            )
        groups: list[tuple[str, tuple[str, ...]]]
        if surface == "setup":
            groups = [("setup page", ("src/openbiliclaw/web/setup/index.html",))]
        elif surface == "desktop":
            groups = [
                ("desktop HTML", ("src/openbiliclaw/web/desktop/index.html",)),
                ("desktop JS", ("src/openbiliclaw/web/desktop/assets/js/app.js",)),
            ]
        elif surface == "mobile":
            groups = [("mobile view model", ("src/openbiliclaw/web/js/view-models.js",))]
        elif surface == "extension_popup":
            groups = [
                ("popup HTML", ("extension/popup/popup.html",)),
                (
                    "popup helper/JS",
                    ("extension/popup/popup-helpers.js", "extension/popup/popup.js"),
                ),
            ]
        elif surface == "source_status":
            groups = [
                ("status API", ("src/openbiliclaw/api/app.py",)),
                ("shared status roster", ("src/openbiliclaw/web/shared/source-status.js",)),
            ]
        elif surface == "credentials":
            groups = [
                ("credential descriptor", ("src/openbiliclaw/api/source_auth/write.py",)),
                ("credential renderer roster", ("src/openbiliclaw/web/shared/source-status.js",)),
            ]
        else:
            groups = [
                ("desktop recommendation", ("src/openbiliclaw/web/desktop/assets/js/app.js",)),
                ("mobile recommendation", ("src/openbiliclaw/web/js/view-models.js",)),
                ("popup recommendation", ("extension/popup/popup-helpers.js",)),
            ]
        requirements = [(name, self._first_word(paths, slug)) for name, paths in groups]
        return self._pass_or_missing(f"surface.{surface}", label, requirements)

    def _dynamic_workflow_surface(self, surface: str) -> AuditResult:
        """Do not accept a legacy family label as instance-management wiring."""
        probes = {
            "cli": [
                ("CLI command group", "src/openbiliclaw/cli.py", "sources_app"),
                (
                    "workflow CLI service",
                    "src/openbiliclaw/cli_custom_sources.py",
                    "WorkflowService",
                ),
            ],
            "setup": [
                (
                    "explicit dynamic-source setup entry",
                    "src/openbiliclaw/web/setup/index.html",
                    "custom-sources",
                ),
            ],
            "desktop": [
                (
                    "instance source panel",
                    "src/openbiliclaw/web/desktop/index.html",
                    "customSourceManager",
                ),
                (
                    "shared manager mount",
                    "src/openbiliclaw/web/desktop/assets/js/app.js",
                    "OpenBiliClawCustomSources",
                ),
            ],
            "mobile": [
                (
                    "shared manager mount",
                    "src/openbiliclaw/web/js/app.js",
                    "OpenBiliClawCustomSources",
                ),
                (
                    "mobile instance attribution",
                    "src/openbiliclaw/web/js/view-models.js",
                    "source_id",
                ),
            ],
            "extension_popup": [
                ("instance source panel", "extension/popup/popup.html", "customSourceManager"),
                ("shared manager mount", "extension/popup/popup.js", "OpenBiliClawCustomSources"),
            ],
            "source_status": [
                (
                    "instance health projection",
                    "src/openbiliclaw/sources/workflow_service.py",
                    "health",
                ),
                (
                    "instance list endpoint",
                    "src/openbiliclaw/api/custom_sources.py",
                    "list_sources",
                ),
                (
                    "shared health renderer",
                    "src/openbiliclaw/web/shared/custom-sources.js",
                    "statusLabel",
                ),
            ],
            "credentials": [
                ("manual login API", "src/openbiliclaw/api/custom_sources.py", "takeover"),
                (
                    "manual login surface",
                    "src/openbiliclaw/web/desktop/source-login.html",
                    "custom-sources",
                ),
                (
                    "shared login entry",
                    "src/openbiliclaw/web/shared/custom-sources.js",
                    "takeoverUrl",
                ),
            ],
            "recommendation": [
                ("source attribution DTO", "src/openbiliclaw/api/models.py", "source_name"),
                (
                    "desktop source label",
                    "src/openbiliclaw/web/desktop/assets/js/app.js",
                    "recommendationSourceName",
                ),
                (
                    "mobile source label",
                    "src/openbiliclaw/web/js/views/recommend.js",
                    "getRecommendationSourceLabel",
                ),
                (
                    "popup source label",
                    "extension/popup/popup-helpers.js",
                    "recommendationSourceName",
                ),
            ],
        }
        return self._pass_or_missing(
            f"surface.{surface}",
            f"dynamic {surface.replace('_', ' ')} product surface",
            [(label, self._word(path, token)) for label, path, token in probes[surface]],
        )

    def _media(self) -> list[AuditResult]:
        media = self.contract.media
        image_security: AuditResult | None = None
        if media.image == "none":
            image = self._n_a(
                "media.image",
                "cover image registration",
                "media.image='none'",
                self.contract.exclusions["media.image"],
            )
        elif media.image == "direct":
            image = AuditResult(
                "media.image",
                "direct browser cover delivery",
                "MANUAL",
                False,
                "contract declares direct image delivery; manually verify hotlink/referer behaviour",
            )
        else:
            image = self._pass_or_missing(
                "media.image",
                "image-proxy host allow-list",
                [
                    (
                        f"ALLOWED_IMAGE_HOST_SUFFIXES contains {host}",
                        self._assignment_value(
                            "src/openbiliclaw/runtime/image_cache.py",
                            "ALLOWED_IMAGE_HOST_SUFFIXES",
                            host,
                        ),
                    )
                    for host in media.image_hosts
                ],
            )
            image_security = AuditResult(
                "media.image-network-boundary",
                "image proxy DNS/redirect/SSRF boundary",
                "MANUAL",
                True,
                (
                    "static hostname syntax and allow-list wiring are not DNS safety proof; "
                    "acceptance must verify every redirect hop and resolved address rejects "
                    "loopback/private/link-local/reserved targets and document rebinding policy"
                ),
            )
        if media.deep_link != "native":
            deep_link = self._n_a(
                "media.deep-link",
                "mobile native-app deep link",
                f"media.deep_link={media.deep_link!r}",
                self.contract.exclusions["media.deep-link"],
            )
        else:
            slug = self.contract.canonical_slug
            deep_link = self._pass_or_missing(
                "media.deep-link",
                "mobile native-app deep-link parser and regression",
                [
                    (
                        "buildAppDeepLink concrete platform branch",
                        self._word("src/openbiliclaw/web/js/app-launch.js", slug),
                    ),
                    (
                        "source-specific deep-link test",
                        self._first_word(
                            (
                                "tests/test_mobile_app_launch.py",
                                "tests/test_app_launch.py",
                                "tests/test_mobile_web_app_launch.py",
                            ),
                            slug,
                        ),
                    ),
                ],
            )
        if not media.native_save:
            native_save = self._n_a(
                "media.native-save",
                "platform-native save adapter/executor",
                "media.native_save=false",
                self.contract.exclusions["media.native-save"],
            )
        else:
            slug = self.contract.canonical_slug
            native_save = self._pass_or_missing(
                "media.native-save",
                "native-save adapter, extension executor, and regression",
                [
                    (
                        "backend extension adapter definition",
                        self._literal("src/openbiliclaw/saved_sync/adapters/extension.py", slug),
                    ),
                    (
                        "extension native-save platform registry",
                        self._literal("extension/src/shared/e2e.ts", slug),
                    ),
                    (
                        "native-save regression",
                        self._first_word(
                            (
                                "tests/test_extension_native_save_broker.py",
                                "tests/test_six_platform_native_save_e2e_harness.py",
                            ),
                            slug,
                        ),
                    ),
                ],
            )
        results = [image, deep_link, native_save]
        if image_security is not None:
            results.append(image_security)
        return results

    def _engagement(self) -> list[AuditResult]:
        candidates = tuple(dict.fromkeys((*self._source_files(), *self._producer_files())))
        results: list[AuditResult] = []
        for metric in ENGAGEMENT_KEYS:
            availability = self.contract.engagement[metric]
            capability = f"engagement.{metric}"
            label = f"{metric}_count availability and mapping"
            if availability == "unavailable":
                results.append(
                    self._n_a(
                        capability,
                        label,
                        f"engagement.{metric}='unavailable'",
                        self.contract.exclusions[capability],
                    )
                )
                continue
            results.append(
                self._pass_or_missing(
                    capability,
                    label,
                    [
                        (
                            f"{metric}_count in source normalizer/producer",
                            self._first_word(candidates, f"{metric}_count"),
                        ),
                        (
                            f"source-specific {metric}_count regression",
                            self._source_specific_test(f"{metric}_count"),
                        ),
                    ],
                )
            )
        return results

    def _decision_ledger(self) -> list[AuditResult]:
        """Keep semantic decisions visible without pretending static proof."""
        profile = self.contract.profile
        if profile.refresh_mode == "none":
            refresh = self._n_a(
                "profile.refresh-mode",
                "profile refresh ownership and cadence",
                "profile.refresh_mode='none'",
            )
        else:
            refresh = AuditResult(
                "profile.refresh-mode",
                "profile refresh ownership and cadence",
                "MANUAL",
                False,
                (
                    f"contract declares refresh_mode={profile.refresh_mode!r}; verify ownership, "
                    "stable identity, admission, durable ingress, and bounded seen state"
                ),
            )
        return [
            AuditResult(
                "identity.semantics",
                "item identity, URL, dedupe, and account scope",
                "MANUAL",
                False,
                (
                    f"item_id={self.contract.identity.item_id}; url={self.contract.identity.url}; "
                    f"dedupe={self.contract.identity.dedupe}; "
                    f"account_scope={self.contract.identity.account_scope}"
                ),
            ),
            AuditResult(
                "auth.identity-evidence",
                "auth routes, login-state path, and identity evidence",
                "MANUAL",
                False,
                (
                    f"resolution={self.contract.auth.account_resolution}; "
                    f"evidence={self.contract.auth.identity_evidence}; "
                    f"verify_action={self.contract.auth.verify_action}; "
                    f"login_state_path={self.contract.auth.login_state_path}; "
                    f"login_cookie_names={list(self.contract.auth.login_cookie_names)}; "
                    f"capability_modes={self.contract.auth.capability_modes}; "
                    f"capability_required={self.contract.auth.capability_required}"
                ),
            ),
            AuditResult(
                "upstream.response-contract",
                "success content types, pagination, and terminal evidence",
                "MANUAL",
                False,
                (
                    f"success_content_types={list(self.contract.upstream.success_content_types)}; "
                    f"pagination={self.contract.upstream.pagination}; "
                    f"terminal_evidence={self.contract.upstream.terminal_evidence}; "
                    f"terminal_policy={self.contract.upstream.terminal_policy}; "
                    f"partial_policy={self.contract.upstream.partial_policy}; "
                    f"publication_time_policy={self.contract.upstream.publication_time_policy}"
                ),
            ),
            AuditResult(
                "engagement.branch-coverage",
                "engagement metric parity across fetch branches",
                "MANUAL",
                False,
                (
                    "mapped_metrics="
                    f"{[key for key, value in self.contract.engagement.items() if value == 'mapped']}; "
                    f"capability_routes={self.contract.transport.capability_routes}; acceptance "
                    "must compare the same upstream item across every active route/branch"
                ),
            ),
            AuditResult(
                "discover.operational-contract",
                "search generation, budgets, and cursor recovery",
                "MANUAL",
                False,
                (
                    f"search_generation={self.contract.discover.search_generation}; "
                    f"budget={self.contract.discover.budget}; cursor={self.contract.discover.cursor}"
                ),
            ),
            refresh,
            AuditResult(
                "e2e.action-boundary",
                "safe and state-changing real-E2E actions",
                "MANUAL",
                False,
                (
                    f"safe_actions={list(self.contract.e2e.safe_actions)}; "
                    f"safe_assertions={self.contract.e2e.safe_assertions}; "
                    f"safe_postconditions={self.contract.e2e.safe_postconditions}; "
                    f"mutating_actions={list(self.contract.e2e.mutating_actions)}; "
                    "mutating actions require exact user authorization or a test account"
                ),
            ),
            AuditResult(
                "transport.fallback-ownership",
                "primary/fallback transport ownership",
                "MANUAL",
                False,
                (
                    f"primary_owner={self.contract.transport.owner}; "
                    f"fallback_owner={self.contract.transport.fallback_owner}; "
                    f"route_aliases={list(self.contract.transport.route_aliases)}; "
                    f"capability_routes={self.contract.transport.capability_routes}; "
                    f"network_policy={self.contract.transport.network_policy}"
                ),
            ),
            AuditResult(
                "events.mapping-and-scope",
                "event mappings, strategy prefixes, and backend scope caps",
                "MANUAL",
                False,
                (
                    f"strategy_prefixes={list(self.contract.events.strategy_prefixes)}; "
                    f"mappings={self.contract.events.mappings}; "
                    f"scope_caps={self.contract.events.scope_caps}"
                ),
            ),
            AuditResult(
                "task.runtime-bounds",
                "lease, idle/absolute deadlines, retry, and buffer bounds",
                "MANUAL",
                False,
                (
                    f"lease={self.contract.task.lease}; idle={self.contract.task.idle_deadline}; "
                    f"absolute={self.contract.task.absolute_deadline}; "
                    f"retry={self.contract.task.retry}; buffer={self.contract.task.buffer}"
                ),
            ),
            AuditResult(
                "e2e.smoke-sinks",
                "isolated smoke-test projection budget",
                "MANUAL",
                False,
                (
                    f"storage_scope={self.contract.smoke.storage_scope}; "
                    f"sinks={self.contract.smoke.sinks}"
                ),
            ),
        ]

    def run(self) -> list[AuditResult]:
        """Return the complete, deterministic capability inventory."""
        results = [
            self._canonical_registry(),
            self._transport(),
            self._content_types(),
            self._config(),
            self._api_models(),
            self._api_status(),
            self._api_credentials(),
            *self._source_auth(),
            self._source_policy(),
            self._shared_source_keys(),
            self._cli(),
            self._tests(),
            *self._docs(),
            *self._discover(),
            *self._search(),
            *self._profile(),
            self._extension(),
            *self._extension_features(),
        ]
        results.extend(self._surface_result(surface) for surface in SURFACE_KEYS)
        results.extend(self._engagement())
        results.extend(self._media())
        results.extend(self._decision_ledger())
        results.append(
            AuditResult(
                "manual.semantic-e2e",
                "semantic, built-artifact, and real E2E verification",
                "MANUAL",
                False,
                (
                    "static registration evidence cannot prove normalization semantics, auth truth, "
                    "built extension assets, upstream behaviour, or real E2E completion"
                ),
            )
        )
        return results


def changed_files_since(root: Path, ref: str) -> set[str]:
    """Return tracked changes plus untracked files, without invoking a shell."""
    if not ref.strip() or "\x00" in ref or ref.startswith("-"):
        raise ContractError("--diff-base must be a non-empty git ref that does not start with '-'")
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", ref, "--"],
        check=False,
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        message = diff.stderr.strip() or f"git diff exited {diff.returncode}"
        raise ContractError(f"cannot resolve --diff-base {ref!r}: {message}")
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        check=False,
        capture_output=True,
        text=True,
    )
    if untracked.returncode != 0:
        message = untracked.stderr.strip() or f"git ls-files exited {untracked.returncode}"
        raise ContractError(f"cannot inventory untracked files: {message}")
    return {
        line.strip()
        for line in (*diff.stdout.splitlines(), *untracked.stdout.splitlines())
        if line.strip()
    }


def build_payload(
    contract_path: Path,
    root: Path,
    contract: PlatformContract,
    results: list[AuditResult],
    diff_base: str,
) -> dict[str, Any]:
    counts = {status: sum(result.status == status for result in results) for status in STATUS_ORDER}
    required_missing = sum(result.required and result.status == "MISSING" for result in results)
    contract_payload = asdict(contract)
    if contract.schema_version == 1:
        # Preserve the schema-v1 JSON surface byte-for-byte at the field level.
        # ``host_policy='static'`` is an internal compatibility default; only
        # schema v2 makes that policy part of the machine-readable contract.
        contract_payload.pop("host_policy", None)
    return {
        "tool": "platform-source-registration-inventory",
        "disclaimer": DISCLAIMER,
        "repository_root": str(root),
        "contract_path": str(contract_path),
        "diff_base": diff_base or None,
        "contract": contract_payload,
        "summary": {
            **counts,
            "required_missing": required_missing,
            "registration_check_passed": required_missing == 0,
            "fully_verified": required_missing == 0 and counts["MANUAL"] == 0,
        },
        "results": [asdict(result) for result in results],
    }


def print_human(payload: dict[str, Any]) -> None:
    contract = payload["contract"]
    print("Platform source registration inventory")
    print(f"Source: {contract['display_name']} ({contract['canonical_slug']})")
    print(f"Integration level: {contract['integration_level']}")
    if payload["diff_base"]:
        print(
            f"Diff base: {payload['diff_base']} (annotations only; audit still reads the worktree)"
        )
    print(f"Scope: {payload['disclaimer']}")
    print()
    for result in payload["results"]:
        marker = "required" if result["required"] else "advisory"
        print(f"{result['status']:<7} [{marker:<8}] {result['capability']}: {result['label']}")
        print(f"         {result['detail']}")
        for evidence in result["evidence"]:
            changed = evidence["changed_since_base"]
            suffix = " [diff]" if changed is True else " [unchanged]" if changed is False else ""
            print(f"         - {evidence['path']}:{evidence['line']}{suffix} {evidence['excerpt']}")
    summary = payload["summary"]
    rendered = " ".join(f"{status}={summary[status]}" for status in STATUS_ORDER)
    print()
    print(f"Summary: {rendered}; required_missing={summary['required_missing']}")
    if summary["MANUAL"]:
        print("Manual items remain open; they are not counted as PASS.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path, help="platform contract TOML")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when any required registration is MISSING",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--diff-base",
        default="",
        help="annotate evidence changed since this git ref; does not weaken full-tree checks",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract_path = args.contract.expanduser().resolve()
    try:
        contract = load_contract(contract_path)
        root = find_repo_root(contract_path)
        changed = changed_files_since(root, args.diff_base) if args.diff_base else None
        results = Inventory(root, contract, changed).run()
        payload = build_payload(contract_path, root, contract, results, args.diff_base)
    except ContractError as exc:
        print(f"contract audit error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(payload)
    if args.check and payload["summary"]["required_missing"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
