"""Tests for KnownWordsManagerDialog (Issue #42)."""

from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
from anki_miner.services.known_word_db import KnownWordDB


def _db_with_user_words(tmp_path, user=("ラーメン", "カレー"), anki=("食べる",)):
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    if anki:
        db.add_words(set(anki), source="anki")
    if user:
        db.add_words(set(user), source="user")
    return db


class TestPopulation:
    def test_lists_only_user_words_sorted(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        shown = [dlg.word_list.item(r).text() for r in range(dlg.word_list.count())]
        assert shown == sorted({"ラーメン", "カレー"})

    def test_count_label_splits_user_and_cached(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        text = dlg.count_label.text()
        assert "2 user word(s)" in text
        assert "1 cached from Anki" in text


class TestExport:
    def test_export_writes_one_word_per_line(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("寿司", "天ぷら"))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        out = tmp_path / "export.txt"
        count = dlg.export_to(out)
        assert count == 2
        assert out.read_text(encoding="utf-8").splitlines() == sorted({"寿司", "天ぷら"})

    def test_export_excludes_anki_cache(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("寿司",), anki=("食べる", "飲む"))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        out = tmp_path / "export.txt"
        dlg.export_to(out)
        assert out.read_text(encoding="utf-8").splitlines() == ["寿司"]

    def test_export_empty_list(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        out = tmp_path / "export.txt"
        assert dlg.export_to(out) == 0
        assert out.read_text(encoding="utf-8") == ""


class TestRemove:
    def test_remove_selected_deletes_and_refreshes(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("ラーメン", "カレー", "寿司"))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        # Select the row showing カレー.
        for r in range(dlg.word_list.count()):
            if dlg.word_list.item(r).text() == "カレー":
                dlg.word_list.item(r).setSelected(True)
        dlg._on_remove()
        assert db.get_words_by_source("user") == {"ラーメン", "寿司"}
        shown = [dlg.word_list.item(r).text() for r in range(dlg.word_list.count())]
        assert "カレー" not in shown

    def test_remove_no_selection_is_noop(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("ラーメン",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        dlg._on_remove()
        assert db.get_words_by_source("user") == {"ラーメン"}


class TestReset:
    def test_reset_clears_only_user_words(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("ラーメン", "カレー"), anki=("食べる",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        db.clear_user()  # exercise the underlying op the slot calls after confirm
        dlg._refresh()
        assert db.get_words_by_source("user") == set()
        assert db.get_known_words() == {"食べる"}
        assert dlg.word_list.count() == 0


class TestSearch:
    def test_filter_hides_non_matching(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("ラーメン", "カレー"))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        dlg.search_input.setText("カレー")
        hidden = {dlg.word_list.item(r).text(): dlg.word_list.item(r).isHidden() for r in range(dlg.word_list.count())}
        assert hidden["カレー"] is False
        assert hidden["ラーメン"] is True
