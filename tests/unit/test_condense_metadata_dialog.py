"""Tests for the condense metadata editor dialog (Issue #113)."""

import pytest

pytest.importorskip("PyQt6.QtCore")

from PIL import Image
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.condense_metadata_dialog import CondenseMetadataDialog
from anki_miner.services.audio_tagger import TrackMetadata

PREFILL = [
    TrackMetadata(title="Ep One", album="Season 01", artist="Show", track=1),
    TrackMetadata(title="Ep Two", album="Season 01", artist="Show", track=2),
]
FILES = ["e1.mkv", "e2.mkv"]


def _dialog(qtbot, prefill=PREFILL, files=FILES) -> CondenseMetadataDialog:
    dialog = CondenseMetadataDialog(files, prefill)
    qtbot.addWidget(dialog)
    return dialog


def test_prefill_lands_in_cells(qtbot):
    dialog = _dialog(qtbot)
    table = dialog.table
    assert table.rowCount() == 2
    assert table.item(0, CondenseMetadataDialog.COL_FILE).text() == "e1.mkv"
    assert table.item(0, CondenseMetadataDialog.COL_TRACK).text() == "1"
    assert table.item(1, CondenseMetadataDialog.COL_TITLE).text() == "Ep Two"
    # File column is read-only; tag columns are editable.
    assert not table.item(0, CondenseMetadataDialog.COL_FILE).flags() & Qt.ItemFlag.ItemIsEditable
    assert table.item(0, CondenseMetadataDialog.COL_TITLE).flags() & Qt.ItemFlag.ItemIsEditable


def test_metadata_reflects_edits_and_globals(qtbot):
    dialog = _dialog(qtbot)
    dialog.table.item(0, CondenseMetadataDialog.COL_TITLE).setText("Renamed")
    dialog.genre_edit.setText("Anime")
    metas = dialog.metadata()
    assert metas[0].title == "Renamed"
    assert metas[1].title == "Ep Two"
    assert all(m.genre == "Anime" for m in metas)


def test_genre_defaults_to_condensed_audio(qtbot):
    assert _dialog(qtbot).genre_edit.text() == "Condensed Audio"


def test_apply_artist_to_all(qtbot):
    dialog = _dialog(qtbot)
    dialog.artist_edit.setText("New Artist")
    dialog.apply_artist_button.click()
    assert all(m.artist == "New Artist" for m in dialog.metadata())


def test_track_parse_lenient(qtbot):
    dialog = _dialog(qtbot)
    dialog.table.item(0, CondenseMetadataDialog.COL_TRACK).setText("  7 ")
    dialog.table.item(1, CondenseMetadataDialog.COL_TRACK).setText("junk")
    metas = dialog.metadata()
    assert metas[0].track == 7
    assert metas[1].track is None


def test_artwork_folded_into_rows(qtbot, tmp_path):
    art = tmp_path / "cover.png"
    Image.new("RGB", (64, 64)).save(art)
    dialog = _dialog(qtbot)
    dialog.artwork_selector.set_path(str(art))
    assert all(m.artwork_path == art for m in dialog.metadata())


def test_missing_artwork_file_is_none(qtbot, tmp_path):
    dialog = _dialog(qtbot)
    dialog.artwork_selector.set_path(str(tmp_path / "gone.png"))
    assert all(m.artwork_path is None for m in dialog.metadata())


def test_no_artwork_is_none(qtbot):
    assert all(m.artwork_path is None for m in _dialog(qtbot).metadata())
