"""``_drain_deferred_deletes``: the installer-smoke exit-path cleanup.

A real reproduction, not a theory: dropping the ``app.processEvents()`` call
or returning early the moment a pass delivers nothing both reliably
reintroduce the SIGSEGV that ``_drain_deferred_deletes`` exists to prevent
(``tests/unit/test_app_installer_smoke.py::
test_installer_smoke_failure_exits_nonzero_without_dialog`` is the end-to-end
pin for that, over a real subprocess). This file is the narrow unit-level pin
for the two things that ARE meant to change: the loop runs its full fixed
count unconditionally regardless of ``sendPostedEvents`` returning something
useful (it does not), and it is observable -- a DEBUG log names whether
deletes were still landing on the very last pass.

A fake ``app`` stands in for ``QApplication`` here: it accepts
``installEventFilter``/``removeEventFilter`` and, on ``sendPostedEvents``,
replays a caller-supplied number of ``DeferredDelete`` deliveries into every
installed filter -- exactly what ``_DeferredDeleteWatcher`` counts in
production, without needing real QObjects that regenerate their own deletes.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent

from anki_miner.gui.app import _drain_deferred_deletes


class _FakeApp:
    """Records calls and can simulate any per-pass DeferredDelete count."""

    def __init__(self, deletes_per_pass: list[int]) -> None:
        self._deletes_per_pass = list(deletes_per_pass)
        self._filters: list[object] = []
        self.send_calls = 0
        self.process_calls = 0

    def installEventFilter(self, obj: object) -> None:  # noqa: N802 - mirrors QObject's API
        self._filters.append(obj)

    def removeEventFilter(self, obj: object) -> None:  # noqa: N802 - mirrors QObject's API
        self._filters.remove(obj)

    def sendPostedEvents(self, receiver: object, event_type: int) -> None:  # noqa: N802 - mirrors QCoreApplication
        pass_index = self.send_calls
        self.send_calls += 1
        n = self._deletes_per_pass[pass_index] if pass_index < len(self._deletes_per_pass) else 0
        event = QEvent(QEvent.Type.DeferredDelete)
        for _ in range(n):
            for f in list(self._filters):
                f.eventFilter(None, event)

    def processEvents(self) -> None:  # noqa: N802 - mirrors QCoreApplication
        self.process_calls += 1


def test_loop_runs_the_full_fixed_pass_count_even_once_a_pass_delivers_nothing(caplog):
    """No early return: passes 2-8 must still run after pass 1 goes quiet.

    An early return here is exactly what reproduced the SIGSEGV on the
    installer-smoke failure path -- see the module docstring.
    """
    fake = _FakeApp([3, 0, 0, 0, 0, 0, 0, 0])

    _drain_deferred_deletes(fake, max_passes=8)  # type: ignore[arg-type]

    assert fake.send_calls == 8
    assert fake.process_calls == 8


def test_no_debug_log_when_the_queue_is_actually_empty_by_the_last_pass(caplog):
    fake = _FakeApp([3, 1, 0, 0, 0, 0, 0, 0])

    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.app"):
        _drain_deferred_deletes(fake, max_passes=8)  # type: ignore[arg-type]

    assert "still pending" not in caplog.text


def test_debug_log_names_the_cap_when_deletes_are_still_landing_on_the_last_pass(caplog):
    fake = _FakeApp([5] * 8)  # never goes quiet

    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.app"):
        _drain_deferred_deletes(fake, max_passes=8)  # type: ignore[arg-type]

    assert "still pending" in caplog.text
    assert "8" in caplog.text  # names the pass cap and/or the pending count


def test_the_event_filter_is_removed_even_after_hitting_the_cap():
    fake = _FakeApp([5] * 8)

    _drain_deferred_deletes(fake, max_passes=8)  # type: ignore[arg-type]

    assert fake._filters == []
