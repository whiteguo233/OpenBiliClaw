"""Tests for shared Ollama runtime supervision helpers."""

import os

import httpx
import pytest

from openbiliclaw.config import Config


def test_ollama_required_detects_chat_and_embedding_routes() -> None:
    from openbiliclaw.runtime.ollama_supervisor import ollama_required

    cfg = Config()
    assert ollama_required(cfg) is False

    cfg.llm.default_provider = "ollama"
    # Naming Ollama as a route does not invent a chat model.
    assert ollama_required(cfg) is False
    cfg.llm.ollama.model = "qwen2.5:7b"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.fallback_provider = " ollama "
    cfg.llm.ollama.model = "qwen2.5:7b"
    assert ollama_required(cfg) is True

    cfg = Config()
    cfg.llm.discovery.provider = "OLLAMA"
    cfg.llm.ollama.model = "qwen2.5:7b"
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
    cfg.llm.ollama.model = "qwen2.5:7b"
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
    cfg.llm.ollama.model = "qwen2.5:7b"
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

    monkeypatch.setattr(sup, "_managed_daemon", None)
    assert sup.stop_managed_ollama() is False


def test_stop_managed_ollama_skips_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    class _Dead:
        pid = 1

        def poll(self) -> int:
            return 0  # already exited

    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(_Dead(), "http://localhost:11434", None)
    )
    assert sup.stop_managed_ollama() is False
    assert sup._managed_daemon is None  # record cleared


def test_stop_managed_ollama_closes_log_file_even_when_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    class _Dead:
        pid = 1

        def poll(self) -> int:
            return 0  # already exited

    class _Log:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    log_file = _Log()
    monkeypatch.setattr(
        sup,
        "_managed_daemon",
        sup._ManagedDaemon(_Dead(), "http://localhost:11434", None, log_file),
    )

    assert sup.stop_managed_ollama() is False
    assert log_file.closed is True
    assert sup._managed_daemon is None


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
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(proc, "http://localhost:11434", None)
    )
    monkeypatch.setattr(sup.os, "name", "posix")
    monkeypatch.setattr(sup.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sup.os, "killpg", lambda pgid, sig: killed.update(pgid=pgid, sig=sig))

    assert sup.stop_managed_ollama() is True
    assert killed["pgid"] == 4321
    assert proc.waited is True
    # Idempotent: record cleared, a second call does nothing.
    assert sup._managed_daemon is None
    assert sup.stop_managed_ollama() is False


def test_start_serve_records_managed_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon we spawn is recorded so it can be stopped cleanly on exit."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
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
    assert sup._managed_daemon is not None
    assert sup._managed_daemon.proc is not None
    assert sup._managed_daemon.proc.pid == 999
    # (a) default start records the default endpoint spec.
    assert sup._managed_daemon.base_url == "http://127.0.0.1:11434"


def test_start_serve_reports_starting_and_ready_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.runtime import embedding_progress
    from openbiliclaw.runtime import ollama_supervisor as sup

    phases: list[str] = []
    monkeypatch.setattr(embedding_progress, "report_ollama_phase", phases.append)
    monkeypatch.setattr(sup, "_managed_daemon", None)
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
    assert phases == ["starting", "ready"]


def test_start_serve_reports_down_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.runtime import embedding_progress
    from openbiliclaw.runtime import ollama_supervisor as sup

    phases: list[str] = []
    monkeypatch.setattr(embedding_progress, "report_ollama_phase", phases.append)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert sup._ollama_start_serve_background() is False
    assert phases == ["starting", "down"]


def test_start_serve_sets_default_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Managed Ollama keeps bge-m3/llama-server warm across UI poll gaps."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
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


def test_managed_models_dir_uses_existing_relocation_marker(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    marker = tmp_path / "ollama-models"
    monkeypatch.setattr(sup, "ollama_models_relocation_candidate", lambda: str(marker))

    assert sup.managed_models_dir() is None
    marker.mkdir()
    assert sup.managed_models_dir() == str(marker)


def test_start_serve_sets_managed_ollama_models_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    models_dir = tmp_path / "ollama-models"
    models_dir.mkdir()
    monkeypatch.setattr(sup, "ollama_models_relocation_candidate", lambda: str(models_dir))
    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
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
    assert env["OLLAMA_MODELS"] == str(models_dir)


def test_start_serve_preserves_explicit_ollama_models_env(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    models_dir = tmp_path / "ollama-models"
    explicit_dir = tmp_path / "explicit-models"
    models_dir.mkdir()
    monkeypatch.setattr(sup, "ollama_models_relocation_candidate", lambda: str(models_dir))
    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setenv("OLLAMA_MODELS", str(explicit_dir))
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
    assert env["OLLAMA_MODELS"] == str(explicit_dir)


def test_start_serve_does_not_record_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopting an already-running Ollama leaves the handle None → stop won't
    kill it."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)

    assert sup._ollama_start_serve_background() is True
    assert sup._managed_daemon is None


def test_restart_managed_ollama_refuses_foreign_daemon(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime import ollama_supervisor as sup

    models_dir = tmp_path / "ollama-models"
    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)

    ok, reason = sup.restart_managed_ollama_with_models_dir(str(models_dir))

    assert ok is False
    assert reason == "external_ollama"
    # A refused attempt must NOT leave the migration-marker dir behind: its mere
    # existence would make a later managed start point OLLAMA_MODELS at a
    # modeless dir (managed_models_dir uses existence as the marker).
    assert not models_dir.exists()


def test_cli_keeps_ollama_re_exports() -> None:
    from openbiliclaw import cli as cli_module
    from openbiliclaw.runtime import ollama_supervisor

    assert cli_module._ollama_is_running is ollama_supervisor._ollama_is_running
    assert (
        cli_module._ollama_start_serve_background
        is ollama_supervisor._ollama_start_serve_background
    )


# --- Task 0: managed-daemon spec, endpoint predicate, restart routing, env hardening ---


class _RecordingPopen:
    """Fake Popen capturing constructor kwargs (env) for env-inspection tests."""

    instances: list[dict[str, object]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.pid = 4242
        type(self).instances.append(kwargs)

    def poll(self) -> None:
        return None


def _patch_ollama_binary(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    _RecordingPopen.instances = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr("subprocess.Popen", _RecordingPopen)
    return _RecordingPopen.instances


def test_start_managed_at_records_private_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) start_managed_ollama_at records (proc, base_url, abspath(models_dir))."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    health = iter([False, True])  # guard: down; loop: up
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    _patch_ollama_binary(monkeypatch)

    assert sup.start_managed_ollama_at("/tmp/priv-models", "127.0.0.1:11435") is True
    rec = sup._managed_daemon
    assert rec is not None
    assert rec.proc is not None
    assert rec.proc.pid == 4242
    assert rec.base_url == "http://127.0.0.1:11435"
    assert rec.models_dir == os.path.abspath("/tmp/priv-models")


def test_start_managed_at_adoption_records_none_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) the early-return adoption branch records (None, base_url, abspath(dir))."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)  # already up

    assert sup.start_managed_ollama_at("/tmp/priv-models", "127.0.0.1:11435") is True
    rec = sup._managed_daemon
    assert rec is not None
    assert rec.proc is None  # adopted — recorded but not signalable
    assert rec.base_url == "http://127.0.0.1:11435"
    assert rec.models_dir == os.path.abspath("/tmp/priv-models")
    assert sup.is_managed_endpoint("http://127.0.0.1:11435") is True


def test_is_managed_endpoint_normalizes_and_false_without_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) host/scheme normalized; False when no record exists."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    assert sup.is_managed_endpoint("http://127.0.0.1:11435") is False

    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://localhost:11435", "/tmp/m")
    )
    assert sup.is_managed_endpoint("http://127.0.0.1:11435") is True  # localhost ≡ 127.0.0.1
    assert sup.is_managed_endpoint("http://127.0.0.1:11435/v1") is True  # /v1 path ignored
    assert sup.is_managed_endpoint("http://127.0.0.1:11434") is False  # wrong port


def test_restart_private_record_relaunches_via_private_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) a private record restarts via the private path with hard-set env, never 11434."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/priv")
    )
    # refusal probe: dead; start guard: dead; health loop: up
    probes = iter([False, False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(probes))
    calls = _patch_ollama_binary(monkeypatch)

    ok, reason = sup.restart_managed_ollama()
    assert ok is True
    assert reason == ""
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_MODELS"] == os.path.abspath("/tmp/priv")
    assert env["OLLAMA_KEEP_ALIVE"] == "24h"
    assert env["OLLAMA_HOST"] != "127.0.0.1:11434"


def test_restart_default_record_uses_default_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """(e) a default record keeps today's default-daemon behavior (no private host)."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://localhost:11434", None)
    )
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    probes = iter([False, False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(probes))
    calls = _patch_ollama_binary(monkeypatch)

    ok, reason = sup.restart_managed_ollama()
    assert ok is True
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert "OLLAMA_HOST" not in env  # default path never binds a private host


def test_restart_refuses_external_and_adopted_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """(f) refusal probes the recorded endpoint; external / adopted-alive kill nothing."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    # No record + something answering the default endpoint → external_ollama.
    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)
    assert sup.restart_managed_ollama() == (False, "external_ollama")

    # Adopted private daemon (proc=None) still alive → adopted_alive; probes recorded url.
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    seen: list[object] = []

    def _probe(base: object = None, *a: object, **k: object) -> bool:
        seen.append(base)
        return True

    monkeypatch.setattr(sup, "_ollama_is_running", _probe)
    assert sup.restart_managed_ollama() == (False, "adopted_alive")
    assert seen == ["http://127.0.0.1:11435"]  # recorded endpoint, not hardcoded 11434


def test_restart_with_models_dir_refuses_private_record(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(g) the models-dir migration tool refuses a private daemon record."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    models_dir = tmp_path / "new-models"
    ok, reason = sup.restart_managed_ollama_with_models_dir(str(models_dir))
    assert ok is False
    assert reason == "private_daemon"
    assert not models_dir.exists()  # refused before creating the marker dir


def test_start_managed_at_hard_sets_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """(D5) the private daemon hard-sets OLLAMA_KEEP_ALIVE, overriding a user tweak."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "0")  # user RAM-saving tweak
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    calls = _patch_ollama_binary(monkeypatch)

    assert sup.start_managed_ollama_at("/tmp/m", "127.0.0.1:11435") is True
    assert calls[0]["env"]["OLLAMA_KEEP_ALIVE"] == "24h"


def test_managed_ollama_log_capture_requires_project_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Packaged starts log ollama serve + llama-server stderr instead of DEVNULL."""
    import subprocess

    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    calls = _patch_ollama_binary(monkeypatch)

    assert sup.start_managed_ollama_at("/tmp/priv-models", "127.0.0.1:11435") is True
    kwargs = calls[0]
    assert kwargs["stdout"] is not None
    assert kwargs["stdout"] is not subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert sup._managed_daemon is not None
    assert sup._managed_daemon.log_file is not None
    assert (tmp_path / "logs" / "ollama-managed.log").exists()


def test_managed_ollama_keeps_devnull_without_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without OPENBILICLAW_PROJECT_ROOT (dev/CLI/tests) behaviour stays DEVNULL."""
    import subprocess

    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    calls = _patch_ollama_binary(monkeypatch)

    assert sup.start_managed_ollama_at("/tmp/priv-models", "127.0.0.1:11435") is True
    kwargs = calls[0]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_default_path_keep_alive_respects_user_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """(i) the default path keeps setdefault — a deliberate user setting wins."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    monkeypatch.setattr(sup, "_managed_daemon", None)
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "0")
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    calls = _patch_ollama_binary(monkeypatch)

    assert sup._ollama_start_serve_background() is True
    assert calls[0]["env"]["OLLAMA_KEEP_ALIVE"] == "0"


# --- Task 2: watchdog with backoff (fake clock/probe — no real sleeps) ---


def _reset_watchdog_state(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Zero the watchdog counters and capture backoff sleeps."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    sleeps: list[float] = []
    monkeypatch.setattr(sup, "_watchdog_failures", 0)
    monkeypatch.setattr(sup, "_watchdog_gave_up", False)
    monkeypatch.setattr(sup, "_restart_in_progress", False)
    monkeypatch.setattr(sup, "_watchdog_sleep", sleeps.append)
    return sleeps


def test_watchdog_healthy_probe_no_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) healthy probe → no restart, failure counter stays 0."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: True)
    restarts: list[str] = []
    monkeypatch.setattr(sup, "restart_managed_ollama", lambda: restarts.append("r") or (True, ""))

    sup._watchdog_tick()
    assert restarts == []
    assert sup._watchdog_failures == 0


def test_watchdog_dead_daemon_restarts_with_recorded_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) owned proc exited + probe dead → restart routing invoked once with the spec."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    class _Dead:
        pid = 7

        def poll(self) -> int:
            return 1  # exited

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(
        sup,
        "_managed_daemon",
        sup._ManagedDaemon(_Dead(), "http://127.0.0.1:11435", "/tmp/priv"),
    )
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: False)
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: False)
    started: list[tuple[str, str]] = []

    def _fake_start_at(models_dir: str, host: str) -> bool:
        started.append((models_dir, host))
        return True

    monkeypatch.setattr(sup, "start_managed_ollama_at", _fake_start_at)

    sup._watchdog_tick()
    assert started == [("/tmp/priv", "http://127.0.0.1:11435")]
    assert sup._watchdog_failures == 0


def test_watchdog_backoff_sequence_and_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) restart failures back off 5,10,20,40,80 then give up with phase down."""
    from openbiliclaw.runtime import embedding_progress
    from openbiliclaw.runtime import ollama_supervisor as sup

    sleeps = _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: False)
    restarts: list[str] = []
    monkeypatch.setattr(
        sup,
        "restart_managed_ollama",
        lambda: restarts.append("r") or (False, "start_failed"),
    )
    phases: list[str] = []
    monkeypatch.setattr(embedding_progress, "report_ollama_phase", phases.append)

    for _ in range(6):
        sup._watchdog_tick()

    assert sleeps == [5.0, 10.0, 20.0, 40.0, 80.0]
    assert len(restarts) == 5  # 6th tick attempts nothing — gave up
    assert sup._watchdog_gave_up is True
    assert "down" in phases


def test_watchdog_healthy_probe_resets_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) a healthy probe after failures resets the backoff counter."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(sup, "_watchdog_failures", 3)
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: True)

    sup._watchdog_tick()
    assert sup._watchdog_failures == 0


def test_watchdog_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """(e) start_ollama_watchdog spawns exactly one daemon thread."""
    import threading

    from openbiliclaw.runtime import ollama_supervisor as sup

    sup.start_ollama_watchdog()
    first = sup._watchdog_thread
    sup.start_ollama_watchdog()
    assert sup._watchdog_thread is first
    alive = [t for t in threading.enumerate() if t.name == "obc-ollama-watchdog" and t.is_alive()]
    assert len(alive) == 1
    assert alive[0].daemon is True


def test_reset_watchdog_backoff_reenables_after_give_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(f) reset_watchdog_backoff() re-enables attempts after give-up."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(sup, "_watchdog_failures", 5)
    monkeypatch.setattr(sup, "_watchdog_gave_up", True)
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/m")
    )
    restarts: list[str] = []
    monkeypatch.setattr(sup, "restart_managed_ollama", lambda: restarts.append("r") or (True, ""))
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: False)

    sup._watchdog_tick()
    assert restarts == []  # gave up: no attempts

    sup.reset_watchdog_backoff()
    assert sup._watchdog_gave_up is False
    assert sup._watchdog_failures == 0
    sup._watchdog_tick()
    assert restarts == ["r"]  # attempts re-enabled


def test_watchdog_idles_without_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """(g) no managed record → the loop idles without probing."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(sup, "_managed_daemon", None)
    probes: list[str] = []
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: probes.append(url) or True)

    sup._watchdog_tick()
    assert probes == []


def test_successful_starts_arm_watchdog_and_reset_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both start paths arm the watchdog and clear backoff on success."""
    from openbiliclaw.runtime import ollama_supervisor as sup

    _reset_watchdog_state(monkeypatch)
    monkeypatch.setattr(sup, "_watchdog_failures", 4)
    monkeypatch.setattr(sup, "_watchdog_gave_up", True)
    armed: list[str] = []
    monkeypatch.setattr(sup, "start_ollama_watchdog", lambda *a, **k: armed.append("armed"))
    monkeypatch.setattr(sup, "_managed_daemon", None)
    health = iter([False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(health))
    _patch_ollama_binary(monkeypatch)

    assert sup._ollama_start_serve_background() is True
    assert armed == ["armed"]
    assert sup._watchdog_failures == 0
    assert sup._watchdog_gave_up is False

    # Private path, including its adoption branch, also arms.
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: True)
    assert sup.start_managed_ollama_at("/tmp/m", "127.0.0.1:11435") is True
    assert armed == ["armed", "armed"]
