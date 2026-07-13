"""Shared synchronous InstallWorker runner for worker tests.

Deliberately synchronous: it calls ``worker.run()`` directly on the current
thread, bypassing ``QThread.start()``. Do NOT reintroduce start/wait/timeout —
real QThread scheduling here reintroduces the xdist QThread flakiness these
tests exist to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.gui.workers.install_worker import InstallWorker


def _run_worker_sync(worker: InstallWorker) -> None:
    """Run the worker's run() synchronously (bypass QThread.start)."""
    worker.run()
