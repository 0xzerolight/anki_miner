"""Tests for the Settings tab's Export/Import Settings actions (run from the Profile Manager, D14)."""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import file_dialogs
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
    def test_export_writes_portable_file(self, tab, tmp_path, monkeypatch, messageboxes):
        target = tmp_path / "my_settings.json"
        monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))

        tab.export_settings()

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["anki_miner_settings"] == 1
        assert payload["settings"]["anki_deck_name"] == tab.config.anki_deck_name
        assert "dicts_root" not in payload["settings"]
        assert messageboxes["information"], "success dialog expected"
        assert not messageboxes["critical"]

    def test_export_cancelled_is_noop(self, tab, monkeypatch, messageboxes):
        monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(""))

        tab.export_settings()

        assert not messageboxes["information"]
        assert not messageboxes["critical"]

    def test_a_failure_reported_from_the_profile_manager_lands_on_its_banner(self, tab, tmp_path, monkeypatch, qtbot):
        from anki_miner.gui.widgets.base import ScreenIssueHost

        class _Surface(ScreenIssueHost, QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.issues: list[object] = []

            def show_screen_issue(self, issue, *, action=None) -> None:
                self.issues.append(issue)

        surface = _Surface()
        qtbot.addWidget(surface)
        target = tmp_path / "missing-dir" / "x.json"
        monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))
        monkeypatch.setattr(
            "anki_miner.gui.utils.config_manager.GUIConfigManager.export_config",
            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
        )

        tab.export_settings(surface)

        assert len(surface.issues) == 1


class TestImportButton:
    def _write_export(self, tmp_path, config):
        path = tmp_path / "incoming.json"
        GUIConfigManager.export_config(config, path)
        return path

    def test_import_confirm_yes_applies_and_reloads(self, tab, test_config, tmp_path, monkeypatch, messageboxes, qtbot):
        path = self._write_export(tmp_path, replace(test_config, anki_deck_name="ImportedDeck"))
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(path)))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings()

        assert messageboxes["question"], "confirmation prompt expected"
        assert len(received) == 1
        assert received[0].anki_deck_name == "ImportedDeck"
        # Machine-specific fields kept current.
        assert received[0].dicts_root == test_config.dicts_root
        # Simulate MainWindow's config_refreshed round-trip after persistence.
        tab.update_config(received[0])
        qtbot.waitUntil(lambda: not tab.subtitles_panel._state_in_flight, timeout=5000)
        # Panels reloaded to show the imported values.
        assert tab.anki_panel.get_deck_name() == "ImportedDeck"
        assert "✓" in tab.save_status_label.text()

    def test_import_confirm_no_is_noop(self, tab, test_config, tmp_path, monkeypatch, messageboxes):
        messageboxes["_reply"]["question"] = QMessageBox.StandardButton.No
        path = self._write_export(tmp_path, replace(test_config, anki_deck_name="Rejected"))
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(path)))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings()

        assert received == []
        assert tab.anki_panel.get_deck_name() == test_config.anki_deck_name

    def test_import_file_dialog_cancelled_is_noop(self, tab, monkeypatch, messageboxes):
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(""))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings()

        assert received == []
        assert not messageboxes["question"]

    def test_import_malformed_file_reports_an_issue_and_emits_nothing(self, tab, tmp_path, monkeypatch, messageboxes):
        bad = tmp_path / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(bad)))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings()

        issue = tab.issue_banner().current_issue()
        assert issue is not None and issue.summary == "Settings could not be imported."
        assert str(bad) in issue.details, "the path belongs in Details, not the sentence"
        assert received == []

    def test_import_wrong_shape_json_reports_an_issue_and_emits_nothing(self, tab, tmp_path, monkeypatch, messageboxes):
        bad = tmp_path / "list.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(bad)))
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.import_settings()

        issue = tab.issue_banner().current_issue()
        assert issue is not None and issue.summary == "Settings could not be imported."
        assert received == []


class TestConfirmationsFromTheProfileManager:
    """P-B3.7a: the tab's flash row sits behind the modal manager, so a clean finish there went unseen."""

    @staticmethod
    def _manager(tab, qtbot):
        from anki_miner.gui.widgets.dialogs.profile_manager_dialog import ProfileManagerDialog

        class _Controller:
            def switch_to(self, profile_id):
                raise AssertionError("not under test")

            def create_from_current(self, name):
                raise AssertionError("not under test")

        dialog = ProfileManagerDialog(_Controller(), lambda: None, settings_actions=tab)
        qtbot.addWidget(dialog)
        return dialog

    def test_a_clean_import_is_confirmed_in_the_manager(
        self, tab, test_config, tmp_path, monkeypatch, messageboxes, qtbot
    ):
        path = tmp_path / "incoming.json"
        GUIConfigManager.export_config(replace(test_config, anki_deck_name="ImportedDeck"), path)
        monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(path)))
        dialog = self._manager(tab, qtbot)

        dialog.import_settings_button.click()

        assert dialog.status_label.text() == "✓ Imported"
        assert tab.save_status_label.text() == ""

    def test_a_reset_is_confirmed_in_the_manager(self, tab, messageboxes, qtbot):
        dialog = self._manager(tab, qtbot)

        dialog.reset_settings_button.click()

        assert dialog.status_label.text() == "✓ Reset to defaults"
        assert tab.save_status_label.text() == ""

    def test_a_reset_without_a_surface_still_flashes_the_tab(self, tab, messageboxes):
        tab.reset_settings()

        assert tab.save_status_label.text() == "✓ Reset to defaults"
