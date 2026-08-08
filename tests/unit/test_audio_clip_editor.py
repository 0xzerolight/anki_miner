"""Tests for AudioClipEditor — the word curator's per-word audio clip strip."""

from __future__ import annotations

import pytest

from anki_miner.gui.widgets.audio_clip_editor import (
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
    AudioClipEditor,
)

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


class TestSeeding:
    def test_starts_collapsed(self, editor):
        """Niche feature: invisible until asked for."""
        assert editor.expanded is False
        assert not editor.body.isVisibleTo(editor)

    def test_seeds_the_padded_default(self, editor):
        """The fields state what ffmpeg would cut right now, not zeroes."""
        _seed(editor)
        assert editor.current_window() == DEFAULT
        assert editor.has_override() is False

    def test_default_start_clamped_to_zero(self, editor):
        editor.set_word(0.1, 2.0, PADDING, None)
        assert editor.current_window()[0] == 0.0

    def test_seeds_an_existing_override(self, editor):
        _seed(editor, override=(4.0, 9.5))
        assert editor.current_window() == (4.0, 9.5)
        assert editor.has_override() is True

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
        assert editor.length_label.text() == ""

    def test_set_word_re_enables_after_clear(self, editor):
        _seed(editor)
        editor.clear_word()
        _seed(editor)
        assert editor.isEnabled() is True


class TestEditing:
    def test_edit_emits_the_window(self, editor):
        _seed(editor)
        changes: list[tuple[float, float]] = []
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        editor.in_spin.setValue(4.0)
        assert changes == [(4.0, 7.3)]

    def test_edit_marks_the_header(self, editor):
        _seed(editor)
        editor.in_spin.setValue(4.0)
        assert "edited" in editor.toggle_button.text()
        assert editor.reset_button.isEnabled() is True

    def test_untouched_header_is_plain(self, editor):
        _seed(editor)
        assert "edited" not in editor.toggle_button.text()
        assert editor.reset_button.isEnabled() is False

    def test_length_readout_tracks_the_window(self, editor):
        _seed(editor)
        assert editor.length_label.text().startswith("2.6")
        editor.out_spin.setValue(9.7)  # 9.7 - 4.7
        assert editor.length_label.text().startswith("5.0")

    def test_step_is_a_tenth_of_a_second(self, editor):
        _seed(editor)
        editor.in_spin.stepBy(1)
        assert editor.current_window()[0] == pytest.approx(4.8)


class TestBounds:
    def test_out_cannot_precede_in(self, editor):
        _seed(editor)
        editor.out_spin.setValue(1.0)
        in_value, out_value = editor.current_window()
        assert out_value == pytest.approx(in_value + MIN_CLIP_SECONDS)

    def test_in_cannot_pass_out(self, editor):
        _seed(editor)
        editor.in_spin.setValue(99.0)
        in_value, out_value = editor.current_window()
        assert in_value == pytest.approx(out_value - MIN_CLIP_SECONDS)

    def test_in_never_goes_negative(self, editor):
        editor.set_word(0.1, 2.0, PADDING, None)
        editor.in_spin.setValue(-5.0)
        assert editor.current_window()[0] == 0.0

    def test_length_capped(self, editor):
        """A slipped decimal must not ask for a multi-minute clip."""
        _seed(editor)
        editor.out_spin.setValue(900.0)
        in_value, out_value = editor.current_window()
        assert out_value - in_value == pytest.approx(MAX_CLIP_SECONDS)


class TestReset:
    def test_restores_the_default(self, editor):
        _seed(editor, override=(4.0, 9.5))
        editor.reset_button.click()
        assert editor.current_window() == DEFAULT
        assert editor.has_override() is False

    def test_emits_reset_not_change(self, editor):
        _seed(editor, override=(4.0, 9.5))
        resets: list[bool] = []
        changes: list[tuple[float, float]] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.clip_changed.connect(lambda a, b: changes.append((a, b)))
        editor.reset_button.click()
        assert resets == [True]
        assert changes == []

    def test_noop_without_a_word(self, editor):
        resets: list[bool] = []
        editor.clip_reset.connect(lambda: resets.append(True))
        editor.reset_button.click()
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


class TestExpansion:
    def test_toggle_shows_the_body(self, editor):
        editor.set_expanded(True)
        assert editor.expanded is True
        assert editor.body.isVisibleTo(editor)

    def test_toggle_reports_state(self, editor):
        states: list[bool] = []
        editor.expanded_changed.connect(states.append)
        editor.set_expanded(True)
        editor.set_expanded(False)
        assert states == [True, False]
