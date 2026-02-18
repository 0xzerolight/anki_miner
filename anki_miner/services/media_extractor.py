"""Service for extracting media (screenshots and audio) from video files."""

import json
import logging
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.utils import ensure_directory, safe_filename

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
        self._cache_lock = threading.Lock()

    def extract_media(
        self,
        video_file: Path,
        word: TokenizedWord,
    ) -> MediaData:
        """Extract screenshot and audio for a single word.

        Args:
            video_file: Path to video file
            word: TokenizedWord with timing information

        Returns:
            MediaData with paths to extracted files
        """
        # Sanitize filename
        safe_word = safe_filename(word.lemma)
        timestamp = int(word.start_time * 1000)

        screenshot_file = f"{safe_word}_{timestamp}.jpg"
        audio_file = f"{safe_word}_{timestamp}.mp3"

        screenshot_path = self.config.media_temp_folder / screenshot_file
        audio_path = self.config.media_temp_folder / audio_file

        # Extract screenshot
        screenshot_success = self._extract_screenshot(
            video_file, word.start_time, word.duration, screenshot_path
        )

        # Extract audio
        audio_success = self._extract_audio(video_file, word.start_time, word.duration, audio_path)

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
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Extract media for multiple words in parallel.

        Args:
            video_file: Path to video file
            words: List of words to extract media for
            progress_callback: Optional callback for progress reporting

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
                executor.submit(self.extract_media, video_file, word): word for word in words
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
                            progress_callback.on_progress(
                                completed, f"Extracting media: {word.lemma}"
                            )
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
        """Extract a screenshot from video.

        Args:
            video_file: Path to video file
            start_time: Start time in seconds
            duration: Duration in seconds
            output_path: Output path for screenshot

        Returns:
            True if successful, False otherwise
        """
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

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning(
                    f"Screenshot extraction failed for {output_path.name}: "
                    f"ffmpeg exit code {proc.returncode}: {proc.stderr.decode(errors='replace').strip()}"
                )
                return False
            return output_path.exists()
        except subprocess.TimeoutExpired:
            logger.warning(f"Screenshot extraction timed out for {output_path.name}")
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"Screenshot extraction error for {output_path.name}: {e}")
            return False

    def _get_japanese_audio_stream(self, video_file: Path) -> int | None:
        """Detect Japanese audio stream index using ffprobe.

        Args:
            video_file: Path to video file

        Returns:
            Stream index of Japanese audio, or None if not found
        """
        # Check cache first (thread-safe)
        with self._cache_lock:
            if video_file in self._audio_stream_cache:
                return self._audio_stream_cache[video_file]

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",  # Audio streams only
            str(video_file),
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
            if proc.returncode != 0:
                logger.warning(f"ffprobe failed for {video_file}: {proc.stderr}")
                with self._cache_lock:
                    self._audio_stream_cache[video_file] = None
                return None

            data = json.loads(proc.stdout)
            streams = data.get("streams", [])

            # Look for Japanese audio stream
            japanese_codes = {"jpn", "ja", "japanese", "jp"}

            for stream in streams:
                tags = stream.get("tags", {})
                language = tags.get("language", "").lower()

                if language in japanese_codes:
                    stream_index = stream.get("index")
                    logger.info(
                        f"Found Japanese audio: stream {stream_index} (language: {language})"
                    )
                    stream_index_int: int | None = (
                        int(stream_index) if stream_index is not None else None
                    )
                    with self._cache_lock:
                        self._audio_stream_cache[video_file] = stream_index_int
                    return stream_index_int

            # Log available streams for debugging
            available_langs = [s.get("tags", {}).get("language", "unknown") for s in streams]
            logger.warning(f"No Japanese audio found. Available languages: {available_langs}")
            with self._cache_lock:
                self._audio_stream_cache[video_file] = None
            return None

        except Exception as e:
            logger.warning(f"Error probing audio streams: {e}")
            with self._cache_lock:
                self._audio_stream_cache[video_file] = None
            return None

    def _extract_audio(
        self,
        video_file: Path,
        start_time: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """Extract audio clip from video, preferring Japanese audio.

        Args:
            video_file: Path to video file
            start_time: Start time in seconds
            duration: Duration in seconds
            output_path: Output path for audio

        Returns:
            True if successful, False otherwise
        """
        # Calculate audio timing with padding
        audio_start = max(0, start_time - self.config.audio_padding)
        audio_duration = duration + (self.config.audio_padding * 2)

        # Detect Japanese audio stream
        jp_stream = self._get_japanese_audio_stream(video_file)

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

        # Map to Japanese audio if found, otherwise use first audio stream
        if jp_stream is not None:
            cmd.extend(["-map", f"0:{jp_stream}"])
            logger.debug(f"Using Japanese audio stream {jp_stream}")
        else:
            cmd.extend(["-map", "0:a:0"])  # First audio stream
            logger.warning("No Japanese audio found, using first audio stream")

        cmd.extend(
            [
                "-vn",  # No video
                "-acodec",
                "libmp3lame",
                "-q:a",
                "2",  # Audio quality
                str(output_path),
            ]
        )

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            if proc.returncode != 0:
                logger.error(f"ffmpeg audio extraction failed: {proc.stderr.decode()}")
                return False
            return output_path.exists()
        except Exception as e:
            logger.error(f"Audio extraction error: {e}")
            return False
