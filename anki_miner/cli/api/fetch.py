"""fetch: YouTube videos into run folders, ready for mine (API.md, "fetch").

Per episode, what Video -> YouTube does before mining: probe, classify,
download, then local transcription or caption alignment, into
``<run_dir>/<run_id>/fetch-<n>/``. Nothing goes to Anki and no lock is held: a
fetch writes only its run folder and the API log (yt-dlp keeps its own cache
outside Anki Miner's data folder, and the speech model is only read).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import cast

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import (
    BAD_RUN_FILE,
    CANCELLED,
    FETCH_FAILED,
    SETUP_ERROR,
    YOUTUBE_REFUSED,
    ApiError,
)
from anki_miner.cli.api.files import FetchEpisode, JobsFile
from anki_miner.cli.api.jobs import Run, RunFailed, job_runs
from anki_miner.cli.api.readiness import speech_problem
from anki_miner.cli.api.runfolder import ProgressFile
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    VideoTooLongError,
    YouTubeFetchError,
    YouTubeTimeoutError,
    YtdlpNotFoundError,
)
from anki_miner.gui.utils.service_factory import create_youtube_fetcher
from anki_miner.models.youtube import FetchedMedia, SubMode, SubtitleSource, VideoInfo
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.youtube_fetcher import classify_probe_result
from anki_miner.services.youtube_postfetch import align_fetched, transcribe_fetched
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg
from anki_miner.utils.ytdlp_resolver import ytdlp_available

NO_YTDLP = "yt-dlp is not installed. Open Anki Miner, go to Video -> YouTube and install it, then try again."
_NO_FFMPEG = "ffmpeg was not found. Install ffmpeg, or set its location in Anki Miner's settings."
_CANCELLED = "The run was cancelled."
#: probing, downloading, then transcribing or aligning when the run does either
_STAGES = 3
#: What running again later can get past: a login wall that lifts, a browser
#: that lets go of its cookie database, a slow network.
_TRANSIENT = (BotDetectionError, CookieDatabaseLockedError, YouTubeTimeoutError)


def _failure(exc: YouTubeFetchError) -> RunFailed:
    return RunFailed(FETCH_FAILED, str(exc) or type(exc).__name__, transient=isinstance(exc, _TRANSIENT))


def call(fetch_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``fetch FETCH_FILE``: the file checked, then its runs."""
    return fetch_runs(files.parse_fetch_file(files.read_json_file(fetch_file)), cancel_all)


def fetch_runs(job: JobsFile[FetchEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *job* fetched in turn; a fetch-<n>.json for each that succeeds."""
    return job_runs(job, "fetch", cancel_all, partial(_prepare, job), _fetch_one)


def _prepare(job: JobsFile[FetchEpisode]) -> AnkiMinerConfig:
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    if not ytdlp_available(config):
        raise ApiError(SETUP_ERROR, NO_YTDLP)
    if not binary_available(resolve_ffmpeg(config)):
        raise ApiError(SETUP_ERROR, _NO_FFMPEG)
    return config


def _fetch_one(config: AnkiMinerConfig, episode: FetchEpisode, run: Run) -> dict[str, object]:
    run_config = replace(config, media_temp_folder=run.media)
    info, fetched = _fetch(run_config, episode, run.out, run.cancel, run.progress)
    return _record(info, fetched)


def _fetch(
    config: AnkiMinerConfig,
    episode: FetchEpisode,
    workspace: Path,
    cancel: threading.Event,
    progress: ProgressFile,
) -> tuple[VideoInfo, FetchedMedia]:
    fetcher = create_youtube_fetcher(config)
    progress.on_stage(1, _STAGES, "probe")
    try:
        info = fetcher.probe_metadata(episode.youtube_url)
    except VideoTooLongError as exc:
        raise ApiError(YOUTUBE_REFUSED, str(exc)) from exc
    except YtdlpNotFoundError as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    except YouTubeFetchError as exc:
        if cancel.is_set():  # the probe takes no cancel, but one can land while it runs
            raise ApiError(CANCELLED, _CANCELLED) from exc
        raise _failure(exc) from exc
    source = cast(SubtitleSource, episode.subtitle_source or config.youtube_subtitle_source)
    mineable, reason, sub_mode = classify_probe_result(info, config, source)
    if not mineable or sub_mode is None:
        raise ApiError(YOUTUBE_REFUSED, reason or "This video cannot be fetched.")
    if sub_mode == "transcribe" and (problem := speech_problem(config)) is not None:
        raise ApiError(SETUP_ERROR, problem)
    if cancel.is_set():
        raise ApiError(CANCELLED, _CANCELLED)
    # 0o700 like allocate_youtube_workspace: a cookie-authenticated download never lands world-readable.
    workspace.mkdir(mode=0o700)
    progress.on_stage(2, _STAGES, "download")
    progress.on_start(100, "")
    try:
        fetched = fetcher.fetch_video(
            episode.youtube_url,
            info.video_id,
            workspace,
            sub_mode,
            progress_cb=_percent(progress),
            cancel_event=cancel,
            fallback_allowed=info.has_auto_ja_subs,
        )
    except (FfmpegNotFoundError, YtdlpNotFoundError) as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    except YouTubeFetchError as exc:
        if cancel.is_set():
            raise ApiError(CANCELLED, _CANCELLED) from exc
        raise _failure(exc) from exc
    # The fetcher raises only for a cancel it saw itself; one that landed as the download ended stops here.
    if cancel.is_set():
        raise ApiError(CANCELLED, _CANCELLED)
    align = config.youtube_align_captions if episode.align_captions is None else episode.align_captions
    fetched = _subtitle(config, fetched, workspace, cancel, progress, align=align, sub_mode=sub_mode)
    if cancel.is_set():
        raise ApiError(CANCELLED, _CANCELLED)
    return info, fetched


def _subtitle(
    config: AnkiMinerConfig,
    fetched: FetchedMedia,
    workspace: Path,
    cancel: threading.Event,
    progress: ProgressFile,
    *,
    align: bool,
    sub_mode: SubMode,
) -> FetchedMedia:
    """Transcription for a caption-less download, else alignment when asked: process_youtube_url's order."""
    if fetched.subtitle_file is None:
        progress.on_stage(3, _STAGES, "transcribe")
        progress.on_start(100, "")
        try:
            fetched = transcribe_fetched(
                config, MediaExtractorService(config), fetched, workspace, cancel, _percent(progress)
            )
        except YouTubeFetchError as exc:  # TranscriptionFailedError, TranscriptionProducedNothingError
            raise _failure(exc) from exc
        if fetched.subtitle_file is None:
            raise ApiError(CANCELLED, _CANCELLED)
        return fetched
    if not align:
        return fetched

    def stage(_step: object, _fraction: float | None) -> None:
        # Opened by align_fetched itself: native auto-captions come back unaligned and end at stage 2.
        progress.on_stage(3, _STAGES, "align")

    aligned = align_fetched(config, fetched, workspace, cancel, stage, sub_mode=sub_mode)
    if aligned is None:
        raise ApiError(CANCELLED, _CANCELLED)
    return aligned


def _percent(progress: ProgressFile) -> Callable[[object, float | None], None]:
    """A fetch or step callback as done/total in percent; its label is the log's, not the file's."""

    def report(_label: object, fraction: float | None) -> None:
        if fraction is not None:
            progress.on_progress(round(max(0.0, min(1.0, fraction)) * 100), "")

    return report


def _record(info: VideoInfo, fetched: FetchedMedia) -> dict[str, object]:
    """fetch-<n>.json: the files, and the overrides a YouTube run sets (process_youtube_url)."""
    return {
        "video_file": str(fetched.video_file),
        "subtitle_file": str(fetched.subtitle_file),
        "sub_source": fetched.sub_source,
        "video_id": info.video_id,
        "title": info.title,
        "duration": info.duration_s,
        "episode_name_override": f"YT:{info.video_id}",
        "series_name_override": "YouTube",
        "source_label_override": info.title,
    }
