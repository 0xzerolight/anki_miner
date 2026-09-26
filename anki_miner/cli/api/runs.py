"""prepare and commit, one episode (run) at a time (proposal, "The flow").

Each call resolves settings once per distinct saved config, runs the run-level
checks once, and shares one lookup stack across its runs. Every run's media
and temp folder live under ``<run_dir>/<run_id>/media`` and are removed before
the run ends; the processor gets no known-words DB and no stats service, so
known_words.db and stats.db are never touched (the caller keeps that record).
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import (
    BAD_LINE,
    CANCELLED,
    INTERNAL,
    MINING_FAILED,
    RUN_STALE,
    SETUP_ERROR,
    SUBTITLE_UNREADABLE,
    UNKNOWN_RUN,
    VIDEO_UNREADABLE,
    ApiError,
    run_verdict,
    setup_failure,
)
from anki_miner.cli.api.files import CommitFile, CommitRun, Episode, RunFile, parse_episode
from anki_miner.cli.api.lines import CandidateCapture, PickSelection, candidates_file, line_merges, validate_picks
from anki_miner.cli.api.runfolder import (
    CANDIDATES,
    MEDIA,
    SAVED_RUN,
    CancelWatcher,
    ProgressFile,
    SavedRun,
    file_stamp,
    next_result_path,
    read_json,
    reset_run_folder,
    write_json,
)
from anki_miner.cli.runner import SetupFailure, check_card_target, check_environment
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError, SubtitleParseError
from anki_miner.gui.utils.service_factory import (
    SharedLookupServices,
    create_episode_processor,
    create_shared_lookup_services,
)
from anki_miner.models import MiningOutcome, ProcessingResult, classify_result
from anki_miner.presenters.null_presenter import NullPresenter
from anki_miner.services.anki_service import AnkiService
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg, resolve_ffprobe

logger = logging.getLogger(__name__)

Entries = list[tuple[float, float, str]]


class _LogPresenter(NullPresenter):
    """Pipeline messages go to the API log; nothing reaches the caller but files and the verdict."""

    def show_info(self, message: str) -> None:
        logger.info("%s", message)

    def show_success(self, message: str) -> None:
        logger.info("%s", message)

    def show_warning(self, message: str) -> None:
        logger.warning("%s", message)

    def show_error(self, message: str) -> None:
        logger.error("%s", message)


@contextmanager
def _services(config: AnkiMinerConfig) -> Iterator[SharedLookupServices]:
    """Run-level checks, then the lookup stack the runs share."""
    try:
        check_environment(config)
    except SetupFailure as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    tools = (("ffmpeg", resolve_ffmpeg(config)), ("ffprobe", resolve_ffprobe(config)))
    missing = [name for name, path in tools if not binary_available(path)]
    if missing:
        raise ApiError(
            SETUP_ERROR,
            f"{' and '.join(missing)} not found. Install ffmpeg, or set its location in Anki Miner's settings.",
        )
    shared = create_shared_lookup_services(config)
    try:
        try:
            check_card_target(config, AnkiService(config), shared.definition_service)
        except SetupFailure as exc:
            raise ApiError(SETUP_ERROR, str(exc)) from exc
        except (AnkiConnectionError, ValueError) as exc:  # ValueError: AnkiService refuses incomplete anki_fields
            raise setup_failure(exc) from exc
        yield shared
    finally:
        shared.close()


def _index_stamps(config: AnkiMinerConfig, shared: SharedLookupServices) -> list[list[object]]:
    """The enabled, usable dictionary and frequency indexes, identified by id, size and mtime."""
    found: list[tuple[str, str, Path]] = []
    if shared.dictionary_registry is not None:
        found += [("dictionary", m.dict_id, m.db_path) for m in shared.dictionary_registry.usable_enabled(config)]
    if shared.frequency_registry is not None:
        found += [("frequency", m.source_id, m.db_path) for m in shared.frequency_registry.usable_enabled(config)]
    return sorted(([family, slot, file_stamp(path)] for family, slot, path in found), key=json.dumps)


def _episode_config(config: AnkiMinerConfig, episode: Episode, folder: Path) -> AnkiMinerConfig:
    """This episode's tags added, and its media kept in its run folder."""
    tags = " ".join(part for part in (config.anki_tags.strip(), episode.tags.strip()) if part)
    return replace(config, anki_tags=tags, media_temp_folder=folder / MEDIA)


def _check_video(config: AnkiMinerConfig, episode: Episode) -> None:
    if get_media_duration_seconds(episode.video_file, resolve_ffprobe(config)) is None:
        raise ApiError(VIDEO_UNREADABLE, f"The video cannot be opened: {episode.video_file}")


@dataclass
class _Mined:
    result: ProcessingResult
    callback: Any
    created: dict[str, int]
    media_missing: dict[str, list[str]]
    media_store_failures: int


def _mine(
    config: AnkiMinerConfig,
    episode: Episode,
    folder: Path,
    shared: SharedLookupServices,
    make_callback: Callable[[Entries], Any],
    cancel_all: threading.Event,
) -> _Mined:
    """One process_episode call, media inside the run folder, cleaned up whatever happens."""
    run_config = _episode_config(config, episode, folder)
    processor = create_episode_processor(
        run_config,
        _LogPresenter(),
        None,
        anki_service=AnkiService(run_config),
        shared_lookup=shared,
        with_known_words_db=False,
        run_temp_root=folder / MEDIA,
    )
    try:
        try:
            entries = processor.subtitle_parser.parse_raw_entries(episode.subtitle_file, episode.subtitle_offset)
        except SubtitleParseError as exc:
            raise ApiError(SUBTITLE_UNREADABLE, str(exc)) from exc
        callback = make_callback(entries)
        with CancelWatcher(folder, cancel_all) as cancel:
            try:
                result = processor.process_episode(
                    episode.video_file,
                    episode.subtitle_file,
                    progress_callback=ProgressFile(folder, episode.run_id),
                    curation_callback=callback,
                    cancel_event=cancel,
                    **episode.process_kwargs(),  # type: ignore[arg-type]
                )
            except (AnkiConnectionError, SetupError) as exc:  # the processor's own preflight
                raise setup_failure(exc) from exc
        anki = processor.anki_service
        failures = anki.last_media_store_failures
        return _Mined(
            result=result,
            callback=callback,
            created=dict(zip(anki.last_created_mined_forms, anki.last_created_note_ids, strict=True)),
            media_missing=dict(processor.last_media_missing),
            media_store_failures=failures if isinstance(failures, int) else 0,
        )
    finally:
        processor.close()
        shutil.rmtree(folder / MEDIA, ignore_errors=True)


def _outcome_error(result: ProcessingResult) -> ApiError | None:
    outcome = classify_result(result)
    if outcome is MiningOutcome.CANCELLED:
        return ApiError(CANCELLED, "The run was cancelled.")
    if outcome is MiningOutcome.FAILED:
        return ApiError(MINING_FAILED, "; ".join(result.errors) or "The run failed.")
    return None


def _guarded(run_id: str, body: Callable[[], dict[str, object]]) -> dict[str, object]:
    """One run's verdict: its own ApiError, or INTERNAL for anything unexpected (logged)."""
    try:
        return body()
    except ApiError as exc:
        return run_verdict(run_id, error=exc)
    except Exception as exc:  # noqa: BLE001 — one broken run must not end the call
        logger.exception("API run %s failed", run_id)
        return run_verdict(run_id, error=ApiError(INTERNAL, f"{type(exc).__name__}: {exc}"))


# ---- prepare -------------------------------------------------------------


def prepare_runs(run_file: RunFile, cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *run_file* through phases 1-2; its candidates.json and saved run on success."""
    config = settings.resolve_run_config(run_file.profile, run_file.language, run_file.overlay)
    with _services(config) as shared:
        return [
            _guarded(episode.run_id, partial(_prepare_one, run_file, episode, config, shared, cancel_all))
            for episode in run_file.episodes
        ]


def _prepare_one(
    run_file: RunFile,
    episode: Episode,
    config: AnkiMinerConfig,
    shared: SharedLookupServices,
    cancel_all: threading.Event,
) -> dict[str, object]:
    folder = run_file.run_dir / episode.run_id
    reset_run_folder(folder)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    _check_video(config, episode)
    capture = CandidateCapture()
    seen: list[Entries] = []

    def make(entries: Entries) -> CandidateCapture:
        seen.append(entries)
        return capture

    mined = _mine(config, episode, folder, shared, make, cancel_all)
    error = _outcome_error(mined.result)
    if error is not None:
        raise error
    [entries] = seen
    write_json(
        folder / CANDIDATES, candidates_file(episode.run_id, entries, line_merges(config, entries), capture.words)
    )
    # The run-level settings: the episode's own tags are in the saved episode,
    # which commit re-reads rather than compares.
    saved = SavedRun.capture(
        profile=run_file.profile,
        language=run_file.language,
        overlay=run_file.overlay,
        episode=episode,
        config_view=settings.staleness_view(config),
        indexes=_index_stamps(config, shared),
    )
    write_json(folder / SAVED_RUN, saved.to_json())
    return run_verdict(episode.run_id, file=CANDIDATES)


# ---- commit --------------------------------------------------------------


def commit_runs(commit_file: CommitFile, cancel_all: threading.Event) -> list[dict[str, object]]:
    """Each named run's words mined; a result-<n>.json per run that got as far as mining."""
    with ExitStack() as stack:
        cache: dict[str, SharedLookupServices | ApiError] = {}

        def shared_for(config: AnkiMinerConfig) -> SharedLookupServices:
            key = json.dumps(settings.staleness_view(config), sort_keys=True, default=str)
            if key not in cache:
                try:
                    cache[key] = stack.enter_context(_services(config))
                except ApiError as exc:
                    cache[key] = exc
            found = cache[key]
            if isinstance(found, ApiError):
                raise found
            return found

        return [
            _guarded(run.run_id, partial(_commit_one, commit_file.run_dir, run, shared_for, cancel_all))
            for run in commit_file.runs
        ]


def _commit_one(
    run_dir: Path,
    run: CommitRun,
    shared_for: Callable[[AnkiMinerConfig], SharedLookupServices],
    cancel_all: threading.Event,
) -> dict[str, object]:
    folder = run_dir / run.run_id
    saved = SavedRun.read(folder)
    candidates = read_json(folder / CANDIDATES)
    if saved is None or not isinstance(candidates, dict):
        raise ApiError(UNKNOWN_RUN, f"No prepared run {run.run_id!r} in {run_dir}.")
    bad = validate_picks(run.words, candidates.get("candidates") or [])
    if bad is not None:
        raise ApiError(BAD_LINE, bad)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    episode = parse_episode(saved.episode, "the saved episode")
    config = settings.resolve_run_config(saved.profile, saved.language, saved.overlay)
    shared = shared_for(config)
    _check_video(config, episode)
    now = SavedRun.capture(
        profile=saved.profile,
        language=saved.language,
        overlay=saved.overlay,
        episode=episode,
        config_view=settings.staleness_view(config),
        indexes=_index_stamps(config, shared),
    )
    stale = saved.stale_reason(now)
    if stale is not None:
        raise ApiError(RUN_STALE, stale)

    def make(entries: Entries) -> PickSelection:
        return PickSelection(run.words, entries, line_merges(config, entries))

    mined = _mine(config, episode, folder, shared, make, cancel_all)
    error = _outcome_error(mined.result)
    path = next_result_path(folder)
    write_json(
        path,
        {
            "schema": 1,
            "run_id": run.run_id,
            "outcome": classify_result(mined.result).value,
            "anki_write_state": mined.result.anki_write_state.value,
            "failure_is_transient": bool(mined.result.failure_is_transient),
            "error": error.code if error else None,
            "message": error.message if error else None,
            "media_store_failures": mined.media_store_failures,
            "words": mined.callback.report(mined.created, mined.media_missing),
        },
    )
    return run_verdict(run.run_id, error=error, file=path.name)
