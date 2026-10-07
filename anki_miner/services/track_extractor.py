"""Save a video's embedded subtitle and audio tracks as standalone files (Utilities → Tracks).

One ffmpeg run per track, stream-copied into the track's own container: the
bytes are the release's own and nothing is re-encoded. MP4 timed text is the one
exception (no plain-file muxer takes it), so it becomes SubRip. The source is
only read, and each track lands through :func:`atomic_write_path`, so a
cancelled or failed run never leaves a half-written file under the final name.

Deliberately separate from ``AudioCondenserService.extract_embedded_subtitle``:
that one serves Condense and Retime with a text-only, transcoded temp file the
caller deletes; this one keeps every track faithful under a deterministic name.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.services.audio_condenser import _pick_subtitle_stream
from anki_miner.services.reading._util import natural_sort_key
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.audio_track_detector import (
    AudioStream,
    SubtitleStream,
    list_audio_streams,
    list_subtitle_streams,
    matches_language_tag,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.file_pairing import FilePairMatcher, resolve_output_paths
from anki_miner.utils.file_utils import bounded_output_name, is_junk_path
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState, run_supervised

logger = logging.getLogger(__name__)

TrackKind = Literal["subtitle", "audio"]
Stream = SubtitleStream | AudioStream


@dataclass(frozen=True)
class TrackFormat:
    """Where one codec's copy goes: file suffix, ffmpeg muxer (``-f``), codec argument."""

    suffix: str
    muxer: str
    codec: str = "copy"


SUBTITLE_FORMATS: dict[str, TrackFormat] = {
    "ass": TrackFormat(".ass", "ass"),
    # No ffmpeg >= 4 demuxer reports codec ``ssa`` (Matroska and .ssa files map
    # to ``ass``, and ffmpeg 8's ssa encoder/decoder are aliases of ass), so
    # there is nothing to transcode with: copy is the only option. Defensive row.
    "ssa": TrackFormat(".ass", "ass"),
    "subrip": TrackFormat(".srt", "srt"),
    "webvtt": TrackFormat(".vtt", "webvtt"),
    # MP4 timed text has no plain-file muxer; SubRip keeps its text and timing.
    "mov_text": TrackFormat(".srt", "srt", "subrip"),
    # The sup muxer needs ffmpeg >= 6; an older ffmpeg on PATH fails this track only.
    "hdmv_pgs_subtitle": TrackFormat(".sup", "sup"),
}
#: dvd_subtitle, dvb_subtitle and anything unknown: Matroska takes them all.
SUBTITLE_FALLBACK = TrackFormat(".mks", "matroska")

AUDIO_FORMATS: dict[str, TrackFormat] = {
    "aac": TrackFormat(".m4a", "ipod"),
    "alac": TrackFormat(".m4a", "ipod"),
    "opus": TrackFormat(".opus", "opus"),
    "vorbis": TrackFormat(".ogg", "ogg"),
    "flac": TrackFormat(".flac", "flac"),
    "mp3": TrackFormat(".mp3", "mp3"),
}
#: ac3, eac3, dts, truehd, pcm_* and anything unknown.
AUDIO_FALLBACK = TrackFormat(".mka", "matroska")

#: Inputs this tool lists: mining's video set plus WebM (yt-dlp's usual output).
TRACKS_VIDEO_EXTENSIONS: frozenset[str] = frozenset(FilePairMatcher.VIDEO_EXTENSIONS) | {".webm"}

#: A full demux of a multi-hour remux; same class as Condense's embedded-subtitle timeout.
_EXTRACT_TIMEOUT_S = 1800.0
#: What a language tag must look like before it may become part of a file name.
_LANGUAGE_TAG = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{1,8})*")


@dataclass(frozen=True)
class TrackRef:
    """A track by kind and position within that kind (0-based), the same in every episode."""

    kind: TrackKind
    position: int


@dataclass(frozen=True)
class MediaTracks:
    """One video's subtitle and audio streams, in demuxer order."""

    subtitles: tuple[SubtitleStream, ...] = ()
    audio: tuple[AudioStream, ...] = ()

    def find(self, ref: TrackRef) -> Stream | None:
        if ref.kind == "subtitle":
            return next((s for s in self.subtitles if s.sub_index == ref.position), None)
        return next((a for a in self.audio if a.audio_index == ref.position), None)

    @property
    def is_empty(self) -> bool:
        return not self.subtitles and not self.audio


@dataclass(frozen=True)
class InputProbe:
    """What the tab lists for a picked file or folder."""

    source: Path
    videos: tuple[Path, ...]
    #: Tracks of ``videos[0]``; empty when there is no video or it is unreadable.
    tracks: MediaTracks
    preselected: tuple[TrackRef, ...]


@dataclass(frozen=True)
class PlannedTrack:
    ref: TrackRef
    stream: Stream
    fmt: TrackFormat
    dest: Path


class ExtractStatus(Enum):
    SAVED = "saved"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ExtractResult:
    status: ExtractStatus
    reason: str = ""


def _codec_of(stream: Stream) -> str | None:
    return stream.codec_name if isinstance(stream, SubtitleStream) else stream.codec


def _global_index(stream: Stream) -> int:
    return stream.index if isinstance(stream, SubtitleStream) else stream.global_index


def track_format(kind: TrackKind, codec: str | None) -> TrackFormat:
    """The container a track of *codec* is copied into."""
    if kind == "subtitle":
        return SUBTITLE_FORMATS.get(codec or "", SUBTITLE_FALLBACK)
    return AUDIO_FORMATS.get(codec or "", AUDIO_FALLBACK)


def is_track_input(path: Path) -> bool:
    return path.suffix.lower() in TRACKS_VIDEO_EXTENSIONS


def list_videos(folder: Path) -> list[Path]:
    """The folder's videos in natural order (1, 2, 10), junk dropped: the run's own order."""
    files = (f for f in folder.iterdir() if f.is_file() and is_track_input(f) and not is_junk_path(f.name))
    return sorted(files, key=lambda f: natural_sort_key(f.name))


def probe_tracks(video: Path, ffprobe_cmd: str) -> MediaTracks:
    """Both stream lists. An unreadable file and a trackless one both come back empty."""
    return MediaTracks(
        subtitles=tuple(list_subtitle_streams(video, ffprobe_cmd)),
        audio=tuple(list_audio_streams(video, ffprobe_cmd)),
    )


def preferred_refs(tracks: MediaTracks, codes: frozenset[str]) -> tuple[TrackRef, ...]:
    """The subtitle to tick before the user does, or nothing.

    Condense's ranking picks it, but a pick in another language is dropped:
    saved as ``EP01.srt`` it would pair into mining in the wrong language. An
    untagged track is taken only when it is the only text track. Audio is never
    pre-ticked.
    """
    pick = _pick_subtitle_stream(list(tracks.subtitles), None, codes)
    if pick is None:
        return ()
    texts = [s for s in tracks.subtitles if s.is_text]
    lone_untagged = len(texts) == 1 and pick.language_tag in (None, "und")
    if matches_language_tag(pick.language_tag, codes) or lone_untagged:
        return (TrackRef("subtitle", pick.sub_index),)
    return ()


def probe_input(source: Path, ffprobe_cmd: str, codes: frozenset[str]) -> InputProbe:
    """List *source*'s videos and the first one's tracks. Blocking: call off the GUI thread."""
    videos = tuple(list_videos(source)) if source.is_dir() else (source,)
    if not videos:
        return InputProbe(source, (), MediaTracks(), ())
    tracks = probe_tracks(videos[0], ffprobe_cmd)
    return InputProbe(source, videos, tracks, preferred_refs(tracks, codes))


def output_name(
    stem: str,
    ref: TrackRef,
    language_tag: str | None,
    fmt: TrackFormat,
    *,
    sole_of_kind: bool,
    directory: Path,
) -> str:
    """``EP01.ass`` for the only ticked track of its kind, else ``EP01.s2.jpn.ass``.

    The lone name is the one mining pairs. The number is 1-based, as the table
    shows it. The language tag is file metadata, so it reaches the name only
    when it looks like one (``../x`` never does) and says something (not ``und``).
    """
    if sole_of_kind:
        fixed = fmt.suffix
    else:
        lang = (language_tag or "").lower()
        lang_part = f".{lang}" if lang != "und" and _LANGUAGE_TAG.fullmatch(lang) else ""
        letter = "s" if ref.kind == "subtitle" else "a"
        fixed = f".{letter}{ref.position + 1}{lang_part}{fmt.suffix}"
    return bounded_output_name(stem, fixed, directory)


def plan_outputs(
    video: Path, tracks: MediaTracks, ticked: Sequence[TrackRef], out_dir: Path
) -> tuple[list[PlannedTrack], list[TrackRef]]:
    """Match *ticked* to *video*'s streams by position; return (planned, missing).

    Whether a track is the only one of its kind is decided by *ticked*, not by
    what this video has, so every episode of a folder gets the same names.
    """
    per_kind = Counter(ref.kind for ref in ticked)
    found: list[tuple[TrackRef, Stream, TrackFormat, str]] = []
    missing: list[TrackRef] = []
    for ref in ticked:
        stream = tracks.find(ref)
        if stream is None:
            missing.append(ref)
            continue
        fmt = track_format(ref.kind, _codec_of(stream))
        name = output_name(
            video.stem, ref, stream.language_tag, fmt, sole_of_kind=per_kind[ref.kind] == 1, directory=out_dir
        )
        found.append((ref, stream, fmt, name))
    dests = resolve_output_paths(out_dir, [name for *_, name in found])
    planned = [PlannedTrack(ref, stream, fmt, dest) for (ref, stream, fmt, _), dest in zip(found, dests, strict=True)]
    return planned, missing


def build_command(ffmpeg: str, video: Path, plan: PlannedTrack, staged: Path) -> list[str]:
    """One stream, copied (or, for MP4 text, converted) into the table's muxer.

    Absolute paths: a relative ``Re:Zero 01.mkv`` reads as protocol ``Re``.
    ``-y`` because the staging file already exists; ``-f`` because the
    staging name must not choose the muxer (``.mks`` names none).
    """
    codec_flag = "-c:s" if plan.ref.kind == "subtitle" else "-c:a"
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(video.absolute()),
        "-map",
        f"0:{_global_index(plan.stream)}",
        "-map_chapters",
        "-1",
        codec_flag,
        plan.fmt.codec,
        "-f",
        plan.fmt.muxer,
        str(staged.absolute()),
    ]


class _Incomplete(Exception):
    """Raised inside ``atomic_write_path`` so the staging file is dropped, not published."""

    def __init__(self, result: ExtractResult) -> None:
        super().__init__(result.reason)
        self.result = result


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _failure(result: SupervisedResult, staged: Path) -> ExtractResult | None:
    """The track's failure, or None when ffmpeg wrote it. Raises when ffmpeg never started."""
    if result.state is SupervisedState.CANCELLED:
        return ExtractResult(ExtractStatus.CANCELLED)
    if result.returncode is None and isinstance(result.error, OSError):
        raise FfmpegNotFoundError(f"ffmpeg could not be started: {result.error}")
    if result.state is SupervisedState.TIMED_OUT:
        return ExtractResult(ExtractStatus.FAILED, "ffmpeg timed out")
    if result.state is not SupervisedState.COMPLETED:
        return ExtractResult(
            ExtractStatus.FAILED, _last_line(result.stderr) or f"ffmpeg exited with {result.returncode}"
        )
    if staged.stat().st_size == 0:
        return ExtractResult(ExtractStatus.FAILED, "ffmpeg wrote an empty file")
    return None


class TrackExtractorService:
    """Probe a video's tracks and save one at a time."""

    def __init__(self, config: AnkiMinerConfig) -> None:
        self._config = config

    def probe(self, video: Path) -> MediaTracks:
        return probe_tracks(video, resolve_ffprobe(self._config))

    def extract(self, video: Path, plan: PlannedTrack, *, cancel_event: threading.Event | None = None) -> ExtractResult:
        """Write *plan*'s track to ``plan.dest``; the destination changes only on success.

        Raises:
            FfmpegNotFoundError: ffmpeg could not be started at all.
        """
        ffmpeg = resolve_ffmpeg(self._config)
        try:
            with atomic_write_path(plan.dest) as staged:
                result = run_supervised(
                    build_command(ffmpeg, video, plan, staged),
                    timeout_s=_EXTRACT_TIMEOUT_S,
                    cancel=cancel_event,
                    op="ffmpeg track-extract",
                )
                failure = _failure(result, staged)
                if failure is not None:
                    raise _Incomplete(failure)
        except _Incomplete as incomplete:
            return incomplete.result
        except OSError as exc:
            # mkstemp or replace in a read-only, missing or locked folder.
            logger.warning("Track extract to %s failed: %s: %s", plan.dest, type(exc).__name__, exc)
            return ExtractResult(ExtractStatus.FAILED, str(exc))
        return ExtractResult(ExtractStatus.SAVED)
