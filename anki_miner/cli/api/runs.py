"""mine, one episode (run) at a time (API.md, "mine").

A call resolves its settings once, runs the run-level checks once, and shares
one lookup stack across its runs. Every run's media and temp folder live under
``<run_dir>/<run_id>/media`` and are removed before the run ends; the
processor gets no known-words DB and no stats service, so known_words.db and
stats.db are never touched (the caller keeps that record). A dry run
(``Kind.DRY_RUN``) ends at the curation step: nothing is cut and nothing
reaches Anki. A render (``Kind.RENDER``) runs to the notes and writes them,
with their media, to ``<run_id>/render-<n>/`` and ``render-<n>.json``
instead of Anki.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable, Iterator, Mapping
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
from anki_miner.cli.api.render import Rendered, RenderService
from anki_miner.cli.api.runfolder import (
    MEDIA,
    CancelWatcher,
    ProgressFile,
    clear_leftovers,
    next_numbered,
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
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import (
    CANCELLED_ERROR,
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
from anki_miner.services.validation_service import ValidationService
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
    """What a run does with the words it reaches (API.md): mine them, only report them, or render their notes."""

    MINE = "mine"
    DRY_RUN = "dry_run"
    RENDER = "render"


class _OfflineAnki(AnkiService):
    """A dry run's Anki: no card-target check, and never reached for cards (the selection hands on nothing)."""

    def verify_card_target(self) -> None:
        return None


def require_ffmpeg(config: AnkiMinerConfig) -> None:
    """SETUP_ERROR unless both ffmpeg and ffprobe run."""
    tools = (("ffmpeg", resolve_ffmpeg(config)), ("ffprobe", resolve_ffprobe(config)))
    missing = [name for name, path in tools if not binary_available(path)]
    if missing:
        raise ApiError(
            SETUP_ERROR,
            f"{' and '.join(missing)} not found. Install ffmpeg, or set its location in Anki Miner's settings.",
        )


@contextmanager
def _services(config: AnkiMinerConfig, kind: Kind) -> Iterator[SharedLookupServices]:
    """Run-level checks, then the lookup stack the runs share. A dry run checks neither ffmpeg nor Anki."""
    try:
        check_environment(config)
    except SetupFailure as exc:
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    if kind is not Kind.DRY_RUN:
        require_ffmpeg(config)
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


def check_video(config: AnkiMinerConfig, video_file: Path) -> None:
    """VIDEO_UNREADABLE unless ffprobe can open *video_file*."""
    ffprobe = resolve_ffprobe(config)
    # No container duration means "duration unknown" (a live-mode MKV, an
    # interrupted recording), not "cannot be opened": such a file still plays
    # and mines, so a readable video stream is enough.
    if get_media_duration_seconds(video_file, ffprobe) is None and get_primary_video_codec(video_file, ffprobe) is None:
        raise ApiError(VIDEO_UNREADABLE, f"The video cannot be opened: {video_file}")


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
    #: a render's notes by mined_form; empty for the other kinds
    rendered: dict[str, Rendered]


def _mine(
    config: AnkiMinerConfig,
    episode: Episode,
    folder: Path,
    shared: SharedLookupServices,
    cancel_all: threading.Event,
    kind: Kind,
    out: Path,
) -> _Mined:
    """One process_episode call, media inside the run folder, cleaned up whatever happens.

    The temp folder is ``<run_id>/media/`` for every kind: a run folder serves one call at a time.
    A render's notes and their media go to *out* (``<run_id>/render-<n>/``), which stays.
    """
    run_config = _episode_config(config, episode, folder)
    dry = kind is Kind.DRY_RUN
    render = RenderService(run_config, out) if kind is Kind.RENDER else None
    anki_service = _OfflineAnki(run_config) if dry else render if render is not None else AnkiService(run_config)
    processor = create_episode_processor(
        run_config,
        _LogPresenter(),
        None,
        anki_service=anki_service,
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
            succeeded = classify_result(result) is MiningOutcome.SUCCESS
            made: dict[str, str] = {}
            if dry and succeeded:
                # Inside the watcher, so a cancel also cuts short a dry run's wait on a busy Anki.
                made = _dry_statuses(run_config, processor, selection, cancel.is_set)
                if cancel.is_set():  # the check was cut short: the run stopped there, as one cancelled mid-run
                    result = replace(result, errors=[*result.errors, CANCELLED_ERROR])
                    succeeded, made = False, {}
        rendered: dict[str, Rendered] = {}
        if render is not None:  # a stopped render still reports the notes it wrote: their files are in *out*
            rendered = dict(render.rendered)
            made = dict.fromkeys(rendered, "rendered")
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
                collapsed=list(processor.last_collapsed),
                stopped=not succeeded,
                made=made,
            ),
            media_store_failures=failures if isinstance(failures, int) else 0,
            rendered=rendered,
        )
    finally:
        processor.close()
        shutil.rmtree(folder / MEDIA, ignore_errors=True)


def _dry_statuses(
    config: AnkiMinerConfig, processor: EpisodeProcessor, selection: WordSelection, cancelled: Callable[[], bool]
) -> dict[str, str]:
    """A dry run's status per word it would have mined: ready, no_definition or duplicate (API.md)."""
    picked = selection.picked()
    made_here = [word for word, from_line in picked if from_line]
    # A produced word already passed phase 2's dictionary check; a made one never met it.
    undefined = {
        word.mined_form
        for word, ok in zip(made_here, processor.definition_viable(made_here) if made_here else [], strict=True)
        if not ok
    }
    in_anki = _duplicates_in_anki(config, [word for word, _ in picked], cancelled)
    return {
        word.mined_form: (
            "no_definition" if word.mined_form in undefined else "duplicate" if word.mined_form in in_anki else "ready"
        )
        for word, _ in picked
    }


def _duplicates_in_anki(config: AnkiMinerConfig, words: list[TokenizedWord], cancelled: Callable[[], bool]) -> set[str]:
    """The words mine's duplicate checks would refuse, when Anki answers: a dry run needs no Anki."""
    if not words:
        return set()
    # One try, no retry: the duplicate probe retries a refused connection (three
    # tries, 8 s apart), which would hold every episode of a closed Anki.
    if not ValidationService(config).check_ankiconnect()[0]:
        return set()
    anki = AnkiService(config)
    anki.set_cancelled_check(cancelled)
    try:
        return anki.duplicate_fronts([CardPayload(word=w, media=MediaData(), definition="") for w in words])
    except AnkiConnectionError as exc:  # also a refusal that is not a duplicate (a missing deck or note type)
        logger.warning("Dry run: Anki's duplicate check failed, so no word is reported duplicate: %s", exc)
        return set()


def _outcome_error(result: ProcessingResult) -> ApiError | None:
    outcome = classify_result(result)
    if outcome is MiningOutcome.CANCELLED:
        return ApiError(CANCELLED, "The run was cancelled.")
    if outcome is MiningOutcome.FAILED:
        return ApiError(MINING_FAILED, "; ".join(result.errors) or "The run failed.")
    return None


def guarded(run_id: str, body: Callable[[], dict[str, object]]) -> dict[str, object]:
    """One run's verdict: its own ApiError, or INTERNAL for anything unexpected (logged)."""
    try:
        return body()
    except ApiError as exc:
        return run_verdict(run_id, error=exc)
    except Exception as exc:  # noqa: BLE001 — one broken run must not end the call
        logger.exception("API run %s failed", run_id)
        return run_verdict(run_id, error=ApiError(INTERNAL, f"{type(exc).__name__}: {exc}"))


def mine_runs(run_file: RunFile, cancel_all: threading.Event, kind: Kind = Kind.MINE) -> list[dict[str, object]]:
    """Every episode of *run_file* mined in turn.

    Each run that got as far as mining writes its result-<n>.json (a render, its render-<n>.json).
    """
    clear_leftovers(run_file.run_dir, (episode.run_id for episode in run_file.episodes))
    config = settings.resolve_run_config(run_file.profile, run_file.language, run_file.overlay)
    with _services(config, kind) as shared:
        return [
            guarded(episode.run_id, partial(_mine_one, run_file.run_dir, episode, config, shared, cancel_all, kind))
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
        check_video(config, episode.video_file)
    # Numbered first: a render's files folder carries the number of its json.
    path = next_numbered(folder, "render" if kind is Kind.RENDER else "result")
    out = folder / path.stem
    mined = _mine(config, episode, folder, shared, cancel_all, kind, out)
    error = _outcome_error(mined.result)
    words = mined.selection.report(mined.fates)
    report: dict[str, object] = {
        "schema": 1,
        "run_id": episode.run_id,
        "dry_run": kind is Kind.DRY_RUN,
        "outcome": classify_result(mined.result).value,
        "anki_write_state": mined.result.anki_write_state.value,
        "failure_is_transient": bool(mined.result.failure_is_transient),
        "error": error.code if error else None,
        "message": error.message if error else None,
        "media_store_failures": mined.media_store_failures,
        "words": words,
    }
    if kind is Kind.RENDER:  # nothing reaches Anki, and a render is never a dry run
        del report["dry_run"], report["anki_write_state"]
        report["words"] = _render_rows(words, mined.rendered, out)
    write_json(path, report)
    return run_verdict(episode.run_id, error=error, file=path.name)


def _render_rows(rows: list[dict[str, object]], rendered: Mapping[str, Rendered], out: Path) -> list[dict[str, object]]:
    """A render's rows: each rendered word's fields, and its files as paths inside the run folder."""
    for row in rows:
        form = row["mined_form"]
        note = rendered.get(form) if row["status"] == "rendered" and isinstance(form, str) else None
        row["fields"] = note.fields if note else None
        row["files"] = [f"{out.name}/{name}" for name in note.files] if note else []
    return rows
