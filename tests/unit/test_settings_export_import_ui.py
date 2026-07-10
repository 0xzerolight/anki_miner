"""Tests for the Settings tab's Export/Import Settings buttons."""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    widget._debounce_timer.setInterval(60_000)
    yield widget
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


@pytest.fixture
def messageboxes(monkeypatch):
    """Capture QMessageBox calls; question answers Yes by default."""
    captured: dict[str, list[tuple]] = {"information": [], "critical": [], "question": []}
    reply = {"question": QMessageBox.StandardButton.Yes}

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: captured["information"].append(a))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: captured["critical"].append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: captured["question"].append(a) or reply["question"])
    captured["_reply"] = reply  # type: ignore[assignment]
    return captured


class TestExportButton:
    def test_buttons_exist(self, tab):
        assert tab.export_settings_button is not None
        assert tab.import_settings_button is not None

    def test_export_writes_portable_file(self, tab, tmp_path, monkeypatch, messageboxes):
        target = tmp_path / "my_settings.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))

        tab.export_settings_button.click()

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["anki_miner_settings"] == 1
        assert payload["settings"]["anki_deck_name"] == tab.config.anki_deck_name
        assert "dicts_root" not in payload["settings"]
        assert messageboxes["information"], "success dialog expected"
        assert not messageboxes["critical"]

    def test_export_cancelled_is_noop(self, tab, monkeypatch, messageboxes):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))

        tab.export_settings_button.click()

        assert not messageboxes["information"]
        assert not messageboxes["critical"]


class TestImportButton:
    def _write_export(self, tmp_path, config):
        path = tmp_path / "incoming.json"
        GUIConfigManager.export_config(config, path)
        return path

    def test_import_confirm_yes_applies_and_reloads(self, tab, test_config, tmp_path, monkeypatch, messageboxes):
        path = self._write_export(tmp_path, replace(test_config, anki_deck_name="ImportedDeck"))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings_button.click()

        assert messageboxes["question"], "confirmation prompt expected"
        assert len(received) == 1
        assert received[0].anki_deck_name == "ImportedDeck"
        # Machine-specific fields kept current.
        assert received[0].dicts_root == test_config.dicts_root
        # Panels reloaded to show the imported values.
        assert tab.deck_input.text() == "ImportedDeck"
        assert "✓" in tab.save_status_label.text()

    def test_import_confirm_no_is_noop(self, tab, test_config, tmp_path, monkeypatch, messageboxes):
        messageboxes["_reply"]["question"] = QMessageBox.StandardButton.No
        path = self._write_export(tmp_path, replace(test_config, anki_deck_name="Rejected"))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings_button.click()

        assert received == []
        assert tab.deck_input.text() == test_config.anki_deck_name

    def test_import_file_dialog_cancelled_is_noop(self, tab, monkeypatch, messageboxes):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings_button.click()

        assert received == []
        assert not messageboxes["question"]

    def test_import_malformed_file_shows_critical_no_emit(self, tab, tmp_path, monkeypatch, messageboxes):
        bad = tmp_path / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings_button.click()

        assert messageboxes["critical"], "error dialog expected for malformed file"
        assert received == []

    def test_import_wrong_shape_json_shows_critical_no_emit(self, tab, tmp_path, monkeypatch, messageboxes):
        bad = tmp_path / "list.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings_button.click()

        assert messageboxes["critical"]
        assert received == []
