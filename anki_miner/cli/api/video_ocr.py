"""video-ocr: subtitles read off a video's picture or an image subtitle track (API.md, "video-ocr").

Per episode, what Utilities -> Video OCR does for one file, into
``<run_dir>/<run_id>/video-ocr-<n>/<stem>.srt``. The source is a video's
picture (its subtitle region), a video's image subtitle track (PGS, VobSub,
DVB), or an image subtitle file (``.sup``, or a VobSub ``.idx``/``.sub`` pair);
the image tracks are read over the whole frame. Nothing goes to Anki and no
lock is held.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import (
    BAD_RUN_FILE,
    CANCELLED,
    SETUP_ERROR,
    TRACK_NOT_IMAGE,
    VIDEO_UNREADABLE,
    ApiError,
)
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.readiness import video_ocr_problem
from anki_miner.cli.api.runfolder import ProgressFile
from anki_miner.cli.api.runs import check_video, require_ffmpeg
from anki_miner.cli.api.sources import resolve_track
from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _valid_region
from anki_miner.services.video_ocr.bitmap_source import is_bitmap_file
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.services.video_ocr.scanner import VideoOcrStatus, scan_video
from anki_miner.utils.file_utils import bounded_output_name

_KEYS = frozenset({"run_id", "video_file", "track", "region"})
_REGION_KEYS = frozenset({"x", "y", "w", "h"})
_NO_REGION = (
    "{where} reads the video's picture, but it names no region and the profile has none. "
    "Send region, or draw one in Anki Miner: Utilities -> Video OCR."
)


@dataclass(frozen=True)
class VideoOcrEpisode:
    run_id: str
    #: a video, or an image subtitle file (.sup, or either half of a VobSub pair)
    video_file: Path
    #: the ffprobe stream index of the video's image track to read, ``inspect``'s; None reads the picture
    track: int | None
    #: the caller's region of the picture; None takes the profile's
    region: Region | None

    @property
    def reads_picture(self) -> bool:
        return self.track is None and not is_bitmap_file(self.video_file)


def _region(raw: object, where: str) -> Region:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _REGION_KEYS, _REGION_KEYS, where)
    values = tuple(files.opt_float(obj[key], f"{where}.{key}") for key in ("x", "y", "w", "h"))
    # The config's own rule, so a caller's region is one the Video OCR tab could have saved.
    valid = _valid_region(values)
    if not valid:
        raise files.bad(f"{where} must be fractions of the frame: x and y from 0, w and h above 0, inside the frame.")
    return Region(*valid)


def _episode(raw: object, where: str) -> VideoOcrEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, frozenset({"run_id", "video_file"}), where)
    video_file = files.abs_path(files.text(obj["video_file"], f"{where}.video_file"))
    track = files.opt_int(obj.get("track"), f"{where}.track")
    if track is not None and track < 0:
        raise files.bad(f"{where}.track must be a whole number of 0 or more.")
    region = None if obj.get("region") is None else _region(obj["region"], f"{where}.region")
    if is_bitmap_file(video_file) and (track is not None or region is not None):
        raise files.bad(f"{where}: an image subtitle file is read whole; it takes no track and no region.")
    if track is not None and region is not None:
        raise files.bad(f"{where}: an image track is read over the whole frame; send track or region, not both.")
    return VideoOcrEpisode(files.valid_run_id(obj["run_id"], f"{where}.run_id"), video_file, track, region)


def call(video_ocr_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``video-ocr VIDEO_OCR_FILE``: the file checked, then its runs."""
    return video_ocr_runs(
        files.parse_jobs_file(files.read_json_file(video_ocr_file), "video-ocr", _episode), cancel_all
    )


def video_ocr_runs(job: JobsFile[VideoOcrEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode read in turn; a video-ocr-<n>.json for each that finishes."""
    return job_runs(job, "video-ocr", cancel_all, partial(_prepare, job), _scan_one)


def _prepare(job: JobsFile[VideoOcrEpisode]) -> AnkiMinerConfig:
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    # The tab refuses a picture run until a region is drawn; so does the API, before any run.
    if Region.from_config(config.video_ocr_region) is None:
        for i, episode in enumerate(job.episodes):
            if episode.reads_picture and episode.region is None:
                raise ApiError(BAD_RUN_FILE, _NO_REGION.format(where=f"episodes[{i}]"))
    if (problem := video_ocr_problem(config)) is not None:
        raise ApiError(SETUP_ERROR, problem)
    require_ffmpeg(config)
    return config


def _scan_one(config: AnkiMinerConfig, episode: VideoOcrEpisode, run: Run) -> dict[str, object]:
    run.progress.on_stage(1, 1, "video-ocr")
    source = episode.video_file
    bitmap_track: int | None = None
    region = FULL_FRAME
    if is_bitmap_file(source):
        if not source.is_file():
            raise ApiError(VIDEO_UNREADABLE, f"File not found: {source}")
    else:
        check_video(config, source)
        if episode.track is not None:
            stream = resolve_track(config, source, episode.track)
            if stream.is_text:
                raise ApiError(
                    TRACK_NOT_IMAGE,
                    f"Track {episode.track} of {source.name} is a text subtitle ({stream.codec_name}), "
                    "which needs no OCR. extract copies it.",
                )
            # The scan picks the track by its place among the subtitle tracks, not by the stream index.
            bitmap_track = stream.sub_index
        else:
            picture = episode.region or Region.from_config(config.video_ocr_region)
            assert picture is not None  # _prepare refuses a picture run that has neither
            region = picture
    run.out.mkdir()
    out_srt = run.out / bounded_output_name(source.stem, ".srt", run.out)
    run.progress.on_start(100, "")
    try:
        result = scan_video(
            config,
            source,
            region,
            out_srt,
            progress_cb=_percent(run.progress),
            cancel_event=run.cancel,
            bitmap_track=bitmap_track,
        )
    except EngineLoadError as exc:
        raise ApiError(SETUP_ERROR, str(exc) or "The Video OCR engine could not start.") from exc
    if result.status is VideoOcrStatus.CANCELLED:
        raise ApiError(CANCELLED, "The run was cancelled.")
    if result.status is VideoOcrStatus.DECODE_FAILED:
        raise ApiError(VIDEO_UNREADABLE, f"{source.name} could not be read: {result.detail}")
    if result.status is VideoOcrStatus.NO_TEXT:
        return {"subtitle_file": None, "status": "no_text", "lines": 0}
    return {"subtitle_file": str(out_srt), "status": "generated", "lines": result.cue_count}


def _percent(progress: ProgressFile) -> Callable[[float, float | None], None]:
    """The scan's position as done/total in percent of the source's length; 0 while that is unknown."""

    def report(seconds: float, duration: float | None) -> None:
        if duration:
            progress.on_progress(round(max(0.0, min(1.0, seconds / duration)) * 100), "")

    return report
