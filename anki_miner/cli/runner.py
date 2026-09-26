"""Expand command-line inputs into jobs and mine them with one set of services.

Mirrors what the GUI queue workers do per run — the staleness gate, ONE shared
AnkiService and lookup bundle, the card-target + offline-dictionary preflight,
one EpisodeProcessor — without Qt threads or signals. Two deliberate
differences: no curation callback is ever passed (there is nobody to answer a
curator, so every filtered word is mined), and there is no automatic retry
(each item reports ``retryable``, classified exactly as the workers classify
it, and the calling tool decides).
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import ClassVar, cast

from anki_miner.cli.events import EventPresenter, EventProgress, EventSink
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, OperationCancelled, SetupError
from anki_miner.exceptions.youtube import YouTubeFetchError
from anki_miner.gui.utils.service_factory import (
    create_episode_processor,
    create_shared_lookup_services,
    create_youtube_fetcher,
)
from anki_miner.gui.workers._queue_worker_base import (
    anki_write_state_of,
    exception_retry_eligible,
    queue_preflight_error,
)
from anki_miner.gui.workers.reading_queue_worker import load_reading_source
from anki_miner.gui.workers.youtube_queue_worker import allocate_youtube_workspace
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models import MiningOutcome, ProcessingResult, classify_result, classify_terminal_outcome
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.youtube import SubtitleSource
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.orchestration.episode_processor import require_usable_offline_provider
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.asr.model_availability import usable_model_installed
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.reading import detector
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.services.stats_service import StatsService
from anki_miner.services.youtube_fetcher import classify_probe_result
from anki_miner.utils.file_pairing import FilePairMatcher
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.youtube_url import classify_youtube_url
from anki_miner.utils.ytdlp_resolver import ytdlp_available

logger = logging.getLogger(__name__)


class InputError(ValueError):
    """The command line named something that cannot be mined (usage_error)."""


class SetupFailure(Exception):
    """A run-level check failed before any item was attempted (setup_error)."""


class _ItemRefused(Exception):
    """One item cannot be mined; the run continues."""


@dataclass(frozen=True)
class EpisodeJob:
    """A video file and the subtitle file that goes with it."""

    video: Path
    subtitle: Path
    kind: ClassVar[str] = "episode"

    def describe(self) -> dict[str, str]:
        return {"video": str(self.video), "subtitle": str(self.subtitle)}


@dataclass(frozen=True)
class ReadingJob:
    """One reading source (a subtitle file, a book, a mokuro volume) without media."""

    source: ReadingSourceRef
    path: Path
    kind: ClassVar[str] = "reading"

    def describe(self) -> dict[str, str]:
        return {"path": str(self.path), "title": str(getattr(self.source, "title", ""))}


@dataclass(frozen=True)
class YouTubeJob:
    """One YouTube video URL, fetched with yt-dlp and then mined."""

    url: str
    kind: ClassVar[str] = "youtube"

    def describe(self) -> dict[str, str]:
        return {"url": self.url}


Job = EpisodeJob | ReadingJob | YouTubeJob


@dataclass
class ItemReport:
    """What one job produced, as the ``item_done`` event and the result's ``items`` carry it."""

    item: int
    kind: str
    input: dict[str, str]
    status: str  # "success" | "failed" | "cancelled" | "skipped"
    cards_created: int = 0
    new_words_found: int = 0
    total_words_found: int = 0
    note_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retryable: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "item": self.item,
            "kind": self.kind,
            "input": self.input,
            "status": self.status,
            "cards_created": self.cards_created,
            "new_words_found": self.new_words_found,
            "total_words_found": self.total_words_found,
            "note_ids": self.note_ids,
            "errors": self.errors,
            "retryable": self.retryable,
        }


# ---- job expansion ---------------------------------------------------------


def _existing_file(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise InputError(f"File not found: {path}")
    return resolved


def _existing_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise InputError(f"This is not a folder: {path}")
    return resolved


def pair_jobs(pairs: Sequence[tuple[Path, Path]]) -> list[EpisodeJob]:
    """One job per explicit (video, subtitle) pair; both files must exist."""
    return [EpisodeJob(video=_existing_file(v), subtitle=_existing_file(s)) for v, s in pairs]


def batch_jobs(video_dir: Path, subtitle_dir: Path) -> list[EpisodeJob]:
    """Pair two folders by episode number, exactly as Video -> Batch does."""
    pairs = FilePairMatcher.find_pairs_by_episode_number(_existing_dir(video_dir), _existing_dir(subtitle_dir))
    if not pairs:
        raise InputError("No subtitle file could be matched to any video file in those folders.")
    return [EpisodeJob(video=pair.video, subtitle=pair.subtitle) for pair in pairs]


def reading_jobs(paths: Sequence[Path]) -> list[ReadingJob]:
    """Classify each path with the Reading tab's detector (one path may hold several volumes)."""
    jobs: list[ReadingJob] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.exists():
            raise InputError(f"File not found: {raw}")
        try:
            refs = detector.detect(path)
        except (SetupError, OSError) as exc:
            raise InputError(str(exc)) from exc
        jobs.extend(ReadingJob(source=ref, path=path) for ref in refs)
    return jobs


def youtube_jobs(urls: Sequence[str]) -> list[YouTubeJob]:
    """One job per YouTube video URL; playlist links are refused."""
    for url in urls:
        if classify_youtube_url(url).kind not in ("video", "video_in_playlist"):
            raise InputError(f"Not a YouTube video URL (playlist links are not supported): {url}")
    return [YouTubeJob(url=url) for url in urls]


def run_status(reports: Sequence[ItemReport], *, cancelled: bool) -> str:
    """The run's ``result.status`` from its item reports.

    Delegates to :func:`classify_terminal_outcome`, the same whole-run
    classifier the GUI queue sites use (models/processing.py). ``failed`` is
    the complement of ``succeeded`` — not a count of specific status strings
    — which keeps this exactly equivalent to the prior hand-rolled rule for
    every input (proved in
    tests/unit/test_cli_runner.py::test_run_status_matches_old_rule, run
    against both this and the pre-refactor implementation).
    """
    succeeded = sum(1 for r in reports if r.status == "success")
    failed = len(reports) - succeeded
    return classify_terminal_outcome(succeeded, failed, cancelled=cancelled).value


# ---- the run -----------------------------------------------------------------


def check_environment(config: AnkiMinerConfig, *, youtube: bool = False) -> None:
    """Run-level checks before any service is built (stale index, language pack, yt-dlp)."""
    stale = stale_resource_reimport_error(config)
    if stale is not None:
        raise SetupFailure(stale)
    # The mining language's engine pack (zh, ko, spaCy languages, ...): the
    # same probe the GUI asks, so a missing pack refuses the run once instead
    # of failing every item with a raw ModuleNotFoundError.
    probe = get_profile(config_language(config)).unavailable_reason
    reason = probe() if probe is not None else None
    if reason:
        raise SetupFailure(reason)
    if youtube and not ytdlp_available(config):
        raise SetupFailure(
            "yt-dlp is not installed. Open Anki Miner, go to Video -> YouTube and install it, then try again."
        )


def check_card_target(
    config: AnkiMinerConfig, anki_service: AnkiService, definition_service: DefinitionService
) -> None:
    """Card target, then a usable offline dictionary. AnkiConnectionError propagates unwrapped."""
    preflight = queue_preflight_error(
        anki_service.verify_card_target,
        partial(require_usable_offline_provider, config, definition_service),
    )
    if preflight is not None:
        raise SetupFailure(preflight)


class MiningRun:
    """Mine a list of jobs in order with one processor and one set of services."""

    def __init__(self, config: AnkiMinerConfig, sink: EventSink, cancel: threading.Event) -> None:
        self._config = config
        self._sink = sink
        self._cancel = cancel
        self._presenter = EventPresenter(sink)
        self._progress = EventProgress(sink)

    def run(self, jobs: Sequence[Job]) -> list[ItemReport]:
        """Mine every job; raise :class:`SetupFailure` if a run-level check fails first."""
        check_environment(self._config, youtube=any(isinstance(job, YouTubeJob) for job in jobs))
        anki_service = AnkiService(self._config)
        shared = create_shared_lookup_services(self._config)
        try:
            for message in shared.load_result.info:
                self._presenter.show_info(message)
            for message in shared.load_result.warnings:
                self._presenter.show_warning(message)
            try:
                check_card_target(self._config, anki_service, shared.definition_service)
            except AnkiConnectionError as exc:
                raise SetupFailure(str(exc)) from exc
            processor = create_episode_processor(
                self._config,
                self._presenter,
                StatsService(self._config.stats_db_path, language=self._config.language),
                anki_service=anki_service,
                shared_lookup=shared,
            )
            try:
                return [self._run_item(idx, job, processor) for idx, job in enumerate(jobs)]
            finally:
                processor.close()
        finally:
            shared.close()

    def _run_item(self, idx: int, job: Job, processor: EpisodeProcessor) -> ItemReport:
        if self._cancel.is_set():
            return ItemReport(item=idx, kind=job.kind, input=job.describe(), status="skipped")
        self._sink.current_item = idx
        self._sink.emit("item_start", item=idx, kind=job.kind, input=job.describe())
        log_summary(logger, "CLI item start", idx=idx, kind=job.kind)
        try:
            report = self._report(idx, job, self._mine(job, processor))
        except _ItemRefused as exc:
            report = self._failed(idx, job, str(exc))
        except OperationCancelled:
            report = ItemReport(item=idx, kind=job.kind, input=job.describe(), status="cancelled")
        except YouTubeFetchError as exc:
            if self._cancel.is_set():
                report = ItemReport(item=idx, kind=job.kind, input=job.describe(), status="cancelled")
            else:
                logger.warning("CLI item %d fetch failed: %s", idx, exc)
                report = self._failed(idx, job, str(exc), self._retryable(exc, processor))
        except Exception as exc:  # noqa: BLE001 — one broken item must not end the run
            logger.exception("CLI item %d failed", idx)
            report = self._failed(idx, job, str(exc) or type(exc).__name__, self._retryable(exc, processor))
        self._sink.emit("item_done", **report.to_json())
        self._sink.current_item = None
        log_summary(logger, "CLI item end", idx=idx, status=report.status, cards=report.cards_created)
        return report

    def _mine(self, job: Job, processor: EpisodeProcessor) -> ProcessingResult:
        if isinstance(job, EpisodeJob):
            return processor.process_episode(
                job.video, job.subtitle, progress_callback=self._progress, cancel_event=self._cancel
            )
        if isinstance(job, ReadingJob):
            document = load_reading_source(processor, self._config, job.source, cancel_check=self._cancel.is_set)
            return processor.process_reading(document, progress_callback=self._progress, cancel_event=self._cancel)
        return self._mine_youtube(job, processor)

    def _mine_youtube(self, job: YouTubeJob, processor: EpisodeProcessor) -> ProcessingResult:
        try:
            info = create_youtube_fetcher(self._config).probe_metadata(job.url)
        except YouTubeFetchError as exc:
            if self._cancel.is_set():
                raise
            # A private, deleted or region-locked video fails its probe with a
            # generic YouTubeFetchError; the GUI marks it PROBE_ERROR and never
            # retries it, so the item is refused, never reported retryable.
            raise _ItemRefused(str(exc)) from exc
        source = cast(SubtitleSource, self._config.youtube_subtitle_source)
        mineable, error, sub_mode = classify_probe_result(info, self._config, source)
        if not mineable or sub_mode is None:
            raise _ItemRefused(error or "This video cannot be mined.")
        if sub_mode == "transcribe" and not usable_model_installed(self._config):
            raise _ItemRefused(
                f"This video needs local transcription, but the model {self._config.asr_model} is not installed. "
                "Install it in Anki Miner: Settings -> Transcription & Alignment."
            )
        workspace = allocate_youtube_workspace(self._config)
        try:
            return processor.process_youtube_url(
                url=job.url,
                video_id=info.video_id,
                workspace=workspace,
                sub_mode=sub_mode,
                cancel_event=self._cancel,
                progress_callback=self._progress,
                fetch_progress_cb=lambda label, frac: self._sink.emit("download", label=label, fraction=frac),
                source_label=info.title,
                align_captions=self._config.youtube_align_captions,
                fallback_allowed=info.has_auto_ja_subs,
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    @staticmethod
    def _retryable(exc: BaseException, processor: EpisodeProcessor) -> bool:
        return exception_retry_eligible(exc, anki_write_state_of(processor))

    @staticmethod
    def _failed(idx: int, job: Job, message: str, retryable: bool = False) -> ItemReport:
        return ItemReport(
            item=idx, kind=job.kind, input=job.describe(), status="failed", errors=[message], retryable=retryable
        )

    @staticmethod
    def _report(idx: int, job: Job, result: ProcessingResult) -> ItemReport:
        outcome = classify_result(result)
        status = {MiningOutcome.SUCCESS: "success", MiningOutcome.CANCELLED: "cancelled"}.get(outcome, "failed")
        return ItemReport(
            item=idx,
            kind=job.kind,
            input=job.describe(),
            status=status,
            cards_created=int(result.cards_created),
            new_words_found=int(result.new_words_found),
            total_words_found=int(result.total_words_found),
            # ProcessingResult.card_ids holds NOTE ids (AnkiService.create_cards_batch returns them).
            note_ids=list(result.card_ids),
            errors=list(result.errors) if status == "failed" else [],
            retryable=result.auto_retry_eligible is True,
        )
