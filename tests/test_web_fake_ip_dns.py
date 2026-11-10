"""Exercise proxy fake-IP DNS at the real public-Web and Ego preflight seams."""

import socket
from unittest.mock import Mock

import httpx
import pytest

from openbiliclaw.sources import web_public
from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import EgoRequestPolicy


@pytest.fixture
def dns(monkeypatch):
    def setup(addresses, *, public="93.184.215.14", proxy=None, error=False):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
            ],
        )
        client_class = httpx.AsyncClient
        calls = []

        def reply(request):
            calls.append(request)
            assert request.url.host == "1.1.1.1" and request.url.path == "/dns-query"
            assert request.url.params["name"] == "books.example.com"
            assert "cookie" not in request.headers and "authorization" not in request.headers
            if error:
                raise httpx.ConnectError("unavailable", request=request)
            kind = int(request.url.params["type"])
            return httpx.Response(
                200,
                json={
                    "Status": 0,
                    "TC": False,
                    "Question": [{"name": "books.example.com", "type": kind}],
                    "Answer": [{"type": 1, "data": public}] if kind == 1 else [],
                },
            )

        def client(**kwargs):
            if "transport" in kwargs:
                return client_class(**kwargs)
            assert kwargs.pop("proxy") == proxy
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            return client_class(transport=httpx.MockTransport(reply), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        monkeypatch.setattr(
            "openbiliclaw.runtime.image_network.image_proxy_for_host", lambda *a, **k: proxy
        )
        return calls

    return setup


@pytest.mark.parametrize("proxy", [None, "http://127.0.0.1:7897"])
async def test_ego_preflight_accepts_verified_public_dns_behind_fake_ip(dns, proxy):
    calls = dns(["198.18.0.4", "198.19.255.254"], proxy=proxy)
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://books.example.com/",
            "allowed_origins": ["https://books.example.com"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    assert await EgoRequestPolicy().authorize(
        plan, {"url": plan.entry_url, "method": "GET", "document": True}, interactive=False
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["198.18.0.4", "10.0.0.1"],
        ["198.18.0.4", "93.184.215.14"],
    ],
)
async def test_private_or_mixed_local_answers_never_trigger_public_dns(dns, addresses):
    calls = dns(addresses)
    assert await web_public._system_resolver("books.example.com", 443) == addresses
    assert not calls


async def test_literal_fake_ip_is_never_resolved_as_a_public_hostname(dns):
    calls = dns(["198.18.0.4"])
    assert await web_public._system_resolver("198.18.0.4", 443) == ["198.18.0.4"]
    assert not calls


@pytest.mark.parametrize(
    "public,error", [("127.0.0.1", False), ("198.18.0.1", False), ("93.184.215.14", True)]
)
async def test_public_web_rejects_invalid_or_unavailable_fallback(dns, public, error):
    calls = dns(["198.18.0.4"], public=public, error=error)
    with pytest.raises((OSError, ValueError, httpx.HTTPError)):
        await web_public._system_resolver("books.example.com", 443)
    assert calls


async def test_http_fetch_pins_verified_addresses_instead_of_proxy_fake_ip(dns, monkeypatch):
    calls = dns(["198.18.0.4"])
    monkeypatch.setattr(web_public, "_web_proxy_for_origin", lambda _: None)
    async with web_public.SafeWebTransport(
        allowed_origins=["https://books.example.com"]
    ) as fetcher:
        fetcher._pinned_backend.pin = pin = Mock()
        await fetcher._validate_dns(
            web_public._normalize_recipe_origin("https://books.example.com")
        )
        pin.assert_called_once_with("books.example.com", 443, ("93.184.215.14",))
    assert len(calls) == 2
