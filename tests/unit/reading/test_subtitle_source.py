"""Tests for the subtitle-file reading source loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import subtitle_source
from anki_miner.services.reading.subtitle_source import _format_cue_time

_SRT = """\
1
00:00:01,000 --> 00:00:03,000
こんにちは

2
00:01:23,500 --> 00:01:25,000
元気ですか

3
01:02:03,000 --> 01:02:05,000
さようなら
"""

_ASS_STYLELESS = """\
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,こんにちは
Comment: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,これはコメント
Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,{\\b1}太字{\\b0}テキスト
Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,
"""

_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:03.000
こんにちは

00:00:04.000 --> 00:00:06.000
元気ですか
"""


def _ref(path: Path) -> ReadingSourceRef:
    return ReadingSourceRef(
        kind="subtitle",
        path=path,
        image_root=None,
        title=path.stem,
        volume=None,
    )


def _write(tmp_path: Path, name: str, content: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


# --------------------------------------------------------------------------- #
# Per-cue units.
# --------------------------------------------------------------------------- #


def test_srt_one_unit_per_cue_in_order(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.srt", _SRT)))

    assert [u.text for u in doc.units] == ["こんにちは", "元気ですか", "さようなら"]
    assert [u.index for u in doc.units] == [0, 1, 2]
    assert all(u.image_ref is None for u in doc.units)


def test_location_labels_are_cue_start_times(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.srt", _SRT)))

    assert [u.location_label for u in doc.units] == ["0:01", "1:23", "1:02:03"]


def test_document_identity_mirrors_video_path(tmp_path):
    show_dir = tmp_path / "MyShow"
    show_dir.mkdir()
    doc = subtitle_source.load(_ref(_write(show_dir, "Ep01.srt", _SRT)))

    assert doc.kind == "subtitle"
    assert doc.series == "MyShow"
    assert doc.episode == "Ep01"
    assert doc.title == "Ep01"
    assert doc.warnings == []


def test_vtt_parses(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.vtt", _VTT)))

    assert [u.text for u in doc.units] == ["こんにちは", "元気ですか"]


# --------------------------------------------------------------------------- #
# Cue cleaning: comments, styling tags, empties.
# --------------------------------------------------------------------------- #


def test_styleless_ass_parses_via_extension_format(tmp_path):
    # Content autodetection raises FormatAutodetectionError on a styles-less
    # ASS; the loader must force format_ from the extension.
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.ass", _ASS_STYLELESS)))

    assert [u.text for u in doc.units] == ["こんにちは", "太字テキスト"]


def test_comment_events_skipped(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.ass", _ASS_STYLELESS)))

    assert all("コメント" not in u.text for u in doc.units)


def test_styling_tags_stripped_and_empty_cues_dropped(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.ass", _ASS_STYLELESS)))

    # {\b1}/{\b0} stripped by clean_subtitle_text; the empty Dialogue dropped.
    assert "太字テキスト" in [u.text for u in doc.units]
    assert all(u.text for u in doc.units)
    # Indexes stay dense after drops (they drive unit_labels lookups).
    assert [u.index for u in doc.units] == list(range(len(doc.units)))


def test_uppercase_extension_parses(tmp_path):
    # detect() accepts EP01.SRT; the loader must lowercase before the
    # ext→format lookup (map keys are lowercase).
    doc = subtitle_source.load(_ref(_write(tmp_path, "EP01.SRT", _SRT)))

    assert len(doc.units) == 3


# --------------------------------------------------------------------------- #
# Encoding.
# --------------------------------------------------------------------------- #


def test_cp932_file_decodes(tmp_path):
    doc = subtitle_source.load(_ref(_write(tmp_path, "Ep01.srt", _SRT, encoding="cp932")))

    assert [u.text for u in doc.units] == ["こんにちは", "元気ですか", "さようなら"]


# --------------------------------------------------------------------------- #
# Failure modes.
# --------------------------------------------------------------------------- #


def test_missing_file_raises_setup_error(tmp_path):
    with pytest.raises(SetupError) as excinfo:
        subtitle_source.load(_ref(tmp_path / "ghost.srt"))

    assert "ghost.srt" in str(excinfo.value)


def test_unparseable_content_raises_setup_error(tmp_path):
    with pytest.raises(SetupError) as excinfo:
        subtitle_source.load(_ref(_write(tmp_path, "Ep01.srt", "not a subtitle at all")))

    assert "Ep01.srt" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Timestamp formatting helper.
# --------------------------------------------------------------------------- #


def test_format_cue_time_trims_leading_zero_units():
    assert _format_cue_time(1.0) == "0:01"
    assert _format_cue_time(83.5) == "1:23"
    assert _format_cue_time(3723.0) == "1:02:03"
