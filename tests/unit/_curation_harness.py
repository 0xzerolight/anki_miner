"""The curation-gate park recipe, shared by the MiningTabBase curation tests.

A real thread calls ``MiningTabBase._curation_bridge``, which emits
``_curation_requested`` and parks in ``_curation_event.wait()``. The tests
here exercise what releases it (accept, reject, poison, teardown, shutdown),
so they need that thread genuinely parked. Real threads on purpose: the
synchronous ``_worker_sync`` harness cannot park anything.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtTest import QTest

#: ``CurationWorker.result`` before the bridge returns, so ``result is None``
#: always means the bridge itself returned None (cancelled/rejected).
UNSET = "<unset>"

# No signal marks "parked in _curation_event.wait()": the emit is the bridge's
# last step before it, so the thread gets this long to take that step.
_PARK_SETTLE_S = 0.05


class CurationWorker(QThread):
    """Runs ``_curation_bridge`` off the GUI thread, exactly like a mining worker."""

    def __init__(self, tab, words) -> None:
        super().__init__()
        self._tab = tab
        self._words = words
        self.thread_obj: QThread | None = None
        self.result: object = UNSET

    def run(self) -> None:
        self.thread_obj = QThread.currentThread()
        self.result = self._tab._curation_bridge(self._words)


def drain_until(predicate: Callable[[], object], timeout_ms: int = 3000, step_ms: int = 10) -> bool:
    """Spin the GUI event loop (delivering queued signals) until ``predicate`` or timeout."""
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return bool(predicate())


def settle_into_gate() -> None:
    """Give a thread that just emitted the curation request time to park."""
    time.sleep(_PARK_SETTLE_S)


def park_at_gate(tab, start: Callable[[], None]) -> None:
    """Call ``start`` and return once the bridge thread it launches is parked.

    The probe is a DirectConnection, so it runs on the bridge thread at emit
    time and needs no event-loop spin -- spinning would deliver the queued
    ``_on_curation_requested`` slot and defeat every "nobody answered" case.
    """
    reached_gate = threading.Event()
    tab._curation_requested.connect(lambda _words: reached_gate.set(), Qt.ConnectionType.DirectConnection)
    start()
    assert reached_gate.wait(2.0), "the bridge never emitted the curation request"
    settle_into_gate()


def park_worker_at_gate(tab, words: Iterable[str] = ("w1",)) -> CurationWorker:
    """Start a ``CurationWorker`` and return it parked at the curation gate."""
    worker = CurationWorker(tab, list(words))
    park_at_gate(tab, worker.start)
    assert not worker.isFinished(), "worker should be parked at the curation gate"
    return worker
