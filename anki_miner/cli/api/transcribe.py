"""transcribe: a subtitle for a video or audio file that has none (API.md, "transcribe").

Per episode, what Utilities -> Generate does for one file: the speech model
transcribes it into ``<run_dir>/<run_id>/transcribe-<n>/<stem>.srt``. Nothing
goes to Anki and no lock is held: a transcription writes only its run folder,
the API log and the transcript cache it shares with the window (a second
transcription of the same file reads it back).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import (
    AUDIO_UNREADABLE,
    BAD_RUN_FILE,
    CANCELLED,
    SETUP_ERROR,
    VIDEO_UNREADABLE,
    ApiError,
)
from anki_miner.cli.api.files import E, JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.readiness import speech_problem
from anki_miner.cli.api.runfolder import ProgressFile
from anki_miner.cli.api.runs import check_video, require_ffmpeg
from anki_miner.cli.api.sources import check_audio
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.asr.model_manager import decode_language
from anki_miner.services.asr.subtitle_generation import SubtitleGenStatus, generate_subtitle_one
from anki_miner.services.media_extractor import MediaExtractorService

CANCELLED_MESSAGE = "The run was cancelled."
_KEYS = frozenset({"run_id", "video_file", "audio_file"})
#: extracting the audio, then transcribing it
_STAGES = 2


@dataclass(frozen=True)
class TranscribeEpisode:
    run_id: str
    #: The one file to transcribe, and whether it is a video (else an audio file).
    media_file: Path
    is_video: bool


def _episode(raw: object, where: str) -> TranscribeEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, _KEYS, frozenset({"run_id"}), where)
    video = files.opt_text(obj.get("video_file"), f"{where}.video_file")
    audio = files.opt_text(obj.get("audio_file"), f"{where}.audio_file")
    media = video if video is not None else audio
    if media is None or (video is not None and audio is not None):
        raise files.bad(f"{where} must name exactly one of video_file and audio_file.")
    return TranscribeEpisode(
        files.valid_run_id(obj["run_id"], f"{where}.run_id"), files.abs_path(media), video is not None
    )


def call(transcribe_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``transcribe TRANSCRIBE_FILE``: the file checked, then its runs."""
    job = files.parse_jobs_file(files.read_json_file(transcribe_file), "transcribe", _episode)
    return transcribe_runs(job, cancel_all)


def transcribe_runs(job: JobsFile[TranscribeEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *job* transcribed in turn; a transcribe-<n>.json for each that ends."""
    return job_runs(job, "transcribe", cancel_all, partial(prepare_speech, job), _transcribe_one)


def prepare_speech(job: JobsFile[E]) -> AnkiMinerConfig:
    """A speech job's call-level checks (``transcribe``, ``sync``): the settings, the speech model, ffmpeg.

    A model that cannot transcribe the mining language (Cantonese on ``small``)
    refuses the call here, before any run, as a missing model does.
    """
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    if (problem := speech_problem(config)) is not None:
        raise ApiError(SETUP_ERROR, problem)
    try:
        decode_language(config.asr_model, asr_language(config))
    except SetupError as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    require_ffmpeg(config)
    return config


def asr_language(config: AnkiMinerConfig) -> str:
    """The language the speech model listens for: the mining language's."""
    return get_profile(config_language(config)).asr_language


def _transcribe_one(config: AnkiMinerConfig, episode: TranscribeEpisode, run: Run) -> dict[str, object]:
    run_config = replace(config, media_temp_folder=run.media)
    media = episode.media_file
    if episode.is_video:
        check_video(run_config, media)
    else:
        check_audio(run_config, media)
    out_srt = run.out / f"{media.stem}.srt"  # generate_subtitle_one makes the folder only for a subtitle
    run.progress.on_stage(1, _STAGES, "extract")
    transcribing = transcribing_progress(run.progress, 2, _STAGES)
    result = generate_subtitle_one(
        run_config,
        MediaExtractorService(run_config),
        media,
        out_srt,
        on_transcribe_start=lambda: transcribing(0.0),
        transcribe_progress_cb=transcribing,
        cancel_event=run.cancel,
        language=asr_language(config),
    )
    if result.status is SubtitleGenStatus.CANCELLED:
        raise ApiError(CANCELLED, CANCELLED_MESSAGE)
    if result.status is SubtitleGenStatus.EXTRACTION_FAILED:
        code = VIDEO_UNREADABLE if episode.is_video else AUDIO_UNREADABLE
        raise ApiError(code, f"The audio could not be extracted from {media}")
    if result.status is SubtitleGenStatus.NO_SPEECH:
        return {"subtitle_file": None, "status": "no_speech"}
    return {"subtitle_file": str(out_srt), "status": "generated"}


def transcribing_progress(progress: ProgressFile, stage: int, stages: int) -> Callable[[float], None]:
    """The transcriber's fraction as done/total in percent, opening *stage* on its first call.

    A transcript the cache had skips extraction and its start callback: the
    stage opens on its one progress call (1.0) instead.
    """
    opened = False

    def report(fraction: float) -> None:
        nonlocal opened
        if not opened:
            opened = True
            progress.on_stage(stage, stages, "transcribe")
            progress.on_start(100, "")
        progress.on_progress(round(max(0.0, min(1.0, fraction)) * 100), "")

    return report
