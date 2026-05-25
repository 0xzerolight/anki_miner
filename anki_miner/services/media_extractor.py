"""Service for extracting media (screenshots and audio) from video files."""

import logging
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.utils import (
    AudioStream,
    ensure_directory,
    find_japanese_audio_stream,
    list_audio_streams,
    safe_filename,
)
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES

logger = logging.getLogger(__name__)


class MediaExtractorService:
    """Extract screenshots and audio clips from video files (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the media extractor.

        Args:
            config: Configuration for media extraction
        """
        self.config = config
        ensure_directory(config.media_temp_folder)
        self._audio_stream_cache: dict[Path, int | None] = {}
        self._audio_stream_list_cache: dict[Path, list[AudioStream]] = {}
        self._cache_lock = threading.Lock()
        # Lazy, cached encoder-availability probe for animated screenshots.
        # Keyed by ffmpeg encoder name (e.g. "libsvtav1", "libwebp_anim").
        self._animated_encoder_ok: dict[str, bool] = {}
        self._encoder_probe_lock = threading.Lock()

    def extract_media(
        self,
        video_file: Path,
        word: TokenizedWord,
        temp_folder: Path | None = None,
        *,
        audio_track_override: int | None = None,
    ) -> MediaData:
        """Extract screenshot and audio for a single word.

        Args:
            video_file: Path to video file
            word: TokenizedWord with timing information
            temp_folder: Per-run temp directory to write output into; when
                omitted, falls back to the config-level media_temp_folder.
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.

        Returns:
            MediaData with paths to extracted files
        """
        # Sanitize filename
        safe_word = safe_filename(word.lemma)
        timestamp = int(word.start_time * 1000)

        screenshot_ext = self.config.screenshot_animated_format if self.config.screenshot_animated else "jpg"
        screenshot_file = f"{safe_word}_{timestamp}.{screenshot_ext}"
        audio_file = f"{safe_word}_{timestamp}.{self.config.audio_format}"

        output_dir = temp_folder if temp_folder is not None else self.config.media_temp_folder
        screenshot_path = output_dir / screenshot_file
        audio_path = output_dir / audio_file

        # Extract screenshot
        screenshot_success = self._extract_screenshot(video_file, word.start_time, word.duration, screenshot_path)

        # Extract audio
        audio_success = self._extract_audio(
            video_file, word.start_time, word.duration, audio_path, audio_track_override
        )

        return MediaData(
            screenshot_path=screenshot_path if screenshot_success else None,
            audio_path=audio_path if audio_success else None,
            screenshot_filename=screenshot_file if screenshot_success else None,
            audio_filename=audio_file if audio_success else None,
        )

    def extract_media_batch(
        self,
        video_file: Path,
        words: list[TokenizedWord],
        progress_callback: ProgressCallback | None = None,
        cancelled_check: Callable[[], bool] | None = None,
        temp_folder: Path | None = None,
        *,
        audio_track_override: int | None = None,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Extract media for multiple words in parallel.

        Args:
            video_file: Path to video file
            words: List of words to extract media for
            progress_callback: Optional callback for progress reporting
            cancelled_check: Optional callable returning True when the caller
                wants in-flight work cancelled.
            temp_folder: Per-run temp directory forwarded to extract_media.
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.

        Returns:
            List of (word, media_data) tuples (only includes words with successful extraction)
        """
        if progress_callback:
            progress_callback.on_start(len(words), "Extracting media")

        media_data_list = []
        max_workers = self.config.max_parallel_workers
        was_cancelled = False

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all extraction jobs
            future_to_word = {
                executor.submit(
                    self.extract_media, video_file, word, temp_folder, audio_track_override=audio_track_override
                ): word
                for word in words
            }

            # Collect results as they complete
            for completed, future in enumerate(as_completed(future_to_word), 1):
                # Check cancellation between items
                if cancelled_check and cancelled_check():
                    executor.shutdown(wait=False, cancel_futures=True)
                    was_cancelled = True
                    break

                word = future_to_word[future]

                try:
                    media = future.result()

                    if media.has_screenshot:
                        media_data_list.append((word, media))
                        if progress_callback:
                            progress_callback.on_progress(completed, f"Extracting media: {word.lemma}")
                    else:
                        if progress_callback:
                            progress_callback.on_progress(completed, f"No screenshot: {word.lemma}")

                except Exception as e:
                    if progress_callback:
                        progress_callback.on_error(word.lemma, str(e))

        if progress_callback and not was_cancelled:
            progress_callback.on_complete()

        return media_data_list

    def _extract_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """Extract a screenshot from video, dispatching to static or animated path."""
        if self.config.screenshot_animated:
            return self._extract_animated_screenshot(video_file, start_time, duration, output_path)
        return self._extract_static_screenshot(video_file, start_time, duration, output_path)

    def _run_ffmpeg(self, cmd: list[str], op_name: str, timeout: int, context: str = "") -> bool:
        """Run an ffmpeg/ffprobe command. Log + swallow errors. Return success bool.

        Returns True only on a zero exit code. Callers may impose additional
        post-run checks (e.g. ``output_path.exists()``) on top of this.
        """
        suffix = f" for {context}" if context else ""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                return True
            logger.warning("%s failed%s: ffmpeg exit code %s: %s", op_name, suffix, result.returncode, result.stderr)
            return False
        except subprocess.TimeoutExpired:
            logger.warning("%s timed out%s after %ss", op_name, suffix, timeout)
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("%s error%s: %s", op_name, suffix, e)
            return False

    def _extract_static_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """Extract a single still frame as JPEG."""
        # Calculate screenshot time (offset from start)
        screenshot_time = start_time + min(self.config.screenshot_offset, duration / 2)

        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-ss",
            str(screenshot_time),
            "-i",
            str(video_file),
            "-frames:v",
            "1",  # Extract single frame
            "-q:v",
            "2",  # Quality (2 = high)
            str(output_path),
        ]

        if not self._run_ffmpeg(cmd, "Static screenshot extraction", timeout=30, context=output_path.name):
            return False
        return output_path.exists()

    @staticmethod
    def _quality_to_avif_crf(quality: int) -> int:
        """Map user-facing 0-100 quality (higher = better) to AVIF CRF 0-63 (lower = better)."""
        clamped = max(0, min(100, int(quality)))
        return round(63 - (clamped / 100) * 63)

    @staticmethod
    def _encoder_for_format(fmt: str) -> str:
        """Return the ffmpeg encoder name for an animated format."""
        if fmt == "avif":
            return "libsvtav1"
        if fmt == "webp":
            return "libwebp_anim"
        raise ValueError(f"Unsupported animated screenshot format: {fmt}")

    def _check_encoder_available(self, encoder: str) -> bool:
        """Probe ffmpeg once for an encoder; cache result."""
        with self._encoder_probe_lock:
            cached = self._animated_encoder_ok.get(encoder)
            if cached is not None:
                return cached
            try:
                proc = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-encoders"],
                    capture_output=True,
                    timeout=15,
                    text=True,
                )
                available = proc.returncode == 0 and encoder in proc.stdout
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning(f"ffmpeg encoder probe failed: {e}")
                available = False
            self._animated_encoder_ok[encoder] = available
            if not available:
                logger.error(
                    f"ffmpeg encoder '{encoder}' not available. "
                    "Animated screenshots in this format will fail. "
                    "Install ffmpeg with the required encoder, or switch format in Settings."
                )
            return available

    def _extract_animated_screenshot(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """Extract a short animated clip (AVIF or WebP) instead of a static frame."""
        fmt = self.config.screenshot_animated_format
        try:
            encoder = self._encoder_for_format(fmt)
        except ValueError as e:
            logger.error(str(e))
            return False

        if not self._check_encoder_available(encoder):
            return False

        # Clip timing:
        # - When `screenshot_animated_match_audio` is enabled, the clip spans the
        #   full audio range (subtitle window + audio padding on both sides) so the
        #   visual matches the audio exactly.
        # - Otherwise, clip duration is capped by subtitle duration and configurable.
        # In both cases a 0.5s floor avoids 0-frame clips on very short subtitles.
        if self.config.screenshot_animated_match_audio:
            pad = float(self.config.audio_padding)
            clip_start = max(0.0, start_time - pad)
            clip_duration = max(duration + 2 * pad, 0.5)
        else:
            clip_start = start_time
            configured = float(self.config.screenshot_animated_clip_duration)
            clip_duration = min(configured, max(duration, 0.5))

        fps = int(self.config.screenshot_animated_fps)
        height = int(self.config.screenshot_animated_height)
        quality = int(self.config.screenshot_animated_quality)

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-ss",
            str(clip_start),
            "-t",
            str(clip_duration),
            "-i",
            str(video_file),
            "-an",
            "-sn",
            "-vf",
            f"fps={fps},scale=-2:{height}",
        ]

        if fmt == "avif":
            crf = self._quality_to_avif_crf(quality)
            cmd.extend(
                [
                    "-c:v",
                    "libsvtav1",
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-loop",
                    "0",
                ]
            )
        else:  # webp
            cmd.extend(
                [
                    "-c:v",
                    "libwebp_anim",
                    "-quality",
                    str(max(0, min(100, quality))),
                    "-loop",
                    "0",
                ]
            )

        cmd.append(str(output_path))

        if not self._run_ffmpeg(cmd, "Animated screenshot extraction", timeout=60, context=output_path.name):
            return False
        return output_path.exists()

    def _get_japanese_audio_stream(self, video_file: Path) -> int | None:
        """Detect Japanese audio stream index using ffprobe.

        Returns the global ffprobe stream index for ffmpeg `-map 0:N`.
        Thread-safe cache avoids re-probing the same file.
        """
        with self._cache_lock:
            if video_file in self._audio_stream_cache:
                return self._audio_stream_cache[video_file]

        result = find_japanese_audio_stream(video_file)
        global_index = result.global_index if result is not None else None

        with self._cache_lock:
            self._audio_stream_cache[video_file] = global_index
        return global_index

    def _list_audio_streams_cached(self, video_file: Path) -> list[AudioStream]:
        """Return full audio stream list for *video_file*, probing once and caching.

        Thread-safe under ``_cache_lock``.
        """
        with self._cache_lock:
            if video_file in self._audio_stream_list_cache:
                return self._audio_stream_list_cache[video_file]

        streams = list_audio_streams(video_file)

        with self._cache_lock:
            self._audio_stream_list_cache[video_file] = streams
        return streams

    def _resolve_audio_track_global_index(self, video_file: Path, audio_track_override: int | None) -> int | None:
        """Translate an optional *audio_track_override* (audio_index) to a ffprobe global index.

        - If *audio_track_override* is ``None``, returns the JP auto-detect result.
        - Otherwise looks up the stream with matching ``audio_index`` in the cached stream list
          and returns its ``global_index``. Falls back to JP auto-detect (with a warning) when
          no stream matches.
        """
        if audio_track_override is None:
            return self._get_japanese_audio_stream(video_file)

        streams = self._list_audio_streams_cached(video_file)
        for stream in streams:
            if stream.audio_index == audio_track_override:
                return stream.global_index

        logger.warning(
            "audio_track_override=%d not found in stream list (got %d streams); "
            "falling back to Japanese auto-detect",
            audio_track_override,
            len(streams),
        )
        # Reuse the streams list we already probed; don't re-run ffprobe.
        for stream in streams:
            if stream.language_tag in JAPANESE_LANGUAGE_CODES:
                return stream.global_index
        return None

    def _extract_audio(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
        audio_track_override: int | None = None,
    ) -> bool:
        """Extract audio clip from video, preferring Japanese audio.

        Args:
            video_file: Path to video file
            start_time: Start time in seconds
            duration: Duration in seconds
            output_path: Output path for audio
            audio_track_override: Optional 0-indexed audio track (audio_index) to use instead
                of auto-detecting Japanese. None (default) preserves existing JP auto-detect.

        Returns:
            True if successful, False otherwise
        """
        # Calculate audio timing with padding
        audio_start = max(0, start_time - self.config.audio_padding)
        audio_duration = duration + (self.config.audio_padding * 2)

        # Resolve encoder for the configured format and probe ffmpeg for support
        # before launching the encode. Cached probe; failure logs a clear error.
        encoder = "libopus" if self.config.audio_format == "opus" else "libmp3lame"
        if not self._check_encoder_available(encoder):
            return False

        # Resolve audio stream: honour override when set, else JP auto-detect.
        global_index = self._resolve_audio_track_global_index(video_file, audio_track_override)

        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(audio_start),
            "-t",
            str(audio_duration),
            "-i",
            str(video_file),
        ]

        if global_index is not None:
            cmd.extend(["-map", f"0:{global_index}"])
            logger.debug(f"Using audio stream {global_index}")
        else:
            cmd.extend(["-map", "0:a:0"])  # First audio stream
            logger.warning("No Japanese audio found, using first audio stream")

        cmd.extend(
            [
                "-vn",  # No video
                "-acodec",
                encoder,
                "-b:a",
                f"{self.config.audio_bitrate}k",
            ]
        )

        # libopus rejects multi-channel input (e.g. 5.1 surround eac3 common in
        # anime BD/WEB-DL releases) without an explicit channel mapping. Downmix
        # to stereo — Anki flashcards play through headphones/laptop speakers,
        # surround serves no purpose. MP3 (libmp3lame) tolerates 5.1 natively so
        # we leave its channel layout alone to preserve existing behavior.
        if self.config.audio_format == "opus":
            cmd.extend(["-ac", "2"])

        cmd.append(str(output_path))

        if not self._run_ffmpeg(cmd, "Audio extraction", timeout=30, context=output_path.name):
            return False
        return output_path.exists()
