"""Regression tests for SIGTERM child-process cleanup (orphan fix).

Root cause: uvicorn's ``Server.capture_signals`` restores the "original"
signal handler and re-raises the captured signal on shutdown. SIGINT
re-raised becomes KeyboardInterrupt and unwinds through ``finally``;
SIGTERM re-raised hits SIG_DFL and the process dies instantly, skipping
the ``finally`` that terminates the four background child processes —
they become PPID=1 orphans. The fix installs a SIGTERM handler that
raises ``SystemExit(143)`` before uvicorn starts.
"""

import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from openbiliclaw import cli as cli_module


def test_hook_raises_systemexit_with_conventional_code() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_module._raise_systemexit_on_signal(signal.SIGTERM, None)
    assert exc_info.value.code == 143


def test_install_hook_replaces_and_restores_sigterm_handler() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    restore = cli_module._install_sigterm_cleanup_hook()
    try:
        assert signal.getsignal(signal.SIGTERM) is cli_module._raise_systemexit_on_signal
    finally:
        restore()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_install_hook_is_noop_off_main_thread() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    results: list[object] = []

    def _worker() -> None:
        restore = cli_module._install_sigterm_cleanup_hook()
        results.append(restore)
        restore()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(results) == 1
    assert callable(results[0])
    assert signal.getsignal(signal.SIGTERM) == previous


def test_sigterm_reraise_unwinds_through_finally(tmp_path: Path) -> None:
    """End-to-end: mimic uvicorn's restore+raise_signal(SIGTERM) shutdown.

    Without the hook the re-raised SIGTERM would kill the process before
    the finally runs; with it, the finally writes a marker file and the
    process exits 143.
    """
    marker = tmp_path / "cleaned"
    script = (
        "import signal, sys\n"
        "from openbiliclaw.cli import _install_sigterm_cleanup_hook\n"
        "restore = _install_sigterm_cleanup_hook()\n"
        "try:\n"
        "    # 模拟 uvicorn shutdown:恢复它保存的原始处理器并重发 SIGTERM\n"
        "    signal.raise_signal(signal.SIGTERM)\n"
        "finally:\n"
        "    restore()\n"
        "    open(sys.argv[1], 'w').write('cleaned')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(marker)],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 143, result.stderr.decode(errors="replace")
    assert marker.read_text() == "cleaned"
