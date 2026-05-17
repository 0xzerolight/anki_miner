"""Tests for media_extractor module."""

import dataclasses
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.media_extractor import MediaExtractorService

MODULE = "anki_miner.services.media_extractor"


@pytest.fixture
def service(test_config):
    """Create a MediaExtractorService with ensure_directory patched out."""
    with patch(f"{MODULE}.ensure_directory"):
        svc = MediaExtractorService(test_config)
    # Skip audio encoder probe; audio tests use mocked subprocess.run.
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

    def test_success_both_screenshot_and_audio(
        self, service, video_file, make_tokenized_word, test_config
    ):
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

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            service._extract_screenshot(video_file, start_time, duration, output_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert cmd[cmd.index("-ss") + 1] == str(expected_time)
        assert cmd[cmd.index("-i") + 1] == str(video_file)
        assert "-frames:v" in cmd
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert "-q:v" in cmd
        assert cmd[cmd.index("-q:v") + 1] == "2"
        assert cmd[-1] == str(output_path)

    def test_screenshot_time_uses_half_duration_when_offset_larger(
        self, service, video_file, tmp_path
    ):
        """When screenshot_offset > duration/2, should use duration/2."""
        output_path = tmp_path / "output.jpg"
        # config.screenshot_offset = 1.0, duration/2 = 0.5
        # screenshot_time = 3.0 + min(1.0, 0.5) = 3.5
        start_time = 3.0
        duration = 1.0

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            service._extract_screenshot(video_file, start_time, duration, output_path)

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == str(3.5)

    def test_returns_true_on_success(self, service, video_file, tmp_path):
        """Should return True when ffmpeg exits 0 and output file exists."""
        output_path = tmp_path / "output.jpg"
        output_path.write_bytes(b"\xff\xd8fake-jpeg")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is True

    def test_returns_false_on_nonzero_exit(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits with non-zero code."""
        output_path = tmp_path / "output.jpg"

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"error output"

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_subprocess_error(self, service, video_file, tmp_path):
        """Should return False when subprocess raises SubprocessError."""
        output_path = tmp_path / "output.jpg"

        with patch(
            f"{MODULE}.subprocess.run",
            side_effect=subprocess.SubprocessError("process failed"),
        ):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_timeout(self, service, video_file, tmp_path):
        """Should return False when ffmpeg times out (TimeoutExpired is a SubprocessError)."""
        output_path = tmp_path / "output.jpg"

        with patch(
            f"{MODULE}.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 30),
        ):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_when_output_missing_despite_success(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits 0 but output file does not exist."""
        output_path = tmp_path / "nonexistent.jpg"

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
            result = service._extract_screenshot(video_file, 1.0, 2.0, output_path)

        # output_path does not exist on disk, so result should be False
        assert result is False


class TestAnimatedScreenshot:
    """Tests for animated screenshot extraction (AVIF / WebP)."""

    def test_filename_uses_avif_when_animated(
        self, animated_avif_service, video_file, make_tokenized_word
    ):
        """Filename extension should be .avif when animated+avif is configured."""
        word = make_tokenized_word(lemma="食べる", start_time=1.5, duration=2.0)
        with (
            patch.object(animated_avif_service, "_extract_screenshot", return_value=True),
            patch.object(animated_avif_service, "_extract_audio", return_value=True),
        ):
            result = animated_avif_service.extract_media(video_file, word)

        assert result.screenshot_filename == "食べる_1500.avif"
        assert result.audio_filename == "食べる_1500.mp3"

    def test_filename_uses_webp_when_format_webp(
        self, animated_webp_service, video_file, make_tokenized_word
    ):
        """Filename extension should be .webp when animated+webp is configured."""
        word = make_tokenized_word(lemma="飲む", start_time=2.0, duration=1.5)
        with (
            patch.object(animated_webp_service, "_extract_screenshot", return_value=True),
            patch.object(animated_webp_service, "_extract_audio", return_value=True),
        ):
            result = animated_webp_service.extract_media(video_file, word)

        assert result.screenshot_filename == "飲む_2000.webp"

    def test_animated_config_dispatches_to_animated_path(
        self, animated_avif_service, video_file, tmp_path
    ):
        """_extract_screenshot must call animated impl when toggle is on."""
        output_path = tmp_path / "clip.avif"
        with (
            patch.object(
                animated_avif_service, "_extract_animated_screenshot", return_value=True
            ) as animated,
            patch.object(
                animated_avif_service, "_extract_static_screenshot", return_value=True
            ) as static,
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

    def test_clip_duration_capped_by_word_duration(
        self, animated_avif_service, video_file, tmp_path
    ):
        """When configured clip duration exceeds word duration, ffmpeg -t should be word duration."""
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        # config clip = 2.0s, word duration = 1.0s → expect -t 1.0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=1.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(1.0)

    def test_clip_duration_floor_for_very_short_subtitles(
        self, animated_avif_service, video_file, tmp_path
    ):
        """Floor very-short subtitles to 0.5s to avoid 0-frame clips."""
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=0.1, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(0.5)

    def test_match_audio_uses_padded_range_when_enabled(
        self, animated_avif_service, video_file, tmp_path
    ):
        """match_audio=True shifts -ss back by padding and extends -t by 2*padding."""
        cfg = dataclasses.replace(
            animated_avif_service.config,
            screenshot_animated_match_audio=True,
            audio_padding=0.3,
        )
        animated_avif_service.config = cfg
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=1.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=0.1, duration=1.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == str(0.0)

    def test_match_audio_overrides_configured_duration(
        self, animated_avif_service, video_file, tmp_path
    ):
        """match_audio=True bypasses the configured clip_duration cap."""
        cfg = dataclasses.replace(
            animated_avif_service.config,
            screenshot_animated_match_audio=True,
            audio_padding=0.3,
            screenshot_animated_clip_duration=2.0,
        )
        animated_avif_service.config = cfg
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=5.0, duration=5.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-t") + 1] == str(5.6)

    def test_avif_command_shape(self, animated_avif_service, video_file, tmp_path):
        """AVIF ffmpeg command must include libsvtav1, CRF, loop, scale filter."""
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_avif_service._extract_animated_screenshot(
                video_file, start_time=2.0, duration=3.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            animated_webp_service._extract_animated_screenshot(
                video_file, start_time=2.0, duration=3.0, output_path=output_path
            )

        cmd = mock_run.call_args[0][0]
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

    def test_animated_returns_false_on_ffmpeg_failure(
        self, animated_avif_service, video_file, tmp_path
    ):
        """Non-zero ffmpeg exit on encode → return False."""
        output_path = tmp_path / "clip.avif"
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"encode failed"

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
            result = animated_avif_service._extract_animated_screenshot(
                video_file, 1.0, 2.0, output_path
            )

        assert result is False


class TestExtractAudio:
    """Tests for _extract_audio method."""

    def test_padding_calculation(self, service, video_file, tmp_path):
        """Should apply audio padding: start = start - 0.3, duration = dur + 0.6."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 5.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        # audio_start = max(0, 5.0 - 0.3) = 4.7
        assert cmd[cmd.index("-ss") + 1] == str(4.7)
        # audio_duration = 2.0 + (0.3 * 2) = 2.6
        assert cmd[cmd.index("-t") + 1] == str(2.6)

    def test_start_clamped_to_zero(self, service, video_file, tmp_path):
        """Should clamp audio start to 0 when start_time - padding < 0."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 0.1, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        # audio_start = max(0, 0.1 - 0.3) = max(0, -0.2) = 0
        assert cmd[cmd.index("-ss") + 1] == str(0)

    def test_maps_japanese_audio_stream(self, service, video_file, tmp_path):
        """Should use -map 0:{stream_index} when Japanese audio detected."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=2),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        map_index = cmd.index("-map")
        assert cmd[map_index + 1] == "0:2"

    def test_falls_back_to_first_audio_stream(self, service, video_file, tmp_path):
        """Should use -map 0:a:0 when no Japanese audio stream detected."""
        output_path = tmp_path / "output.mp3"
        output_path.write_bytes(b"\xff\xfbfake-mp3")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        map_index = cmd.index("-map")
        assert cmd[map_index + 1] == "0:a:0"

    def test_returns_false_on_nonzero_exit(self, service, video_file, tmp_path):
        """Should return False when ffmpeg exits with non-zero code."""
        output_path = tmp_path / "output.mp3"

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"error output"

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc),
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path)

        assert result is False

    def test_returns_false_on_exception(self, service, video_file, tmp_path):
        """Should return False when subprocess raises an exception."""
        output_path = tmp_path / "output.mp3"

        with (
            patch(
                f"{MODULE}.subprocess.run",
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

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
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

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
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

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        bitrate_index = cmd.index("-b:a")
        assert cmd[bitrate_index + 1] == "64k"

    def test_returns_false_when_encoder_unavailable(self, service, video_file, tmp_path):
        """Should hard-fail (return False) when the configured encoder is missing (Issue #18)."""
        output_path = tmp_path / "out.mp3"

        with (
            patch.object(service, "_check_encoder_available", return_value=False),
            patch(f"{MODULE}.subprocess.run") as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            result = service._extract_audio(video_file, 1.0, 2.0, output_path)

        assert result is False
        # No ffmpeg encode call should have been made.
        mock_run.assert_not_called()

    def test_opus_downmixes_to_stereo(self, test_config, video_file, tmp_path):
        """Should pass -ac 2 when opus to avoid libopus 5.1 channel-mapping error (Issue #18)."""
        cfg = dataclasses.replace(test_config, audio_format="opus", audio_bitrate=64)
        with patch(f"{MODULE}.ensure_directory"):
            svc = MediaExtractorService(cfg)
        svc._animated_encoder_ok["libopus"] = True

        output_path = tmp_path / "out.opus"
        output_path.write_bytes(b"OggS")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(svc, "_get_japanese_audio_stream", return_value=None),
        ):
            svc._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        ac_index = cmd.index("-ac")
        assert cmd[ac_index + 1] == "2"

    def test_mp3_does_not_force_stereo(self, service, video_file, tmp_path):
        """MP3 path preserves source channel layout (libmp3lame handles 5.1 natively)."""
        output_path = tmp_path / "out.mp3"
        output_path.write_bytes(b"\xff\xfbfake")

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run,
            patch.object(service, "_get_japanese_audio_stream", return_value=None),
        ):
            service._extract_audio(video_file, 1.0, 2.0, output_path)

        cmd = mock_run.call_args[0][0]
        assert "-ac" not in cmd

    def test_filename_extension_matches_format(
        self, test_config, video_file, make_tokenized_word, tmp_path
    ):
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

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
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

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
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

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc) as mock_run:
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

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
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

        with patch(f"{MODULE}.subprocess.run", return_value=mock_proc):
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

        def fake_extract(vf, word, temp_folder=None):
            # Create real files so has_any_media returns True
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

        def fake_extract(vf, word, temp_folder=None):
            nonlocal call_count
            call_count += 1
            from anki_miner.models import MediaData

            if word.lemma == "成功":
                # Create a real file so has_any_media returns True
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

        def fake_extract(vf, word, temp_folder=None):
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

        def fake_extract(vf, word, temp_folder=None):
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

        def fake_extract(vf, word, temp_folder=None):
            if word.lemma == "悪い":
                raise RuntimeError("ffmpeg exploded")
            from anki_miner.models import MediaData

            ss = tmp_path / "good.jpg"
            ss.write_bytes(b"\xff\xd8fake")
            return MediaData(screenshot_path=ss, screenshot_filename="good.jpg")

        with patch.object(service, "extract_media", side_effect=fake_extract):
            result = service.extract_media_batch(video_file, words, recording_progress)

        # Only the successful word should be in results
        assert len(result) == 1
        assert result[0][0].lemma == "良い"
        # The error should be reported
        assert len(recording_progress.errors) == 1
        assert recording_progress.errors[0][0] == "悪い"
        assert "ffmpeg exploded" in recording_progress.errors[0][1]
