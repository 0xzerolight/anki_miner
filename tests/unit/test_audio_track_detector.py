"""Tests for audio_track_detector utility."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    AudioStream,
    JapaneseAudioStream,
    find_japanese_audio_stream,
    get_primary_video_codec,
    list_audio_streams,
)

MODULE = "anki_miner.utils.audio_track_detector"


def _ffprobe_json(streams: list[dict]) -> str:
    """Build an ffprobe JSON payload from descriptors.

    Required key: ``index`` (int).
    Optional keys: ``language``, ``title``, ``codec_name``, ``channels``, ``default``.
    Existing tests only use ``index`` and ``language``; new keys are ignored when absent.
    """
    out = []
    for s in streams:
        entry = {"index": s["index"], "codec_type": "audio", "tags": {}}
        if "language" in s:
            entry["tags"]["language"] = s["language"]
        if "title" in s:
            entry["tags"]["title"] = s["title"]
        if "codec_name" in s:
            entry["codec_name"] = s["codec_name"]
        if "channels" in s:
            entry["channels"] = s["channels"]
        if "default" in s:
            entry["disposition"] = {"default": s["default"]}
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


class TestListAudioStreams:
    def test_empty_streams_returns_empty_list(self, video_file):
        stdout = json.dumps({"streams": []})
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result == []

    def test_single_stream_all_fields(self, video_file):
        stdout = _ffprobe_json(
            [
                {
                    "index": 2,
                    "language": "jpn",
                    "title": "Japanese 5.1",
                    "codec_name": "aac",
                    "channels": 6,
                    "default": 1,
                }
            ]
        )
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert len(result) == 1
        s = result[0]
        assert isinstance(s, AudioStream)
        assert s.global_index == 2
        assert s.audio_index == 0
        assert s.language_tag == "jpn"
        assert s.title_tag == "Japanese 5.1"
        assert s.codec == "aac"
        assert s.channels == 6
        assert s.is_default is True

    def test_multiple_streams_audio_index_increments(self, video_file):
        stdout = _ffprobe_json(
            [
                {"index": 1, "language": "eng", "codec_name": "ac3", "channels": 2, "default": 1},
                {"index": 2, "language": "jpn", "codec_name": "aac", "channels": 6, "default": 0},
            ]
        )
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert len(result) == 2
        assert result[0].audio_index == 0
        assert result[0].global_index == 1
        assert result[0].language_tag == "eng"
        assert result[1].audio_index == 1
        assert result[1].global_index == 2
        assert result[1].language_tag == "jpn"

    def test_missing_index_skipped_but_audio_index_slot_consumed(self, video_file):
        """Stream with no index is omitted from results; next stream gets audio_index=1."""
        payload = {
            "streams": [
                {"codec_type": "audio", "tags": {"language": "eng"}},  # no index — skipped
                {"index": 5, "codec_type": "audio", "tags": {"language": "jpn"}},
            ]
        }
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=json.dumps(payload))):
            result = list_audio_streams(video_file)
        assert len(result) == 1
        assert result[0].global_index == 5
        assert result[0].audio_index == 1  # slot 0 consumed by skipped stream

    def test_ffprobe_nonzero_returncode_returns_empty(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(returncode=1, stderr="boom")):
            assert list_audio_streams(video_file) == []

    def test_ffprobe_timeout_returns_empty(self, video_file):
        with patch(
            f"{MODULE}.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ):
            assert list_audio_streams(video_file) == []

    def test_ffprobe_os_error_returns_empty(self, video_file):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError("ffprobe missing")):
            assert list_audio_streams(video_file) == []

    def test_malformed_json_returns_empty(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout="not json{")):
            assert list_audio_streams(video_file) == []

    def test_disposition_default_one_is_true(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn", "default": 1}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].is_default is True

    def test_disposition_default_zero_is_false(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn", "default": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].is_default is False

    def test_disposition_absent_is_false(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].is_default is False

    def test_channels_as_int_coerced(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "channels": 2}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].channels == 2
        assert isinstance(result[0].channels, int)

    def test_channels_absent_is_none(self, video_file):
        stdout = _ffprobe_json([{"index": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].channels is None

    def test_language_tag_none_when_absent(self, video_file):
        stdout = _ffprobe_json([{"index": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].language_tag is None

    def test_title_tag_none_when_absent(self, video_file):
        stdout = _ffprobe_json([{"index": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].title_tag is None

    def test_codec_none_when_absent(self, video_file):
        stdout = _ffprobe_json([{"index": 0}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].codec is None

    def test_language_tag_lowercased(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "JPN"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = list_audio_streams(video_file)
        assert result[0].language_tag == "jpn"

    def test_ffprobe_command_uses_select_audio_streams(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            list_audio_streams(video_file)
        args = mock_run.call_args[0][0]
        assert args[0] == "ffprobe"
        assert "-select_streams" in args
        assert args[args.index("-select_streams") + 1] == "a"
        assert str(video_file) in args

    def test_default_ffprobe_cmd_is_bare_literal(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            list_audio_streams(video_file)
        assert mock_run.call_args[0][0][0] == "ffprobe"

    def test_custom_ffprobe_cmd_becomes_cmd0(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            list_audio_streams(video_file, ffprobe_cmd="/custom/ffprobe")
        args = mock_run.call_args[0][0]
        assert args[0] == "/custom/ffprobe"
        # Remaining args unchanged.
        assert "-select_streams" in args
        assert str(video_file) in args


class TestFindJapaneseFfprobeCmd:
    def test_find_japanese_forwards_custom_ffprobe_cmd(self, video_file):
        stdout = _ffprobe_json([{"index": 0, "language": "jpn"}])
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            find_japanese_audio_stream(video_file, ffprobe_cmd="/custom/ffprobe")
        assert mock_run.call_args[0][0][0] == "/custom/ffprobe"


def _video_json(codec_name) -> str:
    """Build an ffprobe JSON payload for a single video stream.

    ``codec_name`` of None omits the field; otherwise it's set verbatim.
    """
    stream: dict = {"index": 0, "codec_type": "video"}
    if codec_name is not None:
        stream["codec_name"] = codec_name
    return json.dumps({"streams": [stream]})


class TestGetPrimaryVideoCodec:
    def test_av1_returned_lowercase(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=_video_json("av1"))):
            assert get_primary_video_codec(video_file) == "av1"

    def test_uppercase_codec_normalized(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=_video_json("AV1"))):
            assert get_primary_video_codec(video_file) == "av1"

    def test_h264_returned(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=_video_json("h264"))):
            assert get_primary_video_codec(video_file) == "h264"

    def test_no_streams_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout='{"streams": []}')):
            assert get_primary_video_codec(video_file) is None

    def test_missing_codec_name_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=_video_json(None))):
            assert get_primary_video_codec(video_file) is None

    def test_nonzero_returncode_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(returncode=1, stderr="boom")):
            assert get_primary_video_codec(video_file) is None

    def test_subprocess_error_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)):
            assert get_primary_video_codec(video_file) is None

    def test_os_error_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", side_effect=FileNotFoundError("ffprobe missing")):
            assert get_primary_video_codec(video_file) is None

    def test_malformed_json_returns_none(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout="not json{")):
            assert get_primary_video_codec(video_file) is None

    def test_forwards_custom_ffprobe_cmd_and_selects_video_stream(self, video_file):
        with patch(f"{MODULE}.subprocess.run", return_value=_mock_proc(stdout=_video_json("av1"))) as mock_run:
            get_primary_video_codec(video_file, ffprobe_cmd="/custom/ffprobe")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/custom/ffprobe"
        assert "v:0" in cmd  # selects the first video stream
