"""Tests for the WordCurationDialog sentence picker.

Covers picking which example sentence (and scene) gets mined when a word
appears on multiple subtitle lines:
1. No picker for single-occurrence words.
2. Focusing a multi-candidate word populates the list, default-selecting the
   current pick.
3. Activating another candidate updates the chosen word, the Sentence cell, and
   seeks the player.
4. get_selected_words returns the chosen variant (original when untouched).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMenu

from anki_miner.gui.utils.phrase_wrap import WORD_JOINER
from anki_miner.gui.utils.qt_helpers import COPY_ROLE
from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord


def _leaf(lemma: str, sentence: str, start_time: float) -> TokenizedWord:
    """A candidate variant (no nested candidates)."""
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="",
        sentence=sentence,
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="動詞",
    )


def _word_with_candidates() -> TokenizedWord:
    """A word that appears on three lines; current pick = the first."""
    cands = [
        _leaf("食べる", "朝ごはんを食べる", 1.0),
        _leaf("食べる", "パンを食べる", 5.0),
        _leaf("食べる", "早く食べなさい", 9.0),
    ]
    word = _leaf("食べる", "朝ごはんを食べる", 1.0)
    word.sentence_candidates = cands
    return word


def _plain_word() -> TokenizedWord:
    """A single-occurrence word (no candidates)."""
    return _leaf("走る", "公園を走る", 20.0)


def _variant_word() -> TokenizedWord:
    """A word whose mined form diverges from unidic's lemma (Issue #107).

    想う is a real 動詞 case: the tagger returns lemma 思う (the canonical
    headword, kanji variant collapsed) with orthBase 想う. mined_form — the card
    front, and what the "Word (mined)" column prints — is 想う. Two candidates so
    the sentence picker is live and pick-independence is testable.
    """
    word = TokenizedWord(
        surface="想っ",
        lemma="思う",
        reading="オモウ",
        sentence="君のことを想った",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        pos="動詞",
        orth_base="想う",
    )
    word.sentence_candidates = [word, _leaf("想う", "彼女を想う気持ち", 7.0)]
    return word


def _noun_leaf(surface: str, lemma: str, sentence: str, start_time: float) -> TokenizedWord:
    """A noun candidate variant.

    Nouns are surface-mined, so a variant that lands on a different subtitle line
    carries a different ``surface`` — and a different ``mined_form`` with it.
    This is the shape ``WordFilter._swap_word_to_line`` produces (it
    ``dataclasses.replace``s ``surface``, not ``lemma``/``reading``), and the only
    shape that makes columns 1 and 2 observable at all: the verb fixture above
    mines ``orth_base or lemma``, identical across every candidate.
    """
    return TokenizedWord(
        surface=surface,
        lemma=lemma,
        reading="コドモ",
        sentence=sentence,
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="名詞",
    )


def _noun_with_varying_surface(
    lemma: str = "子供",
    variant: str = "子ども",
    base_time: float = 1.0,
) -> TokenizedWord:
    """A noun on two lines whose surface — and mined form — differ per line."""
    cands = [
        _noun_leaf(lemma, lemma, f"{lemma}が走る", base_time),
        _noun_leaf(variant, lemma, f"{variant}と遊ぶ", base_time + 4.0),
    ]
    # Guard the premise: if the fold rules ever collapse these two spellings the
    # column assertions below would pass vacuously, before AND after any fix.
    assert cands[0].surface != cands[1].surface
    assert cands[0].mined_form != cands[1].mined_form

    word = _noun_leaf(lemma, lemma, f"{lemma}が走る", base_time)
    word.sentence_candidates = cands
    return word


def _select_and_fire(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


@pytest.fixture()
def mixed_words():
    # Row 0 has candidates, row 1 does not.
    return [_word_with_candidates(), _plain_word()]


class TestPickerVisibility:
    def test_no_picker_without_candidates(self, qtbot):
        dlg = WordCurationDialog([_plain_word()])
        qtbot.addWidget(dlg)
        assert dlg._has_candidates is False
        assert not hasattr(dlg, "sentence_list")

    def test_picker_present_with_candidates(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        assert dlg._has_candidates is True
        assert hasattr(dlg, "sentence_list")

    def test_sentence_cell_shows_candidate_count(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        row = dlg._visual_row_for_index(0)
        assert row is not None
        assert "(3)" in dlg.table.item(row, 4).text()


class TestPickerPopulation:
    def test_focus_populates_and_selects_current(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        assert dlg.sentence_list.count() == 3
        # Default pick is the first candidate (matches the word's sentence/timing).
        assert dlg.sentence_list.currentRow() == 0
        assert dlg.sentence_list.isEnabled()

    def test_focus_single_occurrence_disables_list(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 1)
        assert dlg.sentence_list.count() == 0
        assert dlg.sentence_list.isEnabled() is False

    def test_every_candidate_is_listed_and_the_list_scrolls(self, qtbot):
        """The picker is unbounded: 40 lines are 40 options, reachable by scrolling.

        The pane is nowhere near 40 rows tall, so the count alone is only half the
        contract — per-pixel vertical scrolling is what makes the tail reachable.
        """
        word = _leaf("食べる", "文 0", 0.0)
        word.sentence_candidates = [_leaf("食べる", f"文 {i}", float(i)) for i in range(40)]
        dlg = WordCurationDialog([word])
        qtbot.addWidget(dlg)

        _select_and_fire(dlg, 0)

        assert dlg.sentence_list.count() == 40
        assert dlg.sentence_list.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel


class TestPickerPaneLabel:
    """The pane title carries the option count (an unbounded list needs one)."""

    def test_label_counts_the_options(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        assert dlg.sentence_pane_label.text() == "Sentences (3)"

    def test_label_drops_the_count_for_a_single_occurrence_word(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        _select_and_fire(dlg, 1)
        assert dlg.sentence_pane_label.text() == "Sentences"


class TestPickerViewportStability:
    """Wrapped rows re-flow when the scrollbar toggles the viewport width.

    Candidate counts swing per focused word (the list is unbounded), so a
    scrollbar that comes and goes re-wraps every sentence left/right — the
    stutter users see while arrow-keying through the table. The gutter is
    reserved permanently: viewport width must not depend on candidate count.
    """

    def test_viewport_width_constant_across_candidate_counts(self, qtbot):
        many = _leaf("食べる", "文 0", 0.0)
        many.sentence_candidates = [_leaf("食べる", f"文 {i}", float(i)) for i in range(40)]
        dlg = WordCurationDialog([many, _word_with_candidates()])
        qtbot.addWidget(dlg)
        dlg.resize(780, 600)  # offscreen screen is 800x800; stay inside it
        dlg.show()
        QApplication.processEvents()

        _select_and_fire(dlg, 0)  # 40 candidates: scrollbar needed
        QApplication.processEvents()
        width_many = dlg.sentence_list.viewport().width()

        _select_and_fire(dlg, 1)  # 3 short candidates: content fits the pane
        QApplication.processEvents()
        width_few = dlg.sentence_list.viewport().width()

        assert width_few == width_many


class TestPickerSelection:
    def test_pick_updates_chosen_and_cell(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)

        dlg.sentence_list.setCurrentRow(1)  # user picks the 2nd sentence

        assert dlg._chosen[0].sentence == "パンを食べる"
        assert dlg._chosen[0].start_time == 5.0
        cell = dlg.table.item(dlg._visual_row_for_index(0), 4)
        assert "パンを食べる" in cell.text()
        assert "(3)" in cell.text()

    def test_get_selected_words_returns_chosen(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(2)

        selected = dlg.get_selected_words()
        # Row 0 word reflects the pick; row 1 untouched word is unchanged.
        chosen = next(w for w in selected if w.lemma == "食べる")
        assert chosen.sentence == "早く食べなさい"
        assert chosen.start_time == 9.0
        plain = next(w for w in selected if w.lemma == "走る")
        assert plain.sentence == "公園を走る"

    def test_focusing_alone_does_not_record_a_pick(self, qtbot, mixed_words):
        """The default sentence flows through until the user actually picks."""
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)

        assert 0 not in dlg._chosen
        chosen = next(w for w in dlg.get_selected_words() if w.lemma == "食べる")
        assert chosen.sentence == "朝ごはんを食べる"

    def test_the_pick_survives_focusing_away_and_back(self, qtbot, mixed_words):
        """Refocusing must not re-resolve the row to its default sentence."""
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(2)

        _select_and_fire(dlg, 1)
        _select_and_fire(dlg, 0)

        assert dlg._chosen[0].sentence == "早く食べなさい"
        chosen = next(w for w in dlg.get_selected_words() if w.lemma == "食べる")
        assert chosen.sentence == "早く食べなさい"

    def test_untouched_word_returns_original(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        # Never focus/pick — defaults flow through.
        selected = dlg.get_selected_words()
        chosen = next(w for w in selected if w.lemma == "食べる")
        assert chosen.sentence == "朝ごはんを食べる"


def _dialog_with_stub_player(mixed_words, tmp_path):
    """A curation dialog whose player widget is a real QWidget stub.

    A real QWidget is needed so the per-widget Space shortcut can be installed
    on it; ``_create_player_widget`` is patched to skip real Qt multimedia setup.
    """
    from PyQt6.QtWidgets import QWidget

    video: Path = tmp_path / "v.mkv"
    video.write_bytes(b"")
    ctx = CurationMediaContext(video_file=video, subtitle_entries=[(1.0, 3.0, "x")], offset=0.0)
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
        return WordCurationDialog(mixed_words, media_context=ctx)


class TestPickerPlayerSeek:
    def test_pick_seeks_player_to_chosen_scene(self, qtbot, mixed_words, tmp_path):
        dlg = _dialog_with_stub_player(mixed_words, tmp_path)
        qtbot.addWidget(dlg)
        mock_player = MagicMock()
        dlg.player_widget = mock_player

        _select_and_fire(dlg, 0)
        mock_player.reset_mock()
        dlg.sentence_list.setCurrentRow(2)  # pick the 9.0s scene

        # The pick's seek is deferred to the next event-loop tick (off the
        # synchronous currentRowChanged emission), so drive the loop before
        # asserting — a single click must land the preview.
        qtbot.waitUntil(lambda: mock_player.seek_seconds.called, timeout=1000)
        mock_player.seek_seconds.assert_called_with(9.0)
        mock_player.pause.assert_called()


class TestPlayPauseShortcut:
    """Space play/pause must reach the player from every pane the user clicks
    into to preview a scene — not just the word table (Issue: dead Space after
    interacting with the sentence picker)."""

    @staticmethod
    def _has_space_play_pause(widget) -> bool:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeySequence, QShortcut

        space = QKeySequence(Qt.Key.Key_Space)
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        # Direct children only: a shortcut parented to ``widget`` lists under it.
        return any(
            sc.key() == space and sc.context() == ctx
            for sc in widget.findChildren(QShortcut, options=Qt.FindChildOption.FindDirectChildrenOnly)
        )

    def test_space_shortcut_on_table_and_sentence_list(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        assert self._has_space_play_pause(dlg.table)
        assert self._has_space_play_pause(dlg.sentence_list)

    def test_space_shortcut_on_player_pane(self, qtbot, mixed_words, tmp_path):
        """The shortcut hangs off the pane container, so it also covers the clip
        strip and the prev/next line buttons that sit beside the player (#120)."""
        dlg = _dialog_with_stub_player(mixed_words, tmp_path)
        qtbot.addWidget(dlg)
        assert self._has_space_play_pause(dlg.player_pane)


class TestContextMenuCopy:
    """Right-click copies must agree with what the row shows.

    "Copy sentence" copies the sentence the user picked in the Sentences box,
    not the primary/first one (Issue #95). "Copy word" copies the mined form —
    the card front, column 1 — not unidic's variant-collapsing lemma (#107).
    """

    @staticmethod
    def _copy_word_via_menu(dlg: WordCurationDialog, idx: int) -> None:
        """Drive the real context-menu handler and click "Copy word" (1st action)."""
        row = dlg._visual_row_for_index(idx)
        assert row is not None
        rect = dlg.table.visualItemRect(dlg.table.item(row, 0))
        with patch.object(QMenu, "exec", lambda self, _pos: self.actions()[0]):
            dlg._on_table_context_menu(rect.center())

    @staticmethod
    def _copy_sentence_via_menu(dlg: WordCurationDialog, idx: int) -> None:
        """Drive the real context-menu handler and click "Copy sentence".

        Patches ``QMenu.exec`` to return the 2nd action (Copy sentence), and
        points the handler at the word's row via its item rect centre.
        """
        row = dlg._visual_row_for_index(idx)
        assert row is not None
        rect = dlg.table.visualItemRect(dlg.table.item(row, 0))
        with patch.object(QMenu, "exec", lambda self, _pos: self.actions()[1]):
            dlg._on_table_context_menu(rect.center())

    def test_copy_sentence_uses_selected_alternate(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)  # user picks the 2nd sentence

        self._copy_sentence_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == "パンを食べる"

    def test_copy_sentence_default_when_no_pick(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        # Never open the picker — the default (primary) sentence flows through.
        self._copy_sentence_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == "朝ごはんを食べる"

    def test_copy_word_uses_mined_form_not_lemma(self, qtbot):
        """Issue #107: 想う was copied as 思う, unidic's collapsed headword."""
        word = _variant_word()
        dlg = WordCurationDialog([word])
        qtbot.addWidget(dlg)
        assert word.mined_form == "想う" and word.lemma == "思う"

        self._copy_word_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == "想う"

    def test_copy_word_matches_the_mined_column(self, qtbot):
        """The copied text is exactly what the row prints in "Word (mined)"."""
        dlg = WordCurationDialog([_variant_word()])
        qtbot.addWidget(dlg)
        row = dlg._visual_row_for_index(0)
        assert row is not None

        self._copy_word_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == dlg.table.item(row, 1).text()

    def test_copy_word_follows_the_pick(self, qtbot):
        """The menu copies the picked variant's mined form, not the primary's.

        Needs a surface-mined POS whose two lines differ: on a 動詞 both
        candidates resolve through ``orth_base or lemma`` to the same string, so
        the assertion would hold whichever object the handler read.
        """
        dlg = WordCurationDialog([_noun_with_varying_surface()])
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)

        self._copy_word_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == "子ども"

    def test_copy_word_still_matches_the_mined_column_after_a_pick(self, qtbot):
        """Clipboard and column 1 stay the same string once a pick has moved both."""
        dlg = WordCurationDialog([_noun_with_varying_surface()])
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)

        self._copy_word_via_menu(dlg, 0)

        assert QApplication.clipboard().text() == _cell(dlg, 0, 1).text()


class TestRowCopyRole:
    """Ctrl+C row copy serializes COPY_ROLE, so a pick must refresh it too —
    the Issue #95 defect on the row-copy path."""

    def test_copy_role_follows_the_pick(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)  # user picks the 2nd sentence

        row = dlg._visual_row_for_index(0)
        assert row is not None
        assert dlg.table.item(row, 4).data(COPY_ROLE) == "パンを食べる"

    def test_copy_role_default_when_no_pick(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        row = dlg._visual_row_for_index(0)
        assert row is not None
        # Full sentence, not the elided cell text with its "(3)" count suffix.
        assert dlg.table.item(row, 4).data(COPY_ROLE) == "朝ごはんを食べる"


def _cell(dlg: WordCurationDialog, idx: int, column: int):
    """The live cell for word ``idx``'s column, resolved through the sort order."""
    row = dlg._visual_row_for_index(idx)
    assert row is not None
    item = dlg.table.item(row, column)
    assert item is not None
    return item


class TestPickPropagatesToRow:
    """Picking a sentence must update everything the row says about that
    occurrence, not only the Sentence cell (Issue #108)."""

    @pytest.fixture()
    def noun_words(self):
        return [_noun_with_varying_surface()]

    @staticmethod
    def _pick_variant(dlg: WordCurationDialog) -> None:
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)

    def test_form_in_subtitle_tracks_the_pick(self, qtbot, noun_words):
        """The reported bug: col 2 kept the primary occurrence's surface."""
        dlg = WordCurationDialog(noun_words)
        qtbot.addWidget(dlg)
        self._pick_variant(dlg)

        assert _cell(dlg, 0, 2).text() == "子ども"

    def test_mined_form_tracks_the_pick(self, qtbot, noun_words):
        """Nouns are surface-mined, so the card front moves with the pick too."""
        dlg = WordCurationDialog(noun_words)
        qtbot.addWidget(dlg)
        self._pick_variant(dlg)

        assert _cell(dlg, 0, 1).text() == "子ども"

    def test_copy_payload_tracks_the_pick(self, qtbot, noun_words):
        """Ctrl+C lifts COPY_ROLE, which was frozen at populate time — the same
        hole Issue #95 closed on the context menu, still open on the row copy."""
        from anki_miner.gui.utils.qt_helpers import COPY_ROLE

        dlg = WordCurationDialog(noun_words)
        qtbot.addWidget(dlg)
        self._pick_variant(dlg)

        assert _cell(dlg, 0, 2).data(COPY_ROLE) == "子ども"
        assert _cell(dlg, 0, 4).data(COPY_ROLE) == "子どもと遊ぶ"

    def test_sort_key_tracks_the_pick(self, qtbot, noun_words):
        """A repainted cell that keeps its old sort key sorts by a value it no
        longer prints — the contract update_table_item exists to hold."""
        from anki_miner.gui.utils.qt_helpers import SORT_ROLE

        dlg = WordCurationDialog(noun_words)
        qtbot.addWidget(dlg)
        self._pick_variant(dlg)

        assert _cell(dlg, 0, 2).data(SORT_ROLE) == "子ども"

    def test_reading_cell_stays_consistent_with_the_pick(self, qtbot, noun_words):
        """A no-op today (``reading`` is not swapped per line), asserted anyway so
        the row's "cols 1-4 are the chosen variant" contract is pinned end to end."""
        dlg = WordCurationDialog(noun_words)
        qtbot.addWidget(dlg)
        self._pick_variant(dlg)

        assert _cell(dlg, 0, 3).text() == dlg._chosen[0].reading

    @staticmethod
    def _sorted_pair(qtbot):
        """Two nouns, sorted by the mined-form column, where the pick MOVES a row.

        赤 (U+8D64) sorts after 白 (U+767D), and its variant あ (U+3042) sorts
        before both — so picking the variant relocates word 0 from the bottom row
        to the top, which is the only arrangement that exercises the re-sort.
        """
        from PyQt6.QtCore import Qt

        words = [
            _noun_with_varying_surface("赤", "あ", 1.0),
            _noun_with_varying_surface("白", "し", 20.0),
        ]
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg.table.sortItems(1, Qt.SortOrder.AscendingOrder)
        return dlg

    def test_a_pick_does_not_clobber_a_neighbouring_row(self, qtbot):
        """With the table sorted BY a pick column, repainting the row re-sorts it.

        Four cells written against a row index resolved once would then land the
        last three on whichever word slid into the old position. Both rows must
        still describe their own word.
        """
        dlg = self._sorted_pair(qtbot)
        before = dlg._visual_row_for_index(0)

        _select_and_fire(dlg, before)
        assert dlg._candidate_list_index == 0
        dlg.sentence_list.setCurrentRow(1)  # 赤 -> あ, sorts past 白

        # The premise: the row really did move, so the guard is under test.
        assert dlg._visual_row_for_index(0) != before

        assert _cell(dlg, 0, 1).text() == "あ"
        assert _cell(dlg, 0, 2).text() == "あ"
        assert "あと遊ぶ" in _cell(dlg, 0, 4).text()
        # The untouched word kept every one of its own values.
        assert _cell(dlg, 1, 1).text() == "白"
        assert _cell(dlg, 1, 2).text() == "白"
        assert "白が走る" in _cell(dlg, 1, 4).text()

    def test_the_row_is_still_addressable_after_a_re_sort(self, qtbot):
        from PyQt6.QtCore import Qt

        dlg = self._sorted_pair(qtbot)
        _select_and_fire(dlg, dlg._visual_row_for_index(0))
        dlg.sentence_list.setCurrentRow(1)

        row = dlg._visual_row_for_index(0)
        assert row is not None
        assert dlg.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == 0


class TestPickRefreshesDefinitionPane:
    """The definition beside the word must follow the pick as well: for
    surface-mined POS the pick moves ``mined_form``, which is the lookup key."""

    @pytest.fixture()
    def noun_words(self):
        return [_noun_with_varying_surface()]

    @pytest.fixture()
    def sync_off_thread(self, monkeypatch):
        """Run every dispatched lookup inline, on the calling thread."""

        def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
            on_done(work())
            return MagicMock()

        monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)

    def test_a_pick_looks_up_the_chosen_mined_form(self, qtbot, noun_words, sync_off_thread):
        terms: list[str] = []

        def lookup(term: str, lemma: str | None = None):
            terms.append(term)
            return [(term, f"gloss for {term}")]

        dlg = WordCurationDialog(noun_words, lookup_fn=lookup)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        terms.clear()

        dlg.sentence_list.setCurrentRow(1)

        assert terms == ["子ども"]
        assert "子ども" in dlg.definition_view.toHtml()

    def test_refocusing_after_a_pick_keeps_the_chosen_entry(self, qtbot, noun_words, sync_off_thread):
        """The focus path resolved its lookup off the primary, so arrowing away
        and back re-rendered the first occurrence's definition."""
        terms: list[str] = []

        def lookup(term: str, lemma: str | None = None):
            terms.append(term)
            return [(term, f"gloss for {term}")]

        dlg = WordCurationDialog(noun_words, lookup_fn=lookup)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)
        dlg.sentence_list.setCurrentRow(1)
        terms.clear()

        _select_and_fire(dlg, 0)

        assert terms[-1:] == ["子ども"] or terms == []  # cached hits skip lookup_fn
        assert "子ども" in dlg.definition_view.toHtml()
        assert "子供" not in dlg.definition_view.toHtml()

    def test_a_superseded_pick_never_paints(self, qtbot, noun_words, monkeypatch):
        """A reply for a pick the user has already moved off must not paint.

        The pick calls the lookup directly rather than through the focus
        debounce, so the generation stamp is the only thing standing between a
        slow query and the pane repainting the wrong entry.
        """
        pending: list[tuple] = []

        def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
            pending.append((work, on_done))
            return MagicMock()

        monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)

        dlg = WordCurationDialog(noun_words, lookup_fn=lambda term, lemma=None: [(term, f"gloss for {term}")])
        qtbot.addWidget(dlg)
        # Land the focus lookup first: only one request is ever in flight, so a
        # pick made while it is outstanding would queue rather than dispatch.
        _select_and_fire(dlg, 0)
        work, done = pending.pop()
        done(work())
        assert "子供" in dlg.definition_view.toHtml()

        dlg.sentence_list.setCurrentRow(1)  # dispatches the 子ども lookup
        assert pending, "the pick must dispatch its own lookup"
        dlg.sentence_list.setCurrentRow(0)  # user moves back; 子供 is cached, paints now

        assert "子供" in dlg.definition_view.toHtml()

        # The 子ども reply lands late, against a stale generation.
        work, done = pending.pop()
        done(work())
        assert "子ども" not in dlg.definition_view.toHtml()
        assert "子供" in dlg.definition_view.toHtml()


class TestPhraseWrappedDisplay:
    """Candidate rows wrap at BudouX phrase boundaries — display text only.

    The joiner is U+2060 WORD JOINER, injected by ``phrase_wrap_ja``. Every
    other surface stays pristine: ``COPY_ROLE`` (what Ctrl+C lifts), the
    tooltip, and the candidate model itself, so no invisible character can
    reach the clipboard or a card.
    """

    def test_display_wrapped_copy_tooltip_and_model_pristine(self, qtbot, mixed_words):
        dlg = WordCurationDialog(mixed_words)
        qtbot.addWidget(dlg)
        _select_and_fire(dlg, 0)

        cands = mixed_words[0].sentence_candidates
        assert dlg.sentence_list.count() == len(cands)
        saw_joiner = False
        for i, cand in enumerate(cands):
            item = dlg.sentence_list.item(i)
            assert item.text().replace(WORD_JOINER, "") == cand.sentence
            assert item.data(COPY_ROLE) == cand.sentence
            assert item.toolTip() == cand.sentence
            saw_joiner = saw_joiner or WORD_JOINER in item.text()
        # The fixtures are real multi-phrase sentences; at least one must
        # actually gain a boundary or the transform is wired to nothing.
        assert saw_joiner
        assert all(WORD_JOINER not in cand.sentence for cand in cands)
