"""Config read/write tools (M3 read, M7 gated write).

- ``get_config`` (read): renders the ``Config`` dataclass as JSON with
  recursive redaction — any key containing api_key / cookie / token / secret /
  password / credential is masked before anything reaches the LLM.
- ``update_config`` (hard_write): the agent loop never runs this handler
  directly; the M7 approval gate parks the call and the approve endpoint
  re-dispatches it after user approval. The write is deliberately narrow
  (v1): only existing scalar (str/int/float/bool) leaf fields, and any key
  containing a secret or path/storage marker is refused outright. A
  successful production write uses ``ctx.config_update_hook`` to submit a
  scalar patch through the API settings transaction. Compatibility callers
  retain persist/reload hooks, operating on a copy rather than the live config
  and compensating both persisted and runtime config when reload fails.
"""

from __future__ import annotations

import json
import logging
import math
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.config import validate_runtime_config

from .common import ToolComponentUnavailableError, maybe_await, require_component, truncate_text
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)

_REDACTED = "***已脱敏***"
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "cookie",
    "token",
    "secret",
    "password",
    "credential",
    "sessdata",
    "access_key",
)
# Path/storage-like keys are out of scope for conversational writes: a bad
# value strands the install (data_dir) or breaks file resolution.
_PATH_MARKERS = ("dir", "path", "file", "database")
_UPDATE_CONFIG_EXPLICIT_DENY = frozenset({"data_dir"})
_MAX_OUTPUT_CHARS = 6000

_MISSING = object()


def _redact(value: Any) -> Any:
    """Recursively mask sensitive keys in a config mapping."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if item and any(marker in lowered for marker in _SENSITIVE_MARKERS):
                redacted[key_text] = _REDACTED
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def build_config_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the config-domain tools bound to ``ctx``."""
    return [
        Tool(
            name="get_config",
            description=(
                "读取当前系统配置（已脱敏：api_key / cookie / token / 密码等敏感字段"
                "一律打码，绝不返回原文）。可用 section 只看某一段，"
                "如 llm / bilibili / discovery / agent / scheduler。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "配置段名（可空，空则返回全部，超长会截断）",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda args: _get_config(ctx, args),
        ),
        Tool(
            name="update_config",
            description=(
                "修改系统配置（hard_write：调用后不会立即生效，会生成待批准动作卡片，"
                "用户在对话中批准后才真正写入 config.toml 并热重载）。"
                "仅支持白名单内的安全键：已存在的普通标量配置项（文本/数字/布尔）；"
                "密钥、Cookie、Token 类与路径/存储类配置一律拒绝。"
            ),
            permission_level="hard_write",
            impact_hint="修改系统配置并保存到 config.toml，随后触发热重载。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "点分配置路径，如 llm.default_provider",
                    },
                    "value": {
                        "type": "string",
                        "description": "目标值（字符串形式，会按当前值类型转换）",
                    },
                    "reason": {"type": "string", "description": "修改原因，可空"},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            handler=lambda args: _update_config(ctx, args),
        ),
    ]


def _get_config(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    config = require_component(ctx.config, "config")
    if is_dataclass(config) and not isinstance(config, type):
        data: dict[str, Any] = asdict(config)
    elif isinstance(config, dict):
        data = dict(config)
    else:
        return "组件不可用: config 不是可序列化的配置对象"
    redacted = _redact(data)

    section = str(args.get("section") or "").strip()
    if section:
        if section not in redacted:
            available = ", ".join(sorted(redacted))
            return f"配置中没有「{section}」段。可用段: {available}"
        redacted = {section: redacted[section]}

    payload = json.dumps(redacted, ensure_ascii=False, indent=1, default=str)
    return truncate_text(payload, _MAX_OUTPUT_CHARS)


def _update_config_denial(key: str) -> str | None:
    """Return a refusal message when ``key`` is outside the writable whitelist."""
    segments = [segment.strip() for segment in key.split(".")]
    if any(not segment for segment in segments):
        return f"配置键格式应为「段.字段」（如 llm.default_provider），收到: {key or '（空）'}"
    if key in _UPDATE_CONFIG_EXPLICIT_DENY:
        return f"配置项 {key} 会影响数据存储位置，不允许通过对话修改，请在设置页操作。"
    for segment in segments:
        if segment.startswith("_"):
            return f"配置项 {key} 不是公开配置字段，不允许通过对话修改。"
        lowered = segment.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            return f"配置项 {key} 属于密钥/凭据类敏感字段，一律不允许通过对话修改。"
        # Token match ("data_dir" → {"data", "dir"}) so plain words like
        # "profile" don't trip the "file" marker.
        tokens = lowered.replace("-", "_").split("_")
        if any(marker in tokens for marker in _PATH_MARKERS):
            return f"配置项 {key} 涉及路径/存储位置，不允许通过对话修改，请在设置页操作。"
    return None


def _resolve_config_leaf(config: Any, key: str) -> tuple[Any, str, Any] | None:
    """Walk ``key`` to (owner, leaf_attr, current_value); None when unknown."""
    segments = key.split(".")
    owner = config
    for segment in segments[:-1]:
        owner = getattr(owner, segment, _MISSING)
        if owner is _MISSING or isinstance(owner, (str, int, float, bool, list, tuple)):
            return None
    leaf = segments[-1]
    current = getattr(owner, leaf, _MISSING)
    if current is _MISSING:
        return None
    return owner, leaf, current


def _coerce_config_value(raw: str, current: Any) -> tuple[bool, Any]:
    """Coerce the string ``raw`` to the type of ``current`` (scalar only)."""
    if isinstance(current, bool):
        normalized = raw.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True, True
        if normalized in ("false", "0", "no", "off"):
            return True, False
        return False, f"无法把「{raw}」解析为布尔值（可用 true/false）"
    if isinstance(current, int):
        try:
            return True, int(raw.strip())
        except ValueError:
            return False, f"无法把「{raw}」解析为整数"
    if isinstance(current, float):
        try:
            value = float(raw.strip())
        except ValueError:
            return False, f"无法把「{raw}」解析为数字"
        if not math.isfinite(value):
            return False, "配置数字必须是有限值，不能为 NaN 或无穷大"
        return True, value
    if isinstance(current, str):
        return True, raw
    return False, "该配置项不是普通标量（文本/数字/布尔），暂不支持通过对话修改"


async def _update_config(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    config = require_component(ctx.config, "config")
    key = str(args.get("key") or "").strip()
    raw_value = str(args.get("value") or "")
    reason = str(args.get("reason") or "").strip()

    denial = _update_config_denial(key)
    if denial is not None:
        logger.info("update_config refused (whitelist): %s", key)
        raise ValueError(denial)

    resolved = _resolve_config_leaf(config, key)
    if resolved is None:
        raise ValueError(f"配置项不存在: {key}。可用 get_config 查看当前配置结构。")
    _owner, leaf, current = resolved

    coerced, new_value = _coerce_config_value(raw_value, current)
    if not coerced:
        raise ValueError(f"配置项 {key} 修改失败: {new_value}")
    update_hook = getattr(ctx, "config_update_hook", None)
    if new_value == current and not callable(update_hook):
        return f"配置项 {key} 已是目标值 {raw_value}，无需修改。"

    candidate = deepcopy(config)
    candidate_leaf = _resolve_config_leaf(candidate, key)
    assert candidate_leaf is not None
    owner = candidate_leaf[0]
    setattr(owner, leaf, new_value)
    validate_runtime_config(candidate)

    # Production applies one field through the API's durable config queue.
    # It reloads the latest disk revision and never mutates the live Config
    # before runtime handoff succeeds.
    if callable(update_hook):
        outcome = await maybe_await(update_hook(key, new_value))
        return f"已更新配置 {key}: {current!r} → {new_value!r}。{outcome}"

    persist_hook = getattr(ctx, "config_persist_hook", None)
    if not callable(persist_hook):
        raise ToolComponentUnavailableError("组件不可用: config_persist_hook 未初始化")

    previous_config = deepcopy(config)
    try:
        # save_config validates selected persistence fields only. Validate the
        # runtime contract first so a typo cannot replace a working LLM route
        # on disk and strand the very conversation performing the change.
        saved_path = persist_hook(candidate)
    except Exception:
        logger.exception("update_config persist failed, rolled back: %s", key)
        raise

    reload_note = "热重载已触发。"
    reload_hook = getattr(ctx, "config_reload_hook", None)
    if callable(reload_hook):
        try:
            await maybe_await(reload_hook(candidate))
        except Exception as reload_error:
            logger.warning("update_config hot-reload failed: %s", key, exc_info=True)
            rollback_errors: list[str] = []
            try:
                persist_hook(previous_config)
            except Exception as exc:
                logger.exception("update_config file rollback failed: %s", key)
                rollback_errors.append(f"配置文件恢复失败: {exc}")
            # Reload can publish the candidate before a subsequent step fails.
            # Always attempt runtime compensation, even if restoring disk fails.
            try:
                await maybe_await(reload_hook(previous_config))
            except Exception as exc:
                logger.exception("update_config runtime rollback failed: %s", key)
                rollback_errors.append(f"运行时恢复失败: {exc}")
            else:
                ctx.config = previous_config
            if rollback_errors:
                raise RuntimeError(
                    f"热重载失败: {reload_error}；{'；'.join(rollback_errors)}。"
                    "请检查配置文件与运行状态。"
                ) from reload_error
            raise
    else:
        reload_note = "运行时未接线热重载，重启后生效。"

    ctx.config = candidate

    logger.info("update_config applied: %s = %r (reason: %s)", key, new_value, reason or "-")
    suffix = f"（原因: {reason}）" if reason else ""
    return (
        f"已更新配置 {key}: {current!r} → {new_value!r}{suffix}。"
        f"已保存到 {saved_path}，{reload_note}"
    )
