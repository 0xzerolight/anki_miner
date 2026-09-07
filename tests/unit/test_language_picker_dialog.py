"""Subtitle-language picker for Utilities → Download."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402

from anki_miner.gui.widgets.dialogs.language_picker_dialog import LanguagePickerDialog  # noqa: E402
from anki_miner.services.media_downloader import UrlTracks  # noqa: E402


def _codes(dialog: Any, *, checked: bool | None = None) -> list[str]:
    out = []
    for row in range(dialog.lang_list.count()):
        item = dialog.lang_list.item(row)
        code = item.data(Qt.ItemDataRole.UserRole)
        if code is None:  # section header
            continue
        if checked is None or (item.checkState() == Qt.CheckState.Checked) == checked:
            out.append(code)
    return out


def _visible_codes(dialog: Any) -> list[str]:
    return [
        dialog.lang_list.item(r).data(Qt.ItemDataRole.UserRole)
        for r in range(dialog.lang_list.count())
        if not dialog.lang_list.item(r).isHidden()
        and dialog.lang_list.item(r).data(Qt.ItemDataRole.UserRole) is not None
    ]


def _make(qtbot: Any, selection: str = "ja", **kwargs: Any) -> LanguagePickerDialog:
    dialog = LanguagePickerDialog(selection, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


class TestSeeding:
    def test_a_simple_list_ticks_its_codes(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja,en")
        assert set(_codes(dialog, checked=True)) == {"ja", "en"}
        assert dialog.advanced_edit.text() == ""

    def test_an_expression_lands_in_advanced_with_nothing_ticked(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "en.*,-live_chat")
        assert _codes(dialog, checked=True) == []
        assert dialog.advanced_edit.text() == "en.*,-live_chat"

    def test_a_selected_code_outside_the_curated_list_still_appears_ticked(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "sw")
        assert "sw" in _codes(dialog, checked=True)

    def test_the_list_shows_names_not_bare_codes(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        labels = [dialog.lang_list.item(r).text() for r in range(dialog.lang_list.count())]
        assert any("Japanese" in label for label in labels)


class TestDetected:
    def test_detected_codes_lead_the_list(self, qtbot: Any) -> None:
        tracks = UrlTracks("T", ("ja", "en"), ("fr",), (), False)
        dialog = _make(qtbot, "ja", detected=tracks)
        assert _codes(dialog)[:3] == ["ja", "en", "fr"]

    def test_an_auto_only_code_is_labelled(self, qtbot: Any) -> None:
        tracks = UrlTracks("T", ("ja",), ("fr",), (), False)
        dialog = _make(qtbot, "ja", detected=tracks)
        row = next(
            r
            for r in range(dialog.lang_list.count())
            if dialog.lang_list.item(r).data(Qt.ItemDataRole.UserRole) == "fr"
        )
        assert "automatic" in dialog.lang_list.item(row).text()

    def test_a_detected_code_is_not_repeated_in_the_common_section(self, qtbot: Any) -> None:
        tracks = UrlTracks("T", ("ja",), (), (), False)
        dialog = _make(qtbot, "ja", detected=tracks)
        assert _codes(dialog).count("ja") == 1

    def test_machine_translation_note_is_shown_only_when_they_exist(self, qtbot: Any) -> None:
        # isHidden(), not isVisible(): an unshown dialog's children are never
        # "visible", so isVisible() would pass for both cases and prove nothing.
        shown = _make(qtbot, "ja", detected=UrlTracks("T", (), ("ja",), (), True))
        assert shown.translations_note.isHidden() is False
        absent = _make(qtbot, "ja", detected=UrlTracks("T", (), ("ja",), (), False))
        assert absent.translations_note.isHidden() is True

    def test_no_detection_means_no_note_and_no_detected_section(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        assert dialog.translations_note.isHidden() is True


class TestResult:
    def test_ticked_codes_become_a_comma_list_in_list_order(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "en,ja")
        assert dialog.selected_langs() == "ja,en"  # curated order, not seed order

    def test_advanced_text_wins_over_the_boxes(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        dialog.advanced_edit.setText("all,-live_chat")
        assert dialog.selected_langs() == "all,-live_chat"

    def test_advanced_text_disables_the_list(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        dialog.advanced_edit.setText("en.*")
        assert dialog.lang_list.isEnabled() is False
        dialog.advanced_edit.setText("")
        assert dialog.lang_list.isEnabled() is True

    def test_ok_is_disabled_with_an_empty_selection(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "")
        assert dialog.ok_button.isEnabled() is False
        dialog.advanced_edit.setText("ja")
        assert dialog.ok_button.isEnabled() is True

    def test_unticking_the_last_box_disables_ok(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        assert dialog.ok_button.isEnabled() is True
        row = next(
            r
            for r in range(dialog.lang_list.count())
            if dialog.lang_list.item(r).data(Qt.ItemDataRole.UserRole) == "ja"
        )
        dialog.lang_list.item(row).setCheckState(Qt.CheckState.Unchecked)
        assert dialog.ok_button.isEnabled() is False


class TestSearch:
    def test_search_hides_non_matching_rows(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        dialog.search_edit.setText("japan")
        assert _visible_codes(dialog) == ["ja"]

    def test_search_matches_the_code_too(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        dialog.search_edit.setText("zh-Hant")
        assert _visible_codes(dialog) == ["zh-Hant"]

    def test_a_ticked_row_hidden_by_search_stays_in_the_result(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja,en")
        dialog.search_edit.setText("japan")
        assert dialog.selected_langs() == "ja,en"

    def test_clearing_the_search_restores_every_row(self, qtbot: Any) -> None:
        dialog = _make(qtbot, "ja")
        before = len(_visible_codes(dialog))
        dialog.search_edit.setText("japan")
        dialog.search_edit.setText("")
        assert len(_visible_codes(dialog)) == before
