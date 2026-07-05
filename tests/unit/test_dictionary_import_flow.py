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


# --- catalog-slot pinned re-import guard --------------------------------------

from anki_miner.services.dictionary.storage import create_index, write_meta  # noqa: E402
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip  # noqa: E402


def _seed_slot(dicts_root: Path, dict_id: str, source_name: str) -> None:
    db = dicts_root / dict_id / "index.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    create_index(db)
    write_meta(db, {"source_name": source_name})


class TestCatalogSlotBaseMatches:
    def test_matches_same_base_newer_date(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        _seed_slot(tmp_path / "dicts", "jitendex", "Jitendex.org [2025-11-05]")
        fresh = build_yomitan_zip(tmp_path / "src" / "j.zip", title="Jitendex.org [2026-06-06]")
        assert flow._catalog_slot_base_matches("jitendex", fresh) is True

    def test_rejects_different_base(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        _seed_slot(tmp_path / "dicts", "jitendex", "Jitendex.org [2025-11-05]")
        wrong = build_yomitan_zip(tmp_path / "src" / "d.zip", title="Daijirin [2026-01-01]")
        assert flow._catalog_slot_base_matches("jitendex", wrong) is False

    def test_rejects_when_slot_not_on_disk(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        fresh = build_yomitan_zip(tmp_path / "src" / "j.zip", title="Jitendex.org [2026-06-06]")
        assert flow._catalog_slot_base_matches("jitendex", fresh) is False


class TestReimportDictCatalogGuard:
    def test_catalog_slot_accepts_fresh_same_base_zip(self, tmp_path: Path):
        dicts_root = tmp_path / "dicts"
        flow = _make_flow(dicts_root)
        # Block right after the guard so no real worker/QThread runs.
        flow._panel.request_resource_release.return_value = False
        _seed_slot(dicts_root, "jitendex", "Jitendex.org [2025-11-05]")
        fresh = build_yomitan_zip(tmp_path / "src" / "j.zip", title="Jitendex.org [2026-06-06]")

        with (
            patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=(str(fresh), "")),
            patch(f"{MOD}.QMessageBox.warning") as warn,
        ):
            flow.reimport_dict("jitendex")

        # Guard passed → release was attempted (then blocked). The one warning is
        # the release-block, NOT a "does not match slot" abort.
        flow._panel.request_resource_release.assert_called_once()
        assert warn.call_count == 1
        assert "match" not in warn.call_args.args[1].lower()

    def test_catalog_slot_rejects_wrong_base_zip(self, tmp_path: Path):
        dicts_root = tmp_path / "dicts"
        flow = _make_flow(dicts_root)
        _seed_slot(dicts_root, "jitendex", "Jitendex.org [2025-11-05]")
        wrong = build_yomitan_zip(tmp_path / "src" / "d.zip", title="Daijirin [2026-01-01]")

        with (
            patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=(str(wrong), "")),
            patch(f"{MOD}.QMessageBox.warning") as warn,
        ):
            flow.reimport_dict("jitendex")

        # Aborted at the guard: mismatch warning shown, handles never released.
        warn.assert_called_once()
        assert "match" in warn.call_args.args[1].lower()
        flow._panel.request_resource_release.assert_not_called()

    def test_non_catalog_slot_still_rejects_mismatch(self, tmp_path: Path):
        dicts_root = tmp_path / "dicts"
        flow = _make_flow(dicts_root)
        other = build_yomitan_zip(tmp_path / "src" / "o.zip", title="Other Dict", revision="v1")

        with (
            patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=(str(other), "")),
            patch(f"{MOD}.QMessageBox.warning") as warn,
        ):
            flow.reimport_dict("some-slot")  # not a catalog slot

        warn.assert_called_once()
        assert "match" in warn.call_args.args[1].lower()
        flow._panel.request_resource_release.assert_not_called()
