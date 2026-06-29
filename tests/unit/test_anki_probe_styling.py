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
