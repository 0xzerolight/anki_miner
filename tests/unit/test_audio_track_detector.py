"""Tests for audio_track_detector utility."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    JapaneseAudioStream,
    find_japanese_audio_stream,
)

MODULE = "anki_miner.utils.audio_track_detector"


def _ffprobe_json(streams: list[dict]) -> str:
    """Build an ffprobe JSON payload from descriptors.

    Each descriptor must include `index`; `language` is optional.
    """
    out = []
    for s in streams:
        entry = {"index": s["index"], "codec_type": "audio", "tags": {}}
        if "language" in s:
            entry["tags"]["language"] = s["language"]
        out.append(entry)
    return json.dumps({"streams": out})


def _mock_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@pytest.fixture
def video_file(tmp_path):
    return tmp_path / "episode.mkv"


class TestFindJapaneseAudioStream:
    def test_japanese_at_position_zero(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = find_japanese_audio_stream(video_file)
        assert result == JapaneseAudioStream(global_index=0, audio_index=0, language_tag="jpn")

    def test_japanese_after_english(self, video_file):
        stdout = _ffprobe_json(
            [
                {"index": 1, "language": "eng"},
                {"index": 2, "language": "jpn"},
            ]
        )
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = find_japanese_audio_stream(video_file)
        assert result is not None
        assert result.global_index == 2
        assert result.audio_index == 1
        assert result.language_tag == "jpn"

    def test_no_japanese_returns_none(self, video_file):
        stdout = _ffprobe_json(
            [
                {"index": 0, "language": "eng"},
                {"index": 1, "language": "fre"},
            ]
        )
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            assert find_japanese_audio_stream(video_file) is None

    def test_no_streams_returns_none(self, video_file):
        stdout = json.dumps({"streams": []})
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            assert find_japanese_audio_stream(video_file) is None

    def test_missing_language_tag_returns_none(self, video_file):
        stdout = _ffprobe_json([{"index": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            assert find_japanese_audio_stream(video_file) is None

    def test_ffprobe_nonzero_returncode_returns_none(self, video_file):
        with patch(
            f"{MODULE}.subprocess.run",
            return_value=_mock_proc(returncode=1, stderr="boom"),
        ):
            assert find_japanese_audio_stream(video_file) is None

    def test_ffprobe_subprocess_error_returns_none(self, video_file):
        with patch(
            f"{MODULE}.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ):
            assert find_japanese_audio_stream(video_file) is None

    def test_ffprobe_os_error_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError("ffprobe missing")):
            assert find_japanese_audio_stream(video_file) is None

    def test_malformed_json_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout="not json{")):
            assert find_japanese_audio_stream(video_file) is None

    @pytest.mark.parametrize("lang_code", sorted(JAPANESE_LANGUAGE_CODES))
    def test_detects_all_japanese_codes_lowercase(self, video_file, lang_code):
        stdout = _ffprobe_json([{"index": 7, "language": lang_code}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = find_japanese_audio_stream(video_file)
        assert result is not None
        assert result.global_index == 7
        assert result.audio_index == 0
        assert result.language_tag == lang_code

    @pytest.mark.parametrize("lang_code", ["JA", "JPN", "Japanese", "Jp"])
    def test_language_tag_matching_is_case_insensitive(self, video_file, lang_code):
        stdout = _ffprobe_json([{"index": 0, "language": lang_code}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = find_japanese_audio_stream(video_file)
        assert result is not None
        assert result.language_tag == lang_code.lower()

    def test_skips_streams_with_no_index(self, video_file):
        payload = {
            "streams": [
                {"codec_type": "audio", "tags": {"language": "jpn"}},
                {"index": 4, "codec_type": "audio", "tags": {"language": "jpn"}},
            ]
        }
        with patch(
            f"{MODULE}.subprocess.run",
            return_value=_mock_proc(stdout=json.dumps(payload)),
        ):
            result = find_japanese_audio_stream(video_file)
        assert result is not None
        assert result.global_index == 4
        assert result.audio_index == 1

    def test_ffprobe_command_uses_select_audio_streams(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            find_japanese_audio_stream(video_file)
        args = mock_run.call_args[0][0]
        assert args[0] == "ffprobe"
        assert "-select_streams" in args
        assert args[args.index("-select_streams") + 1] == "a"
        assert str(video_file) in args
