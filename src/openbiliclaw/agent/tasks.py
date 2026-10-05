"""Durable background task center for the 「聊一聊」 chat (M6).

Tasks are first-class persistent objects (``agent_tasks`` table): they exist
independently of their originating session, carry their own execution log
(``steps``) and are auditable/resumable-by-reissue. A task runs an
``AgentLoop`` with a **read-only** tool subset (``filter_by_permission("read")``)
inside a ``BackgroundTaskRegistry``-tracked asyncio task; every loop event is
appended to the durable step log as it happens.

Write actions are never executed in the background. Instead the loop may call
the ``propose_suggestion`` meta tool to emit structured suggestions
(``{action, summary, payload}``); when the run finishes, the report plus the
suggestion list are persisted and a summary message is written back into the
originating chat session for the user to confirm item by item (the confirmed
write path is the M7 approval gate's job).

Restart semantics: in-flight asyncio tasks never survive a process restart,
so startup marks rows still in pending/running as ``interrupted`` (terminal);
tasks are deliberately never auto-resumed. Config hot reload cancels tracked
tasks via the registry, which lands them in ``interrupted`` as well, while an
explicit user cancel lands them in ``cancelled``.

Two meta tools live here:

- ``propose_suggestion`` — registered into the background task's read-only
  registry by the runner; its handler appends validated suggestions to a
  per-run collector.
- ``start_background_task`` — registered into interactive chat skill subsets
  by the API layer (same pattern as ``suggest_skill``): the agent proposes a
  background task, the SSE ``tool_call`` event renders as a confirmation
  card, and the frontend starts the task via ``POST /api/chat/tasks`` once
  the user confirms. The handler itself has no side effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.storage.database import (
    AGENT_TASK_TERMINAL_STATUSES,
    DEFAULT_CHAT_SESSION_ID,
    MAX_AGENT_TASK_SUGGESTIONS,
)

from .loop import AgentLoop
from .tools.registry import Tool, ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

logger = logging.getLogger(__name__)

PROPOSE_SUGGESTION_TOOL_NAME = "propose_suggestion"
START_BACKGROUND_TASK_TOOL_NAME = "start_background_task"

DEFAULT_TASK_MAX_STEPS = 32

# Write-capable actions a background task may propose. The background loop
# itself never executes them; the user confirms each suggestion back in the
# conversation.
SUGGESTION_ACTIONS: tuple[str, ...] = (
    "write_memory",
    "submit_feedback",
    "save_item",
    "create_source",
    "toggle_source",
    "update_config",
)
MAX_SUGGESTION_SUMMARY_CHARS = 500
MAX_SUGGESTION_PAYLOAD_CHARS = 4000

_TASK_SYSTEM_PROMPT = (
    "你是一个后台任务执行 agent。任务由用户在对话中发起，你在无人值守模式下运行，规则如下：\n"
    "- 你只能使用只读工具查询信息，不能修改任何数据；\n"
    "- 所有需要写入的动作（记记忆、提交反馈、保存条目、管理订阅源、改配置等）"
    "都必须通过 propose_suggestion 工具产出结构化建议（action / summary / payload），"
    "由用户回到对话后逐项确认执行；\n"
    "- 逐步推进，每一步工具调用都会记入任务的执行日志，用户可在任务中心查看；\n"
    "- 完成后在最终答复里给出结果报告：做了什么、发现了什么、建议清单导读（如有）。"
)


def build_propose_suggestion_tool(collector: list[dict[str, Any]]) -> Tool:
    """Build the ``propose_suggestion`` meta tool bound to one run's collector.

    The handler validates and records one structured suggestion; execution is
    always user-confirmed back in the conversation (M7 approval gate).
    """

    def _handler(arguments: dict[str, Any]) -> str:
        action = str(arguments.get("action") or "").strip()
        summary = str(arguments.get("summary") or "").strip()
        payload = arguments.get("payload")
        if action not in SUGGESTION_ACTIONS:
            return f"建议无效：未知 action {action!r}。可选：{', '.join(SUGGESTION_ACTIONS)}"
        if not summary:
            return "建议无效：缺少 summary（一句话说明要做什么、为什么）。"
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return "建议无效：payload 必须是对象。"
        if len(collector) >= MAX_AGENT_TASK_SUGGESTIONS:
            return f"建议清单已满（{MAX_AGENT_TASK_SUGGESTIONS} 条），请挑选最重要的建议记录。"
        if len(summary) > MAX_SUGGESTION_SUMMARY_CHARS:
            summary = summary[:MAX_SUGGESTION_SUMMARY_CHARS] + "…"
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        if len(serialized_payload) > MAX_SUGGESTION_PAYLOAD_CHARS:
            return f"建议无效：payload 超过 {MAX_SUGGESTION_PAYLOAD_CHARS} 字符，请精简。"
        collector.append({"action": action, "summary": summary, "payload": payload})
        return (
            f"已记录建议 #{len(collector)}（{action}）。"
            "任务完成后建议会随报告一起交给用户逐项确认；请继续任务或给出最终报告。"
        )

    return Tool(
        name=PROPOSE_SUGGESTION_TOOL_NAME,
        description=(
            "记录一条需要用户确认的写操作建议。后台任务不能执行任何写操作；"
            "当你认为应该记记忆、提交反馈、保存条目、管理订阅源或修改配置时，"
            "用这个工具把动作、一句话说明和结构化参数交给用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(SUGGESTION_ACTIONS),
                    "description": "建议的写操作类型",
                },
                "summary": {
                    "type": "string",
                    "description": "一句话说明要做什么、为什么（展示给用户）",
                },
                "payload": {
                    "type": "object",
                    "description": "执行该动作所需的结构化参数",
                },
            },
            "required": ["action", "summary"],
            "additionalProperties": False,
        },
        handler=_handler,
        permission_level="read",
    )


def build_start_background_task_tool(skill_names: Iterable[str]) -> Tool:
    """Build the ``start_background_task`` meta tool for interactive chat.

    Same pattern as ``suggest_skill``: the handler only validates and records
    the proposal — the frontend renders the tool call as a confirmation card
    and, once the user confirms, starts the task via ``POST /api/chat/tasks``.
    """
    names = [name.strip() for name in skill_names if name.strip()]

    def _handler(arguments: dict[str, Any]) -> str:
        prompt = str(arguments.get("prompt") or "").strip()
        title = str(arguments.get("title") or "").strip()
        skill = str(arguments.get("skill") or "").strip()
        if not prompt:
            return "任务建议无效：缺少 prompt（任务的完整描述）。"
        if skill and skill not in names:
            return f"任务建议无效：不存在名为 {skill!r} 的 skill。可选：{', '.join(names)}"
        suffix = f"（标题：{title}）" if title else ""
        return (
            f"已生成后台任务建议{suffix}。"
            "请用自然语言向用户说明这个后台任务要做什么、大约会查哪些数据，并等待用户确认；"
            "用户确认后，前端会调用 POST /api/chat/tasks 发起任务，你不需要也不能自行执行。"
        )

    properties: dict[str, Any] = {
        "prompt": {
            "type": "string",
            "description": "任务的完整描述（后台 agent 看到的唯一指令，要写清楚目标）",
        },
        "title": {
            "type": "string",
            "description": "任务短标题（展示在任务中心）",
        },
    }
    if names:
        properties["skill"] = {
            "type": "string",
            "enum": names,
            "description": "可选：指定执行任务的 skill（角色）；缺省使用通用后台执行人设",
        }
    return Tool(
        name=START_BACKGROUND_TASK_TOOL_NAME,
        description=(
            "建议把一个耗时较长的任务转为后台任务执行。仅在任务明显需要多轮检索/分析、"
            "不适合在当前对话内完成时使用；这只生成建议，是否发起由用户确认后前端调用 "
            "POST /api/chat/tasks 决定。"
        ),
        parameters={
            "type": "object",
            "properties": properties,
            "required": ["prompt"],
            "additionalProperties": False,
        },
        handler=_handler,
        permission_level="read",
    )


class AgentTaskRunner:
    """Executes durable agent tasks in tracked background asyncio tasks.

    ``runtime`` is the duck-typed RuntimeContext; components (``llm_service``
    / ``agent_tool_registry`` / ``skill_catalog`` / ``config``) are resolved
    lazily at run start so the runner survives the hot-reload atomic swap.
    Without a ``task_registry`` the runner falls back to bare
    ``asyncio.create_task`` (same backward-compatibility rule as the rest of
    the runtime).
    """

    def __init__(
        self,
        database: Any,
        *,
        runtime: Any = None,
        task_registry: BackgroundTaskRegistry | None = None,
    ) -> None:
        self._database = database
        self._runtime = runtime
        self._task_registry = task_registry
        self._local_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_requested: set[str] = set()

    def start(
        self,
        *,
        session_id: str = "",
        prompt: str,
        title: str = "",
        skill: str = "",
    ) -> dict[str, Any]:
        """Create the durable row and launch background execution."""
        task_id = f"agent-task-{uuid.uuid4().hex}"
        row = cast(
            "dict[str, Any]",
            self._database.create_agent_task(
                task_id=task_id,
                session_id=session_id,
                title=title,
                prompt=prompt,
                skill=skill,
            ),
        )
        self._launch(task_id)
        return row

    def recover_interrupted(self) -> int:
        """Mark tasks a previous process left active as ``interrupted``."""
        return int(self._database.interrupt_stale_agent_tasks())

    async def cancel(self, task_id: str) -> bool:
        """Cancel one active task; returns False when nothing was cancellable.

        The coroutine's ``CancelledError`` handler lands the terminal state;
        when no live asyncio task owns the row (e.g. it was never launched in
        this process) the status is written directly here.
        """
        normalized_id = task_id.strip()
        row = self._database.get_agent_task(normalized_id)
        if row is None or row["status"] in AGENT_TASK_TERMINAL_STATUSES:
            return False
        self._cancel_requested.add(normalized_id)
        cancelled = False
        if self._task_registry is not None:
            cancelled = bool(await self._task_registry.cancel(f"agent_task.{normalized_id}"))
        local = self._local_tasks.get(normalized_id)
        if local is not None and not local.done():
            local.cancel()
            with contextlib.suppress(BaseException):
                await local
            cancelled = True
        row = self._database.get_agent_task(normalized_id)
        if row is not None and row["status"] not in AGENT_TASK_TERMINAL_STATUSES:
            self._database.update_agent_task_status(
                normalized_id,
                "cancelled",
                error="用户取消了该任务。",
            )
            cancelled = True
        self._cancel_requested.discard(normalized_id)
        return cancelled

    def _launch(self, task_id: str) -> None:
        coro = self._execute(task_id)
        if self._task_registry is not None:
            self._task_registry.track(f"agent_task.{task_id}", coro)
            return
        self._local_tasks[task_id] = asyncio.create_task(coro, name=f"agent_task.{task_id}")

    async def _execute(self, task_id: str) -> None:
        database = self._database
        row = database.get_agent_task(task_id)
        if row is None:
            return
        if not database.update_agent_task_status(task_id, "running", progress="任务已启动"):
            return  # cancelled/interrupted between creation and launch
        try:
            loop, registry = self._resolve_components(row)
            suggestions: list[dict[str, Any]] = []
            registry.register(build_propose_suggestion_tool(suggestions))
            final_text = ""
            async for event in loop.run(
                system_instruction=self._build_system_prompt(row),
                user_message=str(row["prompt"]),
                tools=registry,
            ):
                # delta 片段只服务交互式 SSE 实时渲染；后台任务没有读者，
                # 步骤日志只落完整步骤，文本由 thinking / final 全文重建。
                if event.type != "delta":
                    database.append_agent_task_step(task_id, step=event.to_dict())
                if event.type == "final":
                    final_text = event.text
            report = final_text.strip() or "任务已结束，但没有产出文字报告；详见执行记录。"
            if not database.set_agent_task_report(task_id, report=report, suggestions=suggestions):
                return  # a cancel/failure landed first — do not overwrite it
            self._write_back_summary(row, report=report, suggestions=suggestions)
        except asyncio.CancelledError:
            if task_id in self._cancel_requested:
                database.update_agent_task_status(
                    task_id,
                    "cancelled",
                    error="用户取消了该任务。",
                )
            else:
                # Hot reload / shutdown cancels tracked tasks without a user
                # cancel request — record it as an interruption.
                database.update_agent_task_status(
                    task_id,
                    "interrupted",
                    error="服务重启或热重载中断了该任务，可从任务中心重新发起。",
                )
            raise
        except Exception as exc:
            logger.exception("Agent task %s failed", task_id)
            error_text = str(exc)[:500]
            if database.update_agent_task_status(task_id, "failed", error=error_text):
                self._write_back_summary(row, report="", suggestions=[], error=error_text)
        finally:
            self._cancel_requested.discard(task_id)
            self._local_tasks.pop(task_id, None)

    def _resolve_components(self, row: dict[str, Any]) -> tuple[AgentLoop, ToolRegistry]:
        """Build a read-only loop for one task run from current components."""
        runtime = self._runtime
        llm = getattr(runtime, "llm_service", None)
        base_registry = getattr(runtime, "agent_tool_registry", None)
        if llm is None or base_registry is None:
            raise RuntimeError("Agent runtime is not configured for background tasks.")
        registry = base_registry.filter_by_permission("read")
        skill_definition = None
        skill_name = str(row.get("skill") or "").strip()
        if skill_name:
            catalog = getattr(runtime, "skill_catalog", None)
            skill_definition = catalog.get(skill_name) if catalog is not None else None
            if skill_definition is not None:
                registry = registry.subset(skill_definition.tools)
        agent_config = getattr(getattr(runtime, "config", None), "agent", None)
        max_steps = int(getattr(agent_config, "task_max_steps", DEFAULT_TASK_MAX_STEPS) or 0)
        tool_result_max_chars = int(getattr(agent_config, "tool_result_max_chars", 4000) or 0)
        loop = AgentLoop(
            llm,
            registry,
            max_steps=max(1, max_steps or DEFAULT_TASK_MAX_STEPS),
            tool_result_max_chars=max(200, tool_result_max_chars or 4000),
            caller="agent.task",
        )
        return loop, registry

    def _build_system_prompt(self, row: dict[str, Any]) -> str:
        parts = [_TASK_SYSTEM_PROMPT]
        title = str(row.get("title") or "").strip()
        if title:
            parts.append(f"任务标题：{title}")
        skill_name = str(row.get("skill") or "").strip()
        if skill_name:
            catalog = getattr(self._runtime, "skill_catalog", None)
            definition = catalog.get(skill_name) if catalog is not None else None
            if definition is not None:
                parts.append(
                    f"本任务使用 skill「{definition.display_name}」的人设：\n"
                    f"{definition.system_prompt}"
                )
        return "\n\n".join(parts)

    def _write_back_summary(
        self,
        row: dict[str, Any],
        *,
        report: str,
        suggestions: list[dict[str, Any]],
        error: str = "",
    ) -> None:
        """Post the terminal summary message into the originating session.

        The write goes through the regular durable chat-turn path
        (``create_chat_turn`` + ``complete_chat_turn``) with
        ``payload.type == "agent_task_summary"`` so the frontend renders a
        summary card carrying the report and the suggestion list.
        """
        database = self._database
        create_turn = getattr(database, "create_chat_turn", None)
        complete_turn = getattr(database, "complete_chat_turn", None)
        if not callable(create_turn) or not callable(complete_turn):
            return
        task_id = str(row.get("task_id") or "")
        title = str(row.get("title") or "").strip() or str(row.get("prompt") or "")[:30]
        session_id = str(row.get("session_id") or "").strip() or DEFAULT_CHAT_SESSION_ID
        failed = bool(error)
        if failed:
            reply = (
                f"后台任务「{title}」失败了：{error}\n你可以从任务中心查看执行记录，或让我重试。"
            )
        else:
            reply = report
            if suggestions:
                lines = "\n".join(
                    f"{index}. [{item['action']}] {item['summary']}"
                    for index, item in enumerate(suggestions, start=1)
                )
                reply = (
                    f"{report}\n\n建议清单（{len(suggestions)} 项，需你逐项确认后执行）：\n{lines}"
                )
        turn_id = f"agent-task-{task_id}"
        try:
            create_turn(
                turn_id=turn_id,
                session="desktop",
                scope="chat",
                message=f"[后台任务{'失败' if failed else '完成'}] {title}",
                payload={
                    "type": "agent_task_summary",
                    "task_id": task_id,
                    "task_status": "failed" if failed else "completed",
                    "suggestions": [dict(item) for item in suggestions],
                },
                session_id=session_id,
            )
            complete_turn(turn_id, reply=reply)
        except Exception:
            logger.exception("Failed to write back agent task summary for %s", task_id)
