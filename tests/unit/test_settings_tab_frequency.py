"""Tests for SettingsTab._resolve_frequency_path — Yomitan freq-zip save hook.

Covers the four branches the GUI hook has to get right: no-path, CSV passthrough,
zip-with-overwrite-decline, and the full worker-driven import (success / failure /
cancel). The real ``FrequencyImportWorker`` is used end-to-end (a real QThread
runs); only the inner ``import_yomitan_freq_zip`` call is stubbed so we control
the outcome deterministically.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.frequency import YomitanFreqImportResult

# QApplication required for any Qt widget test.
_app = QApplication.instance() or QApplication([])


@pytest.fixture
def freq_home(tmp_path, monkeypatch):
    """Redirect ANKI_MINER_HOME to a tmp dir so the importer doesn't touch ~."""
    home = tmp_path / "anki_miner_home"
    home.mkdir()
    monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.ANKI_MINER_HOME", home)
    return home


@pytest.fixture
def tab(test_config: AnkiMinerConfig, freq_home: Path):
    widget = SettingsTab(test_config)
    yield widget
    widget.deleteLater()


def _capture_messagebox(monkeypatch, default_question_reply=QMessageBox.StandardButton.Yes):
    """Stub QMessageBox.{question,warning,information}; return captured calls."""
    captured: dict[str, list[tuple[str, str]]] = {"question": [], "warning": [], "information": []}

    def fake_question(parent, title, text, *args, **kwargs):
        captured["question"].append((title, text))
        return default_question_reply

    def fake_warning(parent, title, text, *args, **kwargs):
        captured["warning"].append((title, text))
        return QMessageBox.StandardButton.Ok

    def fake_information(parent, title, text, *args, **kwargs):
        captured["information"].append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(fake_information))
    return captured


def _stub_importer(monkeypatch, *, result=None, error=None):
    """Replace import_yomitan_freq_zip inside the worker with a controllable stub."""
    calls: list[tuple[Path, Path]] = []

    def fake(zip_path, dest_csv, *, progress=None, cancel_check=None):
        calls.append((zip_path, dest_csv))
        if progress is not None:
            progress(1, 1, "Imported test_meta_bank_1.json")
        if error is not None:
            raise error
        # Materialize the CSV so dest_csv.exists() flips to True for next-save tests.
        dest_csv.write_text("term,rank\n猫,100\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        "anki_miner.gui.workers.frequency_import_worker.import_yomitan_freq_zip",
        fake,
    )
    return calls


class TestPassthroughBranches:
    def test_empty_path_returns_empty_path(self, tab, monkeypatch):
        _capture_messagebox(monkeypatch)
        calls = _stub_importer(monkeypatch)
        tab.filtering_panel.frequency_selector.set_path("")

        out = tab._resolve_frequency_path()

        assert out == Path("")
        assert calls == []

    def test_csv_path_returns_unchanged(self, tab, tmp_path, monkeypatch):
        _capture_messagebox(monkeypatch)
        calls = _stub_importer(monkeypatch)
        csv = tmp_path / "freq.csv"
        csv.write_text("word,rank\n猫,1\n", encoding="utf-8")
        tab.filtering_panel.frequency_selector.set_path(str(csv))

        out = tab._resolve_frequency_path()

        assert out == Path(str(csv))
        assert calls == []


class TestOverwritePrompt:
    def test_existing_nonempty_csv_prompts_and_user_declines(self, tab, tmp_path, freq_home, monkeypatch):
        # Seed an existing frequency.csv.
        existing = freq_home / "frequency.csv"
        existing.write_text("word,rank\nfoo,1\n", encoding="utf-8")
        # Pin config's frequency_list_path so we can verify the decline-return value.
        prior = freq_home / "previous.csv"
        prior.write_text("word,rank\nprior,1\n", encoding="utf-8")
        tab.config = replace(tab.config, frequency_list_path=prior)

        captured = _capture_messagebox(monkeypatch, default_question_reply=QMessageBox.StandardButton.No)
        calls = _stub_importer(monkeypatch)
        zip_path = tmp_path / "freq.zip"
        zip_path.write_bytes(b"")  # contents irrelevant — importer is stubbed
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        out = tab._resolve_frequency_path()

        assert out == prior  # rest of save proceeds with the prior path
        assert calls == []  # importer never ran
        assert len(captured["question"]) == 1
        assert "Overwrite" in captured["question"][0][0]

    def test_existing_empty_csv_does_not_prompt(self, tab, tmp_path, freq_home, monkeypatch):
        existing = freq_home / "frequency.csv"
        existing.write_text("", encoding="utf-8")  # zero bytes

        captured = _capture_messagebox(monkeypatch)
        calls = _stub_importer(
            monkeypatch,
            result=YomitanFreqImportResult(
                source_name="Test",
                source_revision="v1",
                entry_count=1,
                skipped_display_only=0,
            ),
        )
        zip_path = tmp_path / "freq.zip"
        zip_path.write_bytes(b"")
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        out = tab._resolve_frequency_path()

        assert out == freq_home / "frequency.csv"
        assert calls and calls[0][0] == zip_path
        assert captured["question"] == []  # no overwrite prompt on empty file


class TestWorkerOutcomes:
    def test_successful_import_updates_selector_and_returns_csv(self, tab, tmp_path, freq_home, monkeypatch):
        captured = _capture_messagebox(monkeypatch)
        result = YomitanFreqImportResult(
            source_name="JPDB",
            source_revision="2024-01",
            entry_count=42,
            skipped_display_only=3,
        )
        calls = _stub_importer(monkeypatch, result=result)
        zip_path = tmp_path / "jpdb.zip"
        zip_path.write_bytes(b"")
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        out = tab._resolve_frequency_path()

        assert out == freq_home / "frequency.csv"
        # The import STAGES to a .pending sibling — the real CSV is untouched and
        # the selector/dialog are deferred until both imports commit (T-10).
        assert calls and calls[0] == (zip_path, freq_home / "frequency.csv.pending")
        assert captured["information"] == []
        assert (freq_home / "frequency.csv.pending").exists()
        assert not (freq_home / "frequency.csv").exists()

        # Promotion happens on commit: .pending -> final, selector + dialog.
        tab._commit_pending_csv_imports()
        assert (freq_home / "frequency.csv").exists()
        assert not (freq_home / "frequency.csv.pending").exists()
        assert tab.filtering_panel.frequency_selector.get_path() == str(freq_home / "frequency.csv")
        assert len(captured["information"]) == 1
        assert "JPDB" in captured["information"][0][1]
        assert "skipped 3" in captured["information"][0][1]

    def test_failed_import_shows_warning_and_returns_none(self, tab, tmp_path, monkeypatch):
        captured = _capture_messagebox(monkeypatch)
        _stub_importer(monkeypatch, error=SetupError("Invalid index.json: bad"))
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"")
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        out = tab._resolve_frequency_path()

        assert out is None
        assert len(captured["warning"]) == 1
        assert "Invalid index.json" in captured["warning"][0][1]

    def test_cancelled_import_returns_none_without_warning(self, tab, tmp_path, monkeypatch):
        captured = _capture_messagebox(monkeypatch)
        _stub_importer(monkeypatch, error=SetupError("Import cancelled"))
        zip_path = tmp_path / "freq.zip"
        zip_path.write_bytes(b"")
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        out = tab._resolve_frequency_path()

        assert out is None
        # Cancellation is user-initiated — no error dialog.
        assert captured["warning"] == []


class TestNoReimportOnSecondSave:
    def test_after_import_selector_path_is_csv(self, tab, tmp_path, freq_home, monkeypatch):
        _capture_messagebox(monkeypatch)
        result = YomitanFreqImportResult(
            source_name="Test",
            source_revision="v1",
            entry_count=1,
            skipped_display_only=0,
        )
        _stub_importer(monkeypatch, result=result)
        zip_path = tmp_path / "freq.zip"
        zip_path.write_bytes(b"")
        tab.filtering_panel.frequency_selector.set_path(str(zip_path))

        tab._resolve_frequency_path()
        # Commit promotes the staged import and updates the selector to the CSV.
        tab._commit_pending_csv_imports()

        # Second save: selector now points at CSV; passthrough branch taken.
        # Replace the stub with one that asserts it's not called.
        def must_not_run(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("importer should not run on second save")

        monkeypatch.setattr(
            "anki_miner.gui.workers.frequency_import_worker.import_yomitan_freq_zip",
            must_not_run,
        )
        out = tab._resolve_frequency_path()
        assert out == Path(str(freq_home / "frequency.csv"))
