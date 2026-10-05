"""JSON CLI bridge for the OpenClaw adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bootstrap import build_openclaw_adapter
from .capabilities import build_capabilities
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
from .skill import build_openclaw_skills

if TYPE_CHECKING:
    from collections.abc import Sequence

_SKILL_PACK_PATH = (
    Path(__file__).resolve().parents[4] / "skills" / "openbiliclaw-adapter" / "SKILL.md"
)

_RUNTIME_STREAM_URL = "ws://127.0.0.1:8420/api/runtime-stream"

# Event types that the ``listen`` command forwards to stdout.
#
# ``delight.candidate`` — proactive surprise recommendation push.
# ``interest.probe``    — the agent has a new speculative interest hypothesis
#                         it wants the user to confirm; payload mirrors the
#                         response of ``next-probe``.
_LISTEN_EVENT_TYPES = frozenset(
    {
        "delight.candidate",
        "delight.liked",
        "delight.disliked",
        "delight.chat",
        "delight.refreshed",
        "interest.probe",
        "interest.confirmed",
        "interest.rejected",
        "interest.deferred",
        "interest.chat",
        "avoidance.probe",
        "avoidance.confirmed",
        "avoidance.rejected",
        "avoidance.deferred",
        "avoidance.chat",
    }
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openbiliclaw-openclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync-account")
    subparsers.add_parser("get-profile")
    subparsers.add_parser("capabilities")
    subparsers.add_parser("get-delight")
    subparsers.add_parser("platform-availability")
    subparsers.add_parser("profile-edit-state")
    subparsers.add_parser("next-probe")
    subparsers.add_parser("next-avoidance-probe")
    subparsers.add_parser("runtime-status")
    subparsers.add_parser("doctor")
    subparsers.add_parser("emit-skill-descriptors")

    chat_parser = subparsers.add_parser(
        "chat",
        help="Send one Socratic dialogue turn and print the agent's reply as JSON.",
    )
    chat_parser.add_argument(
        "--message",
        required=True,
        help="User message to send to the Socratic dialogue.",
    )
    chat_parser.add_argument(
        "--session",
        default="openclaw",
        help="Dialogue session label (default: 'openclaw').",
    )
    chat_parser.add_argument("--scope", default="chat")
    chat_parser.add_argument("--turn-id", default="")
    chat_parser.add_argument("--subject-id", default="")
    chat_parser.add_argument("--subject-title", default="")
    chat_parser.add_argument("--reply-to-turn-id", default="")

    chat_history_parser = subparsers.add_parser("chat-history")
    chat_history_parser.add_argument("--session", default="openclaw")
    chat_history_parser.add_argument("--scope", default="")
    chat_history_parser.add_argument("--limit", type=int, default=50)

    listen_parser = subparsers.add_parser(
        "listen",
        help="Stream proactive events (delight.candidate) via WebSocket as JSON lines.",
    )
    listen_parser.add_argument(
        "--ws-url",
        default=_RUNTIME_STREAM_URL,
        help="WebSocket URL for the runtime stream.",
    )
    listen_parser.add_argument(
        "--events",
        default=",".join(sorted(_LISTEN_EVENT_TYPES)),
        help="Comma-separated event types to forward (default: delight.candidate).",
    )

    recommend_parser = subparsers.add_parser("recommend")
    recommend_parser.add_argument("--limit", type=int, default=5)
    recommend_parser.add_argument("--source-platform", default="")
    recommend_parser.add_argument("--exclude-item-id", action="append", default=[])
    recommend_parser.add_argument(
        "--realtime",
        action="store_true",
        help=(
            "Generate fresh per-item LLM expressions at request time (slow). "
            "Default serves precomputed pool copy (fast)."
        ),
    )
    refresh_group = recommend_parser.add_mutually_exclusive_group()
    refresh_group.add_argument(
        "--refresh-if-needed",
        action="store_true",
        help="Trigger runtime refresh before returning recommendations.",
    )
    refresh_group.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Skip runtime refresh and only read/generate recommendations.",
    )

    feedback_parser = subparsers.add_parser("submit-feedback")
    feedback_parser.add_argument("--recommendation-id", type=int, required=True)
    feedback_parser.add_argument("--feedback-type", required=True)
    feedback_parser.add_argument("--note", default="")
    feedback_parser.add_argument(
        "--request-id",
        required=True,
        help="Stable idempotency ID; reuse it for retries of the same action.",
    )

    for command, default_limit in (("reshuffle", 5), ("append", 10)):
        page_parser = subparsers.add_parser(command)
        page_parser.add_argument("--limit", type=int, default=default_limit)
        page_parser.add_argument("--source-platform", default="")
        page_parser.add_argument("--exclude-item-id", action="append", default=[])

    activity_parser = subparsers.add_parser("activity-feed")
    activity_parser.add_argument("--limit", type=int, default=10)
    activity_parser.add_argument("--before", default="")

    avoidance_feedback_parser = subparsers.add_parser("respond-avoidance-probe")
    avoidance_feedback_parser.add_argument("--domain", required=True)
    avoidance_feedback_parser.add_argument(
        "--response",
        choices=["confirm", "reject", "defer", "chat"],
        required=True,
    )
    avoidance_feedback_parser.add_argument("--message", default="")

    interest_feedback_parser = subparsers.add_parser("respond-interest-probe")
    interest_feedback_parser.add_argument("--domain", required=True)
    interest_feedback_parser.add_argument(
        "--response",
        choices=["confirm", "reject", "defer", "chat"],
        required=True,
    )
    interest_feedback_parser.add_argument("--message", default="")
    interest_feedback_parser.add_argument("--confirmation-source", default="")
    interest_feedback_parser.add_argument("--surface", default="agent")

    delight_feedback_parser = subparsers.add_parser("respond-delight")
    delight_feedback_parser.add_argument("--bvid", default="")
    delight_feedback_parser.add_argument("--content-id", default="")
    delight_feedback_parser.add_argument("--source-platform", default="")
    delight_feedback_parser.add_argument(
        "--response",
        choices=["view", "like", "dislike", "chat", "dismiss"],
        required=True,
    )
    delight_feedback_parser.add_argument("--title", default="")
    delight_feedback_parser.add_argument("--message", default="")
    delight_feedback_parser.add_argument("--request-id", default="")

    edit_parser = subparsers.add_parser("edit-profile")
    edit_parser.add_argument("--target", required=True)
    edit_parser.add_argument("--op", required=True)
    edit_parser.add_argument("--value", default=None)
    edit_parser.add_argument("--parent", default="")
    edit_parser.add_argument("--weight", type=float, default=None)

    save_parser = subparsers.add_parser("save-local")
    save_parser.add_argument("--list-kind", choices=["favorite", "watch_later"], required=True)
    save_parser.add_argument("--source-platform", required=True)
    save_parser.add_argument("--content-id", default="")
    save_parser.add_argument("--content-url", default="")
    save_parser.add_argument("--content-type", default="video")
    save_parser.add_argument("--title", default="")
    save_parser.add_argument("--author-name", default="")
    save_parser.add_argument("--cover-url", default="")
    save_parser.add_argument("--note", default="")

    remove_saved_parser = subparsers.add_parser("remove-saved")
    remove_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    remove_saved_parser.add_argument("--item-key", required=True)

    list_saved_parser = subparsers.add_parser("list-saved")
    list_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    list_saved_parser.add_argument("--limit", type=int, default=50)

    sync_saved_parser = subparsers.add_parser("sync-saved")
    sync_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    sync_saved_parser.add_argument("--item-key", action="append", default=[])
    sync_saved_parser.add_argument(
        "--allow-state-changing",
        action="store_true",
        help="Explicitly authorize native account writes for this command.",
    )
    return parser


def _print_payload(payload: dict[str, object]) -> None:
    sys.stdout.write(f"{json.dumps(payload, ensure_ascii=False)}\n")


async def _run_command(args: argparse.Namespace, adapter: Any) -> dict[str, object]:
    if args.command == "doctor":
        skills = build_openclaw_skills(adapter)
        return {
            "ok": True,
            "data": {
                "protocol_version": "agent-bridge/v2",
                "host_names": ["openclaw", "hermes", "workbuddy"],
                "skill_pack_path": str(_SKILL_PACK_PATH),
                "skill_pack_exists": _SKILL_PACK_PATH.exists(),
                "skill_count": len(skills),
                "skill_names": [item.name for item in skills],
                "cli_module": "openbiliclaw.integrations.openclaw.cli",
            },
        }
    if args.command == "capabilities":
        return {
            "ok": True,
            "data": asdict(build_capabilities(adapter)),
        }
    if args.command == "emit-skill-descriptors":
        skills = build_openclaw_skills(adapter)
        return {
            "ok": True,
            "data": {
                "skills": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "input_schema": item.input_schema,
                    }
                    for item in skills
                ]
            },
        }
    try:
        if args.command == "sync-account":
            result = await adapter.sync_account()
        elif args.command == "get-profile":
            result = await adapter.get_profile()
        elif args.command == "get-delight":
            result = await adapter.get_delight()
        elif args.command == "runtime-status":
            result = await adapter.get_runtime_status()
        elif args.command == "recommend":
            recommend_kwargs: dict[str, object] = {
                "limit": args.limit,
                "refresh_if_needed": bool(args.refresh_if_needed),
            }
            if args.source_platform:
                recommend_kwargs["source_platform"] = args.source_platform
            if args.exclude_item_id:
                recommend_kwargs["excluded_item_ids"] = args.exclude_item_id
            if getattr(args, "realtime", False):
                recommend_kwargs["realtime"] = True
            result = await adapter.recommend(**recommend_kwargs)
        elif args.command in {"reshuffle", "append"}:
            method_name = "reshuffle" if args.command == "reshuffle" else "append_recommendations"
            method = getattr(adapter, method_name)
            result = await method(
                limit=args.limit,
                source_platform=args.source_platform,
                excluded_item_ids=args.exclude_item_id,
            )
        elif args.command == "submit-feedback":
            request = FeedbackRequest(
                recommendation_id=args.recommendation_id,
                feedback_type=args.feedback_type,
                note=args.note,
                request_id=args.request_id,
            )
            result = await adapter.submit_feedback(request)
        elif args.command == "chat":
            chat_request = ChatRequest(
                message=args.message,
                session=getattr(args, "session", "openclaw"),
                scope=getattr(args, "scope", "chat"),
                turn_id=getattr(args, "turn_id", ""),
                subject_id=getattr(args, "subject_id", ""),
                subject_title=getattr(args, "subject_title", ""),
                reply_to_turn_id=getattr(args, "reply_to_turn_id", ""),
            )
            result = await adapter.chat(chat_request)
        elif args.command == "chat-history":
            result = await adapter.get_chat_history(
                session=args.session,
                scope=args.scope,
                limit=args.limit,
            )
        elif args.command == "activity-feed":
            result = await adapter.get_activity_feed(limit=args.limit, before=args.before)
        elif args.command == "platform-availability":
            result = await adapter.get_platform_availability()
        elif args.command == "next-probe":
            result = await adapter.get_next_probe()
        elif args.command == "next-avoidance-probe":
            result = await adapter.get_next_avoidance_probe()
        elif args.command == "respond-avoidance-probe":
            avoidance_request = AvoidanceProbeFeedbackRequest(
                domain=args.domain,
                response=args.response,
                message=args.message,
            )
            result = await adapter.respond_avoidance_probe(avoidance_request)
        elif args.command == "respond-interest-probe":
            result = await adapter.respond_interest_probe(
                InterestProbeFeedbackRequest(
                    domain=args.domain,
                    response=args.response,
                    message=args.message,
                    confirmation_source=args.confirmation_source,
                    surface=args.surface,
                )
            )
        elif args.command == "respond-delight":
            result = await adapter.respond_delight(
                DelightFeedbackRequest(
                    bvid=args.bvid,
                    content_id=args.content_id,
                    source_platform=args.source_platform,
                    response=args.response,
                    title=args.title,
                    message=args.message,
                    request_id=args.request_id,
                )
            )
        elif args.command == "profile-edit-state":
            result = await adapter.get_profile_edit_state()
        elif args.command == "edit-profile":
            result = await adapter.edit_profile(
                ProfileEditRequest(
                    target=args.target,
                    op=args.op,
                    value=args.value,
                    parent=args.parent,
                    weight=args.weight,
                )
            )
        elif args.command == "save-local":
            result = await adapter.save_local(
                SavedItemRequest(
                    list_kind=args.list_kind,
                    source_platform=args.source_platform,
                    content_id=args.content_id,
                    content_url=args.content_url,
                    content_type=args.content_type,
                    title=args.title,
                    author_name=args.author_name,
                    cover_url=args.cover_url,
                    note=args.note,
                )
            )
        elif args.command == "remove-saved":
            result = await adapter.remove_saved(
                SavedRemoveRequest(list_kind=args.list_kind, item_key=args.item_key)
            )
        elif args.command == "list-saved":
            result = await adapter.list_saved(list_kind=args.list_kind, limit=args.limit)
        elif args.command == "sync-saved":
            result = await adapter.sync_saved(
                SavedSyncRequest(
                    list_kind=args.list_kind,
                    item_keys=args.item_key,
                    allow_state_changing=bool(args.allow_state_changing),
                )
            )
        else:  # pragma: no cover - argparse guarantees command validity
            raise AdapterValidationError(f"Unsupported command: {args.command}")
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
    return {
        "ok": True,
        "data": asdict(result),
    }


# ---------------------------------------------------------------------------
# ``listen`` — long-running WebSocket event stream
# ---------------------------------------------------------------------------

_WS_RECONNECT_DELAY = 3.0
_DELIGHT_ACK_URL = "http://127.0.0.1:8420/api/delight/sent"


async def _acknowledge_delight(bvid: str) -> None:
    """POST acknowledgment so the backend marks the item as notified."""
    try:
        import aiohttp

        async with (
            aiohttp.ClientSession(trust_env=False) as session,
            session.post(
                _DELIGHT_ACK_URL,
                json={"bvid": bvid},
            ) as resp,
        ):
            resp.raise_for_status()
    except Exception:
        # Fallback to synchronous urllib when aiohttp is unavailable
        try:
            import urllib.request

            req = urllib.request.Request(
                _DELIGHT_ACK_URL,
                data=json.dumps({"bvid": bvid}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        except Exception:
            pass


async def _listen_ws(ws_url: str, event_types: frozenset[str]) -> None:
    """Connect to the runtime WebSocket stream and forward matching events.

    Each matching event is written to stdout as a single JSON line:

        {"type": "delight.candidate", "bvid": "BV1xxx", ...}

    The connection auto-reconnects on failure. Press Ctrl-C to stop.
    """
    try:
        import websockets
    except ModuleNotFoundError:
        _print_payload(
            {
                "ok": False,
                "error": (
                    "The 'listen' command requires the 'websockets' package. "
                    "Install it with:  pip install websockets"
                ),
                "error_type": "dependency_error",
            }
        )
        return

    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                _print_payload(
                    {
                        "ok": True,
                        "data": {
                            "status": "connected",
                            "ws_url": ws_url,
                            "event_types": sorted(event_types),
                        },
                    }
                )
                sys.stdout.flush()
                async for raw_message in ws:
                    try:
                        event = json.loads(raw_message)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get("type", ""))
                    if event_type not in event_types:
                        continue
                    _print_payload({"ok": True, "data": event})
                    sys.stdout.flush()
                    # Auto-ACK delight candidates so cooldown starts immediately
                    if event_type == "delight.candidate":
                        bvid = str(event.get("bvid", ""))
                        if bvid:
                            await _acknowledge_delight(bvid)
        except Exception:
            _print_payload(
                {
                    "ok": False,
                    "error": "WebSocket disconnected, reconnecting...",
                    "error_type": "connection_error",
                }
            )
            sys.stdout.flush()
            await asyncio.sleep(_WS_RECONNECT_DELAY)


def main(argv: Sequence[str] | None = None, *, adapter: Any | None = None) -> int:
    """Run the OpenClaw adapter CLI and print JSON output."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    # ``listen`` is a long-running stream — handle separately
    if args.command == "listen":
        event_types = (
            frozenset(t.strip() for t in args.events.split(",") if t.strip()) or _LISTEN_EVENT_TYPES
        )
        with suppress(KeyboardInterrupt):
            asyncio.run(_listen_ws(args.ws_url, event_types))
        return 0

    if adapter is not None:
        resolved_adapter = adapter
    elif args.command in {"doctor", "capabilities", "emit-skill-descriptors"}:
        resolved_adapter = object()
    else:
        resolved_adapter = build_openclaw_adapter()
    payload = asyncio.run(_run_command(args, resolved_adapter))
    _print_payload(payload)
    return 0 if bool(payload.get("ok", False)) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
