"""Protocol-neutral skill descriptors for OpenClaw integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from .errors import AdapterOperationError, AdapterValidationError
from .schemas import AvoidanceProbeFeedbackRequest, ChatRequest, FeedbackRequest


@dataclass(slots=True)
class OpenClawSkillDescriptor:
    """One skill descriptor that can be registered with OpenClaw."""

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


def build_openclaw_skills(adapter: Any) -> list[OpenClawSkillDescriptor]:
    """Build protocol-neutral skill descriptors backed by the adapter."""

    async def sync_account_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.sync_account)

    async def get_profile_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_profile)

    async def recommend_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            return await adapter.recommend(
                limit=_to_int(payload.get("limit", 5), default=5),
                refresh_if_needed=bool(payload.get("refresh_if_needed", False)),
            )

        return await _run_handler(action)

    async def submit_feedback_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            request = FeedbackRequest(
                recommendation_id=_to_int(payload.get("recommendation_id", 0)),
                feedback_type=str(payload.get("feedback_type", "")),
                note=str(payload.get("note", "")),
            )
            return await adapter.submit_feedback(request)

        return await _run_handler(action)

    async def get_delight_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_delight)

    async def get_runtime_status_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_runtime_status)

    async def chat_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            request = ChatRequest(
                message=str(payload.get("message", "")),
                session=str(payload.get("session", "openclaw")),
            )
            return await adapter.chat(request)

        return await _run_handler(action)

    async def get_next_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_next_probe)

    async def get_next_avoidance_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return await _run_handler(adapter.get_next_avoidance_probe)

    async def respond_avoidance_probe_handler(payload: dict[str, object]) -> dict[str, object]:
        async def action() -> Any:
            request = AvoidanceProbeFeedbackRequest(
                domain=str(payload.get("domain", "")),
                response=str(payload.get("response", "")),
                message=str(payload.get("message", "")),
            )
            return await adapter.respond_avoidance_probe(request)

        return await _run_handler(action)

    return [
        OpenClawSkillDescriptor(
            name="openbiliclaw_sync_account",
            description="Run one account-side signal sync from Bilibili into OpenBiliClaw.",
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
            name="openbiliclaw_recommend",
            description="Generate a batch of recommendations from OpenBiliClaw.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1},
                    "refresh_if_needed": {"type": "boolean"},
                },
            },
            handler=recommend_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_submit_feedback",
            description="Submit explicit recommendation feedback back into OpenBiliClaw.",
            input_schema={
                "type": "object",
                "properties": {
                    "recommendation_id": {"type": "integer", "minimum": 1},
                    "feedback_type": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["recommendation_id", "feedback_type"],
            },
            handler=submit_feedback_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_delight",
            description="Get a proactive surprise recommendation that might delight the user.",
            input_schema={"type": "object", "properties": {}},
            handler=get_delight_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_get_runtime_status",
            description="Read the current OpenBiliClaw runtime status summary.",
            input_schema={"type": "object", "properties": {}},
            handler=get_runtime_status_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_chat",
            description=(
                "Send one Socratic dialogue turn to OpenBiliClaw and receive "
                "the agent's reply. The dialogue probes deeper into the user's "
                "motivations and refines the soul profile automatically."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1},
                    "session": {"type": "string"},
                },
                "required": ["message"],
            },
            handler=chat_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_next_probe",
            description=(
                "Get the next speculative-interest hypothesis that the agent "
                "wants the user to confirm or reject. Returns a ready-to-ask "
                "question plus raw hypothesis data. Use chat to relay the "
                "user's answer back into the learning loop."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=get_next_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_next_avoidance_probe",
            description=(
                "Get the next speculative avoidance hypothesis that the agent "
                "wants the user to confirm or reject. Returns a ready-to-ask "
                "question plus raw avoidance data."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=get_next_avoidance_probe_handler,
        ),
        OpenClawSkillDescriptor(
            name="openbiliclaw_respond_avoidance_probe",
            description="Submit a user response to a speculative avoidance probe.",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "minLength": 1},
                    "response": {
                        "type": "string",
                        "enum": ["confirm", "reject", "chat"],
                    },
                    "message": {"type": "string"},
                },
                "required": ["domain", "response"],
            },
            handler=respond_avoidance_probe_handler,
        ),
    ]
