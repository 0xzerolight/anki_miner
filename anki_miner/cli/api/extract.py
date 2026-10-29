"""extract: a video's text subtitle track saved as its own file, ready for mine (API.md, "extract").

Per episode, what Utilities -> Tracks does for one ticked subtitle track:
ffmpeg copies the stream as stored (ASS stays ASS, SubRip stays SubRip) into
``<run_dir>/<run_id>/extract-<n>/<video stem>.<language>.<ext>``. An image
track (PGS, VobSub) has no text to copy; ``video-ocr`` reads it. Nothing goes
to Anki and no lock is held.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import (
    BAD_RUN_FILE,
    CANCELLED,
    SETUP_ERROR,
    TRACK_NOT_TEXT,
    VIDEO_UNREADABLE,
    ApiError,
)
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.runs import check_video, require_ffmpeg
from anki_miner.cli.api.sources import resolve_track
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.services.track_extractor import (
    _LANGUAGE_TAG,
    SUBTITLE_FORMATS,
    ExtractStatus,
    PlannedTrack,
    TrackExtractorService,
    TrackRef,
)
from anki_miner.utils.file_utils import bounded_output_name

_KEYS = frozenset({"run_id", "video_file", "track"})
#: A text codec with no plain-file muxer of its own becomes SubRip, as MP4 timed text does.
_AS_SUBRIP = SUBTITLE_FORMATS["mov_text"]


@dataclass(frozen=True)
class ExtractEpisode:
    run_id: str
    video_file: Path
    #: the ffprobe stream index ``inspect`` lists
    track: int


def _episode(raw: object, where: str) -> ExtractEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, _KEYS, where)
    track = files.opt_int(obj["track"], f"{where}.track")
    if track is None or track < 0:
        raise files.bad(f"{where}.track must be a whole number of 0 or more.")
    return ExtractEpisode(
        run_id=files.valid_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=files.abs_path(files.text(obj["video_file"], f"{where}.video_file")),
        track=track,
    )


def call(extract_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``extract EXTRACT_FILE``: the file checked, then its runs."""
    return extract_runs(files.parse_jobs_file(files.read_json_file(extract_file), "extract", _episode), cancel_all)


def extract_runs(job: JobsFile[ExtractEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode's track saved in turn; an extract-<n>.json for each that succeeds."""
    return job_runs(job, "extract", cancel_all, partial(_prepare, job), _extract_one)


def _prepare(job: JobsFile[ExtractEpisode]) -> AnkiMinerConfig:
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    require_ffmpeg(config)
    return config


def _extract_one(config: AnkiMinerConfig, episode: ExtractEpisode, run: Run) -> dict[str, object]:
    run.progress.on_stage(1, 1, "extract")
    video = episode.video_file
    check_video(config, video)
    stream = resolve_track(config, video, episode.track)
    if not stream.is_text:
        raise ApiError(
            TRACK_NOT_TEXT,
            f"Track {episode.track} of {video.name} is an image subtitle ({stream.codec_name}), "
            "which has no text to copy. video-ocr reads it.",
        )
    # ffprobe gives the tag lowercased; "und" says no more than a missing one.
    language = stream.language_tag if stream.language_tag not in (None, "und") else None
    fmt = SUBTITLE_FORMATS.get(stream.codec_name or "", _AS_SUBRIP)
    # The tag is file metadata: it reaches the name only when it looks like one (Tracks' rule).
    tag = f".{language}" if language is not None and _LANGUAGE_TAG.fullmatch(language) else ""
    run.out.mkdir()
    dest = run.out / bounded_output_name(video.stem, tag + fmt.suffix, run.out)
    plan = PlannedTrack(TrackRef("subtitle", stream.sub_index), stream, fmt, dest)
    try:
        result = TrackExtractorService(config).extract(video, plan, cancel_event=run.cancel)
    except FfmpegNotFoundError as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    if result.status is ExtractStatus.CANCELLED:
        raise ApiError(CANCELLED, "The run was cancelled.")
    if result.status is ExtractStatus.FAILED:
        raise ApiError(VIDEO_UNREADABLE, f"Track {episode.track} of {video.name} could not be copied: {result.reason}")
    return {
        "subtitle_file": str(dest),
        "language": language,
        "codec": stream.codec_name,
        # Saved, but ffmpeg reported trouble (a damaged or half-downloaded source): the track may be cut short.
        "warning": result.reason or None,
    }
