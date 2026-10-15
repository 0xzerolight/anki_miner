"""WP-06 (ruling R10): Manage Known Words lists every source, filters, and removes only your words."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import QMessageBox, QTableView

from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
from anki_miner.services.known_word_db import KnownWordDB


def _db(tmp_path) -> KnownWordDB:
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    db.add_words({"食べる", "飲む"}, source="anki")
    db.add_words({"走る"}, source="mined")
    db.add_words({"カレー", "ラーメン"}, source="user")
    return db


def _dialog(qtbot, tmp_path) -> KnownWordsManagerDialog:
    dlg = KnownWordsManagerDialog(_db(tmp_path))
    qtbot.addWidget(dlg)
    return dlg


def _show(dlg: KnownWordsManagerDialog, source: str) -> None:
    dlg.source_filter.setCurrentIndex(dlg.source_filter.findData(source))


def _cells(dlg: KnownWordsManagerDialog) -> dict[str, tuple[str, str]]:
    proxy = dlg.word_table.model()
    return {
        proxy.index(row, 0).data(): (proxy.index(row, 1).data(), proxy.index(row, 2).data())
        for row in range(proxy.rowCount())
    }


def _select(dlg: KnownWordsManagerDialog, word: str) -> None:
    proxy = dlg.word_table.model()
    for row in range(proxy.rowCount()):
        if proxy.index(row, 0).data() == word:
            dlg.word_table.selectRow(row)
            return
    raise AssertionError(f"{word!r} is not shown")


def test_it_is_a_model_backed_table(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)

    assert isinstance(dlg.word_table, QTableView)
    assert isinstance(dlg.word_table.model(), QSortFilterProxyModel)
    assert not hasattr(dlg, "word_list")


def test_it_opens_on_the_words_you_added(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)

    assert dlg.source_filter.currentData() == "user"
    assert [row.lemma for row in dlg.visible_rows()] == ["カレー", "ラーメン"]


def test_all_sources_lists_every_row_with_its_source_and_date(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)

    _show(dlg, "")

    cells = _cells(dlg)
    assert set(cells) == {"食べる", "飲む", "走る", "カレー", "ラーメン"}
    assert cells["カレー"][0] == "Added by you"
    assert cells["走る"][0] == "Mined by Anki Miner"
    assert cells["食べる"][0] == "From Anki"
    assert all(len(date) == 10 and date[4] == "-" for _source, date in cells.values())


def test_the_headers_name_the_columns(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    proxy = dlg.word_table.model()

    headers = [proxy.headerData(column, Qt.Orientation.Horizontal) for column in range(3)]

    assert headers == ["Word", "Source", "Added"]


def test_the_source_filter_and_the_search_combine(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)

    _show(dlg, "anki")
    assert sorted(row.lemma for row in dlg.visible_rows()) == ["食べる", "飲む"]

    dlg.search_input.setText("飲")
    assert [row.lemma for row in dlg.visible_rows()] == ["飲む"]


def test_a_row_from_anki_cannot_be_removed(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    _show(dlg, "")

    _select(dlg, "食べる")

    assert not dlg.remove_button.isEnabled()
    assert dlg.remove_button.toolTip()
    dlg._on_remove()
    assert "食べる" in dlg._db.get_known_words()


def test_a_word_you_added_is_removed(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    _show(dlg, "")

    _select(dlg, "カレー")
    assert dlg.remove_button.isEnabled()
    dlg._on_remove()

    assert "カレー" not in dlg._db.get_known_words()
    assert "カレー" not in _cells(dlg)
    assert "食べる" in _cells(dlg)


def test_reset_counts_your_words_not_the_rows_on_screen(qtbot, tmp_path, monkeypatch):
    """Reset clears only user rows, so it asks whenever you have any, whatever the filter shows."""
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    dlg = _dialog(qtbot, tmp_path)
    _show(dlg, "anki")

    dlg._on_reset()

    assert len(asked) == 1
    assert dlg._db.get_words_by_source("user") == set()
    assert {"食べる", "飲む", "走る"} <= dlg._db.get_known_words()


def test_reset_with_no_words_of_yours_does_not_ask(qtbot, tmp_path, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    db = _db(tmp_path)
    db.clear_user()
    dlg = KnownWordsManagerDialog(db)
    qtbot.addWidget(dlg)
    _show(dlg, "")

    dlg._on_reset()

    assert asked == []


def test_copy_lifts_the_full_timestamp(qtbot, tmp_path):
    from anki_miner.gui.utils.qt_helpers import COPY_ROLE

    dlg = _dialog(qtbot, tmp_path)
    proxy = dlg.word_table.model()

    assert len(proxy.index(0, 2).data(COPY_ROLE)) == 19


import anki_miner.gui.widgets.dialogs.known_words_dialog as known_words_dialog_mod  # noqa: E402


def test_the_button_says_export_words(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)

    assert dlg.export_button.text() == "Export words…"


def test_csv_export_writes_every_visible_row(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    _show(dlg, "")
    out = tmp_path / "words.csv"

    assert dlg.export_to(out) == 5

    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "word,source,added_at_utc"
    assert sorted(line.split(",")[0] for line in lines[1:]) == sorted(["食べる", "飲む", "走る", "カレー", "ラーメン"])
    assert {line.split(",")[1] for line in lines[1:]} == {"anki", "mined", "user"}


def test_txt_export_follows_the_source_filter(qtbot, tmp_path):
    dlg = _dialog(qtbot, tmp_path)
    _show(dlg, "anki")
    out = tmp_path / "words.txt"

    dlg.export_to(out)

    assert sorted(out.read_text(encoding="utf-8").splitlines()) == ["食べる", "飲む"]


def test_the_worker_writes_the_rows_that_were_on_screen_when_asked(qtbot, tmp_path, monkeypatch):
    dlg = _dialog(qtbot, tmp_path)
    out = tmp_path / "words.csv"
    captured: dict[str, object] = {}

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        captured["work"] = work

    monkeypatch.setattr(known_words_dialog_mod, "run_off_thread", fake_run_off_thread)
    monkeypatch.setattr(
        known_words_dialog_mod.file_dialogs, "pick_save_file", lambda *args, on_done, **kwargs: on_done(str(out))
    )

    dlg._on_export()  # the browser shows "Added by you"
    _show(dlg, "anki")  # changed before the worker runs
    captured["work"]()

    words = [line.split(",")[0] for line in out.read_text(encoding="utf-8-sig").splitlines()[1:]]
    assert sorted(words) == ["カレー", "ラーメン"]


def test_the_save_picker_suggests_a_csv(qtbot, tmp_path, monkeypatch):
    dlg = _dialog(qtbot, tmp_path)
    seen: dict[str, str] = {}

    def pick(parent, title, initial, file_filter, *args, on_done, **kwargs):
        seen["initial"] = initial
        seen["filter"] = file_filter
        on_done("")

    monkeypatch.setattr(known_words_dialog_mod.file_dialogs, "pick_save_file", pick)
    dlg._on_export()

    assert seen["initial"].endswith("known_words.csv")
    assert "*.csv" in seen["filter"]
    assert "*.txt" in seen["filter"]
