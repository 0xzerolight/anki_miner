"""Worker for Utilities → Readability (#132): score subtitle files against known words.

Builds the mining services once, so the parser carries the same dictionary
probes (and so the same word counts) as a mining run, reads the known set
read-only, then measures each file. Writes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError, SetupError
from anki_miner.gui.utils.service_factory import Services, create_services, create_shared_lookup_services
from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.audio_fetch_common import close_all
from anki_miner.services.known_word_db import collect_known_forms
from anki_miner.services.readability import measure

logger = logging.getLogger(__name__)


class ReadabilityWorker(FileQueueWorker):
    """Measure each subtitle file; one ``file_measured`` per scored file."""

    #: (idx, ReadabilityStats), emitted just before that file's ``file_finished``.
    file_measured = pyqtSignal(int, object)
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
            services = create_services(replace(self._config, use_whitelist=False), shared_lookup=shared)
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
            self._known = collect_known_forms(services.known_word_db, self._config, vocabulary)
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
        assert self._services is not None
        parser = self._services.subtitle_parser
        words, line_index = parser.parse_subtitle_file_with_index(path)
        counts = parser.count_lemmas(path)  # replays the line cache the parse just filled
        stats = measure(words, line_index, counts, self._known, self._services.word_filter)
        if stats.word_count == 0:
            # e.g. the .en.srt beside the .ja.srt: a row of dashes says nothing.
            self.file_skipped.emit(idx, path, self.tr("No words in the mining language"))
            return
        self.file_measured.emit(idx, stats)
        self.file_finished.emit(idx, path, None)
