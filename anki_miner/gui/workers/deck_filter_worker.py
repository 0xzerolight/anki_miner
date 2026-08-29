"""Worker threads for the Deck Filter tool (Utilities → Deck Filter)."""

import logging
from types import SimpleNamespace

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import create_shared_lookup_services, resolve_known_words_db_path
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.deck_filter import (
    DeckFilterOptions,
    DeckFilterPlan,
    DeckFilterResult,
    apply_deck_filter,
    scan_deck_filter,
)
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService
from anki_miner.services.wordset_service import WordsetService
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


def _build_filter_bundle(config: AnkiMinerConfig, frequency_service) -> SimpleNamespace:
    """Assemble the duck-typed service bundle ``scan_deck_filter`` consumes.

    The shared lookup bundle only carries definition/pitch/frequency; the
    word-list, wordset, and known-words services are built here with the same
    guarded shapes as ``create_services`` (a broken optional source degrades
    to None, never aborts the scan). The tagger is optional too — without it
    the scan keeps ``lemma == expression`` and generates no readings.
    """
    known_word_db = None
    try:
        known_word_db = KnownWordDB(resolve_known_words_db_path(config))
    except Exception as e:
        logger.warning("Could not open known word database: %s", e)

    word_list_service = None
    if config.use_blacklist or config.use_whitelist:
        try:
            word_list_service = WordListService(
                blacklist_path=config.blacklist_path if config.use_blacklist else None,
                whitelist_path=config.whitelist_path if config.use_whitelist else None,
            )
            word_list_service.load()
        except Exception as e:
            logger.warning("Could not load word lists: %s", e)
            word_list_service = None

    wordset_service = None
    if config.excluded_wordsets:
        try:
            wordset_service = WordsetService(enabled_ids=config.excluded_wordsets)
            wordset_service.load()
            if not wordset_service.is_available():
                wordset_service = None
        except Exception as e:
            logger.warning("Could not load name wordsets: %s", e)
            wordset_service = None

    tagger = None
    try:
        from anki_miner.services.tagger import get_shared_tagger

        tagger = get_shared_tagger()
    except Exception as e:
        logger.warning("Tagger unavailable for deck filter scan (%s); readings/lemmas degrade.", e)

    return SimpleNamespace(
        # ``script=`` is load-bearing for non-ja: this object IS the bundle's
        # word_filter, and without it ko/zh option ids would reach the JA
        # predicate table and match nothing.
        word_filter=WordFilterService(
            config,
            mined_form=get_profile(config_language(config)).mined_form,
            script=get_profile(config_language(config)).script,
        ),
        frequency_service=frequency_service,
        word_list_service=word_list_service,
        wordset_service=wordset_service,
        known_word_db=known_word_db,
        tagger=tagger,
    )


class DeckFilterScanWorker(CancellableWorker):
    """Runs ``scan_deck_filter`` off the GUI thread.

    Builds ONE ``AnkiService`` plus the lookup/filter services, so all
    SQLite/registry I/O happens here, never on the GUI thread. Read-only.
    """

    progress = pyqtSignal(int, int)  # (notes examined, total)
    result_ready = pyqtSignal(object, object)  # (DeckFilterPlan, tuple[str, ...])

    def __init__(self, config: AnkiMinerConfig, options: DeckFilterOptions, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.options = options

    def run(self) -> None:
        self.log_start(
            "DeckFilterScanWorker",
            source=self.options.source_deck,
            expression_field=self.options.expression_field,
        )
        try:
            if self.check_cancelled():
                return
            anki_service = AnkiService(self.config)
            shared_lookup = create_shared_lookup_services(self.config)
            try:
                if self.check_cancelled():
                    return
                services = _build_filter_bundle(self.config, shared_lookup.frequency_service)
                plan = scan_deck_filter(
                    anki_service,
                    self.config,
                    services,
                    self.options,
                    progress=self.progress.emit,
                    is_cancelled=self.check_cancelled,
                )
                if not self.check_cancelled():
                    log_summary(
                        logger,
                        "DeckFilterScanWorker done",
                        scanned=plan.scanned,
                        kept=len(plan.kept),
                    )
                    self.result_ready.emit(plan, tuple(shared_lookup.load_result.warnings))
            finally:
                shared_lookup.close()
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            self.report_failure(
                e,
                context="DeckFilterScanWorker",
                on_error=lambda msg: self.error.emit(f"Deck filter scan failed: {msg}"),
            )


class DeckFilterApplyWorker(CancellableWorker):
    """Runs ``apply_deck_filter`` off the GUI thread.

    Builds only an ``AnkiService`` — apply copies the plan's scan-time
    values, so no lookup service is loaded here. Cancellation is honored
    between chunks; committed chunks stay copied.
    """

    progress = pyqtSignal(int, int)  # (notes copied, total)
    result_ready = pyqtSignal(object)  # DeckFilterResult
    cancelled = pyqtSignal()

    def __init__(self, config: AnkiMinerConfig, plan: DeckFilterPlan, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.plan = plan

    def run(self) -> None:
        self.log_start(
            "DeckFilterApplyWorker",
            target=self.plan.options.target_deck,
            notes=len(self.plan.kept),
        )
        try:
            if self.check_cancelled():
                self.result_ready.emit(DeckFilterResult(0, 0))
                self.cancelled.emit()
                return
            anki_service = AnkiService(self.config)
            result = apply_deck_filter(
                anki_service,
                self.plan,
                progress=self.progress.emit,
                is_cancelled=self.check_cancelled,
            )
            # apply commits per chunk and returns confirmed partial counts on
            # cancellation. Always deliver that terminal receipt so the UI
            # clears the consumed plan.
            self.result_ready.emit(result)
            if self.check_cancelled():
                self.cancelled.emit()
            else:
                log_summary(
                    logger,
                    "DeckFilterApplyWorker done",
                    created=result.created,
                    not_created=result.not_created,
                )
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            self.report_failure(
                e,
                context="DeckFilterApplyWorker",
                on_error=lambda msg: self.error.emit(f"Deck filter apply failed: {msg}"),
                on_cancelled=self.cancelled.emit,
            )
