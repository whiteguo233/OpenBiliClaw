"""ChildProcessSupervisor keeps the desktop backend children alive."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.proc import (
    ChildProcessSupervisor,
    ManagedChild,
    backend_child_env,
    backend_config_generation,
)

_ENTRY_PATH = Path(__file__).resolve().parents[1] / "packaging" / "entry.py"


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return int(self.returncode or 0)


def _spawn_recorder() -> tuple[list[FakeProcess], object]:
    spawned: list[FakeProcess] = []

    def spawn() -> FakeProcess:
        process = FakeProcess(pid=1000 + len(spawned))
        spawned.append(process)
        return process

    return spawned, spawn


def test_supervisor_respawns_crashed_child_with_exponential_backoff() -> None:
    clock = [100.0]
    spawned, spawn = _spawn_recorder()
    child = ManagedChild("worker", spawn)
    supervisor = ChildProcessSupervisor([child], clock=lambda: clock[0])

    supervisor.check_once()
    assert child.process is spawned[0]

    spawned[0].returncode = 1
    clock[0] = 101.0
    supervisor.check_once()
    assert child.process is spawned[0]
    assert child.restart_at == 103.0  # first crash: base 2s

    clock[0] = 102.99
    supervisor.check_once()
    assert child.process is spawned[0]

    clock[0] = 103.0
    supervisor.check_once()
    assert child.process is spawned[1]

    spawned[1].returncode = 1
    clock[0] = 104.0
    supervisor.check_once()
    assert child.restart_at == 108.0  # immediate second crash doubles the delay


def test_supervisor_resets_backoff_after_a_healthy_lifetime() -> None:
    clock = [0.0]
    spawned, spawn = _spawn_recorder()
    child = ManagedChild("worker", spawn)
    supervisor = ChildProcessSupervisor(
        [child],
        clock=lambda: clock[0],
        healthy_after_seconds=10.0,
    )

    supervisor.check_once()
    spawned[0].returncode = 1
    clock[0] = 1.0
    supervisor.check_once()
    clock[0] = 3.0
    supervisor.check_once()
    assert child.process is spawned[1]

    spawned[1].returncode = 1
    clock[0] = 4.0
    supervisor.check_once()
    assert child.restart_at == 8.0  # failures=2 -> 4s
    clock[0] = 8.0
    supervisor.check_once()
    assert child.process is spawned[2]

    spawned[2].returncode = 1
    clock[0] = 100.0
    supervisor.check_once()
    assert child.restart_at == 102.0  # lived long enough: backoff reset to base


def test_supervisor_stop_terminates_children() -> None:
    spawned, spawn = _spawn_recorder()
    child = ManagedChild("worker", spawn)
    supervisor = ChildProcessSupervisor([child])

    supervisor.check_once()
    assert len(spawned) == 1

    supervisor.stop(terminate_timeout_seconds=0.1)

    assert child.process is None
    assert spawned[0].terminated is True
    assert spawned[0].killed is False


def test_supervisor_restarts_running_workers_after_applied_config_changes() -> None:
    """Repairing the API's model must also replace the evaluator's old model."""
    applied_generation = [0]
    model = ["broken-model"]
    spawned: list[FakeProcess] = []
    child_models: list[str] = []

    def spawn() -> FakeProcess:
        process = FakeProcess(pid=1000 + len(spawned))
        spawned.append(process)
        child_models.append(model[0])
        return process

    child = ManagedChild("discovery-worker", spawn)
    supervisor = ChildProcessSupervisor(
        [child], config_generation_provider=lambda: applied_generation[0]
    )
    supervisor.check_once()
    assert child_models == ["broken-model"]

    # A saved setting is only committed once the API's runtime handoff succeeds.
    model[0] = "working-model"
    applied_generation[0] += 1
    supervisor.check_once()

    assert child_models == ["broken-model", "working-model"]
    assert spawned[0].terminated
    assert child.process is spawned[1]
    supervisor.stop()


def test_config_restart_waits_for_the_apply_queue_to_settle() -> None:
    applying = [False]
    app = SimpleNamespace(
        state=SimpleNamespace(
            backend_config_generation=0,
            config_apply_task=SimpleNamespace(done=lambda: not applying[0]),
        )
    )
    spawned, spawn = _spawn_recorder()
    supervisor = ChildProcessSupervisor(
        [ManagedChild("discovery-worker", spawn)],
        config_generation_provider=lambda: backend_config_generation(app),
    )
    supervisor.check_once()
    applying[0] = True
    app.state.backend_config_generation = 1
    supervisor.check_once()
    assert len(spawned) == 1
    assert not spawned[0].terminated

    # Intermediate revisions coalesce; only the final committed config is read.
    app.state.backend_config_generation = 2
    applying[0] = False
    supervisor.check_once()
    supervisor.check_once()
    assert len(spawned) == 2
    assert spawned[0].terminated
    supervisor.stop()


def test_respawn_config_keeps_the_parents_active_data_directory(tmp_path, monkeypatch) -> None:
    from openbiliclaw.config import Config, load_config, save_config

    active = tmp_path / "active-data"
    pending = tmp_path / "pending-data"
    cfg = Config(data_dir=str(pending))
    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    app = SimpleNamespace(state=SimpleNamespace(active_runtime_data_path=active))
    env = backend_child_env(app, OPENBILICLAW_FULL_WORKER="1")
    assert env["OPENBILICLAW_FULL_WORKER"] == "1"
    monkeypatch.setenv("OPENBILICLAW_RUNTIME_DATA_DIR", env["OPENBILICLAW_RUNTIME_DATA_DIR"])
    assert load_config(config_path).data_path == active
    assert load_config(config_path, consult_environment=False).data_path == pending


def test_config_restart_does_not_spawn_while_the_old_owner_cannot_be_stopped() -> None:
    class StuckProcess(FakeProcess):
        def terminate(self) -> None:
            raise OSError("termination denied")

        def kill(self) -> None:
            raise OSError("kill denied")

    generation = [0]
    old = StuckProcess(pid=1)
    spawned: list[FakeProcess] = []

    def spawn() -> FakeProcess:
        spawned.append(old)
        return old

    child = ManagedChild("discovery-worker", spawn)
    supervisor = ChildProcessSupervisor([child], config_generation_provider=lambda: generation[0])
    supervisor.check_once()
    generation[0] += 1
    supervisor.check_once()
    supervisor.check_once()
    assert len(spawned) == 1
    assert child.process is old
    assert child.restart_requested
    old.returncode = 0
    supervisor.stop()


@pytest.mark.parametrize("worker_mode, child_count", [("1", 4), ("0", 1)])
def test_cli_wires_config_restarts_and_stops_every_child(
    tmp_path,
    monkeypatch,
    worker_mode: str,
    child_count: int,
) -> None:
    from openbiliclaw import cli, config, proc
    from openbiliclaw.api import app as api
    from openbiliclaw.runtime import api_server

    app = SimpleNamespace(
        state=SimpleNamespace(
            backend_config_generation=0,
            active_runtime_data_path=tmp_path / "active-data",
        )
    )
    supervisors: list[ChildProcessSupervisor] = []
    spawned: list[FakeProcess] = []

    class ManualSupervisor(ChildProcessSupervisor):
        def start(self) -> None:
            supervisors.append(self)
            self.check_once()

    def spawn(name: str, module: str, env: dict[str, str]) -> FakeProcess:
        assert env["OPENBILICLAW_RUNTIME_DATA_DIR"] == str(app.state.active_runtime_data_path)
        process = FakeProcess(pid=1000 + len(spawned))
        spawned.append(process)
        return process

    def serve(api_app: object, **kwargs: object) -> None:
        assert api_app is app
        assert len(spawned) == child_count
        app.state.backend_config_generation += 1
        supervisors[0].check_once()
        assert len(spawned) == child_count * 2
        assert all(p.terminated for p in spawned[:child_count])

    monkeypatch.setenv("OPENBILICLAW_WORKER", worker_mode)
    monkeypatch.setenv("OPENBILICLAW_FULL_WORKER", "0")
    monkeypatch.setenv("OPENBILICLAW_IMAGE_SERVICE_URL", "")
    monkeypatch.setattr(config, "load_config", lambda: config.Config(data_dir=str(tmp_path)))
    monkeypatch.setattr(api, "create_app", lambda: app)
    monkeypatch.setattr(proc, "ChildProcessSupervisor", ManualSupervisor)
    monkeypatch.setattr(cli, "_spawn_background_child", spawn)
    monkeypatch.setattr(cli, "_start_tailnet_runtime_best_effort", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "ensure_recommendation_transport_env", lambda path: "test")
    monkeypatch.setattr(api_server, "create_wildcard_listener_sockets", lambda *a: None)
    monkeypatch.setattr("uvicorn.run", serve)

    cli._run_api_server()

    assert all(p.terminated for p in spawned)


def test_supervisor_keeps_respawning_across_sweeps() -> None:
    clock = [0.0]
    spawned, spawn = _spawn_recorder()
    child = ManagedChild("worker", spawn)
    supervisor = ChildProcessSupervisor([child], clock=lambda: clock[0])

    supervisor.check_once()
    for expected_count in range(2, 6):
        child.process.returncode = 1  # type: ignore[union-attr]
        clock[0] += 1000.0
        supervisor.check_once()  # detect + schedule (long lifetime -> reset)
        clock[0] += 2.0
        supervisor.check_once()  # respawn
        assert len(spawned) == expected_count


def test_entry_worker_dispatch_converts_child_crashes_to_system_exit() -> None:
    """Issue #250: an uncaught child exception must not reach the bootloader."""
    source = _ENTRY_PATH.read_text(encoding="utf-8")
    dispatch = source.split("runpy.run_module(sys.argv[2]", 1)[1]
    guard = dispatch.split("raise SystemExit(0)", 1)[0]

    assert "except Exception:" in guard
    assert "SystemExit(1)" in guard


def test_entry_main_supervises_backend_children() -> None:
    source = _ENTRY_PATH.read_text(encoding="utf-8")

    assert "config_generation_provider=lambda: backend_config_generation(app)" in source
    assert "child_supervisor.stop()" in source
