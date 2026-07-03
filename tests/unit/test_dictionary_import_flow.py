"""Tests for DictionaryImportFlow dialog start-directory (F12).

The Add/Re-import Yomitan-zip dialogs should open at the dictionaries dir
(``config.dicts_root``) instead of falling back to the home directory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow

MOD = "anki_miner.gui.controllers.dictionary_import_flow"


def _make_flow(dicts_root: Path) -> DictionaryImportFlow:
    cfg = MagicMock()
    cfg.dicts_root = dicts_root
    return DictionaryImportFlow(
        parent=MagicMock(spec=QWidget),
        panel=MagicMock(),
        get_config=lambda: cfg,
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )


def test_add_dict_dialog_defaults_to_dicts_dir():
    dicts_root = Path("/home/u/.anki_miner/dicts")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{MOD}.resolve_start_dir", return_value=str(dicts_root)) as rsd,
        patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=("", "")),
    ):
        flow.add_dict()  # empty selection → early return after the dialog

    rsd.assert_called_once()
    assert rsd.call_args.kwargs.get("default_dir") == dicts_root


def test_reimport_dict_dialog_defaults_to_dicts_dir():
    dicts_root = Path("/home/u/.anki_miner/dicts")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{MOD}.resolve_start_dir", return_value=str(dicts_root)) as rsd,
        patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=("", "")),
    ):
        flow.reimport_dict("some-dict")  # empty selection → early return

    rsd.assert_called_once()
    assert rsd.call_args.kwargs.get("default_dir") == dicts_root


def test_import_notes_empty_when_clean():
    """A clean import contributes no trailing note (plan 4.7/4.8)."""
    flow = _make_flow(Path("/x"))
    assert flow._import_notes({"skipped_malformed": 0, "media_warnings": []}) == ""
    assert flow._import_notes({}) == ""


def test_import_notes_reports_malformed_and_media():
    """Malformed-skip count and media-warning count surface in the note."""
    flow = _make_flow(Path("/x"))
    note = flow._import_notes({"skipped_malformed": 5, "media_warnings": ["w1", "w2"]})
    assert "5" in note
    assert "malformed" in note
    assert "2" in note
    assert "media" in note.lower()


# --- check_for_updates / _show_update_report (plan 9.2) ---------------------

from anki_miner.gui.workers.dictionary_update_check_worker import (  # noqa: E402
    UpdateCheckOutcome,
)
from anki_miner.services.dictionary.updater import UpdateInfo  # noqa: E402


def _capture_info(monkeypatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        f"{MOD}.QMessageBox.information",
        lambda _parent, title, body, *a, **k: captured.append((title, body)),
    )
    return captured


def test_update_report_all_up_to_date(monkeypatch):
    flow = _make_flow(Path("/x"))
    captured = _capture_info(monkeypatch)
    flow._show_update_report([])
    assert len(captured) == 1
    assert "up to date" in captured[0][1].lower()


def test_update_report_lists_updates_and_download_url(monkeypatch):
    flow = _make_flow(Path("/x"))
    captured = _capture_info(monkeypatch)
    outcome = UpdateCheckOutcome(
        display_name="Jitendex",
        dict_id="jitendex-2024",
        info=UpdateInfo("2024.01", "2024.06", "https://jitendex.org/new.zip"),
        error=None,
    )
    flow._show_update_report([outcome])
    body = captured[0][1]
    assert "Jitendex" in body
    assert "2024.01" in body and "2024.06" in body
    assert "https://jitendex.org/new.zip" in body


def test_update_report_lists_errors(monkeypatch):
    flow = _make_flow(Path("/x"))
    captured = _capture_info(monkeypatch)
    outcome = UpdateCheckOutcome("BadDict", "bad", None, "boom")
    flow._show_update_report([outcome])
    body = captured[0][1]
    assert "BadDict" in body and "boom" in body


def test_check_for_updates_no_dicts_shows_info(monkeypatch):
    flow = _make_flow(Path("/x"))
    flow._panel.get_chain.return_value = ()
    captured = _capture_info(monkeypatch)
    # Registry.load() is a disk scan; stub it out (empty dicts_root anyway).
    monkeypatch.setattr(f"{MOD}.DictionaryRegistry.load", lambda self: None)
    flow.check_for_updates()
    assert len(captured) == 1
    assert "No installed dictionaries" in captured[0][1]


def test_check_for_updates_starts_worker_for_installed_dicts(monkeypatch):
    from anki_miner.config import ChainEntry

    flow = _make_flow(Path("/x"))
    flow._panel.get_chain.return_value = (ChainEntry(kind="indexed", dict_id="d1", enabled=True),)

    meta = MagicMock()
    meta.dict_id = "d1"
    meta.source_name = "Dict One"
    meta.db_path = Path("/x/d1/index.sqlite")
    monkeypatch.setattr(f"{MOD}.DictionaryRegistry.load", lambda self: None)
    monkeypatch.setattr(f"{MOD}.DictionaryRegistry.get", lambda self, _id: meta)

    started: list = []

    def _fake_worker(jobs):
        inst = MagicMock(name="DictionaryUpdateCheckWorker")
        inst.jobs = jobs
        inst.start.side_effect = lambda: started.append(jobs)
        return inst

    monkeypatch.setattr(f"{MOD}.DictionaryUpdateCheckWorker", _fake_worker)
    # QProgressDialog would show a real widget; stub it.
    monkeypatch.setattr(f"{MOD}.QProgressDialog", MagicMock())

    flow.check_for_updates()

    assert started == [[("d1", "Dict One", Path("/x/d1/index.sqlite"))]]
    # Import buttons disabled while the check runs.
    flow._panel._check_updates_btn.setEnabled.assert_called_with(False)
