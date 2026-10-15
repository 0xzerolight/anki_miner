"""TS-10: Video OCR keeps only rows with a character of the mining language's script."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.video_ocr import scanner
from anki_miner.services.video_ocr.region import Region
from anki_miner.services.video_ocr.scanner import VideoOcrStatus, read_region_text, scan_video
from anki_miner.services.video_ocr.text_cleanup import keep_script_rows

# numpy-backed (the gate in test_asr_marker_gating.py)
pytestmark = pytest.mark.asr

JA = get_profile("ja").script.contains_target_script
ES = get_profile("es").script.contains_target_script
_WHOLE = Region(0.0, 0.0, 1.0, 1.0)


def test_japanese_keeps_kana_and_kanji_rows():
    assert keep_script_rows("「行くぞ」\nHP 100\n12:30", JA) == "「行くぞ」"


def test_spanish_keeps_latin_rows_and_drops_digits_and_symbols():
    # Review Focus 3: a non-Japanese mining language.
    assert keep_script_rows("¿Vamos?\n12:30\n♪♪\nSí, vamos.", ES) == "¿Vamos?\nSí, vamos."


def test_a_frame_with_no_script_row_reads_as_no_text():
    assert keep_script_rows("100 / 250", JA) == ""
    assert keep_script_rows("", ES) == ""


class _PixelReader:
    """Reads a solid frame's value as the text it stands for (0 = nothing on screen)."""

    def __init__(self, lines: dict[int, str]) -> None:
        self._lines = lines

    def read_text(self, bgr: np.ndarray) -> str:
        return self._lines.get(int(bgr[0, 0, 0]), "")


def _samples(*values: int):
    """One 0.1 s sample per value, each a solid 8x8 frame."""
    return iter([(i / 10, np.full((8, 8, 3), v, dtype=np.uint8)) for i, v in enumerate(values)])


def _one_line_clip(monkeypatch) -> None:
    # 1 s blank, 1 s of the line, 1 s blank.
    monkeypatch.setattr(scanner, "get_media_duration_seconds", lambda *a, **k: 3.0)
    monkeypatch.setattr(scanner, "iter_samples", lambda *a, **k: _samples(*([0] * 10 + [200] * 10 + [0] * 10)))


def test_scan_video_filters_rows_for_the_mining_language(test_config, tmp_path, monkeypatch):
    # Review Focus 3: the scan dispatches on the (Spanish) profile.
    _one_line_clip(monkeypatch)
    out = tmp_path / "clip.srt"
    result = scan_video(
        replace(test_config, language="es"),
        tmp_path / "clip.mkv",
        _WHOLE,
        out,
        reader=_PixelReader({200: "Hola, amigo.\n12:30"}),
    )
    assert result.status is VideoOcrStatus.SUCCESS and result.cue_count == 1
    written = out.read_text(encoding="utf-8")
    assert "Hola, amigo." in written and "12:30" not in written


def test_a_cue_of_only_digits_is_no_text(test_config, tmp_path, monkeypatch):
    _one_line_clip(monkeypatch)
    out = tmp_path / "clip.srt"
    result = scan_video(test_config, tmp_path / "clip.mkv", _WHOLE, out, reader=_PixelReader({200: "100 / 250"}))
    assert result.status is VideoOcrStatus.NO_TEXT and not out.exists()


def test_test_this_frame_applies_the_same_filter(test_config, monkeypatch):
    monkeypatch.setattr(scanner, "_reader", lambda config, reader: _PixelReader({0: "「行くぞ」\nHP 100"}))
    assert read_region_text(test_config, np.zeros((8, 8, 3), dtype=np.uint8)) == "「行くぞ」"
