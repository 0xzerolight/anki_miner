"""The frame pick driven through a REAL SubtitlePlayerWidget.

``test_word_curation_screenshot_frame.py`` replaces the player with a MagicMock,
which is right for wiring assertions but mocks away the whole path the reported
bug lived on: mpv's ``time-pos`` observer crosses a thread boundary by queued
signal, so the position slider is only as fresh as the last tick the GUI thread
actually processed. Under load it trails the picture, and a pick read off it
stamps a frame the viewer already watched go past.

Only the mpv core itself is faked here. The local fixture duplicates the one in
``test_subtitle_player_widget.py`` on purpose rather than moving it to the shared
conftest: it has to be active during ``WordCurationDialog.__init__``, which is
where the real player widget is built and pointed at a source.
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

PLAYER_MODULE = "anki_miner.gui.widgets.subtitle_player_widget"
ENTRIES = [(5.0, 7.0, "食べる"), (20.0, 22.0, "走る")]
FPS = 15


@pytest.fixture()
def fake_mpv():
    """Fake the mpv core at the player module's seam; yields the fake player."""
    from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget

    player = MagicMock(name="mpv.MPV")
    player.pause = True
    player.track_list = []
    player.time_pos = None
    player.event_callback.return_value = lambda fn: fn
    with (
        patch.object(MpvVideoWidget, "has_render_context", property(lambda self: True)),
        patch(f"{PLAYER_MODULE}.mpv_available", return_value=True),
        patch(f"{PLAYER_MODULE}.create_mpv_player", return_value=player),
    ):
        yield player


@pytest.fixture()
def words():
    return [
        TokenizedWord(
            surface="食べた",
            lemma="食べる",
            reading="タベル",
            sentence="食べるのテスト",
            start_time=5.0,
            end_time=7.0,
            duration=2.0,
            pos="動詞",
        )
    ]


@pytest.fixture()
def video(tmp_path) -> Path:
    path = tmp_path / "episode.mkv"
    path.write_bytes(b"\x00")
    return path


def _dialog(qtbot, words, video) -> WordCurationDialog:
    ctx = CurationMediaContext(video_file=video, subtitle_entries=list(ENTRIES))
    dlg = WordCurationDialog(words, media_context=ctx)
    qtbot.addWidget(dlg)  # weak ref only — the caller holds the strong one
    dlg.player_widget._on_file_loaded()
    dlg.player_widget._on_duration(60.0)
    return dlg


def _focus(dlg: WordCurationDialog, row: int) -> None:
    dlg.table.setCurrentCell(row, 0)
    dlg._on_row_focus_changed()
    dlg._focus_timer.stop()
    dlg._on_focus_timer_fired()


class TestPickWhilePlaying:
    def test_the_pick_takes_the_live_position_not_the_stale_slider(self, qtbot, fake_mpv, words, video):
        """The reported bug, end to end through the real slider.

        The tick for frame 31 never reaches the GUI thread, so the slider still
        says frame 30. The card has to get 31 — the frame on screen.
        """
        dlg = _dialog(qtbot, words, video)
        _focus(dlg, 0)
        dlg.player_widget._on_time_pos(30 / FPS)  # last tick delivered
        fake_mpv.pause = False
        fake_mpv.time_pos = 31 / FPS  # mpv has moved on; no tick for it

        dlg.use_frame_button.click()

        assert dlg._screenshot_overrides[0] == pytest.approx(31 / FPS)
        assert dlg.player_widget.position_slider.value() == 2000  # provably stale

    def test_the_pick_pauses_the_mpv_core(self, qtbot, fake_mpv, words, video):
        dlg = _dialog(qtbot, words, video)
        _focus(dlg, 0)
        fake_mpv.pause = False
        fake_mpv.time_pos = 31 / FPS

        dlg.use_frame_button.click()

        assert fake_mpv.pause is True

    def test_mid_drag_the_handle_still_wins(self, qtbot, fake_mpv, words, video):
        """The user points at the frame they want; mpv is still catching up."""
        dlg = _dialog(qtbot, words, video)
        _focus(dlg, 0)
        dlg.player_widget.position_slider.setSliderDown(True)
        dlg.player_widget.position_slider.setValue(1541)
        fake_mpv.time_pos = 9.0

        dlg.use_frame_button.click()

        assert dlg._screenshot_overrides[0] == 1.541

    def test_a_step_then_a_pick_lands_on_the_stepped_frame(self, qtbot, fake_mpv, words, video):
        dlg = _dialog(qtbot, words, video)
        _focus(dlg, 0)
        fake_mpv.command.reset_mock()

        dlg.player_widget.frame_forward_button.click()
        fake_mpv.command.assert_called_once_with("frame-step")
        # The step's own position tick, which mpv would deliver next.
        fake_mpv.time_pos = 32 / FPS
        dlg.player_widget._on_time_pos(32 / FPS)
        dlg.use_frame_button.click()

        assert dlg._screenshot_overrides[0] == pytest.approx(32 / FPS)

    def test_the_picked_frame_rides_the_selected_word(self, qtbot, fake_mpv, words, video):
        dlg = _dialog(qtbot, words, video)
        _focus(dlg, 0)
        fake_mpv.time_pos = 31 / FPS
        dlg.use_frame_button.click()
        dlg.table.item(0, 0).setCheckState(Qt.CheckState.Checked)

        selected = dlg.get_selected_words()

        assert [w.screenshot_override for w in selected] == [pytest.approx(31 / FPS)]
        assert words[0].screenshot_override is None  # the source word is untouched
