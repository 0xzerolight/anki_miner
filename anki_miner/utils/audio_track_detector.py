"""Detect Japanese audio streams in video files via ffprobe."""

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from anki_miner.utils.subprocess_log import log_command, log_command_result, tail_for_log
from anki_miner.utils.subprocess_utils import no_window_kwargs

logger = logging.getLogger(__name__)

# A probe that returns something other than JSON returned an error page, a
# usage banner or a shell wrapper's complaint; the head of it names which.
_MALFORMED_STDOUT_CHARS = 200

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
    ``width``/``height`` are the canvas ffprobe reports for an image track (PGS
    1920x1080, VobSub 720x480); None for text tracks and when unknown. Video OCR
    draws an image track on a canvas this size (F-18).
    """

    index: int
    sub_index: int
    codec_name: str | None
    language_tag: str | None
    title: str | None
    is_text: bool
    is_forced: bool = False
    is_default: bool = False
    width: int | None = None
    height: int | None = None


def matches_language_tag(language_tag: str | None, codes: frozenset[str]) -> bool:
    """Whether *language_tag* names one of *codes*.

    Accepts the exact tag and a BCP 47 regional variant whose primary subtag
    is in *codes* (``ja-JP`` -> ``ja``, ``ko-KR`` -> ``ko``). A code that is
    merely a prefix (``jav``) never matches: the variant must be
    dash-separated.
    """
    if language_tag is None:
        return False
    normalized = language_tag.lower()
    if normalized in codes:
        return True
    primary, sep, _ = normalized.partition("-")
    return bool(sep) and primary in codes


def _positive_int(value: object) -> int | None:
    """ffprobe's ``width``/``height`` as an int, or None when absent, zero or not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (ValueError, OverflowError):
        return None
    return number if number > 0 else None


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

    log_command(logger, "ffprobe", cmd, timeout_s=30)
    started_at = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
        )
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        # Nothing ran, so there is no stderr: the argv is the whole diagnosis.
        log_command(logger, "ffprobe", cmd, timeout_s=30, level=logging.WARNING)
        logger.warning("Error probing %s (select=%s): %s: %s", video_path, select_streams, type(e).__name__, e)
        return None

    elapsed_s = time.monotonic() - started_at
    if proc.returncode != 0:
        log_command_result(
            logger,
            "ffprobe",
            cmd,
            returncode=proc.returncode,
            stderr_tail=tail_for_log(proc.stderr or ""),
            elapsed_s=elapsed_s,
            level=logging.WARNING,
        )
        return None

    try:
        data: dict = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        # ffprobe exited 0 and still produced non-JSON: log the head of what it
        # did produce, which names the wrapper or error page that answered.
        logger.warning(
            "ffprobe returned malformed JSON for %s: %s: stdout[:%d]=%r",
            video_path,
            e,
            _MALFORMED_STDOUT_CHARS,
            (proc.stdout or "")[:_MALFORMED_STDOUT_CHARS],
        )
        return None
    log_command_result(logger, "ffprobe", cmd, returncode=0, elapsed_s=elapsed_s)
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
    log_command(logger, "ffprobe", cmd, timeout_s=30)
    started_at = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
        )
        if proc.returncode != 0:
            # Was a silent None: a duration probe that fails takes the caller's
            # progress reporting down with it, so it says so now.
            log_command_result(
                logger,
                "ffprobe",
                cmd,
                returncode=proc.returncode,
                stderr_tail=tail_for_log(proc.stderr or ""),
                elapsed_s=time.monotonic() - started_at,
                level=logging.WARNING,
            )
            return None
        duration = float(json.loads(proc.stdout)["format"]["duration"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as e:
        log_command(logger, "ffprobe", cmd, timeout_s=30, level=logging.WARNING)
        logger.warning("Error probing duration of %s: %s: %s", video_path, type(e).__name__, e)
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
                width=_positive_int(stream.get("width")),
                height=_positive_int(stream.get("height")),
            )
        )

    return result


@dataclass(frozen=True)
class Chapter:
    """One container chapter from ffprobe: its span in seconds and its title tag."""

    start: float
    end: float
    title: str | None


def list_chapters(media_path: Path, ffprobe_cmd: str = "ffprobe", *, require_video: bool = False) -> list[Chapter]:
    """Probe *media_path*'s chapters with one ``ffprobe -show_chapters`` call.

    With *require_video*, the same call also lists the file's moving-picture
    streams (``-select_streams V``, which leaves out cover art) and a file
    without one has no chapters: an audiobook's chapters carry the book's own
    titles ("Opening the Door"), never an anime opening or ending.

    Returns ``[]`` when the file has no chapters (the common case) and on any
    failure: ffprobe missing, a timeout, a non-zero exit, output that is not a
    JSON object. Failures log at DEBUG only, because a file without readable
    chapters is simply mined whole (F-08 step 2). A chapter whose times do not
    parse is skipped. ``ffprobe_cmd`` is ``resolve_ffprobe(config)`` at a
    config-bearing call site, so frozen bundles use the bundled binary.
    """
    cmd = [ffprobe_cmd, "-v", "quiet", "-print_format", "json", "-show_chapters"]
    if require_video:
        cmd += ["-show_entries", "stream=index", "-select_streams", "V"]
    cmd.append(str(media_path))
    log_command(logger, "ffprobe", cmd, timeout_s=30)
    started_at = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
        )
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug("Chapter probe failed for %s: %s: %s", media_path, type(e).__name__, e)
        return []
    elapsed_s = time.monotonic() - started_at
    if proc.returncode != 0:
        log_command_result(
            logger,
            "ffprobe",
            cmd,
            returncode=proc.returncode,
            stderr_tail=tail_for_log(proc.stderr or ""),
            elapsed_s=elapsed_s,
        )
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.debug("Chapter probe of %s returned malformed JSON: %s", media_path, e)
        return []
    if not isinstance(data, dict):
        return []
    log_command_result(logger, "ffprobe", cmd, returncode=0, elapsed_s=elapsed_s)
    if require_video and not data.get("streams"):
        return []

    chapters: list[Chapter] = []
    for raw in data.get("chapters") or []:
        try:
            start = float(raw["start_time"])
            end = float(raw["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        tags = raw.get("tags") or {}
        title = tags.get("title") or tags.get("TITLE") or None
        chapters.append(Chapter(start=start, end=end, title=title))
    return chapters


def find_japanese_audio_stream(
    video_file: Path, ffprobe_cmd: str = "ffprobe", *, codes: frozenset[str] | None = None
) -> AudioStream | None:
    """Probe a video file with ffprobe and return its mining-language audio stream.

    Returns None if ffprobe fails, returns malformed JSON, or no audio stream
    carries a matching language tag.

    ``ffprobe_cmd`` is forwarded to :func:`list_audio_streams`; defaults to the
    bare ``"ffprobe"`` literal so direct callers are unaffected.

    ``codes`` is the mining language's ``LanguageProfile.audio_track_codes``;
    it is keyword-only so the pre-existing positional ``ffprobe_cmd`` callers
    stay untouched, and ``None`` means :data:`JAPANESE_LANGUAGE_CODES`.
    """
    wanted = JAPANESE_LANGUAGE_CODES if codes is None else codes
    streams = list_audio_streams(video_file, ffprobe_cmd=ffprobe_cmd)

    japanese_streams = [stream for stream in streams if matches_language_tag(stream.language_tag, wanted)]
    if japanese_streams:
        stream = next((candidate for candidate in japanese_streams if candidate.is_default), japanese_streams[0])
        logger.info(
            "Found matching audio: global stream %d, audio track %d (language: %s)",
            stream.global_index,
            stream.audio_index,
            stream.language_tag,
        )
        return stream

    available_langs = [s.language_tag or "unknown" for s in streams]
    logger.warning(
        "No audio tagged %s found in %s. Available languages: %s", sorted(wanted), video_file, available_langs
    )
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


def get_video_size(video_file: Path, ffprobe_cmd: str = "ffprobe") -> tuple[int, int] | None:
    """Return the first video stream's ``(width, height)``, or None.

    None on any ffprobe failure, for a file with no video stream (a ``.sup``),
    and when either dimension is missing. Video OCR's canvas for an image
    subtitle track whose own size ffprobe does not report (F-18).
    """
    data = _run_ffprobe_json(video_file, "v:0", ffprobe_cmd)
    if data is None:
        return None
    streams = data.get("streams", [])
    if not streams:
        return None
    width, height = _positive_int(streams[0].get("width")), _positive_int(streams[0].get("height"))
    return (width, height) if width is not None and height is not None else None


class TrackTitle(IntEnum):
    """What a subtitle track's title, or a sidecar's name tags, says it holds; best first (MS-06, F-04)."""

    #: Says it is the whole dialogue: "Full Subtitles", "Dialogue".
    FULL = 0
    #: Says neither: no title, a language name, a release group.
    PLAIN = 1
    #: Dialogue plus sound notes: "SDH", "CC", "Hearing Impaired".
    ANNOTATED = 2
    #: Not the dialogue: signs, songs, forced lines, commentary, karaoke.
    PARTIAL = 3


# Every keyword is bounded by a non-word character (or an end) on both sides, the
# way subtitle_cleaner.NON_DIALOGUE_STYLE_RE bounds ASS style names, so "Designs"
# is not "signs", "Fullmetal" is not "full" and "Hide" is not "hi". A sidecar's
# tags (".en.forced", "_track3_[eng]") read through the same patterns.
_FULL_TITLE_RE = re.compile(r"(?:^|[\W_])(?:full|dialogue|dialog|complete)(?:$|[\W_])", re.IGNORECASE)
_PARTIAL_TITLE_RE = re.compile(
    r"(?:^|[\W_])(?:signs?|songs?|s&s|forced|commentary|karaoke|lyrics|typesetting|op[\W_]*ed)(?:$|[\W_])",
    re.IGNORECASE,
)
_ANNOTATED_TITLE_RE = re.compile(
    r"(?:^|[\W_])(?:sdh|cc|hi|hoh|hearing[\W_]*impaired|hard[\W_]*of[\W_]*hearing)(?:$|[\W_])",
    re.IGNORECASE,
)


def track_title_rank(text: str | None) -> TrackTitle:
    """Rank a track title or a sidecar's name tags; ``None`` and ``""`` are :attr:`TrackTitle.PLAIN`.

    A title that says "full" or "dialogue" wins over any other keyword in it
    ("Full Subtitles (Signs excluded)" is the dialogue track); otherwise a
    partial keyword beats an SDH one ("SDH Forced" is forced).
    """
    if not text:
        return TrackTitle.PLAIN
    if _FULL_TITLE_RE.search(text):
        return TrackTitle.FULL
    if _PARTIAL_TITLE_RE.search(text):
        return TrackTitle.PARTIAL
    if _ANNOTATED_TITLE_RE.search(text):
        return TrackTitle.ANNOTATED
    return TrackTitle.PLAIN


def subtitle_stream_title(stream: SubtitleStream) -> TrackTitle:
    """*stream*'s title rank; the forced disposition is :attr:`TrackTitle.PARTIAL` whatever the title says."""
    return TrackTitle.PARTIAL if stream.is_forced else track_title_rank(stream.title)


def primary_subtitle_key(stream: SubtitleStream, codes: frozenset[str]) -> tuple[bool, bool, int, int]:
    """Sort key for the mining track among a video's subtitle streams, smallest first (MS-06).

    Partial tracks (signs, songs, forced) last; then the mining language
    (*codes*, the profile's ``audio_track_codes``) before any other; then full
    dialogue before a plain title before SDH; then stream order. With no titles
    this is exactly the order ``audio_condenser._pick_subtitle_stream`` used
    before MS-06 (forced, language, index), so an untitled file picks the same
    stream as before. A partial track is ranked, never refused: a lone signs
    track is still better than nothing.
    """
    title = subtitle_stream_title(stream)
    return (
        title is TrackTitle.PARTIAL,
        not matches_language_tag(stream.language_tag, codes),
        int(title),
        stream.sub_index,
    )
