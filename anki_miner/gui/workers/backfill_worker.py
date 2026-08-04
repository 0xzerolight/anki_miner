"""Worker threads for the Card Backfill tool (Utilities → Card Backfill)."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import create_shared_lookup_services
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.card_backfiller import (
    BACKFILL_TAG,
    BackfillOptions,
    BackfillPlan,
    BackfillResult,
    apply_backfill,
    scan_backfill,
)
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


class BackfillScanWorker(CancellableWorker):
    """Runs ``scan_backfill`` off the GUI thread.

    Builds ONE ``AnkiService`` plus the lookup-only shared service bundle, so
    all SQLite/CSV/registry I/O happens here, never on the GUI thread.
    Read-only.
    """

    progress = pyqtSignal(int, int)  # (scanned, total)
    result_ready = pyqtSignal(object)  # BackfillPlan

    def __init__(self, config: AnkiMinerConfig, options: BackfillOptions, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.options = options

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
            try:
                if self.check_cancelled():
                    return
                plan = scan_backfill(
                    anki_service,
                    self.config,
                    shared_lookup,
                    self.options,
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
                    self.result_ready.emit(plan)
            finally:
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
