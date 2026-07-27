"""Settings reports its own recoverable failures in place (D24, T5).

Export, import, the known-words maintenance actions and the Anki deck probe all
used to open a modal. Settings is where the repair lives, so the failure belongs
on the page rather than on top of it — and the file path that failed belongs in
Details, not in the sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.widgets.settings_tab import SettingsTab


@pytest.fixture
def settings(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.resolve_start_dir",
        lambda *a, **kw: str(tmp_path),
    )
    tab = SettingsTab(AnkiMinerConfig())
    qtbot.addWidget(tab)
    return tab


def _choose_save(monkeypatch, target: Path) -> None:
    monkeypatch.setattr(
        "anki_miner.gui.utils.file_dialogs.get_save_file_name",
        lambda *a, **kw: (str(target), ""),
    )


def _choose_open(monkeypatch, source: Path) -> None:
    monkeypatch.setattr(
        "anki_miner.gui.utils.file_dialogs.get_open_file_name",
        lambda *a, **kw: (str(source), ""),
    )


class TestExport:
    def test_a_failed_export_is_reported_on_the_page(self, settings, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        _choose_save(monkeypatch, target)

        def _refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(GUIConfigManager, "export_config", staticmethod(_refuse))
        settings._on_export_settings()

        issue = settings.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "Settings could not be exported."
        assert str(target) not in issue.summary
        assert str(target) in issue.details
        assert "Permission denied" in issue.details

    def test_the_repair_retries_the_export(self, settings, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        _choose_save(monkeypatch, target)
        attempts: list[int] = []

        def _refuse(*args, **kwargs):
            attempts.append(1)
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(GUIConfigManager, "export_config", staticmethod(_refuse))
        settings._on_export_settings()
        settings.issue_banner().action_button.click()
        assert len(attempts) == 2

    def test_a_successful_export_clears_a_stale_issue(self, settings, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        _choose_save(monkeypatch, target)
        shown: list[str] = []
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.QMessageBox.information",
            lambda *a, **kw: shown.append("ok"),
        )
        settings.show_screen_issue(_stale_issue())
        settings._on_export_settings()
        assert settings.issue_banner().current_issue() is None
        assert shown == ["ok"]


class TestImport:
    def test_an_unreadable_file_is_reported_on_the_page(self, settings, tmp_path, monkeypatch):
        source = tmp_path / "broken.json"
        source.write_text("{ not json", encoding="utf-8")
        _choose_open(monkeypatch, source)
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
            lambda *a, **kw: __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes,
        )
        settings._on_import_settings()

        issue = settings.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "Settings could not be imported."
        assert str(source) not in issue.summary
        assert str(source) in issue.details

    def test_the_overwrite_confirmation_stays_modal(self, settings, tmp_path, monkeypatch):
        """Import overwrites the live settings — the last chance to say no stays a modal."""
        source = tmp_path / "ok.json"
        source.write_text(json.dumps({"deck_name": "Mining"}), encoding="utf-8")
        _choose_open(monkeypatch, source)
        asked: list[str] = []
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
            lambda _p, _t, body, *a, **kw: asked.append(body)
            or __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.No,
        )
        settings._on_import_settings()
        assert len(asked) == 1


class TestKnownWords:
    def test_a_failed_cache_rebuild_is_reported_on_the_page(self, settings, monkeypatch):
        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
            lambda *a, **kw: __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes,
        )

        class _Broken:
            def __init__(self, *_a, **_kw):
                raise OSError("database is locked")

        monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.KnownWordDB", _Broken)
        settings._on_rebuild_known_words()

        issue = settings.issue_banner().current_issue()
        assert issue is not None
        assert "database is locked" not in issue.summary
        assert "database is locked" in issue.details

    def test_a_failed_manage_dialog_is_reported_on_the_page(self, settings, monkeypatch):
        class _Broken:
            def __init__(self, *_a, **_kw):
                raise OSError("database is locked")

        monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.KnownWordDB", _Broken)
        settings._on_manage_known_words()

        issue = settings.issue_banner().current_issue()
        assert issue is not None
        assert "database is locked" in issue.details


class TestAudioMarkerSweep:
    def test_a_failed_sweep_is_reported_on_the_page(self, settings):
        settings._on_retry_missing_audio_error("PermissionError: [Errno 13]")
        issue = settings.issue_banner().current_issue()
        assert issue is not None
        assert "Errno 13" not in issue.summary
        assert "Errno 13" in issue.details
        assert settings.audio_panel._retry_missing_btn.isEnabled()


def _stale_issue():
    from anki_miner.gui.widgets.base import ScreenIssue

    return ScreenIssue(summary="Settings could not be exported.")
