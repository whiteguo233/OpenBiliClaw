"""Safe, stable user-facing reasons for paused custom discovery."""

from __future__ import annotations

from typing import Any

FAILURE_ALERT_THRESHOLD = 3
HEALTHY_STATUSES = {"ok", "empty", "not_modified", "ready"}


def attention_reason(code: str) -> dict[str, str]:
    """Use fixed copy, never a browser error, challenge URL or credential."""
    verification = code == "verification_required"
    return {
        "code": "verification_required" if verification else "login_required",
        "title": "网站需要安全验证" if verification else "网站需要登录",
        "message": (
            "网站要求人工安全验证。请完成验证并成功试采后继续。"
            if verification
            else "网站登录已失效或需要登录。请登录并成功试采后继续。"
        ),
        "action_label": "处理验证" if verification else "处理登录",
    }


def failure_reason(error: str, status: str = "") -> dict[str, str]:
    """Classify known transport codes without exposing raw errors or URLs."""
    code = error.lower().split(" (", 1)[0]
    if status == "rate_limited" or code in {"rate_limited", "http_429"}:
        kind, title, message = (
            "rate_limited",
            "网站暂时限流",
            "网站限制了访问频率，系统会等待后重试。",
        )
    elif code in {"timeout", "request_timeout", "ego_timeout", "ego_navigation_timeout"}:
        kind, title, message = (
            "timeout",
            "连接网站超时",
            "网站响应超时，请检查网络；系统会稍后重试。",
        )
    elif code in {
        "network_error",
        "request_error",
        "dns_error",
        "ego_navigation_failed",
        "http_error",
        "upstream_error",
        "robots_unavailable",
    }:
        kind, title, message = (
            "network_error",
            "暂时无法访问网站",
            "无法正常访问网站，请检查站点和网络；系统会稍后重试。",
        )
    elif code in {"candidate_queue_full", "candidate_enqueue_error"}:
        kind, title, message = (
            "queue_error",
            "采集内容暂未入队",
            "内容交接暂时失败，系统会保留进度并重试。",
        )
    elif code.startswith(("ego_host_", "ego_setup_")) or code in {
        "executor_unavailable",
        "ego_unavailable",
        "browser_unavailable",
        "ego_not_installed",
        "ego_not_ready",
        "ego_connection_timeout",
        "ego_connection_closed",
        "ego_protocol_timeout",
        "ego_protocol_error",
        "ego_session_identity_changed",
    }:
        kind, title, message = (
            "runtime_unavailable",
            "网页执行环境不可用",
            "请检查网页执行环境状态；系统会稍后重试。",
        )
    elif code in {
        "extraction_unverified",
        "invalid_selector",
        "content_scope_changed",
        "invalid_feed",
        "parse_error",
        "invalid_xml",
        "invalid_json",
        "unsupported_content_type",
        "invalid_content_type",
        "empty_extraction",
        "invalid_document",
        "schema_changed",
    }:
        kind, title, message = (
            "parse_error",
            "来源内容无法正常解析",
            "网站内容或结构可能变化，请查看最近运行并在聊一聊中调整采集要求。",
        )
    elif code in {"access_denied", "robots_denied", "blocked_address", "origin_not_allowed"}:
        kind, title, message = (
            "access_denied",
            "网站访问受限",
            "网站拒绝访问或当前采集范围不允许访问，请检查来源权限或更换来源。",
        )
    elif code == "not_found":
        kind, title, message = (
            "not_found",
            "来源地址不存在",
            "网站返回地址不存在，请在聊一聊中更新来源地址或更换来源。",
        )
    else:
        kind, title, message = (
            "fetch_failed",
            "来源采集持续失败",
            "采集未正常完成，请查看来源和最近运行；系统会稍后重试。",
        )
    return {
        "code": f"source_{kind}",
        "title": title,
        "message": message,
        "action_label": "查看来源",
    }


def workflow_issue(workflow: dict[str, Any]) -> dict[str, Any] | None:
    """Keep an issue active during a retry; only a completed success clears it."""
    if not workflow.get("enabled", True):
        return None
    status = str(workflow.get("status", ""))
    if status == "login_required":
        return {**attention_reason(str(workflow.get("last_error", ""))), "action": "source_login"}
    failures = int(workflow.get("consecutive_failures", 0))
    if failures < FAILURE_ALERT_THRESHOLD or status in HEALTHY_STATUSES:
        return None
    return {
        **failure_reason(str(workflow.get("last_error", "")), status),
        "action": "source_settings",
        "failure_count": failures,
        "next_retry_at": workflow.get("next_run_at", 0),
    }


def workflow_attention(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    """Project actionable failures and first-retry status from durable state."""
    issues = [issue for w in workflows if (issue := workflow_issue(w))]
    issues.sort(key=lambda issue: issue["action"] != "source_login")
    if not issues:
        failed = next(
            (w for w in workflows if w.get("enabled", True) and w.get("consecutive_failures", 0)),
            None,
        )
        return {
            "attention_required": False,
            "attention_count": 0,
            "attention_code": "",
            "attention_action": "",
            "message": (
                f"已连续失败 {failed['consecutive_failures']} 次。"
                + failure_reason(str(failed.get("last_error", "")), str(failed.get("status", "")))[
                    "message"
                ]
                if failed
                else ""
            ),
        }
    reason = issues[0]
    failures = max((issue.get("failure_count", 0) for issue in issues), default=0)
    prefix = (
        f"已连续失败 {failures} 次。"
        if reason["action"] == "source_settings"
        else f"{len(issues)} 个采集任务需要处理。"
    )
    return {
        "attention_required": True,
        "attention_count": len(issues),
        "attention_code": reason["code"],
        "attention_action": reason["action"],
        "action_label": reason["action_label"],
        "title": reason["title"],
        "failure_count": failures,
        "next_retry_at": reason.get("next_retry_at", 0),
        "message": prefix + reason["message"],
    }
