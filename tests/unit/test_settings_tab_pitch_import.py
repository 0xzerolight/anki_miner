"""Tests for SettingsTab._resolve_pitch_accent_path — Yomitan pitch-zip save hook.

Covers the four branches the GUI hook has to get right: no-path, CSV passthrough,
zip-with-overwrite-decline, and the full worker-driven import (success / failure /
cancel). The real ``YomitanCsvImportWorker`` is used end-to-end (a real QThread
runs); only the inner ``import_yomitan_pitch_zip`` call is stubbed so we control
the outcome deterministically.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.pitch_accent import YomitanPitchImportResult


@pytest.fixture
def pitch_home(tmp_path, monkeypatch):
    """Redirect ANKI_MINER_HOME to a tmp dir so the importer doesn't touch ~."""
    home = tmp_path / "anki_miner_home"
    home.mkdir()
    monkeypatch.setattr("anki_miner.gui.controllers.zip_import_flow.ANKI_MINER_HOME", home)
    return home


@pytest.fixture
def tab(test_config: AnkiMinerConfig, pitch_home: Path, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    # _on_save_clicked reconciles styling, spawning a short-lived AnkiConnect
    # worker; join it and flush queued signals so a late status update can't fire
    # into a torn-down QLabel. Mirrors closeEvent.
    widget.shutdown()
    for w in widget.iter_close_workers():
        if w is not None:
            w.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
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
    """Replace import_yomitan_pitch_zip inside the worker with a controllable stub."""
    calls: list[tuple[Path, Path]] = []

    def fake(zip_path, dest_csv, *, progress=None, cancel_check=None):
        calls.append((zip_path, dest_csv))
        if progress is not None:
            progress(1, 1, "Imported test_meta_bank_1.json")
        if error is not None:
            raise error
        # Materialize the CSV so dest_csv.exists() flips to True for next-save tests.
        dest_csv.write_text("reading,kanji,pattern\nネコ,猫,0\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.import_yomitan_pitch_zip",
        fake,
    )
    return calls


class TestPassthroughBranches:
    def test_cleared_selector_keeps_current_path(self, tab, monkeypatch):
        # A cleared selector means "no change" — keep the current pitch path,
        # never round-trip through Path("") (which persists PosixPath('.')).
        _capture_messagebox(monkeypatch)
        calls = _stub_importer(monkeypatch)
        tab.dictionary_panel.pitch_accent_selector.set_path("")

        out = tab._resolve_pitch_accent_path()

        assert out == tab.config.pitch_accent_path
        assert out != Path(".")
        assert calls == []

    def test_cleared_selector_commit_does_not_persist_dot(self, tab, monkeypatch):
        # End-to-end: clearing the selector and committing keeps the prior path
        # instead of persisting the literal "." into config.
        _capture_messagebox(monkeypatch)
        _stub_importer(monkeypatch)
        prior = tab.config.pitch_accent_path
        tab.dictionary_panel.pitch_accent_selector.set_path("")

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.commit_settings()

        assert len(received) == 1
        assert received[0].pitch_accent_path == prior
        assert received[0].pitch_accent_path != Path(".")

    def test_csv_path_returns_unchanged(self, tab, tmp_path, monkeypatch):
        _capture_messagebox(monkeypatch)
        calls = _stub_importer(monkeypatch)
        csv = tmp_path / "pitch.csv"
        csv.write_text("reading,kanji,pattern\nネコ,猫,0\n", encoding="utf-8")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(csv))

        out = tab._resolve_pitch_accent_path()

        assert out == Path(str(csv))
        assert calls == []


class TestOverwritePrompt:
    def test_existing_nonempty_csv_prompts_and_user_declines(self, tab, tmp_path, pitch_home, monkeypatch):
        # Seed an existing pitch_accent.csv.
        existing = pitch_home / "pitch_accent.csv"
        existing.write_text("reading,kanji,pattern\nフー,foo,0\n", encoding="utf-8")
        # Pin config's pitch_accent_path so we can verify the decline-return value.
        prior = pitch_home / "previous.csv"
        prior.write_text("reading,kanji,pattern\nプライア,prior,0\n", encoding="utf-8")
        tab.config = replace(tab.config, pitch_accent_path=prior)

        captured = _capture_messagebox(monkeypatch, default_question_reply=QMessageBox.StandardButton.No)
        calls = _stub_importer(monkeypatch)
        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"")  # contents irrelevant — importer is stubbed
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out == prior  # rest of save proceeds with the prior path
        assert calls == []  # importer never ran
        assert len(captured["question"]) == 1
        assert "Overwrite" in captured["question"][0][0]

    def test_existing_empty_csv_does_not_prompt(self, tab, tmp_path, pitch_home, monkeypatch):
        existing = pitch_home / "pitch_accent.csv"
        existing.write_text("", encoding="utf-8")  # zero bytes

        captured = _capture_messagebox(monkeypatch)
        calls = _stub_importer(
            monkeypatch,
            result=YomitanPitchImportResult(
                source_name="Test",
                source_revision="v1",
                entry_count=1,
                skipped_display_only=0,
            ),
        )
        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out == pitch_home / "pitch_accent.csv"
        assert calls and calls[0][0] == zip_path
        assert captured["question"] == []  # no overwrite prompt on empty file


class TestWorkerOutcomes:
    def test_successful_import_updates_selector_and_returns_csv(self, tab, tmp_path, pitch_home, monkeypatch):
        captured = _capture_messagebox(monkeypatch)
        result = YomitanPitchImportResult(
            source_name="Kanjium",
            source_revision="2024-01",
            entry_count=42,
            skipped_display_only=3,
        )
        calls = _stub_importer(monkeypatch, result=result)
        zip_path = tmp_path / "kanjium.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out == pitch_home / "pitch_accent.csv"
        # The import STAGES to a .pending sibling — the real CSV is untouched and
        # the selector/dialog are deferred until both imports commit (T-10).
        assert calls and calls[0] == (zip_path, pitch_home / "pitch_accent.csv.pending")
        assert captured["information"] == []
        assert (pitch_home / "pitch_accent.csv.pending").exists()
        assert not (pitch_home / "pitch_accent.csv").exists()

        # Promotion happens on commit: .pending -> final, selector + dialog.
        tab._commit_pending_csv_imports()
        assert (pitch_home / "pitch_accent.csv").exists()
        assert not (pitch_home / "pitch_accent.csv.pending").exists()
        assert tab.dictionary_panel.pitch_accent_selector.get_path() == str(pitch_home / "pitch_accent.csv")
        assert len(captured["information"]) == 1
        assert "Kanjium" in captured["information"][0][1]
        assert "skipped 3" in captured["information"][0][1]

    def test_failed_import_shows_warning_and_returns_none(self, tab, tmp_path, monkeypatch):
        captured = _capture_messagebox(monkeypatch)
        _stub_importer(monkeypatch, error=SetupError("Invalid index.json: bad"))
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out is None
        assert len(captured["warning"]) == 1
        assert "Invalid index.json" in captured["warning"][0][1]

    def test_real_cancellation_returns_none_without_warning(self, tab, tmp_path, monkeypatch):
        # An ACTUAL user cancel (worker cancelled → importer aborts on its
        # cancel_check) is silent — routed through the worker's distinct
        # ``cancelled`` signal, not inferred from the error text.
        captured = _capture_messagebox(monkeypatch)

        def fake(zip_path, dest_csv, *, progress=None, cancel_check=None):
            if cancel_check and cancel_check():
                raise SetupError("Import cancelled")
            dest_csv.write_text("reading,kanji,pattern\nネコ,猫,0\n", encoding="utf-8")
            return YomitanPitchImportResult(
                source_name="X", source_revision="v1", entry_count=1, skipped_display_only=0
            )

        monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.import_yomitan_pitch_zip", fake)

        # Pre-cancel the real worker so the importer aborts on its first check.
        from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker as _RealWorker

        def precancel(import_fn, zip_path, dest_csv, parent=None):
            worker = _RealWorker(import_fn, zip_path, dest_csv, parent)
            worker.cancel()
            return worker

        monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.YomitanCsvImportWorker", precancel)

        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out is None
        assert captured["warning"] == []

    def test_error_containing_word_cancel_still_shows_warning(self, tab, tmp_path, monkeypatch):
        # A genuine failure whose message merely CONTAINS "cancel" (the worker
        # was NOT cancelled) must still surface the error dialog.
        captured = _capture_messagebox(monkeypatch)
        _stub_importer(monkeypatch, error=SetupError("download cancelled: connection reset"))
        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        out = tab._resolve_pitch_accent_path()

        assert out is None
        assert len(captured["warning"]) == 1
        assert "cancelled" in captured["warning"][0][1]


class TestNoReimportOnSecondSave:
    def test_after_import_selector_path_is_csv(self, tab, tmp_path, pitch_home, monkeypatch):
        _capture_messagebox(monkeypatch)
        result = YomitanPitchImportResult(
            source_name="Test",
            source_revision="v1",
            entry_count=1,
            skipped_display_only=0,
        )
        _stub_importer(monkeypatch, result=result)
        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"")
        tab.dictionary_panel.pitch_accent_selector.set_path(str(zip_path))

        tab._resolve_pitch_accent_path()
        # Commit promotes the staged import and updates the selector to the CSV.
        tab._commit_pending_csv_imports()

        # Second save: selector now points at CSV; passthrough branch taken.
        # Replace the stub with one that asserts it's not called.
        def must_not_run(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("importer should not run on second save")

        monkeypatch.setattr(
            "anki_miner.gui.widgets.settings_tab.import_yomitan_pitch_zip",
            must_not_run,
        )
        out = tab._resolve_pitch_accent_path()
        assert out == Path(str(pitch_home / "pitch_accent.csv"))


class TestPitchSaveStillAbortsOnFailure:
    """A failing pitch import must keep the last-good pitch path (the rest of
    the auto-save commit still goes through), re-sync the selector so the next
    commit can't silently retry the import, and leave the user's existing
    pitch_accent.csv byte-for-byte untouched (frequency no longer participates
    in this staged-import flow)."""

    def test_pitch_failure_does_not_overwrite_existing_pitch_csv(self, tab, tmp_path, pitch_home, monkeypatch):
        captured = _capture_messagebox(monkeypatch)

        # Seed an existing pitch_accent.csv whose bytes must survive the failed import.
        original_pitch = "reading,kanji,pattern\nオリジナル,original,0\n"
        existing_pitch = pitch_home / "pitch_accent.csv"
        existing_pitch.write_text(original_pitch, encoding="utf-8")

        # Pitch importer FAILS — the commit must keep the last-good path.
        def fake_pitch(zip_path, dest_csv, *, progress=None, cancel_check=None):
            raise SetupError("pitch zip is broken")

        monkeypatch.setattr("anki_miner.gui.widgets.settings_tab.import_yomitan_pitch_zip", fake_pitch)

        pitch_zip = tmp_path / "pitch.zip"
        pitch_zip.write_bytes(b"")
        # The overwrite guard would otherwise prompt for the existing pitch CSV;
        # _capture_messagebox answers Yes by default so the import proceeds.
        tab.config = replace(tab.config, pitch_accent_path=existing_pitch)
        tab.dictionary_panel.pitch_accent_selector.set_path(str(pitch_zip))

        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)

        tab.commit_settings()

        # Commit went through with the LAST-GOOD pitch path; failure surfaced.
        assert len(received) == 1, "the commit must still go through on pitch failure"
        assert received[0].pitch_accent_path == existing_pitch
        assert any("Pitch" in title for title, _ in captured["warning"])
        # Selector re-synced off the failed .zip so the next commit can't
        # silently retry the import.
        assert tab.dictionary_panel.pitch_accent_selector.get_path() == str(existing_pitch)
        # The existing pitch_accent.csv is byte-for-byte untouched.
        assert existing_pitch.read_text(encoding="utf-8") == original_pitch
        # No staging file is left behind.
        assert not (pitch_home / "pitch_accent.csv.pending").exists()
