"""Worker threads for the Card Backfill tool (Tools → Card Backfill)."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import create_services
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

logger = logging.getLogger(__name__)


class BackfillScanWorker(CancellableWorker):
    """Runs ``scan_backfill`` off the GUI thread.

    Builds ONE ``AnkiService`` and injects it into ``create_services`` (the
    factory would otherwise construct a redundant second instance), so all
    SQLite/CSV/registry I/O happens here, never on the GUI thread. Read-only.
    """

    progress = pyqtSignal(int, int)  # (scanned, total)
    result_ready = pyqtSignal(object)  # BackfillPlan

    def __init__(self, config: AnkiMinerConfig, options: BackfillOptions, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.options = options

    def run(self) -> None:
        try:
            if self.check_cancelled():
                return
            anki_service = AnkiService(self.config)
            services = create_services(self.config, anki_service=anki_service)
            if self.check_cancelled():
                return
            plan = scan_backfill(
                anki_service,
                self.config,
                services,
                self.options,
                progress=self.progress.emit,
                is_cancelled=self.check_cancelled,
            )
            if not self.check_cancelled():
                self.result_ready.emit(plan)
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            logger.exception("BackfillScanWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"Backfill scan failed: {e}")


class BackfillApplyWorker(CancellableWorker):
    """Runs ``apply_backfill`` off the GUI thread.

    Builds only an ``AnkiService`` — apply writes the plan's precomputed
    values, so the lookup services (the ``create_services`` bundle) are
    scan-only and never loaded here. Cancellation is honored between chunks;
    committed chunks stay written and tagged.
    """

    progress = pyqtSignal(int, int)  # (notes processed, total notes)
    result_ready = pyqtSignal(object)  # BackfillResult

    def __init__(self, config: AnkiMinerConfig, plan: BackfillPlan, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.plan = plan

    def run(self) -> None:
        try:
            if self.check_cancelled():
                self.result_ready.emit(BackfillResult(0, 0, 0, 0))
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
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            logger.exception("BackfillApplyWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"Backfill apply failed: {e}")
