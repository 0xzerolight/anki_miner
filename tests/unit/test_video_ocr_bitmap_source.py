"""F-18: which image subtitle track Video OCR reads, on how big a canvas, for how long."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.video_ocr.bitmap_source import (
    CANVAS_COLOUR,
    BitmapSource,
    ffmpeg_input_args,
    is_bitmap_file,
    open_bitmap_source,
    overlay_filter,
    vobsub_index,
)
from anki_miner.services.video_ocr.errors import FrameSourceError
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.utils.audio_track_detector import SubtitleStream

_LIST = "anki_miner.services.video_ocr.bitmap_source.list_subtitle_streams"
_SIZE = "anki_miner.services.video_ocr.bitmap_source.get_video_size"
_RUN = "anki_miner.services.video_ocr.bitmap_source.subprocess.run"


def _image(sub_index: int = 0, *, width: int | None = 1920, height: int | None = 1080) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name="hdmv_pgs_subtitle",
        language_tag="jpn",
        title=None,
        is_text=False,
        width=width,
        height=height,
    )


def _text(sub_index: int = 0) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1, sub_index=sub_index, codec_name="ass", language_tag="jpn", title=None, is_text=True
    )


def _packets(stdout: str, returncode: int = 0) -> MagicMock:
    done = MagicMock()
    done.returncode = returncode
    done.stdout = stdout
    done.stderr = ""
    return done


def test_the_full_frame_region():
    assert Region(0.0, 0.0, 1.0, 1.0) == FULL_FRAME


def test_image_subtitle_files_are_recognised():
    assert all(is_bitmap_file(Path(name)) for name in ("a.sup", "a.SUP", "a.idx", "a.sub"))
    assert not any(is_bitmap_file(Path(name)) for name in ("a.mkv", "a.srt", "a.ass"))


def test_a_vobsub_pair_resolves_to_its_idx_from_either_half(tmp_path):
    (tmp_path / "m.idx").write_text("x", encoding="utf-8")
    (tmp_path / "m.sub").write_bytes(b"x")
    assert vobsub_index(tmp_path / "m.sub") == tmp_path / "m.idx"
    assert vobsub_index(tmp_path / "m.idx") == tmp_path / "m.idx"


def test_half_a_vobsub_pair_is_refused(test_config, tmp_path):
    (tmp_path / "m.sub").write_bytes(b"x")
    assert vobsub_index(tmp_path / "m.sub") is None
    with pytest.raises(FrameSourceError, match=r"\.idx"):
        open_bitmap_source(test_config, tmp_path / "m.sub")


def test_a_sup_takes_its_track_size_and_last_packet(test_config, tmp_path):
    sup = tmp_path / "m.sup"
    sup.write_bytes(b"x")
    with (
        patch(_LIST, return_value=[_image()]),
        patch(_RUN, return_value=_packets("1.000000,N/A\n4.000000,N/A\n")) as run,
    ):
        source = open_bitmap_source(test_config, sup)
    assert source == BitmapSource(sup, 0, 1920, 1080, 5.0)
    assert "s:0" in run.call_args.args[0]


def test_a_packet_duration_extends_the_end(test_config, tmp_path):
    # VobSub packets carry a display duration; the canvas must outlast it.
    (tmp_path / "m.idx").write_text("x", encoding="utf-8")
    (tmp_path / "m.sub").write_bytes(b"x")
    with (
        patch(_LIST, return_value=[_image(width=720, height=480)]),
        patch(_RUN, return_value=_packets("10.000000,3.500000\n")),
    ):
        source = open_bitmap_source(test_config, tmp_path / "m.sub")
    assert source.path == tmp_path / "m.idx" and (source.width, source.height) == (720, 480)
    assert source.end_seconds == pytest.approx(14.5)


def test_a_video_track_is_chosen_by_position_with_the_video_size_as_fallback(test_config, tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    streams = [_text(0), _image(1, width=None, height=None)]
    with (
        patch(_LIST, return_value=streams),
        patch(_SIZE, return_value=(1280, 720)),
        patch(_RUN, return_value=_packets("2.0,N/A\n")),
    ):
        source = open_bitmap_source(test_config, video, sub_index=1)
    assert (source.sub_index, source.width, source.height) == (1, 1280, 720)


def test_an_odd_canvas_is_made_even(test_config, tmp_path):
    sup = tmp_path / "m.sup"
    sup.write_bytes(b"x")
    with patch(_LIST, return_value=[_image(width=721, height=481)]), patch(_RUN, return_value=_packets("1.0,N/A\n")):
        source = open_bitmap_source(test_config, sup)
    assert (source.width, source.height) == (720, 480)


def test_a_text_track_position_is_not_an_image_track(test_config, tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    with patch(_LIST, return_value=[_text(0)]), pytest.raises(FrameSourceError):
        open_bitmap_source(test_config, video, sub_index=0)


def test_unreadable_timing_is_a_frame_source_error(test_config, tmp_path):
    sup = tmp_path / "m.sup"
    sup.write_bytes(b"x")
    with (
        patch(_LIST, return_value=[_image()]),
        patch(_RUN, return_value=_packets("", returncode=1)),
        pytest.raises(FrameSourceError, match="timing"),
    ):
        open_bitmap_source(test_config, sup)


def test_ffmpeg_arguments_keep_timestamps_and_bound_the_canvas():
    source = BitmapSource(Path("/m.sup"), 0, 320, 96, 5.0)
    args = ffmpeg_input_args(source, 10)
    assert args[0] == "-copyts"
    assert args[args.index("-t") + 1] == "5.000"
    assert f"color=c={CANVAS_COLOUR}:s=320x96:r=10" in args
    assert args[-1] == str(Path("/m.sup"))
    assert overlay_filter(BitmapSource(Path("/v.mkv"), 2, 320, 96, 5.0)) == "[0:v][1:s:2]overlay=eof_action=pass"


def test_the_module_does_not_load_numpy():
    # The Video OCR tab imports it at app startup, which must stay numpy-free.
    code = "import sys, anki_miner.services.video_ocr.bitmap_source; sys.exit(1 if 'numpy' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], timeout=120).returncode == 0
