"""Portable, declarative and bounded browser discovery plans.

Plans contain selectors and data, never executable code or credentials. The
compiler is used before persistence and again before execution. Browser session
identities and login material are deliberately owned outside this document.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from string import Formatter
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

WorkflowStatus = Literal["ok", "empty", "login_required", "failed", "degraded"]
_VARIABLES = frozenset({"query", "date_from", "date_to"})
_ACTION_FIELDS = {
    "goto": {"action", "url"},
    "fill_search": {"action", "selector", "value"},
    "submit_search": {"action", "selector"},
    "click_link": {"action", "selector"},
    "wait_for": {"action", "selector"},
    "collect": {
        "action",
        "item_selector",
        "link_selector",
        "title_selector",
        "author_selector",
        "published_selector",
        "published_attribute",
        "description_selector",
        "empty_selector",
        "next_selector",
    },
}


class BrowserWorkflowValidationError(ValueError):
    """A workflow exceeds its declarative authority or supported limits."""


@dataclass(frozen=True, slots=True)
class WorkflowParameters:
    """Per-run search values; interpolated as text or URL-encoded data."""

    query: str = ""
    date_from: str = ""
    date_to: str = ""

    def render(self, template: str, *, url: bool = False) -> str:
        values = asdict(self)
        if any(len(value) > 1000 for value in values.values()):
            raise BrowserWorkflowValidationError("workflow parameter exceeds 1000 characters")
        if url:
            values = {key: quote(value, safe="") for key, value in values.items()}
        return template.format_map(values)


@dataclass(frozen=True, slots=True)
class WorkflowAuth:
    """Positive login readiness and login-wall evidence; no login secrets."""

    required: bool = False
    ready_selector: str = ""
    login_selector: str = ""
    login_origins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowBudgets:
    """Hard ceilings, inherited by every executor rather than model decisions."""

    max_items: int = 20
    max_pages: int = 3
    timeout_seconds: int = 45


@dataclass(frozen=True, slots=True)
class BrowserWorkflowPlan:
    """Compiled portable plan. Call ``to_dict`` for JSON persistence."""

    version: int
    entry_url: str
    allowed_origins: tuple[str, ...]
    steps: tuple[dict[str, str], ...]
    auth: WorkflowAuth = field(default_factory=WorkflowAuth)
    budgets: WorkflowBudgets = field(default_factory=WorkflowBudgets)
    resource_origins: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["allowed_origins"] = list(self.allowed_origins)
        result["steps"] = [dict(step) for step in self.steps]
        result["resource_origins"] = list(self.resource_origins)
        result["auth"]["login_origins"] = list(self.auth.login_origins)
        return result


@dataclass(frozen=True, slots=True)
class BrowserWorkflowItem:
    """Extracted discovery metadata, awaiting ordinary content admission."""

    title: str
    url: str
    author_name: str = ""
    published_at: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class BrowserWorkflowResult:
    """Never infer a successful empty result from an extraction failure."""

    status: WorkflowStatus
    items: tuple[BrowserWorkflowItem, ...] = ()
    detail: str = ""
    error_code: str = ""
    scope_complete: bool = True
    pages_visited: int = 0
    observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def workflow_url(value: object) -> str:
    """Normalize an HTTP(S) URL without DNS access; network checks follow at run time."""

    if not isinstance(value, str) or not value or len(value) > 4096:
        raise BrowserWorkflowValidationError("URL must be a nonempty HTTP(S) string")
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise BrowserWorkflowValidationError("URL contains unsupported characters")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise BrowserWorkflowValidationError("URL authority is invalid") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise BrowserWorkflowValidationError("URL must use HTTP(S)")
    if parts.username is not None or parts.password is not None:
        raise BrowserWorkflowValidationError("URL must not contain credentials")
    try:
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise BrowserWorkflowValidationError("URL hostname is invalid") from exc
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BrowserWorkflowValidationError("URL must use a public DNS hostname")
    if (
        "." not in host
        or host.endswith((".localhost", ".local", ".internal", ".home", ".lan"))
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
            for part in host.split(".")
        )
    ):
        raise BrowserWorkflowValidationError("URL must use a public DNS hostname")
    default_port = 443 if parts.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))


def workflow_origin(url: str) -> str:
    """Return a normalized exact origin."""

    parts = urlsplit(workflow_url(url))
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _object(value: object, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(key not in fields for key in value):
        raise BrowserWorkflowValidationError(f"{name} has unknown fields or is not an object")
    return value


def _selector(value: object, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > 300 or (required and not value.strip()):
        raise BrowserWorkflowValidationError("selector must be a bounded CSS selector")
    value = value.strip()
    if value and (
        value.startswith(("@", "ref=", "xpath=", "text=", "loc=", "css=", "//"))
        or any(char in value for char in ("\n", "\r", "\x00", "{", "}", ">>"))
    ):
        raise BrowserWorkflowValidationError("persisted locators must be stable CSS selectors")
    return value


def _template(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise BrowserWorkflowValidationError("template must contain 1..4096 characters")
    try:
        for _, variable, format_spec, conversion in Formatter().parse(value):
            if variable is not None and (variable not in _VARIABLES or format_spec or conversion):
                raise BrowserWorkflowValidationError("unsupported workflow template parameter")
    except ValueError as exc:
        raise BrowserWorkflowValidationError("invalid workflow template") from exc
    return value


def _integer(value: object, lower: int, upper: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise BrowserWorkflowValidationError(f"{field_name} must be an integer {lower}..{upper}")
    return value


def _optional_origins(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) > 12:
        raise BrowserWorkflowValidationError(
            "optional origins must be a list of at most 12 origins"
        )
    origins: list[str] = []
    for origin in value:
        normalized = workflow_origin(workflow_url(origin))
        parts = urlsplit(str(origin))
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise BrowserWorkflowValidationError("origins must not contain paths or queries")
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


def compile_browser_workflow(payload: Mapping[str, Any]) -> BrowserWorkflowPlan:
    """Reject unsupported operations, authority changes, secrets and unbounded plans."""

    _object(
        payload,
        {"version", "entry_url", "allowed_origins", "resource_origins", "steps", "auth", "budgets"},
        "workflow",
    )
    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise BrowserWorkflowValidationError("workflow version must be 1")
    entry = workflow_url(payload.get("entry_url"))
    raw_origins = payload.get("allowed_origins")
    if (
        isinstance(raw_origins, (str, bytes))
        or not isinstance(raw_origins, Sequence)
        or not 1 <= len(raw_origins) <= 12
    ):
        raise BrowserWorkflowValidationError("allowed_origins must have 1..12 exact origins")
    origins: list[str] = []
    for raw in raw_origins:
        normalized = workflow_url(raw)
        parsed = urlsplit(str(raw))
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise BrowserWorkflowValidationError("allowed_origins must not contain paths")
        origin = workflow_origin(normalized)
        if origin not in origins:
            origins.append(origin)
    if workflow_origin(entry) not in origins:
        raise BrowserWorkflowValidationError("entry_url is outside allowed_origins")
    raw_steps = payload.get("steps")
    if (
        isinstance(raw_steps, (str, bytes))
        or not isinstance(raw_steps, Sequence)
        or not 1 <= len(raw_steps) <= 20
    ):
        raise BrowserWorkflowValidationError("workflow must have 1..20 steps")
    steps: list[dict[str, str]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            raise BrowserWorkflowValidationError("each workflow step must be an object")
        action = raw_step.get("action")
        if not isinstance(action, str) or action not in _ACTION_FIELDS:
            raise BrowserWorkflowValidationError("unsupported workflow action")
        _object(raw_step, _ACTION_FIELDS[action], "step")
        step = {"action": action}
        if action == "goto":
            step["url"] = _template(raw_step.get("url"))
            # Dynamic fields cannot change the authority or scheme.
            authority = urlsplit(step["url"]).netloc
            if "{" in authority or "}" in authority:
                raise BrowserWorkflowValidationError("URL authority cannot be parameterized")
            rendered = WorkflowParameters().render(step["url"], url=True)
            if workflow_origin(rendered) not in origins:
                raise BrowserWorkflowValidationError("goto is outside allowed_origins")
        elif action == "collect":
            for key in _ACTION_FIELDS[action] - {"action", "published_attribute"}:
                step[key] = _selector(
                    raw_step.get(key),
                    required=key
                    in {
                        "item_selector",
                        "link_selector",
                    },
                )
            attribute = raw_step.get("published_attribute", "datetime")
            if attribute not in {"", "datetime", "title", "data-date", "content"}:
                raise BrowserWorkflowValidationError("unsupported publication attribute")
            step["published_attribute"] = attribute
        else:
            step["selector"] = _selector(raw_step.get("selector"), required=True)
            if action == "fill_search":
                step["value"] = _template(raw_step.get("value"))
        steps.append(step)
    if steps[-1]["action"] != "collect" or sum(s["action"] == "collect" for s in steps) != 1:
        raise BrowserWorkflowValidationError("workflow must end with exactly one collect step")
    raw_auth = _object(
        payload.get("auth", {}),
        {"required", "ready_selector", "login_selector", "login_origins"},
        "auth",
    )
    required = raw_auth.get("required", False)
    if not isinstance(required, bool):
        raise BrowserWorkflowValidationError("auth.required must be boolean")
    auth = WorkflowAuth(
        required,
        _selector(raw_auth.get("ready_selector"), required=required),
        _selector(raw_auth.get("login_selector")),
        _optional_origins(raw_auth.get("login_origins", [])),
    )
    raw_budgets = _object(
        payload.get("budgets", {}), {"max_items", "max_pages", "timeout_seconds"}, "budgets"
    )
    budgets = WorkflowBudgets(
        _integer(raw_budgets.get("max_items", 20), 1, 50, "max_items"),
        _integer(raw_budgets.get("max_pages", 3), 1, 5, "max_pages"),
        _integer(raw_budgets.get("timeout_seconds", 45), 5, 120, "timeout_seconds"),
    )
    return BrowserWorkflowPlan(
        1,
        entry,
        tuple(origins),
        tuple(steps),
        auth,
        budgets,
        _optional_origins(payload.get("resource_origins", [])),
    )
