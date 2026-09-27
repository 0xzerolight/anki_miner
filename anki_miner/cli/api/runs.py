"""mine, one episode (run) at a time (API.md, "mine").

A call resolves its settings once, runs the run-level checks once, and shares
one lookup stack across its runs. Every run's media and temp folder live under
``<run_dir>/<run_id>/media`` and are removed before the run ends; the
processor gets no known-words DB and no stats service, so known_words.db and
stats.db are never touched (the caller keeps that record).
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from anki_miner.cli.api import settings
from anki_miner.cli.api.contract import (
    CANCELLED,
    INTERNAL,
    MINING_FAILED,
    SETUP_ERROR,
    SUBTITLE_UNREADABLE,
    VIDEO_UNREADABLE,
    ApiError,
    run_verdict,
    setup_failure,
)
from anki_miner.cli.api.files import Episode, RunFile
from anki_miner.cli.api.lines import Fates, WordSelection, line_merges
from anki_miner.cli.api.runfolder import MEDIA, CancelWatcher, ProgressFile, next_result_path, write_json
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
from anki_miner.services.cue_merge import merge_budget_seconds
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg, resolve_ffprobe

logger = logging.getLogger(__name__)


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
    selection: WordSelection
    fates: Fates
    media_store_failures: int


def _mine(
    config: AnkiMinerConfig,
    episode: Episode,
    folder: Path,
    shared: SharedLookupServices,
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
        parser = processor.subtitle_parser
        try:
            entries = parser.parse_raw_entries(episode.subtitle_file, episode.subtitle_offset)
            # The same lines at the file's own times: line_start is what the caller read there.
            raw = parser.parse_raw_entries(episode.subtitle_file, 0.0)
        except SubtitleParseError as exc:
            raise ApiError(SUBTITLE_UNREADABLE, str(exc)) from exc
        selection = WordSelection(
            episode.words, entries, raw, line_merges(config, entries), merge_budget_seconds(config.audio_padding)
        )
        with CancelWatcher(folder, cancel_all) as cancel:
            try:
                result = processor.process_episode(
                    episode.video_file,
                    episode.subtitle_file,
                    progress_callback=ProgressFile(folder, episode.run_id),
                    curation_callback=selection,
                    cancel_event=cancel,
                    **episode.process_kwargs(),  # type: ignore[arg-type]
                )
            except (AnkiConnectionError, SetupError) as exc:  # the processor's own preflight
                raise setup_failure(exc) from exc
        anki = processor.anki_service
        failures = anki.last_media_store_failures
        return _Mined(
            result=result,
            selection=selection,
            fates=Fates(
                created=dict(zip(anki.last_created_mined_forms, anki.last_created_note_ids, strict=True)),
                not_created=dict(anki.last_not_created),
                dropped=dict(processor.last_word_drops),
                rejected=list(processor.last_definition_rejects),
                media_missing=dict(processor.last_media_missing),
                stopped=classify_result(result) is not MiningOutcome.SUCCESS,
            ),
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


def mine_runs(run_file: RunFile, cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every episode of *run_file* mined in turn; a result-<n>.json for each that got as far as mining."""
    config = settings.resolve_run_config(run_file.profile, run_file.language, run_file.overlay)
    with _services(config) as shared:
        return [
            _guarded(episode.run_id, partial(_mine_one, run_file.run_dir, episode, config, shared, cancel_all))
            for episode in run_file.episodes
        ]


def _mine_one(
    run_dir: Path,
    episode: Episode,
    config: AnkiMinerConfig,
    shared: SharedLookupServices,
    cancel_all: threading.Event,
) -> dict[str, object]:
    folder = run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    _check_video(config, episode)
    mined = _mine(config, episode, folder, shared, cancel_all)
    error = _outcome_error(mined.result)
    path = next_result_path(folder)
    write_json(
        path,
        {
            "schema": 1,
            "run_id": episode.run_id,
            "outcome": classify_result(mined.result).value,
            "anki_write_state": mined.result.anki_write_state.value,
            "failure_is_transient": bool(mined.result.failure_is_transient),
            "error": error.code if error else None,
            "message": error.message if error else None,
            "media_store_failures": mined.media_store_failures,
            "words": mined.selection.report(mined.fates),
        },
    )
    return run_verdict(episode.run_id, error=error, file=path.name)
