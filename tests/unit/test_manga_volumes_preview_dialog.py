"""Tests for the structural manga-volumes preview dialog.

``MangaVolumesPreviewDialog`` lists the volume(s) a folder resolves to — one
row per ``ReadingSourceRef`` — without loading or tokenizing anything. It is
informational (Close-only): no worker, no cards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.gui.widgets.dialogs.manga_volumes_preview_dialog import MangaVolumesPreviewDialog
from anki_miner.services.reading.models import ReadingSourceRef


def _ref(kind: str = "mokuro", title: str = "Series", volume: str | None = "1", name: str = "vol1.mokuro"):
    return ReadingSourceRef(
        kind=kind,  # type: ignore[arg-type]
        path=Path(f"/manga/{title}/{name}"),
        image_root=None,
        title=title,
        volume=volume,
    )


@pytest.fixture
def make_dialog(qtbot):
    def _make(refs):
        dialog = MangaVolumesPreviewDialog(refs)
        qtbot.addWidget(dialog)
        return dialog

    return _make


class TestRows:
    """The table has one row per ref with title/volume/format/source columns."""

    def test_row_count_matches_refs(self, make_dialog):
        refs = [_ref(title="A", volume="1"), _ref(title="A", volume="2"), _ref(title="A", volume="3")]
        dialog = make_dialog(refs)
        assert dialog.table.rowCount() == 3

    def test_single_volume_one_row(self, make_dialog):
        dialog = make_dialog([_ref()])
        assert dialog.table.rowCount() == 1

    def test_columns_populated(self, make_dialog):
        dialog = make_dialog([_ref(title="MyShow", volume="7", name="v7.mokuro")])
        assert dialog.table.item(0, 0).text() == "MyShow"
        assert dialog.table.item(0, 1).text() == "7"
        assert dialog.table.item(0, 2).text() == "Mokuro"
        assert dialog.table.item(0, 3).text() == "v7.mokuro"

    def test_source_tooltip_is_full_path(self, make_dialog):
        ref = _ref(name="v1.mokuro")
        dialog = make_dialog([ref])
        assert dialog.table.item(0, 3).toolTip() == str(ref.path)

    def test_missing_volume_renders_blank(self, make_dialog):
        dialog = make_dialog([_ref(kind="epub", title="Book", volume=None, name="book.epub")])
        assert dialog.table.item(0, 1).text() == ""
        assert dialog.table.item(0, 2).text() == "EPUB"


class TestTitle:
    """The window title reflects the volume count."""

    def test_title_shows_count(self, make_dialog):
        dialog = make_dialog([_ref(), _ref()])
        assert "2" in dialog.windowTitle()
