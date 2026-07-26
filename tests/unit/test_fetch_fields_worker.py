"""Tests for the FetchFieldsWorker and the SettingsTab wiring around it.

The button click (``AnkiSettingsPanel.fetch_fields_requested``) used to be a
dead-end signal with no connected slot. These tests pin the wired behaviour:

- The worker calls ``AnkiService.get_note_type_fields`` and emits its result.
- The probe controller dispatches the worker and routes the result back into
  ``AnkiSettingsPanel.populate_from_field_list``.
- An empty note-type input short-circuits without spawning a worker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.workers.fetch_workers import FetchFieldsWorker


class TestFetchFieldsWorker:
    """The worker is a thin shim — verify it calls the service and emits."""

    def test_emits_result_ready_with_fields(self, qapp):
        service = MagicMock()
        service.get_note_type_fields.return_value = ["Expression", "Sentence"]

        worker = FetchFieldsWorker(service, "MyNote")

        received: list[list[str]] = []
        worker.result_ready.connect(received.append)

        worker.run()  # synchronous: bypass QThread.start

        service.get_note_type_fields.assert_called_once_with("MyNote")
        assert received == [["Expression", "Sentence"]]

    def test_emits_empty_list_when_service_returns_empty(self, qapp):
        service = MagicMock()
        service.get_note_type_fields.return_value = []

        worker = FetchFieldsWorker(service, "Bogus")

        received: list[list[str]] = []
        worker.result_ready.connect(received.append)

        worker.run()

        assert received == [[]]

    def test_emits_error_when_service_raises(self, qapp):
        service = MagicMock()
        service.get_note_type_fields.side_effect = RuntimeError("boom")

        worker = FetchFieldsWorker(service, "MyNote")

        errors: list[str] = []
        results: list[list[str]] = []
        worker.error.connect(errors.append)
        worker.result_ready.connect(results.append)

        worker.run()

        assert results == []
        assert len(errors) == 1
        assert "boom" in errors[0]

    def test_cancel_before_run_skips_emit(self, qapp):
        service = MagicMock()
        service.get_note_type_fields.return_value = ["X"]

        worker = FetchFieldsWorker(service, "MyNote")

        received: list[list[str]] = []
        worker.result_ready.connect(received.append)

        worker.cancel()
        worker.run()

        assert received == []


class TestSettingsTabFetchFieldsWiring:
    """Pin the button-click -> service -> populate_from_field_list path."""

    def test_click_with_empty_note_type_does_not_spawn_worker(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("")  # explicit empty
        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "populate_from_field_list", populate)

        with patch("anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker") as worker_cls:
            tab.anki_panel.fetch_fields_button.click()

        worker_cls.assert_not_called()
        populate.assert_not_called()
        # Friendly status on the note-type line.
        assert "Select a note type" in tab.anki_panel.notetype_status.text()

    def test_click_routes_fetched_fields_into_populate(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Japanese-1.0")
        tab.anki_panel.ankiconnect_url_input.setText("http://localhost:8765")

        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "populate_from_field_list", populate)

        # Build a fake worker class whose instances:
        #   - record what they were called with
        #   - invoke result_ready synchronously when .start() is called
        built: list[MagicMock] = []

        def fake_worker_factory(service, note_type, parent):
            inst = MagicMock()
            inst.note_type = note_type
            inst.service = service
            inst.isRunning.return_value = False
            # Simulate the fetched field list arriving from the worker thread.
            inst.start.side_effect = lambda: tab._anki_probe._on_fetch_fields_finished(
                note_type, ["Expression", "Sentence", "MainDefinition"]
            )
            built.append(inst)
            return inst

        with patch(
            "anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker",
            side_effect=fake_worker_factory,
        ):
            tab.anki_panel.fetch_fields_button.click()

        # Worker was spun up for the right note type.
        assert len(built) == 1
        assert built[0].note_type == "Japanese-1.0"
        built[0].start.assert_called_once()

        # The fetched list was handed to populate_from_field_list on the main thread.
        populate.assert_called_once_with(["Expression", "Sentence", "MainDefinition"])
        # Status surfaces the count.
        assert "Fetched 3 fields" in tab.anki_panel.notetype_status.text()
        # Button is re-enabled after the result lands.
        assert tab.anki_panel.fetch_fields_button.isEnabled()

    def test_empty_fetch_result_shows_friendly_status(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Missing")

        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "populate_from_field_list", populate)

        def fake_worker_factory(service, note_type, parent):
            inst = MagicMock()
            inst.isRunning.return_value = False
            inst.start.side_effect = lambda: tab._anki_probe._on_fetch_fields_finished(note_type, [])
            return inst

        with patch(
            "anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker",
            side_effect=fake_worker_factory,
        ):
            tab.anki_panel.fetch_fields_button.click()

        populate.assert_not_called()
        assert "Could not fetch" in tab.anki_panel.notetype_status.text()
        assert tab.anki_panel.fetch_fields_button.isEnabled()

    def test_late_field_fetch_does_not_map_into_new_note_type(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Type A")
        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "populate_from_field_list", populate)

        worker = MagicMock()
        worker.isRunning.return_value = False
        with patch("anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker", return_value=worker):
            tab.anki_panel.fetch_fields_button.click()

        on_fields = worker.result_ready.connect.call_args.args[0]
        tab.anki_panel.set_note_type("Type B")
        on_fields(["Expression", "Sentence"])

        populate.assert_not_called()
