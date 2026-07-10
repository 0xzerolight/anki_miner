"""Tests for SubtitleTracksDialog."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QRadioButton

from anki_miner.gui.widgets.dialogs.subtitle_tracks_dialog import (
    SubtitleTracksDialog,
    _format_track_label,
)
from anki_miner.utils.audio_track_detector import SubtitleStream

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream(
    sub_index: int,
    *,
    index: int | None = None,
    language: str | None = None,
    title: str | None = None,
    codec: str = "subrip",
    is_text: bool = True,
) -> SubtitleStream:
    return SubtitleStream(
        index=index if index is not None else sub_index,
        sub_index=sub_index,
        codec_name=codec,
        language_tag=language,
        title=title,
        is_text=is_text,
    )


def _radios(dialog: SubtitleTracksDialog) -> list[QRadioButton]:
    return dialog.findChildren(QRadioButton)


def _labels(dialog: SubtitleTracksDialog) -> list[QLabel]:
    return dialog.findChildren(QLabel)


# ---------------------------------------------------------------------------
# 1. Multi-track render
# ---------------------------------------------------------------------------


def test_multi_track_render(qtbot) -> None:
    jp = _stream(0, language="jpn", codec="subrip")
    en = _stream(1, language="eng", codec="ass")
    en_com = _stream(2, language="eng", codec="subrip")

    dialog = SubtitleTracksDialog([jp, en, en_com], current_override=None, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    assert len(radios) == 4  # 1 Auto + 3 tracks

    auto_radio = radios[0]
    assert auto_radio.isChecked()
    assert "Track 1" in auto_radio.text()
    assert "jpn" in auto_radio.text()

    texts = [r.text() for r in radios[1:]]
    assert any("SUBRIP" in t for t in texts)
    assert any("ASS" in t for t in texts)
    assert any("eng" in t for t in texts)


# ---------------------------------------------------------------------------
# 2. Preselect from current_override
# ---------------------------------------------------------------------------


def test_multi_track_preselect_override(qtbot) -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    en_com = _stream(2, language="eng")

    dialog = SubtitleTracksDialog([jp, en, en_com], current_override=1, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    auto_radio = radios[0]
    assert not auto_radio.isChecked()

    checked = [r for r in radios if r.isChecked()]
    assert len(checked) == 1
    assert "Track 2" in checked[0].text()


# ---------------------------------------------------------------------------
# 3. Apply round-trip (select sub_index=2)
# ---------------------------------------------------------------------------


def test_multi_track_apply_round_trip(qtbot) -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = SubtitleTracksDialog([jp, en, fr], current_override=None, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    # radios[0] = Auto, radios[1]=jp(0), radios[2]=en(1), radios[3]=fr(2)
    radios[3].setChecked(True)
    dialog._on_accept()

    assert dialog.selected_override() == 2


# ---------------------------------------------------------------------------
# 4. Auto round-trip
# ---------------------------------------------------------------------------


def test_auto_round_trip(qtbot) -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = SubtitleTracksDialog([jp, en, fr], current_override=1, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    radios[0].setChecked(True)  # Auto
    dialog._on_accept()

    assert dialog.selected_override() is None


# ---------------------------------------------------------------------------
# 5. Cancel preserves override
# ---------------------------------------------------------------------------


def test_cancel_preserves_override(qtbot) -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")
    fr = _stream(2, language="fra")

    dialog = SubtitleTracksDialog([jp, en, fr], current_override=1, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    # Select a different track but reject
    radios[3].setChecked(True)  # sub_index=2
    dialog.reject()

    assert dialog.selected_override() == 1


# ---------------------------------------------------------------------------
# 6. Auto-detect none label (no JP text track found)
# ---------------------------------------------------------------------------


def test_auto_detect_none_label(qtbot) -> None:
    jp = _stream(0, language="jpn")
    en = _stream(1, language="eng")

    dialog = SubtitleTracksDialog([jp, en], current_override=None, auto_detected=None)
    qtbot.addWidget(dialog)
    auto_radio = _radios(dialog)[0]

    assert "no Japanese subtitle track found" in auto_radio.text()


# ---------------------------------------------------------------------------
# 7. Auto label prefers the JP text track it was handed
# ---------------------------------------------------------------------------


def test_auto_label_uses_jp_text_track(qtbot) -> None:
    en = _stream(0, language="eng")
    jp = _stream(1, language="jpn")

    dialog = SubtitleTracksDialog([en, jp], current_override=None, auto_detected=jp)
    qtbot.addWidget(dialog)
    auto_radio = _radios(dialog)[0]

    assert "Track 2" in auto_radio.text()
    assert "jpn" in auto_radio.text()
    assert auto_radio.isChecked()


# ---------------------------------------------------------------------------
# 8. Bitmap rows disabled + annotated + unselectable
# ---------------------------------------------------------------------------


def test_bitmap_row_disabled_and_annotated(qtbot) -> None:
    jp = _stream(0, language="jpn", codec="subrip")
    pgs = _stream(1, language="eng", codec="hdmv_pgs_subtitle", is_text=False)

    dialog = SubtitleTracksDialog([jp, pgs], current_override=None, auto_detected=jp)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    # radios[0]=Auto, radios[1]=jp(text), radios[2]=pgs(bitmap)
    assert radios[1].isEnabled()
    assert not radios[2].isEnabled()
    assert "cannot condense" in radios[2].text()


def test_bitmap_override_falls_back_to_auto(qtbot) -> None:
    jp = _stream(0, language="jpn", codec="subrip")
    pgs = _stream(1, language="eng", codec="dvd_subtitle", is_text=False)

    # current_override points at the bitmap track — unselectable, so Auto wins
    dialog = SubtitleTracksDialog([jp, pgs], current_override=1, auto_detected=jp)
    qtbot.addWidget(dialog)

    assert dialog.selected_override() is None
    assert _radios(dialog)[0].isChecked()


# ---------------------------------------------------------------------------
# 9. Single-track variant
# ---------------------------------------------------------------------------


def test_single_track_variant(qtbot) -> None:
    stream = _stream(0, language="jpn", codec="subrip")
    dialog = SubtitleTracksDialog([stream], current_override=None, auto_detected=stream)
    qtbot.addWidget(dialog)

    assert len(_radios(dialog)) == 0
    assert dialog.selected_override() is None

    label_texts = [lbl.text() for lbl in _labels(dialog)]
    assert any("only one subtitle track" in t for t in label_texts)


# ---------------------------------------------------------------------------
# 10. Zero-track variant
# ---------------------------------------------------------------------------


def test_zero_track_variant(qtbot) -> None:
    dialog = SubtitleTracksDialog([], current_override=None, auto_detected=None)
    qtbot.addWidget(dialog)

    assert dialog.selected_override() is None

    label_texts = [lbl.text() for lbl in _labels(dialog)]
    assert any("No subtitle tracks found" in t for t in label_texts)


# ---------------------------------------------------------------------------
# 11. Single/zero-track variants always return None, even with a non-None override
# ---------------------------------------------------------------------------


def test_single_track_returns_none_even_with_override(qtbot) -> None:
    stream = _stream(0, language="jpn", codec="subrip")
    dialog = SubtitleTracksDialog([stream], current_override=5, auto_detected=stream)
    qtbot.addWidget(dialog)
    assert dialog.selected_override() is None


def test_zero_track_returns_none_even_with_override(qtbot) -> None:
    dialog = SubtitleTracksDialog([], current_override=5, auto_detected=None)
    qtbot.addWidget(dialog)
    assert dialog.selected_override() is None


# ---------------------------------------------------------------------------
# 12. Stale current_override falls back to None
# ---------------------------------------------------------------------------


def test_stale_current_override_falls_back_to_none(qtbot) -> None:
    s0 = _stream(0, language="jpn")
    s1 = _stream(1, language="eng")

    dialog = SubtitleTracksDialog([s0, s1], current_override=99, auto_detected=s0)
    qtbot.addWidget(dialog)

    assert dialog.selected_override() is None
    auto_radio = _radios(dialog)[0]
    assert auto_radio.isChecked()


# ---------------------------------------------------------------------------
# 13. Title tag appended
# ---------------------------------------------------------------------------


def test_title_tag_appended(qtbot) -> None:
    jp = _stream(0, language="jpn", title="Signs & Songs")
    en = _stream(1, language="eng")

    dialog = SubtitleTracksDialog([jp, en], current_override=None, auto_detected=en)
    qtbot.addWidget(dialog)
    radios = _radios(dialog)

    # radios[1] is the jp track (sub_index=0)
    assert "Signs & Songs" in radios[1].text()


# ---------------------------------------------------------------------------
# 14. _format_track_label
# ---------------------------------------------------------------------------


def test_format_track_label_text() -> None:
    label = _format_track_label(_stream(0, language="jpn", codec="ass", title="Full"))
    assert label == "Track 1 — jpn · ASS (Full)"


def test_format_track_label_bitmap() -> None:
    label = _format_track_label(_stream(2, language="eng", codec="dvd_subtitle", is_text=False))
    assert "Track 3 — eng · DVD_SUBTITLE" in label
    assert "image-based — cannot condense" in label


def test_format_track_label_missing_language_and_codec() -> None:
    label = _format_track_label(
        SubtitleStream(index=0, sub_index=0, codec_name=None, language_tag=None, title=None, is_text=True)
    )
    assert label == "Track 1 — und · ?"
