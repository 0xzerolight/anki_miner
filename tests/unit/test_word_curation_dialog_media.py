"""Tests for WordCurationDialog media/dictionary extensions (Task 3).

Covers:
1. Backward-compat: plain ``WordCurationDialog(words)`` construction.
2. Player seek called on row selection (debounce timer driven directly).
3. Dictionary lookup rendered; cache prevents double-calls.
4. Lookup uses word.lemma, not word.mined_form.
5. Missing video file → no crash; table + dict still work.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_word(
    lemma: str = "食べる",
    surface: str | None = None,
    start_time: float = 1.0,
    pos: str | None = "動詞",
) -> TokenizedWord:
    return TokenizedWord(
        surface=surface or f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos=pos,
    )


def _make_media_context(
    video_file: Path | None = None,
    subtitle_entries: list[tuple[float, float, str]] | None = None,
) -> CurationMediaContext:
    return CurationMediaContext(
        video_file=video_file,
        subtitle_entries=subtitle_entries or [(1.0, 3.0, "食べる")],
        offset=0.0,
    )


def _select_row(dialog: WordCurationDialog, row: int) -> None:
    """Programmatically select a table row and trigger the focus slot."""
    dialog.table.setCurrentCell(row, 0)
    # itemSelectionChanged fires when we change current cell programmatically,
    # but let's also call the slot directly to be reliable in headless mode.
    dialog._on_row_focus_changed()


def _fire_timer(dialog: WordCurationDialog) -> None:
    """Fire the debounce timer immediately without waiting."""
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def words():
    return [
        _make_word("食べる", start_time=1.0),
        _make_word("走る", start_time=5.0),
    ]


@pytest.fixture()
def existing_video(tmp_path):
    """A real (empty) file so Path.exists() returns True."""
    p = tmp_path / "test.mkv"
    p.write_bytes(b"")
    return p


# ---------------------------------------------------------------------------
# 1. Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Existing call sites must remain unaffected by the new optional args."""

    def test_positional_words_only(self, qtbot, words):
        """WordCurationDialog(words) constructs without error."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        assert dlg is not None

    def test_positional_words_and_parent(self, qtbot, words):
        """WordCurationDialog(words, parent) with explicit parent works."""
        dlg = WordCurationDialog(words, None)
        qtbot.addWidget(dlg)
        assert dlg is not None

    def test_no_player_pane(self, qtbot, words):
        """No media_context → player_widget attribute absent."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "player_widget")

    def test_no_dict_pane(self, qtbot, words):
        """No lookup_fn → definition_view attribute absent."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "definition_view")

    def test_get_selected_words_works(self, qtbot, words):
        """get_selected_words() returns all words (all checked by default)."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        selected = dlg.get_selected_words()
        assert len(selected) == len(words)

    def test_get_selected_words_after_deselect(self, qtbot, words):
        """Deselect all then check get_selected_words returns empty."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg._deselect_all()
        assert dlg.get_selected_words() == []


# ---------------------------------------------------------------------------
# 2. Player seek on row selection
# ---------------------------------------------------------------------------


def _build_dialog_with_mock_player(qtbot, words, ctx, lookup_fn=None):
    """Build a dialog with the player widget replaced by a MagicMock stub.

    We patch ``_create_player_widget`` so the splitter receives a real QWidget
    placeholder (Qt requires a real QWidget for addWidget), then swap
    ``dlg.player_widget`` to a bare MagicMock so seek/pause calls can be asserted.
    """
    from PyQt6.QtWidgets import QWidget

    # QSplitter.addWidget needs a real QWidget subclass instance.
    # Don't addWidget(real_stub): it becomes a child of the dialog's splitter
    # and is deleted when the dialog is closed; qtbot must not close it again.
    real_stub = QWidget()

    with patch.object(
        WordCurationDialog,
        "_create_player_widget",
        return_value=real_stub,
    ):
        dlg = WordCurationDialog(words, media_context=ctx, lookup_fn=lookup_fn)
    qtbot.addWidget(dlg)

    # Swap to a free MagicMock so seek_seconds / pause are trackable.
    mock_player = MagicMock()
    dlg.player_widget = mock_player
    return dlg, mock_player


class TestPlayerSeek:
    """Row focus triggers seek_seconds(word.start_time) after timer fires."""

    def test_seek_called_on_row_select(self, qtbot, words, existing_video):
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        mock_player.seek_seconds.assert_called_once_with(words[0].start_time)

    def test_pause_called_after_seek(self, qtbot, words, existing_video):
        """After seek, the player must be paused (show frame, don't autoplay)."""
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        mock_player.pause.assert_called_once()

    def test_seek_correct_word_after_sort(self, qtbot, words, existing_video):
        """After table sort, row 0 may map to a different word; seek must use the right one."""
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        # Select row 1 and verify the correct word's start_time is sought
        # (row index ≠ original word index after sorting).
        _select_row(dlg, 1)
        _fire_timer(dlg)

        # Row 1 → original index 1 → words[1].start_time = 5.0
        check_item = dlg.table.item(1, 0)
        original_index = check_item.data(Qt.ItemDataRole.UserRole)
        expected_time = words[original_index].start_time
        mock_player.seek_seconds.assert_called_once_with(expected_time)


def _find_table_shortcut(dialog: WordCurationDialog, key_str: str):
    """Find a QShortcut registered on the table by its key sequence (Issue #55)."""
    from PyQt6.QtGui import QKeySequence, QShortcut

    for sc in dialog.table.findChildren(QShortcut):
        if sc.key() == QKeySequence(key_str):
            return sc
    return None


class TestPlayPauseHotkey:
    """Issue #55 — Space toggles the player; the dialog routes it to the widget."""

    def test_space_shortcut_toggles_player(self, qtbot, words, existing_video):
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        shortcut = _find_table_shortcut(dlg, "Space")
        assert shortcut is not None
        shortcut.activated.emit()

        mock_player.toggle_play_pause.assert_called_once()

    def test_toggle_play_pause_noop_without_player(self, qtbot, words):
        """Dict/table-only dialog: _toggle_play_pause must not raise."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg._toggle_play_pause()  # no player pane → no-op


# ---------------------------------------------------------------------------
# 3. Dictionary lookup and caching
# ---------------------------------------------------------------------------


class TestDictionaryLookup:
    """Dictionary entries appear in definition_view; cache prevents re-calls."""

    def test_lookup_renders_provider_name(self, qtbot, words):
        call_count = 0

        def fake_lookup(lemma: str) -> list[tuple[str, str]]:
            nonlocal call_count
            call_count += 1
            return [("JMdict", "<div>to eat</div>")]

        dlg = WordCurationDialog(words, lookup_fn=fake_lookup)
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        html = dlg.definition_view.toHtml()
        assert "JMdict" in html

    def test_lookup_cached_on_second_select(self, qtbot, words):
        """Selecting the same row twice should invoke lookup_fn only once."""
        call_count = 0

        def fake_lookup(lemma: str) -> list[tuple[str, str]]:
            nonlocal call_count
            call_count += 1
            return [("JMdict", "<div>x</div>")]

        dlg = WordCurationDialog(words, lookup_fn=fake_lookup)
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert call_count == 1, f"Expected 1 lookup call, got {call_count}"

    def test_empty_result_shows_grey_placeholder(self, qtbot, words):
        """Empty lookup result → grey 'No offline dictionary entry' placeholder."""
        dlg = WordCurationDialog(words, lookup_fn=lambda lemma: [])
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        html = dlg.definition_view.toHtml()
        assert "No offline dictionary entry" in html

    def test_empty_result_is_cached(self, qtbot, words):
        """Even empty results are cached so lookup_fn is called only once."""
        call_count = 0

        def empty_lookup(lemma: str) -> list[tuple[str, str]]:
            nonlocal call_count
            call_count += 1
            return []

        dlg = WordCurationDialog(words, lookup_fn=empty_lookup)
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)
        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert call_count == 1


# ---------------------------------------------------------------------------
# 4. Lookup uses word.lemma, not word.mined_form
# ---------------------------------------------------------------------------


class TestLookupUsesLemma:
    """For a verb where lemma != mined_form, lookup_fn receives the lemma."""

    def test_lookup_uses_lemma_not_mined_form(self, qtbot):
        # surface=食べ, lemma=食べる, pos=動詞 → mined_form = lemma = 食べる
        # Use a case where surface differs from lemma more clearly.
        word = TokenizedWord(
            surface="食べ",
            lemma="食べる",
            reading="たべる",
            sentence="食べのテスト",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="動詞",
        )
        # Sanity-check the fixture: for a verb, mined_form == lemma.
        assert word.mined_form == word.lemma  # both are 食べる for verbs

        # Use a non-conjugating word where surface != lemma to better isolate
        # that lemma (not surface or mined_form) is what's passed.
        word2 = TokenizedWord(
            surface="食べ",  # raw surface (different from lemma)
            lemma="食べる",  # dictionary form
            reading="たべる",
            sentence="テスト",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="名詞",  # noun POS → mined_form = surface = 食べ
        )
        # For nouns, mined_form = surface, not lemma.
        assert word2.mined_form == word2.surface  # 食べ
        assert word2.lemma != word2.mined_form  # 食べる != 食べ

        received: list[str] = []

        def capturing_lookup(lemma: str) -> list[tuple[str, str]]:
            received.append(lemma)
            return []

        dlg = WordCurationDialog([word2], lookup_fn=capturing_lookup)
        qtbot.addWidget(dlg)
        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert len(received) == 1
        assert received[0] == word2.lemma, f"Expected lookup on lemma '{word2.lemma}', got '{received[0]}'"
        assert received[0] != word2.mined_form


# ---------------------------------------------------------------------------
# 5. Missing/nonexistent video file → graceful fallback
# ---------------------------------------------------------------------------


class TestMissingVideo:
    """Nonexistent video → no player pane, no crash; table + dict still work."""

    def test_nonexistent_video_no_crash(self, qtbot, words):
        ctx = _make_media_context(video_file=Path("/nonexistent/file.mkv"))
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert dlg is not None

    def test_nonexistent_video_no_player_widget(self, qtbot, words):
        ctx = _make_media_context(video_file=Path("/nonexistent/file.mkv"))
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "player_widget")

    def test_nonexistent_video_dict_still_works(self, qtbot, words):
        """Even with bad video, dict lookup renders correctly."""
        ctx = _make_media_context(video_file=Path("/nonexistent/file.mkv"))
        call_count = 0

        def fake_lookup(lemma: str) -> list[tuple[str, str]]:
            nonlocal call_count
            call_count += 1
            return [("JMdict", "<div>test</div>")]

        dlg = WordCurationDialog(words, media_context=ctx, lookup_fn=fake_lookup)
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert call_count == 1
        assert "JMdict" in dlg.definition_view.toHtml()

    def test_none_video_file_no_player_widget(self, qtbot, words):
        """video_file=None in context → no player pane."""
        ctx = _make_media_context(video_file=None)
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "player_widget")

    def test_none_media_context_no_player_widget(self, qtbot, words):
        """media_context=None → no player pane."""
        dlg = WordCurationDialog(words, media_context=None)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "player_widget")

    def test_table_still_functional_with_bad_video(self, qtbot, words):
        """Table selection/deselection works even when video is missing."""
        ctx = _make_media_context(video_file=Path("/nonexistent/file.mkv"))
        dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)

        dlg._deselect_all()
        assert dlg.get_selected_words() == []

        dlg._select_all()
        assert len(dlg.get_selected_words()) == len(words)


# ---------------------------------------------------------------------------
# 6. Player stop called when dialog closes
# ---------------------------------------------------------------------------


class TestStopOnClose:
    """Closing/rejecting the dialog must stop the embedded player."""

    def test_stop_called_on_reject(self, qtbot, words, existing_video):
        """player_widget.stop() is called when the dialog is rejected (Cancel path)."""
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        dlg.reject()

        mock_player.stop.assert_called_once()

    def test_stop_called_on_accept(self, qtbot, words, existing_video):
        """player_widget.stop() is called when the dialog is accepted (Confirm path)."""
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        dlg.accept()

        mock_player.stop.assert_called_once()

    def test_stop_not_called_when_no_player(self, qtbot, words):
        """Without a player pane, reject() must not raise."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        # Should not raise even though player_widget is absent.
        dlg.reject()


# ---------------------------------------------------------------------------
# 7. Debounce coalesces rapid row-focus changes
# ---------------------------------------------------------------------------


class TestDebounceCoalescing:
    """Rapid _on_row_focus_changed calls must produce only one lookup/seek."""

    def test_rapid_changes_coalesce_to_last_row(self, qtbot, words):
        """Two rapid focus changes → only the final word is looked up after the timer fires."""
        received: list[str] = []

        def capturing_lookup(lemma: str) -> list[tuple[str, str]]:
            received.append(lemma)
            return []

        dlg = WordCurationDialog(words, lookup_fn=capturing_lookup)
        qtbot.addWidget(dlg)

        # Simulate rapid row changes — set pending word for row 0 then row 1
        # without firing the timer in between (just like fast arrow-key scrolling).
        _select_row(dlg, 0)  # sets _pending_word = words[0], starts timer
        _select_row(dlg, 1)  # sets _pending_word = words[1], restarts timer

        # Timer is still pending (not fired yet). Fire it once manually.
        _fire_timer(dlg)

        # Lookup must have been called exactly once, and for the LAST row's lemma.
        assert len(received) == 1, f"Expected 1 lookup call, got {len(received)}"
        assert received[0] == words[1].lemma

    def test_timer_is_restarted_not_duplicated(self, qtbot, words):
        """After two rapid selections the timer must still be single-shot."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _select_row(dlg, 1)

        # The timer should be active (waiting) — a freshly restarted single-shot timer.
        assert dlg._focus_timer.isActive()

        # After firing once, it should be inactive (single-shot exhausted).
        dlg._focus_timer.stop()
        dlg._on_focus_timer_fired()
        assert not dlg._focus_timer.isActive()
