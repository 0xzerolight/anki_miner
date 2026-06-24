"""Tests for media_extractor module."""

import dataclasses
import json
import subprocess
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.media_extractor import MediaExtractorService

MODULE = "anki_miner.services.media_extractor"
DETECTOR_MODULE = "anki_miner.utils.audio_track_detector"


def _popen_mock(returncode=0, stderr=""):
    """A MagicMock shaped like a finished ffmpeg Popen (communicate-style)."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = ("", stderr)
    return proc


@pytest.fixture
def service(test_config):
    """Create a MediaExtractorService with ensure_directory patched out."""
    with patch(f"{MODULE}.ensure_directory"):
        svc = MediaExtractorService(test_config)
    # Skip audio encoder probe; encode tests mock subprocess.Popen.
    svc._animated_encoder_ok["libmp3lame"] = True
    svc._animated_encoder_ok["libopus"] = True
    return svc


@pytest.fixture
def animated_avif_service(test_config):
    """Create a MediaExtractorService with animated AVIF screenshots enabled."""
    cfg = dataclasses.replace(
        test_config,
        screenshot_animated=True,
        screenshot_animated_format="avif",
    )
    with patch(f"{MODULE}.ensure_directory"):
        svc = MediaExtractorService(cfg)
    # Skip encoder probe for unit tests.
    svc._animated_encoder_ok["libsvtav1"] = True
    svc._animated_encoder_ok["libwebp_anim"] = True
    return svc


@pytest.fixture
def animated_webp_service(test_config):
    """Create a MediaExtractorService with animated WebP screenshots enabled."""
    cfg = dataclasses.replace(
        test_config,
        screenshot_animated=True,
        screenshot_animated_format="webp",
    )
    with patch(f"{MODULE}.ensure_directory"):
        svc = MediaExtractorService(cfg)
    svc._animated_encoder_ok["libsvtav1"] = True
    svc._animated_encoder_ok["libwebp_anim"] = True
    return svc


@pytest.fixture
def video_file(tmp_path):
    """Provide a fake video file path."""
    return tmp_path / "episode_01.mkv"


class TestExtractMedia:
    """Tests for extract_media method."""

    def test_success_both_screenshot_and_audio(self, service, video_file, make_tokenized_word, test_config):
        """Should return MediaData with both paths when both extractions succeed."""
        word = make_tokenized_word(lemma="食べる", start_time=1.0, duration=2.0)

        with (
            patch.object(service, "_extract_screenshot", return_value=True),
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(video_file, word)

        assert result.screenshot_path is not None
        assert result.audio_path is not None
        assert result.screenshot_filename is not None
        assert result.audio_filename is not None

    def test_screenshot_only_when_audio_fails(self, service, video_file, make_tokenized_word):
        """Should return screenshot path only when audio extraction fails."""
        word = make_tokenized_word()

        with (
            patch.object(service, "_extract_screenshot", return_value=True),
            patch.object(service, "_extract_audio", return_value=False),
        ):
            result = service.extract_media(video_file, word)

        assert result.screenshot_path is not None
        assert result.screenshot_filename is not None
        assert result.audio_path is None
        assert result.audio_filename is None

    def test_audio_only_when_screenshot_fails(self, service, video_file, make_tokenized_word):
        """Should return audio path only when screenshot extraction fails."""
        word = make_tokenized_word()

        with (
            patch.object(service, "_extract_screenshot", return_value=False),
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(video_file, word)

        assert result.screenshot_path is None
        assert result.screenshot_filename is None
        assert result.audio_path is not None
        assert result.audio_filename is not None

    def test_correct_filename_generation(self, service, video_file, make_tokenized_word):
        """Should generate filenames as {safe_lemma}_{timestamp_ms}.ext."""
        word = make_tokenized_word(lemma="食べる", start_time=1.5, duration=2.0)

        with (
            patch.object(service, "_extract_screenshot", return_value=True),
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(video_file, word)

        # 1.5 * 1000 = 1500
        assert result.screenshot_filename == "食べる_1500.jpg"
        assert result.audio_filename == "食べる_1500.mp3"

    def test_handles_unsafe_characters_in_lemma(self, service, video_file, make_tokenized_word):
        """Should sanitize filenames by replacing unsafe characters."""
        word = make_tokenized_word(lemma='te<st>:wo"rd', start_time=2.0, duration=1.0)

        with (
            patch.object(service, "_extract_screenshot", return_value=True),
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(video_file, word)

        # safe_filename replaces <, >, :, " with underscores
        assert result.screenshot_filename == "te_st__wo_rd_2000.jpg"
        assert result.audio_filename == "te_st__wo_rd_2000.mp3"


class TestExtractScreenshot:
    """Tests for _extract_screenshot method."""

    def test_correct_ffmpeg_args(self, service, video_file, tmp_path):
        """Should pass correct arguments to ffmpeg."""
        output_path = tmp_path / "output.jpg"
        start_time = 5.0
        duration = 4.0
        # screenshot_time = 5.0 + min(1.0, 4.0/2) = 5.0 + 1.0 = 6.0
        expected_time = 6.0

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            service._extract_screenshot(video_file, start_time, duration, output_path)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert cmd[cmd.index("-ss") + 1] == str(expected_time)
        assert cmd[cmd.index("-i") + 1] == str(video_file)
        assert "-frames:v" in cmd
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert "-q:v" in cmd
        assert cmd[cmd.index("-q:v") + 1] == "2"
        assert cmd[-1] == str(output_path)

    def test_screenshot_time_uses_half_duration_when_offset_larger(self, service, video_file, tmp_path):
        """When screenshot_offset > duration/2, should use duration/2."""
        output_path = tmp_path / "output.jpg"
        # config.screenshot_offset = 1.0, duration/2 = 0.5
        # screenshot_time = 3.0 + min(1.0, 0.5) = 3.5
        start_time = 3.0
        duration = 1.0

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            service._extract_screenshot(video_file, start_time, duration, output_path)

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == str(3.5)

    def test_returns_true_on_success(self, service, video_file, tmp_path):
        """Should return True when ffmpeg exits 0 and output file exists."""
        output_path = tmp_path / "output.jpg"
        output_path.write_bytes(b"\xff\xd8fake-jpeg")

        mock_proc = _popen_mock()

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is True

    def test_returns_false_on_nonzero_exit(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits with non-zero code."""
        output_path = tmp_path / "output.jpg"

        mock_proc = _popen_mock(returncode=1, stderr="error output")

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_subprocess_error(self, service, video_file, tmp_path):
        """Should return False when subprocess raises SubprocessError."""
        output_path = tmp_path / "output.jpg"

        with patch(
            f"{MODULE}.subprocess.Popen",
            side_effect=subprocess.SubprocessError("process failed"),
        ):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_timeout(self, service, video_file, tmp_path):
        """Timeout must kill + reap the process and return False (old subprocess.run semantics)."""
        output_path = tmp_path / "output.jpg"

        mock_proc = MagicMock()
        # First communicate times out; the post-kill reaping communicate succeeds.
        mock_proc.communicate.side_effect = [subprocess.TimeoutExpired("ffmpeg", 30), ("", "")]

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch(f"{MODULE}.subprocess.run") as mock_run,
        ):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False
        mock_proc.kill.assert_called_once()
        # Reaped after the kill — no zombie left behind.
        assert mock_proc.communicate.call_count == 2
        mock_run.assert_not_called()

    def test_returns_false_when_output_missing_despite_success(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits 0 but output file does not exist."""
        output_path = tmp_path / "nonexistent.jpg"

        mock_proc = _popen_mock()

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        # output_path does not exist on disk, so result should be False
        assert result is False


class TestAnimatedScreenshot:
    """Tests for animated screenshot extraction (AVIF / WebP)."""

    def test_filename_uses_avif_when_animated(self, animated_avif_service, video_file, make_tokenized_word):
        """Filename extension should be .avif when animated+avif is configured."""
        word = make_tokenized_word(lemma="食べる", start_time=1.5, duration=2.0)
        with (
            patch.object(animated_avif_service, "_extract_screenshot", return_value=True),
            patch.object(animated_avif_service, "_extract_audio", return_value=True),
        ):
            result = animated_avif_service.extract_media(video_file, word)

        assert result.screenshot_filename == "食べる_1500.avif"
        assert result.audio_filename == "食べる_1500.mp3"

    def test_filename_uses_webp_when_format_webp(self, animated_webp_service, video_file, make_tokenized_word):
        """Filename extension should be .webp when animated+webp is configured."""
        word = make_tokenized_word(lemma="飲む", start_time=2.0, duration=1.5)
        with (
            patch.object(animated_webp_service, "_extract_screenshot", return_value=True),
            patch.object(animated_webp_service, "_extract_audio", return_value=True),
        ):
            result = animated_webp_service.extract_media(video_file, word)

        assert result.screenshot_filename == "飲む_2000.webp"

    def test_animated_config_dispatches_to_animated_path(self, animated_avif_service, video_file, tmp_path):
        """_extract_screenshot must call animated impl when toggle is on."""
        output_path = tmp_path / "clip.avif"
        with (
            patch.object(animated_avif_service, "_extract_animated_screenshot", return_value=True) as animated,
            patch.object(animated_avif_service, "_extract_static_screenshot", return_value=True) as static,
        ):
            animated_avif_service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        animated.assert_called_once()
        static.assert_not_called()

    def test_static_config_dispatches_to_static_path(self, service, video_file, tmp_path):
        """_extract_screenshot must call static impl when toggle is off (default)."""
        output_path = tmp_path / "frame.jpg"
        with (
            patch.object(service, "_extract_animated_screenshot", return_value=True) as animated,
            patch.object(service, "_extract_static_screenshot", return_value=True) as static,
        ):
            service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        static.assert_called_once()
        animated.assert_not_called()

    @pytest.mark.parametrize(
        "quality,expected_crf",
        [(0, 63), (100, 0), (30, 44), (50, 32), (75, 16)],
    )
    def test_quality_to_avif_crf_mapping(self, quality, expected_crf):
        """0-100 user quality (higher better) should map to AVIF CRF 0-63 (lower better)."""
        assert MediaExtractorService._quality_to_avif_crf(quality) == expected_crf

    def test_clip_duration_capped_by_word_duration(self, animated_avif_service, video_file, tmp_path):
        """When configured clip duration exceeds word duration, ffmpeg -t should be word duration."""
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        # config clip = 2.0s, word duration = 1.0s → expect -t 1.0
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=1.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(1.0)

    def test_clip_duration_floor_for_very_short_subtitles(self, animated_avif_service, video_file, tmp_path):
        """Floor very-short subtitles to 0.5s to avoid 0-frame clips."""
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=0.1, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(0.5)

    def test_match_audio_uses_padded_range_when_enabled(self, animated_avif_service, video_file, tmp_path):
        """match_audio=True shifts -ss back by padding and extends -t by 2*padding."""
        cfg = dataclasses.replace(
            animated_avif_service.config,
            screenshot_animated_match_audio=True,
            audio_padding=0.3,
        )
        animated_avif_service.config = cfg
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=1.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == str(4.7)
        assert cmd[cmd.index("-t") + 1] == str(1.6)

    def test_match_audio_clamps_start_to_zero(self, animated_avif_service, video_file, tmp_path):
        """match_audio=True must not produce a negative -ss when padding exceeds start_time."""
        cfg = dataclasses.replace(
            animated_avif_service.config,
            screenshot_animated_match_audio=True,
            audio_padding=0.3,
        )
        animated_avif_service.config = cfg
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=0.1, duration=1.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == str(0.0)

    def test_match_audio_overrides_configured_duration(self, animated_avif_service, video_file, tmp_path):
        """match_audio=True bypasses the configured clip_duration cap."""
        cfg = dataclasses.replace(
            animated_avif_service.config,
            screenshot_animated_match_audio=True,
            audio_padding=0.3,
            screenshot_animated_clip_duration=2.0,
        )
        animated_avif_service.config = cfg
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=5.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(5.6)

    def test_avif_command_shape(self, animated_avif_service, video_file, tmp_path):
        """AVIF ffmpeg command must include libsvtav1, CRF, loop, scale filter."""
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=2.0, duration=3.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-an" in cmd
        assert "-sn" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "libsvtav1"
        assert "-crf" in cmd
        # default quality 30 → CRF 44
        assert cmd[cmd.index("-crf") + 1] == "44"
        assert cmd[cmd.index("-loop") + 1] == "0"
        # default fps 20, height 720
        vf = cmd[cmd.index("-vf") + 1]
        assert "fps=20" in vf
        assert "scale=-2:720" in vf
        assert cmd[-1] == str(output_path)

    def test_webp_command_shape(self, animated_webp_service, video_file, tmp_path):
        """WebP ffmpeg command must include libwebp_anim and -quality."""
        output_path = tmp_path / "clip.webp"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_webp_service._extract_animated_screenshot(
                video_file, start_time=2.0, duration=3.0, output_path=output_path
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "libwebp_anim"
        assert "-quality" in cmd
        # default quality 30 maps through as-is for webp
        assert cmd[cmd.index("-quality") + 1] == "30"
        assert cmd[cmd.index("-loop") + 1] == "0"

    def test_returns_false_when_encoder_missing(self, animated_avif_service, video_file, tmp_path):
        """Probe miss for required encoder → return False without calling ffmpeg."""
        output_path = tmp_path / "clip.avif"
        # Clear the test fixture priming so the probe runs.
        animated_avif_service._animated_encoder_ok.clear()

        encoder_probe = MagicMock()
        encoder_probe.returncode = 0
        encoder_probe.stdout = "V..... librav1e Some other encoder\n"  # no libsvtav1

        with patch(f"{MODULE}.subprocess.run", return_value=encoder_probe) as mock_run:
            result = animated_avif_service._extract_animated_screenshot(
                video_file, start_time=1.0, duration=2.0, output_path=output_path
            )

        assert result is False
        # subprocess.run called only for the probe, not for an encode pass
        assert mock_run.call_count == 1
        probe_cmd = mock_run.call_args_list[0][0][0]
        assert probe_cmd[:2] == ["ffmpeg", "-hide_banner"]
        assert "-encoders" in probe_cmd

    def test_encoder_probe_result_cached(self, animated_avif_service, video_file, tmp_path):
        """Encoder probe must run at most once across multiple calls."""
        output_path = tmp_path / "clip.avif"
        animated_avif_service._animated_encoder_ok.clear()

        # First call sees probe stdout; subsequent encode calls use cached miss.
        probe_result = MagicMock()
        probe_result.returncode = 0
        probe_result.stdout = "no relevant encoders here\n"

        with patch(f"{MODULE}.subprocess.run", return_value=probe_result) as mock_run:
            animated_avif_service._extract_animated_screenshot(video_file, 1.0, 2.0, output_path)
            animated_avif_service._extract_animated_screenshot(video_file, 1.0, 2.0, output_path)
            animated_avif_service._extract_animated_screenshot(video_file, 1.0, 2.0, output_path)

        # Exactly one probe call across all three extract attempts.
        assert mock_run.call_count == 1

    def test_animated_returns_false_on_ffmpeg_failure(self, animated_avif_service, video_file, tmp_path):
        """Non-zero ffmpeg exit on encode → return False."""
        output_path = tmp_path / "clip.avif"
        mock_proc = _popen_mock(returncode=1, stderr="encode failed")

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = animated_avif_service._extract_animated_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_unrecognized_animated_format_skips_word_without_spawn(
        self, test_config, video_file, make_tokenized_word, recording_progress
    ):
        """screenshot_animated_format='gif' (unsupported) → word skipped, no ffmpeg spawn, no raise."""
        cfg = dataclasses.replace(test_config, screenshot_animated=True, screenshot_animated_format="gif")
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        with (
            patch.object(svc, "_extract_audio", return_value=False),
            patch(f"{MODULE}.subprocess.Popen") as mock_popen,
            patch(f"{MODULE}.subprocess.run") as mock_run,
        ):
            result = svc.extract_media_batch(video_file, words, recording_progress)

        # The word is skipped (no screenshot), nothing is spawned, nothing raises.
        # OVH-043: a dropped word now emits on_error (non-fatal; run completes normally).
        assert result == []
        mock_popen.assert_not_called()
        mock_run.assert_not_called()
        assert len(recording_progress.errors) == 1
        assert recording_progress.errors[0][0] == "食べる"
        assert "media extraction failed" in recording_progress.errors[0][1]
        assert recording_progress.completes == 1


class TestExtractAudio:
    """Tests for _extract_audio method."""

    def test_padding_calculation(self, service, video_file, tmp_path):
        """Should apply audio padding: start = start - 0.3, duration = dur + 0.6."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 5.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        # audio_start = max(0, 5.0 - 0.3) = 4.7
        assert cmd[cmd.index("-ss") + 1] == str(4.7)
        # audio_duration = 2.0 + (0.3 * 2) = 2.6
        assert cmd[cmd.index("-t") + 1] == str(2.6)

    def test_start_clamped_to_zero(self, service, video_file, tmp_path):
        """Should clamp audio start to 0 when start_time - padding < 0."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 0.1, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        # audio_start = max(0, 0.1 - 0.3) = max(0, -0.2) = 0
        assert cmd[cmd.index("-ss") + 1] == str(0)

    def test_maps_japanese_audio_stream(self, service, video_file, tmp_path):
        """Should use -map 0:{stream_index} when Japanese audio detected."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=2),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        map_index = cmd.index("-map")
        assert cmd[map_index + 1] == "0:2"

    def test_falls_back_to_first_audio_stream(self, service, video_file, tmp_path):
        """Should use -map 0:a:0 when no Japanese audio stream detected."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        map_index = cmd.index("-map")
        assert cmd[map_index + 1] == "0:a:0"

    def test_returns_false_on_nonzero_exit(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits with non-zero code."""
        output_path = tmp_path / "output.mp3"

        mock_proc = _popen_mock(returncode=1, stderr="error output")

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_exception(self, service, video_file, tmp_path):
        """Should return False when subprocess raises an exception."""
        output_path = tmp_path / "output.mp3"

        with (
            patch(
                f"{MODULE}.subprocess.Popen",
                side_effect=OSError("ffmpeg not found"),
            ),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_uses_libmp3lame_when_format_mp3(self, service, video_file, tmp_path):
        """Should pass -acodec libmp3lame when audio_format='mp3' (Issue #18)."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        codec_index = cmd.index("-acodec")
        assert cmd[codec_index + 1] == "libmp3lame"

    def test_uses_libopus_when_format_opus(self, test_config, video_file, tmp_path):
        """Should pass -acodec libopus when audio_format='opus' (Issue #18)."""
        cfg = dataclasses.replace(test_config, audio_format="opus", audio_bitrate=64)
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        svc._animated_encoder_ok["libopus"] = True

        output_path = tmp_path / "out.opus"
        output_path.write_bytes(b"OggS")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        codec_index = cmd.index("-acodec")
        assert cmd[codec_index + 1] == "libopus"

    def test_passes_configured_bitrate(self, test_config, video_file, tmp_path):
        """Should pass -b:a {bitrate}k from config (Issue #18)."""
        cfg = dataclasses.replace(test_config, audio_bitrate=64)
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        svc._animated_encoder_ok["libmp3lame"] = True

        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        bitrate_index = cmd.index("-b:a")
        assert cmd[bitrate_index + 1] == "64k"

    def test_returns_false_when_encoder_unavailable(self, service, video_file, tmp_path):
        """Should hard-fail (return False) when the configured encoder is missing (Issue #18)."""
        output_path = tmp_path / "out.mp3"

        with (
            patch.object(service, "_check_encoder_available", return_value=False),
            patch(f"{MODULE}.subprocess.Popen") as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path)

        assert result is False
        # No ffmpeg encode call should have been made.
        mock_popen.assert_not_called()

    def test_opus_downmixes_to_stereo(self, test_config, video_file, tmp_path):
        """Should pass -ac 2 when opus to avoid libopus 5.1 channel-mapping error (Issue #18)."""
        cfg = dataclasses.replace(test_config, audio_format="opus", audio_bitrate=64)
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        svc._animated_encoder_ok["libopus"] = True

        output_path = tmp_path / "out.opus"
        output_path.write_bytes(b"OggS")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        ac_index = cmd.index("-ac")
        assert cmd[ac_index + 1] == "2"

    def test_mp3_does_not_force_stereo(self, service, video_file, tmp_path):
        """MP3 path preserves source channel layout (libmp3lame handles 5.1 natively)."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        assert "-ac" not in cmd

    def test_filename_extension_matches_format(self, test_config, video_file, make_tokenized_word, tmp_path):
        """extract_media should produce .opus filename when audio_format='opus' (Issue #18)."""
        cfg = dataclasses.replace(test_config, audio_format="opus", media_temp_folder=tmp_path)
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)

        word = make_tokenized_word(lemma="食べる", start_time=1.0, duration=2.0)

        with (
            patch.object(svc, "_extract_screenshot", return_value=True),
            patch.object(svc, "_extract_audio", return_value=True),
        ):
            result = svc.extract_media(video_file, word)

        assert result.audio_filename is not None
        assert result.audio_filename.endswith(".opus")


class TestGetJapaneseAudioStream:
    """Tests for _get_japanese_audio_stream method."""

    def _make_ffprobe_output(self, streams):
        """Helper to build ffprobe JSON output with the given stream descriptors.

        Each stream should be a dict with at least 'index' and 'language' keys.
        """
        stream_list = []
        for s in streams:
            entry = {"index": s["index"], "codec_type": "audio", "tags": {}}
            if "language" in s:
                entry["tags"]["language"] = s["language"]
            stream_list.append(entry)
        return json.dumps({"streams": stream_list})

    def test_returns_stream_index_when_japanese_found(self, service, video_file):
        """Should return the index of the Japanese audio stream."""
        ffprobe_json = self._make_ffprobe_output(
            [
                {"index": 0, "language": "eng"},
                {"index": 1, "language": "jpn"},
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ffprobe_json

        with patch(f"{DETECTOR_MODULE}.subprocess.run", return_value=mock_proc):
            result = service._get_japanese_audio_stream(video_file)

        assert result == 1

    def test_returns_none_when_no_japanese_stream(self, service, video_file):
        """Should return None when no Japanese audio stream exists."""
        ffprobe_json = self._make_ffprobe_output(
            [
                {"index": 0, "language": "eng"},
                {"index": 1, "language": "fre"},
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ffprobe_json

        with patch(f"{DETECTOR_MODULE}.subprocess.run", return_value=mock_proc):
            result = service._get_japanese_audio_stream(video_file)

        assert result is None

    def test_caches_result_for_same_video_file(self, service, video_file):
        """Should cache the result and not call ffprobe again for same file."""
        ffprobe_json = self._make_ffprobe_output(
            [
                {"index": 0, "language": "jpn"},
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ffprobe_json

        with patch(f"{DETECTOR_MODULE}.subprocess.run", return_value=mock_proc) as mock_run:
            first = service._get_japanese_audio_stream(video_file)
            second = service._get_japanese_audio_stream(video_file)

        assert first == 0
        assert second == 0
        mock_run.assert_called_once()

    def test_handles_ffprobe_failure(self, service, video_file):
        """Should return None and cache when ffprobe returns non-zero."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "error"

        with patch(f"{DETECTOR_MODULE}.subprocess.run", return_value=mock_proc):
            result = service._get_japanese_audio_stream(video_file)

        assert result is None
        assert video_file in service._audio_stream_cache

    @pytest.mark.parametrize("lang_code", ["jpn", "ja", "japanese", "jp"])
    def test_detects_all_japanese_language_codes(self, service, tmp_path, lang_code):
        """Should detect Japanese audio for all recognized language codes."""
        # Use a unique video file per parametrize invocation to avoid cache
        vid = tmp_path / f"video_{lang_code}.mkv"
        ffprobe_json = self._make_ffprobe_output(
            [
                {"index": 3, "language": lang_code},
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ffprobe_json

        with patch(f"{DETECTOR_MODULE}.subprocess.run", return_value=mock_proc):
            result = service._get_japanese_audio_stream(vid)

        assert result == 3


class TestExtractMediaBatch:
    """Tests for extract_media_batch method."""

    def test_returns_list_of_tuples(self, service, video_file, make_tokenized_word, tmp_path):
        """Should return a list of (word, media_data) tuples."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            # Create real files so has_screenshot returns True
            ss = tmp_path / f"{word.lemma}.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            from anki_miner.models import MediaData

            return MediaData(
                screenshot_path=ss,
                screenshot_filename=ss.name,
            )

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words)

        assert len(result) == 2
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in result)

    def test_filters_words_with_no_media(self, service, video_file, make_tokenized_word):
        """Should exclude words where extraction produced no media."""
        words = [
            make_tokenized_word(lemma="成功", start_time=1.0),
            make_tokenized_word(lemma="失敗", start_time=3.0),
        ]

        call_count = 0

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            nonlocal call_count
            call_count += 1
            from anki_miner.models import MediaData

            if word.lemma == "成功":
                # Create a real file so has_screenshot returns True
                p = service.config.media_temp_folder / "success.jpg"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"\xff\xd8fake")
                return MediaData(screenshot_path=p, screenshot_filename="success.jpg")
            else:
                # No media
                return MediaData()

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words)

        # Only the successful word should be in results
        assert call_count == 2
        assert len(result) == 1
        assert result[0][0].lemma == "成功"

    def test_excludes_audio_only_results(self, service, video_file, make_tokenized_word):
        """Should exclude words that have audio but no screenshot."""
        words = [
            make_tokenized_word(lemma="音声のみ", start_time=1.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            audio = service.config.media_temp_folder / "audio.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"\xff\xfbfake-mp3")
            return MediaData(audio_path=audio, audio_filename="audio.mp3")

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words)

        assert len(result) == 0

    def test_reports_progress_via_callback(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """Should call progress callbacks: on_start, on_progress, on_complete."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            ss = tmp_path / f"{word.lemma}_prog.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            service.extract_media_batch(video_file, words, recording_progress)

        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0] == (2, "Extracting media")
        assert len(recording_progress.progresses) == 2
        assert recording_progress.completes == 1

    def test_handles_exception_from_individual_extraction(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """Should catch per-word exceptions and report via on_error."""
        words = [
            make_tokenized_word(lemma="良い", start_time=1.0),
            make_tokenized_word(lemma="悪い", start_time=3.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            if word.lemma == "悪い":
                raise RuntimeError("ffmpeg exploded")
            from anki_miner.models import MediaData

            ss = tmp_path / "good.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            audio = tmp_path / "good.mp3"
            audio.write_bytes(b"\xff\xfbfake")
            return MediaData(
                screenshot_path=ss,
                screenshot_filename="good.jpg",
                audio_path=audio,
                audio_filename="good.mp3",
            )

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        # Only the successful word should be in results
        assert len(result) == 1
        assert result[0][0].lemma == "良い"
        # The error should be reported for the exception case only
        assert len(recording_progress.errors) == 1
        assert recording_progress.errors[0][0] == "悪い"
        assert "ffmpeg exploded" in recording_progress.errors[0][1]


class TestExtractCoverArt:
    """Tests for extract_cover_art (audiobook attached_pic extraction)."""

    @pytest.fixture
    def audiobook_file(self, tmp_path):
        """A fake audiobook file that exists on disk (stat must succeed)."""
        f = tmp_path / "book.m4b"
        f.write_bytes(b"fake-m4b-content")
        return f

    @staticmethod
    def _expected_cover_name(media_file: Path) -> str:
        import hashlib

        digest = hashlib.sha1(f"{media_file}:{media_file.stat().st_size}".encode(), usedforsecurity=False).hexdigest()[
            :12
        ]
        return f"audiobook_cover_{digest}.jpg"

    def test_correct_ffmpeg_args_and_deterministic_filename(self, service, audiobook_file, tmp_path):
        """Should map the attached_pic stream to a single jpg with a content-keyed name."""
        mock_proc = _popen_mock()
        expected_name = self._expected_cover_name(audiobook_file)

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            result = service.extract_cover_art(audiobook_file, tmp_path)

        assert result == tmp_path / expected_name
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert cmd[cmd.index("-i") + 1] == str(audiobook_file)
        assert cmd[cmd.index("-map") + 1] == "0:v:0"
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert cmd[-1] == str(tmp_path / expected_name)

    def test_same_file_yields_same_filename_across_calls(self, service, audiobook_file, tmp_path):
        """Deterministic name lets AnkiConnect dedup the cover across cards and runs."""
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(Path, "exists", return_value=True),
        ):
            first = service.extract_cover_art(audiobook_file, tmp_path)
            second = service.extract_cover_art(audiobook_file, tmp_path)

        assert first is not None
        assert first == second

    def test_returns_none_on_ffmpeg_failure(self, service, audiobook_file, tmp_path):
        """No attached_pic stream → ffmpeg exits non-zero → None."""
        mock_proc = _popen_mock(returncode=1, stderr="Stream map '0:v' matches no streams")

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = service.extract_cover_art(audiobook_file, tmp_path)

        assert result is None

    def test_returns_none_when_output_missing_despite_success(self, service, audiobook_file, tmp_path):
        """ffmpeg exit 0 but no file on disk → None."""
        mock_proc = _popen_mock()

        with patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc):
            result = service.extract_cover_art(audiobook_file, tmp_path)

        assert result is None

    def test_returns_none_when_media_file_missing(self, service, tmp_path):
        """Unstatable input file → None without spawning ffmpeg."""
        ghost = tmp_path / "missing.m4b"

        with patch(f"{MODULE}.subprocess.Popen") as mock_popen:
            result = service.extract_cover_art(ghost, tmp_path)

        assert result is None
        mock_popen.assert_not_called()


class TestAudioOnlyMode:
    """Tests for audio_only mode (audiobook mining, Issue #71)."""

    @pytest.fixture
    def audiobook_file(self, tmp_path):
        """A fake audiobook file that exists on disk."""
        f = tmp_path / "book.m4b"
        f.write_bytes(b"fake-m4b-content")
        return f

    def _fake_audio_extract(self, tmp_path):
        """Build an extract_media side_effect producing audio-only MediaData."""

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            audio = tmp_path / f"{word.lemma}_audio.mp3"
            audio.write_bytes(b"\xff\xfbfake-mp3")
            return MediaData(audio_path=audio, audio_filename=audio.name)

        return fake_extract

    def test_extract_media_audio_only_skips_screenshot_ffmpeg(self, service, audiobook_file, make_tokenized_word):
        """audio_only=True must never spawn a screenshot ffmpeg process."""
        word = make_tokenized_word(lemma="食べる", start_time=1.0, duration=2.0)

        with (
            patch(f"{MODULE}.subprocess.Popen") as mock_popen,
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(audiobook_file, word, audio_only=True)

        # No ffmpeg spawned at all: screenshot skipped, audio mocked out.
        mock_popen.assert_not_called()
        assert result.screenshot_path is None
        assert result.screenshot_filename is None
        assert result.audio_path is not None

    def test_default_audio_only_false_still_extracts_screenshot(self, service, audiobook_file, make_tokenized_word):
        """Regression: without audio_only, the screenshot path runs as before."""
        word = make_tokenized_word(lemma="食べる", start_time=1.0, duration=2.0)

        with (
            patch.object(service, "_extract_screenshot", return_value=True) as mock_ss,
            patch.object(service, "_extract_audio", return_value=True),
        ):
            result = service.extract_media(audiobook_file, word)

        mock_ss.assert_called_once()
        assert result.screenshot_path is not None

    def test_batch_extracts_cover_art_exactly_once(self, service, audiobook_file, make_tokenized_word, tmp_path):
        """Cover art is per-book, not per-word: one extraction per batch."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
            make_tokenized_word(lemma="読む", start_time=5.0),
        ]
        cover = tmp_path / "audiobook_cover_abc123def456.jpg"
        cover.write_bytes(b"\xff\xd8fake-jpeg")

        with (
            patch.object(service, "extract_cover_art", return_value=cover) as mock_cover,
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)),
        ):
            result = service.extract_media_batch(audiobook_file, words, audio_only=True)

        mock_cover.assert_called_once()
        assert len(result) == 3

    def test_batch_assigns_shared_cover_to_every_word(self, service, audiobook_file, make_tokenized_word, tmp_path):
        """Every word's MediaData carries the same cover path/filename."""
        words = [
            make_tokenized_word(lemma="食べる", start_time=1.0),
            make_tokenized_word(lemma="飲む", start_time=3.0),
        ]
        cover = tmp_path / "audiobook_cover_abc123def456.jpg"
        cover.write_bytes(b"\xff\xd8fake-jpeg")

        with (
            patch.object(service, "extract_cover_art", return_value=cover),
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)),
        ):
            result = service.extract_media_batch(audiobook_file, words, audio_only=True)

        assert len(result) == 2
        for _, media in result:
            assert media.screenshot_path == cover
            assert media.screenshot_filename == cover.name

    def test_batch_filter_keeps_has_audio_drops_audio_failed(
        self, service, audiobook_file, make_tokenized_word, tmp_path
    ):
        """audio_only filter keys on has_audio: audio-failed words are dropped."""
        words = [
            make_tokenized_word(lemma="成功", start_time=1.0),
            make_tokenized_word(lemma="失敗", start_time=3.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            if word.lemma == "成功":
                audio = tmp_path / "ok.mp3"
                audio.write_bytes(b"\xff\xfbfake-mp3")
                return MediaData(audio_path=audio, audio_filename="ok.mp3")
            return MediaData()  # audio extraction failed

        with (
            patch.object(service, "extract_cover_art", return_value=None),
            patch.object(service, "extract_media", side_effect=fake_extract),
        ):
            result = service.extract_media_batch(audiobook_file, words, audio_only=True)

        assert len(result) == 1
        assert result[0][0].lemma == "成功"

    def test_missing_cover_art_never_excludes_words(self, service, audiobook_file, make_tokenized_word, tmp_path):
        """No embedded cover → screenshot fields stay None, words still kept."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        with (
            patch.object(service, "extract_cover_art", return_value=None),
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)),
        ):
            result = service.extract_media_batch(audiobook_file, words, audio_only=True)

        assert len(result) == 1
        _, media = result[0]
        assert media.screenshot_path is None
        assert media.screenshot_filename is None
        assert media.has_audio

    def test_batch_forwards_audio_only_to_extract_media(self, service, audiobook_file, make_tokenized_word, tmp_path):
        """extract_media_batch must pass audio_only=True to every extract_media call."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        with (
            patch.object(service, "extract_cover_art", return_value=None),
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)) as mock_em,
        ):
            service.extract_media_batch(audiobook_file, words, audio_only=True)

        mock_em.assert_called_once()
        _, kwargs = mock_em.call_args
        assert kwargs.get("audio_only") is True

    def test_batch_forwards_proc_registry_to_extract_cover_art(
        self, service, audiobook_file, make_tokenized_word, tmp_path
    ):
        """Cover extraction must join the batch's cancel registry so kill_all reaches it."""
        from anki_miner.services.media_extractor import _FfmpegProcRegistry

        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        with (
            patch.object(service, "extract_cover_art", return_value=None) as mock_cover,
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)),
        ):
            service.extract_media_batch(audiobook_file, words, audio_only=True)

        mock_cover.assert_called_once()
        _, kwargs = mock_cover.call_args
        assert isinstance(kwargs.get("proc_registry"), _FfmpegProcRegistry)

    def test_precancelled_batch_skips_cover_art_and_returns_empty(
        self, service, audiobook_file, make_tokenized_word, tmp_path
    ):
        """Cancellation set before the batch starts must not run cover extraction."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        with (
            patch.object(service, "extract_cover_art") as mock_cover,
            patch.object(service, "extract_media", side_effect=self._fake_audio_extract(tmp_path)),
        ):
            result = service.extract_media_batch(audiobook_file, words, cancelled_check=lambda: True, audio_only=True)

        mock_cover.assert_not_called()
        assert result == []

    def test_default_batch_does_not_extract_cover_art(self, service, video_file, make_tokenized_word, tmp_path):
        """Regression: audio_only=False keeps the screenshot filter and skips cover art."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            ss = tmp_path / "shot.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename="shot.jpg")

        with (
            patch.object(service, "extract_cover_art") as mock_cover,
            patch.object(service, "extract_media", side_effect=fake_extract),
        ):
            result = service.extract_media_batch(video_file, words)

        mock_cover.assert_not_called()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Helper: import AudioStream from public API
# ---------------------------------------------------------------------------
from anki_miner.utils import AudioStream  # noqa: E402


def _make_audio_stream(audio_index: int, global_index: int) -> AudioStream:
    return AudioStream(
        global_index=global_index,
        audio_index=audio_index,
        language_tag=None,
        title_tag=None,
        codec="aac",
        channels=2,
        is_default=(audio_index == 0),
    )


class TestAudioTrackOverride:
    """Tests for the audio_track_override feature in MediaExtractorService."""

    # ------------------------------------------------------------------
    # 1. Override produces the correct -map arg
    # ------------------------------------------------------------------
    def test_override_produces_correct_map_arg(self, service, video_file, tmp_path):
        """audio_track_override=1 should map to the global_index of audio_index=1."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        streams = [
            _make_audio_stream(audio_index=0, global_index=1),
            _make_audio_stream(audio_index=1, global_index=2),
            _make_audio_stream(audio_index=2, global_index=3),
        ]

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.list_audio_streams", return_value=streams) as mock_list,
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=1)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:2"
        mock_list.assert_called_once_with(video_file, ffprobe_cmd="ffprobe")

    # ------------------------------------------------------------------
    # 2. Override=None preserves JP auto path; list_audio_streams NOT called
    # ------------------------------------------------------------------
    def test_override_none_uses_jp_auto_path(self, service, video_file, tmp_path):
        """When override is None, JP auto-detect is used and list_audio_streams is not called."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp,
            patch(f"{MODULE}.list_audio_streams") as mock_list,
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            from anki_miner.utils.audio_track_detector import JapaneseAudioStream

            mock_find_jp.return_value = JapaneseAudioStream(global_index=2, audio_index=0, language_tag="jpn")

            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=None)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:2"
        mock_list.assert_not_called()

    # ------------------------------------------------------------------
    # 3. Override beats auto-detection; find_japanese_audio_stream NOT called
    # ------------------------------------------------------------------
    def test_override_beats_auto_detection(self, service, video_file, tmp_path):
        """Override wins over JP auto-detect; find_japanese_audio_stream must not be called."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        streams = [
            _make_audio_stream(audio_index=0, global_index=1),
            _make_audio_stream(audio_index=1, global_index=5),
        ]

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp,
            patch(f"{MODULE}.list_audio_streams", return_value=streams),
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=1)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:5"
        mock_find_jp.assert_not_called()

    # ------------------------------------------------------------------
    # 4. Override out of range falls back to JP auto; warning logged
    # ------------------------------------------------------------------
    def test_override_out_of_range_falls_back(self, service, video_file, tmp_path, caplog):
        """When override audio_index doesn't exist, fall back to JP inline filter and log a warning.

        find_japanese_audio_stream must NOT be called; the fallback reuses the already-cached
        stream list and filters by JAPANESE_LANGUAGE_CODES directly.
        """
        import logging

        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        # Stream has a Japanese language tag so the inline JP filter can pick it up.
        from anki_miner.utils import AudioStream

        jp_stream = AudioStream(
            global_index=2,
            audio_index=0,
            language_tag="jpn",
            title_tag=None,
            codec="aac",
            channels=2,
            is_default=True,
        )
        streams = [jp_stream]

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.list_audio_streams", return_value=streams),
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp,
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            caplog.at_level(logging.WARNING, logger="anki_miner.services.media_extractor"),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=99)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:2"
        assert any("audio_track_override=99" in r.message for r in caplog.records)
        # Fallback uses cached stream list inline — find_japanese_audio_stream must not be called.
        mock_find_jp.assert_not_called()

    # ------------------------------------------------------------------
    # 5. Override=0 is a valid first-track index, not a falsy sentinel
    # ------------------------------------------------------------------
    def test_override_zero_is_valid_first_audio_track(self, service, video_file, tmp_path):
        """audio_track_override=0 is a valid index (first audio track), not a falsy 'no override' value.

        Guards against regressions where someone writes `if not audio_track_override:`
        instead of `if audio_track_override is None:`.
        """
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        # One stream: audio_index=0 maps to global_index=3 (e.g. video + sub tracks before it).
        streams = [_make_audio_stream(audio_index=0, global_index=3)]

        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.list_audio_streams", return_value=streams),
            patch(f"{MODULE}.find_japanese_audio_stream") as mock_find_jp,
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=0)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:3"
        # Override is set (even though it's 0) — JP auto-detect must not be triggered.
        mock_find_jp.assert_not_called()

    # ------------------------------------------------------------------
    # 7. Both absent → 0:a:0 (existing behavior preserved)
    # ------------------------------------------------------------------
    def test_both_absent_uses_first_stream(self, service, video_file, tmp_path):
        """When override is None and JP auto-detect returns None, use 0:a:0."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        mock_proc = _popen_mock()

        with (
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path, audio_track_override=None)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        map_idx = cmd.index("-map")
        assert cmd[map_idx + 1] == "0:a:0"

    # ------------------------------------------------------------------
    # 8. Cache hit: list_audio_streams called only once per file
    # ------------------------------------------------------------------
    def test_stream_list_cached_across_calls(self, service, video_file):
        """_list_audio_streams_cached must call list_audio_streams only once per video file."""
        streams = [_make_audio_stream(audio_index=0, global_index=1)]

        with patch(f"{MODULE}.list_audio_streams", return_value=streams) as mock_list:
            first = service._list_audio_streams_cached(video_file)
            second = service._list_audio_streams_cached(video_file)

        assert first == streams
        assert second == streams
        mock_list.assert_called_once_with(video_file, ffprobe_cmd="ffprobe")

    # ------------------------------------------------------------------
    # 8b. invalidate_audio_stream_cache forces re-probe on next call
    # ------------------------------------------------------------------
    def test_invalidate_audio_stream_cache_forces_reprobe(self, service, video_file):
        """After invalidation, the next _list_audio_streams_cached must re-probe.

        Guards against cross-run staleness: the cache is populated within a
        process_episode run (perf win) but the orchestrator calls
        invalidate_audio_stream_cache at the start of each run so a file
        replaced on disk between runs cannot leave the resolver matching
        against stale ffprobe output.
        """
        streams = [_make_audio_stream(audio_index=0, global_index=1)]

        with patch(f"{MODULE}.list_audio_streams", return_value=streams) as mock_list:
            service._list_audio_streams_cached(video_file)
            service._list_audio_streams_cached(video_file)
            assert mock_list.call_count == 1

            service.invalidate_audio_stream_cache(video_file)

            service._list_audio_streams_cached(video_file)
            assert mock_list.call_count == 2

    def test_invalidate_audio_stream_cache_clears_all_when_path_none(self, service, video_file, tmp_path):
        """Passing None clears every cached entry across all files."""
        other_file = tmp_path / "other.mkv"
        other_file.write_bytes(b"fake")
        streams = [_make_audio_stream(audio_index=0, global_index=1)]

        with patch(f"{MODULE}.list_audio_streams", return_value=streams) as mock_list:
            service._list_audio_streams_cached(video_file)
            service._list_audio_streams_cached(other_file)
            assert mock_list.call_count == 2

            service.invalidate_audio_stream_cache(None)

            service._list_audio_streams_cached(video_file)
            service._list_audio_streams_cached(other_file)
            assert mock_list.call_count == 4

    def test_invalidate_audio_stream_cache_unknown_path_is_noop(self, service, tmp_path):
        """Invalidating a file never cached should not raise."""
        ghost = tmp_path / "ghost.mkv"
        # Must not raise.
        service.invalidate_audio_stream_cache(ghost)

    # ------------------------------------------------------------------
    # 9. extract_media_batch forwards override to extract_media
    # ------------------------------------------------------------------
    def test_extract_media_batch_forwards_override(self, service, video_file, make_tokenized_word, tmp_path):
        """extract_media_batch must pass audio_track_override to every extract_media call."""
        words = [make_tokenized_word(lemma="食べる", start_time=1.0)]

        ss = tmp_path / "食べる_1000.jpg"
        ss.write_bytes(b"\xff\xd8fake")
        from anki_miner.models import MediaData

        fake_media = MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", return_value=fake_media) as mock_em:
            service.extract_media_batch(video_file, words, audio_track_override=2)

        mock_em.assert_called_once()
        _, kwargs = mock_em.call_args
        assert kwargs.get("audio_track_override") == 2


class TestFfmpegResolverWiring:
    """The media extractor routes ffmpeg/ffprobe through the resolver.

    In dev/tests (non-frozen, no override) the resolver returns the bare
    literal, so the default-config cases below assert ``cmd[0] == "ffmpeg"``.
    A config override pointing at a real file must surface at ``cmd[0]``.
    """

    @pytest.fixture(autouse=True)
    def _clear_resolver_cache(self):
        """The resolver caches by (name, override, frozen, meipass); reset per test."""
        from anki_miner.utils import ffmpeg_resolver

        ffmpeg_resolver._clear_cache()
        yield
        ffmpeg_resolver._clear_cache()

    def test_static_screenshot_uses_config_override(self, test_config, video_file, tmp_path):
        """When config.ffmpeg_location is a real file, it becomes cmd[0]."""
        fake_ffmpeg = tmp_path / "my_ffmpeg"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        cfg = dataclasses.replace(test_config, ffmpeg_location=str(fake_ffmpeg))
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)

        output_path = tmp_path / "shot.jpg"
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(Path, "exists", return_value=True),
        ):
            svc._extract_screenshot(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == str(fake_ffmpeg)

    def test_audio_uses_config_override(self, test_config, video_file, tmp_path):
        """Audio extraction command cmd[0] honours config.ffmpeg_location."""
        fake_ffmpeg = tmp_path / "my_ffmpeg"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        cfg = dataclasses.replace(test_config, ffmpeg_location=str(fake_ffmpeg))
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        svc._animated_encoder_ok["libmp3lame"] = True
        svc._animated_encoder_ok["libopus"] = True

        output_path = tmp_path / "clip.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")
        mock_proc = _popen_mock()
        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == str(fake_ffmpeg)

    def test_encoder_probe_uses_config_override(self, test_config, tmp_path):
        """_check_encoder_available probe cmd[0] honours config.ffmpeg_location."""
        fake_ffmpeg = tmp_path / "my_ffmpeg"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        cfg = dataclasses.replace(test_config, ffmpeg_location=str(fake_ffmpeg))
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "libmp3lame"
        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run:
            assert svc._check_encoder_available("libmp3lame") is True

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == str(fake_ffmpeg)

    def test_list_audio_streams_cached_passes_resolved_ffprobe(self, test_config, video_file, tmp_path):
        """_list_audio_streams_cached forwards ffprobe_cmd=resolve_ffprobe(config)."""
        fake_ffprobe = tmp_path / "my_ffprobe"
        fake_ffprobe.write_text("#!/bin/sh\n")
        cfg = dataclasses.replace(test_config, ffprobe_location=str(fake_ffprobe))
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)

        with patch(f"{MODULE}.list_audio_streams", return_value=[]) as mock_list:
            svc._list_audio_streams_cached(video_file)

        _, kwargs = mock_list.call_args
        assert kwargs.get("ffprobe_cmd") == str(fake_ffprobe)

    def test_get_japanese_audio_stream_passes_resolved_ffprobe(self, test_config, video_file, tmp_path):
        """_get_japanese_audio_stream forwards ffprobe_cmd=resolve_ffprobe(config)."""
        fake_ffprobe = tmp_path / "my_ffprobe"
        fake_ffprobe.write_text("#!/bin/sh\n")
        cfg = dataclasses.replace(test_config, ffprobe_location=str(fake_ffprobe))
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)

        with patch(f"{MODULE}.find_japanese_audio_stream", return_value=None) as mock_find:
            svc._get_japanese_audio_stream(video_file)

        _, kwargs = mock_find.call_args
        assert kwargs.get("ffprobe_cmd") == str(fake_ffprobe)


class TestOVH043DroppedWordOnError:
    """OVH-043: words dropped by extract_media_batch surface via on_error (non-fatal)."""

    def test_dropped_word_triggers_on_error(self, service, video_file, make_tokenized_word, recording_progress):
        """A word with no screenshot (keep=False) must call on_error with a failure message."""
        words = [make_tokenized_word(lemma="失敗", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            # No screenshot, no audio → keep=False in default mode
            return MediaData()

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        assert result == []
        assert len(recording_progress.errors) == 1
        lemma, msg = recording_progress.errors[0]
        assert lemma == "失敗"
        assert "media extraction failed" in msg

    def test_successful_word_does_not_trigger_on_error(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """A word whose screenshot succeeds must NOT call on_error."""
        words = [make_tokenized_word(lemma="成功", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            ss = tmp_path / "shot.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            # Screenshot succeeded, audio also succeeded
            audio = tmp_path / "audio.mp3"
            audio.write_bytes(b"\xff\xfbfake")
            return MediaData(
                screenshot_path=ss,
                screenshot_filename=ss.name,
                audio_path=audio,
                audio_filename=audio.name,
            )

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        assert len(result) == 1
        assert recording_progress.errors == []

    def test_on_error_is_non_fatal_run_continues(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """on_error for a dropped word must not abort the batch; subsequent words are processed."""
        words = [
            make_tokenized_word(lemma="失敗", start_time=1.0),
            make_tokenized_word(lemma="成功", start_time=2.0),
        ]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            if word.lemma == "失敗":
                return MediaData()
            ss = tmp_path / "shot.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            audio = tmp_path / "audio.mp3"
            audio.write_bytes(b"\xff\xfbfake")
            return MediaData(
                screenshot_path=ss,
                screenshot_filename=ss.name,
                audio_path=audio,
                audio_filename=audio.name,
            )

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        # One word kept, one dropped
        assert len(result) == 1
        assert result[0][0].lemma == "成功"
        # Dropped word emitted an error but run continued
        assert len(recording_progress.errors) == 1
        assert recording_progress.errors[0][0] == "失敗"
        assert recording_progress.completes == 1

    def test_audio_only_dropped_word_triggers_on_error(
        self, service, video_file, make_tokenized_word, recording_progress
    ):
        """In audio_only mode a dropped word (no audio) still emits on_error."""
        words = [make_tokenized_word(lemma="失敗音声", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            return MediaData()  # no audio → keep=False in audio_only mode

        with (
            patch.object(service, "extract_cover_art", return_value=None),
            patch.object(service, "extract_media", side_effect=fake_extract),
        ):
            result = service.extract_media_batch(video_file, words, recording_progress, audio_only=True)

        assert result == []
        assert len(recording_progress.errors) == 1
        assert recording_progress.errors[0][0] == "失敗音声"


class TestOVH044AudioFailedOnError:
    """OVH-044: screenshot OK but audio failed in default mode → on_error emitted, card kept."""

    def test_screenshot_ok_audio_failed_emits_on_error(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """Default mode: screenshot succeeded, audio failed → on_error with 'audio extraction failed'."""
        words = [make_tokenized_word(lemma="音声なし", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            ss = tmp_path / "shot.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            # Screenshot succeeded, audio failed (no audio_path/audio_filename)
            return MediaData(screenshot_path=ss, screenshot_filename=ss.name)

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        # Card is KEPT (screenshot-based keep decision unchanged)
        assert len(result) == 1
        assert result[0][0].lemma == "音声なし"
        # Error is surfaced to the GUI
        assert len(recording_progress.errors) == 1
        lemma, msg = recording_progress.errors[0]
        assert lemma == "音声なし"
        assert "audio extraction failed" in msg

    def test_screenshot_and_audio_ok_no_error(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """Default mode: both screenshot and audio succeeded → no on_error call."""
        words = [make_tokenized_word(lemma="両方成功", start_time=1.0)]

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            ss = tmp_path / "shot.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            audio = tmp_path / "audio.mp3"
            audio.write_bytes(b"\xff\xfbfake")
            return MediaData(
                screenshot_path=ss,
                screenshot_filename=ss.name,
                audio_path=audio,
                audio_filename=audio.name,
            )

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        assert len(result) == 1
        assert recording_progress.errors == []

    def test_audio_only_mode_unaffected_by_ovh044(
        self, service, video_file, make_tokenized_word, recording_progress, tmp_path
    ):
        """audio_only mode: a kept word (audio present, no screenshot) must NOT emit OVH-044 error.

        In audio_only mode the keep decision keys on has_audio, and there is no
        per-word screenshot at all, so the 'has screenshot but not audio' condition
        cannot apply.  This guards against the OVH-044 branch accidentally firing
        in audio_only mode.
        """
        words = [make_tokenized_word(lemma="音声のみ", start_time=1.0)]
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"\xff\xd8fake-cover")

        def fake_extract(vf, word, temp_folder=None, **kwargs):
            from anki_miner.models import MediaData

            audio = tmp_path / "audio.mp3"
            audio.write_bytes(b"\xff\xfbfake")
            # audio_only=True: audio present, screenshot absent — this is normal
            return MediaData(audio_path=audio, audio_filename=audio.name)

        with (
            patch.object(service, "extract_cover_art", return_value=cover),
            patch.object(service, "extract_media", side_effect=fake_extract),
        ):
            result = service.extract_media_batch(video_file, words, recording_progress, audio_only=True)

        assert len(result) == 1
        # No spurious error for missing screenshot in audio_only mode
        assert recording_progress.errors == []


# ---------------------------------------------------------------------------
# Helper: write a minimal valid 32-bit float PCM WAV to a path
# ---------------------------------------------------------------------------
def _write_f32_wav(path: Path, num_samples: int, sample_rate: int = 16000) -> None:
    """Write a mono 32-bit float PCM WAV file with *num_samples* samples of silence."""
    import struct

    # Write silence as raw f32le bytes — no numpy required for the helper itself.
    raw = struct.pack(f"<{num_samples}f", *([0.0] * num_samples))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)  # 4 bytes = 32-bit float
        wf.setframerate(sample_rate)
        wf.writeframes(raw)


def _write_min_wav(path) -> None:
    """Write a 1-frame 16 kHz mono WAV so the zero-frame guard accepts it.

    extract_full_audio rejects a frameless WAV (no decodable audio); arg/flow
    tests that mock ffmpeg must therefore leave a non-empty file on disk.
    """
    import struct
    import wave as _wave

    with _wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)  # pcm_f32le is 4 bytes/sample
        wf.setframerate(16000)
        wf.writeframes(struct.pack("<f", 0.0))


class TestExtractFullAudio:
    """Tests for MediaExtractorService.extract_full_audio."""

    def test_correct_ffmpeg_args_jp_track(self, service, video_file, tmp_path):
        """When JP track detected, command must use -map 0:N and pcm_f32le, -ar 16000, -ac 1."""
        out_wav = tmp_path / "full.wav"
        _write_min_wav(out_wav)
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=3),
            patch.object(Path, "exists", return_value=True),
        ):
            result = service.extract_full_audio(video_file, out_wav)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert cmd[cmd.index("-i") + 1] == str(video_file)
        assert "-map" in cmd
        assert cmd[cmd.index("-map") + 1] == "0:3"
        assert "-vn" in cmd
        assert "-ar" in cmd
        assert cmd[cmd.index("-ar") + 1] == "16000"
        assert "-ac" in cmd
        assert cmd[cmd.index("-ac") + 1] == "1"
        assert "-c:a" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "pcm_f32le"
        assert cmd[-1] == str(out_wav)

    def test_falls_back_to_0a0_when_no_jp_track(self, service, video_file, tmp_path):
        """When no JP track detected and no override, command must use -map 0:a:0."""
        out_wav = tmp_path / "full.wav"
        _write_min_wav(out_wav)
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch.object(Path, "exists", return_value=True),
        ):
            result = service.extract_full_audio(video_file, out_wav)

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-map") + 1] == "0:a:0"

    def test_track_override_respected(self, service, video_file, tmp_path):
        """track_override is an audio-index resolved via _resolve_audio_track_global_index.

        Mirrors _extract_audio semantics: the integer is an audio-stream index,
        not a raw ffprobe global stream index.  Resolution must happen through the
        shared helper so callers get identical behaviour from both methods.
        The resolved global index (5 in this stub) is what ends up in -map.
        """
        out_wav = tmp_path / "full.wav"
        _write_min_wav(out_wav)
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(service, "_resolve_audio_track_global_index", return_value=5) as mock_resolve,
            patch.object(service, "_get_japanese_audio_stream") as mock_jp,
            patch.object(Path, "exists", return_value=True),
        ):
            result = service.extract_full_audio(video_file, out_wav, track_override=2)

        assert result is True
        # Resolution must go through the shared helper, not directly to global index.
        mock_resolve.assert_called_once_with(video_file, 2)
        # _get_japanese_audio_stream must not be called directly (the helper handles it).
        mock_jp.assert_not_called()
        cmd = mock_popen.call_args[0][0]
        # The resolved global index (5) must appear in -map, not the raw audio index (2).
        assert cmd[cmd.index("-map") + 1] == "0:5"

    def test_no_encoder_probe(self, service, video_file, tmp_path):
        """extract_full_audio must NOT call _check_encoder_available (pcm_f32le is built-in)."""
        out_wav = tmp_path / "full.wav"
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch.object(service, "_check_encoder_available") as mock_probe,
            patch.object(Path, "exists", return_value=True),
        ):
            service.extract_full_audio(video_file, out_wav)

        mock_probe.assert_not_called()

    def test_returns_false_on_ffmpeg_failure(self, service, video_file, tmp_path):
        """Non-zero ffmpeg exit must return False."""
        out_wav = tmp_path / "full.wav"
        mock_proc = _popen_mock(returncode=1, stderr="no audio stream")

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service.extract_full_audio(video_file, out_wav)

        assert result is False

    def test_returns_false_when_output_missing_despite_success(self, service, video_file, tmp_path):
        """ffmpeg exit 0 but output file absent → False."""
        out_wav = tmp_path / "full.wav"
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service.extract_full_audio(video_file, out_wav)

        assert result is False

    def test_returns_false_on_zero_frame_wav(self, service, video_file, tmp_path):
        """ffmpeg exit 0 but a frameless WAV (no decodable audio) → False (C5)."""
        import wave as _wave

        out_wav = tmp_path / "full.wav"
        with _wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(4)
            wf.setframerate(16000)
            # no writeframes → 0 frames
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch.object(Path, "exists", return_value=True),
        ):
            result = service.extract_full_audio(video_file, out_wav)

        assert result is False

    def test_cancel_event_aborts_before_ffmpeg(self, service, video_file, tmp_path):
        """A pre-set cancel_event must abort without spawning ffmpeg."""
        import threading

        out_wav = tmp_path / "full.wav"
        cancel = threading.Event()
        cancel.set()

        with (
            patch(f"{MODULE}.subprocess.Popen") as mock_popen,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service.extract_full_audio(video_file, out_wav, cancel_event=cancel)

        assert result is False
        mock_popen.assert_not_called()

    def test_kill_all_not_called_on_success(self, service, video_file, tmp_path):
        """On the normal success path kill_all must NOT be called.

        The cancel-watcher thread wakes on done_event and checks that cancel
        was not set — it must not kill the registry when ffmpeg already finished
        cleanly.  This guards against the original thread-leak where the watcher
        blocked forever on cancel_event.wait(), and against a regression where
        done_event is set but the watcher unconditionally calls kill_all.
        """
        import threading

        out_wav = tmp_path / "full.wav"
        _write_min_wav(out_wav)
        cancel = threading.Event()  # never set — normal run
        mock_proc = _popen_mock()

        with (
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch.object(Path, "exists", return_value=True),
            patch(f"{MODULE}._FfmpegProcRegistry.kill_all") as mock_kill,
        ):
            result = service.extract_full_audio(video_file, out_wav, cancel_event=cancel)
            # Give the watcher thread a moment to finish so we capture any
            # spurious kill_all that fires after _run_ffmpeg returns.
            import time

            time.sleep(0.2)

        assert result is True
        mock_kill.assert_not_called()

    def test_uses_duration_scaled_timeout(self, service, video_file, tmp_path):
        """Duration-scaled timeout must be used (not the 30-60s word-clip timeout)."""
        out_wav = tmp_path / "full.wav"

        with (
            patch.object(service, "_run_ffmpeg", return_value=True) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
            patch.object(Path, "exists", return_value=True),
        ):
            service.extract_full_audio(video_file, out_wav)

        assert mock_run.called
        call_args = mock_run.call_args
        # _run_ffmpeg signature: (cmd, op_name, timeout, context="", proc_registry=None)
        # timeout is the 3rd positional arg (index 2)
        positional = call_args[0]
        kw = call_args[1]
        timeout = kw.get("timeout") if "timeout" in kw else (positional[2] if len(positional) > 2 else None)
        assert timeout is not None
        # must be substantially larger than the 30-60s word-clip timeouts
        assert timeout > 60


class TestWavToFloat32:
    """Tests for wav_to_float32 helper.

    numpy is an [asr] optional extra — tests are skipped when it is not installed.
    """

    def test_returns_correct_shape_sr_duration(self, tmp_path):
        """wav_to_float32 on a generated f32 WAV returns correct shape, sr, and duration."""
        np = pytest.importorskip("numpy")

        from anki_miner.services.media_extractor import wav_to_float32

        sample_rate = 16000
        num_samples = 32000  # 2 seconds
        wav_path = tmp_path / "test.wav"
        _write_f32_wav(wav_path, num_samples, sample_rate)

        samples, sr, duration = wav_to_float32(wav_path)

        assert isinstance(samples, np.ndarray)
        assert samples.dtype == np.float32
        assert samples.shape == (num_samples,)
        assert sr == sample_rate
        assert abs(duration - 2.0) < 1e-6

    def test_samples_in_valid_range(self, tmp_path):
        """Samples from a silent WAV must be zero (or in [-1, 1])."""
        np = pytest.importorskip("numpy")

        from anki_miner.services.media_extractor import wav_to_float32

        wav_path = tmp_path / "silent.wav"
        _write_f32_wav(wav_path, 16000, 16000)

        samples, sr, duration = wav_to_float32(wav_path)

        assert np.all(samples == 0.0)

    def test_non_silent_samples_preserved(self, tmp_path):
        """Non-zero f32 PCM samples must survive the round-trip unchanged."""
        import struct

        np = pytest.importorskip("numpy")

        from anki_miner.services.media_extractor import wav_to_float32

        sample_rate = 16000
        num_samples = 160
        # Generate a linear ramp in [-1, 1] as raw f32le bytes.
        step = 2.0 / (num_samples - 1)
        original_list = [-1.0 + i * step for i in range(num_samples)]
        raw = struct.pack(f"<{num_samples}f", *original_list)
        original = np.frombuffer(raw, dtype=np.float32)

        with wave.open(str(tmp_path / "tone.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(4)
            wf.setframerate(sample_rate)
            wf.writeframes(raw)

        samples, sr, duration = wav_to_float32(tmp_path / "tone.wav")

        np.testing.assert_array_equal(samples, original)
        assert sr == sample_rate
        assert abs(duration - num_samples / sample_rate) < 1e-6
