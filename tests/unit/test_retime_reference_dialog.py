"""Tests for the retiming reference picker.

The dialog flattens two kinds of stream into one list, so the thing worth
pinning is that a chosen row maps back to the right override.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.dialogs import ReferenceChoice, RetimeReferenceDialog, build_reference_choices
from anki_miner.services.retime_reference import ReferenceOverride
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream


def _sub(sub_index: int, *, is_text: bool = True) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name="ass" if is_text else "hdmv_pgs_subtitle",
        language_tag="eng",
        title=None,
        is_text=is_text,
    )


def _audio(audio_index: int) -> AudioStream:
    return AudioStream(
        global_index=audio_index + 10,
        audio_index=audio_index,
        language_tag="jpn",
        title_tag=None,
        codec="aac",
        channels=2,
        is_default=audio_index == 0,
    )


class TestBuildReferenceChoices:
    def test_subtitles_come_before_audio_with_contiguous_positions(self) -> None:
        choices = build_reference_choices([_sub(0), _sub(1)], [_audio(0)])
        assert [c.position for c in choices] == [0, 1, 2]
        assert [c.kind for c in choices] == ["subtitle", "subtitle", "audio"]

    def test_stream_index_is_preserved_per_kind(self) -> None:
        """Position is a row number; stream_index is what the retimer needs."""
        choices = build_reference_choices([_sub(3)], [_audio(2)])
        assert choices[0].to_override() == ReferenceOverride(kind="subtitle", index=3)
        assert choices[1].to_override() == ReferenceOverride(kind="audio", index=2)

    def test_bitmap_subtitles_are_listed_but_unselectable(self) -> None:
        """Hiding them would leave the user hunting for a track they can see elsewhere."""
        choices = build_reference_choices([_sub(0, is_text=False)], [])
        assert len(choices) == 1
        assert choices[0].selectable is False

    def test_empty_inputs_give_no_rows(self) -> None:
        assert build_reference_choices([], []) == []


class TestRetimeReferenceDialog:
    def test_auto_is_the_default_and_returns_none(self, qtbot) -> None:
        dialog = RetimeReferenceDialog(
            streams=build_reference_choices([_sub(0)], [_audio(0)]),
            current_override=None,
            auto_detected=None,
        )
        qtbot.addWidget(dialog)
        dialog._on_accept()
        assert dialog.selected_override() is None

    def test_selecting_a_row_returns_its_position(self, qtbot) -> None:
        choices = build_reference_choices([_sub(0)], [_audio(0)])
        dialog = RetimeReferenceDialog(streams=choices, current_override=1, auto_detected=None)
        qtbot.addWidget(dialog)
        dialog._on_accept()
        assert dialog.selected_override() == 1

    def test_a_bitmap_row_cannot_be_preselected(self, qtbot) -> None:
        choices = build_reference_choices([_sub(0, is_text=False)], [_audio(0)])
        dialog = RetimeReferenceDialog(streams=choices, current_override=0, auto_detected=None)
        qtbot.addWidget(dialog)
        dialog._on_accept()
        assert dialog.selected_override() is None

    def test_labels_name_the_kind_and_the_track_number(self, qtbot) -> None:
        dialog = RetimeReferenceDialog(
            streams=build_reference_choices([_sub(1)], [_audio(0)]),
            current_override=None,
            auto_detected=None,
        )
        qtbot.addWidget(dialog)
        sub_label = dialog._format_track_label(ReferenceChoice(0, "subtitle", 1, "eng", None, "ass", True))
        audio_label = dialog._format_track_label(ReferenceChoice(1, "audio", 0, "jpn", None, "aac", True))
        assert "Subtitle" in sub_label and "2" in sub_label
        assert "Audio" in audio_label and "1" in audio_label
