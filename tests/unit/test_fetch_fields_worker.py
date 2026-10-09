"""Tests for the FetchFieldsWorker and the SettingsTab wiring around it.

The button click (``AnkiSettingsPanel.fetch_fields_requested``) used to be a
dead-end signal with no connected slot. These tests pin the wired behaviour:

- The worker calls ``AnkiService.get_note_type_fields`` and emits its result.
- The probe controller dispatches the worker and routes the result back into
  ``AnkiSettingsPanel.fill_from_field_list``.
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
    """Pin the button-click -> service -> fill_from_field_list path."""

    def test_click_with_empty_note_type_does_not_spawn_worker(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("")  # explicit empty
        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "fill_from_field_list", populate)

        with patch("anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker") as worker_cls:
            tab.anki_panel.fetch_fields_button.click()

        worker_cls.assert_not_called()
        populate.assert_not_called()
        # Friendly status on the fill line.
        assert "Select a note type" in tab.anki_panel.fill_status.text()

    def test_click_routes_fetched_fields_into_populate(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Japanese-1.0")
        tab.anki_panel.ankiconnect_url_input.setText("http://localhost:8765")

        populate = MagicMock(return_value=(None, 0))
        monkeypatch.setattr(tab.anki_panel, "fill_from_field_list", populate)

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

        # The fetched list was handed to fill_from_field_list on the main thread.
        populate.assert_called_once_with(["Expression", "Sentence", "MainDefinition"])
        # Status surfaces the count.
        assert tab.anki_panel.fill_status.text() == "Fetched 3 field(s) and auto-mapped them"
        # Button is re-enabled after the result lands.
        assert tab.anki_panel.fetch_fields_button.isEnabled()

    def test_status_names_the_stale_mappings_auto_map_cleared(self, test_config: AnkiMinerConfig, qtbot):
        """Silently blanking a row the user typed would read as data loss."""
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Chinese Basic")
        tab.anki_panel.set_card_fields(
            {"word": "Expression", "sentence": "Sentence", "definition": "MainDefinition"}
            | dict.fromkeys(("picture", "audio", "expression_furigana", "sentence_furigana"), "")
        )

        tab._anki_probe._on_fetch_fields_finished("Chinese Basic", ["Expression", "Sentence"])

        status = tab.anki_panel.fill_status.text()
        # Both halves are Qt numerus sources, so the count renders as "%n"
        # substituted into the English fallback rather than a Python ternary a
        # catalogue cannot reach.
        assert status == "Fetched 2 field(s) and auto-mapped them; cleared 1 stale mapping(s)"

    def test_status_stays_quiet_when_nothing_was_cleared(self, test_config: AnkiMinerConfig, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Japanese-1.0")
        tab.anki_panel.set_card_fields(
            {"word": "Expression", "sentence": "Sentence"}
            | dict.fromkeys(("definition", "picture", "audio", "expression_furigana", "sentence_furigana"), "")
        )

        tab._anki_probe._on_fetch_fields_finished("Japanese-1.0", ["Expression", "Sentence"])

        assert "cleared" not in tab.anki_panel.fill_status.text()

    def test_empty_fetch_result_shows_friendly_status(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Missing")

        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "fill_from_field_list", populate)

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
        assert "Could not fetch" in tab.anki_panel.fill_status.text()
        assert tab.anki_panel.fetch_fields_button.isEnabled()

    def test_late_field_fetch_does_not_map_into_new_note_type(self, test_config: AnkiMinerConfig, monkeypatch, qtbot):
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Type A")
        populate = MagicMock()
        monkeypatch.setattr(tab.anki_panel, "fill_from_field_list", populate)

        worker = MagicMock()
        worker.isRunning.return_value = False
        with patch("anki_miner.gui.controllers.anki_probe_controller.FetchFieldsWorker", return_value=worker):
            tab.anki_panel.fetch_fields_button.click()

        on_fields = worker.result_ready.connect.call_args.args[0]
        tab.anki_panel.set_note_type("Type B")
        on_fields(["Expression", "Sentence"])

        populate.assert_not_called()

    def test_a_recognised_note_type_reports_its_name(self, test_config: AnkiMinerConfig, qtbot):
        from anki_miner.services.note_presets import LAPIS

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Lapis")

        tab._anki_probe._on_fetch_fields_finished("Lapis", sorted(LAPIS.signature))

        assert tab.anki_panel.fill_status.text() == "Lapis recognised: 15 fields filled."

    def test_a_recognised_note_type_counts_the_language_fields(self, test_config: AnkiMinerConfig, qtbot):
        """Anki Miner Note on zh writes Pinyin, Traditional and MeasureWord beside its 17."""
        from dataclasses import replace

        from tests.unit.amn_fields import AMN_FIELDS

        tab = SettingsTab(replace(test_config, language="zh"))
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Anki Miner Note")

        tab._anki_probe._on_fetch_fields_finished("Anki Miner Note", list(AMN_FIELDS))

        assert tab.anki_panel.fill_status.text() == "Anki Miner Note recognised: 20 fields filled."

    def test_anki_miner_note_turns_on_bold_target_words(self, test_config: AnkiMinerConfig, qtbot):
        """Its audio card hides the <b> word and its sentence / click cards cue it, so the fill
        switches Settings -> Sentences -> Bold target word on as well."""
        from tests.unit.amn_fields import AMN_FIELDS

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Anki Miner Note")
        tab.sentences_panel.set_bold_target_in_sentence(False)

        tab._anki_probe._on_fetch_fields_finished("Anki Miner Note", list(AMN_FIELDS))

        assert tab.sentences_panel.get_bold_target_in_sentence() is True
        assert tab.anki_panel.fill_status.text().startswith("Anki Miner Note recognised: ")
        assert tab.anki_panel.get_card_fields()["language"] == "Language"

    def test_a_preset_without_bold_leaves_the_bold_setting_alone(self, test_config: AnkiMinerConfig, qtbot):
        from anki_miner.services.note_presets import LAPIS

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        tab.anki_panel.set_note_type("Lapis")
        tab.sentences_panel.set_bold_target_in_sentence(False)

        tab._anki_probe._on_fetch_fields_finished("Lapis", sorted(LAPIS.signature))

        assert tab.sentences_panel.get_bold_target_in_sentence() is False
