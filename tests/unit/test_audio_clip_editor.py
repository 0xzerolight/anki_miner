"""Tests for AudioClipEditor — the word curator's per-word audio clip strip."""

from __future__ import annotations

import pytest

from anki_miner.gui.widgets.audio_clip_editor import (
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
    AudioClipEditor,
    to_ticks,
)
from anki_miner.services.media_extractor import resolve_audio_window

PADDING = 0.3
# A line running 5.0 -> 7.0 with 0.3 padding: the default window is 4.7 -> 7.3.
WORD = (5.0, 7.0)
DEFAULT = (4.7, 7.3)


@pytest.fixture
def editor(qtbot) -> AudioClipEditor:
    widget = AudioClipEditor()
    qtbot.addWidget(widget)
    return widget


def _seed(editor: AudioClipEditor, override: tuple[float, float] | None = None) -> None:
    editor.set_word(WORD[0], WORD[1], PADDING, override)


def _drag(editor: AudioClipEditor, in_seconds: float, out_seconds: float) -> None:
    """Emit what the slider emits when the user moves a handle there."""
    editor.slider.values_changed.emit(to_ticks(in_seconds), to_ticks(out_seconds))


class TestSeeding:
    def test_the_strip_is_always_visible(self, editor):
        """No disclosure: one slider and a play button are small enough to stay open."""
        assert editor.slider.isVisibleTo(editor)
        assert not hasattr(editor, "toggle_button")
        assert not hasattr(editor, "set_expanded")

    def test_seeds_the_padded_default(self, editor):
        """The slider states what ffmpeg would cut right now, not zeroes."""
        _seed(editor)
        assert editor.current_window() == DEFAULT
        assert editor.has_override() is False

    def test_off_grid_default_is_not_an_override(self, editor):
        """Quantising a 5.03 s line start must not mark the word edited."""
        editor.set_word(5.03, 7.04, PADDING, None)
        assert editor.has_override() is False

    def test_default_start_clamped_to_zero(self, editor):
        editor.set_word(0.1, 2.0, PADDING, None)
        assert editor.current_window()[0] == 0.0

    def test_near_zero_bounds_match_the_extractor(self, editor, make_tokenized_word):
        word = make_tokenized_word(start_time=0.1, end_time=1.1, duration=1.0)

        editor.set_word(word.start_time, word.end_time, PADDING, None)
        start, duration = resolve_audio_window(word, PADDING)

        assert editor.current_window() == pytest.approx((start, start + duration))
        assert editor.current_window() == pytest.approx((0.0, 1.4))

    def test_seeds_an_existing_override(self, editor):
        _seed(editor, override=(4.0, 9.5))
        assert editor.current_window() == (4.0, 9.5)
        assert editor.has_override() is True

    def test_travel_is_the_window_widened_both_ways(self, editor):
        """Three seconds of slack each side, fixed while a handle moves."""
        _seed(editor)
        assert editor.slider._lo == to_ticks(1.7)
        assert editor.slider._hi == to_ticks(10.3)

    def test_seeding_emits_nothing(self, editor):
        """Scrolling the word list must not record an override per row."""
        changes: list[tuple[float, float]] = []
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        _seed(editor)
        _seed(editor, override=(4.0, 9.5))
        assert changes == []

    def test_clear_word_disables_the_strip(self, editor):
        _seed(editor)
        editor.clear_word()
        assert editor.isEnabled() is False
        assert editor.has_override() is False

    def test_set_word_re_enables_after_clear(self, editor):
        _seed(editor)
        editor.clear_word()
        _seed(editor)
        assert editor.isEnabled() is True


class TestEditing:
    def test_a_drag_emits_the_window(self, editor):
        _seed(editor)
        changes: list[tuple[float, float]] = []
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        _drag(editor, 4.0, 7.3)
        assert changes == [(4.0, 7.3)]
        assert editor.current_window() == (4.0, 7.3)
        assert editor.has_override() is True

    def test_readout_tracks_the_window(self, editor):
        _seed(editor)
        assert editor.slider._text.startswith("2.6")
        _drag(editor, 4.7, 9.7)
        assert editor.slider._text.startswith("5.0")


class TestBounds:
    def test_dragging_in_past_out_pushes_out(self, editor):
        _seed(editor)
        _drag(editor, 7.3, 7.3)
        in_value, out_value = editor.current_window()
        assert out_value - in_value == pytest.approx(MIN_CLIP_SECONDS)

    def test_dragging_out_past_in_pushes_in(self, editor):
        _seed(editor)
        _drag(editor, 4.7, 4.7)
        in_value, out_value = editor.current_window()
        assert out_value - in_value == pytest.approx(MIN_CLIP_SECONDS)

    def test_in_stops_at_the_end_of_its_travel(self, editor):
        _seed(editor)
        _drag(editor, 999.0, 7.3)
        assert editor.current_window() == (10.1, 10.3)

    def test_in_never_goes_negative(self, editor):
        editor.set_word(0.1, 2.0, PADDING, None)
        _drag(editor, -50.0, 2.3)
        assert editor.current_window()[0] == 0.0

    def test_length_capped(self, editor):
        """A very long line must not be draggable into a multi-minute clip."""
        editor.set_word(0.0, 40.0, 0.0, None)
        _drag(editor, 0.0, 43.0)
        in_value, out_value = editor.current_window()
        assert out_value - in_value == pytest.approx(MAX_CLIP_SECONDS)

    def test_seeding_does_not_cap(self, editor):
        """An over-long default is shown as-is, not silently marked edited."""
        editor.set_word(0.0, 40.0, 0.0, None)
        assert editor.current_window() == (0.0, 40.0)
        assert editor.has_override() is False


class TestReset:
    def test_double_click_restores_the_default(self, editor):
        _seed(editor, override=(4.0, 9.5))
        editor.slider.reset_requested.emit()
        assert editor.current_window() == DEFAULT
        assert editor.has_override() is False

    def test_emits_reset_not_change(self, editor):
        _seed(editor, override=(4.0, 9.5))
        resets: list[bool] = []
        changes: list[tuple[float, float]] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        editor.slider.reset_requested.emit()
        assert resets == [True]
        assert changes == []

    def test_noop_without_a_word(self, editor):
        resets: list[bool] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.slider.reset_requested.emit()
        assert resets == []

    def test_reset_is_the_double_click(self, editor):
        """The curator's Reset clip key calls reset(); it must do what the double-click does."""
        _seed(editor, override=(4.0, 9.5))
        resets: list[bool] = []
        changes: list[tuple[float, float]] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))

        editor.reset()

        assert editor.current_window() == DEFAULT
        assert editor.has_override() is False
        assert resets == [True]
        assert changes == []

    def test_reset_without_a_word_does_nothing(self, editor):
        resets: list[bool] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.reset()
        assert resets == []


class TestPlayback:
    def test_play_requests_the_current_window(self, editor):
        _seed(editor, override=(4.0, 9.5))
        requests: list[tuple[float, float]] = []
        editor.play_requested.connect(lambda a, b: requests.append((a, b)))
        editor.play_button.click()
        assert requests == [(4.0, 9.5)]

    def test_play_button_stops_while_playing(self, editor):
        _seed(editor)
        stops: list[bool] = []
        requests: list[tuple[float, float]] = []
        editor.stop_requested.connect(lambda: stops.append(True))
        editor.play_requested.connect(lambda a, b: requests.append((a, b)))

        editor.set_playing(True)
        editor.play_button.click()

        assert stops == [True]
        assert requests == []

    def test_playing_state_changes_the_glyph(self, editor):
        idle = editor.play_button.text()
        editor.set_playing(True)
        assert editor.play_button.text() != idle
        editor.set_playing(False)
        assert editor.play_button.text() == idle

    def test_toggle_play_plays_the_window_then_stops_it(self, editor):
        """The curator's play-clip key calls toggle_play(): exactly the play button."""
        _seed(editor, override=(4.0, 9.5))
        requests: list[tuple[float, float]] = []
        stops: list[bool] = []
        editor.play_requested.connect(lambda a, b: requests.append((a, b)))
        editor.stop_requested.connect(lambda: stops.append(True))

        editor.toggle_play()
        editor.set_playing(True)  # what the host does on play_requested
        editor.toggle_play()

        assert requests == [(4.0, 9.5)]
        assert stops == [True]

    def test_toggle_play_without_a_word_does_nothing(self, editor):
        """A disabled strip ignores the key, as its disabled button ignores a click."""
        editor.clear_word()
        requests: list[tuple[float, float]] = []
        editor.play_requested.connect(lambda a, b: requests.append((a, b)))

        editor.toggle_play()

        assert requests == []


class TestEdgeButtons:
    def test_they_flank_the_slider(self, editor):
        """Start-here beside the start handle, end-here between the slider and play."""
        layout = editor.layout()
        order = [layout.itemAt(i).widget() for i in range(layout.count())]
        assert order == [editor.start_here_button, editor.slider, editor.end_here_button, editor.play_button]

    def test_glyphs_and_tooltips(self, editor):
        assert editor.start_here_button.text() == "⇤"
        assert editor.end_here_button.text() == "⇥"
        assert editor.start_here_button.toolTip() == "Start the clip at the playhead"
        assert editor.end_here_button.toolTip() == "End the clip at the playhead"

    def test_built_as_the_play_button_is(self, editor):
        for button in (editor.start_here_button, editor.end_here_button):
            assert button.autoRaise()
            assert button.focusPolicy() == editor.play_button.focusPolicy()
            assert button.sizePolicy() == editor.play_button.sizePolicy()

    def test_a_click_asks_the_host(self, editor):
        """No payload: the editor owns no media, so the host reads the playhead."""
        _seed(editor)
        asked: list[str] = []
        editor.start_here_requested.connect(lambda: asked.append("start"))
        editor.end_here_requested.connect(lambda: asked.append("end"))

        editor.start_here_button.click()
        editor.end_here_button.click()

        assert asked == ["start", "end"]

    def test_disabled_without_a_word(self, editor):
        editor.clear_word()
        assert not editor.start_here_button.isEnabled()
        assert not editor.end_here_button.isEnabled()


class TestSetEdge:
    def _changes(self, editor) -> list[tuple[float, float]]:
        changes: list[tuple[float, float]] = []
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        return changes

    def test_start_inside_the_travel(self, editor):
        _seed(editor)
        changes = self._changes(editor)

        editor.set_edge(4.2, out=False)

        assert editor.current_window() == (4.2, 7.3)
        assert changes == [(4.2, 7.3)]
        assert editor.has_override() is True
        assert editor.slider._text.startswith("3.1")

    def test_end_inside_the_travel(self, editor):
        _seed(editor)
        changes = self._changes(editor)

        editor.set_edge(9.0, out=True)

        assert editor.current_window() == (4.7, 9.0)
        assert changes == [(4.7, 9.0)]

    def test_inside_the_travel_the_travel_stays(self, editor):
        """Travel is fixed per word; an edit that fits it never moves it."""
        _seed(editor)
        editor.set_edge(9.0, out=True)
        assert (editor.slider._lo, editor.slider._hi) == (to_ticks(1.7), to_ticks(10.3))

    def test_quantised_to_the_tick_grid(self, editor):
        _seed(editor)
        editor.set_edge(4.23, out=False)
        editor.set_edge(6.97, out=True)
        assert editor.current_window() == (4.2, 7.0)

    def test_an_end_past_the_travel_widens_it(self, editor):
        """A playhead can be anywhere in the video; the handle must be able to sit there."""
        _seed(editor)
        changes = self._changes(editor)

        editor.set_edge(14.0, out=True)

        assert editor.current_window() == (4.7, 14.0)
        assert changes == [(4.7, 14.0)]
        # Re-seated by the MARGIN rule around the new window.
        assert (editor.slider._lo, editor.slider._hi) == (to_ticks(1.7), to_ticks(17.0))

    def test_a_start_before_the_travel_widens_it(self, editor):
        _seed(editor)
        editor.set_edge(0.5, out=False)
        assert editor.current_window() == (0.5, 7.3)
        assert (editor.slider._lo, editor.slider._hi) == (0, to_ticks(10.3))

    def test_the_widened_travel_is_what_returning_to_the_word_shows(self, editor):
        _seed(editor)
        editor.set_edge(14.0, out=True)
        travel = (editor.slider._lo, editor.slider._hi)

        _seed(editor, override=editor.current_window())

        assert (editor.slider._lo, editor.slider._hi) == travel

    def test_a_start_at_the_end_of_the_travel_stays_on_the_playhead(self, editor):
        """The travel never pulls the placed edge back to make room for the minimum length."""
        _seed(editor)
        editor.set_edge(10.3, out=False)
        in_value, out_value = editor.current_window()
        assert in_value == 10.3
        assert out_value == pytest.approx(10.3 + MIN_CLIP_SECONDS)

    def test_a_start_past_the_end_pushes_the_end(self, editor):
        _seed(editor)
        editor.set_edge(8.0, out=False)
        in_value, out_value = editor.current_window()
        assert in_value == 8.0
        assert out_value == pytest.approx(8.0 + MIN_CLIP_SECONDS)

    def test_an_end_before_the_start_pushes_the_start(self, editor):
        _seed(editor)
        editor.set_edge(4.0, out=True)
        in_value, out_value = editor.current_window()
        assert out_value == 4.0
        assert in_value == pytest.approx(4.0 - MIN_CLIP_SECONDS)

    def test_an_end_far_ahead_pulls_the_start_to_the_cap(self, editor):
        _seed(editor)
        editor.set_edge(50.0, out=True)
        assert editor.current_window() == (50.0 - MAX_CLIP_SECONDS, 50.0)

    def test_a_start_far_back_pulls_the_end_to_the_cap(self, editor):
        editor.set_word(5.0, 40.0, 0.0, None)  # an over-long default, shown as-is
        editor.set_edge(2.0, out=False)
        assert editor.current_window() == (2.0, 2.0 + MAX_CLIP_SECONDS)

    def test_never_below_zero(self, editor):
        _seed(editor)
        editor.set_edge(-1.0, out=False)
        assert editor.current_window()[0] == 0.0

    def test_noop_without_a_word(self, editor):
        changes = self._changes(editor)
        editor.set_edge(4.0, out=False)
        editor.clear_word()
        editor.set_edge(4.0, out=True)
        assert changes == []
        assert editor.current_window() == (0.0, 0.0)
