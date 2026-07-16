"""Tests for KnownWordsManagerDialog (Issue #42)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import anki_miner.gui.widgets.dialogs.known_words_dialog as known_words_dialog_mod
from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.known_words_import import (
    FORMAT_KEYS,
    KnownWordsImportError,
    KnownWordsImportResult,
)


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


class TestExportDialogStartDir:
    def test_on_export_opens_save_dialog_at_home(self, qtbot, tmp_path, monkeypatch):
        """_on_export must pass a path under home as the initial path arg to getSaveFileName."""
        db = _db_with_user_words(tmp_path, user=("ラーメン",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        captured: dict = {}

        def fake_save(parent, title, initial_path, file_filter, *a, **kw):
            captured["initial"] = initial_path
            return ("", "")  # user cancels

        monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)
        dlg._on_export()

        home = str(Path.home())
        initial = captured.get("initial", "")
        assert initial.startswith(home), f"Expected initial path under home={home!r}; got {initial!r}"
        assert initial != "", "initial path must not be empty"
        assert "known_words.txt" in initial, f"suggested filename must be preserved; got {initial!r}"


# ----------------------------------------------------------------------
# Import…
# ----------------------------------------------------------------------


def _import_result(words, *, format_key="generic", total=None):
    return KnownWordsImportResult(
        format_key=format_key,
        words=frozenset(words),
        total_entries=total if total is not None else len(words),
        is_generic=format_key == "generic",
    )


class TestApplyImport:
    def test_adds_new_words_as_user_source(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        added, already = dlg.apply_import(_import_result({"寿司", "天ぷら"}))
        assert (added, already) == (2, 0)
        assert db.get_words_by_source("user") == {"寿司", "天ぷら"}

    def test_existing_user_words_count_as_already(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("寿司",), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        added, already = dlg.apply_import(_import_result({"寿司", "天ぷら"}))
        assert (added, already) == (1, 1)

    def test_anki_to_user_upgrade_counts_as_added(self, qtbot, tmp_path):
        # A word cached from Anki was NOT in the user list — importing it is an
        # upgrade (survives cache rebuild) and must be reported as added.
        db = _db_with_user_words(tmp_path, user=(), anki=("食べる",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        added, already = dlg.apply_import(_import_result({"食べる"}))
        assert (added, already) == (1, 0)
        assert db.get_words_by_source("user") == {"食べる"}


@pytest.fixture
def capture_off_thread(monkeypatch):
    """Replace run_off_thread with a capturing stub (no real worker thread)."""
    captured: dict = {}

    def fake(parent, work, on_done, on_error=None, *, error_prefix=""):
        captured["parent"] = parent
        captured["work"] = work
        captured["on_done"] = on_done
        captured["on_error"] = on_error
        return MagicMock()

    monkeypatch.setattr(known_words_dialog_mod, "run_off_thread", fake)
    return captured


@pytest.fixture
def message_boxes(monkeypatch):
    """Capture QMessageBox usage; question answers Yes unless overridden."""
    boxes: dict = {"questions": [], "infos": [], "warnings": [], "answer": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: boxes["questions"].append((a[1], a[2])) or boxes["answer"]
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: boxes["infos"].append((a[1], a[2])) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: boxes["warnings"].append((a[1], a[2])) or QMessageBox.StandardButton.Ok
    )
    return boxes


def _start_import(dlg, monkeypatch, path="/fake/words.txt"):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    dlg._on_import()


class TestImportSlot:
    def test_happy_path_confirms_inserts_and_refreshes(
        self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes
    ):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        assert dlg.import_button.isEnabled() is False, "button must disable while parsing"

        capture_off_thread["on_done"](_import_result({"寿司", "天ぷら"}, format_key="migaku_csv"))

        assert message_boxes["questions"], "confirm dialog must be shown"
        assert db.get_words_by_source("user") == {"寿司", "天ぷら"}
        shown = [dlg.word_list.item(r).text() for r in range(dlg.word_list.count())]
        assert sorted(shown) == sorted({"寿司", "天ぷら"})
        assert message_boxes["infos"], "success info must be shown"
        assert dlg.import_button.isEnabled() is True

    def test_generic_confirm_flags_missing_status(
        self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes
    ):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        capture_off_thread["on_done"](_import_result({"寿司"}, format_key="generic"))
        generic_text = message_boxes["questions"][-1][1]

        _start_import(dlg, monkeypatch)
        capture_off_thread["on_done"](_import_result({"寿司"}, format_key="migaku_csv"))
        structured_text = message_boxes["questions"][-1][1]

        assert generic_text != structured_text, "generic import must warn about the missing status data"

    def test_confirm_no_leaves_db_untouched(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        message_boxes["answer"] = QMessageBox.StandardButton.No

        _start_import(dlg, monkeypatch)
        capture_off_thread["on_done"](_import_result({"寿司"}))

        assert db.get_words_by_source("user") == set()
        assert not message_boxes["infos"]
        assert dlg.import_button.isEnabled() is True

    def test_parse_errors_show_reason_specific_warnings(
        self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes
    ):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        seen = []
        for error in (
            KnownWordsImportError("unreadable"),
            KnownWordsImportError("unrecognized"),
            KnownWordsImportError("no_known_words", format_key="migaku_csv"),
        ):
            _start_import(dlg, monkeypatch)
            capture_off_thread["on_done"](error)
            assert dlg.import_button.isEnabled() is True
            seen.append(message_boxes["warnings"][-1][1])

        assert db.get_words_by_source("user") == set()
        assert not message_boxes["questions"]
        assert len(set(seen)) == 3, f"each failure reason needs a distinct message; got {seen!r}"

    def test_unexpected_error_reenables_and_warns(
        self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes
    ):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        capture_off_thread["on_error"]("boom")

        assert message_boxes["warnings"], "unexpected failure must surface a warning"
        assert db.get_words_by_source("user") == set()
        assert dlg.import_button.isEnabled() is True

    def test_cancelled_picker_is_noop(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch, path="")

        assert "work" not in capture_off_thread, "no worker on cancelled picker"
        assert dlg.import_button.isEnabled() is True


class TestFormatLabels:
    def test_every_format_key_has_display_label(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        for key in FORMAT_KEYS:
            label = dlg._format_display_name(key)
            assert label, f"missing display label for format key {key!r}"
            assert label != key, f"raw key leaked for {key!r}"
