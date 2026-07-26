"""AnkiProbeController.refresh_name_lists feeds the deck / note-type combos.

Uses a REAL AnkiSettingsPanel rather than a MagicMock: the point of these
tests is that a combo keeps its selection, which a mock cannot demonstrate.
The result slots are driven directly — starting a real QThread would hit
AnkiConnect and trip the socket tripwire in tests/conftest.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel


@pytest.fixture
def wired(qtbot, test_config: AnkiMinerConfig):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("JP::Mining")
    panel.set_note_type("Lapis")
    ctrl = AnkiProbeController(panel, panel, MagicMock(), lambda: test_config)
    return ctrl, panel


def test_fetched_decks_populate_the_real_combo(wired):
    ctrl, panel = wired
    ctrl._on_name_decks_fetched(["Default", "JP::Mining"])
    assert panel.get_deck_name() == "JP::Mining"
    assert panel.deck_combo.count() == 2
    assert "2" in panel.deck_status.text()


def test_deck_absent_from_anki_reports_failure(wired):
    """A deck Anki does not have must NOT show a green success badge."""
    ctrl, panel = wired
    ctrl._on_name_decks_fetched(["Default"])
    assert panel.get_deck_name() == "JP::Mining"
    assert panel.deck_status.property("status") == "error"
    assert "JP::Mining" in panel.deck_status.text()


def test_empty_deck_fetch_does_not_clear_the_selection(wired):
    ctrl, panel = wired
    ctrl._on_name_decks_fetched([])
    assert panel.get_deck_name() == "JP::Mining"
    assert panel.deck_status.property("status") == "error"


def test_fetched_note_types_populate_the_real_combo(wired):
    ctrl, panel = wired
    ctrl._on_name_notetypes_fetched(["Lapis", "Basic"])
    assert panel.get_note_type() == "Lapis"
    assert panel.notetype_combo.count() == 2


def test_list_refresh_yields_to_an_in_flight_fields_fetch(wired):
    """Auto-Map's status message must not be clobbered by the list refresh.

    The fetched list deliberately EXCLUDES the current note type so the branch
    that actually calls _set_notetype_status fires. Passing a list containing
    it would hit the silent-on-success path, the guard would never be
    consulted, and the test would pass with the guard deleted.
    """
    ctrl, panel = wired
    busy = MagicMock()
    busy.isRunning.return_value = True
    ctrl._fetch_fields_worker = busy
    panel.set_notetype_status(True, "Fetched 18 fields and auto-mapped them")
    ctrl._on_name_notetypes_fetched(["Basic", "Other"])
    assert panel.notetype_combo.count() == 3  # list updated (+ the phantom)
    assert "18 fields" in panel.notetype_status.text()  # message preserved


def test_list_refresh_reports_a_missing_note_type_when_nothing_is_in_flight(wired):
    """The negative half — without the guard the message must be replaced."""
    ctrl, panel = wired
    ctrl._fetch_fields_worker = None
    panel.set_notetype_status(True, "Fetched 18 fields and auto-mapped them")
    ctrl._on_name_notetypes_fetched(["Basic", "Other"])
    assert "Lapis" in panel.notetype_status.text()


def test_close_workers_include_the_name_list_workers(wired, monkeypatch):
    ctrl, _ = wired
    started: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.base_worker.SingleCallWorker.start",
        lambda self: started.append(self),
    )
    ctrl.refresh_name_lists()
    workers = ctrl.iter_close_workers()
    assert len(workers) == 4
    assert sum(w is not None for w in workers) == 2
    assert len(started) == 2
