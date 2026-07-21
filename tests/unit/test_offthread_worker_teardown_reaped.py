"""Regression: a ``run_off_thread`` worker still running when its parent widget
is destroyed at teardown must be cancelled + joined first, or Qt aborts the
process (``Fatal Python error: Aborted`` — QThread destroyed while running).

Before the fix this aborted the whole ``--dist loadfile`` xdist worker and,
under ``--max-worker-restart=0``, reddened CI — the crash surfacing in a later,
innocent file on the same worker (observed victims:
``test_subtitles_settings_panel``, ``test_condense_tab``). The
``pytest_runtest_teardown`` hookwrapper in ``conftest.py`` reaps every live
``run_off_thread`` worker (via the production ``join_all_off_thread_workers``)
at the very start of teardown — before ``_drain_qt_deletes`` destroys the
deleted widget — so no worker thread is alive when Qt tears the objects down.

Without that reaper this test aborts the worker; with it, the worker is joined
and the test passes.
"""

from __future__ import annotations

import threading
import time

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.utils.run_off_thread import run_off_thread


def test_running_offthread_worker_reaped_before_delete_drain(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)

    started = threading.Event()

    def work(is_cancelled) -> None:
        # Cooperative: run until the reaper's cancel() flips the predicate so the
        # bounded join in the teardown hook completes deterministically.
        started.set()
        while not is_cancelled():
            time.sleep(0.01)

    run_off_thread(parent, work, lambda _r: None, pass_cancel_check=True)
    assert started.wait(2.0), "worker never started"

    # Schedule the parent (and its still-running child worker) for destruction.
    # _drain_qt_deletes will run the deferred delete at teardown; the reaper hook
    # must join the worker before that, or Qt aborts on a running QThread.
    parent.deleteLater()
