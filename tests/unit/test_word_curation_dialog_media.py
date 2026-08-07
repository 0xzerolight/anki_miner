"""Tests for WordCurationDialog media/dictionary extensions (Task 3).

Covers:
1. Backward-compat: plain ``WordCurationDialog(words)`` construction.
2. Player seek called on row selection (debounce timer driven directly).
3. Dictionary lookup rendered; cache prevents double-calls.
4. Lookup uses word.lemma, not word.mined_form.
5. Missing video file → no crash; table + dict still work.
6. The lookup itself runs off the GUI thread, one request at a time, with a
   generation guard so a fast scroll cannot paint a stale entry.

The dictionary tests replace ``run_off_thread`` with a synchronous or deferred
stub, exactly as ``test_word_curation_dialog_image.py`` does for page loads.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
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


@pytest.fixture()
def sync_off_thread(monkeypatch):
    """Run every dispatched job inline, on the calling thread."""

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - mirrors SingleCallWorker's error path
            if on_error is not None:
                on_error(str(exc))
            return MagicMock()
        on_done(result)
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)


@pytest.fixture()
def deferred_off_thread(monkeypatch):
    """Capture (work, on_done, on_error) without running them — for overlap tests."""
    pending: list[tuple] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        pending.append((work, on_done, on_error))
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)
    return pending


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
# 1b. Owned non-modal window (decision D33)
# ---------------------------------------------------------------------------


class TestOwnedNonModalWindow:
    """The curator is a window the user works in, not a modal interruption."""

    def test_is_a_top_level_window(self, qtbot, words):
        from PyQt6.QtWidgets import QWidget

        parent = QWidget()
        qtbot.addWidget(parent)
        dlg = WordCurationDialog(words, parent)
        qtbot.addWidget(dlg)

        assert dlg.isWindow()
        assert bool(dlg.windowFlags() & Qt.WindowType.Window)
        assert dlg.parent() is parent  # owned by its tab, not free-floating

    def test_is_non_modal(self, qtbot, words):
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)

        assert dlg.windowModality() == Qt.WindowModality.NonModal
        assert dlg.isModal() is False

    def test_showing_it_blocks_nothing(self, qtbot, words):
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication

        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg.show()

        assert QApplication.activeModalWidget() is None
        assert QGuiApplication.modalWindow() is None

    def test_min_max_hints_survive_the_window_configuration(self, qtbot, words):
        """``add_min_max_buttons`` and the D33 flags must not clobber each other."""
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)

        flags = dlg.windowFlags()
        assert bool(flags & Qt.WindowType.WindowMinimizeButtonHint)
        assert bool(flags & Qt.WindowType.WindowMaximizeButtonHint)


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


@pytest.mark.usefixtures("sync_off_thread")
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
# 4. Lookup uses word.mined_form first, retrying word.lemma on a miss
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("sync_off_thread")
class TestLookupUsesMinedForm:
    """The pane queries the card-front spelling (mined_form) — unidic's lemma
    collapses kanji variants (殺る → 遣る), so a lemma-keyed pane showed the
    wrong homograph's entry. On a miss it retries once under the lemma."""

    def _variant_word(self):
        # 殺る: verb, orth_base keeps the source spelling, lemma is unidic's
        # canonical 遣る → mined_form (殺る) != lemma (遣る).
        word = TokenizedWord(
            surface="殺る",
            lemma="遣る",
            reading="やる",
            sentence="殺るのテスト",
            start_time=1.0,
            end_time=3.0,
            duration=2.0,
            pos="動詞",
            orth_base="殺る",
        )
        assert word.mined_form == "殺る"
        assert word.lemma != word.mined_form
        return word

    def test_lookup_uses_mined_form_when_it_hits(self, qtbot):
        received: list[str] = []

        def capturing_lookup(term: str) -> list[tuple[str, str]]:
            received.append(term)
            return [("JMdict", "<div>to do someone in</div>")]

        dlg = WordCurationDialog([self._variant_word()], lookup_fn=capturing_lookup)
        qtbot.addWidget(dlg)
        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert received == ["殺る"]
        assert "to do someone in" in dlg.definition_view.toHtml()

    def test_lookup_retries_lemma_on_miss(self, qtbot):
        received: list[str] = []

        def capturing_lookup(term: str) -> list[tuple[str, str]]:
            received.append(term)
            return [] if term == "殺る" else [("JMdict", "<div>to do</div>")]

        dlg = WordCurationDialog([self._variant_word()], lookup_fn=capturing_lookup)
        qtbot.addWidget(dlg)
        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert received == ["殺る", "遣る"]
        assert "to do" in dlg.definition_view.toHtml()

    def test_both_miss_placeholder_names_mined_form(self, qtbot):
        dlg = WordCurationDialog([self._variant_word()], lookup_fn=lambda term: [])
        qtbot.addWidget(dlg)
        _select_row(dlg, 0)
        _fire_timer(dlg)

        html = dlg.definition_view.toHtml()
        assert "No offline dictionary entry" in html
        assert "殺る" in html


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

    def test_nonexistent_video_dict_still_works(self, qtbot, words, sync_off_thread):
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
        """player_widget.release() is called when the dialog is rejected (Cancel path).

        ``release`` (not ``stop``) so an in-flight ffprobe probe is joined too.
        """
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        dlg.reject()

        mock_player.release.assert_called_once()

    def test_stop_called_on_accept(self, qtbot, words, existing_video):
        """player_widget.release() is called when the dialog is accepted (Confirm path).

        ``release`` (not ``stop``) so an in-flight ffprobe probe is joined too.
        """
        ctx = _make_media_context(video_file=existing_video)
        dlg, mock_player = _build_dialog_with_mock_player(qtbot, words, ctx)

        dlg.accept()

        mock_player.release.assert_called_once()

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

    def test_rapid_changes_coalesce_to_last_row(self, qtbot, words, sync_off_thread):
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


# ---------------------------------------------------------------------------
# 7. The lookup runs off the GUI thread, serialized, with a stale guard
# ---------------------------------------------------------------------------


def _entry_lookup(received: list[str]):
    """A lookup_fn that records its calls and answers with the term it was given."""

    def lookup(term: str) -> list[tuple[str, str]]:
        received.append(term)
        return [("JMdict", f"<div>{term} entry</div>")]

    return lookup


class TestLookupIsAsynchronous:
    """The curator is the app's most keyboard-driven screen; a dictionary hit on
    the GUI thread blocks arrow-key navigation for as long as the query takes."""

    def test_lookup_is_dispatched_not_run_inline(self, qtbot, words, deferred_off_thread):
        received: list[str] = []
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup(received))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert len(deferred_off_thread) == 1, "lookup was not handed to run_off_thread"
        assert received == [], "lookup_fn ran on the GUI thread"

        work, on_done, _ = deferred_off_thread[0]
        on_done(work())

        assert received == ["食べる"]
        assert "食べる entry" in dlg.definition_view.toHtml()

    def test_only_one_lookup_is_in_flight_at_a_time(self, qtbot, words, deferred_off_thread):
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup([]))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)
        _select_row(dlg, 1)
        _fire_timer(dlg)

        assert len(deferred_off_thread) == 1

    def test_only_the_latest_pending_request_is_kept(self, qtbot, deferred_off_thread):
        received: list[str] = []
        three = [
            _make_word("食べる", start_time=1.0),
            _make_word("走る", start_time=5.0),
            _make_word("泳ぐ", start_time=9.0),
        ]
        dlg = WordCurationDialog(three, lookup_fn=_entry_lookup(received))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)  # dispatched
        _select_row(dlg, 1)
        _fire_timer(dlg)  # queued
        _select_row(dlg, 2)
        _fire_timer(dlg)  # replaces the queued one

        work, on_done, _ = deferred_off_thread[0]
        on_done(work())

        assert len(deferred_off_thread) == 2
        work2, on_done2, _ = deferred_off_thread[1]
        on_done2(work2())

        assert received == ["食べる", "泳ぐ"], "the skipped-over row was fetched anyway"
        assert "泳ぐ entry" in dlg.definition_view.toHtml()

    def test_stale_success_is_not_painted(self, qtbot, words, deferred_off_thread):
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup([]))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)  # row 0 in flight

        # Row 1 resolves from cache, so it paints immediately and supersedes.
        dlg._lookup_cache["走る"] = [("JMdict", "<div>fresher</div>")]
        _select_row(dlg, 1)
        _fire_timer(dlg)
        assert "fresher" in dlg.definition_view.toHtml()

        work, on_done, _ = deferred_off_thread[0]
        on_done(work())  # the superseded row-0 result lands late

        html = dlg.definition_view.toHtml()
        assert "fresher" in html
        assert "食べる entry" not in html

    def test_stale_error_is_not_painted(self, qtbot, words, deferred_off_thread):
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup([]))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)

        dlg._lookup_cache["走る"] = [("JMdict", "<div>fresher</div>")]
        _select_row(dlg, 1)
        _fire_timer(dlg)

        _, _, on_error = deferred_off_thread[0]
        on_error("boom")

        assert "fresher" in dlg.definition_view.toHtml()

    def test_a_late_result_is_still_cached(self, qtbot, words, deferred_off_thread):
        """Dropping a stale paint must not throw away the fetched entry."""
        received: list[str] = []
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup(received))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)
        dlg._lookup_cache["走る"] = [("JMdict", "<div>fresher</div>")]
        _select_row(dlg, 1)
        _fire_timer(dlg)

        work, on_done, _ = deferred_off_thread[0]
        on_done(work())

        # Coming back to row 0 must not re-query.
        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert received == ["食べる"]
        assert "食べる entry" in dlg.definition_view.toHtml()

    def test_cache_hit_renders_without_dispatching(self, qtbot, words, deferred_off_thread):
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup([]))
        qtbot.addWidget(dlg)
        dlg._lookup_cache["食べる"] = [("JMdict", "<div>cached</div>")]

        _select_row(dlg, 0)
        _fire_timer(dlg)

        assert deferred_off_thread == []
        assert "cached" in dlg.definition_view.toHtml()

    def test_failure_shows_the_placeholder(self, qtbot, words, deferred_off_thread):
        dlg = WordCurationDialog(words, lookup_fn=_entry_lookup([]))
        qtbot.addWidget(dlg)

        _select_row(dlg, 0)
        _fire_timer(dlg)
        _, _, on_error = deferred_off_thread[0]
        on_error("boom")

        assert "No offline dictionary entry" in dlg.definition_view.toHtml()


class TestPreviewSuppressedInCurator:
    """The curator with the video preview turned off.

    The pane STAYS (so ``_side_key`` and every saved splitter layout stay too);
    only the GL surface inside it is gone.
    """

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        from anki_miner.gui.utils import video_preview

        video_preview._reset_for_tests()
        monkeypatch.setattr(video_preview, "_enabled", False)
        yield
        video_preview._reset_for_tests()

    def test_no_qopenglwidget_anywhere_in_the_dialog(self, qtbot, words, existing_video):
        """The curator is the mandatory path for every video mine, so this is
        the assertion that says that path no longer touches GL."""
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget

        ctx = _make_media_context(video_file=existing_video)
        dlg = WordCurationDialog(words, media_context=ctx, lookup_fn=lambda _term: [])
        qtbot.addWidget(dlg)
        assert dlg.findChildren(QOpenGLWidget) == []

    def test_player_pane_and_side_key_are_unchanged(self, qtbot, words, existing_video):
        """Gating the pane itself would change _side_key and orphan every saved
        side-split blob. Gating the child does not."""
        ctx = _make_media_context(video_file=existing_video)
        dlg = WordCurationDialog(words, media_context=ctx, lookup_fn=lambda _term: [])
        qtbot.addWidget(dlg)
        assert dlg._show_player
        assert "player" in dlg._side_key
        assert hasattr(dlg, "player_widget")
