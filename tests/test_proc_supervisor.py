"""ChildProcessSupervisor keeps the desktop backend children alive."""

from __future__ import annotations

from pathlib import Path

from openbiliclaw.proc import ChildProcessSupervisor, ManagedChild

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

    assert "ChildProcessSupervisor(children)" in source
    assert "child_supervisor.stop()" in source
