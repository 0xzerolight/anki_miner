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

import sys
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
    """Patch the widget module's mpv seam; yields the fake player instance.

    ``has_render_context`` is forced True so set_source's loadfile runs
    immediately (offscreen unshown widgets never create a real render
    context; the deferred-load path has dedicated tests in
    ``TestDeferredLoad``).
    """
    from anki_miner.gui.widgets.mpv_video_widget import MpvVideoWidget

    player = _make_fake_player()
    monkeypatch.setattr(MpvVideoWidget, "has_render_context", property(lambda self: True))
    with (
        patch(f"{MODULE}.mpv_available", return_value=True),
        patch(f"{MODULE}.create_mpv_player", return_value=player) as factory,
    ):
        yield {"player": player, "factory": factory}


@pytest.fixture
def fake_mpv_no_ctx():
    """Same seam WITHOUT a render context — the real first-load state:
    consumers call set_source in dialog __init__, before the widget is shown
    and any GL context exists."""
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
        # The strip is allocated from construction and empty, not hidden.
        assert widget.subtitle_strip.toPlainText() == ""
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
        # The GL widget is not merely hidden — it is never constructed. Building
        # a QOpenGLWidget just to render a text notice over it is what made the
        # libmpv-absent path share the GL-abort blast radius.
        assert widget.video_widget is None
        assert not widget.video_surface_available
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


class TestBackendNoticeText:
    """The libmpv-unavailable notice is platform- and frozen-aware. A frozen
    Windows user (bundled libmpv-2.dll present but unloadable — the confirmed
    field bug) must NOT be told to `install libmpv2 / brew install mpv`: no such
    rescue exists on Windows. pip/dev on any OS, and frozen macOS/Linux (where
    the loader's system fall-through makes installing a system libmpv work),
    keep the original install advice."""

    def _notice(self, qtbot) -> str:
        with patch(f"{MODULE}.mpv_available", return_value=False):
            widget = _widget(qtbot)
            widget.set_source(VIDEO, ENTRIES)
        return widget._backend_notice_label.text()

    def test_frozen_windows_points_to_log_not_package_manager(self, qtbot, monkeypatch):
        monkeypatch.setattr(f"{MODULE}.frozen_state", lambda: (True, "C:\\meipass"))
        monkeypatch.setattr(sys, "platform", "win32")
        text = self._notice(qtbot)
        assert "anki_miner.log" in text
        assert "libmpv2" not in text  # the useless-on-Windows advice is gone
        assert "brew" not in text

    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_frozen_non_windows_keeps_install_advice(self, qtbot, monkeypatch, platform):
        monkeypatch.setattr(f"{MODULE}.frozen_state", lambda: (True, "/meipass"))
        monkeypatch.setattr(sys, "platform", platform)
        text = self._notice(qtbot)
        assert "libmpv2" in text
        assert "brew" in text

    def test_non_frozen_keeps_install_advice_even_on_windows(self, qtbot, monkeypatch):
        # A pip install on Windows genuinely can add a system libmpv.
        monkeypatch.setattr(f"{MODULE}.frozen_state", lambda: (False, None))
        monkeypatch.setattr(sys, "platform", "win32")
        text = self._notice(qtbot)
        assert "libmpv2" in text
        assert "brew" in text


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


class TestPlayRange:
    """Bounded clip preview — the word curator's audio clip strip drives this."""

    def _loaded(self, qtbot, fake_mpv) -> SubtitlePlayerWidget:
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_file_loaded()
        fake_mpv["player"].command.reset_mock()
        return widget

    def test_seeks_to_start_and_plays(self, qtbot, fake_mpv):
        widget = self._loaded(qtbot, fake_mpv)
        widget.play_range(4.0, 9.5)
        fake_mpv["player"].command.assert_called_once_with("seek", 4.0, "absolute+exact")
        assert fake_mpv["player"].pause is False
        assert widget._range_end == 9.5

    def test_pauses_at_the_end_once(self, qtbot, fake_mpv):
        widget = self._loaded(qtbot, fake_mpv)
        widget.play_range(4.0, 9.5)
        with qtbot.waitSignal(widget.range_finished, timeout=100):
            widget._on_time_pos(9.5)
        assert fake_mpv["player"].pause is True
        assert widget._range_end is None
        # A later tick must not re-pause: the range is spent, and the user may
        # have started playing again.
        fake_mpv["player"].pause = False
        widget._on_time_pos(12.0)
        assert fake_mpv["player"].pause is False

    def test_does_not_stop_before_the_end(self, qtbot, fake_mpv):
        widget = self._loaded(qtbot, fake_mpv)
        widget.play_range(4.0, 9.5)
        widget._on_time_pos(8.0)
        assert fake_mpv["player"].pause is False
        assert widget._range_end == 9.5

    @pytest.mark.parametrize("action", ["play", "pause", "stop", "toggle_play_pause"])
    def test_transport_action_cancels_the_range(self, qtbot, fake_mpv, action):
        """A user taking over playback is never yanked to a stale boundary."""
        widget = self._loaded(qtbot, fake_mpv)
        widget.play_range(4.0, 9.5)

        with qtbot.waitSignal(widget.range_finished, timeout=100):
            getattr(widget, action)()

        assert widget._range_end is None
        fake_mpv["player"].pause = False
        widget._on_time_pos(9.5)
        assert fake_mpv["player"].pause is False

    def test_seek_elsewhere_cancels_the_range(self, qtbot, fake_mpv):
        widget = self._loaded(qtbot, fake_mpv)
        widget.play_range(4.0, 9.5)
        widget.seek_seconds(30.0)
        assert widget._range_end is None

    def test_cancel_is_idempotent_and_quiet(self, qtbot, fake_mpv):
        """No range in effect means no signal — consumers must not see a phantom stop."""
        widget = self._loaded(qtbot, fake_mpv)
        received = []
        widget.range_finished.connect(lambda: received.append(True))
        widget.cancel_range()
        widget.cancel_range()
        assert received == []

    def test_no_player_is_a_noop(self, qtbot, fake_mpv):
        widget = _widget(qtbot)  # set_source never called
        widget.play_range(1.0, 2.0)
        assert widget._range_end is None


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
        assert widget.subtitle_strip.toPlainText() == "こんにちは"

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

    def test_playback_error_surfaces_in_subtitle_strip(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._on_playback_error("demux failure")
        assert "demux failure" in widget.subtitle_strip.toPlainText()

    def test_render_failed_shows_audio_still_plays_notice(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.video_widget.render_failed.emit("no GL")
        assert widget._backend_notice_label.isVisibleTo(widget)
        assert "Audio and subtitles still play" in widget._backend_notice_label.text()
        assert not widget.video_widget.isVisibleTo(widget)


class TestLifecycleSignals:
    """The public seam consumers build a loading/failed state on (D35)."""

    def test_backend_available_reports_the_mpv_seam(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        assert widget.backend_available is True

    def test_backend_unavailable_is_reported(self, qtbot):
        with patch(f"{MODULE}.mpv_available", return_value=False):
            widget = _widget(qtbot)
        assert widget.backend_available is False

    def test_source_loaded_emits_when_mpv_opens_the_file(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        with qtbot.waitSignal(widget.source_loaded, timeout=1000):
            widget._on_file_loaded()

    def test_source_loaded_not_emitted_without_a_player(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        seen = []
        widget.source_loaded.connect(lambda: seen.append(True))
        widget._on_file_loaded()  # no set_source yet: player is None
        assert seen == []

    def test_playback_failed_carries_the_reason(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        with qtbot.waitSignal(widget.playback_failed, timeout=1000) as blocker:
            widget._on_playback_error("demux failure")
        assert blocker.args == ["demux failure"]


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
        assert widget.subtitle_strip.toPlainText() == ""
        widget._update_subtitle(2.5)
        assert widget.subtitle_strip.toPlainText() == "こんにちは"

    def test_set_offset_updates_live(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.set_offset(-0.5)
        widget._update_subtitle(0.6)  # 1.0 - 0.5 = 0.5 <= 0.6 <= 2.0
        assert widget.subtitle_strip.toPlainText() == "こんにちは"

    def test_overlay_hidden_outside_entries(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._update_subtitle(2.7)
        assert widget.subtitle_strip.toPlainText() == ""


class TestFormatTime:
    @pytest.mark.parametrize(
        "ms,expected",
        [(0, "00:00"), (-500, "00:00"), (1000, "00:01"), (61000, "01:01"), (3599000, "59:59")],
    )
    def test_format(self, ms, expected):
        assert SubtitlePlayerWidget._format_time(ms) == expected


class TestDeferredLoad:
    """loadfile must wait for the render context (the black-video regression:
    loading first makes mpv's VO init fail permanently — audio-only pane)."""

    def test_set_source_defers_loadfile_until_render_ready(self, qtbot, fake_mpv_no_ctx):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        fake_mpv_no_ctx["player"].loadfile.assert_not_called()
        assert widget._pending_load == str(VIDEO)
        widget.video_widget.render_ready.emit()
        fake_mpv_no_ctx["player"].loadfile.assert_called_once_with(str(VIDEO))
        assert widget._pending_load is None

    def test_render_ready_flushes_only_once(self, qtbot, fake_mpv_no_ctx):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.video_widget.render_ready.emit()
        widget.video_widget.render_ready.emit()
        fake_mpv_no_ctx["player"].loadfile.assert_called_once()

    def test_resource_before_ready_keeps_latest_source(self, qtbot, fake_mpv_no_ctx):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        other = Path("/tmp/other.mkv")
        widget.set_source(other, [])
        widget.video_widget.render_ready.emit()
        fake_mpv_no_ctx["player"].loadfile.assert_called_once_with(str(other))

    def test_render_failed_still_loads_for_audio(self, qtbot, fake_mpv_no_ctx):
        """The 'audio still plays' notice must be true: a failed render
        context still flushes the queued source."""
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.video_widget.render_failed.emit("no GL")
        fake_mpv_no_ctx["player"].loadfile.assert_called_once_with(str(VIDEO))
        assert widget._backend_notice_label.isVisibleTo(widget)

    def test_teardown_clears_pending_load(self, qtbot, fake_mpv_no_ctx):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget.release()
        widget.video_widget.render_ready.emit()  # must not resurrect the load
        fake_mpv_no_ctx["player"].loadfile.assert_not_called()
        assert widget._pending_load is None

    def test_immediate_load_when_context_already_live(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)  # fixture forces has_render_context
        fake_mpv["player"].loadfile.assert_called_once_with(str(VIDEO))
        assert widget._pending_load is None


class TestSubtitleStrip:
    """The strip is two lines tall for the whole session (decision D45-B).

    It used to be a QLabel shown and hidden per cue, so the video jumped every
    time a line appeared and jumped again when a long line wrapped onto a
    second one.
    """

    def test_two_lines_are_reserved_before_any_cue(self, qtbot, fake_mpv):
        from anki_miner.gui.utils.fonts import JAPANESE_FEATURE, japanese_line_spacing

        widget = _widget(qtbot)
        assert widget.subtitle_strip.height() >= 2 * japanese_line_spacing(JAPANESE_FEATURE)

    def test_height_never_moves_across_the_whole_cue_cycle(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        before = widget.subtitle_strip.height()

        widget._update_subtitle(1.5)  # one short line
        during = widget.subtitle_strip.height()

        widget._update_subtitle(2.7)  # between cues
        between = widget.subtitle_strip.height()

        widget.subtitle_entries = [(1.0, 2.5, "これはとても長い字幕の行で、必ず二行以上に折り返されます。" * 2)]
        widget._update_subtitle(1.5)  # a line that must wrap
        wrapped = widget.subtitle_strip.height()

        widget._update_subtitle(99.0)  # after the last cue
        after = widget.subtitle_strip.height()

        assert {during, between, wrapped, after} == {before}

    def test_text_clears_between_cues_but_the_strip_stays(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES)
        widget._update_subtitle(1.5)
        assert widget.subtitle_strip.toPlainText() == "こんにちは"
        widget._update_subtitle(2.7)
        assert widget.subtitle_strip.toPlainText() == ""
        assert widget.subtitle_strip.height() > 0

    def test_the_cue_uses_the_japanese_face_at_the_scaled_feature_size(self, qtbot, fake_mpv):
        from anki_miner.gui.resources.styles import FONT_SIZES
        from anki_miner.gui.utils.fonts import make_japanese_font, resolved_families

        widget = _widget(qtbot)
        font = widget.subtitle_strip.font()
        assert font.family() == resolved_families().japanese
        assert font.pixelSize() == make_japanese_font(FONT_SIZES.japanese_feature).pixelSize()

    def test_the_cue_is_plain_text_with_no_generated_markup(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.subtitle_entries = [(1.0, 2.0, "<b>強調</b>")]
        widget._update_subtitle(1.5)
        assert widget.subtitle_strip.toPlainText() == "<b>強調</b>"
        assert "<ruby>" not in widget.subtitle_strip.toHtml()

    def test_the_strip_takes_no_focus_so_space_still_reaches_the_player(self, qtbot, fake_mpv):
        from PyQt6.QtCore import Qt

        widget = _widget(qtbot)
        assert widget.subtitle_strip.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert widget.subtitle_strip.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction

    def test_the_leading_is_the_japanese_leading(self, qtbot, fake_mpv):
        from anki_miner.gui.resources.styles._variables import TYPOGRAPHY

        widget = _widget(qtbot)
        widget.subtitle_entries = [(1.0, 2.0, "こんにちは")]
        widget._update_subtitle(1.5)
        block = widget.subtitle_strip.document().firstBlock()
        assert block.blockFormat().lineHeight() == TYPOGRAPHY.japanese_leading_percent


class TestPreviewSuppressed:
    """The preview turned off by ``ANKI_MINER_NO_VIDEO_PREVIEW``.

    Distinct from libmpv-absent (``TestMpvUnavailable``): libmpv loaded fine
    here, and audio is expected to keep working. Only the GL surface is gone.
    """

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        from anki_miner.gui.utils import video_preview

        monkeypatch.setenv(video_preview.ENV_VAR, "1")
        video_preview._reset_for_tests()
        yield
        video_preview._reset_for_tests()

    def test_the_gl_widget_is_never_constructed(self, qtbot, fake_mpv):
        """THE load-bearing assertion of this whole change. Building the
        QOpenGLWidget is what aborts the process on an affected host, so a test
        that only checked visibility would pass while the app still died."""
        with patch(f"{MODULE}.MpvVideoWidget", side_effect=AssertionError("GL widget constructed")):
            widget = _widget(qtbot)
        assert widget.video_widget is None
        assert not widget.video_surface_available

    def test_backend_is_still_available(self, qtbot, fake_mpv):
        """libmpv loaded; only the surface is suppressed. Consumers that gate on
        backend_available must not treat this as "no player at all"."""
        widget = _widget(qtbot)
        assert widget.backend_available

    def test_notice_names_the_variable_not_the_install_advice(self, qtbot, fake_mpv):
        """Telling someone to install libmpv when libmpv is already loaded and
        they simply set the env var sends them in circles. There is deliberately
        no Settings checkbox, so the notice must name the one thing that works."""
        from anki_miner.gui.utils import video_preview

        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, 0.0)
        text = widget._backend_notice_label.text()
        assert widget._backend_notice_label.isVisibleTo(widget)
        assert video_preview.ENV_VAR in text
        assert "libmpv" not in text
        assert "Settings" not in text

    def test_player_is_built_audio_only(self, qtbot, fake_mpv):
        """A vo=libmpv core with no render context to attach logs
        'No render context set.' instead of playing, so video must be declined
        up front rather than left to fail."""
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, 0.0)
        assert fake_mpv["factory"].call_args.kwargs["video"] is False

    def test_loadfile_is_immediate_not_deferred(self, qtbot, fake_mpv_no_ctx):
        """With no surface no render context will ever exist, so the deferred
        path would park the load forever and play nothing."""
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, 0.0)
        assert widget._pending_load is None
        fake_mpv_no_ctx["player"].loadfile.assert_called_once_with(str(VIDEO))

    def test_teardown_still_terminates_the_core(self, qtbot, fake_mpv):
        widget = _widget(qtbot)
        widget.set_source(VIDEO, ENTRIES, 0.0)
        with patch(f"{MODULE}.terminate_mpv_player") as terminate:
            widget._teardown_player()
        terminate.assert_called_once()
        assert widget.player is None


class TestMpvUnavailableSkipsGlWidget:
    def test_no_gl_widget_when_libmpv_is_absent(self, qtbot):
        """Pins the second half of the gate: the libmpv-absent path used to
        build a QOpenGLWidget purely to draw a text notice over it, sharing the
        GL-abort blast radius for no benefit."""
        with (
            patch(f"{MODULE}.mpv_available", return_value=False),
            patch(f"{MODULE}.MpvVideoWidget", side_effect=AssertionError("GL widget constructed")),
        ):
            widget = _widget(qtbot)
        assert widget.video_widget is None
