"""Worker threads for the Card Backfill tool (Utilities → Card Backfill)."""

import contextlib
import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.utils.service_factory import (
    SharedLookupServices,
    create_expression_audio_fetcher,
    create_shared_lookup_services,
)
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.card_backfiller import (
    BACKFILL_TAG,
    FIELD_GROUPS,
    BackfillOptions,
    BackfillPlan,
    BackfillResult,
    apply_backfill,
    scan_backfill,
)
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: Which indexed resource family produces which backfill field keys. Drives the
#: scoped staleness gate: only a family whose output this run would actually
#: write can abort it. Derived from ``FIELD_GROUPS`` rather than restated, so a
#: new field key cannot land in a group the gate does not know about.
_BACKFILL_FIELD_FAMILIES: dict[str, frozenset[str]] = {
    "dictionary": frozenset(FIELD_GROUPS["definition"] + FIELD_GROUPS["glossary"]),
    "frequency": frozenset(FIELD_GROUPS["frequency"]),
    "pitch": frozenset(FIELD_GROUPS["pitch"]),
    "audio": frozenset(FIELD_GROUPS["word_audio"]),
}


class BackfillScanWorker(CancellableWorker):
    """Runs ``scan_backfill`` off the GUI thread.

    Builds ONE ``AnkiService`` plus the lookup-only shared service bundle, so
    all SQLite/CSV/registry I/O happens here, never on the GUI thread.
    Read-only.
    """

    progress = pyqtSignal(int, int)  # (scanned, total)
    result_ready = pyqtSignal(object, object)  # (BackfillPlan, tuple[str, ...])

    def __init__(self, config: AnkiMinerConfig, options: BackfillOptions, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.options = options

    def _check_resource_staleness(self, shared_lookup: SharedLookupServices) -> None:
        """Abort the scan if a resource *this backfill would write* is stale.

        Scoped to the requested fields, not the whole config: a definition-only
        backfill has no business failing over a stale pitch index it will never
        read, and vice versa. Each family maps to the field keys whose values it
        produces.
        """
        families = {family for family, keys in _BACKFILL_FIELD_FAMILIES.items() if self.options.field_keys & keys}
        if not families:
            return
        message = stale_resource_reimport_error(
            self.config,
            families=frozenset(families),
            dictionary_registry=shared_lookup.dictionary_registry,
            frequency_registry=shared_lookup.frequency_registry,
            pitch_registry=shared_lookup.pitch_registry,
            audio_registry=shared_lookup.audio_pack_registry,
        )
        if message is not None:
            raise SetupError(message)

    def run(self) -> None:
        self.log_start(
            "BackfillScanWorker",
            fields=len(self.options.field_keys),
            overwrite=self.options.overwrite,
            deck=self.options.deck,
            note_type=self.config.anki_note_type,
        )
        try:
            if self.check_cancelled():
                return
            anki_service = AnkiService(self.config)
            shared_lookup = create_shared_lookup_services(self.config)
            audio_fetcher = None
            try:
                if self.check_cancelled():
                    return
                self._check_resource_staleness(shared_lookup)
                if FIELD_GROUPS["word_audio"][0] in self.options.field_keys:
                    # Built here rather than in the bundle: only a run that
                    # actually fetches needs one, and the chain's online members
                    # hold a live HTTP session this worker must close.
                    audio_fetcher = create_expression_audio_fetcher(
                        self.config,
                        shared_lookup.load_result,
                        pack_registry=shared_lookup.audio_pack_registry,
                    )
                plan = scan_backfill(
                    anki_service,
                    self.config,
                    shared_lookup,
                    self.options,
                    expression_audio_fetcher=audio_fetcher,
                    progress=self.progress.emit,
                    is_cancelled=self.check_cancelled,
                )
                if not self.check_cancelled():
                    log_summary(
                        logger,
                        "BackfillScanWorker done",
                        notes=len(plan.notes),
                        fields=plan.total_field_changes,
                    )
                    self.result_ready.emit(plan, tuple(shared_lookup.load_result.warnings))
            finally:
                if audio_fetcher is not None:
                    # Duck-typed and suppressed for the same reason
                    # EpisodeProcessor's teardown is: close() is not on the
                    # ExpressionAudioFetcher Protocol (the local-pack fetcher
                    # has none), and a fetcher that cannot close must not sink
                    # an otherwise completed scan.
                    close = getattr(audio_fetcher, "close", None)
                    if callable(close):
                        with contextlib.suppress(Exception):
                            close()
                shared_lookup.close()
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            self.report_failure(
                e,
                context="BackfillScanWorker",
                on_error=lambda msg: self.error.emit(f"Backfill scan failed: {msg}"),
            )


class BackfillApplyWorker(CancellableWorker):
    """Runs ``apply_backfill`` off the GUI thread.

    Builds only an ``AnkiService`` — apply writes the plan's precomputed
    values, so the lookup services (the shared lookup bundle) are
    scan-only and never loaded here. Cancellation is honored between chunks;
    committed chunks stay written and tagged.
    """

    progress = pyqtSignal(int, int)  # (notes processed, total notes)
    result_ready = pyqtSignal(object)  # BackfillResult
    cancelled = pyqtSignal()

    def __init__(self, config: AnkiMinerConfig, plan: BackfillPlan, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.plan = plan

    def run(self) -> None:
        self.log_start(
            "BackfillApplyWorker",
            notes=len(self.plan.notes),
            fields=self.plan.total_field_changes,
            overwrite=self.plan.options.overwrite,
            deck=self.plan.options.deck,
            note_type=self.config.anki_note_type,
        )
        try:
            if self.check_cancelled():
                self.result_ready.emit(BackfillResult(0, 0, 0, 0))
                self.cancelled.emit()
                return
            anki_service = AnkiService(self.config)
            result = apply_backfill(
                anki_service,
                self.plan,
                tag=BACKFILL_TAG,
                progress=self.progress.emit,
                is_cancelled=self.check_cancelled,
            )
            # apply_backfill commits per chunk and returns confirmed partial
            # counts when cancellation stops later chunks. Always deliver that
            # terminal receipt so the UI clears the consumed plan.
            self.result_ready.emit(result)
            if self.check_cancelled():
                self.cancelled.emit()
            else:
                log_summary(
                    logger,
                    "BackfillApplyWorker done",
                    applied=result.notes_updated,
                    tagged=result.tagged,
                    stale=result.skipped_stale,
                    failed=result.failed,
                )
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            self.report_failure(
                e,
                context="BackfillApplyWorker",
                on_error=lambda msg: self.error.emit(f"Backfill apply failed: {msg}"),
                on_cancelled=self.cancelled.emit,
            )
