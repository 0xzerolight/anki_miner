"""Detect Japanese audio streams in video files via ffprobe."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

JAPANESE_LANGUAGE_CODES = frozenset({"jpn", "ja", "japanese", "jp"})


@dataclass(frozen=True)
class AudioStream:
    """Full metadata for a single audio stream from ffprobe.

    `global_index` is the ffprobe stream index, suitable for ffmpeg `-map 0:N`.
    `audio_index` is the position within the audio-only track list (0-indexed),
    suitable for `QMediaPlayer.setActiveAudioTrack(N)`.
    """

    global_index: int
    audio_index: int
    language_tag: str | None
    title_tag: str | None
    codec: str | None
    channels: int | None
    is_default: bool


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


def _run_ffprobe_json(video_path: Path, select_streams: str, ffprobe_cmd: str) -> dict | None:
    """Run ffprobe for ``select_streams`` and return the parsed JSON object.

    Returns ``None`` if ffprobe fails, times out, raises an OSError, returns a
    non-zero exit code, or returns output that does not parse as JSON. Callers
    translate ``None`` into their documented empty/None fallback.

    ffprobe always emits UTF-8 JSON, so stdout is decoded with
    ``encoding="utf-8", errors="replace"`` — not the platform locale codec,
    which on Windows (cp1252/cp932) raises ``UnicodeDecodeError`` on non-ASCII
    stream titles (ubiquitous in anime MKVs). ``UnicodeDecodeError`` is a
    ``ValueError`` and is also caught defensively below.

    ``ffprobe_cmd`` becomes ``cmd[0]``; config-bearing callers should pass
    ``resolve_ffprobe(config)`` so frozen bundles use the bundled binary.
    """
    cmd = [
        ffprobe_cmd,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        select_streams,
        str(video_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
        )
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.warning(f"Error probing {video_path} (select={select_streams}): {e}")
        return None

    if proc.returncode != 0:
        logger.warning(f"ffprobe failed for {video_path}: {proc.stderr}")
        return None

    try:
        data: dict = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.warning(f"ffprobe returned malformed JSON for {video_path}: {e}")
        return None
    return data


def list_audio_streams(video_path: Path, ffprobe_cmd: str = "ffprobe") -> list[AudioStream]:
    """Probe a video file with ffprobe and return all audio streams.

    Returns an empty list if ffprobe fails, times out, raises an OSError,
    returns a non-zero exit code, or returns malformed JSON. Streams missing
    the top-level ``index`` field are skipped, but still consume an
    ``audio_index`` slot (preserving parity with the original enumeration
    behavior).

    ``ffprobe_cmd`` is the executable to invoke (``cmd[0]``); it defaults to the
    bare ``"ffprobe"`` literal so direct callers are unaffected. Config-bearing
    callers should pass ``resolve_ffprobe(config)`` so frozen bundles use the
    bundled binary.
    """
    data = _run_ffprobe_json(video_path, "a", ffprobe_cmd)
    if data is None:
        return []

    raw_streams = data.get("streams", [])
    result: list[AudioStream] = []

    for audio_index, stream in enumerate(raw_streams):
        try:
            global_index = int(stream["index"])
        except (KeyError, TypeError, ValueError):
            # audio_index slot is consumed but stream is skipped
            continue

        tags = stream.get("tags", {}) or {}
        lang_raw = tags.get("language")
        language_tag = lang_raw.lower() if lang_raw else None
        title_tag = tags.get("title") or None

        codec = stream.get("codec_name") or None

        channels_raw = stream.get("channels")
        channels: int | None = None
        if channels_raw is not None:
            try:
                channels = int(channels_raw)
            except (ValueError, TypeError):
                channels = None

        disposition = stream.get("disposition") or {}
        is_default = disposition.get("default") == 1

        result.append(
            AudioStream(
                global_index=global_index,
                audio_index=audio_index,
                language_tag=language_tag,
                title_tag=title_tag,
                codec=codec,
                channels=channels,
                is_default=is_default,
            )
        )

    return result


def find_japanese_audio_stream(video_file: Path, ffprobe_cmd: str = "ffprobe") -> JapaneseAudioStream | None:
    """Probe a video file with ffprobe and return its Japanese audio stream.

    Returns None if ffprobe fails, returns malformed JSON, or no audio stream
    has a Japanese language tag.

    ``ffprobe_cmd`` is forwarded to :func:`list_audio_streams`; defaults to the
    bare ``"ffprobe"`` literal so direct callers are unaffected.
    """
    streams = list_audio_streams(video_file, ffprobe_cmd=ffprobe_cmd)

    for stream in streams:
        if stream.language_tag in JAPANESE_LANGUAGE_CODES:
            logger.info(
                f"Found Japanese audio: global stream {stream.global_index}, "
                f"audio track {stream.audio_index} (language: {stream.language_tag})"
            )
            return JapaneseAudioStream(
                global_index=stream.global_index,
                audio_index=stream.audio_index,
                language_tag=stream.language_tag,
            )

    available_langs = [s.language_tag or "unknown" for s in streams]
    logger.warning(f"No Japanese audio found in {video_file}. Available languages: {available_langs}")
    return None


def get_primary_video_codec(video_file: Path, ffprobe_cmd: str = "ffprobe") -> str | None:
    """Return the codec_name of the first video stream (lowercased), or None.

    Returns None on any ffprobe failure (timeout, OSError, non-zero exit,
    malformed JSON), or when the file has no video stream / no ``codec_name``.
    Callers treat None as "assume supported" so a probe failure never disables
    an otherwise-working preview.

    ``ffprobe_cmd`` is the executable to invoke; defaults to the bare
    ``"ffprobe"`` literal. Config-bearing callers should pass
    ``resolve_ffprobe(config)`` so frozen bundles use the bundled binary.
    """
    data = _run_ffprobe_json(video_file, "v:0", ffprobe_cmd)
    if data is None:
        return None

    streams = data.get("streams", [])
    if not streams:
        return None

    codec = streams[0].get("codec_name")
    return codec.lower() if codec else None
