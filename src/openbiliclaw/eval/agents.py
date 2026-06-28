"""Claude Agent SDK wrappers for eval agents.

Each agent uses `claude_agent_sdk.query()` with system_prompt
for role definition and tool permissions for autonomous operation.
JSON is extracted from the assistant's text response.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from text that may contain markdown code blocks.

    Tries multiple strategies:
    1. Closed markdown code block
    2. Truncated code block (opening ``` but no closing ```)
    3. Full text as JSON
    4. Brace-balanced substring
    5. Truncated JSON repair (append missing closing braces)
    """
    if not text or not text.strip():
        msg = "Empty response — cannot extract JSON"
        raise ValueError(msg)

    # Strategy 1: closed code block
    for match in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            result = json.loads(block)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue

    # Strategy 2: truncated code block (has opening ``` but no closing ```)
    trunc_match = re.search(r"```(?:json)?\s*\n?(.+)", text, re.DOTALL)
    if trunc_match:
        block = trunc_match.group(1).strip()
        # Try parsing as-is first
        result = _try_parse_json_dict(block)
        if result is not None:
            return result
        # Try repairing truncated JSON
        result = _repair_truncated_json(block)
        if result is not None:
            return result

    # Strategy 3: full text as JSON
    stripped = text.strip()
    if stripped.startswith("{"):
        result = _try_parse_json_dict(stripped)
        if result is not None:
            return result

    # Strategy 4: brace-balanced substring (string-aware)
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    result = _try_parse_json_dict(text[start : i + 1])
                    if result is not None:
                        return result
                    break

    # Strategy 5: repair truncated JSON from first { to end
    if start is not None and start >= 0:
        result = _repair_truncated_json(text[start:])
        if result is not None:
            return result

    msg = "No valid JSON object found in response"
    raise ValueError(msg)


def _try_parse_json_dict(text: str) -> dict[str, Any] | None:
    """Try to parse text as a JSON dict, return None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Try to repair truncated JSON by appending missing closing braces/brackets.

    Walks the string tracking brace/bracket depth, then appends the
    necessary closing characters. Handles up to 10 levels of nesting.
    """
    # Strip trailing whitespace and incomplete tokens
    cleaned = text.rstrip()
    # Remove trailing comma (common in truncated arrays/objects)
    if cleaned.endswith(","):
        cleaned = cleaned[:-1]
    # Remove trailing incomplete string (no closing quote)
    # Count unescaped quotes
    in_string = False
    escape_next = False
    open_stack: list[str] = []
    for ch in cleaned:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            open_stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and open_stack:
            open_stack.pop()
    # If still inside a string, try to close it
    suffix = ""
    if in_string:
        suffix += '"'
    # Append missing closing brackets/braces in reverse order
    suffix += "".join(reversed(open_stack))
    if not suffix or len(open_stack) > 10:
        return None
    return _try_parse_json_dict(cleaned + suffix)


async def _collect_text(prompt: str, options: Any) -> str:
    """Run a query and collect all assistant text from the response."""
    all_text, _ = await _collect_text_with_last(prompt, options)
    return all_text


async def _collect_text_with_last(prompt: str, options: Any) -> tuple[str, str]:
    """Run a query and return (all_text, last_message_text).

    For multi-turn agents, the final JSON result is usually in the last
    assistant message, while earlier messages contain tool-use reasoning.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock, query

    all_parts: list[str] = []
    last_parts: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                last_parts = []
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        all_parts.append(block.text)
                        last_parts.append(block.text)
    except Exception as exc:
        logger.error("SDK query failed: %s (type=%s)", exc, type(exc).__name__)
        for attr in ("stderr", "output", "args"):
            val = getattr(exc, attr, None)
            if val and val != exc.args:
                logger.error("  SDK %s: %s", attr, str(val)[:500])
        raise
    return "\n".join(all_parts), "\n".join(last_parts)


async def collect_json(
    prompt: str,
    options: Any,
    *,
    max_retries: int = 2,
    label: str = "",
    json_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect structured JSON from LLM via Claude Agent SDK.

    Uses ``output_format='json'`` with ``max_turns=2`` for reliable
    structured output.  Falls back to text extraction + repair on
    parse failures.

    If *json_schema* is provided it is appended to the prompt as a
    human-readable schema hint so the model knows the expected shape.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    # Append schema hint to prompt
    if json_schema is not None:
        import json as _json

        schema_text = _json.dumps(json_schema, ensure_ascii=False, indent=2)[:2000]
        prompt = f"{prompt}\n\nJSON Schema (输出必须符合此结构):\n{schema_text}"

    # Patch options to enable JSON output mode.
    # output_format='json' only works with max_turns >= 2.
    original_system = getattr(options, "system_prompt", "") or ""
    json_options = ClaudeAgentOptions(
        system_prompt=original_system + "\n只返回纯 JSON 对象，不要 code fence 或其他文字。",
        max_turns=max(getattr(options, "max_turns", 1), 2),
        output_format="json",
    )
    # Copy optional fields if they were set
    for attr in ("allowed_tools", "cwd"):
        val = getattr(options, attr, None)
        if val is not None:
            setattr(json_options, attr, val)

    import time as _time

    tag = f"[{label}] " if label else ""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = _time.monotonic()
        text = ""
        try:
            logger.info("%sSDK call start (attempt %d/%d)", tag, attempt, max_retries)
            text = await _collect_text(prompt, json_options)
            elapsed = _time.monotonic() - t0
            logger.info("%sSDK call done (%.1fs)", tag, elapsed)
            if not text.strip():
                msg = "LLM returned empty response"
                raise ValueError(msg)
            # With output_format='json', response should be pure JSON
            result = _try_parse_json_dict(text.strip())
            if result is not None:
                return result
            # Fall back to extraction strategies (code fence, etc.)
            return _extract_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning(
                "%sJSON extraction attempt %d/%d failed (%.1fs): %s",
                tag,
                attempt,
                max_retries,
                _time.monotonic() - t0,
                exc,
            )
            if text:
                logger.warning("%sRaw response (first 500 chars): %s", tag, text[:500])
            if attempt < max_retries:
                prompt = f"{prompt}\n\n⚠️ 上一次回复 JSON 解析失败（{exc}），请返回纯 JSON 对象。"
        except Exception as exc:
            elapsed = _time.monotonic() - t0
            last_error = exc
            logger.error(
                "%sSDK runtime error attempt %d/%d (%.1fs): %s [%s]",
                tag,
                attempt,
                max_retries,
                elapsed,
                exc,
                type(exc).__name__,
            )
            if attempt >= max_retries:
                break
            import asyncio as _asyncio

            await _asyncio.sleep(2)
    raise ValueError(f"collect_json failed after {max_retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Persona generation prompt snippet (shared by all auto-optimize scripts)
# ---------------------------------------------------------------------------

PERSONA_SCHEMA_HINT = """
严格按照以下字段名生成，不要用其他 key：
{
  "personality_portrait": "至少200字的人格叙事",
  "core": {
    "core_traits": ["特质1", "特质2", "特质3"],
    "deep_needs": ["需求1", "需求2"],
    "mbti": {
      "type": "INTJ",
      "dimensions": {
        "E_I": {"pole": "I", "strength": 0.8},
        "S_N": {"pole": "N", "strength": 0.7},
        "T_F": {"pole": "T", "strength": 0.75},
        "J_P": {"pole": "J", "strength": 0.65}
      },
      "confidence": 0.8,
      "inferred_from": ["行为依据1"]
    }
  },
  "values_layer": {
    "values": ["价值观1", "价值观2"],
    "motivational_drivers": ["驱动力1", "驱动力2"]
  },
  "interest": {
    "likes": [
      {"domain": "领域名", "weight": 0.9, "specifics": [{"name": "子项", "weight": 0.8}]}
    ],
    "dislikes": [{"domain": "讨厌的领域", "weight": 0.8}],
    "favorite_up_users": ["UP主1"]
  },
  "role": {
    "life_stage": "当前生活阶段描述",
    "current_phase": "近期状态描述"
  },
  "surface": {
    "cognitive_style": ["认知风格1"],
    "exploration_openness": 0.7,
    "style": {"depth_preference": 0.8}
  }
}
注意：values_layer 必须包含 "values" 和 "motivational_drivers" 两个数组字段。
role 必须包含 "life_stage" 和 "current_phase" 两个字符串字段。
likes 中每个 domain 必须包含 "domain"、"weight"、"specifics" 字段。
""".strip()


# ---------------------------------------------------------------------------
# JSON Schemas for structured output
# ---------------------------------------------------------------------------

ONION_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "personality_portrait": {"type": "string"},
        "core": {
            "type": "object",
            "properties": {
                "core_traits": {"type": "array", "items": {"type": "string"}},
                "deep_needs": {"type": "array", "items": {"type": "string"}},
                "mbti": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "dimensions": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "pole": {"type": "string"},
                                    "strength": {"type": "number"},
                                },
                            },
                        },
                        "confidence": {"type": "number"},
                        "inferred_from": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
        "values_layer": {
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": {"type": "string"}},
                "motivational_drivers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "interest": {
            "type": "object",
            "properties": {
                "likes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"},
                            "weight": {"type": "number"},
                            "specifics": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "weight": {"type": "number"},
                                    },
                                },
                            },
                        },
                    },
                },
                "dislikes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"},
                            "weight": {"type": "number"},
                            "specifics": {"type": "array"},
                        },
                    },
                },
                "favorite_up_users": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "role": {
            "type": "object",
            "properties": {
                "life_stage": {"type": "string"},
                "current_phase": {"type": "string"},
            },
        },
        "surface": {
            "type": "object",
            "properties": {
                "cognitive_style": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "exploration_openness": {"type": "number"},
            },
        },
    },
    "required": ["personality_portrait", "core", "interest"],
}

EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_type": {"type": "string"},
                    "title": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": ["event_type", "title"],
            },
        },
    },
    "required": ["events"],
}

EVAL_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "layer_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "score": {"type": "number"},
                    "deviations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "score": {"type": "number"},
                                "deviation": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "overall_score": {"type": "number"},
        "attributions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["layer_scores", "overall_score"],
}

PARAM_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["file_path", "old_text", "new_text", "reason"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["changes"],
}


# ---------------------------------------------------------------------------
# Agent runners
# ---------------------------------------------------------------------------


async def run_persona_agent(
    constraints: dict[str, str],
) -> OnionProfile:
    """Generate a ground truth persona using Claude Agent SDK."""
    from claude_agent_sdk import ClaudeAgentOptions

    from openbiliclaw.soul.profile import OnionProfile

    schema_text = json.dumps(ONION_PROFILE_SCHEMA, ensure_ascii=False, indent=2)
    prompt = (
        "请根据以下约束条件，生成一个虚构但合理、自洽的 B 站用户画像。\n"
        "画像必须像一个真实的人——兴趣之间有关联，性格和行为模式一致。\n"
        "所有内容用中文。likes 至少 3 个宽域，每个至少 2 个窄域。\n"
        "personality_portrait 至少 200 字。\n\n"
        f"约束条件:\n{json.dumps(constraints, ensure_ascii=False, indent=2)}\n\n"
        f"请返回严格 JSON（用 ```json 代码块包裹），结构如下:\n{schema_text}"
    )

    text = await _collect_text(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=("你是一个用户画像生成器。请只返回一个 JSON 代码块，不要有其他文字。"),
            max_turns=1,
        ),
    )
    data = _extract_json(text)
    return OnionProfile.from_dict(data)


async def run_event_agent(
    persona: OnionProfile,
    event_count: int = 100,
) -> list[dict[str, Any]]:
    """Generate simulated behavioral events using Claude Agent SDK."""
    from claude_agent_sdk import ClaudeAgentOptions

    persona_context = persona.to_llm_context()
    prompt = (
        f"根据以下用户画像，生成 {event_count} 条该用户在 B 站上的行为事件。\n"
        f"事件类型包括 view/search/like/coin/favorite/comment/feedback/dialogue。\n"
        f"视频标题要像真实 B 站视频。所有内容用中文。\n\n"
        f"用户画像:\n{persona_context}\n\n"
        '请返回 JSON 代码块，格式: {"events": [{"event_type": "...", "title": "..."}]}'
    )

    text = await _collect_text(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt="你是行为模拟器。只返回一个 JSON 代码块，不要其他文字。",
            max_turns=1,
        ),
    )
    data = _extract_json(text)
    events = data.get("events", []) if isinstance(data, dict) else []
    return [e for e in events if isinstance(e, dict)]


async def run_eval_agent(
    expected: OnionProfile,
    predicted: OnionProfile,
) -> dict[str, Any]:
    """Evaluate predicted vs expected profile using Claude Agent SDK."""
    from claude_agent_sdk import ClaudeAgentOptions

    prompt = (
        "请逐层逐字段对比以下两个用户画像，评估预测画像的准确性。\n"
        "对每个字段打 0-1 分，并说明偏差原因。\n\n"
        f"期望画像 (ground truth):\n{expected.to_llm_context()}\n\n"
        f"预测画像:\n{predicted.to_llm_context()}\n\n"
        "请返回 JSON 代码块，格式:\n"
        '{"layer_scores": [{"layer": "core", "score": 0.8, '
        '"deviations": [{"field": "core_traits", "score": 0.7, '
        '"deviation": "缺少xxx"}]}], "overall_score": 0.8, '
        '"attributions": ["归因1"]}'
    )

    text = await _collect_text(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=(
                "你是画像评估专家。严格对比两个画像，给出精确的逐层评分。"
                "只返回一个 JSON 代码块。评分客观：完全匹配1.0，部分匹配0.5-0.8，不匹配0-0.3。"
            ),
            max_turns=1,
        ),
    )
    try:
        return _extract_json(text)
    except (ValueError, json.JSONDecodeError):
        logger.warning("Eval agent did not return valid JSON")
        return {"layer_scores": [], "overall_score": 0.0, "attributions": []}


async def run_optimizer_agent(
    eval_report: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Optimize prompts and pipeline code based on eval deviations.

    This agent has Read/Grep/Glob tools — it can autonomously read code
    to understand both prompt templates and pipeline logic before proposing changes.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    from openbiliclaw.eval.optimizer import MODIFIABLE_FILES

    report_text = json.dumps(eval_report, ensure_ascii=False, indent=2)[:4000]
    files_text = "\n".join(f"  - {f}" for f in MODIFIABLE_FILES)
    prompt = (
        "根据以下评估报告，分析画像系统的偏差原因并提出修改。\n\n"
        "你可以修改 prompt 模板和 pipeline 代码。可修改的文件：\n"
        f"{files_text}\n\n"
        "<pipeline_architecture>\n"
        "数据流：用户事件 → pipeline.ingest_batch() → 信号分类到各层缓冲区\n"
        "  → pipeline.flush() → layer_updaters._update_XXX() → 更新 OnionProfile\n\n"
        "关键函数：\n"
        "  - layer_updaters._update_interest(): 调 PreferenceAnalyzer → 合并偏好 → 同步到 profile\n"
        "  - layer_updaters._update_surface(): 计算 depth_preference"
        "（目前不更新 cognitive_style）\n"
        "  - layer_updaters._update_role/values/core(): 目前全部 return changed=False（TODO）\n"
        "  - preference_analyzer.analyze_events(): LLM 提取兴趣/风格/UP主/讨厌话题\n"
        "  - profile.populate_from_flat_preference(): 将 flat 偏好同步到 OnionProfile 结构\n"
        "  - prompts.py: 所有 LLM prompt 模板\n"
        "</pipeline_architecture>\n\n"
        "每次最多修改 2 处。修改必须是精确的 diff 级别（old_text → new_text）。\n"
        "对 pipeline 代码的修改必须保持函数签名不变、不引入新依赖。\n\n"
        "⚠️ 关键输出要求：\n"
        "1. old_text 和 new_text 只包含需要修改的最小片段（3-10 行），"
        "不要复制整个函数或整段 schema\n"
        "2. old_text 必须是文件中能精确匹配到的原文（包括缩进和换行）\n"
        "3. 你的最后一条消息必须且只能包含一个 ```json 代码块\n\n"
        "最终返回格式（```json 代码块）:\n"
        '{"changes": [{"file_path": "src/openbiliclaw/...", "old_text": "最小修改片段", '
        '"new_text": "替换后的片段", "reason": "修改原因"}], "summary": "一句话总结"}\n\n'
        f"评估报告:\n{report_text}"
    )

    import time as _time

    t0 = _time.monotonic()
    logger.info("[optimizer] Agent start (max_turns=10, scope=%d files)", len(MODIFIABLE_FILES))
    try:
        all_text, last_text = await _collect_text_with_last(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=(
                    "你是画像系统优化专家。你可以修改 prompt 模板和 pipeline Python 代码。\n"
                    "先用 Read 工具阅读相关代码文件理解当前结构和数据流，\n"
                    "然后提出精确的文本修改。\n\n"
                    "原则：最小化修改，不破坏函数签名和导入，每次只改 1-2 处。\n"
                    "如果偏差根因在 pipeline 代码（如字段未同步、更新逻辑缺失），"
                    "优先修改代码而非 prompt。\n\n"
                    "⚠️ 输出格式硬性要求：\n"
                    "- 你的最后一条消息必须是且仅是一个 ```json 代码块\n"
                    "- 不要在 JSON 前后添加任何解释文字\n"
                    "- old_text/new_text 只取最小必要片段（3-10 行），不要复制大段代码\n"
                    '- 如果没有可行修改，返回 {"changes": [], "summary": "原因"}'
                ),
                allowed_tools=["Read", "Grep", "Glob"],
                cwd=str(project_root),
                max_turns=10,
                output_format={
                    "type": "json_schema",
                    "schema": PARAM_CHANGE_SCHEMA,
                },
            ),
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        logger.error("[optimizer] Agent crashed (%.1fs): %s", elapsed, exc)
        return {"changes": [], "summary": f"optimizer agent crashed: {exc!s:.100}"}
    elapsed = _time.monotonic() - t0
    logger.info("[optimizer] Agent done (%.1fs, response %d chars)", elapsed, len(all_text))
    # Try last message first (most likely to contain the JSON result),
    # then fall back to full text (in case JSON spans multiple messages)
    for text_source in [last_text, all_text]:
        if not text_source:
            continue
        try:
            return _extract_json(text_source)
        except (ValueError, json.JSONDecodeError):
            continue

    # JSON extraction failed — use a follow-up collect_json call to
    # format the agent's analysis into the required JSON structure.
    logger.warning("Optimizer agent did not return valid JSON (%.1fs), reformatting...", elapsed)
    agent_analysis = (last_text or all_text or "")[:3000]
    try:
        return await collect_json(
            prompt=(
                "以下是优化专家的分析结果，请提取其中的代码修改方案为 JSON。\n\n"
                "⚠️ 关键要求：\n"
                "- old_text 和 new_text 只取最小片段（3-10 行），不要复制整段代码\n"
                "- 如果分析中没有明确的 old_text → new_text 修改，返回空 changes\n"
                "- summary 用一句话概括分析结论\n\n"
                f"分析内容:\n{agent_analysis}\n\n"
                '返回纯 JSON: {"changes": [...], "summary": "..."}'
            ),
            options=ClaudeAgentOptions(
                system_prompt=(
                    "你是 JSON 提取工具。从分析文本中提取代码修改方案。\n"
                    "只返回纯 JSON 对象，不要 code fence，不要解释。\n"
                    "old_text/new_text 保持简短（最小必要片段）。"
                ),
                max_turns=2,
                output_format="json",
            ),
            max_retries=1,
            label="optimizer_reformat",
            json_schema=PARAM_CHANGE_SCHEMA,
        )
    except Exception:
        logger.warning("Optimizer reformat also failed")
        return {"changes": [], "summary": agent_analysis[:200]}


# ---------------------------------------------------------------------------
# Speculation evaluation agents
# ---------------------------------------------------------------------------


async def run_speculation_event_agent(
    speculations: list[dict[str, str]],
    event_count: int = 30,
    matching_ratio: float = 0.4,
) -> dict[str, Any]:
    """Generate simulated future events for speculation testing.

    Returns {"matching_events": [...], "non_matching_events": [...]}
    where matching events should confirm speculations and non-matching
    events should NOT.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    matching_count = max(1, int(event_count * matching_ratio))
    non_matching_count = event_count - matching_count
    domains_text = "\n".join(
        f"- {s.get('domain', '')}（{s.get('category', '')}）" for s in speculations
    )

    return await collect_json(
        prompt=(
            f"生成两组 B站 行为事件用于测试推测兴趣系统：\n\n"
            f"推测兴趣方向:\n{domains_text}\n\n"
            f"1. matching_events: {matching_count} 条，标题中应包含推测方向的关键词，\n"
            f"   确保事件内容确实与推测方向相关。混合不同的相关程度：\n"
            f"   部分强相关（标题直接包含关键词），部分弱相关（只是主题接近）\n"
            f"2. non_matching_events: {non_matching_count} 条，关于热门但无关的话题\n\n"
            f'返回 JSON: {{"matching_events": [{{"event_type": "view", "title": "..."}}], '
            f'"non_matching_events": [{{"event_type": "view", "title": "..."}}]}}'
        ),
        options=ClaudeAgentOptions(
            system_prompt="你是 B站 行为事件模拟器。直接返回 ```json 代码块，不要有任何前置解释。",
            max_turns=1,
        ),
        max_retries=2,
    )


# ---------------------------------------------------------------------------
# Discovery optimization agent
# ---------------------------------------------------------------------------


async def run_discovery_optimizer_agent(
    eval_report: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Optimize discovery prompts based on eval dimension deviations.

    Similar to run_optimizer_agent but focused on discovery-specific prompts
    and pipeline code.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    from openbiliclaw.eval.discovery_optimizer import DISCOVERY_MODIFIABLE_FILES

    report_text = json.dumps(eval_report, ensure_ascii=False, indent=2)[:4000]
    files_text = "\n".join(f"  - {f}" for f in DISCOVERY_MODIFIABLE_FILES)
    prompt = (
        "根据以下发现系统评估报告，分析内容发现质量的偏差原因并提出修改。\n\n"
        "你可以修改 prompt 模板和发现策略代码。可修改的文件：\n"
        f"{files_text}\n\n"
        "<discovery_architecture>\n"
        "数据流：SoulProfile → 4 种发现策略并发执行 → 合并去重排序 → 多样性压缩 → 缓存\n\n"
        "策略文件职责：\n"
        "  - search.py: 生成搜索词并调用 B站搜索 API\n"
        "  - trending.py: 抓取 B站排行榜分区内容\n"
        "  - related_chain.py: 从种子视频出发沿关联链扩展\n"
        "  - explore.py: LLM 生成跨域探索方向并搜索\n"
        "  - engine.py: 策略编排、去重、多样性压缩\n\n"
        "可优化的 prompt（按策略归因）:\n"
        "  - build_search_queries_prompt(): 搜索词生成（影响 query_quality / diversity）\n"
        "  - build_content_evaluation_prompt(): 内容匹配度评估（影响 relevance / specificity）\n"
        "  - build_explore_domains_prompt(): 跨域探索方向生成\n"
        "</discovery_architecture>\n\n"
        "原则：最小化修改，不破坏函数签名和导入，每次只改 1-2 处。\n"
        "如果偏差根因在策略代码（如过滤逻辑、topic_key 分配），优先修改代码而非 prompt。\n"
        "对代码的修改必须通过 pytest 验证。\n\n"
        "⚠️ 关键输出要求：\n"
        "1. old_text 和 new_text 只包含需要修改的最小片段（3-10 行），不要复制整个函数\n"
        "2. old_text 必须是文件中能精确匹配到的原文（包括缩进和换行）\n"
        "3. 你的最后一条消息必须且只能包含一个 ```json 代码块\n\n"
        "最终返回格式（```json 代码块）:\n"
        '{"changes": [{"file_path": "src/openbiliclaw/...", "old_text": "最小修改片段", '
        '"new_text": "替换后的片段", "reason": "修改原因"}], "summary": "一句话总结"}\n\n'
        f"评估报告:\n{report_text}"
    )

    import time as _time

    t0 = _time.monotonic()
    logger.info(
        "[discovery_optimizer] Agent start (max_turns=10, scope=%d files)",
        len(DISCOVERY_MODIFIABLE_FILES),
    )
    try:
        all_text, last_text = await _collect_text_with_last(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=(
                    "你是发现系统优化专家。你的目标是提高 B 站内容发现的质量。\n"
                    "你可以修改 prompt 模板和发现策略 Python 代码。\n"
                    "先用 Read 工具阅读相关代码文件理解当前结构，\n"
                    "然后提出精确的文本修改。最后返回一个 JSON 代码块包含修改方案。\n"
                    "原则：最小化修改，不破坏函数签名和导入，每次只改 1-2 处。\n"
                    "如果偏差根因在策略代码（如过滤逻辑、topic_key 分配），"
                    "优先修改代码而非 prompt。\n\n"
                    "⚠️ 输出格式硬性要求：\n"
                    "- 你的最后一条消息必须是且仅是一个 ```json 代码块\n"
                    "- 不要在 JSON 前后添加任何解释文字\n"
                    "- old_text/new_text 只取最小必要片段（3-10 行），不要复制大段代码\n"
                    '- 如果没有可行修改，返回 {"changes": [], "summary": "原因"}'
                ),
                allowed_tools=["Read", "Grep", "Glob"],
                cwd=str(project_root),
                max_turns=10,
                output_format={
                    "type": "json_schema",
                    "schema": PARAM_CHANGE_SCHEMA,
                },
            ),
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        logger.error("[discovery_optimizer] Agent crashed (%.1fs): %s", elapsed, exc)
        return {"changes": [], "summary": f"optimizer agent crashed: {exc!s:.100}"}
    elapsed = _time.monotonic() - t0
    logger.info(
        "[discovery_optimizer] Agent done (%.1fs, response %d chars)",
        elapsed,
        len(all_text),
    )
    for text_source in [last_text, all_text]:
        if not text_source:
            continue
        try:
            return _extract_json(text_source)
        except (ValueError, json.JSONDecodeError):
            continue

    logger.warning(
        "Discovery optimizer agent did not return valid JSON (%.1fs), reformatting...",
        elapsed,
    )
    agent_analysis = (last_text or all_text or "")[:3000]
    try:
        return await collect_json(
            prompt=(
                "以下是优化分析的结果，请将其转换为严格的 JSON 格式。\n"
                "如果分析中提到了具体的代码修改方案（old_text → new_text），提取为 changes。\n"
                "如果没有提到可执行的修改，返回空 changes 列表。\n\n"
                f"分析内容:\n{agent_analysis}\n\n"
                "返回格式:\n"
                '{"changes": [{"file_path": "...", "old_text": "...", '
                '"new_text": "...", "reason": "..."}], "summary": "一句话总结"}'
            ),
            options=ClaudeAgentOptions(
                system_prompt=(
                    "你是 JSON 格式化工具。将输入的分析文本提取为指定的 JSON 结构。只返回纯 JSON。"
                ),
                max_turns=2,
            ),
            max_retries=1,
            label="discovery_optimizer_reformat",
            json_schema=PARAM_CHANGE_SCHEMA,
        )
    except Exception:
        logger.warning("Discovery optimizer reformat also failed")
        return {"changes": [], "summary": agent_analysis[:200]}
