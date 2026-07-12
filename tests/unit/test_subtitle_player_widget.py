"""Tests for SubtitlePlayerWidget (embedded libmpv backend).

CI has no libmpv, so the mpv boundary is mocked at the widget module's import
seam: ``{MODULE}.create_mpv_player`` returns a MagicMock player and
``{MODULE}.mpv_available`` is forced True. MpvVideoWidget needs no patching —
under QT_QPA_PLATFORM=offscreen an unshown QOpenGLWidget never initializes GL,
so ``attach(mock)`` only stores the handle. GUI-side behavior is driven by
calling the marshalling slots directly (``_on_time_pos`` etc.), exactly what
the queued signals would deliver.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

MODULE = "anki_miner.gui.widgets.subtitle_player_widget"


def _make_fake_player() -> MagicMock:
    player = MagicMock(name="mpv.MPV")
    player.pause = True
    player.track_list = []
    # event_callback is used as a decorator: player.event_callback("x")(fn)
    player.event_callback.return_value = lambda fn: fn
    return player


@pytest.fixture
def fake_mpv(monkeypatch):
    """Patch the widget module's mpv seam; yields the fake player instance."""
    player = _make_fake_player()
    with (
        patch(f"{MODULE}.mpv_available", return_value=True),
        patch(f"{MODULE}.create_mpv_player", return_value=player) as factory,
    ):
        yield {"player": player, "factory": factory}


def _widget(qtbot) -> SubtitlePlayerWidget:
    widget = SubtitlePlayerWidget()
    qtbot.addWidget(widget)
    return widget


VIDEO = Path("/tmp/fake_video.mkv")
ENTRIES = [(1.0, 2.5, "こんにちは"), (3.0, 4.0, "テスト")]


class TestInit:
    def test_no_player_until_set_source(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        assert widget.player is None
        assert widget.subtitle_entries == []
        fake_mpv["factory"].assert_not_called()

    def test_controls_exist(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        assert widget.play_button.text() == "Play"
        assert widget.position_slider.minimum() == 0
        assert not widget.subtitle_label.isVisibleTo(widget)
        assert not widget._backend_notice_label.isVisibleTo(widget)


class TestSetSource:
    def test_creates_player_and_loads_file(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, 0.5, audio_track_override=1)
        fake_mpv["factory"].assert_called_once()
        fake_mpv["player"].loadfile.assert_called_once_with(str(VIDEO))
        assert widget.player is fake_mpv["player"]
        assert widget.subtitle_entries == ENTRIES
        assert widget._offset == 0.5
        assert widget._audio_track_override == 1

    def test_starts_paused(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        fake_mpv["player"].pause = False  # simulate leftover state
        widget.set_source(VIDEO, ENTRIES)
        assert fake_mpv["player"].pause is True

    def test_resource_reuses_single_instance(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        other = Path("/tmp/other.mkv")
        widget.set_source(other, [])
        fake_mpv["factory"].assert_called_once()  # ONE instance per widget lifetime
        assert fake_mpv["player"].loadfile.call_count == 2
        fake_mpv["player"].loadfile.assert_called_with(str(other))

    def test_resource_resets_transient_state(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        widget._at_eof = True
        widget.seek_seconds(5.0)
        widget.set_source(VIDEO, [])
        assert widget._file_loaded is False
        assert widget._pending_seek_ms is None
        assert widget._at_eof is False

    def test_registers_observers(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        observed = {call.args[0] for call in fake_mpv["player"].observe_property.call_args_list}
        assert {"time-pos", "duration", "pause", "eof-reached"} <= observed


class TestMpvUnavailable:
    def test_notice_shown_and_no_player(self, qtbot):
        with patch(f"{MODULE}.mpv_available", return_value=False):
            widget = _widget(qtbot)
            widget.set_source(VIDEO, ENTRIES, 0.25)
        assert widget.player is None
        assert widget._backend_notice_label.isVisibleTo(widget)
        assert "mpv" in widget._backend_notice_label.text()
        assert not widget.video_widget.isVisibleTo(widget)
        # State for the overlay-driven consumers is still stored.
        assert widget.subtitle_entries == ENTRIES
        assert widget._offset == 0.25

    def test_controls_noop_without_player(self, qtbot):
        with patch(f"{MODULE}.mpv_available", return_value=False):
            widget = _widget(qtbot)
            widget.set_source(VIDEO, ENTRIES)
            widget.play()
            widget.pause()
            widget.stop()
            widget.toggle_play_pause()
            widget.seek_seconds(3.0)
            widget.set_offset(1.0)
        assert widget._offset == 1.0
        widget.release()  # must not raise


class TestAudioTrackSelection:
    def _tracks(self):
        return [
            {"type": "video", "id": 1},
            {"type": "audio", "id": 1, "lang": "eng"},
            {"type": "audio", "id": 2, "lang": "jpn"},
        ]

    def test_override_maps_to_aid_plus_one(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, audio_track_override=1)
        fake_mpv["player"].track_list = self._tracks()
        widget._on_file_loaded()
        assert fake_mpv["player"].aid == 2

    def test_override_out_of_range_logs_and_leaves_default(self, qtbot, fake_mpv, caplog):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, audio_track_override=5)
        fake_mpv["player"].track_list = self._tracks()
        sentinel = object()
        fake_mpv["player"].aid = sentinel
        with caplog.at_level("WARNING"):
            widget._on_file_loaded()
        assert "out of range" in caplog.text
        assert fake_mpv["player"].aid is sentinel

    def test_jp_lang_fallback_sets_track_id(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)  # no override
        fake_mpv["player"].track_list = self._tracks()
        widget._on_file_loaded()
        assert fake_mpv["player"].aid == 2  # the jpn track's mpv id

    def test_no_jp_anywhere_leaves_mpv_default(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        fake_mpv["player"].track_list = [
            {"type": "audio", "id": 1, "lang": "eng"},
            {"type": "audio", "id": 2, "lang": "ger"},
        ]
        sentinel = object()
        fake_mpv["player"].aid = sentinel
        widget._on_file_loaded()
        assert fake_mpv["player"].aid is sentinel


class TestSeek:
    def test_seek_before_file_loaded_is_queued(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.seek_seconds(12.5)
        fake_mpv["player"].command.assert_not_called()
        assert widget._pending_seek_ms == 12500

    def test_pending_seek_applied_once_on_file_loaded(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.seek_seconds(12.5)
        widget._on_file_loaded()
        fake_mpv["player"].command.assert_called_once_with("seek", 12.5, "absolute+exact")
        fake_mpv["player"].command.reset_mock()
        widget._on_file_loaded()  # a second file-loaded must not re-seek
        fake_mpv["player"].command.assert_not_called()

    def test_seek_after_loaded_is_immediate_and_exact(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        widget.seek_seconds(3.25)
        fake_mpv["player"].command.assert_called_once_with("seek", 3.25, "absolute+exact")

    def test_seek_clamps_negative(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        widget.seek_seconds(-4.0)
        fake_mpv["player"].command.assert_called_once_with("seek", 0.0, "absolute+exact")

    def test_slider_move_routes_through_seek(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        widget._on_slider_moved(1500)
        fake_mpv["player"].command.assert_called_once_with("seek", 1.5, "absolute+exact")


class TestPlayPauseStop:
    def test_play_unpauses(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.play()
        assert fake_mpv["player"].pause is False

    def test_play_at_eof_seeks_zero_first(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        widget._on_eof(True)
        widget.play()
        fake_mpv["player"].command.assert_called_once_with("seek", 0.0, "absolute+exact")
        assert fake_mpv["player"].pause is False

    def test_pause_sets_pause(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        fake_mpv["player"].pause = False
        widget.pause()
        assert fake_mpv["player"].pause is True

    def test_stop_pauses_at_zero_keeps_media(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        fake_mpv["player"].pause = False
        widget.stop()
        assert fake_mpv["player"].pause is True
        fake_mpv["player"].command.assert_called_once_with("seek", 0.0, "absolute+exact")
        fake_mpv["player"].stop.assert_not_called()  # mpv stop unloads; must not be used

    def test_toggle_both_directions(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        fake_mpv["player"].pause = True
        widget.toggle_play_pause()
        assert fake_mpv["player"].pause is False
        widget.toggle_play_pause()
        assert fake_mpv["player"].pause is True


class TestObserverSlots:
    def test_time_pos_none_is_ignored(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_time_pos(None)  # the NORMAL initial observer callback
        assert widget.time_label.text() == "00:00 / 00:00"

    def test_time_pos_updates_slider_label_subtitle(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_duration(60.0)
        widget._on_time_pos(1.5)
        assert widget.position_slider.value() == 1500
        assert widget.time_label.text() == "00:01 / 01:00"
        assert widget.subtitle_label.isVisibleTo(widget)
        assert widget.subtitle_label.text() == "こんにちは"

    def test_time_pos_respects_scrub_guard(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_duration(60.0)
        with patch.object(type(widget.position_slider), "isSliderDown", return_value=True):
            widget._on_time_pos(2.0)
        assert widget.position_slider.value() == 0  # not yanked mid-drag

    def test_duration_none_is_ignored(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_duration(None)  # initial observer callback before load
        assert widget.position_slider.maximum() == 0

    def test_duration_sets_range(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_duration(90.5)
        assert widget.position_slider.maximum() == 90500

    def test_pause_observer_drives_button_label(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_pause_changed(False)
        assert widget.play_button.text() == "Pause"
        widget._on_pause_changed(True)  # includes keep-open auto-pause at EOF
        assert widget.play_button.text() == "Play"
        widget._on_pause_changed(None)
        assert widget.play_button.text() == "Play"

    def test_eof_observer_none_and_bool(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_eof(None)
        assert widget._at_eof is False
        widget._on_eof(True)
        assert widget._at_eof is True

    def test_playback_error_surfaces_in_subtitle_label(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_playback_error("demux failure")
        assert widget.subtitle_label.isVisibleTo(widget)
        assert "demux failure" in widget.subtitle_label.text()

    def test_render_failed_shows_audio_still_plays_notice(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.video_widget.render_failed.emit("no GL")
        assert widget._backend_notice_label.isVisibleTo(widget)
        assert "Audio and subtitles still play" in widget._backend_notice_label.text()
        assert not widget.video_widget.isVisibleTo(widget)


class TestTeardown:
    def test_release_detaches_before_terminate(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        order = []
        with patch.object(widget.video_widget, "detach", side_effect=lambda: order.append("detach")):
            fake_mpv["player"].terminate.side_effect = lambda: order.append("terminate")
            widget.release()
        assert order == ["detach", "terminate"]
        assert widget.player is None

    def test_release_idempotent(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.release()
        widget.release()
        fake_mpv["player"].terminate.assert_called_once()

    def test_close_event_tears_down(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.close()
        fake_mpv["player"].terminate.assert_called_once()
        assert widget.player is None

    def test_release_without_source_is_noop(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.release()  # must not raise
        fake_mpv["factory"].assert_not_called()

    def test_set_source_after_release_builds_new_player(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.release()
        widget.set_source(VIDEO, ENTRIES)
        assert fake_mpv["factory"].call_count == 2
        assert widget.player is not None

    def test_slots_guard_after_teardown(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.release()
        widget._on_time_pos(5.0)  # must not raise (player is None)
        widget._on_file_loaded()  # must not raise
        widget.seek_seconds(2.0)  # queues silently
        widget.play()
        widget.pause()


class TestSubtitleOverlay:
    def test_overlay_respects_offset(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, offset=1.0)
        widget._update_subtitle(1.5)  # entry starts at 1.0 + offset 1.0 = 2.0
        assert not widget.subtitle_label.isVisibleTo(widget)
        widget._update_subtitle(2.5)
        assert widget.subtitle_label.isVisibleTo(widget)
        assert widget.subtitle_label.text() == "こんにちは"

    def test_set_offset_updates_live(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.set_offset(-0.5)
        widget._update_subtitle(0.6)  # 1.0 - 0.5 = 0.5 <= 0.6 <= 2.0
        assert widget.subtitle_label.isVisibleTo(widget)

    def test_overlay_hidden_outside_entries(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._update_subtitle(2.7)
        assert not widget.subtitle_label.isVisibleTo(widget)


class TestFormatTime:
    @pytest.mark.parametrize(
        "ms,expected",
        [(0, "00:00"), (-500, "00:00"), (1000, "00:01"), (61000, "01:01"), (3599000, "59:59")],
    )
    def test_format(self, ms, expected):
        assert SubtitlePlayerWidget._format_time(ms) == expected
