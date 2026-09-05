"""Background worker process for OpenBiliClaw.

This package is the home for work that must not run in the API process:
pool maintenance, discovery/evaluation, LLM/embedding jobs, dialogue
settlement, and recommendation snapshot construction.

Phase 0 provides a standalone maintenance worker. Later phases will move
the remaining heavy background loops here and leave the API process with
only read/serve responsibilities.
"""

from __future__ import annotations

from .main import run_maintenance_worker

__all__ = ["run_maintenance_worker"]
