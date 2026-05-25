"""Tests for AudioTracksDialog and _format_channels helper."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QRadioButton

from anki_miner.gui.widgets.dialogs.audio_tracks_dialog import (
    AudioTracksDialog,
    _format_channels,
)
from anki_miner.utils.audio_track_detector import AudioStream

# One QApplication per process; reuse if already created.
_app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream(
    audio_index: int,
    *,
    global_index: int | None = None,
    language: str | None = None,
    title: str | None = None,
    codec: str = "aac",
    channels: int = 2,
    default: bool = False,
) -> AudioStream:
    return AudioStream(
        global_index=global_index if global_index is not None else audio_index,
        audio_index=audio_index,
        language_tag=language,
        title_tag=title,
        codec=codec,
        channels=channels,
        is_default=default,
    )


def _radios(dialog: AudioTracksDialog) -> list[QRadioButton]:
    return dialog.findChildren(QRadioButton)


def _labels(dialog: AudioTracksDialog) -> list[QLabel]:
    return dialog.findChildren(QLabel)


# ---------------------------------------------------------------------------
# 1. _format_channels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channels, expected",
    [
        (1, "mono"),
        (2, "stereo"),
        (6, "5.1"),
        (8, "7.1"),
        (3, "3ch"),
        (None, ""),
    ],
)
def test_format_channels(channels: int | None, expected: str) -> None:
    assert _format_channels(channels) == expected


# ---------------------------------------------------------------------------
# 2. Multi-track render
# ---------------------------------------------------------------------------


def test_multi_track_render() -> None:
    jp = _stream(0, language="jpn", codec="aac", channels=2)
    en = _stream(1, language="eng", codec="ac3", channels=6)
    en_com = _stream(2, language="eng", codec="aac", channels=2)

    dialog = AudioTracksDialog([jp, en, en_com], current_override=None, auto_detected=jp)
    radios = _radios(dialog)

    assert len(radios) == 4  # 1 Auto + 3 tracks

    auto_radio = radios[0]
    assert auto_radio.isChecked()
    assert "Track 1" in auto_radio.text()
    assert "jpn" in auto_radio.text()

    texts = [r.text() for r in radios[1:]]
    assert any("AAC" in t and "stereo" in t for t in texts)
    assert any("AC3" in t and "5.1" in t for t in texts)
    assert any("eng" in t for t in texts)


# ---------------------------------------------------------------------------
# 3. Preselect from current_override
# ---------------------------------------------------------------------------


def test_multi_track_preselect_override() -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    en_com = _stream(2, language="eng")

    dialog = AudioTracksDialog([jp, en, en_com], current_override=1, auto_detected=jp)
    radios = _radios(dialog)

    auto_radio = radios[0]
    assert not auto_radio.isChecked()

    checked = [r for r in radios if r.isChecked()]
    assert len(checked) == 1
    assert "Track 2" in checked[0].text()


# ---------------------------------------------------------------------------
# 4. Apply round-trip (select audio_index=2)
# ---------------------------------------------------------------------------


def test_multi_track_apply_round_trip() -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = AudioTracksDialog([jp, en, fr], current_override=None, auto_detected=jp)
    radios = _radios(dialog)

    # radios[0] = Auto, radios[1]=jp(0), radios[2]=en(1), radios[3]=fr(2)
    radios[3].setChecked(True)
    dialog._on_accept()

    assert dialog.selected_override() == 2


# ---------------------------------------------------------------------------
# 5. Auto round-trip
# ---------------------------------------------------------------------------


def test_auto_round_trip() -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = AudioTracksDialog([jp, en, fr], current_override=1, auto_detected=jp)
    radios = _radios(dialog)

    radios[0].setChecked(True)  # Auto
    dialog._on_accept()

    assert dialog.selected_override() is None


# ---------------------------------------------------------------------------
# 6. Cancel preserves override
# ---------------------------------------------------------------------------


def test_cancel_preserves_override() -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = AudioTracksDialog([jp, en, fr], current_override=1, auto_detected=jp)
    radios = _radios(dialog)

    # Select a different track but reject
    radios[3].setChecked(True)  # audio_index=2
    dialog.reject()

    assert dialog.selected_override() == 1


# ---------------------------------------------------------------------------
# 7. Auto-detect none label
# ---------------------------------------------------------------------------


def test_auto_detect_none_label() -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")

    dialog = AudioTracksDialog([jp, en], current_override=None, auto_detected=None)
    auto_radio = _radios(dialog)[0]

    assert "no Japanese track found" in auto_radio.text()


# ---------------------------------------------------------------------------
# 8. Single-track variant
# ---------------------------------------------------------------------------


def test_single_track_variant() -> None:
    stream = _stream(0, language="jpn", codec="aac", channels=2)
    dialog = AudioTracksDialog([stream], current_override=None, auto_detected=stream)

    assert len(_radios(dialog)) == 0
    assert dialog.selected_override() is None

    label_texts = [lbl.text() for lbl in _labels(dialog)]
    assert any("only one audio track" in t for t in label_texts)


# ---------------------------------------------------------------------------
# 9. Zero-track variant
# ---------------------------------------------------------------------------


def test_zero_track_variant() -> None:
    dialog = AudioTracksDialog([], current_override=None, auto_detected=None)

    assert dialog.selected_override() is None

    label_texts = [lbl.text() for lbl in _labels(dialog)]
    assert any("No audio tracks found" in t for t in label_texts)


# ---------------------------------------------------------------------------
# 10. Title tag appended
# ---------------------------------------------------------------------------


def test_title_tag_appended() -> None:
    jp = _stream(0, language="jpn", title="Director's Commentary")
    en = _stream(1, language="eng")

    dialog = AudioTracksDialog([jp, en], current_override=None, auto_detected=en)
    radios = _radios(dialog)

    # radios[1] is the jp track (audio_index=0)
    assert "Director's Commentary" in radios[1].text()
