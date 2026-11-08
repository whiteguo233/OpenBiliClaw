"""Declarative browser plan authority and serialization tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from openbiliclaw.sources.browser_workflow import (
    BrowserWorkflowValidationError,
    WorkflowParameters,
    compile_browser_workflow,
)


def workflow_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "entry_url": "https://example.com/topics",
        "allowed_origins": ["https://example.com"],
        "steps": [{"action": "collect", "item_selector": "article", "link_selector": "h2 a"}],
    }


def test_workflow_round_trip_preserves_bounded_defaults() -> None:
    plan = compile_browser_workflow(workflow_payload())
    assert compile_browser_workflow(plan.to_dict()) == plan
    assert plan.budgets.max_items == 20
    assert plan.budgets.max_pages == 3
    assert plan.budgets.timeout_seconds == 45


@pytest.mark.parametrize(
    "key,value",
    [
        ("code", "os.system('unsafe')"),
        ("cookies", {"session": "secret"}),
        ("version", True),
        ("version", 2),
        ("entry_url", "http://127.0.0.1/"),
        ("entry_url", "http://metadata.internal/"),
        ("entry_url", "https://user:secret@example.com"),
        ("entry_url", "file:///etc/passwd"),
        ("entry_url", "https://elsewhere.com/"),
        ("allowed_origins", ["https://example.com/path"]),
        ("allowed_origins", ["https://example.com#anchor"]),
        ("allowed_origins", "https://example.com"),
        ("auth", {"required": True}),
        ("auth", {"password": "secret"}),
        ("budgets", {"max_pages": 6}),
        ("budgets", {"max_items": True}),
        ("budgets", {"timeout_seconds": 121}),
    ],
)
def test_workflow_rejects_authority_or_budget_expansion(key: str, value: object) -> None:
    payload = workflow_payload()
    payload[key] = value
    with pytest.raises(BrowserWorkflowValidationError):
        compile_browser_workflow(payload)


@pytest.mark.parametrize(
    "step",
    [
        {"action": "evaluate", "code": "alert(1)"},
        {"action": "click_link", "selector": "@21"},
        {"action": "fill_search", "selector": "input", "value": "{password}"},
        {"action": "fill_search", "selector": "input", "value": "{query.__class__}"},
        {"action": "goto", "url": "https://{query}/"},
        {"action": "goto", "url": "https://elsewhere.com/{query}"},
        {"action": "click_link", "selector": "a", "javascript": "location.href='x'"},
    ],
)
def test_workflow_rejects_unsafe_steps(step: dict[str, Any]) -> None:
    payload = workflow_payload()
    payload["steps"].insert(0, step)
    with pytest.raises(BrowserWorkflowValidationError):
        compile_browser_workflow(payload)


def test_workflow_requires_positive_login_readiness_and_one_final_collect() -> None:
    payload = workflow_payload()
    payload["auth"] = {"required": True, "ready_selector": ".signed-in"}
    assert compile_browser_workflow(payload).auth.required
    duplicate = deepcopy(payload)
    duplicate["steps"] *= 2
    with pytest.raises(BrowserWorkflowValidationError, match="exactly one"):
        compile_browser_workflow(duplicate)


def test_parameters_are_text_or_encoded_url_data() -> None:
    parameters = WorkflowParameters(query="a/b & c", date_from="2026-10-01")
    assert parameters.render("{query}") == "a/b & c"
    assert parameters.render("https://example.com/search?q={query}&from={date_from}", url=True) == (
        "https://example.com/search?q=a%2Fb%20%26%20c&from=2026-10-01"
    )


def test_resource_and_login_origins_are_distinct_from_discovery_authority() -> None:
    payload = workflow_payload()
    payload["resource_origins"] = ["https://cdn.example.com"]
    payload["auth"] = {
        "required": True,
        "ready_selector": ".account",
        "login_origins": ["https://login.example.com"],
    }
    plan = compile_browser_workflow(payload)
    assert plan.resource_origins == ("https://cdn.example.com",)
    assert plan.auth.login_origins == ("https://login.example.com",)
    assert "https://login.example.com" not in plan.allowed_origins
    assert compile_browser_workflow(plan.to_dict()) == plan
    payload["steps"].insert(0, {"action": "goto", "url": "https://cdn.example.com"})
    with pytest.raises(BrowserWorkflowValidationError):
        compile_browser_workflow(payload)


def test_unused_optional_publication_attribute_can_be_empty() -> None:
    payload = workflow_payload()
    payload["steps"][0]["published_attribute"] = ""
    plan = compile_browser_workflow(payload)
    assert plan.steps[0]["published_attribute"] == ""
    assert compile_browser_workflow(plan.to_dict()) == plan
