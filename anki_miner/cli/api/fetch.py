"""fetch: YouTube videos into run folders, ready for mine (API.md, "fetch").

Per episode, what Video -> YouTube does before mining: probe, classify,
download, then local transcription or caption alignment, into
``<run_dir>/<run_id>/fetch-<n>/``. Nothing goes to Anki and no lock is held: a
fetch writes only its run folder and the API log (yt-dlp keeps its own cache
outside Anki Miner's data folder, and the speech model is only read).
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import cast

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import (
    BAD_RUN_FILE,
    CANCELLED,
    FETCH_FAILED,
    INTERNAL,
    SETUP_ERROR,
    YOUTUBE_REFUSED,
    ApiError,
    run_verdict,
)
from anki_miner.cli.api.files import FetchEpisode, FetchFile
from anki_miner.cli.api.runfolder import MEDIA, CancelWatcher, ProgressFile, next_numbered, write_json
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
from anki_miner.models.youtube import FetchedMedia, SubtitleSource, VideoInfo
from anki_miner.services.asr.model_availability import usable_model_installed
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.youtube_fetcher import classify_probe_result
from anki_miner.services.youtube_postfetch import align_fetched, transcribe_fetched
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg
from anki_miner.utils.ytdlp_resolver import ytdlp_available

logger = logging.getLogger(__name__)

NO_YTDLP = "yt-dlp is not installed. Open Anki Miner, go to Video -> YouTube and install it, then try again."
NO_SPEECH_MODEL = (
    "The speech model {model} is not installed. Install it in Anki Miner: Settings -> Transcription & Alignment."
)
_NO_FFMPEG = "ffmpeg was not found. Install ffmpeg, or set its location in Anki Miner's settings."
_CANCELLED = "The run was cancelled."
#: probing, downloading, then transcribing or aligning when the run does either
_STAGES = 3
#: What running again later can get past: a login wall that lifts, a browser
#: that lets go of its cookie database, a slow network.
_TRANSIENT = (BotDetectionError, CookieDatabaseLockedError, YouTubeTimeoutError)
_WORKSPACE = re.compile(r"fetch-\d+")


class _RunFailed(ApiError):
    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(code, message)
        self.transient = transient


def _verdict(run_id: str, *, error: ApiError | None = None, file: str | None = None) -> dict[str, object]:
    transient = isinstance(error, _RunFailed) and error.transient
    return {**run_verdict(run_id, error=error, file=file), "failure_is_transient": transient}


def _failure(exc: YouTubeFetchError) -> _RunFailed:
    return _RunFailed(FETCH_FAILED, str(exc) or type(exc).__name__, transient=isinstance(exc, _TRANSIENT))


def fetch_runs(job: FetchFile, cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *job* fetched in turn; a fetch-<n>.json for each that succeeds."""
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    if not ytdlp_available(config):
        raise ApiError(SETUP_ERROR, NO_YTDLP)
    if not binary_available(resolve_ffmpeg(config)):
        raise ApiError(SETUP_ERROR, _NO_FFMPEG)
    return [
        _guarded(episode.run_id, partial(_fetch_one, job.run_dir, episode, config, cancel_all))
        for episode in job.episodes
    ]


def _guarded(run_id: str, body: Callable[[], dict[str, object]]) -> dict[str, object]:
    """runs.guarded with failure_is_transient on every verdict."""
    try:
        return body()
    except ApiError as exc:
        return _verdict(run_id, error=exc)
    except Exception as exc:  # noqa: BLE001 — one broken run must not end the call
        logger.exception("API fetch %s failed", run_id)
        return _verdict(run_id, error=ApiError(INTERNAL, f"{type(exc).__name__}: {exc}"))


def _fetch_one(
    run_dir: Path, episode: FetchEpisode, config: AnkiMinerConfig, cancel_all: threading.Event
) -> dict[str, object]:
    folder = run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    # A crash's leftover: a fetch-<n>/ that no fetch-<n>.json names. It goes, and
    # its number with it (next_numbered counts folders).
    for leftover in folder.iterdir():
        if _WORKSPACE.fullmatch(leftover.name) and leftover.is_dir() and not leftover.with_suffix(".json").exists():
            shutil.rmtree(leftover, ignore_errors=True)
    path = next_numbered(folder, "fetch")
    workspace = path.with_suffix("")  # fetch-<n>/ beside fetch-<n>.json
    run_config = replace(config, media_temp_folder=folder / MEDIA)
    try:
        with CancelWatcher(folder, cancel_all) as cancel:
            info, fetched = _fetch(run_config, episode, workspace, cancel, ProgressFile(folder, episode.run_id))
    except BaseException:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(folder / MEDIA, ignore_errors=True)
    write_json(path, _record(episode.run_id, info, fetched))
    return _verdict(episode.run_id, file=path.name)


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
    if sub_mode == "transcribe" and not usable_model_installed(config):
        raise ApiError(SETUP_ERROR, NO_SPEECH_MODEL.format(model=config.asr_model))
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
    fetched = _subtitle(config, fetched, workspace, cancel, progress, align=align)
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
    progress.on_stage(3, _STAGES, "align")
    aligned = align_fetched(config, fetched, workspace, cancel, _percent(progress))
    if aligned is None:
        raise ApiError(CANCELLED, _CANCELLED)
    return aligned


def _percent(progress: ProgressFile) -> Callable[[object, float | None], None]:
    """A fetch or step callback as done/total in percent; its label is the log's, not the file's."""

    def report(_label: object, fraction: float | None) -> None:
        if fraction is not None:
            progress.on_progress(round(max(0.0, min(1.0, fraction)) * 100), "")

    return report


def _record(run_id: str, info: VideoInfo, fetched: FetchedMedia) -> dict[str, object]:
    """fetch-<n>.json: the files, and the overrides a YouTube run sets (process_youtube_url)."""
    return {
        "schema": 1,
        "run_id": run_id,
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
