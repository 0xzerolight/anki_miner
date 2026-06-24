"""Tests for anki_miner.services.asr.srt_writer.

pysubs2 is a core dependency; these tests import it directly to verify
round-trip correctness of the produced SRT files.
"""

from __future__ import annotations

import pysubs2

from anki_miner.services.asr.srt_writer import segments_to_srt

# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------


def test_srt_round_trip_single_segment(tmp_path):
    """A single segment must round-trip through SRT with correct timing and text."""
    out = tmp_path / "out.srt"
    segments_to_srt([(0.0, 2.5, "Hello world")], out)

    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 1
    assert subs[0].start == 0  # 0.0s → 0 ms
    assert subs[0].end == 2500  # 2.5s → 2500 ms
    assert subs[0].text == "Hello world"


def test_srt_round_trip_multiple_segments(tmp_path):
    """Multiple segments must all appear in the SRT in order with correct data."""
    out = tmp_path / "multi.srt"
    segments = [
        (0.0, 1.0, "first"),
        (1.5, 3.0, "second"),
        (3.5, 5.0, "third"),
    ]
    segments_to_srt(segments, out)

    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 3
    assert subs[0].text == "first"
    assert subs[1].text == "second"
    assert subs[2].text == "third"
    assert subs[0].start == 0
    assert subs[0].end == 1000
    assert subs[1].start == 1500
    assert subs[1].end == 3000


def test_srt_timing_millisecond_precision(tmp_path):
    """Timing must be rounded to milliseconds correctly."""
    out = tmp_path / "timing.srt"
    segments_to_srt([(1.2345, 4.9876, "text")], out)

    subs = pysubs2.load(str(out), format_="srt")
    assert subs[0].start == round(1.2345 * 1000)
    assert subs[0].end == round(4.9876 * 1000)


def test_srt_text_stripped(tmp_path):
    """Text must be stripped of leading/trailing whitespace in the SRT."""
    out = tmp_path / "stripped.srt"
    segments_to_srt([(0.0, 1.0, "  trimmed  ")], out)

    subs = pysubs2.load(str(out), format_="srt")
    assert subs[0].text == "trimmed"


def test_srt_file_created(tmp_path):
    """segments_to_srt must create the file on disk."""
    out = tmp_path / "exists.srt"
    assert not out.exists()
    segments_to_srt([(0.0, 1.0, "hi")], out)
    assert out.exists()


def test_srt_empty_segments_creates_empty_file(tmp_path):
    """Empty segment list must produce a valid (empty) SRT file."""
    out = tmp_path / "empty.srt"
    segments_to_srt([], out)
    assert out.exists()
    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 0


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_srt_skips_zero_duration_segments(tmp_path):
    """Segments where start == end must be omitted from the SRT."""
    out = tmp_path / "zero_dur.srt"
    segments = [
        (0.0, 0.0, "zero duration"),
        (1.0, 2.0, "valid"),
    ]
    segments_to_srt(segments, out)

    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 1
    assert subs[0].text == "valid"


def test_srt_skips_empty_text_segments(tmp_path):
    """Segments with empty text (after stripping) must be omitted."""
    out = tmp_path / "empty_text.srt"
    segments = [
        (0.0, 1.0, "   "),  # whitespace only → empty after strip
        (1.0, 2.0, "real"),
    ]
    segments_to_srt(segments, out)

    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 1
    assert subs[0].text == "real"


def test_srt_skips_both_zero_duration_and_empty_text(tmp_path):
    """Both zero-duration AND empty-text segments must be skipped."""
    out = tmp_path / "both.srt"
    segments = [
        (0.0, 0.0, "zero dur"),
        (1.0, 2.0, ""),
        (2.0, 3.0, "keep"),
    ]
    segments_to_srt(segments, out)

    subs = pysubs2.load(str(out), format_="srt")
    assert len(subs) == 1
    assert subs[0].text == "keep"


# ---------------------------------------------------------------------------
# Japanese text
# ---------------------------------------------------------------------------


def test_srt_japanese_text_preserved(tmp_path):
    """Japanese text must survive the SRT round-trip without corruption."""
    out = tmp_path / "ja.srt"
    text = "日本語のテキスト"
    segments_to_srt([(0.0, 3.0, text)], out)

    subs = pysubs2.load(str(out), format_="srt", encoding="utf-8")
    assert subs[0].text == text
