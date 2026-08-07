"""Tests for subtitle_viewer — the timing workbench (D35-B, D49).

The mpv boundary is mocked at the player widget's import seam. Qt grabs black
for an mpv GL surface, so nothing here compares rendered pixels: every
assertion is about state, control flow, or the calls that reach the player.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QDialog, QPushButton

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
from anki_miner.gui.widgets.subtitle_viewer import NUDGE_SECONDS, SubtitleViewer

# Patch targets live in the player widget module (its mpv import seam).
PLAYER_MODULE = "anki_miner.gui.widgets.subtitle_player_widget"

ENTRIES = [(10.0, 12.0, "こんにちは"), (30.0, 32.0, "テスト\n二行目"), (50.0, 52.0, "さようなら")]


@pytest.fixture
def fake_mpv(monkeypatch):
    """Patch the player widget's mpv seam; yields the fake player instance.

    ``has_render_context`` is forced True so loadfile runs at set_source
    (offscreen unshown widgets never create a real render context; the
    deferred-load behavior is covered in test_subtitle_player_widget.py)."""
    from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget

    player = MagicMock(name="mpv.MPV")
    player.pause = True
    player.track_list = []
    player.event_callback.return_value = lambda fn: fn
    monkeypatch.setattr(MpvVideoWidget, "has_render_context", property(lambda self: True))
    with (
        patch(f"{PLAYER_MODULE}.mpv_available", return_value=True),
        patch(f"{PLAYER_MODULE}.create_mpv_player", return_value=player) as factory,
    ):
        yield {"player": player, "factory": factory}


def _viewer(qtbot, entries=None, offset=0.0) -> SubtitleViewer:
    viewer = SubtitleViewer(Path("/tmp/fake.mkv"), ENTRIES if entries is None else entries, offset)
    qtbot.addWidget(viewer)
    return viewer


def _spy_transport(viewer: SubtitleViewer) -> tuple[MagicMock, MagicMock]:
    """Replace seek/play with spies AFTER construction parked the first line."""
    seek = MagicMock(name="seek_seconds")
    play = MagicMock(name="play")
    viewer.player_widget.seek_seconds = seek  # type: ignore[method-assign]
    viewer.player_widget.play = play  # type: ignore[method-assign]
    return seek, play


class TestSubtitleViewerEmbeds:
    """Test that SubtitleViewer correctly embeds SubtitlePlayerWidget."""

    def test_viewer_has_player_widget_attribute(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        assert isinstance(viewer.player_widget, SubtitlePlayerWidget)
        assert viewer.player_widget.player is fake_mpv["player"]

    def test_viewer_calls_set_source_on_player_widget(self, qtbot, fake_mpv):
        entries = [(1.0, 2.0, "test")]
        viewer = _viewer(qtbot, entries=entries, offset=0.5)
        assert viewer.player_widget.subtitle_entries == entries
        assert viewer.player_widget._offset == 0.5
        fake_mpv["player"].loadfile.assert_called_once_with("/tmp/fake.mkv")

    def test_viewer_forwards_audio_track_override(self, qtbot, fake_mpv):
        viewer = SubtitleViewer(Path("/tmp/fake.mkv"), [], 0.0, audio_track_override=2)
        qtbot.addWidget(viewer)
        assert viewer.player_widget._audio_track_override == 2

    def test_viewer_delegates_all_args_to_set_source(self, qtbot):
        video_path = Path("/tmp/test.mkv")
        entries = [(1.0, 2.0, "hello")]
        with patch.object(SubtitlePlayerWidget, "set_source") as mock_set_source:
            viewer = SubtitleViewer(video_path, entries, 1.5, audio_track_override=3)
        qtbot.addWidget(viewer)
        mock_set_source.assert_called_once_with(
            video_path,
            entries,
            1.5,
            audio_track_override=3,
        )


class TestSubtitleViewerLineList:
    """Picking a line is how you get to the moment you want to hear."""

    def test_every_entry_is_listed_with_a_timestamp(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        assert viewer.line_list.count() == len(ENTRIES)
        assert viewer.line_list.item(0).text().startswith("00:10")
        assert "こんにちは" in viewer.line_list.item(0).text()

    def test_multi_line_cue_is_flattened_into_one_row(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        assert "\n" not in viewer.line_list.item(1).text()
        assert "テスト 二行目" in viewer.line_list.item(1).text()

    def test_line_start_is_carried_on_the_item(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        assert viewer.line_list.item(2).data(Qt.ItemDataRole.UserRole) == 50.0

    def test_opening_parks_on_the_first_line_without_playing(self, qtbot, fake_mpv):
        with patch.object(SubtitlePlayerWidget, "play") as play:
            viewer = _viewer(qtbot, offset=1.0)
        assert viewer.line_list.currentRow() == 0
        play.assert_not_called()
        assert viewer.player_widget._pending_seek_ms == 11000

    def test_selecting_a_line_seeks_to_start_plus_offset_and_plays(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=1.5)
        seek, play = _spy_transport(viewer)
        viewer.line_list.setCurrentRow(2)
        seek.assert_called_once_with(51.5)
        play.assert_called_once_with()

    def test_clearing_the_selection_seeks_nowhere(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        seek, play = _spy_transport(viewer)
        viewer.line_list.setCurrentRow(-1)
        seek.assert_not_called()
        play.assert_not_called()


class TestSubtitleViewerNudge:
    """Left/Right move the offset by one step and immediately replay it."""

    def test_right_nudges_forward_seeks_and_plays(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=1.0)
        seek, play = _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.get_offset() == pytest.approx(1.10)
        assert viewer.player_widget._offset == pytest.approx(1.10)
        seek.assert_called_once_with(pytest.approx(11.10))
        play.assert_called_once_with()

    def test_left_nudges_backward(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=1.0)
        _spy_transport(viewer)
        viewer.nudge_offset(-NUDGE_SECONDS)
        assert viewer.get_offset() == pytest.approx(0.90)

    def test_nudge_step_is_a_tenth_of_a_second(self, qtbot, fake_mpv):
        assert NUDGE_SECONDS == 0.10

    def test_nudge_keeps_two_decimals_across_repeats(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        _spy_transport(viewer)
        for _ in range(3):
            viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.get_offset() == 0.30
        assert viewer.offset_spinbox.value() == 0.30

    def test_nudge_clamps_to_the_allowed_range(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=SUBTITLE_OFFSET_MAX)
        _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.get_offset() == SUBTITLE_OFFSET_MAX
        viewer._working_offset = SUBTITLE_OFFSET_MIN
        viewer.nudge_offset(-NUDGE_SECONDS)
        assert viewer.get_offset() == SUBTITLE_OFFSET_MIN

    def test_nudge_updates_the_spinbox_without_a_feedback_loop(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.offset_spinbox.value() == pytest.approx(0.10)
        assert viewer.get_offset() == pytest.approx(0.10)


class TestSubtitleViewerOverlay:
    """The overlay over the picture is the offset readout."""

    def test_overlay_reads_a_signed_two_decimal_offset(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=1.2)
        assert viewer.offset_overlay.text() == "Offset +1.20 s"

    def test_overlay_shows_a_negative_offset_signed(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=-0.3)
        assert viewer.offset_overlay.text() == "Offset -0.30 s"

    def test_overlay_follows_a_nudge(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.offset_overlay.text() == "Offset +0.10 s"

    def test_overlay_shares_the_players_cell(self, qtbot, fake_mpv):
        """Over the picture, and never a child of the mpv surface itself."""
        viewer = _viewer(qtbot)
        assert viewer.offset_overlay.parentWidget() is viewer.player_widget.parentWidget()
        assert not viewer.player_widget.video_widget.isAncestorOf(viewer.offset_overlay)


class TestSubtitleViewerCompare:
    """One key holds the original timing so the difference is audible."""

    def test_compare_previews_the_original_offset(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=0.0)
        _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        viewer.compare_button.setChecked(True)
        assert viewer.player_widget._offset == 0.0
        assert viewer.offset_overlay.text() == "Original +0.00 s"

    def test_compare_seeks_and_replays_the_same_line(self, qtbot, fake_mpv):
        """ "Original" is the offset the dialog opened with, not zero."""
        viewer = _viewer(qtbot, offset=2.0)
        seek, play = _spy_transport(viewer)
        viewer.nudge_offset(NUDGE_SECONDS)
        seek.reset_mock()
        play.reset_mock()
        viewer.compare_button.setChecked(True)
        seek.assert_called_once_with(pytest.approx(12.0))
        play.assert_called_once_with()

    def test_compare_restores_the_working_offset(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=2.0)
        _spy_transport(viewer)
        viewer.compare_button.setChecked(True)
        viewer.compare_button.setChecked(False)
        assert viewer.player_widget._offset == 2.0
        assert viewer.offset_overlay.text() == "Offset +2.00 s"

    def test_apply_commits_the_working_offset_while_comparing(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=2.0)
        _spy_transport(viewer)
        viewer.compare_button.setChecked(True)
        viewer.accept()
        assert viewer.get_offset() == 2.0

    def test_nudging_drops_out_of_compare(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=2.0)
        _spy_transport(viewer)
        viewer.compare_button.setChecked(True)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.compare_button.isChecked() is False
        assert viewer.player_widget._offset == pytest.approx(2.10)

    def test_editing_the_offset_drops_out_of_compare(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, offset=2.0)
        _spy_transport(viewer)
        viewer.compare_button.setChecked(True)
        viewer.offset_spinbox.setValue(3.0)
        assert viewer.compare_button.isChecked() is False
        assert viewer.get_offset() == 3.0
        assert viewer.player_widget._offset == 3.0


class TestSubtitleViewerOffset:
    """Tests for SubtitleViewer offset management."""

    def test_get_offset_returns_initial_value(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[], offset=1.5)
        assert viewer.get_offset() == 1.5

    def test_offset_change_updates_get_offset(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.offset_spinbox.setValue(2.5)
        assert viewer.get_offset() == 2.5

    def test_offset_change_forwards_to_player_widget(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.offset_spinbox.setValue(1.0)
        assert viewer.player_widget._offset == 1.0

    def test_typing_an_offset_does_not_jump_playback(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        seek, play = _spy_transport(viewer)
        viewer.offset_spinbox.setValue(1.0)
        seek.assert_not_called()
        play.assert_not_called()


class TestSubtitleViewerKeyboardScope:
    """D49: a bare key is only safe where a text field cannot take focus."""

    def _surface_shortcuts(self, viewer: SubtitleViewer) -> dict[str, QShortcut]:
        return {s.key().toString(): s for s in viewer.workbench.findChildren(QShortcut)}

    def test_transport_keys_are_bound_to_the_workbench(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        bound = self._surface_shortcuts(viewer)
        assert {"Space", "Left", "Right", "A", "Return", "Enter"} <= set(bound)

    def test_workbench_shortcuts_are_scoped_to_widget_with_children(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        for shortcut in self._surface_shortcuts(viewer).values():
            assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

    def test_the_offset_field_is_outside_the_workbench(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        assert not viewer.workbench.isAncestorOf(viewer.offset_spinbox)
        assert viewer.workbench.isAncestorOf(viewer.line_list)
        assert viewer.workbench.isAncestorOf(viewer.player_widget)

    def test_ctrl_return_applies_from_anywhere_in_the_dialog(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        dialog_keys = {
            s.key().toString()
            for s in viewer.findChildren(QShortcut)
            if s.parent() is viewer  # the dialog-wide bindings only
        }
        assert {"Ctrl+Return", "Ctrl+Enter"} <= dialog_keys

    def test_no_button_is_the_dialogs_default(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        for button in viewer.findChildren(QPushButton):
            assert button.isDefault() is False
            assert button.autoDefault() is False

    def test_space_plays_then_pauses(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        space = self._surface_shortcuts(viewer)["Space"]
        space.activated.emit()
        assert fake_mpv["player"].pause is False
        space.activated.emit()
        assert fake_mpv["player"].pause is True

    def test_arrows_nudge_by_one_step_each_way(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        _spy_transport(viewer)
        bound = self._surface_shortcuts(viewer)
        bound["Right"].activated.emit()
        assert viewer.get_offset() == pytest.approx(NUDGE_SECONDS)
        bound["Left"].activated.emit()
        bound["Left"].activated.emit()
        assert viewer.get_offset() == pytest.approx(-NUDGE_SECONDS)

    def test_a_toggles_the_comparison(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        _spy_transport(viewer)
        self._surface_shortcuts(viewer)["A"].activated.emit()
        assert viewer.compare_button.isChecked() is True
        self._surface_shortcuts(viewer)["A"].activated.emit()
        assert viewer.compare_button.isChecked() is False

    def test_bare_return_on_the_workbench_applies(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        self._surface_shortcuts(viewer)["Return"].activated.emit()
        assert viewer.result() == QDialog.DialogCode.Accepted.value


class TestSubtitleViewerStates:
    """A visible loading state and an honest failure, per D35."""

    def test_loading_state_is_shown_until_the_source_opens(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        assert "Loading" in viewer.status_label.text()

    def test_loaded_source_replaces_loading_with_the_key_hints(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        viewer.player_widget.source_loaded.emit()
        assert "Loading" not in viewer.status_label.text()
        assert "Space" in viewer.status_label.text()

    def test_no_loading_state_when_there_is_no_backend(self, qtbot):
        with patch(f"{PLAYER_MODULE}.mpv_available", return_value=False):
            viewer = _viewer(qtbot)
        assert viewer.status_label.text() == ""

    def test_playback_failure_reports_a_screen_issue(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        viewer.player_widget.playback_failed.emit("demux failure")
        issue = viewer.issue_banner().current_issue()
        assert issue is not None
        assert "could not be played" in issue.summary
        assert issue.details == "demux failure"

    def test_a_later_successful_load_clears_the_failure(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        viewer.player_widget.playback_failed.emit("demux failure")
        viewer.player_widget.source_loaded.emit()
        assert viewer.issue_banner().current_issue() is None


class TestSubtitleViewerAlign:
    """Align automatically hands off to the existing aligner."""

    def test_align_closes_with_its_own_result_code(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot)
        viewer.align_button.click()
        assert viewer.result() == SubtitleViewer.ALIGN_REQUESTED
        assert SubtitleViewer.ALIGN_REQUESTED not in (
            QDialog.DialogCode.Accepted.value,
            QDialog.DialogCode.Rejected.value,
        )

    def test_align_releases_the_player_before_the_dialog_closes(self, qtbot, fake_mpv):
        """The core must already be down when the caller is told to navigate."""
        viewer = _viewer(qtbot)
        seen: list[object] = []
        viewer.finished.connect(lambda _code: seen.append(viewer.player_widget.player))
        viewer.align_button.click()
        assert seen == [None]


class TestSubtitleViewerReleases:
    """SubtitleViewer must terminate the mpv core on every exit path."""

    def test_accept_releases_player(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.accept()
        fake_mpv["player"].terminate.assert_called_once()
        assert viewer.player_widget.player is None

    def test_reject_releases_player(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.reject()
        fake_mpv["player"].terminate.assert_called_once()

    def test_close_event_releases_player(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.close()
        fake_mpv["player"].terminate.assert_called_once()

    def test_align_releases_player(self, qtbot, fake_mpv):
        viewer = _viewer(qtbot, entries=[])
        viewer.align_button.click()
        fake_mpv["player"].terminate.assert_called_once()


class TestPreviewSuppressedInViewer:
    """The viewer is an audio-alignment tool, so losing the picture must not
    lose the tool. Same gate as the curator, second consumer."""

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        from anki_miner.gui.utils import video_preview

        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        yield
        video_preview._reset_for_tests()

    def test_no_gl_widget(self, qtbot, fake_mpv):
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget

        viewer = _viewer(qtbot)
        assert viewer.player_widget.video_widget is None
        assert viewer.findChildren(QOpenGLWidget) == []

    def test_the_alignment_controls_still_work(self, qtbot, fake_mpv):
        """Offset nudging is the whole point of this screen and is driven by
        audio; it must survive with no picture."""
        viewer = _viewer(qtbot, offset=0.0)
        viewer.nudge_offset(NUDGE_SECONDS)
        assert viewer.offset_overlay.text() == "Offset +0.10 s"

    def test_does_not_promise_a_picture_that_is_not_coming(self, qtbot, fake_mpv):
        """The status line must gate on video_surface_available, not on
        backend_available — the latter stays True here, so it said "Loading
        video…" directly above a pane saying the preview was turned off."""
        viewer = _viewer(qtbot)
        viewer._show_loading_state()
        assert viewer.status_label.text() == ""


class TestLoadingStateWithAPicture:
    def test_says_loading_when_a_surface_exists(self, qtbot, fake_mpv):
        """The other half of the gate: don't silence the status line for
        everyone while fixing the suppressed case."""
        viewer = _viewer(qtbot)
        viewer._show_loading_state()
        assert viewer.status_label.text() == "Loading video…"
