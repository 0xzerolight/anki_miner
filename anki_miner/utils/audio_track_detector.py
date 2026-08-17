"""Detect Japanese audio streams in video files via ffprobe."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

JAPANESE_LANGUAGE_CODES = frozenset({"jpn", "ja", "japanese", "jp"})

# Image-based subtitle codecs: these carry rendered bitmaps, not extractable
# text, so the condenser detects and reports them but never attempts extraction.
BITMAP_SUBTITLE_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"})


@dataclass(frozen=True)
class AudioStream:
    """Full metadata for a single audio stream from ffprobe.

    `global_index` is the ffprobe stream index, suitable for ffmpeg `-map 0:N`.
    `audio_index` is the position within the audio-only track list (0-indexed,
    demuxer order); the mpv preview maps it to `aid = N + 1`.
    """

    global_index: int
    audio_index: int
    language_tag: str | None
    title_tag: str | None
    codec: str | None
    channels: int | None
    is_default: bool


@dataclass(frozen=True)
class SubtitleStream:
    """Full metadata for a single subtitle stream from ffprobe.

    `index` is the ffprobe global stream index, suitable for ffmpeg `-map 0:N`.
    `sub_index` is the position within the subtitle-only track list (0-indexed),
    suitable for ffmpeg `-map 0:s:N`.
    `is_text` is False for image-based codecs (:data:`BITMAP_SUBTITLE_CODECS`),
    whose bitmaps cannot be extracted as text.
    `is_forced` / `is_default` mirror the ffprobe disposition flags. Forced
    tracks carry only foreign-dialogue lines, which makes them useless as a
    retiming reference (see ``services/retime_reference.py``); they default to
    False so callers constructing a stream by hand stay unaffected.
    """

    index: int
    sub_index: int
    codec_name: str | None
    language_tag: str | None
    title: str | None
    is_text: bool
    is_forced: bool = False
    is_default: bool = False


def is_japanese_language_tag(language_tag: str | None) -> bool:
    """Return whether *language_tag* identifies Japanese.

    Alongside the legacy aliases, accept BCP 47 tags whose primary language
    subtag is ``ja`` (for example, ``ja-JP``).
    """
    if language_tag is None:
        return False
    normalized = language_tag.lower()
    return normalized in JAPANESE_LANGUAGE_CODES or normalized.startswith("ja-")


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
        logger.warning("Error probing %s (select=%s): %s", video_path, select_streams, e)
        return None

    if proc.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", video_path, proc.stderr)
        return None

    try:
        data: dict = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.warning("ffprobe returned malformed JSON for %s: %s", video_path, e)
        return None
    return data


def get_media_duration_seconds(video_path: Path, ffprobe_cmd: str = "ffprobe") -> float | None:
    """Return the container duration of *video_path* in seconds, or None.

    Uses ``format=duration`` (container-level) rather than a stream duration,
    which many MKVs omit. None on any probe failure — callers treat duration
    as unknown, never as zero.
    """
    cmd = [
        ffprobe_cmd,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration",
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
        if proc.returncode != 0:
            return None
        duration = float(json.loads(proc.stdout)["format"]["duration"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("Error probing duration of %s: %s", video_path, e)
        return None
    return duration if duration > 0 else None


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


def list_subtitle_streams(video_path: Path, ffprobe_cmd: str = "ffprobe") -> list[SubtitleStream]:
    """Probe a video file with ffprobe and return all subtitle streams.

    Returns an empty list if ffprobe fails, times out, raises an OSError,
    returns a non-zero exit code, or returns malformed JSON. Streams missing the
    top-level ``index`` field are skipped, but still consume a ``sub_index``
    slot (preserving parity with :func:`list_audio_streams`).

    ``sub_index`` is the position within the subtitle-only track list, suitable
    for ffmpeg ``-map 0:s:N``; ``index`` is the global stream index. These
    diverge whenever subtitle streams are interleaved with audio/video in the
    container.

    ``ffprobe_cmd`` is the executable to invoke (``cmd[0]``); it defaults to the
    bare ``"ffprobe"`` literal so direct callers are unaffected. Config-bearing
    callers should pass ``resolve_ffprobe(config)`` so frozen bundles use the
    bundled binary.
    """
    data = _run_ffprobe_json(video_path, "s", ffprobe_cmd)
    if data is None:
        return []

    raw_streams = data.get("streams", [])
    result: list[SubtitleStream] = []

    for sub_index, stream in enumerate(raw_streams):
        try:
            index = int(stream["index"])
        except (KeyError, TypeError, ValueError):
            # sub_index slot is consumed but stream is skipped
            continue

        tags = stream.get("tags", {}) or {}
        lang_raw = tags.get("language")
        language_tag = lang_raw.lower() if lang_raw else None
        title = tags.get("title") or None

        codec_name = stream.get("codec_name") or None
        is_text = codec_name not in BITMAP_SUBTITLE_CODECS

        disposition = stream.get("disposition") or {}

        result.append(
            SubtitleStream(
                index=index,
                sub_index=sub_index,
                codec_name=codec_name,
                language_tag=language_tag,
                title=title,
                is_text=is_text,
                is_forced=disposition.get("forced") == 1,
                is_default=disposition.get("default") == 1,
            )
        )

    return result


def find_japanese_audio_stream(video_file: Path, ffprobe_cmd: str = "ffprobe") -> AudioStream | None:
    """Probe a video file with ffprobe and return its Japanese audio stream.

    Returns None if ffprobe fails, returns malformed JSON, or no audio stream
    has a Japanese language tag.

    ``ffprobe_cmd`` is forwarded to :func:`list_audio_streams`; defaults to the
    bare ``"ffprobe"`` literal so direct callers are unaffected.
    """
    streams = list_audio_streams(video_file, ffprobe_cmd=ffprobe_cmd)

    japanese_streams = [stream for stream in streams if is_japanese_language_tag(stream.language_tag)]
    if japanese_streams:
        stream = next((candidate for candidate in japanese_streams if candidate.is_default), japanese_streams[0])
        logger.info(
            "Found Japanese audio: global stream %d, audio track %d (language: %s)",
            stream.global_index,
            stream.audio_index,
            stream.language_tag,
        )
        return stream

    available_langs = [s.language_tag or "unknown" for s in streams]
    logger.warning("No Japanese audio found in %s. Available languages: %s", video_file, available_langs)
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
