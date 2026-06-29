"""Tests for AnkiProbeController's card-styling auto-sync (Issue #44).

The controller is now a thin reconciler: ``sync_styling`` applies the managed
block when ``manage_card_styling`` is set and strips it otherwise, with a
``_resync_pending`` self-heal so a sync requested while a write is in flight is
re-fired. Exercised directly with a mocked panel and stubbed worker spawning so
no real threads or AnkiConnect calls are involved.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget

from anki_miner.config import create_default_config
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController


def _make(*, manage=True, note_type="NT"):
    """Build a controller with a mocked panel."""
    panel = MagicMock()
    panel.get_note_type.return_value = note_type
    panel.get_ankiconnect_url.return_value = ""
    cfg = replace(create_default_config(), manage_card_styling=manage)
    ctrl = AnkiProbeController(
        parent=MagicMock(spec=QWidget),
        anki_panel=panel,
        filtering_panel=MagicMock(),
        get_config=lambda: cfg,
    )
    return ctrl, panel


# --- sync_styling dispatch -----------------------------------------------------


def test_sync_applies_when_managing():
    ctrl, _panel = _make(manage=True)
    ctrl._start_styling_write = MagicMock()  # type: ignore[method-assign]
    ctrl.sync_styling()
    ctrl._start_styling_write.assert_called_once_with("apply")


def test_sync_removes_when_not_managing():
    ctrl, _panel = _make(manage=False)
    ctrl._start_styling_write = MagicMock()  # type: ignore[method-assign]
    ctrl.sync_styling()
    ctrl._start_styling_write.assert_called_once_with("remove")


# --- dropped-sync self-heal (_resync_pending) ----------------------------------


def test_start_write_sets_pending_when_busy():
    ctrl, _panel = _make()
    ctrl._styling_worker = MagicMock()
    ctrl._styling_worker.isRunning.return_value = True
    ctrl._start_styling_write("apply")
    assert ctrl._resync_pending is True


def test_finished_flushes_pending_resync():
    ctrl, panel = _make(manage=True)
    ctrl.sync_styling = MagicMock()  # type: ignore[method-assign]
    ctrl._resync_pending = True
    ctrl._on_styling_finished("done")
    panel.set_styling_status.assert_called_once()  # live status
    assert ctrl._resync_pending is False
    ctrl.sync_styling.assert_called_once()


def test_finished_no_resync_when_not_pending():
    ctrl, _panel = _make()
    ctrl.sync_styling = MagicMock()  # type: ignore[method-assign]
    ctrl._resync_pending = False
    ctrl._on_styling_finished("done")
    ctrl.sync_styling.assert_not_called()


def test_error_flushes_pending_resync_and_clears_to_avoid_loop():
    ctrl, panel = _make()
    ctrl.sync_styling = MagicMock()  # type: ignore[method-assign]
    ctrl._resync_pending = True
    ctrl._on_styling_error("Anki down")
    # Status set to pending (None), flag cleared before re-fire (no loop).
    assert panel.set_styling_status.call_args.args[0] is None
    assert ctrl._resync_pending is False
    ctrl.sync_styling.assert_called_once()


def test_error_no_resync_when_not_pending():
    ctrl, _panel = _make()
    ctrl.sync_styling = MagicMock()  # type: ignore[method-assign]
    ctrl._resync_pending = False
    ctrl._on_styling_error("Anki down")
    ctrl.sync_styling.assert_not_called()


# --- status text ---------------------------------------------------------------


def test_live_status_text_distinguishes_managed_from_off():
    ctrl_on, _ = _make(manage=True)
    ctrl_off, _ = _make(manage=False)
    assert "live" in ctrl_on._live_status_text().lower()
    assert "Off" in ctrl_off._live_status_text()


# --- queued signal after panel teardown (CI teardown-flake regression) ---------
#
# A StylingWorker / fetch worker emits its completion signal cross-thread, so it
# can be delivered *after* the target panel's C++ object is destroyed (a tab
# closed mid-probe, or pytest's _drain_qt_deletes freeing the widget tree before
# the worker emits). Touching the dead wrapper raised
# ``RuntimeError: wrapped C/C++ object of type QLabel has been deleted`` inside
# the Qt event loop — surfacing as a teardown ERROR on whichever test owned the
# boundary. The slots now guard with sip.isdeleted and no-op.


def test_styling_slots_noop_after_panel_deleted(qtbot):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication, QWidget

    class _Panel(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list = []

        def set_styling_status(self, *a, **kw) -> None:
            # Touch the C++ object as the real panel does (it writes a child
            # QLabel) so an unguarded call on a deleted wrapper raises.
            self.isEnabled()
            self.calls.append((a, kw))

    panel = _Panel()
    qtbot.addWidget(panel)
    cfg = create_default_config()
    ctrl = AnkiProbeController(
        parent=panel,
        anki_panel=panel,
        filtering_panel=MagicMock(),
        get_config=lambda: cfg,
    )

    # Destroy the panel's C++ object exactly as _drain_qt_deletes does.
    panel.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert sip.isdeleted(panel)

    # Queued completion slots land after deletion: must no-op, not raise.
    ctrl._on_styling_error("Anki down")
    ctrl._on_styling_finished("done")


def test_fetch_decks_slots_noop_after_panel_deleted(qtbot):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication, QWidget

    filtering = QWidget()
    qtbot.addWidget(filtering)
    cfg = create_default_config()
    ctrl = AnkiProbeController(
        parent=MagicMock(spec=QWidget),
        anki_panel=MagicMock(),
        filtering_panel=filtering,
        get_config=lambda: cfg,
    )

    filtering.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert sip.isdeleted(filtering)

    ctrl._on_fetch_decks_finished(["Default"])
    ctrl._on_fetch_decks_error("Anki down")
