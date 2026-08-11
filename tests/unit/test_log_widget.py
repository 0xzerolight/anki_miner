"""LogWidget console behaviour: level filters, search, follow, copy and save.

The Activity log is the only record a long unattended run leaves behind, so the
severity of every line has to survive a copy (it lives in the text, not in a
colour) and a single error has to be findable inside hundreds of lines.
"""

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QTextEdit

from anki_miner.gui.widgets import log_widget as log_widget_module
from anki_miner.gui.widgets.log_widget import LogWidget


@pytest.fixture
def log(qtbot):
    widget = LogWidget()
    qtbot.addWidget(widget)
    return widget


def _lines(widget: LogWidget) -> list[str]:
    return [line for line in widget.text_edit.toPlainText().splitlines() if line]


def _fill(widget: LogWidget) -> None:
    widget.append_info("parsed subtitles")
    widget.append_success("created 12 cards")
    widget.append_warning("no pitch accent for 走る")
    widget.append_error("AnkiConnect refused the note")


class TestSemanticText:
    """Severity is text, not colour — it must survive a copy."""

    def test_text_edit_is_still_the_public_surface(self, log):
        assert isinstance(log.text_edit, QTextEdit)

    def test_level_marker_is_in_the_plain_text(self, log):
        log.append_error("AnkiConnect refused the note")
        assert "[ERROR] AnkiConnect refused the note" in log.text_edit.toPlainText()

    def test_every_level_has_its_own_marker(self, log):
        _fill(log)
        text = log.text_edit.toPlainText()
        assert "[INFO] parsed subtitles" in text
        assert "[SUCCESS] created 12 cards" in text
        assert "[WARNING] no pitch accent for 走る" in text
        assert "[ERROR] AnkiConnect refused the note" in text

    def test_timestamp_still_prefixes_every_line(self, log):
        log.append_info("parsed subtitles")
        line = _lines(log)[0]
        assert line.startswith("[")
        assert line.count(":") >= 2


class TestLevelFilters:
    """All / Info / Warnings / Errors are exclusive."""

    def test_all_is_the_default(self, log):
        _fill(log)
        assert len(_lines(log)) == 4
        assert log.filter_buttons["all"].isChecked()

    def test_errors_shows_only_errors(self, log):
        _fill(log)
        log.filter_buttons["error"].click()
        assert _lines(log) == [line for line in _lines(log) if "[ERROR]" in line]
        assert len(_lines(log)) == 1

    def test_warnings_shows_only_warnings(self, log):
        _fill(log)
        log.filter_buttons["warning"].click()
        assert len(_lines(log)) == 1
        assert "[WARNING]" in _lines(log)[0]

    def test_info_includes_success(self, log):
        _fill(log)
        log.filter_buttons["info"].click()
        text = log.text_edit.toPlainText()
        assert "[INFO] parsed subtitles" in text
        assert "[SUCCESS] created 12 cards" in text
        assert "[WARNING]" not in text
        assert "[ERROR]" not in text

    def test_filters_are_exclusive(self, log):
        log.filter_buttons["error"].click()
        assert log.filter_buttons["error"].isChecked()
        assert not log.filter_buttons["all"].isChecked()
        log.filter_buttons["all"].click()
        assert log.filter_buttons["all"].isChecked()
        assert not log.filter_buttons["error"].isChecked()

    def test_entries_appended_while_filtered_are_not_lost(self, log):
        log.filter_buttons["error"].click()
        log.append_info("parsed subtitles")
        assert _lines(log) == []
        log.filter_buttons["all"].click()
        assert "[INFO] parsed subtitles" in log.text_edit.toPlainText()

    def test_the_active_chip_stays_a_chip(self, log):
        """It shows its state by being checked, not by claiming to be the screen's
        primary action — accent belongs to one task action per screen (D41)."""
        log.filter_buttons["error"].click()

        assert {key: button.objectName() for key, button in log.filter_buttons.items()} == {
            "all": "ghost",
            "info": "ghost",
            "warning": "ghost",
            "error": "ghost",
        }

    def test_pause_follow_says_it_is_on_by_being_checked(self, log):
        log.pause_button.setChecked(True)

        assert log.pause_button.objectName() == "ghost"
        assert log.pause_button.isChecked()


class TestSearch:
    """Case-insensitive search with a shown-of-retained count."""

    def test_search_is_case_insensitive(self, log):
        _fill(log)
        log.search_input.setText("ANKICONNECT")
        assert len(_lines(log)) == 1
        assert "[ERROR]" in _lines(log)[0]

    def test_match_count_reads_shown_of_total(self, log):
        _fill(log)
        log.search_input.setText("ankiconnect")
        assert log.match_label.text() == "1 of 4"

    def test_match_count_with_no_filter_is_total_of_total(self, log):
        _fill(log)
        assert log.match_label.text() == "4 of 4"

    def test_match_count_follows_the_level_filter(self, log):
        _fill(log)
        log.filter_buttons["info"].click()
        assert log.match_label.text() == "2 of 4"

    def test_search_combines_with_the_level_filter(self, log):
        log.append_error("first failure")
        log.append_error("second failure")
        log.append_info("first note")
        log.filter_buttons["error"].click()
        log.search_input.setText("first")
        assert len(_lines(log)) == 1
        assert "first failure" in _lines(log)[0]

    def test_clearing_the_search_restores_every_line(self, log):
        _fill(log)
        log.search_input.setText("ankiconnect")
        log.search_input.setText("")
        assert len(_lines(log)) == 4


class TestFollow:
    """Scrolling up must not yank the reader back to the bottom."""

    @pytest.fixture
    def scrollable(self, qtbot):
        widget = LogWidget()
        qtbot.addWidget(widget)
        widget.resize(320, 220)
        widget.show()
        qtbot.waitExposed(widget)
        for i in range(120):
            widget.append_info(f"line {i}")
        qtbot.waitUntil(lambda: widget.text_edit.verticalScrollBar().maximum() > 0)
        return widget

    def test_follows_by_default(self, scrollable, qtbot):
        bar = scrollable.text_edit.verticalScrollBar()
        scrollable.append_info("newest")
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
        assert scrollable.jump_button.isHidden()

    def test_scrolled_up_reader_is_not_yanked_down(self, scrollable):
        bar = scrollable.text_edit.verticalScrollBar()
        bar.setValue(0)
        scrollable.append_info("newest")
        assert bar.value() == 0

    def test_scrolled_up_shows_the_new_line_count(self, scrollable):
        bar = scrollable.text_edit.verticalScrollBar()
        bar.setValue(0)
        scrollable.append_info("one")
        scrollable.append_info("two")
        assert not scrollable.jump_button.isHidden()
        assert "2" in scrollable.jump_button.text()
        assert "Jump to latest" in scrollable.jump_button.text()

    def test_jump_button_returns_to_the_bottom(self, scrollable, qtbot):
        bar = scrollable.text_edit.verticalScrollBar()
        bar.setValue(0)
        scrollable.append_info("newest")
        scrollable.jump_button.click()
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
        assert scrollable.jump_button.isHidden()

    def test_scrolling_back_to_the_bottom_clears_the_affordance(self, scrollable):
        bar = scrollable.text_edit.verticalScrollBar()
        bar.setValue(0)
        scrollable.append_info("newest")
        assert not scrollable.jump_button.isHidden()
        bar.setValue(bar.maximum())
        assert scrollable.jump_button.isHidden()

    def test_pause_stops_following(self, scrollable):
        bar = scrollable.text_edit.verticalScrollBar()
        scrollable.pause_button.click()
        at_bottom = bar.value()
        scrollable.append_info("newest")
        assert bar.value() == at_bottom
        assert not scrollable.jump_button.isHidden()

    def test_resuming_jumps_to_the_bottom(self, scrollable, qtbot):
        bar = scrollable.text_edit.verticalScrollBar()
        scrollable.pause_button.click()
        scrollable.append_info("newest")
        scrollable.pause_button.click()
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
        assert scrollable.jump_button.isHidden()

    def test_pause_does_not_drop_entries(self, scrollable):
        scrollable.pause_button.click()
        scrollable.append_error("late failure")
        assert "[ERROR] late failure" in scrollable.text_edit.toPlainText()

    def test_filtered_out_lines_do_not_count_as_new(self, scrollable):
        scrollable.filter_buttons["error"].click()
        scrollable.pause_button.click()
        scrollable.append_info("hidden by the filter")
        assert scrollable.jump_button.isHidden()
        scrollable.append_error("visible failure")
        assert "1" in scrollable.jump_button.text()


class TestCopy:
    def test_copy_visible_respects_the_filters(self, log):
        _fill(log)
        log.filter_buttons["error"].click()
        log.copy_visible_button.click()
        text = QApplication.clipboard().text()
        assert "[ERROR] AnkiConnect refused the note" in text
        assert "[INFO]" not in text

    def test_copy_all_ignores_the_filters(self, log):
        _fill(log)
        log.filter_buttons["error"].click()
        log.copy_all_button.click()
        text = QApplication.clipboard().text()
        assert "[INFO] parsed subtitles" in text
        assert "[SUCCESS] created 12 cards" in text
        assert "[WARNING] no pitch accent for 走る" in text
        assert "[ERROR] AnkiConnect refused the note" in text


class TestSave:
    def test_cancelled_dialog_writes_nothing(self, log, tmp_path, monkeypatch):
        monkeypatch.setattr(
            log_widget_module.file_dialogs,
            "pick_save_file",
            lambda *a, on_done, **k: on_done(""),
        )
        _fill(log)
        log.save_button.click()
        assert list(tmp_path.iterdir()) == []

    def test_saves_every_entry_as_utf8_with_level(self, log, qtbot, tmp_path, monkeypatch):
        target = tmp_path / "run.txt"
        monkeypatch.setattr(
            log_widget_module.file_dialogs,
            "pick_save_file",
            lambda *a, on_done, **k: on_done(str(target)),
        )
        _fill(log)
        log.filter_buttons["error"].click()
        log.save_button.click()
        qtbot.waitUntil(lambda: target.exists())
        qtbot.waitUntil(lambda: log.save_button.isEnabled())
        saved = target.read_text(encoding="utf-8")
        assert "[WARNING] no pitch accent for 走る" in saved
        assert "[INFO] parsed subtitles" in saved

    def test_save_failure_is_reported_in_the_log(self, log, qtbot, tmp_path, monkeypatch):
        unwritable = tmp_path / "missing-dir" / "run.txt"
        monkeypatch.setattr(
            log_widget_module.file_dialogs,
            "pick_save_file",
            lambda *a, on_done, **k: on_done(str(unwritable)),
        )
        log.append_info("parsed subtitles")
        log.save_button.click()
        qtbot.waitUntil(lambda: "[ERROR]" in log.text_edit.toPlainText())
        qtbot.waitUntil(lambda: log.save_button.isEnabled())

    def test_failed_save_preserves_existing_file(self, log, qtbot, tmp_path, monkeypatch):
        target = tmp_path / "run.txt"
        target.write_bytes(b"ORIGINAL")
        monkeypatch.setattr(
            log_widget_module.file_dialogs,
            "pick_save_file",
            lambda *a, on_done, **k: on_done(str(target)),
        )

        def _fail_mid_write(path: Path, text: str, **_kwargs) -> int:
            path.write_bytes(text.encode("utf-8")[:4])
            raise OSError("staged write failed")

        monkeypatch.setattr(Path, "write_text", _fail_mid_write)
        log.append_info("new run log")
        log.save_button.click()

        qtbot.waitUntil(lambda: "staged write failed" in log.text_edit.toPlainText())
        qtbot.waitUntil(lambda: log.save_button.isEnabled())
        assert target.read_bytes() == b"ORIGINAL"

    def test_suggested_name_is_a_text_file(self, log, monkeypatch):
        captured: list[str] = []

        def _fake(parent, caption, directory, file_filter, *, on_done):
            captured.append(directory)
            on_done("")

        monkeypatch.setattr(log_widget_module.file_dialogs, "pick_save_file", _fake)
        log.save_button.click()
        assert captured and Path(captured[0]).suffix == ".txt"


class TestRotation:
    def test_oldest_entries_are_dropped_once_the_cap_is_reached(self, log):
        log.MAX_LINES = 5
        log.KEEP_LINES = 3
        for i in range(6):
            log.append_info(f"line {i}")
        lines = _lines(log)
        assert len(lines) == 3  # overflow trims the store back to KEEP_LINES
        assert "line 0" not in log.text_edit.toPlainText()
        assert "line 5" in log.text_edit.toPlainText()

    def test_rotation_also_trims_the_copy_all_payload(self, log):
        log.MAX_LINES = 5
        log.KEEP_LINES = 3
        for i in range(6):
            log.append_info(f"line {i}")
        log.copy_all_button.click()
        assert "line 0" not in QApplication.clipboard().text()


class TestClear:
    def test_clear_empties_the_view_and_the_store(self, log):
        _fill(log)
        log.clear_log()
        assert log.text_edit.toPlainText() == ""
        assert log.match_label.text() == "0 of 0"
        log.copy_all_button.click()
        assert QApplication.clipboard().text() == ""

    def test_clear_hides_the_jump_affordance(self, qtbot):
        widget = LogWidget()
        qtbot.addWidget(widget)
        widget.resize(320, 220)
        widget.show()
        qtbot.waitExposed(widget)
        for i in range(120):
            widget.append_info(f"line {i}")
        widget.text_edit.verticalScrollBar().setValue(0)
        widget.append_info("newest")
        widget.clear_log()
        assert widget.jump_button.isHidden()


class TestProblemSignal:
    """W1's Activity auto-open hangs off this signal."""

    def test_warning_and_error_emit(self, log, qtbot):
        with qtbot.waitSignal(log.problem_logged) as blocker:
            log.append_warning("no pitch accent for 走る")
        assert blocker.args == ["WARNING", "no pitch accent for 走る"]

        with qtbot.waitSignal(log.problem_logged) as blocker:
            log.append_error("AnkiConnect refused the note")
        assert blocker.args == ["ERROR", "AnkiConnect refused the note"]

    def test_info_and_success_do_not_emit(self, log):
        seen: list[tuple[str, str]] = []
        log.problem_logged.connect(lambda level, message: seen.append((level, message)))
        log.append_info("parsed subtitles")
        log.append_success("created 12 cards")
        assert seen == []


class TestLogTypeface:
    """The console used to ask for 'Consolas' at a constant 13px.

    Consolas exists on Windows and nowhere else, and the constant meant the log
    alone ignored the text-size setting (decision D44-B).
    """

    def test_the_console_uses_the_platform_fixed_font(self, log):
        from anki_miner.gui.utils.fonts import resolved_families

        assert log.text_edit.font().family() == resolved_families().monospace

    def test_the_console_never_asks_for_a_windows_only_family(self, log):
        assert log.text_edit.font().family() != "Consolas"

    def test_the_console_follows_the_text_size_setting(self, qtbot, text_scale):
        small = LogWidget()
        qtbot.addWidget(small)
        baseline = small.text_edit.font().pixelSize()

        text_scale(2.0)
        large = LogWidget()
        qtbot.addWidget(large)
        assert large.text_edit.font().pixelSize() == 2 * baseline


@pytest.fixture
def text_scale():
    """Yield ``apply(scale)``, restoring the global text scale afterwards.

    Only the scale is changed, never the application stylesheet: these widgets
    are never shown, so their Python font is the one that answers.
    """
    from anki_miner.gui.resources.styles.theme import Theme

    original = Theme.get_font_scale()
    yield Theme.set_font_scale
    Theme.set_font_scale(original)
