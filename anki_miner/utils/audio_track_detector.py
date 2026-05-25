"""Detect Japanese audio streams in video files via ffprobe."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

JAPANESE_LANGUAGE_CODES = frozenset({"jpn", "ja", "japanese", "jp"})


@dataclass(frozen=True)
class JapaneseAudioStream:
    """Located Japanese audio stream within a video file.

    `global_index` is the ffprobe stream index, suitable for ffmpeg `-map 0:N`.
    `audio_index` is the position within the audio-only track list, suitable
    for `QMediaPlayer.setActiveAudioTrack(N)`.
    """

    global_index: int
    audio_index: int
    language_tag: str


def find_japanese_audio_stream(video_file: Path) -> JapaneseAudioStream | None:
    """Probe a video file with ffprobe and return its Japanese audio stream.

    Returns None if ffprobe fails, returns malformed JSON, or no audio stream
    has a Japanese language tag.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "a",
        str(video_file),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Error probing audio streams for {video_file}: {e}")
        return None

    if proc.returncode != 0:
        logger.warning(f"ffprobe failed for {video_file}: {proc.stderr}")
        return None

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.warning(f"ffprobe returned malformed JSON for {video_file}: {e}")
        return None

    streams = data.get("streams", [])

    for audio_index, stream in enumerate(streams):
        language = stream.get("tags", {}).get("language", "").lower()
        if language in JAPANESE_LANGUAGE_CODES:
            global_index = stream.get("index")
            if global_index is None:
                continue
            logger.info(
                f"Found Japanese audio: global stream {global_index}, "
                f"audio track {audio_index} (language: {language})"
            )
            return JapaneseAudioStream(
                global_index=int(global_index),
                audio_index=audio_index,
                language_tag=language,
            )

    available_langs = [s.get("tags", {}).get("language", "unknown") for s in streams]
    logger.warning(f"No Japanese audio found in {video_file}. Available languages: {available_langs}")
    return None
