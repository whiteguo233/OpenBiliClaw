"""Durable, credential-free Instagram browser tasks and profile events.

The browser extension owns Instagram cookies and same-origin requests.  The
backend stores only immutable task policy plus bounded public media/user rows.
Task completion uses the shared stage-first/first-final-wins protocol so a
lost HTTP acknowledgement can be replayed without replacing canonical data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.event_format import build_event
from openbiliclaw.sources.instagram import (
    INSTAGRAM_SOURCE_STRATEGIES,
    instagram_item_to_content,
)

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

INSTAGRAM_TASK_TYPES = frozenset({"discover", "bootstrap_events"})
INSTAGRAM_DISCOVER_MODES = frozenset({"topic", "creator"})
INSTAGRAM_BOOTSTRAP_SCOPES = (
    "instagram_liked",
    "instagram_saved",
    "instagram_following",
)
INSTAGRAM_BOOTSTRAP_PURPOSES = frozenset({"guided-init", "fetch", "smoke", "profile-rebuild"})
INSTAGRAM_BOOTSTRAP_SCOPE_EVENT_TYPES = {
    "instagram_liked": "like",
    "instagram_saved": "favorite",
    "instagram_following": "follow",
}
INSTAGRAM_BOOTSTRAP_SIGNAL_STRENGTH = {
    "instagram_liked": 0.85,
    "instagram_saved": 1.0,
    "instagram_following": 0.65,
}
INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD = "_instagram_heartbeat_evidence_at"

_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")
_ACCOUNT_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_MAX_ITEMS = 300
_MAX_DISCOVER_PAGES = 20
_MAX_PAGES = 100
_TERMINAL_STATUSES = frozenset({"ok", "empty", "partial", "failed"})
_DEBUG_STATUSES = frozenset(
    {
        "ok",
        "empty",
        "partial",
        "login_required",
        "rate_limited",
        "challenge",
        "challenge_required",
        "schema_changed",
        "invalid_json",
        "parse_error",
        "timeout",
        "failed",
    }
)
INSTAGRAM_LOGIN_FAILURE_CODES = frozenset(
    {"instagram_login_required", "login_required", "http_401", "http_403"}
)
INSTAGRAM_CHALLENGE_FAILURE_CODES = frozenset({"challenge", "challenge_required"})
INSTAGRAM_RATE_LIMIT_FAILURE_CODES = frozenset({"rate_limited", "http_429"})
_INSTAGRAM_FAILURE_CODES = frozenset(
    {
        *INSTAGRAM_LOGIN_FAILURE_CODES,
        *INSTAGRAM_CHALLENGE_FAILURE_CODES,
        *INSTAGRAM_RATE_LIMIT_FAILURE_CODES,
        "account_identity_missing",
        "bounded_public_snapshot",
        "cancelled",
        "creator_not_public",
        "cursor_resume_not_observed",
        "duplicate_cursor",
        "executor_failed",
        "extension_result_timeout",
        "failed",
        "html_response",
        "http_error",
        "instagram_account_changed",
        "instagram_persisted_state_invalid",
        "instagram_session_storage_unavailable",
        "instagram_task_progress_identity_mismatch",
        "instagram_task_progress_invalid",
        "instagram_task_progress_sender_mismatch",
        "instagram_task_result_identity_mismatch",
        "instagram_task_result_sender_mismatch",
        "instagram_task_state_unavailable",
        "invalid_json",
        "invalid_task_identity",
        "item_cap_reached",
        "items_envelope_missing",
        "network_error",
        "next_cursor_missing",
        "page_cap_reached",
        "progress_stalled",
        "parse_error",
        "progress_persistence_unavailable",
        "public_page_unavailable",
        "recovery_tab_gone",
        "recovery_tab_not_owned",
        "request_timeout",
        "response_envelope_unobserved",
        "response_rows_rejected",
        "response_schema_degraded",
        "response_too_large",
        "schema_changed",
        "send_message_failed_after_reload",
        "source_disabled",
        "tab_create_failed",
        "tab_id_unknown",
        "task_absolute_timeout",
        "task_claim_conflict",
        "task_idle_timeout",
        "terminal_evidence_missing",
        "timeout",
        "unexpected_discover_route",
        "unexpected_mime",
    }
)
_HTTP_FAILURE_RE = re.compile(r"^http_[1-5][0-9]{2}$")
_TERMINAL_EVIDENCE_CODES = frozenset(
    {
        "accepted_nonterminal_scope_page",
        "all_collections_terminal",
        "all_scopes_complete",
        "bounded_nonterminal_page",
        "durable_partial_progress_before_timeout",
        "durable_partial_progress_merged",
        "explicit_empty",
        "recognized_envelope_has_next_page_false",
        "response_schema_degraded",
        "response_unobserved",
    }
)


def normalize_instagram_failure_code(value: object) -> str:
    """Return one frozen machine failure code, optionally scope-prefixed.

    Free-form exception text and response excerpts are deliberately rejected;
    accepting only this grammar is what makes result diagnostics safe to keep.
    """

    if not isinstance(value, str):
        return ""
    candidate = value.strip().casefold()
    if not candidate or candidate.count(":") > 1:
        return ""
    scope, separator, code = candidate.rpartition(":")
    if not separator:
        code = candidate
        scope = ""
    if scope and scope not in INSTAGRAM_BOOTSTRAP_SCOPES:
        return ""
    if code not in _INSTAGRAM_FAILURE_CODES and not _HTTP_FAILURE_RE.fullmatch(code):
        return ""
    return f"{scope}:{code}" if scope else code


def instagram_failure_category(*values: object) -> str:
    """Classify exact canonical codes without substring heuristics."""

    for value in values:
        normalized = normalize_instagram_failure_code(value)
        if not normalized:
            continue
        code = normalized.rpartition(":")[2]
        if code in INSTAGRAM_LOGIN_FAILURE_CODES:
            return "login_required"
        if code in INSTAGRAM_CHALLENGE_FAILURE_CODES:
            return "challenge"
        if code in INSTAGRAM_RATE_LIMIT_FAILURE_CODES:
            return "rate_limited"
    return ""


def _normalize_terminal_evidence(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip().casefold()
    if candidate in _TERMINAL_EVIDENCE_CODES:
        return candidate
    scopes = candidate.split(",")
    if not scopes or len(scopes) != len(set(scopes)):
        return ""
    if any(scope not in INSTAGRAM_BOOTSTRAP_SCOPES for scope in scopes):
        return ""
    selected = set(scopes)
    return ",".join(scope for scope in INSTAGRAM_BOOTSTRAP_SCOPES if scope in selected)


def _text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:limit]


def _positive_id(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.isdigit() and int(candidate) > 0 and len(candidate) <= 40:
            return candidate
    return ""


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(maximum, max(minimum, result))


def _keyword_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized > 0 else None


def instagram_account_key(account_id: object) -> str:
    """Hash a positively resolved same-origin current-account numeric id."""

    normalized = _positive_id(account_id)
    if not normalized:
        return ""
    digest = hashlib.sha256(f"instagram:id:{normalized}".encode()).hexdigest()
    return f"sha256:{digest}"


def is_instagram_account_key(value: object) -> bool:
    return bool(_ACCOUNT_KEY_RE.fullmatch(str(value or "").strip()))


def is_instagram_username(value: object) -> bool:
    """Return whether *value* is a valid creator-route username."""

    return isinstance(value, str) and _USERNAME_RE.fullmatch(value.strip()) is not None


def _normalize_task_payload(task_type: object, payload: object) -> dict[str, Any]:
    normalized_type = _text(task_type, limit=32)
    if normalized_type not in INSTAGRAM_TASK_TYPES:
        raise ValueError(f"unsupported Instagram task type: {normalized_type or task_type!r}")
    if not isinstance(payload, dict):
        raise ValueError("Instagram task payload must be an object")

    if normalized_type == "discover":
        mode = _text(payload.get("mode"), limit=32).casefold()
        if mode not in INSTAGRAM_DISCOVER_MODES:
            raise ValueError("Instagram discover mode must be topic or creator")
        query = _text(payload.get("query"), limit=200)
        topic = _text(payload.get("topic"), limit=120)
        username = _text(payload.get("username"), limit=30)
        if mode == "topic":
            topic = topic or query
            if not topic:
                raise ValueError("Instagram topic discover requires topic or query")
            query = query or topic
            username = ""
        else:
            if not _USERNAME_RE.fullmatch(username):
                raise ValueError("Instagram creator discover requires a valid username")
            query = query or username
            topic = ""
        result: dict[str, Any] = {
            "mode": mode,
            "query": query,
            "max_items": _bounded_int(
                payload.get("max_items"), default=100, minimum=1, maximum=_MAX_ITEMS
            ),
            "max_pages": _bounded_int(
                payload.get("max_pages"),
                default=20,
                minimum=1,
                maximum=_MAX_DISCOVER_PAGES,
            ),
            "request_interval_ms": _bounded_int(
                payload.get("request_interval_ms"),
                default=3_000,
                minimum=1_000,
                maximum=30_000,
            ),
        }
        if topic:
            result["topic"] = topic
        if username:
            result["username"] = username.casefold()
        cursor = _text(payload.get("cursor"), limit=2_048)
        if cursor:
            result["cursor"] = cursor
        source_keyword_id = _keyword_id(payload.get("source_keyword_id"))
        if source_keyword_id is not None:
            result["source_keyword_id"] = source_keyword_id
        return result

    raw_scopes = payload.get("scopes")
    if raw_scopes is not None:
        if not isinstance(raw_scopes, list):
            raise ValueError("Instagram bootstrap scopes must be a list")
        scopes = tuple(
            value.strip() for value in raw_scopes if isinstance(value, str) and value.strip()
        )
        if len(scopes) != len(set(scopes)) or frozenset(scopes) != frozenset(
            INSTAGRAM_BOOTSTRAP_SCOPES
        ):
            raise ValueError("Instagram bootstrap scopes are fixed")
    profile_update = payload.get("profile_update") is True
    smoke_only = payload.get("smoke_only") is True
    profile_rebuild = payload.get("profile_rebuild") is True
    purpose = _text(payload.get("purpose"), limit=32).casefold()
    if purpose not in INSTAGRAM_BOOTSTRAP_PURPOSES:
        purpose = (
            "smoke"
            if smoke_only
            else "profile-rebuild"
            if profile_rebuild
            else "guided-init"
            if profile_update
            else "fetch"
        )
    result = {
        "scopes": list(INSTAGRAM_BOOTSTRAP_SCOPES),
        "max_items_per_scope": _bounded_int(
            payload.get("max_items_per_scope"),
            default=_MAX_ITEMS,
            minimum=1,
            maximum=_MAX_ITEMS,
        ),
        "max_pages_per_scope": _bounded_int(
            payload.get("max_pages_per_scope"),
            default=20,
            minimum=1,
            maximum=_MAX_PAGES,
        ),
        "request_interval_ms": _bounded_int(
            payload.get("request_interval_ms"),
            default=3_000,
            minimum=1_000,
            maximum=30_000,
        ),
        "profile_update": profile_update,
        "smoke_only": smoke_only,
        "profile_rebuild": profile_rebuild,
        "purpose": purpose,
        "expected_account_key": (
            str(payload.get("expected_account_key") or "").strip()
            if is_instagram_account_key(payload.get("expected_account_key"))
            else ""
        ),
    }
    if payload.get("incremental") is True:
        raise ValueError("Instagram profile refresh is init-only")
    return result


def _strategy_for_payload(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "topic")
    return INSTAGRAM_SOURCE_STRATEGIES.get(mode, INSTAGRAM_SOURCE_STRATEGIES["topic"])


def _sanitized_item(
    raw: object,
    *,
    payload: dict[str, Any],
    bootstrap: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    scope = _text(raw.get("scope"), limit=64)
    if bootstrap:
        if scope not in INSTAGRAM_BOOTSTRAP_SCOPES:
            return {}
    else:
        scope = ""
    content = instagram_item_to_content(
        raw,
        strategy=("instagram-bootstrap" if bootstrap else _strategy_for_payload(payload)),
        source_keyword_id=(None if bootstrap else _keyword_id(payload.get("source_keyword_id"))),
    )
    if content is None:
        return {}
    is_user = content.content_type == "user"
    if bootstrap and scope == "instagram_following" and not is_user:
        return {}
    if bootstrap and scope != "instagram_following" and is_user:
        return {}

    raw_id = content.content_id.removeprefix("user:") if is_user else content.content_id
    item: dict[str, Any] = {
        "id": raw_id,
        "content_type": content.content_type,
        "url": content.content_url,
        "title": content.title,
    }
    if scope:
        item["scope"] = scope
    code_match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)/", content.content_url)
    if code_match is not None:
        item["code"] = code_match.group(1)
    if content.body_text:
        item["description"] = content.body_text
    if content.cover_url:
        item["cover_url"] = content.cover_url
    author_id = _positive_id(raw.get("author_id"))
    if not author_id and isinstance(raw.get("user"), dict):
        author_id = _positive_id(raw["user"].get("id") or raw["user"].get("pk"))
    if is_user:
        author_id = author_id or raw_id
    if author_id:
        item["author_id"] = author_id
    if content.author_name:
        item["author_name"] = content.author_name
    if content.published_at:
        item["published_at"] = content.published_at
    return item


def instagram_bootstrap_item_key(item: dict[str, Any], *, account_key: str = "") -> str:
    """Return an account/scope/type-partitioned event identity."""

    scope = _text(item.get("scope"), limit=64)
    content_type = _text(item.get("content_type"), limit=32)
    item_id = _positive_id(item.get("id") or item.get("content_id"))
    if scope not in INSTAGRAM_BOOTSTRAP_SCOPES or not content_type or not item_id:
        return ""
    prefix = f"{account_key}:" if is_instagram_account_key(account_key) else ""
    return f"{prefix}{scope}:{content_type}:{item_id}"


def instagram_bootstrap_items_to_events(
    items: list[dict[str, Any]],
    *,
    account_key: str,
) -> list[dict[str, Any]]:
    """Map liked/saved/following membership to unified profile events."""

    if not is_instagram_account_key(account_key):
        return []
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    policy: dict[str, Any] = {
        "scopes": list(INSTAGRAM_BOOTSTRAP_SCOPES),
        "mode": "topic",
    }
    for raw in items:
        item = _sanitized_item(raw, payload=policy, bootstrap=True)
        if not item:
            continue
        key = instagram_bootstrap_item_key(item, account_key=account_key)
        if not key or key in seen:
            continue
        seen.add(key)
        scope = str(item["scope"])
        event_type = INSTAGRAM_BOOTSTRAP_SCOPE_EVENT_TYPES[scope]
        author = _text(item.get("author_name"), limit=128)
        title = _text(item.get("title"), limit=300) or author or str(item["url"])
        label = {
            "instagram_liked": "点赞",
            "instagram_saved": "收藏",
            "instagram_following": "关注",
        }[scope]
        context = f"Instagram{label}：{title}"
        if author and scope != "instagram_following":
            context += f" 作者：{author}"
        metadata: dict[str, Any] = {
            "source_platform": "instagram",
            "content_type": item["content_type"],
            "content_id": item["id"],
            "scope": scope,
            "import_source": f"instagram_bootstrap_{scope.removeprefix('instagram_')}",
            "signal_strength": INSTAGRAM_BOOTSTRAP_SIGNAL_STRENGTH[scope],
            "account_key": account_key,
        }
        for field in ("author_id", "published_at", "description"):
            if item.get(field):
                metadata[field] = item[field]
        events.append(
            build_event(
                event_type=event_type,
                source_platform="instagram",
                title=title,
                url=str(item["url"]),
                author=author,
                context=context,
                metadata=metadata,
            )
        )
    return events


def _sanitize_debug(raw: object) -> dict[str, Any]:
    """Persist only bounded diagnostics, never raw request/response material."""

    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for field in (
        "cursor_observed",
        "identity_resolved",
        "identity_verified",
        "response_observed",
        "authenticated_topic_observed",
        "schema_degraded",
    ):
        if isinstance(raw.get(field), bool):
            result[field] = raw[field]
    raw_evidence = raw.get("terminal_evidence")
    evidence = _normalize_terminal_evidence(raw_evidence)
    if raw_evidence not in (None, "") and not evidence:
        raise ValueError("invalid Instagram terminal evidence code")
    if evidence:
        result["terminal_evidence"] = evidence
    raw_error_code = raw.get("error_code")
    error_code = normalize_instagram_failure_code(raw_error_code)
    if raw_error_code not in (None, "") and not error_code:
        raise ValueError("invalid Instagram debug error code")
    if error_code:
        result["error_code"] = error_code
    source_keyword_id = _keyword_id(raw.get("source_keyword_id"))
    if source_keyword_id is not None:
        result["source_keyword_id"] = source_keyword_id
    status = _text(raw.get("status"), limit=32).casefold()
    if status in _DEBUG_STATUSES:
        result["status"] = status
    failures = raw.get("failures")
    if failures is not None:
        if not isinstance(failures, list):
            raise ValueError("Instagram debug failures must be a list")
        normalized_failures: list[str] = []
        for value in failures[:20]:
            failure = normalize_instagram_failure_code(value)
            if not failure:
                raise ValueError("invalid Instagram debug failure code")
            if failure not in normalized_failures:
                normalized_failures.append(failure)
        if normalized_failures:
            result["failures"] = normalized_failures
    return result


def _candidate_dict(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    content = instagram_item_to_content(
        item,
        strategy=_strategy_for_payload(payload),
        source_keyword_id=_keyword_id(payload.get("source_keyword_id")),
    )
    if content is None:
        return None
    # ``asdict`` is safe here: DiscoveredContent contains only normalized
    # scalar/list fields, and this preserves the exact shared candidate schema.
    return asdict(content)


def _merge_result(
    current: dict[str, Any],
    *,
    task_type: str,
    payload: dict[str, Any],
    items: list[dict[str, Any]] | None,
    scope_complete: dict[str, Any] | None,
    account_id: object,
    debug: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bootstrap = task_type == "bootstrap_events"
    account_key = instagram_account_key(account_id)
    existing_account_key = str(current.get("account_key") or "")
    if bootstrap and items and not account_key:
        raise ValueError("Instagram bootstrap items require a numeric current account id")
    if (
        account_key
        and existing_account_key
        and not secrets.compare_digest(account_key, existing_account_key)
    ):
        raise PermissionError("instagram_account_changed")
    account_key = account_key or existing_account_key

    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_scope: dict[str, int] = {scope: 0 for scope in INSTAGRAM_BOOTSTRAP_SCOPES}
    max_per_scope = int(payload.get("max_items_per_scope") or _MAX_ITEMS)
    max_items = int(payload.get("max_items") or _MAX_ITEMS)
    added: list[dict[str, Any]] = []

    def admit(raw: object, *, incoming: bool) -> None:
        item = _sanitized_item(raw, payload=payload, bootstrap=bootstrap)
        if not item:
            return
        if bootstrap:
            scope = str(item["scope"])
            key = instagram_bootstrap_item_key(item, account_key=account_key)
            if per_scope[scope] >= max_per_scope:
                return
        else:
            scope = ""
            key = f"{item['content_type']}:{item['id']}"
            if len(combined) >= max_items:
                return
        if not key or key in seen:
            return
        seen.add(key)
        combined.append(item)
        if bootstrap:
            per_scope[scope] += 1
        if incoming:
            added.append(item)

    current_items = current.get("items")
    if isinstance(current_items, list):
        for value in current_items:
            admit(value, incoming=False)
    if isinstance(items, list):
        for value in items:
            admit(value, incoming=True)

    merged: dict[str, Any] = {"items": combined}
    if bootstrap:
        if account_key:
            merged["account_key"] = account_key
        merged["scope_counts"] = per_scope
        completeness = {scope: False for scope in INSTAGRAM_BOOTSTRAP_SCOPES}
        current_complete = current.get("scope_complete")
        if isinstance(current_complete, dict):
            for scope in INSTAGRAM_BOOTSTRAP_SCOPES:
                completeness[scope] = current_complete.get(scope) is True
        if isinstance(scope_complete, dict):
            for scope in INSTAGRAM_BOOTSTRAP_SCOPES:
                if scope_complete.get(scope) is True:
                    completeness[scope] = True
        merged["scope_complete"] = completeness
    else:
        merged["mode"] = payload["mode"]
        merged["query"] = payload["query"]
        source_keyword_id = _keyword_id(payload.get("source_keyword_id"))
        if source_keyword_id is not None:
            merged["source_keyword_id"] = source_keyword_id
        candidates = [
            candidate
            for item in combined
            if (candidate := _candidate_dict(item, payload)) is not None
        ]
        merged["candidates"] = candidates

    merged_debug = _sanitize_debug(current.get("debug"))
    merged_debug.update(_sanitize_debug(debug))
    if not bootstrap:
        merged_debug["mode"] = str(payload["mode"])
        merged_debug["query"] = str(payload["query"])
        source_keyword_id = _keyword_id(payload.get("source_keyword_id"))
        if source_keyword_id is not None:
            merged_debug["source_keyword_id"] = source_keyword_id
    if merged_debug:
        merged["debug"] = merged_debug
    return merged, added


def _validate_terminal(
    result: dict[str, Any],
    *,
    task_type: str,
    payload: dict[str, Any],
    terminal_status: str,
    error: str,
    account_id: object,
) -> dict[str, Any]:
    status = _text(terminal_status, limit=32).casefold()
    if status not in _TERMINAL_STATUSES:
        raise ValueError("unsupported Instagram terminal status")
    result = dict(result)
    result["status"] = status
    normalized_error = normalize_instagram_failure_code(error)
    if error.strip() and not normalized_error:
        raise ValueError("invalid Instagram machine error code")
    if normalized_error:
        result["error"] = normalized_error
    raw_debug = result.get("debug")
    debug: dict[str, Any] = dict(raw_debug) if isinstance(raw_debug, dict) else {}
    evidence = _text(debug.get("terminal_evidence"), limit=120)
    items = result.get("items") if isinstance(result.get("items"), list) else []

    if status == "failed":
        if not normalized_error:
            raise ValueError("failed Instagram result requires an error")
        if items:
            raise ValueError("failed Instagram result cannot contain items; use partial")
        return result
    if not evidence or debug.get("response_observed") is not True:
        raise ValueError("Instagram terminal result lacks affirmative response evidence")

    if task_type == "discover":
        if status == "empty" and items:
            raise ValueError("empty Instagram discover result cannot contain items")
        if status == "ok" and not items:
            raise ValueError("zero-row Instagram discover must use empty")
        if status == "partial" and not items:
            raise ValueError("partial Instagram discover must retain accepted items")
        return result

    if not instagram_account_key(account_id):
        raise ValueError("Instagram bootstrap terminal requires current account id")
    requested = tuple(payload.get("scopes") or INSTAGRAM_BOOTSTRAP_SCOPES)
    completeness = result.get("scope_complete")
    if not isinstance(completeness, dict):
        raise ValueError("Instagram bootstrap terminal lacks scope completeness")
    all_complete = all(completeness.get(scope) is True for scope in requested)
    if status in {"ok", "empty"} and not all_complete:
        raise ValueError("complete Instagram bootstrap requires every scope complete")
    if status == "partial" and all_complete:
        raise ValueError("partial Instagram bootstrap must retain an incomplete scope")
    if status == "empty" and items:
        raise ValueError("empty Instagram bootstrap cannot contain items")
    if status == "ok" and not items:
        raise ValueError("zero-row Instagram bootstrap must use empty")
    return result


def sanitize_instagram_task_result(
    task_type: str,
    payload: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    items: list[dict[str, Any]] | None = None,
    scope_counts: dict[str, Any] | None = None,
    scope_complete: dict[str, Any] | None = None,
    account_id: object = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical safe subset of one task callback.

    ``scope_counts`` is accepted for wire compatibility but never trusted: the
    canonical count is recomputed from admitted bounded rows.
    """

    del scope_counts
    normalized_payload = _normalize_task_payload(task_type, payload)
    merged, _ = _merge_result(
        current or {},
        task_type=task_type,
        payload=normalized_payload,
        items=items,
        scope_complete=scope_complete,
        account_id=account_id,
        debug=debug,
    )
    return merged


class InstagramTaskQueue:
    """Durable queue with one atomic Instagram lease across extensions."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS instagram_tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                claimed_at TIMESTAMP,
                claim_token TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_instagram_tasks_status
                ON instagram_tasks (status, created_at);
            CREATE INDEX IF NOT EXISTS idx_instagram_tasks_type_created
                ON instagram_tasks (type, created_at);
            """
        )
        self._db.conn.commit()

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 10,
    ) -> str | None:
        normalized_payload = _normalize_task_payload(task_type, payload)
        budget_lane = (
            f"discover:{normalized_payload['mode']}" if task_type == "discover" else task_type
        )
        if daily_budget > 0 and self._budgeted_count_today(budget_lane) >= daily_budget:
            logger.info("instagram task budget exhausted: type=%s", task_type)
            return None
        task_id = str(uuid.uuid4())
        participating = bool(self._db.conn.in_transaction)
        self._db.conn.execute(
            "INSERT INTO instagram_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(normalized_payload, ensure_ascii=False)),
        )
        if not participating:
            self._db.conn.commit()
        return task_id

    def _budgeted_count_today(self, budget_lane: str) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if budget_lane.startswith("discover:"):
            mode = budget_lane.partition(":")[2]
            rows = self._db.conn.execute(
                "SELECT payload_json FROM instagram_tasks WHERE type='discover' AND created_at>=?",
                (today,),
            ).fetchall()
            count = 0
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"] or "{}"))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("mode") == mode:
                    count += 1
            return count
        row = self._db.conn.execute(
            "SELECT COUNT(*) FROM instagram_tasks WHERE type=? AND created_at>=?",
            (budget_lane, today),
        ).fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _public_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        try:
            payload = json.loads(str(result.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key, value in payload.items():
                result.setdefault(key, value)
        return result

    def next_pending(
        self,
        only_ids: set[str] | None = None,
        *,
        task_types: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        # The extension's bounded absolute deadline is 12 minutes.  Keep the
        # backend lease longer so another browser cannot steal legitimate
        # pagination, while still allowing crash recovery afterwards.
        stale_before = (datetime.now(UTC) - timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M:%S")
        where = (
            "(status='pending' OR (status='in_progress' AND (claimed_at IS NULL OR claimed_at<=?)))"
        )
        params: list[Any] = [stale_before]
        if only_ids is not None:
            ids = [str(value) for value in only_ids if str(value).strip()]
            if not ids:
                return None
            where += f" AND id IN ({','.join('?' for _ in ids)})"
            params.extend(ids)
        if task_types is not None:
            selected_types = [str(value).strip() for value in task_types if str(value).strip()]
            if not selected_types:
                return None
            where += f" AND type IN ({','.join('?' for _ in selected_types)})"
            params.extend(selected_types)
        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM instagram_tasks WHERE status='in_progress' AND claimed_at>? LIMIT 1",
                (stale_before,),
            ).fetchone()
            if active is not None:
                conn.commit()
                return None
            row = conn.execute(
                f"SELECT * FROM instagram_tasks WHERE {where} "
                "ORDER BY created_at ASC, rowid ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            claim_token = str(uuid.uuid4())
            conn.execute(
                "UPDATE instagram_tasks SET status='in_progress', "
                "claimed_at=CURRENT_TIMESTAMP, claim_token=? WHERE id=?",
                (claim_token, task_id),
            )
            claimed = conn.execute(
                "SELECT * FROM instagram_tasks WHERE id=?", (task_id,)
            ).fetchone()
            conn.commit()
            return self._public_row(claimed) if claimed is not None else None
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def claim_token_matches(self, task_id: str, claim_token: str) -> bool:
        token = str(claim_token or "").strip()
        if not token:
            return False
        row = self._db.conn.execute(
            "SELECT claim_token FROM instagram_tasks WHERE id=?", (task_id,)
        ).fetchone()
        expected = str(row["claim_token"] or "") if row is not None else ""
        return bool(expected) and secrets.compare_digest(expected, token)

    def find_recent_task(
        self,
        task_type: str,
        *,
        recent_hours: float,
        statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        if recent_hours <= 0:
            return None
        selected = statuses or _RECENT_TASK_STATUSES
        placeholders = ",".join("?" for _ in selected)
        cutoff = (datetime.now(UTC) - timedelta(hours=recent_hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = self._db.conn.execute(
            f"SELECT * FROM instagram_tasks WHERE type=? AND created_at>=? "
            f"AND status IN ({placeholders}) ORDER BY "
            "CASE WHEN status IN ('pending','in_progress') THEN 0 "
            "WHEN status='completed' THEN 1 ELSE 2 END, created_at DESC LIMIT 1",
            (task_type, cutoff, *selected),
        ).fetchone()
        return self._public_row(row) if row is not None else None

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.conn.execute(
            "SELECT * FROM instagram_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return self._public_row(row) if row is not None else None

    def _task_policy(self, task_id: str) -> tuple[str, dict[str, Any]]:
        row = self._db.conn.execute(
            "SELECT type, payload_json FROM instagram_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        task_type = str(row["type"])
        try:
            raw_payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid stored Instagram task policy") from exc
        return task_type, _normalize_task_payload(task_type, raw_payload)

    def merge_result(
        self,
        task_id: str,
        *,
        claim_token: str,
        items: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        scope_complete: dict[str, Any] | None = None,
        account_id: object = None,
        debug: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        from openbiliclaw.sources.task_result_protocol import mutate_unstaged_result

        del scope_counts
        task_type, payload = self._task_policy(task_id)
        added: list[dict[str, Any]] = []

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            nonlocal added
            merged, added = _merge_result(
                current,
                task_type=task_type,
                payload=payload,
                items=items,
                scope_complete=scope_complete,
                account_id=account_id,
                debug=debug,
            )
            return merged

        mutated, _ = mutate_unstaged_result(
            self._db,
            table="instagram_tasks",
            task_id=task_id,
            mutate=mutate,
            expected_claim_token=claim_token,
        )
        return added if mutated else []

    def stage_final_result(
        self,
        task_id: str,
        *,
        terminal_status: str,
        claim_token: str,
        items: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        scope_complete: dict[str, Any] | None = None,
        account_id: object = None,
        error: str = "",
        debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        staged, _fresh = self.stage_final_result_with_freshness(
            task_id,
            terminal_status=terminal_status,
            claim_token=claim_token,
            items=items,
            scope_counts=scope_counts,
            scope_complete=scope_complete,
            account_id=account_id,
            error=error,
            debug=debug,
        )
        return staged

    def stage_final_result_with_freshness(
        self,
        task_id: str,
        *,
        terminal_status: str,
        claim_token: str,
        items: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        scope_complete: dict[str, Any] | None = None,
        account_id: object = None,
        error: str = "",
        debug: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Stage one final and report whether this callback won atomically."""

        from openbiliclaw.sources.task_result_protocol import (
            STAGED_TERMINAL_STATUS_FIELD,
            mutate_unstaged_result,
        )

        del scope_counts
        task_type, payload = self._task_policy(task_id)
        normalized_status = _text(terminal_status, limit=32).casefold()

        def merge(current: dict[str, Any]) -> dict[str, Any]:
            merged, _ = _merge_result(
                current,
                task_type=task_type,
                payload=payload,
                items=items,
                scope_complete=scope_complete,
                account_id=account_id,
                debug=debug,
            )
            canonical = _validate_terminal(
                merged,
                task_type=task_type,
                payload=payload,
                terminal_status=normalized_status,
                error=error,
                account_id=account_id,
            )
            if task_type == "bootstrap_events":
                # Server-owned evidence time lets a replay repair a crash
                # between canonical staging and heartbeat projection without
                # overwriting a browser heartbeat observed after the stage.
                canonical[INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD] = datetime.now(UTC).isoformat()
            canonical[STAGED_TERMINAL_STATUS_FIELD] = normalized_status
            return canonical

        fresh, staged = mutate_unstaged_result(
            self._db,
            table="instagram_tasks",
            task_id=task_id,
            mutate=merge,
            expected_claim_token=claim_token,
        )
        return dict(staged), bool(fresh)

    def complete_staged_result(self, task_id: str, *, claim_token: str) -> bool:
        from openbiliclaw.sources.task_result_protocol import complete_staged_result

        return bool(
            complete_staged_result(
                self._db,
                table="instagram_tasks",
                task_id=task_id,
                expected_claim_token=claim_token,
            )
        )

    def complete(self, task_id: str, *, claim_token: str) -> bool:
        """API-friendly alias for completing an already-staged result."""

        return self.complete_staged_result(task_id, claim_token=claim_token)

    def fail(
        self,
        task_id: str,
        *,
        claim_token: str,
        error: str,
        debug: dict[str, Any] | None = None,
    ) -> bool:
        from openbiliclaw.sources.task_result_protocol import mutate_unstaged_result

        normalized_error = normalize_instagram_failure_code(error)
        if not normalized_error:
            raise ValueError("Instagram task failure requires a machine error code")
        result: dict[str, Any] = {"status": "failed", "error": normalized_error}
        if clean_debug := _sanitize_debug(debug):
            result["debug"] = clean_debug
        mutated, _ = mutate_unstaged_result(
            self._db,
            table="instagram_tasks",
            task_id=task_id,
            mutate=lambda _current: result,
            terminal_status="failed",
            expected_claim_token=claim_token,
        )
        return bool(mutated)

    def fail_account_mismatch(self, task_id: str, *, claim_token: str) -> bool:
        """Terminalize a mismatched bootstrap claim without admitting its rows.

        An unstaged task receives a frozen, row-free failure payload.  If a
        first-final payload was already staged, preserve that canonical JSON
        byte-for-byte and only quarantine the task as failed so it cannot hold
        the global Instagram lease indefinitely.
        """

        return self._quarantine_bootstrap_claim(
            task_id,
            claim_token=claim_token,
            error="instagram_account_changed",
        )

    def fail_identity_missing(self, task_id: str, *, claim_token: str) -> bool:
        """Terminalize a bootstrap claim that lacks authoritative identity."""

        return self._quarantine_bootstrap_claim(
            task_id,
            claim_token=claim_token,
            error="account_identity_missing",
        )

    def _quarantine_bootstrap_claim(
        self,
        task_id: str,
        *,
        claim_token: str,
        error: str,
    ) -> bool:
        """Release one invalid bootstrap claim without rewriting staged evidence."""

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        normalized_error = normalize_instagram_failure_code(error)
        if not normalized_error:
            raise ValueError("Instagram bootstrap quarantine requires a machine error code")

        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT type, status, result_json, claim_token FROM instagram_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            expected_token = str(row["claim_token"] or "")
            provided_token = str(claim_token or "")
            if (
                not expected_token
                or not provided_token
                or not secrets.compare_digest(
                    expected_token,
                    provided_token,
                )
            ):
                raise PermissionError("task_claim_conflict")
            if str(row["type"] or "").strip() != "bootstrap_events":
                raise ValueError("account mismatch terminal is bootstrap-only")
            if str(row["status"] or "").strip() in {"completed", "failed"}:
                conn.commit()
                return False

            current = parse_task_result(row["result_json"])
            if staged_terminal_status(current):
                conn.execute(
                    "UPDATE instagram_tasks SET status='failed', "
                    "completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (task_id,),
                )
            else:
                failure = {
                    "status": "failed",
                    "error": normalized_error,
                    "cancelled": True,
                }
                conn.execute(
                    "UPDATE instagram_tasks SET result_json=?, status='failed', "
                    "completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(failure, ensure_ascii=False), task_id),
                )
            conn.commit()
            return True
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "cancelled",
        claim_token: str | None = None,
    ) -> bool:
        """Cancel only an unstaged task while preserving first-final-wins."""

        from openbiliclaw.sources.task_result_protocol import mutate_unstaged_result

        error = normalize_instagram_failure_code(reason) or "cancelled"
        mutated, _ = mutate_unstaged_result(
            self._db,
            table="instagram_tasks",
            task_id=task_id,
            mutate=lambda _current: {
                "status": "failed",
                "error": error,
                "cancelled": True,
            },
            terminal_status="failed",
            expected_claim_token=claim_token,
        )
        return bool(mutated)


__all__ = [
    "INSTAGRAM_CHALLENGE_FAILURE_CODES",
    "INSTAGRAM_BOOTSTRAP_PURPOSES",
    "INSTAGRAM_BOOTSTRAP_SCOPES",
    "INSTAGRAM_BOOTSTRAP_SCOPE_EVENT_TYPES",
    "INSTAGRAM_DISCOVER_MODES",
    "INSTAGRAM_LOGIN_FAILURE_CODES",
    "INSTAGRAM_RATE_LIMIT_FAILURE_CODES",
    "InstagramTaskQueue",
    "instagram_failure_category",
    "is_instagram_username",
    "instagram_account_key",
    "INSTAGRAM_HEARTBEAT_EVIDENCE_AT_FIELD",
    "instagram_bootstrap_item_key",
    "instagram_bootstrap_items_to_events",
    "is_instagram_account_key",
    "normalize_instagram_failure_code",
    "sanitize_instagram_task_result",
]
