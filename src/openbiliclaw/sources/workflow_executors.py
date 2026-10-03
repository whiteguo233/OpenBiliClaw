"""Portable browser execution with isolated persistent login sessions.

Only fixed repository-authored JavaScript is evaluated. A workflow supplies data
and stable CSS selectors, never host code. Chromium resolves allowed names to
prevalidated pinned addresses; request routing enforces origins and methods.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from openbiliclaw.sources.browser_install import browser_runtime_status
from openbiliclaw.sources.browser_workflow import (
    BrowserWorkflowItem,
    BrowserWorkflowPlan,
    BrowserWorkflowResult,
    BrowserWorkflowValidationError,
    WorkflowParameters,
    compile_browser_workflow,
    workflow_origin,
    workflow_url,
)
from openbiliclaw.sources.web_public import _is_global_address, _system_resolver

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from playwright.async_api import BrowserContext, CDPSession, Page, Playwright, Request, Route

    from openbiliclaw.sources.web_public import DNSResolver

# These limits are operational resource ceilings, not relevance thresholds.
_MAX_REQUESTS = 250
_STEP_TIMEOUT_MS = 10_000
# Large deferred search indexes can delay DOMContentLoaded on otherwise healthy
# public sites. The enclosing workflow timeout remains the total run ceiling.
_NAVIGATION_TIMEOUT_MS = 30_000
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SNAPSHOT_JS = r"""() => {
  const visible = e => !!(e && e.getClientRects().length);
  const selector = e => {
    if (!e) return '';
    if (e.id && e.id.length <= 80) return '#' + CSS.escape(e.id);
    let s = e.tagName.toLowerCase();
    const name = e.getAttribute('name');
    if (name && name.length <= 80) return s + '[name=' + JSON.stringify(name) + ']';
    const cs = [...e.classList].filter(x => /^[a-zA-Z_][\w-]{0,50}$/.test(x)).slice(0,3);
    return s + cs.map(x=>'.'+CSS.escape(x)).join('');
  };
  const path = (e,stop=null) => {
    const parts=[];
    while(e && e!==stop && e!==document.body && parts.length<5) {
      parts.unshift(selector(e));
      if(!stop && e.id && e.id.length<=80) break;
      e=e.parentElement;
    }
    if(stop && e!==stop) return '';
    const result=parts.join(' > ');
    return result.length<=300?result:'';
  };
  const safeUrl = s => {try {const u=new URL(s,location.href);u.username='';u.password='';
    u.search='';u.hash='';return u.href;}catch{return '';}};
  const text = e => ['INPUT','TEXTAREA','SELECT','FORM'].includes(e.tagName) ? '' :
    (e.innerText||'').trim().slice(0,180);
  const main = document.querySelector('main,[role=main],#main-content,#content,.main-content')
    || document.body;
  const tags='a,button,input,form,article,section,ul,li,p,span.meta,time,h1,h2,h3';
  const controls=[...document.querySelectorAll('input,form,button,textarea,select')]
    .filter(visible).slice(0,20);
  const content=[...main.querySelectorAll(tags)].filter(visible).slice(0,80);
  const remaining=[...document.querySelectorAll(tags)].filter(visible).slice(0,100);
  const chosen=[...new Set([...controls,...content,...remaining])].slice(0,100);
  const rows=chosen.map(e=>{
    const ancestors=[];
    for(let a=e.parentElement;a && a!==document.body && ancestors.length<3;a=a.parentElement)
      ancestors.push(selector(a));
    const childLinks=[...e.querySelectorAll('a[href]')].filter(visible).slice(0,3).map(a=>({
      selector:path(a,e),href:safeUrl(a.href),text:text(a).slice(0,120)
    })).filter(a=>a.selector);
    return {tag:e.tagName.toLowerCase(),selector:selector(e),path_selector:path(e),
      text:text(e),href:e.tagName==='A'?safeUrl(e.href):'',type:e.getAttribute('type')||'',
      method:e.tagName==='FORM'?(e.method||'get'):'',
      action:e.tagName==='FORM'?safeUrl(e.action):'',
      parent_selector:selector(e.parentElement),parent_path_selector:path(e.parentElement),
      ancestor_selectors:ancestors,
      previous_sibling_selector:selector(e.previousElementSibling),
      next_sibling_selector:selector(e.nextElementSibling),
      child_selectors:[...e.children].slice(0,6).map(selector),child_links:childLinks};
  });
  return {url:safeUrl(location.href),title:document.title.slice(0,200),main_selector:selector(main),
    elements:rows,login_wall:[...document.querySelectorAll('input[type=password]')].some(visible)};
}"""
_COLLECT_JS = r"""({step,limit}) => {
  const text = (node,sel) => {const e=sel?node.querySelector(sel):node;
    return (e?.innerText||e?.textContent||'').trim();};
  const visible = e => !!(e && e.getClientRects().length);
  const items=[...document.querySelectorAll(step.item_selector)].slice(0,limit).map(node=>{
    const a=node.querySelector(step.link_selector);
    const t=step.published_selector?node.querySelector(step.published_selector):null;
    return {title:text(node,step.title_selector||step.link_selector).slice(0,500),url:a?.href||'',
      author_name:step.author_selector?text(node,step.author_selector).slice(0,200):'',
      published_at:(t?.getAttribute(step.published_attribute)||t?.textContent||'').slice(0,100),
      description:step.description_selector?text(node,step.description_selector).slice(0,2000):''};
  });
  const next=step.next_selector?document.querySelector(step.next_selector):null;
  const empty=step.empty_selector?visible(document.querySelector(step.empty_selector)):false;
  return {items,empty,
    next:next?.href||''};
}"""
_FORM_JS = r"""selector => {
  const matches=document.querySelectorAll(selector);
  if(matches.length!==1 || matches[0].tagName!=='FORM') return null;
  const f=matches[0];
  if((f.method||'get').toLowerCase()!=='get') return null;
  if(f.querySelector('input[type=password]')) return null;
  const fields=[...new FormData(f).entries()].filter(([k,v])=>typeof v==='string');
  return {action:f.action,fields};
}"""
_SEARCH_INPUT_JS = r"""selector => {
  const es=document.querySelectorAll(selector); if(es.length!==1) return false;
  const e=es[0]; return e.tagName==='INPUT' && ['search','text',''].includes(e.type)
    && !/password|token|secret|credential|otp|verification/i.test(e.name+' '+e.id);
}"""


class BrowserWorkflowExecutionError(RuntimeError):
    """Body-free machine-readable browser execution failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _browser_start_error(exc: BaseException) -> BrowserWorkflowExecutionError:
    """Retain diagnosable network codes without returning URLs or browser call logs."""

    match = re.search(r"net::(ERR_[A-Z_]+)", str(exc))
    if match:
        code = match.group(1).lower()
        return BrowserWorkflowExecutionError(code, f"Browser network request failed ({code})")
    if type(exc).__name__ == "TimeoutError":
        return BrowserWorkflowExecutionError(
            "browser_timeout", "Website did not load before the browser timeout"
        )
    return BrowserWorkflowExecutionError(
        "browser_error", "Browser session could not open the website"
    )


class BrowserNetworkPolicy:
    """DNS pinning and exact-origin policy, with a trusted local-fixture seam.

    ``test_loopback_origins`` is Python dependency injection only; plans and API
    payloads cannot enable it. It permits only 127.0.0.1, never other local IPs.
    """

    def __init__(
        self,
        *,
        resolver: DNSResolver | None = None,
        test_loopback_origins: Sequence[str] = (),
    ) -> None:
        self._resolver = resolver or _system_resolver
        self._test_origins = frozenset(test_loopback_origins)

    async def pinned_rules(self, origins: Sequence[str]) -> str:
        """Resolve every allowed origin once and pin Chromium's socket destination."""

        pins: dict[str, str] = {}
        for origin in origins:
            parts = urlsplit(origin)
            host = str(parts.hostname)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            try:
                async with asyncio.timeout(5):
                    addresses = await self._resolver(host, port)
            except (OSError, TimeoutError, ValueError) as exc:
                raise BrowserWorkflowExecutionError(
                    "dns_error", "Website DNS lookup failed"
                ) from exc
            if (
                not addresses
                or len(addresses) > 32
                or any(
                    not _is_global_address(address)
                    and not (origin in self._test_origins and address == "127.0.0.1")
                    for address in addresses
                )
            ):
                raise BrowserWorkflowExecutionError(
                    "blocked_address", "Website resolves to a non-public network address"
                )
            address = next((ip for ip in addresses if ":" not in ip), addresses[0])
            if ":" in address:
                address = f"[{address}]"
            if host in pins and pins[host] != address:
                raise BrowserWorkflowExecutionError(
                    "dns_changed", "Website DNS changed during setup"
                )
            pins[host] = address
        # Unknown destinations fail in Chromium even before application routing.
        return ", ".join([*(f"MAP {host} {ip}" for host, ip in pins.items()), "MAP * ~NOTFOUND"])

    def require_url(self, url: str, origins: Sequence[str]) -> str:
        """Validate a navigation, redirect, resource or extracted URL."""

        try:
            normalized = workflow_url(url)
            origin = workflow_origin(normalized)
        except BrowserWorkflowValidationError as exc:
            raise BrowserWorkflowExecutionError("blocked_url", "Unsupported browser URL") from exc
        if origin not in origins:
            raise BrowserWorkflowExecutionError(
                "origin_not_allowed", "Website origin is not allowed"
            )
        return normalized


class BrowserWorkflowSession:
    """One isolated browser profile; manual actions require explicit interactive mode."""

    def __init__(
        self,
        *,
        context: BrowserContext,
        page: Page,
        playwright: Playwright,
        lock: asyncio.Lock,
        plan: BrowserWorkflowPlan,
        policy: BrowserNetworkPolicy,
        interactive: bool,
    ) -> None:
        self.context = context
        self.page = page
        self.plan = plan
        self._playwright = playwright
        self._lock = lock
        self._policy = policy
        self.interactive = interactive
        self._closed = False
        self._requests = 0
        self._blocked = 0
        self._last_block_code = ""
        self.pages_visited = 0
        self._login_redirect = False
        self._blocked_resources: set[str] = set()
        self._blocked_navigation: set[str] = set()
        self._response_cdp: CDPSession | None = None
        self._observation_stopped = False
        self._embedded_blocked = False
        self._network_requests: dict[str, tuple[str, str]] = {}
        self._resource_failures: dict[str, dict[str, Any]] = {}

    def _observe_request(self, event: dict[str, Any]) -> None:
        """Keep only body-free origin/type metadata for bounded response diagnostics."""

        request_id = str(event.get("requestId", ""))
        if not request_id or len(self._network_requests) >= _MAX_REQUESTS:
            return
        try:
            origin = workflow_origin(str(event.get("request", {}).get("url", "")))
        except BrowserWorkflowValidationError:
            return
        resource_type = str(event.get("type", "Other"))
        if resource_type not in {
            "Document",
            "Script",
            "Stylesheet",
            "Image",
            "Font",
            "XHR",
            "Fetch",
            "Other",
        }:
            resource_type = "Other"
        self._network_requests[request_id] = (origin, resource_type)

    def _record_resource_failure(
        self, request_id: str, *, status: int = 0, error_code: str = ""
    ) -> None:
        metadata = self._network_requests.get(request_id)
        if metadata is None or (
            request_id not in self._resource_failures and len(self._resource_failures) >= 12
        ):
            return
        failure = self._resource_failures.setdefault(
            request_id, {"origin": metadata[0], "resource_type": metadata[1]}
        )
        if status:
            failure["status"] = status
        if error_code:
            failure["error_code"] = error_code

    def _observe_response(self, event: dict[str, Any]) -> None:
        """ORB may hide normal response events; extra-info retains its HTTP status."""

        status = event.get("statusCode")
        if isinstance(status, int) and 400 <= status <= 599:
            self._record_resource_failure(str(event.get("requestId", "")), status=status)

    def _observe_loading_failure(self, event: dict[str, Any]) -> None:
        match = re.search(r"net::(ERR_[A-Z_]+)", str(event.get("errorText", "")))
        if match:
            self._record_resource_failure(
                str(event.get("requestId", "")), error_code=match.group(1).lower()
            )

    async def _route(self, route: Route, request: Request) -> None:
        self._requests += 1
        try:
            if self._requests > _MAX_REQUESTS:
                raise BrowserWorkflowExecutionError(
                    "request_budget", "Browser request budget exceeded"
                )
            if request.frame != self.page.main_frame:
                raise BrowserWorkflowExecutionError(
                    "embedded_page_unsupported", "Embedded frames and popup pages are unsupported"
                )
            navigation_origins = self.plan.allowed_origins
            if self.interactive:
                navigation_origins += self.plan.auth.login_origins
            if request.is_navigation_request():
                try:
                    origin = workflow_origin(request.url)
                except BrowserWorkflowValidationError:
                    origin = ""
                if not self.interactive and origin in self.plan.auth.login_origins:
                    self._login_redirect = True
                    raise BrowserWorkflowExecutionError(
                        "login_required", "Website login needs attention"
                    )
                self._policy.require_url(request.url, navigation_origins)
            else:
                self._policy.require_url(
                    request.url, (*navigation_origins, *self.plan.resource_origins)
                )
            methods = {"GET", "HEAD", "OPTIONS"}
            if self.interactive and workflow_origin(request.url) in navigation_origins:
                methods.add("POST")
            if request.method not in methods:
                raise BrowserWorkflowExecutionError(
                    "read_only", "Background requests must be read-only"
                )
        except BrowserWorkflowExecutionError as exc:
            self._blocked += 1
            if exc.code == "embedded_page_unsupported":
                self._embedded_blocked = True
                self._last_block_code = exc.code
                await route.abort("blockedbyclient")
                return
            try:
                blocked_origin = workflow_origin(request.url)
                destination = (
                    self._blocked_navigation
                    if request.is_navigation_request()
                    else self._blocked_resources
                )
                if len(destination) < 12:
                    destination.add(blocked_origin)
            except BrowserWorkflowValidationError:
                pass
            self._last_block_code = exc.code
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _guard_redirect_response(self, event: dict[str, Any]) -> None:
        """Check every redirect before Chromium follows it (route skips later hops)."""

        cdp = self._response_cdp
        if cdp is None:
            return
        request_id = str(event.get("requestId", ""))
        try:
            status = int(event.get("responseStatusCode", 0))
            location = next(
                (
                    str(header["value"])
                    for header in event.get("responseHeaders", [])
                    if str(header.get("name", "")).lower() == "location"
                ),
                "",
            )
            if 300 <= status < 400 and location:
                target = urljoin(str(event["request"]["url"]), location)
                navigation = event.get("resourceType") == "Document"
                allowed = self.plan.allowed_origins
                if self.interactive:
                    allowed += self.plan.auth.login_origins
                if not navigation:
                    allowed += self.plan.resource_origins
                try:
                    origin = workflow_origin(target)
                    if (
                        navigation
                        and not self.interactive
                        and origin in self.plan.auth.login_origins
                    ):
                        self._login_redirect = True
                        raise BrowserWorkflowExecutionError(
                            "login_required", "Website login needs attention"
                        )
                    self._policy.require_url(target, allowed)
                except (BrowserWorkflowExecutionError, BrowserWorkflowValidationError) as exc:
                    self._blocked += 1
                    self._last_block_code = (
                        exc.code
                        if isinstance(exc, BrowserWorkflowExecutionError)
                        else "blocked_url"
                    )
                    try:
                        origin = workflow_origin(target)
                        destination = (
                            self._blocked_navigation if navigation else self._blocked_resources
                        )
                        if len(destination) < 12:
                            destination.add(origin)
                    except BrowserWorkflowValidationError:
                        pass
                    await cdp.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
                    return
            await cdp.send("Fetch.continueRequest", {"requestId": request_id})
        except Exception:
            # Closing/cancelling a session invalidates outstanding protocol requests.
            # A failed guard must never continue an unchecked redirect.
            with suppress(Exception):
                await cdp.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
                )

    async def navigate(self, url: str) -> None:
        """Navigate within the allowed origins and validate final redirect destination."""

        navigation_origins = self.plan.allowed_origins
        if self.interactive:
            navigation_origins += self.plan.auth.login_origins
        checked = self._policy.require_url(url, navigation_origins)
        try:
            await self.page.goto(
                checked, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS
            )
        except Exception:
            if self._login_redirect:
                raise BrowserWorkflowExecutionError(
                    "login_required", "Website redirected to its login provider"
                ) from None
            raise
        self._policy.require_url(self.page.url, navigation_origins)
        self.pages_visited += 1

    async def snapshot(self) -> dict[str, Any]:
        """Bounded DOM facts; excludes input values and URL query/credential values."""

        if self._observation_stopped:
            parsed = urlsplit(self.plan.entry_url)
            result: dict[str, Any] = {
                "url": urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
                "title": "",
                "elements": [],
                "login_wall": self._login_redirect,
            }
        else:
            value = await self.page.evaluate(_SNAPSHOT_JS)
            result = dict(value) if isinstance(value, dict) else {}
        result["blocked_requests"] = self._blocked
        result["unsupported_embedded_pages"] = self._embedded_blocked
        result["blocked_resource_origins"] = sorted(self._blocked_resources)
        result["blocked_navigation_origins"] = sorted(self._blocked_navigation)
        result["resource_failures"] = list(self._resource_failures.values())
        if self._last_block_code:
            result["last_block_code"] = self._last_block_code
        return result

    async def screenshot(self) -> bytes:
        """Capture the current viewport for an authenticated human takeover surface."""

        return await self.page.screenshot(type="png", full_page=False)

    async def act(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """Keep manual credentials out of browser exception messages and server logs."""

        try:
            return await self._act(action)
        except BrowserWorkflowExecutionError:
            raise
        except Exception:
            raise BrowserWorkflowExecutionError(
                "manual_action_failed", "Manual browser action failed; refresh the login page"
            ) from None

    async def _act(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """Perform a human-authored action without persisting its text or credentials."""

        if not self.interactive:
            raise BrowserWorkflowExecutionError(
                "manual_only", "Manual control requires login takeover"
            )
        kind = action.get("action")
        if kind not in {"click", "fill", "key"}:
            raise BrowserWorkflowExecutionError(
                "invalid_action", "Unsupported manual browser action"
            )
        if kind == "click" and "x" in action and "y" in action:
            x, y = action["x"], action["y"]
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not 0 <= x <= 1280
                or not 0 <= y <= 900
            ):
                raise BrowserWorkflowExecutionError("invalid_action", "Invalid viewport coordinate")
            await self.page.mouse.click(x, y)
        else:
            selector = action.get("selector")
            if selector is not None and (not isinstance(selector, str) or len(selector) > 300):
                raise BrowserWorkflowExecutionError("invalid_action", "Invalid manual selector")
            if kind in {"click", "fill"} and not selector:
                raise BrowserWorkflowExecutionError("invalid_action", "Manual selector is required")
            if kind == "click":
                await self.page.locator(str(selector)).click(timeout=_STEP_TIMEOUT_MS)
            elif kind == "fill":
                value = action.get("value")
                if not isinstance(value, str) or len(value) > 4096:
                    raise BrowserWorkflowExecutionError("invalid_action", "Invalid manual input")
                await self.page.locator(str(selector)).fill(value, timeout=_STEP_TIMEOUT_MS)
            else:
                key = action.get("key")
                if key not in {
                    "Enter",
                    "Tab",
                    "Escape",
                    "Backspace",
                    "ArrowDown",
                    "ArrowUp",
                    "Space",
                }:
                    raise BrowserWorkflowExecutionError("invalid_action", "Unsupported manual key")
                await self.page.keyboard.press(str(key))
        return await self.snapshot()

    async def close(self) -> None:
        """Flush the profile and release its exclusive execution lease."""

        if self._closed:
            return
        self._closed = True
        try:
            await self.context.close()
        finally:
            try:
                await self._playwright.stop()
            finally:
                self._lock.release()

    async def login_required(self) -> bool:
        auth = self.plan.auth
        if (
            auth.login_selector
            and await self.page.locator(auth.login_selector).count()
            and await self.page.locator(auth.login_selector).first.is_visible()
        ):
            return True
        password = self.page.locator('input[type="password"]')
        if await password.count() and await password.first.is_visible():
            return True
        if auth.required:
            ready = self.page.locator(auth.ready_selector)
            return not (await ready.count() and await ready.first.is_visible())
        return False

    async def execute(self, parameters: WorkflowParameters) -> BrowserWorkflowResult:
        """Run declarative read operations on this session's already opened entry page."""

        if self.interactive:
            raise BrowserWorkflowExecutionError(
                "manual_active", "Finish login before background execution"
            )
        if await self.login_required():
            return BrowserWorkflowResult(
                "login_required",
                detail="Website login needs user attention",
                error_code="login_required",
                scope_complete=False,
                pages_visited=self.pages_visited,
            )
        for step in self.plan.steps:
            action = step["action"]
            if action == "goto":
                await self.navigate(parameters.render(step["url"], url=True))
            elif action == "fill_search":
                if not await self.page.evaluate(_SEARCH_INPUT_JS, step["selector"]):
                    raise BrowserWorkflowExecutionError(
                        "not_search_input", "Search input is not a safe text field"
                    )
                await self.page.locator(step["selector"]).fill(
                    parameters.render(step["value"]), timeout=_STEP_TIMEOUT_MS
                )
            elif action == "submit_search":
                form = await self.page.evaluate(_FORM_JS, step["selector"])
                if not isinstance(form, dict):
                    raise BrowserWorkflowExecutionError(
                        "not_get_search", "Search requires a GET form without credentials"
                    )
                target = self._policy.require_url(str(form["action"]), self.plan.allowed_origins)
                parts = urlsplit(target)
                fields = [(str(k), str(v)) for k, v in form["fields"]]
                target = urlunsplit(
                    (
                        parts.scheme,
                        parts.netloc,
                        parts.path,
                        urlencode([*parse_qsl(parts.query), *fields]),
                        "",
                    )
                )
                await self.navigate(target)
            elif action == "click_link":
                href = await self.page.locator(step["selector"]).get_attribute(
                    "href", timeout=_STEP_TIMEOUT_MS
                )
                if not href:
                    raise BrowserWorkflowExecutionError(
                        "not_link", "Discovery navigation requires a real link"
                    )
                await self.navigate(urljoin(self.page.url, href))
            elif action == "wait_for":
                await self.page.locator(step["selector"]).first.wait_for(
                    state="visible", timeout=_STEP_TIMEOUT_MS
                )
            elif action == "collect":
                return await self._collect(step)
            if await self.login_required():
                return BrowserWorkflowResult(
                    "login_required",
                    detail="Website login needs user attention",
                    error_code="login_required",
                    scope_complete=False,
                    pages_visited=self.pages_visited,
                )
        raise BrowserWorkflowExecutionError("invalid_plan", "Workflow has no collection step")

    async def _collect(self, step: dict[str, str]) -> BrowserWorkflowResult:
        items: list[BrowserWorkflowItem] = []
        seen_urls: set[str] = set()
        seen_pages = {self.page.url}
        for page_index in range(self.plan.budgets.max_pages):
            if await self.login_required():
                return BrowserWorkflowResult(
                    "login_required",
                    tuple(items),
                    "Website login expired",
                    "login_required",
                    False,
                    self.pages_visited,
                )
            data = await self.page.evaluate(
                _COLLECT_JS, {"step": step, "limit": self.plan.budgets.max_items}
            )
            invalid = False
            for raw in data["items"]:
                try:
                    url = self._policy.require_url(str(raw["url"]), self.plan.allowed_origins)
                except BrowserWorkflowExecutionError:
                    invalid = True
                    continue
                title = str(raw["title"]).strip()
                if not title:
                    invalid = True
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                items.append(
                    BrowserWorkflowItem(
                        title,
                        url,
                        str(raw["author_name"]),
                        str(raw["published_at"]),
                        str(raw["description"]),
                    )
                )
                if len(items) >= self.plan.budgets.max_items:
                    return BrowserWorkflowResult(
                        "ok", tuple(items), scope_complete=False, pages_visited=self.pages_visited
                    )
            if invalid or (not data["items"] and not data["empty"]):
                return BrowserWorkflowResult(
                    "degraded" if items else "failed",
                    tuple(items),
                    "Collection selectors need verification",
                    "extraction_unverified",
                    False,
                    self.pages_visited,
                    await self.snapshot(),
                )
            next_url = str(data["next"])
            if not next_url:
                return BrowserWorkflowResult(
                    "ok" if items else "empty", tuple(items), pages_visited=self.pages_visited
                )
            next_url = self._policy.require_url(next_url, self.plan.allowed_origins)
            if next_url in seen_pages:
                return BrowserWorkflowResult(
                    "degraded",
                    tuple(items),
                    "Pagination returned to an earlier page",
                    "pagination_loop",
                    False,
                    self.pages_visited,
                )
            if page_index + 1 < self.plan.budgets.max_pages:
                seen_pages.add(next_url)
                await self.navigate(next_url)
        return BrowserWorkflowResult(
            "ok" if items else "empty",
            tuple(items),
            scope_complete=False,
            pages_visited=self.pages_visited,
        )


class PlaywrightWorkflowExecutor:
    """Chromium executor available on desktop, CLI and Linux/container hosts."""

    def __init__(
        self,
        profile_root: Path,
        *,
        headless: bool = True,
        executable_path: str | None = None,
        policy: BrowserNetworkPolicy | None = None,
    ) -> None:
        self.profile_root = profile_root
        self.headless = headless
        self.executable_path = executable_path
        self.policy = policy or BrowserNetworkPolicy()

    def availability(self) -> dict[str, Any]:
        """Report dependency readiness; browser launch errors remain explicit at run time."""

        status = browser_runtime_status()
        if self.executable_path:
            status["installed"] = os.path.isfile(self.executable_path)
            status["executable_path"] = self.executable_path
        return {"executor": "playwright", "available": status["installed"], **status}

    async def open_session(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        interactive: bool = False,
        observation_only: bool = False,
    ) -> BrowserWorkflowSession:
        """Open the isolated profile and visit the entry; do not run collection steps."""

        plan = compile_browser_workflow(plan.to_dict())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
            raise BrowserWorkflowExecutionError("invalid_session", "Browser session ID is invalid")
        if not self.availability()["available"]:
            raise BrowserWorkflowExecutionError(
                "executor_unavailable", "Install openbiliclaw[browser] and Chromium"
            )
        from playwright.async_api import async_playwright

        network_origins = (*plan.allowed_origins, *plan.resource_origins)
        if interactive:
            network_origins += plan.auth.login_origins
        pinned_rules = await self.policy.pinned_rules(network_origins)
        profile = self.profile_root / session_id
        if profile.is_symlink() or self.profile_root.is_symlink():
            raise BrowserWorkflowExecutionError(
                "invalid_session", "Browser profile cannot be a symlink"
            )
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(profile, 0o700)
        lock = _SESSION_LOCKS.setdefault(str(profile.resolve()), asyncio.Lock())
        if lock.locked():
            raise BrowserWorkflowExecutionError(
                "session_busy", "This website session is already in use"
            )
        await lock.acquire()
        try:
            playwright = await async_playwright().start()
        except BaseException as exc:
            lock.release()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BrowserWorkflowExecutionError(
                "browser_unavailable", "Browser driver could not start"
            ) from None
        context: BrowserContext | None = None
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile),
                headless=self.headless,
                executable_path=self.executable_path,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
                },
                # Explicit context proxy bypass also overrides macOS system proxies.
                # The unused loopback endpoint is a fail-closed fallback; allowing a
                # system proxy would delegate DNS and invalidate our socket pins.
                proxy={"server": "http://127.0.0.1:9", "bypass": "*"},
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 1280, "height": 900},
                args=[
                    f"--host-resolver-rules={pinned_rules}",
                    "--no-proxy-server",
                    "--disable-background-networking",
                    "--disable-extensions",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-features=WebTransport,DnsOverHttps",
                ],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            session = BrowserWorkflowSession(
                context=context,
                page=page,
                playwright=playwright,
                lock=lock,
                plan=plan,
                policy=self.policy,
                interactive=interactive,
            )
            session._response_cdp = await context.new_cdp_session(page)
            session._response_cdp.on("Network.requestWillBeSent", session._observe_request)
            session._response_cdp.on("Network.responseReceivedExtraInfo", session._observe_response)
            session._response_cdp.on("Network.loadingFailed", session._observe_loading_failure)
            await session._response_cdp.send("Network.enable")
            session._response_cdp.on("Fetch.requestPaused", session._guard_redirect_response)
            await session._response_cdp.send(
                "Fetch.enable",
                {
                    "patterns": [{"urlPattern": "*", "requestStage": "Response"}],
                },
            )
            await context.route("**/*", session._route)
            # Playwright >=1.48 is required: do not silently permit socket egress.
            await context.route_web_socket("**/*", lambda socket: socket.close())
            context.on("page", lambda extra: asyncio.create_task(extra.close()))
            page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
            page.on("download", lambda download: asyncio.create_task(download.cancel()))
            try:
                await session.navigate(plan.entry_url)
            except Exception:
                if not (
                    observation_only
                    and session._blocked_navigation
                    and session._last_block_code in {"origin_not_allowed", "login_required"}
                ):
                    raise
                session._observation_stopped = True
            return session
        except BaseException as exc:
            try:
                if context is not None:
                    await context.close()
            finally:
                try:
                    await playwright.stop()
                finally:
                    lock.release()
            if isinstance(exc, (BrowserWorkflowExecutionError, asyncio.CancelledError)):
                raise
            raise _browser_start_error(exc) from None

    async def run(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        parameters: WorkflowParameters | None = None,
    ) -> BrowserWorkflowResult:
        """Execute a bounded run, returning stable diagnostics without secrets or traces."""

        session: BrowserWorkflowSession | None = None
        try:
            async with asyncio.timeout(plan.budgets.timeout_seconds):
                session = await self.open_session(plan, session_id=session_id)
                return await session.execute(parameters or WorkflowParameters())
        except BrowserWorkflowExecutionError as exc:
            return BrowserWorkflowResult(
                "login_required" if exc.code == "login_required" else "failed",
                detail=exc.detail,
                error_code=exc.code,
                scope_complete=False,
            )
        except TimeoutError:
            return BrowserWorkflowResult(
                "failed",
                detail="Browser workflow exceeded its time budget",
                error_code="timeout",
                scope_complete=False,
            )
        except Exception:
            # Browser exceptions include complete URLs and form values. Do not leak them.
            observation: dict[str, Any] = {}
            if session is not None:
                try:
                    async with asyncio.timeout(2):
                        observation = await session.snapshot()
                except Exception:
                    pass
            return BrowserWorkflowResult(
                "failed",
                detail="Browser workflow failed; verify selectors and browser installation",
                error_code="browser_error",
                scope_complete=False,
                observation=observation,
            )
        finally:
            if session is not None:
                await session.close()


class EgoLiteWorkflowExecutor:
    """Diagnose the optional Ego Lite v2 host bridge without launching an unsafe session.

    Official v2 exposes nodejs/taskSpace/page.cdp, but does not expose a fail-closed
    request interceptor or isolated pinned browser launch. Until such a host
    bridge is configured, use the portable Chromium executor for scheduled work.
    """

    def availability(self) -> dict[str, Any]:
        executable = shutil.which("ego-browser")
        detail = (
            "Ego Lite requires a macOS host bridge"
            if sys.platform != "darwin"
            else (
                "ego-browser is not installed"
                if executable is None
                else "Ego Lite v2 cannot enforce the required isolated network policy"
            )
        )
        return {
            "executor": "ego_lite",
            "available": False,
            "installed": executable is not None,
            "detail": detail,
            "fallback": "playwright",
        }

    async def open_session(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        interactive: bool = False,
        observation_only: bool = False,
    ) -> BrowserWorkflowSession:
        """Fail explicitly rather than launching an unisolated personal browser."""

        raise BrowserWorkflowExecutionError(
            "executor_unavailable", str(self.availability()["detail"])
        )

    async def run(
        self,
        plan: BrowserWorkflowPlan,
        *,
        session_id: str,
        parameters: WorkflowParameters | None = None,
    ) -> BrowserWorkflowResult:
        return BrowserWorkflowResult(
            "failed",
            detail=str(self.availability()["detail"]),
            error_code="executor_unavailable",
            scope_complete=False,
        )
