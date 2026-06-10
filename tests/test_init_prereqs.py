"""Tests for InitPrereqs cached probes (gui-init plan C1)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openbiliclaw.runtime import init_prereqs
from openbiliclaw.runtime.init_prereqs import InitPrereqs


class _Provider:
    def __init__(self, ok: bool) -> None:
        self._ok = ok
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        return self._ok


def _ctx(
    *, provider: Any = None, cookie: str = "", platforms: dict[str, bool] | None = None
) -> Any:
    registry = SimpleNamespace(get=lambda: provider) if provider is not None else None
    platforms = platforms or {}
    sources = SimpleNamespace(
        **{
            name: SimpleNamespace(enabled=platforms.get(name, False))
            for name in init_prereqs._PLATFORM_SOURCE_FIELDS
        }
    )
    config = SimpleNamespace(
        bilibili=SimpleNamespace(cookie=cookie), sources=sources, data_path=None
    )
    return SimpleNamespace(llm_registry=registry, config=config)


async def test_chat_ready_true_and_cached() -> None:
    provider = _Provider(ok=True)
    pr = InitPrereqs(_ctx(provider=provider))
    assert await pr.chat_ready() is True
    assert await pr.chat_ready() is True  # cached
    assert provider.calls == 1  # single probe within TTL


async def test_chat_ready_false_when_provider_unhealthy() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=False)))
    assert await pr.chat_ready() is False


async def test_chat_ready_false_when_no_registry() -> None:
    pr = InitPrereqs(_ctx(provider=None))
    assert await pr.chat_ready() is False


async def test_bilibili_check_failed_without_cookie() -> None:
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie=""))
    assert await pr.bilibili_check() == "failed"


async def test_bilibili_check_ok_and_cached(monkeypatch: Any) -> None:
    calls = {"n": 0}

    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            calls["n"] += 1
            return SimpleNamespace(authenticated=True)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="sessdata=abc"))
    assert await pr.bilibili_check() == "ok"
    assert await pr.bilibili_check() == "ok"  # cached (60s success TTL)
    assert calls["n"] == 1


async def test_bilibili_check_failed_when_unauthenticated(monkeypatch: Any) -> None:
    class _FakeAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def validate_cookie(self, _cookie: str) -> Any:
            return SimpleNamespace(authenticated=False)

    monkeypatch.setattr(init_prereqs, "AuthManager", _FakeAuth)
    pr = InitPrereqs(_ctx(provider=_Provider(ok=True), cookie="bad"))
    assert await pr.bilibili_check() == "failed"


def test_enabled_platforms_reads_config() -> None:
    pr = InitPrereqs(_ctx(platforms={"bilibili": True, "douyin": True}))
    assert pr.enabled_platforms() == ["bilibili", "douyin"]
