"""retime: a subtitle fitted to its video, as a copy (API.md, "retime").

Per episode, what Utilities -> Retime does for one pair: the subtitle is
aligned against a subtitle already timed to the video (the caller's
``reference_subtitle``, else the video's own dialogue track), or else the
video's audio, and the engines' fit is checked before it is kept. The result
is ``<run_dir>/<run_id>/retime-<n>/<subtitle file name>``; the caller's file
is only read. Nothing goes to Anki and no lock is held.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

import pysubs2

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import BAD_RUN_FILE, CANCELLED, SUBTITLE_UNREADABLE, ApiError
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.runs import check_video, require_ffmpeg
from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.audio_condenser import load_subtitle_events
from anki_miner.services.subtitle_retimer import RetimeOutcome, retime_subtitle
from anki_miner.utils.subtitle_encoding import script_check_kwarg

_KEYS = frozenset({"run_id", "video_file", "subtitle_file", "reference_subtitle"})


@dataclass(frozen=True)
class RetimeEpisode:
    run_id: str
    video_file: Path
    subtitle_file: Path
    #: a subtitle already timed to the video; None takes the video's own track, then its audio
    reference_subtitle: Path | None


def _episode(raw: object, where: str) -> RetimeEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, frozenset({"run_id", "video_file", "subtitle_file"}), where)
    return RetimeEpisode(
        run_id=files.valid_run_id(obj["run_id"], f"{where}.run_id"),
        video_file=files.abs_path(files.text(obj["video_file"], f"{where}.video_file")),
        subtitle_file=files.abs_path(files.text(obj["subtitle_file"], f"{where}.subtitle_file")),
        reference_subtitle=files.opt_path(files.opt_text(obj.get("reference_subtitle"), f"{where}.reference_subtitle")),
    )


def call(retime_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``retime RETIME_FILE``: the file checked, then its runs."""
    return retime_runs(files.parse_jobs_file(files.read_json_file(retime_file), "retime", _episode), cancel_all)


def retime_runs(job: JobsFile[RetimeEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode retimed in turn; a retime-<n>.json for each that finishes, fitted or not."""
    return job_runs(job, "retime", cancel_all, partial(_prepare, job), _retime_one)


def _prepare(job: JobsFile[RetimeEpisode]) -> AnkiMinerConfig:
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    require_ffmpeg(config)
    return config


def _readable(path: Path, encodings: tuple[str, ...] | None, script: object) -> None:
    """SUBTITLE_UNREADABLE unless *path* loads with at least one cue."""
    try:
        cues = load_subtitle_events(path, encodings=encodings, **script_check_kwarg(encodings, script))
    except (OSError, UnicodeError, ValueError, pysubs2.Pysubs2Error) as exc:
        raise ApiError(SUBTITLE_UNREADABLE, f"The subtitle file cannot be read: {path} ({exc})") from exc
    if not cues:
        raise ApiError(SUBTITLE_UNREADABLE, f"The subtitle file has no lines: {path}")


def _retime_one(config: AnkiMinerConfig, episode: RetimeEpisode, run: Run) -> dict[str, object]:
    run.progress.on_stage(1, 1, "retime")
    check_video(config, episode.video_file)
    profile = get_profile(config_language(config))
    # The subtitle is read in the mining language, as the retime itself reads it; the
    # reference may be another language's, and only its times count.
    _readable(episode.subtitle_file, profile.import_encodings, profile.script)
    if episode.reference_subtitle is not None:
        _readable(episode.reference_subtitle, None, None)
    run.out.mkdir()
    out_sub = run.out / episode.subtitle_file.name
    # An embedded track picked as the reference is extracted into the run's temp folder.
    outcome = retime_subtitle(
        replace(config, media_temp_folder=run.media),
        episode.video_file,
        episode.subtitle_file,
        out_sub,
        reference_subtitle=episode.reference_subtitle,
        cancel_event=run.cancel,
    )
    if outcome.cancelled:
        raise ApiError(CANCELLED, "The run was cancelled.")
    return _record(outcome, out_sub)


def _record(outcome: RetimeOutcome, out_sub: Path) -> dict[str, object]:
    """retime-<n>.json. One shift (ffsubsync's offset, or alass moving the whole file as one block) is
    ``offset_seconds``; alass's per-block shifts are listed as they are."""
    shifts = outcome.block_shifts_seconds
    offset = outcome.offset_seconds
    if offset is None and len(shifts) == 1:
        offset = shifts[0]
    return {
        "subtitle_file": str(out_sub) if outcome.ok else None,
        "status": "retimed" if outcome.ok else "no_fit",
        "engine": outcome.engine,
        "offset_seconds": offset,
        "framerate_scale": outcome.framerate_scale,
        "block_shifts_seconds": list(shifts),
        "message": None if outcome.ok else outcome.reason,
    }
