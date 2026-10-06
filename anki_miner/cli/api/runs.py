"""mine, one episode (run) at a time (API.md, "mine").

A call resolves its settings once, runs the run-level checks once, and shares
one lookup stack across its runs. Every run's media and temp folder live under
``<run_dir>/<run_id>/media`` and are removed before the run ends; the
processor gets no known-words DB and no stats service, so known_words.db and
stats.db are never touched (the caller keeps that record). A dry run
(``Kind.DRY_RUN``) ends at the curation step: nothing is cut and nothing
reaches Anki.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

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
from anki_miner.cli.api.lines import Fates, LineWords, WordSelection, line_merges, named_word
from anki_miner.cli.api.runfolder import MEDIA, CancelWatcher, ProgressFile, next_result_path, write_json
from anki_miner.cli.runner import SetupFailure, check_card_target, check_environment
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError, SubtitleParseError
from anki_miner.gui.utils.service_factory import (
    SharedLookupServices,
    create_episode_processor,
    create_shared_lookup_services,
)
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import (
    CardPayload,
    MediaData,
    MiningOutcome,
    ProcessingResult,
    TokenizedWord,
    classify_result,
)
from anki_miner.presenters.null_presenter import NullPresenter
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.cue_merge import merge_budget_seconds
from anki_miner.utils.audio_track_detector import get_media_duration_seconds, get_primary_video_codec
from anki_miner.utils.ffmpeg_resolver import binary_available, resolve_ffmpeg, resolve_ffprobe

if TYPE_CHECKING:
    from anki_miner.orchestration.episode_processor import EpisodeProcessor

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


class Kind(Enum):
    """What a run does with the words it reaches (API.md): mine them, or only report them."""

    MINE = "mine"
    DRY_RUN = "dry_run"


class _OfflineAnki(AnkiService):
    """A dry run's Anki: no card-target check, and never reached for cards (the selection hands on nothing)."""

    def verify_card_target(self) -> None:
        return None


@contextmanager
def _services(config: AnkiMinerConfig, kind: Kind) -> Iterator[SharedLookupServices]:
    """Run-level checks, then the lookup stack the runs share. A dry run checks neither ffmpeg nor Anki."""
    try:
        check_environment(config)
    except SetupFailure as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    if kind is not Kind.DRY_RUN:
        tools = (("ffmpeg", resolve_ffmpeg(config)), ("ffprobe", resolve_ffprobe(config)))
        missing = [name for name, path in tools if not binary_available(path)]
        if missing:
            raise ApiError(
                SETUP_ERROR,
                f"{' and '.join(missing)} not found. Install ffmpeg, or set its location in Anki Miner's settings.",
            )
    shared = create_shared_lookup_services(config)
    try:
        anki = _OfflineAnki if kind is Kind.DRY_RUN else AnkiService
        try:
            # A dry run's check is the offline dictionary alone.
            check_card_target(config, anki(config), shared.definition_service)
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
    ffprobe = resolve_ffprobe(config)
    # No container duration means "duration unknown" (a live-mode MKV, an
    # interrupted recording), not "cannot be opened": such a file still plays
    # and mines, so a readable video stream is enough.
    if (
        get_media_duration_seconds(episode.video_file, ffprobe) is None
        and get_primary_video_codec(episode.video_file, ffprobe) is None
    ):
        raise ApiError(VIDEO_UNREADABLE, f"The video cannot be opened: {episode.video_file}")


def _removed(processor: EpisodeProcessor, fold: Callable[[str], str] | None, name: str) -> bool:
    """The dictionary check or the duplicate-expression merge removed the word *name* names."""
    losers = [word for word, _winner in processor.last_collapsed]
    return any(named_word(name, words, fold) is not None for words in (processor.last_definition_rejects, losers))


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
    kind: Kind,
) -> _Mined:
    """One process_episode call, media inside the run folder, cleaned up whatever happens.

    The temp folder is ``<run_id>/media/`` for every kind: a run folder serves one call at a time.
    """
    run_config = _episode_config(config, episode, folder)
    dry = kind is Kind.DRY_RUN
    processor = create_episode_processor(
        run_config,
        _LogPresenter(),
        None,
        anki_service=_OfflineAnki(run_config) if dry else AnkiService(run_config),
        shared_lookup=shared,
        with_known_words_db=False,
        run_temp_root=folder / MEDIA,
        extra_whitelist=frozenset(request.word for request in episode.words),
    )
    try:
        parser = processor.subtitle_parser
        try:
            entries = parser.parse_raw_entries(episode.subtitle_file, episode.subtitle_offset)
            # The same lines at the file's own times: line_start is what the caller read there.
            raw = parser.parse_raw_entries(episode.subtitle_file, 0.0)
        except SubtitleParseError as exc:
            raise ApiError(SUBTITLE_UNREADABLE, str(exc)) from exc
        fold = get_profile(config_language(config)).dedup_fold
        line_words = LineWords(
            parse_line=processor.parse_sentence_fn,
            word_on_line=processor.word_on_line,
            with_reading=processor.word_filter.with_reading,
            readings=processor.definition_service.offline_term_readings,
            removed=partial(_removed, processor, fold),
        )
        selection = WordSelection(
            episode.words,
            entries,
            raw,
            line_merges(config, entries),
            merge_budget_seconds(config.audio_padding),
            clean=parser._clean_line_text,  # the cleaner parse_raw_entries applied to the lines
            fold=fold,
            allow_duplicates=run_config.allow_duplicate_cards,
            line_words=line_words,
            dry_run=dry,
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
        succeeded = classify_result(result) is MiningOutcome.SUCCESS
        return _Mined(
            result=result,
            selection=selection,
            fates=Fates(
                created=dict(zip(anki.last_created_mined_forms, anki.last_created_note_ids, strict=True)),
                not_created=dict(anki.last_not_created),
                dropped=dict(processor.last_word_drops),
                rejected=list(processor.last_definition_rejects),
                media_missing=dict(processor.last_media_missing),
                collapsed=list(processor.last_collapsed),
                stopped=not succeeded,
                made=_dry_statuses(run_config, processor, selection) if dry and succeeded else {},
            ),
            media_store_failures=failures if isinstance(failures, int) else 0,
        )
    finally:
        processor.close()
        shutil.rmtree(folder / MEDIA, ignore_errors=True)


def _dry_statuses(config: AnkiMinerConfig, processor: EpisodeProcessor, selection: WordSelection) -> dict[str, str]:
    """A dry run's status per word it would have mined: ready, no_definition or duplicate (API.md)."""
    picked = selection.picked()
    made_here = [word for word, from_line in picked if from_line]
    # A produced word already passed phase 2's dictionary check; a made one never met it.
    undefined = {
        word.mined_form
        for word, ok in zip(made_here, processor.definition_viable(made_here) if made_here else [], strict=True)
        if not ok
    }
    in_anki = _duplicates_in_anki(config, [word for word, _ in picked])
    return {
        word.mined_form: (
            "no_definition" if word.mined_form in undefined else "duplicate" if word.mined_form in in_anki else "ready"
        )
        for word, _ in picked
    }


def _duplicates_in_anki(config: AnkiMinerConfig, words: list[TokenizedWord]) -> set[str]:
    """The words mine's duplicate checks would refuse, when Anki answers: a dry run needs no Anki."""
    if not words:
        return set()
    try:
        return AnkiService(config).duplicate_fronts(
            [CardPayload(word=w, media=MediaData(), definition="") for w in words]
        )
    except AnkiConnectionError:
        return set()


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


def mine_runs(run_file: RunFile, cancel_all: threading.Event, kind: Kind = Kind.MINE) -> list[dict[str, object]]:
    """Every episode of *run_file* mined in turn; a result-<n>.json for each that got as far as mining."""
    config = settings.resolve_run_config(run_file.profile, run_file.language, run_file.overlay)
    with _services(config, kind) as shared:
        return [
            _guarded(episode.run_id, partial(_mine_one, run_file.run_dir, episode, config, shared, cancel_all, kind))
            for episode in run_file.episodes
        ]


def _mine_one(
    run_dir: Path,
    episode: Episode,
    config: AnkiMinerConfig,
    shared: SharedLookupServices,
    cancel_all: threading.Event,
    kind: Kind,
) -> dict[str, object]:
    folder = run_dir / episode.run_id
    folder.mkdir(exist_ok=True)
    if cancel_all.is_set():
        raise ApiError(CANCELLED, "Cancelled before this run started.")
    if kind is not Kind.DRY_RUN:  # a dry run cuts nothing from the video
        _check_video(config, episode)
    mined = _mine(config, episode, folder, shared, cancel_all, kind)
    error = _outcome_error(mined.result)
    path = next_result_path(folder)
    write_json(
        path,
        {
            "schema": 1,
            "run_id": episode.run_id,
            "dry_run": kind is Kind.DRY_RUN,
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
