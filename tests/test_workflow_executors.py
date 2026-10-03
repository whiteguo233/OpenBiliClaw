"""Real isolated Chromium against controlled local fixtures, never user profiles."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from openbiliclaw.sources.browser_install import browser_runtime_status
from openbiliclaw.sources.browser_workflow import WorkflowParameters, compile_browser_workflow
from openbiliclaw.sources.workflow_executors import (
    BrowserNetworkPolicy,
    BrowserWorkflowExecutionError,
    EgoLiteWorkflowExecutor,
    PlaywrightWorkflowExecutor,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _require_browser_for_live_fixtures(request: pytest.FixtureRequest) -> None:
    if (
        request.node.name.startswith(("test_real_browser", "test_browser_driver"))
        and not browser_runtime_status()["installed"]
    ):
        pytest.skip("install the optional browser runtime to run real Chromium fixtures")


class _FixtureHandler(BaseHTTPRequestHandler):
    requests_seen: list[tuple[str, str]] = []

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        self.requests_seen.append(("GET", self.path))
        path = urlsplit(self.path).path
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", parse_qs(urlsplit(self.path).query)["to"][0])
            self.end_headers()
            return
        if path == "/sso":
            if "test_session=active" not in self.headers.get("Cookie", ""):
                self.send_response(302)
                self.send_header(
                    "Location", f"http://accounts.fixture.test:{self.server.server_port}/login"
                )
                self.end_headers()
                return
            path = "/private"
        if path == "/asset.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(b"window.fixtureAssetLoaded=true")
            return
        if path == "/private" and "test_session=active" not in self.headers.get("Cookie", ""):
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/forbidden")
            self.end_headers()
            return
        if path == "/hierarchy":
            content = (
                "<nav>"
                + "".join(f'<a href="/nav/{i}">Nav {i}</a>' for i in range(150))
                + '</nav><form action="/results?secret=query-secret"><input name="q" '
                'value="input-secret"></form><main id="main-content"><ul class="news">'
                '<li><h2><a href="/content/1?secret=link-secret">First</a></h2>'
                '<span class="meta">Published yesterday</span><p>Summary</p>'
                '<a class="readmore" href="/content/1">Read more</a></li>'
                '<li><h2><a href="/content/2">Second</a></h2></li></ul></main>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode())
            return
        pages = {
            "/iframe": '<article><h2><a href="/content/1">First</a></h2></article>'
            '<iframe src="/forbidden"></iframe>',
            "/cdn-resource": '<article><h2><a href="/content/1">First</a></h2></article>'
            f'<script src="http://cdn.fixture.test:{self.server.server_port}/asset.js"></script>',
            "/page1": '<article><h2><a href="/content/1">First</a></h2><p>One</p>'
            '<span class="author">Ada</span><time datetime="2026-10-02">today</time>'
            '</article><a class="next" href="/page2">Next</a>',
            "/page2": '<article><h2><a href="/content/2">Second</a></h2></article>',
            "/empty": '<p class="empty">No matching discussions</p>',
            "/blank": "<main>Site layout changed</main>",
            "/login": '<form class="login" action="/login" method="post">'
            '<input name="username"><input name="password" type="password">'
            '<button type="submit">Sign in</button></form>',
            "/private": '<div class="account">Signed in</div><article>'
            '<h2><a href="/content/private">Private discussion</a></h2></article>',
            "/search": '<form role="search" action="/results" method="get">'
            '<input type="search" name="q"><button>Search</button></form>',
            "/post-search": '<form role="search" action="/mutate" method="post">'
            '<input type="search" name="q"><button>Search</button></form>',
            "/loop": '<article><h2><a href="/content/1">First</a></h2></article>'
            '<a class="next" href="/loop">Next</a>',
            "/unsafe": '<article><h2><a href="http://127.0.0.1/private">Unsafe</a></h2></article>',
            "/resources": '<article><h2><a href="/content/1">First</a></h2></article>'
            f'<img src="http://127.0.0.1:{self.server.server_port}/forbidden">'
            '<script>fetch("/mutate",{method:"POST",body:"nope"})</script>',
        }
        if path == "/results":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            content = f'<article><h2><a href="/content/result">{query}</a></h2></article>'
        else:
            content = pages.get(path, "<main>Not found</main>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def do_POST(self) -> None:  # noqa: N802
        self.requests_seen.append(("POST", self.path))
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(303)
        self.send_header(
            "Set-Cookie", "test_session=active; Domain=.fixture.test; Path=/; Max-Age=600; HttpOnly"
        )
        self.send_header("Location", f"http://fixture.test:{self.server.server_port}/private")
        self.end_headers()


@pytest.fixture
def fixture_site() -> Iterator[str]:
    _FixtureHandler.requests_seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://fixture.test:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


async def _loopback_resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"]


def _plan(origin: str, path: str = "/page1", **updates: Any) -> Any:
    payload: dict[str, Any] = {
        "version": 1,
        "entry_url": origin + path,
        "allowed_origins": [origin],
        "steps": [
            {
                "action": "collect",
                "item_selector": "article",
                "link_selector": "h2 a",
                "title_selector": "h2",
                "author_selector": ".author",
                "description_selector": "p",
                "published_selector": "time",
                "empty_selector": ".empty",
                "next_selector": "a.next",
            }
        ],
        "budgets": {"timeout_seconds": 20},
    }
    payload.update(updates)
    return compile_browser_workflow(payload)


def _executor(tmp_path: Path, origin: str) -> PlaywrightWorkflowExecutor:
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("browser extra is not installed")
    return PlaywrightWorkflowExecutor(
        tmp_path / "profiles",
        policy=BrowserNetworkPolicy(resolver=_loopback_resolver, test_loopback_origins=[origin]),
    )


async def test_network_policy_rejects_private_dns_without_test_injection() -> None:
    policy = BrowserNetworkPolicy(resolver=_loopback_resolver)
    with pytest.raises(BrowserWorkflowExecutionError, match="non-public"):
        await policy.pinned_rules(["https://public-looking.example"])


async def test_network_policy_pins_validated_ip_and_denies_unknown_hosts() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return ["1.1.1.1", "8.8.8.8"]

    rules = await BrowserNetworkPolicy(resolver=resolver).pinned_rules(["https://example.com"])
    assert rules == "MAP example.com 1.1.1.1, MAP * ~NOTFOUND"


async def test_real_browser_collects_paginates_and_applies_metadata(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    result = await _executor(tmp_path, fixture_site).run(_plan(fixture_site), session_id="public")
    assert result.status == "ok", result
    assert result.pages_visited == 2
    assert [item.title for item in result.items] == ["First", "Second"]
    assert result.items[0].author_name == "Ada"
    assert result.items[0].published_at == "2026-10-02"
    assert result.items[0].description == "One"
    assert result.scope_complete


async def test_real_browser_empty_requires_positive_evidence(
    tmp_path: Path, fixture_site: str
) -> None:
    executor = _executor(tmp_path, fixture_site)
    empty = await executor.run(_plan(fixture_site, "/empty"), session_id="empty")
    assert empty.status == "empty"
    broken = await executor.run(_plan(fixture_site, "/blank"), session_id="empty")
    assert broken.status == "failed"
    assert broken.error_code == "extraction_unverified"
    assert "elements" in broken.observation


async def test_real_browser_dynamic_search_uses_get_only(tmp_path: Path, fixture_site: str) -> None:
    executor = _executor(tmp_path, fixture_site)
    steps = [
        {"action": "fill_search", "selector": "input[name=q]", "value": "{query}"},
        {"action": "submit_search", "selector": "form[role=search]"},
        {"action": "collect", "item_selector": "article", "link_selector": "h2 a"},
    ]
    result = await executor.run(
        _plan(fixture_site, "/search", steps=steps),
        session_id="search",
        parameters=WorkflowParameters(query="photography & light"),
    )
    assert result.status == "ok", result
    assert result.items[0].title == "photography & light"
    rejected = await executor.run(
        _plan(fixture_site, "/post-search", steps=steps),
        session_id="search",
        parameters=WorkflowParameters(query="read only"),
    )
    assert rejected.error_code == "not_get_search"
    assert not any(method == "POST" for method, _ in _FixtureHandler.requests_seen)


async def test_real_browser_login_handoff_persists_session_and_detects_expiry(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    plan = _plan(
        fixture_site,
        "/private",
        auth={"required": True, "ready_selector": ".account", "login_selector": "form.login"},
    )
    before = await executor.run(plan, session_id="login")
    assert before.status == "login_required", before
    session = await executor.open_session(plan, session_id="login", interactive=True)
    try:
        await session.act(
            {"action": "fill", "selector": "input[name=username]", "value": "fixture-user"}
        )
        snapshot = await session.act(
            {"action": "fill", "selector": "input[name=password]", "value": "fixture-secret"}
        )
        assert "fixture-secret" not in json.dumps(snapshot)
        assert "fixture-user" not in json.dumps(snapshot)
        assert (await session.screenshot()).startswith(b"\x89PNG")
        with pytest.raises(BrowserWorkflowExecutionError, match="already in use"):
            await executor.open_session(plan, session_id="login")
        await session.act({"action": "click", "selector": "button"})
        await session.page.wait_for_selector(".account")
    finally:
        await session.close()
    after = await executor.run(plan, session_id="login")
    assert after.status == "ok", after
    assert after.items[0].title == "Private discussion"
    session = await executor.open_session(plan, session_id="login", interactive=True)
    await session.context.clear_cookies()
    await session.close()
    expired = await executor.run(plan, session_id="login")
    assert expired.status == "login_required", expired


async def test_real_browser_blocks_private_navigation_subrequests_and_mutations(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    session = await executor.open_session(_plan(fixture_site, "/resources"), session_id="safety")
    try:
        snapshot = await session.snapshot()
        assert snapshot["blocked_requests"] >= 2
        with pytest.raises(BrowserWorkflowExecutionError, match="Manual control"):
            await session.act({"action": "click", "selector": "a"})
        with pytest.raises(BrowserWorkflowExecutionError, match="Unsupported"):
            await session.navigate("http://127.0.0.1/private")
    finally:
        await session.close()
    assert not any(
        path == "/forbidden" or method == "POST" for method, path in _FixtureHandler.requests_seen
    )
    redirect = await executor.run(_plan(fixture_site, "/redirect-private"), session_id="safety")
    assert redirect.status == "failed"
    assert not any(path == "/forbidden" for _, path in _FixtureHandler.requests_seen)


async def test_real_browser_item_budget_and_pagination_loop(
    tmp_path: Path, fixture_site: str
) -> None:
    executor = _executor(tmp_path, fixture_site)
    result = await executor.run(_plan(fixture_site, budgets={"max_items": 1}), session_id="bounds")
    assert len(result.items) == 1
    assert not result.scope_complete
    loop = await executor.run(_plan(fixture_site, "/loop"), session_id="bounds")
    assert loop.status == "degraded"
    assert loop.error_code == "pagination_loop"
    unsafe = await executor.run(_plan(fixture_site, "/unsafe"), session_id="bounds")
    assert unsafe.status == "failed"
    assert not unsafe.items


def test_ego_reports_unavailable_without_claiming_a_safe_execution_path() -> None:
    availability = EgoLiteWorkflowExecutor().availability()
    assert availability["available"] is False
    assert availability["detail"]
    assert availability["fallback"] == "playwright"


async def test_real_browser_separates_cdn_resources_from_navigation(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    cdn_origin = fixture_site.replace("fixture.test", "cdn.fixture.test")
    executor = PlaywrightWorkflowExecutor(
        tmp_path / "profiles",
        policy=BrowserNetworkPolicy(
            resolver=_loopback_resolver,
            test_loopback_origins=[fixture_site, cdn_origin],
        ),
    )
    session = await executor.open_session(_plan(fixture_site, "/cdn-resource"), session_id="cdn")
    try:
        assert (await session.snapshot())["blocked_resource_origins"] == [cdn_origin]
        assert not any(path == "/asset.js" for _, path in _FixtureHandler.requests_seen)
    finally:
        await session.close()
    session = await executor.open_session(
        _plan(fixture_site, "/cdn-resource", resource_origins=[cdn_origin]),
        session_id="cdn",
    )
    try:
        assert await session.page.evaluate("() => window.fixtureAssetLoaded === true")
        with pytest.raises(BrowserWorkflowExecutionError, match="origin is not allowed"):
            await session.navigate(cdn_origin + "/asset.js")
    finally:
        await session.close()


async def test_real_browser_requires_explicit_manual_sso_origin(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    login_origin = fixture_site.replace("fixture.test", "accounts.fixture.test")
    executor = PlaywrightWorkflowExecutor(
        tmp_path / "profiles",
        policy=BrowserNetworkPolicy(
            resolver=_loopback_resolver,
            test_loopback_origins=[fixture_site, login_origin],
        ),
    )
    plan = _plan(
        fixture_site,
        "/sso",
        auth={"required": True, "ready_selector": ".account", "login_origins": [login_origin]},
    )
    periodic = await executor.run(plan, session_id="sso")
    assert periodic.status == "login_required", periodic
    session = await executor.open_session(plan, session_id="sso", interactive=True)
    try:
        assert session.page.url.startswith(login_origin)
        await session.act(
            {"action": "fill", "selector": "input[name=username]", "value": "fixture"}
        )
        await session.act(
            {"action": "fill", "selector": "input[name=password]", "value": "fixture"}
        )
        await session.act({"action": "click", "selector": "button"})
        await session.page.wait_for_selector(".account")
    finally:
        await session.close()
    resumed = await executor.run(plan, session_id="sso")
    assert resumed.status == "ok", resumed
    assert resumed.items[0].title == "Private discussion"


async def test_browser_driver_start_failure_releases_session_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_site: str,
) -> None:
    import playwright.async_api

    class Driver:
        async def start(self) -> None:
            raise RuntimeError("driver failed")

    monkeypatch.setattr(playwright.async_api, "async_playwright", Driver)
    executor = _executor(tmp_path, fixture_site)
    for _ in range(2):
        with pytest.raises(BrowserWorkflowExecutionError, match="driver could not start"):
            await executor.open_session(_plan(fixture_site), session_id="start-failure")


async def test_real_browser_observation_reports_unapproved_login_origin_without_following(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    session = await executor.open_session(
        _plan(fixture_site, "/sso"), session_id="sso-observe", observation_only=True
    )
    try:
        snapshot = await session.snapshot()
        assert snapshot["blocked_navigation_origins"] == [
            fixture_site.replace("fixture.test", "accounts.fixture.test")
        ]
        assert not any(path == "/login" for _, path in _FixtureHandler.requests_seen)
    finally:
        await session.close()


async def test_real_browser_checks_each_redirect_hop_before_same_host_port_change(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    forbidden = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    worker = threading.Thread(target=forbidden.serve_forever, daemon=True)
    worker.start()
    target = f"http://fixture.test:{forbidden.server_port}/forbidden"
    middle = fixture_site + "/redirect?" + urlencode({"to": target})
    entry = "/redirect?" + urlencode({"to": middle})
    try:
        executor = _executor(tmp_path, fixture_site)
        session = await executor.open_session(
            _plan(fixture_site, entry), session_id="redirect-chain", observation_only=True
        )
        try:
            snapshot = await session.snapshot()
            assert snapshot["blocked_navigation_origins"] == [
                f"http://fixture.test:{forbidden.server_port}"
            ]
        finally:
            await session.close()
        assert not any(path == "/forbidden" for _, path in _FixtureHandler.requests_seen)
    finally:
        forbidden.shutdown()
        forbidden.server_close()
        worker.join(timeout=2)


async def test_real_browser_manual_error_does_not_leak_input(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    session = await executor.open_session(
        _plan(fixture_site, "/login"), session_id="manual-errors", interactive=True
    )
    try:
        with pytest.raises(BrowserWorkflowExecutionError) as raised:
            await session.act(
                {"action": "fill", "selector": "button", "value": "test-password-secret"}
            )
        assert raised.value.code == "manual_action_failed"
        assert "test-password-secret" not in str(raised.value)
    finally:
        await session.close()


async def test_real_browser_blocks_embedded_pages_without_claiming_login_redirect(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    session = await executor.open_session(_plan(fixture_site, "/iframe"), session_id="iframe")
    try:
        snapshot = await session.snapshot()
        assert snapshot["unsupported_embedded_pages"]
        assert snapshot["blocked_navigation_origins"] == []
        assert not any(path == "/forbidden" for _, path in _FixtureHandler.requests_seen)
    finally:
        await session.close()


async def test_real_browser_snapshot_prioritizes_content_and_supplies_reusable_dom_relations(
    tmp_path: Path,
    fixture_site: str,
) -> None:
    executor = _executor(tmp_path, fixture_site)
    session = await executor.open_session(_plan(fixture_site, "/hierarchy"), session_id="hierarchy")
    try:
        snapshot = await session.snapshot()
        rows = snapshot["elements"]
        assert len(rows) <= 100
        assert any(row["tag"] == "input" for row in rows)
        heading = next(row for row in rows if row["tag"] == "h2" and row["text"] == "First")
        assert heading["parent_selector"] == "li"
        assert heading["next_sibling_selector"] == "span.meta"
        assert heading["ancestor_selectors"] == ["li", "ul.news", "#main-content"]
        assert heading["child_links"][0]["selector"] == "a"
        item = next(row for row in rows if row["tag"] == "li")
        assert item["path_selector"] == "#main-content > ul.news > li"
        link_selector = item["child_links"][0]["selector"]
        assert link_selector == "h2 > a"
        serialized = json.dumps(snapshot)
        assert "input-secret" not in serialized
        assert "query-secret" not in serialized
        assert "link-secret" not in serialized
        steps = [
            {
                "action": "collect",
                "item_selector": item["path_selector"],
                "link_selector": link_selector,
            }
        ]
    finally:
        await session.close()
    result = await executor.run(
        _plan(fixture_site, "/hierarchy", steps=steps), session_id="hierarchy"
    )
    assert result.status == "ok", result
    assert [item.title for item in result.items] == ["First", "Second"]
