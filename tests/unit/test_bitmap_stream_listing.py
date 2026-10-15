"""F-18: ffprobe's canvas size for image subtitle tracks, and the video size fallback."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from anki_miner.utils.audio_track_detector import get_video_size, list_subtitle_streams

_RUN = "anki_miner.utils.audio_track_detector.subprocess.run"


def _proc(payload: dict, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = json.dumps(payload)
    proc.stderr = ""
    return proc


def test_an_image_track_reports_its_canvas(tmp_path):
    payload = {
        "streams": [
            {"index": 2, "codec_name": "hdmv_pgs_subtitle", "width": 1920, "height": 1080, "tags": {"language": "jpn"}},
            {"index": 3, "codec_name": "ass", "tags": {}},
        ]
    }
    with patch(_RUN, return_value=_proc(payload)):
        image, text = list_subtitle_streams(tmp_path / "v.mkv")
    assert (image.is_text, image.width, image.height) == (False, 1920, 1080)
    assert (text.is_text, text.width, text.height) == (True, None, None)


def test_a_zero_or_garbled_size_is_unknown(tmp_path):
    payload = {"streams": [{"index": 2, "codec_name": "dvd_subtitle", "width": 0, "height": "x"}]}
    with patch(_RUN, return_value=_proc(payload)):
        (stream,) = list_subtitle_streams(tmp_path / "v.mkv")
    assert (stream.width, stream.height) == (None, None)


def test_get_video_size_reads_the_first_video_stream(tmp_path):
    payload = {"streams": [{"index": 0, "codec_name": "h264", "width": 1280, "height": 720}]}
    with patch(_RUN, return_value=_proc(payload)) as run:
        assert get_video_size(tmp_path / "v.mkv") == (1280, 720)
    assert "v:0" in run.call_args.args[0]


def test_get_video_size_is_none_without_a_video_or_on_failure(tmp_path):
    with patch(_RUN, return_value=_proc({"streams": []})):
        assert get_video_size(tmp_path / "v.sup") is None
    with patch(_RUN, return_value=_proc({}, returncode=1)):
        assert get_video_size(tmp_path / "v.mkv") is None
