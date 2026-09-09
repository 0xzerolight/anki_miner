"""AnkiProbeController drives the known-words expression-field pickers.

Uses a REAL FilteringSettingsPanel: the point is that a fetched list reaches
the picker and lands as a row, which a mock cannot demonstrate. The result
slots are driven directly — starting a real QThread would hit AnkiConnect and
trip the socket tripwire in tests/conftest.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def wired(qtbot, test_config: AnkiMinerConfig):
    # Own references to both panels: qtbot.addWidget keeps only a weak one.
    anki_panel = AnkiSettingsPanel()
    qtbot.addWidget(anki_panel)
    anki_panel.set_ankiconnect_url(test_config.ankiconnect_url)
    filtering_panel = FilteringSettingsPanel()
    qtbot.addWidget(filtering_panel)
    ctrl = AnkiProbeController(anki_panel, anki_panel, filtering_panel, lambda: test_config)
    return ctrl, anki_panel, filtering_panel


def test_fetch_note_types_starts_a_worker_and_disables_the_button(wired, monkeypatch):
    """The starter itself, which no other test in this file reaches.

    Every other test drives a result slot directly, so a wrong worker factory,
    a mis-wired ``result_ready`` or a deleted ``worker.start()`` would ship a
    dead button with the file green.
    """
    ctrl, _anki_panel, filtering_panel = wired
    started: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.base_worker.SingleCallWorker.start",
        lambda self: started.append(self),
    )

    ctrl.fetch_known_words_note_types()

    assert ctrl._known_words_notetypes_worker is not None
    assert started == [ctrl._known_words_notetypes_worker]
    assert filtering_panel.add_known_words_field_button.isEnabled() is False


def test_fetched_note_types_open_the_picker(wired, monkeypatch):
    ctrl, _anki_panel, filtering_panel = wired
    requested: list[str] = []
    filtering_panel.fetch_known_words_fields_requested.connect(requested.append)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        lambda *args, **kwargs: ("Sentence First", True),
    )

    ctrl._on_known_words_note_types_fetched(["Lapis", "Sentence First"])

    assert requested == ["Sentence First"]
    assert filtering_panel.add_known_words_field_button.isEnabled()


def test_empty_note_type_list_reports_and_opens_nothing(wired, monkeypatch):
    """[] means unreachable Anki as often as an empty collection (D24: banner,
    never a modal)."""
    ctrl, _anki_panel, filtering_panel = wired
    reported: list[str] = []
    opened: list[bool] = []
    monkeypatch.setattr(ctrl, "_report", lambda summary, details="": reported.append(summary))

    def fake_get_item(*args, **kwargs):
        opened.append(True)
        return "", False

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        fake_get_item,
    )

    ctrl._on_known_words_note_types_fetched([])

    assert len(reported) == 1
    assert opened == []
    assert filtering_panel.add_known_words_field_button.isEnabled()


def test_note_type_result_for_another_endpoint_is_dropped(wired, monkeypatch):
    ctrl, anki_panel, filtering_panel = wired
    opened: list[bool] = []
    monkeypatch.setattr(filtering_panel, "set_available_note_types", lambda names: opened.append(True))
    anki_panel.set_ankiconnect_url("http://127.0.0.1:9999")

    ctrl._on_known_words_note_types_fetched(["Lapis"], "http://127.0.0.1:8765")

    assert opened == []


def test_fetch_fields_starts_a_worker(wired, monkeypatch):
    """Same reason as the note-type starter: nothing else calls this method."""
    ctrl, _anki_panel, _filtering_panel = wired
    started: list[object] = []
    monkeypatch.setattr(
        "anki_miner.gui.workers.base_worker.SingleCallWorker.start",
        lambda self: started.append(self),
    )

    ctrl.fetch_known_words_fields("Sentence First")

    assert ctrl._known_words_fields_worker is not None
    assert started == [ctrl._known_words_fields_worker]


def test_fetched_fields_add_the_row(wired, monkeypatch):
    ctrl, _anki_panel, filtering_panel = wired
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        lambda *args, **kwargs: ("Word", True),
    )

    ctrl._on_known_words_fields_fetched("Sentence First", ["Sentence", "Word"])

    assert filtering_panel.get_known_words_expression_fields() == {"Sentence First": "Word"}


def test_empty_field_list_reports_and_opens_no_picker(wired, monkeypatch):
    """AnkiConnect answers [] for an unreachable Anki as well as for a real
    empty result. The controller owns that verdict — it is the only guard, so
    an empty dialog must never open."""
    ctrl, _anki_panel, filtering_panel = wired
    reported: list[str] = []
    opened: list[bool] = []
    monkeypatch.setattr(ctrl, "_report", lambda summary, details="": reported.append(summary))

    def fake_get_item(*args, **kwargs):
        opened.append(True)
        return "", False

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.filtering_settings_panel.QInputDialog.getItem",
        fake_get_item,
    )

    ctrl._on_known_words_fields_fetched("Sentence First", [])

    assert len(reported) == 1
    assert opened == []
    assert filtering_panel.get_known_words_expression_fields() == {}


def test_settings_tab_wires_both_picker_signals(qtbot, test_config, monkeypatch):
    """The panel's signals must reach the controller.

    Without this, deleting the two connect() lines in settings_tab.py leaves
    every other test in this file green and ships a button that does nothing —
    the failure mode this repo has already shipped once.

    The class attributes are patched BEFORE construction because
    ``connect(self._anki_probe.fetch_known_words_note_types)`` binds the method
    at connect time.
    """
    note_type_calls: list[bool] = []
    field_calls: list[str] = []
    monkeypatch.setattr(
        AnkiProbeController,
        "fetch_known_words_note_types",
        lambda self: note_type_calls.append(True),
    )
    monkeypatch.setattr(
        AnkiProbeController,
        "fetch_known_words_fields",
        lambda self, note_type: field_calls.append(note_type),
    )

    tab = SettingsTab(test_config)
    qtbot.addWidget(tab)
    try:
        tab.filtering_panel.fetch_known_words_note_types_requested.emit()
        tab.filtering_panel.fetch_known_words_fields_requested.emit("X")

        assert note_type_calls == [True]
        assert field_calls == ["X"]
    finally:
        tab.shutdown()
        for worker in tab.iter_close_workers():
            if worker is not None:
                worker.wait(3000)
        qtbot.wait(10)


def test_worker_errors_are_reported_not_raised(wired, monkeypatch):
    ctrl, _anki_panel, _filtering_panel = wired
    reported: list[str] = []
    monkeypatch.setattr(ctrl, "_report", lambda summary, details="": reported.append(summary))

    ctrl._on_known_words_note_types_error("boom")
    ctrl._on_known_words_fields_error("boom")

    assert len(reported) == 2


def test_both_handles_are_joined_on_close(wired):
    """closeEvent joins every live probe through iter_close_workers (T-12)."""
    ctrl, _anki_panel, _filtering_panel = wired
    ctrl._known_words_notetypes_worker = MagicMock()
    ctrl._known_words_fields_worker = MagicMock()

    workers = ctrl.iter_close_workers()

    assert ctrl._known_words_notetypes_worker in workers
    assert ctrl._known_words_fields_worker in workers
