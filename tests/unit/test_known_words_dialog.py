"""Tests for KnownWordsManagerDialog (Issue #42)."""

import contextlib
import threading
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

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


def _shown(dlg) -> list[str]:
    """The words the browser shows, top to bottom."""
    return [row.lemma for row in dlg.visible_rows()]


def _select(dlg, word: str) -> None:
    """Select the browser row showing ``word``, and only it."""
    proxy = dlg.word_table.model()
    for row in range(proxy.rowCount()):
        if proxy.index(row, 0).data() == word:
            dlg.word_table.selectRow(row)
            return
    raise AssertionError(f"{word!r} is not shown")


class TestPopulation:
    def test_lists_only_user_words_sorted(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        shown = _shown(dlg)
        assert shown == sorted({"ラーメン", "カレー"})

    def test_count_label_splits_user_and_cached(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        text = dlg.count_label.text()
        assert "User words: 2" in text
        assert "cached from Anki: 1" in text


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
        _select(dlg, "カレー")
        dlg._on_remove()
        assert db.get_words_by_source("user") == {"ラーメン", "寿司"}
        shown = _shown(dlg)
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
        assert dlg.word_table.model().rowCount() == 0


class TestSearch:
    def test_filter_hides_non_matching(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path, user=("ラーメン", "カレー"))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        dlg.search_input.setText("カレー")
        assert _shown(dlg) == ["カレー"]


class TestExportDialogStartDir:
    def test_on_export_opens_save_dialog_at_home(self, qtbot, tmp_path, monkeypatch):
        """_on_export must pass a path under home as the initial path arg to the save picker."""
        db = _db_with_user_words(tmp_path, user=("ラーメン",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        captured: dict = {}

        def fake_save(parent, title, initial_path, file_filter, *a, on_done, **kw):
            captured["initial"] = initial_path
            on_done("")  # user cancels

        monkeypatch.setattr(known_words_dialog_mod.file_dialogs, "pick_save_file", fake_save)
        dlg._on_export()

        home = str(Path.home())
        initial = captured.get("initial", "")
        assert initial.startswith(home), f"Expected initial path under home={home!r}; got {initial!r}"
        assert initial != "", "initial path must not be empty"
        assert "known_words.txt" in initial, f"suggested filename must be preserved; got {initial!r}"

    def test_write_failure_shows_nonmodal_retry(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=("ラーメン",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        dlg.show()
        picks: list[str] = []

        def pick_save(*args, on_done, **kwargs):
            target = str(tmp_path / "known_words.txt")
            picks.append(target)
            on_done(target)

        monkeypatch.setattr(known_words_dialog_mod.file_dialogs, "pick_save_file", pick_save)
        monkeypatch.setattr(dlg, "export_to", MagicMock(side_effect=OSError("disk full")))

        dlg._on_export()
        capture_off_thread["on_error"]("disk full")
        capture_off_thread["on_finished"]()

        issue = dlg.issue_banner().current_issue()
        assert issue is not None
        assert issue.action_id == "known-words.export-retry"
        assert "disk full" in issue.details
        assert dlg.isVisible()
        assert message_boxes["infos"] == []
        dlg.issue_banner().action_button.click()
        assert len(picks) == 2

    @pytest.mark.parametrize("export_error", [False, True], ids=["success", "error"])
    def test_close_suppresses_delayed_real_worker_callbacks(
        self, qtbot, tmp_path, monkeypatch, message_boxes, export_error
    ):
        db = _db_with_user_words(tmp_path, user=("ラーメン",))
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        dlg.show()
        target = tmp_path / "known_words.txt"
        entered = threading.Event()
        release = threading.Event()
        real_export_to = dlg.export_to

        def delayed_export(path):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release export")
            if export_error:
                raise OSError("disk full")
            return real_export_to(path)

        monkeypatch.setattr(dlg, "export_to", delayed_export)
        monkeypatch.setattr(
            known_words_dialog_mod.file_dialogs,
            "pick_save_file",
            lambda *args, on_done, **kwargs: on_done(str(target)),
        )

        dlg._on_export()
        workers = list(dlg._off_thread_workers)
        assert len(workers) == 1
        try:
            assert entered.wait(3)
            dlg.accept()
            release.set()
            assert workers[0].wait(3000)
            qtbot.waitUntil(lambda: not dlg._off_thread_workers, timeout=3000)
        finally:
            release.set()
            for worker in workers:
                with contextlib.suppress(RuntimeError):
                    worker.wait(3000)

        assert not dlg.isVisible()
        assert message_boxes["infos"] == []
        assert dlg.issue_banner().current_issue() is None
        assert dlg.export_button.isEnabled() is False
        assert target.exists() is not export_error


# ----------------------------------------------------------------------
# Import…
# ----------------------------------------------------------------------


def _import_result(words, *, format_key="generic", total=None):
    return KnownWordsImportResult(
        format_key=format_key,
        words=frozenset(words),
        total_entries=total if total is not None else len(words),
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

    def test_a_decomposed_spelling_is_not_reported_as_new(self, qtbot, tmp_path):
        """The parser does not fold; add_words does. Count what gets written.

        がくせい written as か + U+3099 is the same lemma as the stored composed
        form, so importing it adds no row — reporting it as newly added told
        the user something the database did not do.
        """
        nfc = "がくせい"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfd != nfc
        db = _db_with_user_words(tmp_path, user=(nfc,), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        added, already = dlg.apply_import(_import_result({nfd}))

        assert (added, already) == (0, 1)
        assert db.get_words_by_source("user") == {nfc}

    def test_both_spellings_in_one_file_count_once(self, qtbot, tmp_path):
        nfc = "がくせい"
        nfd = unicodedata.normalize("NFD", nfc)
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        added, already = dlg.apply_import(_import_result({nfd, nfc}))

        assert (added, already) == (1, 0)
        assert db.word_count() == 1

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

    def fake(parent, work, on_done, on_error=None, *, error_prefix="", on_finished=None):
        captured["parent"] = parent
        captured["work"] = work
        captured["on_done"] = on_done
        captured["on_error"] = on_error
        captured["on_finished"] = on_finished
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
    monkeypatch.setattr(known_words_dialog_mod.file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(path))
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
        shown = _shown(dlg)
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
            KnownWordsImportError("too_large"),
            KnownWordsImportError("unreadable"),
            KnownWordsImportError("undecodable"),
            KnownWordsImportError("unrecognized"),
            KnownWordsImportError("no_known_words", format_key="migaku_csv"),
        ):
            _start_import(dlg, monkeypatch)
            capture_off_thread["on_done"](error)
            assert dlg.import_button.isEnabled() is True
            seen.append(dlg.issue_banner().current_issue().summary)

        assert db.get_words_by_source("user") == set()
        assert not message_boxes["questions"]
        assert len(set(seen)) == 5, f"each failure reason needs a distinct message; got {seen!r}"
        # The size gate never opens the file and the decode gate read it fine,
        # so neither may claim a read failure.
        assert seen[0] == "That file is too large to import."
        assert seen[1] == "The file could not be read."
        assert seen[2] == "That file's text encoding could not be read."

    def test_unexpected_error_reenables_and_warns(
        self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes
    ):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        capture_off_thread["on_error"]("boom")

        issue = dlg.issue_banner().current_issue()
        assert issue is not None, "unexpected failure must be visible"
        assert "boom" in issue.details
        assert db.get_words_by_source("user") == set()
        assert dlg.import_button.isEnabled() is True

    def test_cancelled_picker_is_noop(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch, path="")

        assert "work" not in capture_off_thread, "no worker on cancelled picker"
        assert dlg.import_button.isEnabled() is True

    def test_close_ignores_late_parse_result(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        dlg.accept()
        capture_off_thread["on_done"](_import_result({"寿司"}))

        assert message_boxes["questions"] == []
        assert message_boxes["infos"] == []
        assert db.get_words_by_source("user") == set()

    def test_close_ignores_late_parse_error(self, qtbot, tmp_path, monkeypatch, capture_off_thread, message_boxes):
        db = _db_with_user_words(tmp_path, user=(), anki=())
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)

        _start_import(dlg, monkeypatch)
        dlg.accept()
        capture_off_thread["on_error"]("boom")

        assert dlg.issue_banner().current_issue() is None


class TestFormatLabels:
    def test_every_format_key_has_display_label(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        for key in FORMAT_KEYS:
            label = dlg._format_display_name(key)
            assert label, f"missing display label for format key {key!r}"
            assert label != key, f"raw key leaked for {key!r}"


class TestJapaneseTypeface:
    """Every entry here is a Japanese word (decision D45-B).

    The Japanese *face* matters because this app also ships Simplified and
    Traditional Chinese interfaces, and those prefer different shapes for the
    same characters. Only the face: a cell font with no size leaves the shared
    row height where the density rule put it.
    """

    def test_entries_use_the_japanese_face(self, qtbot, tmp_path):
        from PyQt6.QtCore import Qt

        from anki_miner.gui.utils.fonts import resolved_families

        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        proxy = dlg.word_table.model()
        assert proxy.rowCount()
        for row in range(proxy.rowCount()):
            font = proxy.index(row, 0).data(Qt.ItemDataRole.FontRole)
            assert font.family() == resolved_families().japanese

    def test_entries_pin_no_size_into_the_row(self, qtbot, tmp_path):
        from PyQt6.QtCore import Qt

        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db)
        qtbot.addWidget(dlg)
        proxy = dlg.word_table.model()
        for row in range(proxy.rowCount()):
            assert proxy.index(row, 0).data(Qt.ItemDataRole.FontRole).pixelSize() == -1


class TestContentStyleFollowsLanguage:
    """No explicit content_style: the fallback follows the dialog's own
    ``language`` param, not a hardcoded ja."""

    def test_default_content_style_follows_the_language_param(self, qtbot, tmp_path):
        from anki_miner.languages.registry import get_profile

        db = _db_with_user_words(tmp_path)
        dlg = KnownWordsManagerDialog(db, language="zh")
        qtbot.addWidget(dlg)
        assert dlg._content_style == get_profile("zh").content_style


class TestImeSafety:
    """D49 — the filter field holds Japanese, so Return must stay text entry."""

    def test_no_default_button_after_show(self, qtbot, tmp_path):
        from PyQt6.QtWidgets import QPushButton

        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        dlg.show()  # Qt promotes a default button from its own show handler
        buttons = dlg.findChildren(QPushButton)
        assert buttons
        assert not any(b.isDefault() or b.autoDefault() for b in buttons)

    def test_return_in_filter_does_not_close_the_manager(self, qtbot, tmp_path):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        dlg.show()
        dlg.search_input.setFocus()
        QTest.keyClick(dlg.search_input, Qt.Key.Key_Return)
        assert dlg.isVisible()
        QTest.keyClick(dlg.search_input, Qt.Key.Key_Enter)  # keypad
        assert dlg.isVisible()


class TestRebuild:
    """C13: Rebuild sits beside the cached-from-Anki count it clears."""

    def test_rebuild_sits_in_the_dialog_and_calls_back(self, qtbot, tmp_path):
        db = _db_with_user_words(tmp_path)
        calls: list[object] = []
        dlg = KnownWordsManagerDialog(db, on_rebuild=calls.append, rebuild_enabled=True)
        qtbot.addWidget(dlg)
        assert dlg.rebuild_button.text() == "Rebuild Known Words DB"
        assert dlg.rebuild_button.isEnabled()

        dlg.rebuild_button.click()

        assert len(calls) == 1
        assert not dlg.rebuild_button.isEnabled()  # in flight
        db.clear(preserve_user=True)
        calls[0]()  # the tab reports the rebuild finished
        assert dlg.rebuild_button.isEnabled()
        assert "cached from Anki: 0" in dlg.count_label.text()

    def test_rebuild_is_disabled_while_the_cache_is_off(self, qtbot, tmp_path):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path), on_rebuild=lambda _done: None)
        qtbot.addWidget(dlg)
        assert not dlg.rebuild_button.isEnabled()

    def test_no_callback_no_button(self, qtbot, tmp_path):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        assert dlg.rebuild_button.isHidden()


class TestFrame:
    """C19: the standard dialog frame -- title, subtitle, one primary Close last."""

    def test_uses_the_enhanced_dialog_frame(self, qtbot, tmp_path):
        from anki_miner.gui.widgets.base.enhanced_dialog import EnhancedDialog

        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        assert isinstance(dlg, EnhancedDialog)
        assert dlg._title_label.text() == "Local Known Words"
        assert "Word Curator" in dlg._subtitle_label.text()

    def test_close_is_the_one_footer_button_and_primary(self, qtbot, tmp_path):
        from PyQt6.QtWidgets import QPushButton

        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        footer = dlg._footer_widget.findChildren(QPushButton)
        assert [b.text() for b in footer] == ["Close"]
        assert footer[0].objectName() == "primary"

    def test_close_is_looked_up_in_the_catalogued_context(self, qtbot, tmp_path):
        """Z.5 (UI audit 2026-09-29): the German manager read "Close". PyQt's
        ``self.tr`` looks the string up under the runtime class name, and the
        catalogs hold the shared button under ``EnhancedDialog`` only.
        """
        from PyQt6.QtCore import QTranslator
        from PyQt6.QtWidgets import QApplication, QPushButton

        class _ContextTranslator(QTranslator):
            def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
                return f"{context}:{source}" if source == "Close" else None

        app = QApplication.instance()
        assert app is not None
        translator = _ContextTranslator()
        app.installTranslator(translator)
        try:
            dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
            qtbot.addWidget(dlg)
            footer = dlg._footer_widget.findChildren(QPushButton)
            assert [b.text() for b in footer] == ["EnhancedDialog:Close"]
        finally:
            app.removeTranslator(translator)

    def test_the_action_row_is_never_clipped_at_the_minimum_size(self, qtbot, tmp_path):
        """Z.5 (UI audit 2026-09-29): shrunk to its minimum at 1024x768, the
        manager was narrower than its action row, so "Remove Selected" and
        "Reset User List" were cut off on both sides. An explicit minimum width
        on a window replaces its layout's minimum instead of flooring it. The
        longer label stands in for the app's wider face and longer locales.
        """
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        dlg.reset_button.setText("Reset the whole user list of known words")
        dlg.show()
        qtbot.waitExposed(dlg)
        dlg.resize(1, 1)
        qtbot.wait(20)
        for button in (dlg.remove_button, dlg.import_button, dlg.export_button, dlg.reset_button):
            assert button.width() >= button.sizeHint().width(), button.text()
        right_edge = dlg.reset_button.mapTo(dlg, dlg.reset_button.rect().topRight()).x()
        assert right_edge < dlg.width()


class TestKindleAndLuteImport:
    """SK-09: the dialog names the two new formats and parses for the mining language."""

    def test_the_new_formats_have_their_own_names(self, qtbot, tmp_path):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)

        assert dlg._format_display_name("kindle_vocab") == "Kindle Vocabulary Builder"
        assert dlg._format_display_name("lute_csv") == "Lute or LWT term export"

    def test_the_picker_offers_kindle_databases(self, qtbot, tmp_path, monkeypatch):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)
        seen: dict[str, str] = {}

        def pick(*args, on_done, **kwargs):
            seen["filter"] = args[3]
            on_done("")

        monkeypatch.setattr(known_words_dialog_mod.file_dialogs, "pick_open_file", pick)
        dlg._on_import()

        assert "*.db" in seen["filter"]

    def test_the_parse_is_for_the_mining_language(self, qtbot, tmp_path, monkeypatch, capture_off_thread):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path), language="es")
        qtbot.addWidget(dlg)
        seen: dict[str, object] = {}

        def parse(path, **kwargs):
            seen.update(kwargs)
            return _import_result({"gato"})

        monkeypatch.setattr(known_words_dialog_mod, "parse_known_words_file", parse)
        _start_import(dlg, monkeypatch)
        capture_off_thread["work"]()

        assert seen["language"] == "es"

    def test_the_not_recognized_message_names_the_new_formats(self, qtbot, tmp_path):
        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path))
        qtbot.addWidget(dlg)

        dlg._show_import_error(KnownWordsImportError("unrecognized"))

        summary = dlg.issue_banner().current_issue().summary
        assert "Kindle" in summary
        assert "Lute" in summary

    def test_a_file_in_other_languages_names_them(self, qtbot, tmp_path):
        """Ruling C3: a Lute file with no row in the mining language says what it holds."""
        from anki_miner.languages.registry import language_display_name

        dlg = KnownWordsManagerDialog(_db_with_user_words(tmp_path), language="de")
        qtbot.addWidget(dlg)

        dlg._show_import_error(
            KnownWordsImportError("other_language", format_key="lute_csv", languages=("Japanese", "Spanish"))
        )

        summary = dlg.issue_banner().current_issue().summary
        assert language_display_name("de") in summary
        assert "Japanese, Spanish" in summary
