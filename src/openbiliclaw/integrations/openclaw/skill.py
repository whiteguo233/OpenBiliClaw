"""Protocol-neutral skill descriptors for the local agent bridge.

The descriptor names retain the ``openbiliclaw_`` prefix for compatibility
with existing OpenClaw workspaces.  Hermes, WorkBuddy and other hosts can
consume the same JSON schemas and handlers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from .errors import AdapterOperationError, AdapterValidationError
from .schemas import (
    AvoidanceProbeFeedbackRequest,
    ChatRequest,
    DelightFeedbackRequest,
    FeedbackRequest,
    InterestProbeFeedbackRequest,
    ProfileEditRequest,
    SavedItemRequest,
    SavedRemoveRequest,
    SavedSyncRequest,
)


@dataclass(slots=True)
class OpenClawSkillDescriptor:
    """One stable capability descriptor exposed to an agent host."""

    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)
    handler: Callable[[dict[str, object]], Awaitable[dict[str, object]]] | None = None


async def _run_handler(action: Callable[[], Awaitable[Any]]) -> dict[str, object]:
    try:
        result = await action()
        return {
            "ok": True,
            "data": asdict(result),
        }
    except AdapterValidationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "validation_error",
        }
    except AdapterOperationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "operation_error",
        }


def _to_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def build_openclaw_skills(adapter: Any) -> list[OpenClawSkillDescriptor]:
    """Build the complete current capability manifest.

    This function is the single registry for host-visible capabilities.  A
    core feature is not considered agent-integrated until it has a descriptor
    here, a CLI route where appropriate, and contract tests.
    """

    async def sync_account_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.sync_account)

    async def get_profile_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_profile)

    async def get_capabilities_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_capabilities)

    async def recommend_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            kwargs: dict[str, object] = {
                "limit": _to_int(payload.get("limit", 5), default=5),
                "refresh_if_needed": _to_bool(payload.get("refresh_if_needed", False)),
            }
            if "source_platform" in payload:
                kwargs["source_platform"] = str(payload.get("source_platform", ""))
            if "excluded_item_ids" in payload:
                kwargs["excluded_item_ids"] = _to_strings(payload.get("excluded_item_ids"))
            for key in ("request_id", "stream_ref", "explicit_intent"):
                if key in payload:
                    kwargs[key] = str(payload[key])
            return await adapter.recommend(**kwargs)

        return await _run_handler(action)

    async def reshuffle_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.reshuffle(
                limit=_to_int(payload.get("limit", 5), default=5),
                source_platform=str(payload.get("source_platform", "")),
                excluded_item_ids=_to_strings(payload.get("excluded_item_ids")),
                **{
                    key: str(payload[key])
                    for key in ("request_id", "stream_ref", "explicit_intent")
                    if key in payload
                },
            )

        return await _run_handler(action)

    async def append_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.append_recommendations(
                limit=_to_int(payload.get("limit", 10), default=10),
                source_platform=str(payload.get("source_platform", "")),
                excluded_item_ids=_to_strings(payload.get("excluded_item_ids")),
                **{
                    key: str(payload[key])
                    for key in ("request_id", "stream_ref", "explicit_intent")
                    if key in payload
                },
            )

        return await _run_handler(action)

    async def restore_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.restore_recommendations(
                stream_ref=str(payload.get("stream_ref", "openclaw")),
                batch_ref=str(payload.get("batch_ref", "")),
                source_platform=str(payload.get("source_platform", "")),
            )

        return await _run_handler(action)

    async def present_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.present_recommendations(
                batch_ref=str(payload.get("batch_ref", "")),
                ack_id=str(payload.get("ack_id", "")),
                item_keys=_to_strings(payload.get("item_keys")),
            )

        return await _run_handler(action)

    async def submit_feedback_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.submit_feedback(
                FeedbackRequest(
                    recommendation_id=_to_int(payload.get("recommendation_id", 0)),
                    feedback_type=str(payload.get("feedback_type", "")),
                    note=str(payload.get("note", "")),
                    request_id=str(payload.get("request_id", "")),
                    batch_ref=str(payload.get("batch_ref", "")),
                )
            )

        return await _run_handler(action)

    async def get_delight_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_delight)

    async def respond_delight_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.respond_delight(
                DelightFeedbackRequest(
                    bvid=str(payload.get("bvid", "")),
                    content_id=str(payload.get("content_id", "")),
                    source_platform=str(payload.get("source_platform", "")),
                    response=str(payload.get("response", "")),
                    title=str(payload.get("title", "")),
                    message=str(payload.get("message", "")),
                    request_id=str(payload.get("request_id", "")),
                )
            )

        return await _run_handler(action)

    async def get_runtime_status_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_runtime_status)

    async def get_activity_feed_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.get_activity_feed(
                limit=_to_int(payload.get("limit", 10), default=10),
                before=str(payload.get("before", "")),
            )

        return await _run_handler(action)

    async def get_platform_availability_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_platform_availability)

    async def chat_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.chat(
                ChatRequest(
                    message=str(payload.get("message", "")),
                    session=str(payload.get("session", "openclaw")),
                    scope=str(payload.get("scope", "chat")),
                    turn_id=str(payload.get("turn_id", "")),
                    subject_id=str(payload.get("subject_id", "")),
                    subject_title=str(payload.get("subject_title", "")),
                    reply_to_turn_id=str(payload.get("reply_to_turn_id", "")),
                )
            )

        return await _run_handler(action)

    async def get_chat_history_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.get_chat_history(
                session=str(payload.get("session", "openclaw")),
                scope=str(payload.get("scope", "")),
                limit=_to_int(payload.get("limit", 50), default=50),
            )

        return await _run_handler(action)

    async def get_next_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_next_probe)

    async def respond_interest_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.respond_interest_probe(
                InterestProbeFeedbackRequest(
                    domain=str(payload.get("domain", "")),
                    response=str(payload.get("response", "")),
                    message=str(payload.get("message", "")),
                    confirmation_source=str(payload.get("confirmation_source", "")),
                    surface=str(payload.get("surface", "agent")),
                )
            )

        return await _run_handler(action)

    async def get_next_avoidance_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_next_avoidance_probe)

    async def respond_avoidance_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.respond_avoidance_probe(
                AvoidanceProbeFeedbackRequest(
                    domain=str(payload.get("domain", "")),
                    response=str(payload.get("response", "")),
                    message=str(payload.get("message", "")),
                )
            )

        return await _run_handler(action)

    async def get_profile_edit_state_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_profile_edit_state)

    async def edit_profile_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            value = payload.get("value")
            weight = payload.get("weight")
            return await adapter.edit_profile(
                ProfileEditRequest(
                    target=str(payload.get("target", "")),
                    op=str(payload.get("op", "")),
                    value=value if isinstance(value, (str, float, int)) else None,
                    parent=str(payload.get("parent", "")),
                    weight=float(weight) if isinstance(weight, (int, float)) else None,
                )
            )

        return await _run_handler(action)

    async def save_local_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.save_local(
                SavedItemRequest(
                    list_kind=str(payload.get("list_kind", "")),
                    source_platform=str(payload.get("source_platform", "")),
                    content_id=str(payload.get("content_id", "")),
                    content_url=str(payload.get("content_url", "")),
                    content_type=str(payload.get("content_type", "video")),
                    title=str(payload.get("title", "")),
                    author_name=str(payload.get("author_name", "")),
                    cover_url=str(payload.get("cover_url", "")),
                    note=str(payload.get("note", "")),
                )
            )

        return await _run_handler(action)

    async def remove_saved_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.remove_saved(
                SavedRemoveRequest(
                    list_kind=str(payload.get("list_kind", "")),
                    item_key=str(payload.get("item_key", "")),
                )
            )

        return await _run_handler(action)

    async def list_saved_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.list_saved(
                list_kind=str(payload.get("list_kind", "")),
                limit=_to_int(payload.get("limit", 50), default=50),
            )

        return await _run_handler(action)

    async def sync_saved_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.sync_saved(
                SavedSyncRequest(
                    list_kind=str(payload.get("list_kind", "")),
                    item_keys=_to_strings(payload.get("item_keys")),
                    allow_state_changing=_to_bool(payload.get("allow_state_changing")),
                )
            )

        return await _run_handler(action)

    return [
        OpenClawSkillDescriptor(
            name="openbiliclaw_sync_account",
            description="Run one account-side signal sync across the configured sources.",
            input_schema={"type": "object", "properties": {}},
            handler=sync_account_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_profile",
            description="Read the current OpenBiliClaw user profile summary.",
            input_schema={"type": "object", "properties": {}},
            handler=get_profile_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_capabilities",
            description="Negotiate the current protocol version and complete skill manifest.",
            input_schema={"type": "object", "properties": {}},
            handler=get_capabilities_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_recommend",
            description=(
                "Generate a multi-source batch. Director mode requires stable request_id; "
                "tool delivery is not user exposure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "refresh_if_needed": {"type": "boolean"},
                    "source_platform": {"type": "string"},
                    "excluded_item_ids": {"type": "array", "items": {"type": "string"}},
                    "request_id": {
                        "type": "string",
                        "maxLength": 128,
                        "description": "Required in Director mode; reuse for retries.",
                    },
                    "stream_ref": {"type": "string", "maxLength": 128},
                    "explicit_intent": {"type": "string", "maxLength": 500},
                },
            },
            handler=recommend_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_reshuffle",
            description="Replace the current recommendation page from the precomputed pool.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "source_platform": {"type": "string"},
                    "excluded_item_ids": {"type": "array", "items": {"type": "string"}},
                    "request_id": {"type": "string", "maxLength": 128},
                    "stream_ref": {"type": "string", "maxLength": 128},
                    "explicit_intent": {"type": "string", "maxLength": 500},
                },
            },
            handler=reshuffle_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_append_recommendations",
            description="Append another recommendation page from the precomputed pool.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "source_platform": {"type": "string"},
                    "excluded_item_ids": {"type": "array", "items": {"type": "string"}},
                    "request_id": {"type": "string", "maxLength": 128},
                    "stream_ref": {"type": "string", "maxLength": 128},
                    "explicit_intent": {"type": "string", "maxLength": 500},
                },
            },
            handler=append_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_restore_recommendations",
            description=(
                "Restore an immutable Director screen without generating cards "
                "or recording exposure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "stream_ref": {"type": "string"},
                    "batch_ref": {"type": "string"},
                    "source_platform": {"type": "string"},
                },
            },
            handler=restore_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_present_recommendations",
            description=(
                "Acknowledge only cards actually visible to the user. "
                "Never invoke merely because a tool returned cards."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "batch_ref": {"type": "string"},
                    "ack_id": {"type": "string"},
                    "item_keys": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                },
                "required": ["batch_ref", "ack_id", "item_keys"],
            },
            handler=present_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_submit_feedback",
            description="Submit explicit recommendation feedback with an idempotency key.",
            input_schema={
                "type": "object",
                "properties": {
                    "recommendation_id": {"type": "integer", "minimum": 1},
                    "feedback_type": {
                        "type": "string",
                        "enum": ["like", "dislike", "comment", "dismiss"],
                    },
                    "note": {"type": "string"},
                    "batch_ref": {"type": "string", "maxLength": 256},
                    "request_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 400,
                        "description": "Stable idempotency ID; reuse it for retries.",
                    },
                },
                "required": ["recommendation_id", "feedback_type", "request_id"],
            },
            handler=submit_feedback_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_delight",
            description="Get a proactive multi-source surprise recommendation.",
            input_schema={"type": "object", "properties": {}},
            handler=get_delight_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_respond_delight",
            description="View, react to, dismiss or chat about a surprise recommendation.",
            input_schema={
                "type": "object",
                "properties": {
                    "bvid": {"type": "string"},
                    "content_id": {"type": "string"},
                    "source_platform": {"type": "string"},
                    "response": {
                        "type": "string",
                        "enum": ["view", "like", "dislike", "chat", "dismiss"],
                    },
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "request_id": {"type": "string", "maxLength": 400},
                },
                "required": ["response"],
            },
            handler=respond_delight_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_runtime_status",
            description="Read the current runtime and account-sync status summary.",
            input_schema={"type": "object", "properties": {}},
            handler=get_runtime_status_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_activity_feed",
            description="Read recent recommendations, feedback and cognition activity.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "before": {"type": "string"},
                },
            },
            handler=get_activity_feed_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_platform_availability",
            description="Read servable recommendation inventory by source platform.",
            input_schema={"type": "object", "properties": {}},
            handler=get_platform_availability_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_chat",
            description="Send one durable Socratic dialogue turn and receive the reply.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1},
                    "session": {"type": "string"},
                    "scope": {"type": "string"},
                    "turn_id": {"type": "string"},
                    "subject_id": {"type": "string"},
                    "subject_title": {"type": "string"},
                    "reply_to_turn_id": {"type": "string"},
                },
                "required": ["message"],
            },
            handler=chat_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_chat_history",
            description="Read durable dialogue history for one host session.",
            input_schema={
                "type": "object",
                "properties": {
                    "session": {"type": "string"},
                    "scope": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            handler=get_chat_history_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_next_probe",
            description="Get the next speculative-interest hypothesis.",
            input_schema={"type": "object", "properties": {}},
            handler=get_next_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_respond_interest_probe",
            description="Confirm, reject, defer or discuss an interest hypothesis.",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "minLength": 1},
                    "response": {"type": "string", "enum": ["confirm", "reject", "defer", "chat"]},
                    "message": {"type": "string"},
                    "confirmation_source": {"type": "string"},
                    "surface": {"type": "string"},
                },
                "required": ["domain", "response"],
            },
            handler=respond_interest_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_next_avoidance_probe",
            description="Get the next speculative-avoidance hypothesis.",
            input_schema={"type": "object", "properties": {}},
            handler=get_next_avoidance_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_respond_avoidance_probe",
            description="Confirm, reject, defer or discuss an avoidance hypothesis.",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "minLength": 1},
                    "response": {"type": "string", "enum": ["confirm", "reject", "defer", "chat"]},
                    "message": {"type": "string"},
                },
                "required": ["domain", "response"],
            },
            handler=respond_avoidance_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_profile_edit_state",
            description="Read the full editable profile overlay state.",
            input_schema={"type": "object", "properties": {}},
            handler=get_profile_edit_state_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_edit_profile",
            description="Apply one deterministic profile edit and return fresh state.",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "minLength": 1},
                    "op": {"type": "string"},
                    "value": {},
                    "parent": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["target", "op"],
            },
            handler=edit_profile_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_save_local",
            description="Save a multi-source item locally without an external account write.",
            input_schema={
                "type": "object",
                "properties": {
                    "list_kind": {"type": "string", "enum": ["favorite", "watch_later"]},
                    "source_platform": {"type": "string"},
                    "content_id": {"type": "string"},
                    "content_url": {"type": "string"},
                    "content_type": {"type": "string"},
                    "title": {"type": "string"},
                    "author_name": {"type": "string"},
                    "cover_url": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["list_kind", "source_platform"],
            },
            handler=save_local_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_remove_saved",
            description="Remove one item from a local saved list.",
            input_schema={
                "type": "object",
                "properties": {
                    "list_kind": {"type": "string", "enum": ["favorite", "watch_later"]},
                    "item_key": {"type": "string", "minLength": 1},
                },
                "required": ["list_kind", "item_key"],
            },
            handler=remove_saved_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_list_saved",
            description="List local saved items and native-sync status.",
            input_schema={
                "type": "object",
                "properties": {
                    "list_kind": {"type": "string", "enum": ["favorite", "watch_later"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["list_kind"],
            },
            handler=list_saved_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_sync_saved",
            description="Run an explicitly authorized native-save synchronization task.",
            input_schema={
                "type": "object",
                "properties": {
                    "list_kind": {"type": "string", "enum": ["favorite", "watch_later"]},
                    "item_keys": {"type": "array", "items": {"type": "string"}},
                    "allow_state_changing": {"type": "boolean"},
                },
                "required": ["list_kind", "allow_state_changing"],
            },
            handler=sync_saved_handler,
        ),
    ]
