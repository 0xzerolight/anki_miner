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
