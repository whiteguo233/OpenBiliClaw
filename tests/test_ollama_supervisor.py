"""Tests for shared Ollama runtime supervision helpers."""

import httpx
import pytest

from openbiliclaw.config import Config


def test_ollama_required_detects_chat_and_embedding_routes() -> None:
    from openbiliclaw.runtime.ollama_supervisor import ollama_required

    cfg = Config()
    assert ollama_required(cfg) is False

    cfg.llm.default_provider = "ollama"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.fallback_provider = " ollama "
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.discovery.provider = "OLLAMA"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.embedding.provider = "ollama"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.embedding.fallback_provider = "ollama"
    assert ollama_required(cfg) is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.20:11434", False),
        ("https://ollama.example.com", False),
    ],
)
def test_is_loopback(url: str, expected: bool) -> None:
    from openbiliclaw.runtime.ollama_supervisor import is_loopback

    assert is_loopback(url) is expected


def test_effective_ollama_endpoint_strips_v1_suffix_for_chat() -> None:
    from openbiliclaw.runtime.ollama_supervisor import effective_ollama_endpoint

    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.base_url = "http://localhost:11434/v1/"

    assert effective_ollama_endpoint(cfg) == "http://localhost:11434"


def test_effective_ollama_endpoint_uses_embedding_base_url() -> None:
    from openbiliclaw.runtime.ollama_supervisor import effective_ollama_endpoint

    cfg = Config()
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.base_url = "http://127.0.0.1:11434/v1/"

    assert effective_ollama_endpoint(cfg) == "http://127.0.0.1:11434"


def test_ollama_probe_uses_root_api_version_after_v1_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime.ollama_supervisor import (
        _ollama_is_running,
        effective_ollama_endpoint,
    )

    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.base_url = "http://localhost:11434/v1"
    endpoint = effective_ollama_endpoint(cfg)
    seen_urls: list[str] = []

    class _FakeResp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            seen_urls.append(url)
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    assert _ollama_is_running(host=endpoint) is True
    assert seen_urls == ["http://localhost:11434/api/version"]


def test_stop_managed_ollama_noop_when_nothing_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no daemon we started (None handle = adopted external Ollama), stop
    is a no-op so a user-managed Ollama is never killed."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_proc", None)
    assert sup.stop_managed_ollama() is False


def test_stop_managed_ollama_skips_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    class _Dead:
        pid = 1

        def poll(self) -> int:
            return 0  # already exited

    monkeypatch.setattr(sup, "_managed_proc", _Dead())
    assert sup.stop_managed_ollama() is False
    assert sup._managed_proc is None  # handle cleared


def test_stop_managed_ollama_signals_process_group_unix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    class _Alive:
        pid = 4321

        def __init__(self) -> None:
            self.waited = False

        def poll(self) -> None:
            return None  # still running

        def wait(self, timeout: float | None = None) -> None:
            self.waited = True

    proc = _Alive()
    killed: dict[str, int] = {}
    monkeypatch.setattr(sup, "_managed_proc", proc)
    monkeypatch.setattr(sup.os, "name", "posix")
    monkeypatch.setattr(sup.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sup.os, "killpg", lambda pgid, sig: killed.update(pgid=pgid, sig=sig))

    assert sup.stop_managed_ollama() is True
    assert killed["pgid"] == 4321
    assert proc.waited is True
    # Idempotent: handle cleared, a second call does nothing.
    assert sup._managed_proc is None
    assert sup.stop_managed_ollama() is False


def test_start_serve_records_managed_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon we spawn is recorded so it can be stopped cleanly on exit."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_proc", None)
    # Guard probe: not running yet; health loop: up right after spawn.
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pid = 999

        def poll(self) -> None:
            return None

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    assert sup._ollama_start_serve_background() is True
    assert sup._managed_proc is not None
    assert sup._managed_proc.pid == 999


def test_start_serve_sets_default_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Managed Ollama keeps bge-m3/llama-server warm across UI poll gaps."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_proc", None)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))

    calls: list[dict[str, object]] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pid = 999
            calls.append(kwargs)

        def poll(self) -> None:
            return None

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    assert sup._ollama_start_serve_background() is True
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["OLLAMA_KEEP_ALIVE"] == "24h"


def test_start_serve_does_not_record_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopting an already-running Ollama leaves the handle None → stop won't
    kill it."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_proc", None)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)

    assert sup._ollama_start_serve_background() is True
    assert sup._managed_proc is None


def test_cli_keeps_ollama_re_exports() -> None:
    from openbiliclaw import cli as cli_module
    from openbiliclaw.runtime import ollama_supervisor

    assert cli_module._ollama_is_running is ollama_supervisor._ollama_is_running
    assert (
        cli_module._ollama_start_serve_background
        is ollama_supervisor._ollama_start_serve_background
    )
