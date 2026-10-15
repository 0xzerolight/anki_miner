"""Worker for Utilities → Readability (#132): score subtitles, books and manga against known words.

Builds the mining services once, so the parser carries the same dictionary
probes (and so the same word counts) as a mining run, reads the known set
read-only, then measures each file. A subtitle file parses from its path; a
book or manga volume loads through the Reading tab's loaders and parses as text
units, counted by lemma like the subtitle path. Writes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, OperationCancelled, SetupError
from anki_miner.gui.utils.service_factory import Services, create_services, create_shared_lookup_services
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.gui.workers.reading_queue_worker import load_reading_source
from anki_miner.models.readability import ReadabilityStats
from anki_miner.services.audio_fetch_common import close_all
from anki_miner.services.known_word_db import collect_knowledge_forms
from anki_miner.services.readability import combine, measure
from anki_miner.services.reading import detector
from anki_miner.services.reading.aozora_source import split_oversize
from anki_miner.utils.file_pairing import FilePairMatcher

logger = logging.getLogger(__name__)


class ReadabilityWorker(FileQueueWorker):
    """Measure each subtitle file, book or manga volume; one ``file_measured`` per scored file."""

    #: (idx, ReadabilityStats), emitted just before that file's ``file_finished``.
    file_measured = pyqtSignal(int, object)
    #: A dictionary, word list or other source that failed to load; the check goes on without it.
    load_warning = pyqtSignal(str)
    #: The language cannot tokenize (missing engine): every file would fail alike.
    _FATAL_QUEUE_EXCEPTIONS = (SetupError,)

    def __init__(self, config: AnkiMinerConfig, files: list[Path], parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._files = list(files)
        self._services: Services | None = None
        self._known: set[str] = set()

    def _process_queue(self) -> None:
        shared = create_shared_lookup_services(self._config)
        services: Services | None = None
        try:
            # Readability measures the text: a mining preference list must not
            # move its numbers, and the parser's whitelist rescue (R1) would.
            # use_known_words_db only gates create_services' initialize(), a
            # write; collect_knowledge_forms below reads the user's own setting.
            services = create_services(
                replace(self._config, use_whitelist=False, use_known_words_db=False), shared_lookup=shared
            )
            for message in (*shared.load_result.warnings, *services.load_result.warnings):
                self.load_warning.emit(message)
            if self.is_cancelled:
                return
            try:
                # Not degraded: an empty set on a timeout would read as "0% known".
                vocabulary = services.anki_service.get_existing_vocabulary(allow_degraded=False)
            except AnkiConnectionError as exc:
                # Anki simply not running is expected: one WARNING line, not the
                # traceback run() would write (report_failure's rule).
                logger.warning("ReadabilityWorker: Anki not reachable: %s", exc)
                self.fatal_exception = exc  # the tab names it in its own words
                self._fatal_error = True
                self.error.emit(str(exc))
                return
            # F-01: with Word Filters → "Count a word as known only after its
            # card has been reviewed" on, known % counts reviewed cards only;
            # a failed review query falls back to every card. Read-only: the
            # reviewed flags in the cache are written by mining runs alone.
            self._known = collect_knowledge_forms(
                services.known_word_db,
                self._config,
                vocabulary,
                services.anki_service.get_reviewed_vocabulary,
            )
            self._services = services
            super()._process_queue()
        finally:
            self._services = None
            if services is not None:
                # No EpisodeProcessor owns these here; online fetchers hold a requests.Session.
                close_all((services.expression_audio_fetcher, services.sentence_audio_fetcher))
            shared.close()

    def _queue_items(self) -> list[Path]:
        return self._files

    def _process_item(self, idx: int, path: Path) -> None:
        if path.suffix.lower() in FilePairMatcher.SUBTITLE_EXTENSIONS:
            stats: ReadabilityStats | None = self._measure_subtitle(path)
        else:
            stats = self._measure_reading(idx, path)
        if stats is None:
            return  # already reported as a failed file, or cancelled mid-load
        if stats.word_count == 0:
            # e.g. the .en.srt beside the .ja.srt: a row of dashes says nothing.
            self.file_skipped.emit(idx, path, self.tr("No words in the mining language"))
            return
        self.file_measured.emit(idx, stats)
        self.file_finished.emit(idx, path, None)

    def _measure_subtitle(self, path: Path) -> ReadabilityStats:
        assert self._services is not None
        parser = self._services.subtitle_parser
        words, line_index = parser.parse_subtitle_file_with_index(path)
        counts = parser.count_lemmas(path)  # replays the line cache the parse just filled
        return measure(words, line_index, counts, self._known, self._services.word_filter)

    def _measure_reading(self, idx: int, path: Path) -> ReadabilityStats | None:
        """A book or manga volume, loaded as Reading mines it; ``None`` once reported."""
        services = self._services
        assert services is not None
        try:
            # A .txt over the loader's size cap is measured in parts, as Novels mines it.
            refs = split_oversize(detector.detect(path))
        except (SetupError, OSError) as exc:  # e.g. a .cbz with no .mokuro beside or inside it
            self._report_unreadable(idx, exc)
            return None
        parts: list[ReadabilityStats] = []
        for ref in refs:
            try:
                document = load_reading_source(services, self._config, ref, cancel_check=self.check_cancelled)
            except OperationCancelled:
                return None
            except SetupError as exc:
                # A protected EPUB or a broken volume fails this file only. The
                # parser's SetupError below (no engine) still stops the queue.
                self._report_unreadable(idx, exc)
                return None
            words, line_index, counts = services.subtitle_parser.parse_text_units(
                document.units, True, count_by_lemma=True
            )
            parts.append(measure(words, line_index or [], counts, self._known, services.word_filter))
        return combine(parts)

    def _report_unreadable(self, idx: int, exc: Exception) -> None:
        # One WARNING line, not the traceback the base loop logs for an unexpected error.
        logger.warning("ReadabilityWorker: file %d unreadable: %s", idx, exc)
        self.file_finished.emit(idx, None, str(exc))
