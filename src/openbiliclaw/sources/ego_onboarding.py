"""Bounded first-run UI automation for our isolated, signed Ego installation.

This uses Ego's own temporary loopback CDP endpoint, never Playwright. It clicks
the same controls observed in the official 0.6.0.16 setup page. No hidden
preferences or native completion APIs are used; no browser data is imported
and no system permissions are granted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)


class EgoOnboardingError(RuntimeError):
    """Safe, fixed diagnostic for an unknown or unavailable setup UI."""


_STEPS = (
    (("最快的浏览器", "fastest browser for AI agents"), ("开始使用", "Get started")),
    (("各有各的 Space", "Space for you and your agent"), ("下一步", "Next")),
    (("和 Chrome 一样顺手", "Feels like Chrome"), ("下一步", "Next")),
    (("隐私优先的设计", "Private by design"), ("下一步", "Next")),
    (("将数据导入 ego", "Import data to ego"), ("稍后设置", "Set up later")),
    (("保持联系", "Stay in the loop"), ("跳过", "Skip")),
    (("你是如何了解到 ego", "How did you hear about ego"), ("跳过", "Skip")),
    (("选个喜欢的标签页布局", "Pick your tab style"), ("下一步", "Next")),
    (("一切就绪", "You are all set"), ("打开 ego(lite)", "Open ego(lite)")),
)
_OPTIONS = (
    {"设为默认浏览器", "Set as default browser"},
    {"添加到程序坞", "Add to Dock"},
    {"分享崩溃报告", "Share crash reports"},
)

# Read only setup controls. In particular, never inspect import profile names,
# email/input values, browser history, or cookies exposed by other WebUI pages.
_SNAPSHOT = """(()=>{
 const headings=[...document.querySelectorAll('h1')].map(e=>e.innerText.trim());
 const final=headings.some(h=>['一切就绪','You are all set'].includes(h));
 return {url:location.href,headings:headings,
 buttons:[...document.querySelectorAll('button')].filter(e=>!e.disabled)
   .map(e=>e.innerText.trim()).filter(Boolean),
 checkboxes:final?[...document.querySelectorAll('input[type=checkbox]')]
   .map(e=>({label:e.closest('label')?.innerText.trim()||'',checked:e.checked})):[]};})()"""


def onboarding_action(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Choose one known visible UI action; unknown states never get guessed."""
    if snapshot.get("url") != "chrome://onboarding/":
        raise EgoOnboardingError("ego_setup_unknown_page")
    headings = " ".join(str(x) for x in snapshot.get("headings", []))
    buttons = snapshot.get("buttons", [])
    for index, (titles, choices) in enumerate(_STEPS):
        if not any(title in headings for title in titles):
            continue
        if index == len(_STEPS) - 1:
            checkboxes = snapshot.get("checkboxes", [])
            if len(checkboxes) != len(_OPTIONS) or any(
                sum(box.get("label") in labels for box in checkboxes) != 1 for labels in _OPTIONS
            ):
                raise EgoOnboardingError("ego_setup_unknown_options")
            for box in checkboxes:
                if box.get("checked") is not False:
                    return {"step": index, "checkbox": str(box["label"])}
        matching = [button for button in buttons if button in choices]
        if len(matching) != 1:
            raise EgoOnboardingError("ego_setup_unknown_controls")
        return {"step": index, "button": matching[0], "complete": index == 8}
    raise EgoOnboardingError("ego_setup_unknown_step")


def _action_script(action: dict[str, Any]) -> str:
    # All arguments derive from the fixed selector above, never from website/model data.
    return (
        """(a=>{
      if(location.href!=='chrome://onboarding/')return false;
      if(a.checkbox){const es=[...document.querySelectorAll('input[type=checkbox]')]
        .filter(e=>e.closest('label')?.innerText.trim()===a.checkbox);
        if(es.length!==1||!es[0].checked)return false;es[0].click();return true;}
      const es=[...document.querySelectorAll('button')]
        .filter(e=>!e.disabled&&e.innerText.trim()===a.button);
      if(es.length!==1)return false;es[0].click();return true;
    })("""
        + json.dumps(action, ensure_ascii=False)
        + ")"
    )


async def complete_onboarding(port: int, *, timeout: float = 90) -> None:
    """Operate only the official onboarding target on the caller's verified host."""
    if not 1024 <= port <= 65535:
        raise EgoOnboardingError("ego_setup_invalid_endpoint")
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            target: dict[str, Any] | None = None
            for _ in range(60):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/json/list")
                    response.raise_for_status()
                    matches = [
                        row
                        for row in response.json()
                        if row.get("type") == "browser_ui"
                        and row.get("url") == "chrome://onboarding/"
                    ]
                    if len(matches) > 1:
                        raise EgoOnboardingError("ego_setup_ambiguous_page")
                    if matches:
                        target = matches[0]
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.25)
            if target is None:
                raise EgoOnboardingError("ego_setup_page_unavailable")
            endpoint = str(target.get("webSocketDebuggerUrl", ""))
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "ws"
                or parsed.hostname != "127.0.0.1"
                or parsed.port != port
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/devtools/")
            ):
                raise EgoOnboardingError("ego_setup_invalid_endpoint")
            async with connect(endpoint, max_size=1024 * 1024, open_timeout=5) as socket:
                serial = 0

                async def evaluate(expression: str) -> Any:
                    nonlocal serial
                    serial += 1
                    await socket.send(
                        json.dumps(
                            {
                                "id": serial,
                                "method": "Runtime.evaluate",
                                "params": {"expression": expression, "returnByValue": True},
                            }
                        )
                    )
                    async with asyncio.timeout(5):
                        while True:
                            answer = json.loads(await socket.recv())
                            if answer.get("id") == serial:
                                result = answer.get("result", {})
                                if answer.get("error") or result.get("exceptionDetails"):
                                    raise EgoOnboardingError("ego_setup_command_failed")
                                return result.get("result", {}).get("value")

                previous = -1
                for _ in range(40):
                    try:
                        snapshot = await evaluate(_SNAPSHOT)
                    except TimeoutError:
                        # Reading startup UI is safe to repeat. Keep the outer
                        # deadline; an unanswered action must never be replayed.
                        logger.info("Waiting for the Ego setup page snapshot")
                        continue
                    if not isinstance(snapshot, dict):
                        raise EgoOnboardingError("ego_setup_invalid_snapshot")
                    if snapshot.get("url") == "chrome://onboarding/" and not snapshot.get(
                        "headings"
                    ):
                        # The native target exists before React renders its initial
                        # controls. Wait boundedly, without invoking a hidden API.
                        await asyncio.sleep(0.25)
                        continue
                    action = onboarding_action(snapshot)
                    step = action["step"]
                    if step != previous:
                        logger.info("Ego setup page reached step %s", step)
                    if step < previous:
                        raise EgoOnboardingError("ego_setup_unexpected_transition")
                    # Allow resuming a partly completed setup but never clicking
                    # repeated Next during an animated transition to another step.
                    if step == previous and "checkbox" not in action and step != 8:
                        await asyncio.sleep(0.25)
                        continue
                    try:
                        applied = await evaluate(_action_script(action))
                    except TimeoutError as exc:
                        raise EgoOnboardingError("ego_setup_action_timeout") from exc
                    if not applied:
                        raise EgoOnboardingError("ego_setup_control_changed")
                    previous = step
                    if action.get("complete"):
                        # A disappeared page is not proof of completion. Caller must
                        # restart without debugging and verify the native CLI channel.
                        return
                    await asyncio.sleep(0.5)
                raise EgoOnboardingError("ego_setup_transition_timeout")
