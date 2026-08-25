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
    panel.set_ankiconnect_url(test_config.ankiconnect_url)
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


def test_probing_twice_releases_the_first_deck_worker(wired, monkeypatch):
    """A second probe must not accumulate live QThreads (worker-release sweep).

    Every probe worker here is parented to the settings tab (window lifetime),
    so without an explicit ``deleteLater()`` on ``finished`` it is never
    garbage collected -- each probe piles up one more live QObject for the
    rest of the session. Simulates the first worker's native ``finished``
    firing, then asserts the controller released it (deleteLater + cleared
    attribute) before the second probe starts.
    """
    ctrl, panel = wired
    worker_one = MagicMock()
    worker_two = MagicMock()
    factory = MagicMock(side_effect=[worker_one, worker_two])
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchDecksWorker",
        factory,
    )

    ctrl.fetch_decks()
    assert ctrl._fetch_decks_worker is worker_one
    on_finished = worker_one.finished.connect.call_args.args[0]
    on_finished()  # simulate the QThread actually exiting

    worker_one.deleteLater.assert_called_once()
    assert ctrl._fetch_decks_worker is None

    ctrl.fetch_decks()
    assert ctrl._fetch_decks_worker is worker_two
    worker_two.deleteLater.assert_not_called()


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


def test_field_probe_drops_result_and_error_after_endpoint_changes(wired, monkeypatch):
    ctrl, panel = wired
    panel.set_ankiconnect_url("http://127.0.0.1:8765")
    populate = MagicMock()
    monkeypatch.setattr(panel, "populate_from_field_list", populate)
    worker = MagicMock()
    worker.isRunning.return_value = False
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker",
        MagicMock(return_value=worker),
    )

    ctrl.fetch_fields()
    on_result = worker.result_ready.connect.call_args.args[0]
    on_error = worker.error.connect.call_args.args[0]
    panel.set_ankiconnect_url("http://127.0.0.1:9999")

    on_result(["Expression", "Sentence"])
    on_error("Error from old endpoint")

    populate.assert_not_called()
    assert panel.notetype_status.text() == ""


def test_excluded_deck_probe_drops_late_callbacks_after_endpoint_clears(wired, monkeypatch):
    ctrl, panel = wired
    filtering_panel = ctrl._filtering_panel
    worker = MagicMock()
    worker.isRunning.return_value = False
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchDecksWorker",
        MagicMock(return_value=worker),
    )
    report = MagicMock()
    monkeypatch.setattr(ctrl, "_report", report)

    ctrl.fetch_decks()
    on_result = worker.result_ready.connect.call_args.args[0]
    on_error = worker.error.connect.call_args.args[0]
    panel.set_ankiconnect_url("")

    on_result(["Deck from A"])
    on_error("Error from A")

    filtering_panel.set_available_decks.assert_not_called()
    assert filtering_panel.set_add_deck_button_enabled.call_args.args == (True,)
    report.assert_not_called()


def test_name_list_probes_drop_late_callbacks_after_endpoint_changes(wired, monkeypatch):
    ctrl, panel = wired
    decks_worker = MagicMock()
    notetypes_worker = MagicMock()
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchDecksWorker",
        MagicMock(return_value=decks_worker),
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchNotetypesWorker",
        MagicMock(return_value=notetypes_worker),
    )

    ctrl.refresh_name_lists()
    on_decks_result = decks_worker.result_ready.connect.call_args.args[0]
    on_decks_error = decks_worker.error.connect.call_args.args[0]
    on_notetypes_result = notetypes_worker.result_ready.connect.call_args.args[0]
    on_notetypes_error = notetypes_worker.error.connect.call_args.args[0]
    panel.set_ankiconnect_url("http://127.0.0.1:9999")
    panel.set_available_decks(["Current"])
    panel.set_deck_name("Current")
    panel.set_available_note_types(["CurrentType"])
    panel.set_note_type("CurrentType")
    panel.set_deck_status(None, "Current endpoint deck list pending")
    panel.set_notetype_status(None, "Current endpoint note-type list pending")

    on_decks_result(["Deck from A"])
    on_decks_error("Deck error from A")
    on_notetypes_result(["Type from A"])
    on_notetypes_error("Note-type error from A")

    assert panel.deck_combo.findText("Deck from A") == -1
    assert panel.notetype_combo.findText("Type from A") == -1
    assert panel.deck_status.text() == "Current endpoint deck list pending"
    assert panel.notetype_status.text() == "Current endpoint note-type list pending"


def test_blank_endpoint_does_not_start_field_probe(wired, monkeypatch):
    ctrl, panel = wired
    panel.set_ankiconnect_url("")
    worker_factory = MagicMock()
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker",
        worker_factory,
    )

    ctrl.fetch_fields()

    worker_factory.assert_not_called()


def test_blank_endpoint_does_not_start_excluded_deck_probe(wired, monkeypatch):
    ctrl, panel = wired
    panel.set_ankiconnect_url("")
    worker_factory = MagicMock()
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchDecksWorker",
        worker_factory,
    )

    ctrl.fetch_decks()

    worker_factory.assert_not_called()


def test_blank_endpoint_does_not_start_name_list_probes(wired, monkeypatch):
    ctrl, panel = wired
    panel.set_ankiconnect_url("")
    decks_factory = MagicMock()
    notetypes_factory = MagicMock()
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchDecksWorker",
        decks_factory,
    )
    monkeypatch.setattr(
        "anki_miner.gui.controllers.anki_probe_controller.FetchNotetypesWorker",
        notetypes_factory,
    )

    ctrl.refresh_name_lists()

    decks_factory.assert_not_called()
    notetypes_factory.assert_not_called()
