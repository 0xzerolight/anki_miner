"""Tests for the word curator's per-word screenshot frame override.

The clip strip and the line-expansion row have their own modules; this one owns
the frame buttons — when they appear, which index a pick lands on, what
invalidates it, and what ``get_selected_words`` hands the extraction phase.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord
from anki_miner.services.media_extractor import resolve_screenshot_time

ENTRIES = [(5.0, 7.0, "食べる"), (20.0, 22.0, "走る")]


def _make_word(lemma: str = "食べる", start_time: float = 5.0, **kwargs) -> TokenizedWord:
    return TokenizedWord(
        surface=kwargs.pop("surface", f"{lemma}た"),
        lemma=lemma,
        reading="タベル",
        sentence=kwargs.pop("sentence", f"{lemma}のテスト"),
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        pos="動詞",
        **kwargs,
    )


@pytest.fixture()
def words():
    return [_make_word("食べる", start_time=5.0), _make_word("走る", start_time=20.0)]


@pytest.fixture()
def existing_video(tmp_path) -> Path:
    video = tmp_path / "episode.mkv"
    video.write_bytes(b"\x00")
    return video


def _dialog(qtbot, words, video, *, seconds: float = 12.5, **ctx_kwargs):
    """Build a curator whose player is a MagicMock (as the media tests do)."""
    real_stub = QWidget()
    ctx = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES), **ctx_kwargs)
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=real_stub):
        dlg = WordCurationDialog(words, media_context=ctx)
    qtbot.addWidget(dlg)
    mock_player = MagicMock()
    mock_player.current_seconds = seconds
    dlg.player_widget = mock_player
    return dlg, mock_player


def _focus(dialog: WordCurationDialog, row: int) -> None:
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


class TestButtonPresence:
    def test_buttons_present_with_a_video_player(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert hasattr(dlg, "use_frame_button")
        assert hasattr(dlg, "frame_reset_button")

    def test_absent_on_a_table_only_curator(self, qtbot, words):
        with patch.object(WordCurationDialog, "_create_player_widget", return_value=QWidget()):
            dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "use_frame_button")

    def test_absent_when_animated_screenshots_are_on(self, qtbot, words, existing_video):
        """An animated screenshot's window comes from the clip, not one instant."""
        dlg, _ = _dialog(qtbot, words, existing_video, screenshot_animated=True)
        assert not hasattr(dlg, "use_frame_button")

    def test_absent_without_a_video_surface(self, qtbot, words, existing_video):
        """ANKI_MINER_NO_VIDEO_PREVIEW=1: audio still plays, but there is no frame."""
        stub = QWidget()
        stub.video_surface_available = False
        ctx = CurationMediaContext(video_file=existing_video, subtitle_entries=list(ENTRIES))
        with patch.object(WordCurationDialog, "_create_player_widget", return_value=stub):
            dlg = WordCurationDialog(words, media_context=ctx)
        qtbot.addWidget(dlg)
        assert not hasattr(dlg, "use_frame_button")

    def test_side_key_unchanged(self, qtbot, words, existing_video):
        """The buttons live inside the player pane, so saved layouts survive."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert dlg._side_key == "player"


class TestPreviewAgreement:
    """The preview parks on the frame the card's screenshot will be grabbed at.

    It used to park on the line's start while extraction grabbed
    ``screenshot_offset`` seconds later, so the frame the user judged and the
    frame they got were never the same picture. Expected values come from the
    extraction phase's own resolver, never from a number restated here.
    """

    def test_an_unpicked_word_previews_the_frame_the_card_gets(self, qtbot, words, existing_video):
        dlg, mock_player = _dialog(qtbot, words, existing_video, screenshot_offset=1.0)
        _focus(dlg, 0)
        expected = resolve_screenshot_time(words[0], 5.0, 2.0, 1.0)
        assert expected == 6.0  # the offset the card uses, not the cue start
        mock_player.seek_seconds.assert_called_once_with(expected)

    def test_a_short_cue_previews_the_capped_offset(self, qtbot, existing_video):
        """A cue shorter than twice the offset cannot overshoot its own window."""
        short = TokenizedWord(
            surface="走った",
            lemma="走る",
            reading="ハシル",
            sentence="走るのテスト",
            start_time=10.0,
            end_time=10.4,
            duration=0.4,
            pos="動詞",
        )
        dlg, mock_player = _dialog(qtbot, [short], existing_video, screenshot_offset=1.0)
        _focus(dlg, 0)
        expected = resolve_screenshot_time(short, 10.0, 0.4, 1.0)
        assert expected == pytest.approx(10.2)
        mock_player.seek_seconds.assert_called_once_with(expected)

    def test_a_picked_word_previews_its_picked_frame(self, qtbot, words, existing_video):
        dlg, mock_player = _dialog(qtbot, words, existing_video, seconds=12.5, screenshot_offset=1.0)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        mock_player.seek_seconds.reset_mock()
        _focus(dlg, 0)
        mock_player.seek_seconds.assert_called_once_with(12.5)

    def test_reset_reparks_on_the_computed_frame(self, qtbot, words, existing_video):
        dlg, mock_player = _dialog(qtbot, words, existing_video, seconds=12.5, screenshot_offset=1.0)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        mock_player.seek_seconds.reset_mock()
        dlg.frame_reset_button.click()
        qtbot.wait(20)  # the re-park is deferred out of the clicked handler
        mock_player.seek_seconds.assert_called_once_with(resolve_screenshot_time(words[0], 5.0, 2.0, 1.0))

    def test_the_preview_goes_through_the_shared_resolver(self, qtbot, words, existing_video, monkeypatch):
        """Not a re-derived formula: the dialog calls the extractor's resolver."""
        monkeypatch.setattr(
            "anki_miner.gui.widgets.dialogs.word_curation_dialog.resolve_screenshot_time",
            lambda *a, **k: 123.0,
        )
        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        mock_player.seek_seconds.assert_called_once_with(123.0)

    def test_the_context_offset_is_honoured(self, qtbot, words, existing_video):
        dlg, mock_player = _dialog(qtbot, words, existing_video, screenshot_offset=0.25)
        _focus(dlg, 0)
        mock_player.seek_seconds.assert_called_once_with(5.25)


class TestPauseOnPick:
    """A pick has to leave the stamped frame on screen, or it confirms nothing."""

    def test_pick_pauses_the_player(self, qtbot, words, existing_video):
        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        mock_player.reset_mock()  # focus already paused via _preview_scene
        dlg.use_frame_button.click()
        mock_player.pause.assert_called_once_with()

    def test_pause_happens_before_the_position_is_read(self, qtbot, words, existing_video):
        """Reading first would stamp a frame the picture has already left."""
        dlg, mock_player = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        order: list[str] = []
        mock_player.pause.side_effect = lambda: order.append("pause")
        type(mock_player).current_seconds = PropertyMock(side_effect=lambda: (order.append("read"), 31 / 15)[1])
        try:
            dlg.use_frame_button.click()
        finally:
            del type(mock_player).current_seconds
        assert order == ["pause", "read"]
        assert dlg._screenshot_overrides == {0: pytest.approx(31 / 15)}


class TestStamping:
    def test_click_records_the_players_position(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=12.5)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        assert dlg._screenshot_overrides == {0: 12.5}

    def test_click_records_a_frame_boundary_verbatim(self, qtbot, words, existing_video):
        """Real frame boundaries are not round: 15 fps frame 31 is 2.0666... s."""
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=31 / 15)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        assert dlg._screenshot_overrides == {0: pytest.approx(31 / 15)}
        assert "2.07" in dlg.frame_reset_button.toolTip()

    def test_click_records_against_the_focused_index(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=21.0)
        _focus(dlg, 1)
        dlg.use_frame_button.click()
        assert dlg._screenshot_overrides == {1: 21.0}

    def test_reset_drops_it(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        dlg.frame_reset_button.click()
        assert dlg._screenshot_overrides == {}

    def test_reset_is_enabled_only_with_an_override(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        assert not dlg.frame_reset_button.isEnabled()
        dlg.use_frame_button.click()
        assert dlg.frame_reset_button.isEnabled()
        _focus(dlg, 1)
        assert not dlg.frame_reset_button.isEnabled()

    def test_reset_tooltip_names_the_picked_second(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=12.5)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        assert "12.50" in dlg.frame_reset_button.toolTip()

    def test_pick_is_disabled_while_another_episode_is_displayed(self, qtbot, existing_video, tmp_path):
        """Season curation: the frame on screen is not this word's episode yet."""
        other = tmp_path / "ep2.mkv"
        other.write_bytes(b"\x00")
        dlg, _ = _dialog(qtbot, [_make_word("食べる", start_time=5.0, video_file=other)], existing_video)
        _focus(dlg, 0)
        assert not dlg.use_frame_button.isEnabled()

    def test_scrolling_records_nothing(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        _focus(dlg, 1)
        assert dlg._screenshot_overrides == {}


class TestSelection:
    def test_untouched_words_are_returned_unchanged(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video)
        assert all(w.screenshot_override is None for w in dlg.get_selected_words())

    def test_picked_frame_rides_the_selection(self, qtbot, words, existing_video):
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=12.5)
        _focus(dlg, 0)
        dlg.use_frame_button.click()

        selected = {w.lemma: w for w in dlg.get_selected_words()}

        assert selected["食べる"].screenshot_override == 12.5
        assert selected["走る"].screenshot_override is None

    def test_source_word_is_not_mutated(self, qtbot, words, existing_video):
        """Variants are shared with the filter service; the stamp rides a copy."""
        dlg, _ = _dialog(qtbot, words, existing_video)
        _focus(dlg, 0)
        dlg.use_frame_button.click()
        dlg.get_selected_words()
        assert words[0].screenshot_override is None


class TestInvalidation:
    @pytest.fixture()
    def picker_words(self):
        first = _make_word("食べる", start_time=5.0, sentence="一つ目")
        second = _make_word("食べる", start_time=40.0, sentence="二つ目")
        primary = _make_word("食べる", start_time=5.0, sentence="一つ目")
        primary.sentence_candidates = [first, second]
        return [primary]

    def test_a_sentence_pick_drops_the_frame(self, qtbot, picker_words, existing_video):
        """A pick moves the scene, so a frame chosen in the old one is meaningless."""
        dlg, _ = _dialog(qtbot, picker_words, existing_video)
        _focus(dlg, 0)
        dlg.use_frame_button.click()

        dlg._on_candidate_chosen(1)

        assert dlg._screenshot_overrides == {}

    def test_a_line_expansion_keeps_the_frame(self, qtbot, words, existing_video):
        """A merged line is the same scene extended — the frame is still in it."""
        dlg, _ = _dialog(qtbot, words, existing_video, seconds=12.5)
        _focus(dlg, 0)
        dlg.use_frame_button.click()

        dlg._on_expand_line(1)

        assert dlg._screenshot_overrides == {0: 12.5}
