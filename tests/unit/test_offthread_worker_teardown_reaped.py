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

from PyQt6.QtWidgets import QVBoxLayout, QWidget

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


def test_running_offthread_worker_survives_the_test_frame_dying(qtbot):
    """A screen held only by a test local must reach teardown, not die at return.

    The sibling above keeps its parent alive by accident: ``deleteLater()`` hands
    ownership to C++, so the widget outlives the frame. Drop that call and the
    shape is every GUI test in the suite — ``tab = SomeTab(config)``, a worker
    dispatched by a panel nested inside it, no explicit join. The local is
    released when the test function returns, inside ``pytest_pyfunc_call``:
    still the CALL phase, before any teardown hook, so the reaper below has not
    run and Qt aborts the process (``qFatal``) on the running QThread it
    destroys with the tree. ``pytest-qt`` tracks ``addWidget`` widgets by
    weakref, so it does not hold the widget either.

    The owner here is the nested panel, NOT the widget the test holds, because
    that is the arrangement that actually crashed CI: pinning the worker's own
    parent would not have saved it — only keeping the top-level widget alive
    does. ``conftest``'s ``pytest_configure`` patch is what does that. Remove it
    and this test kills the whole xdist worker.
    """
    started = threading.Event()

    def work(is_cancelled) -> None:
        started.set()
        while not is_cancelled():
            time.sleep(0.01)

    top = QWidget()
    qtbot.addWidget(top)
    panel = QWidget()
    QVBoxLayout(top).addWidget(panel)

    run_off_thread(panel, work, lambda _r: None, pass_cancel_check=True)
    assert started.wait(2.0), "worker never started"
    # No deleteLater, no explicit join: returning here is the whole test.
