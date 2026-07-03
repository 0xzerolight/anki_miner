"""QThread worker that wraps the frequency-source importer with progress + cancel.

Runs :func:`~anki_miner.services.frequency.source_importer.import_frequency_source`
off the GUI thread and surfaces progress, completion, and failure as Qt signals.
Cancellation is delegated to the importer via its ``cancel_check`` callback, wired
to the base class's thread-safe ``is_cancelled`` flag.

Mirrors
:class:`~anki_miner.gui.workers.dictionary_import_worker.DictionaryImportWorker`:
the importer's progress callback is the same ``(current, total, message)`` triplet
(``ProgressFn``), so the ``progress`` signal here matches.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.frequency.source_importer import import_frequency_source

logger = logging.getLogger(__name__)


class FrequencyImportWorker(CancellableWorker):
    """Imports a frequency source (Yomitan zip or CSV/TSV) in the background.

    Signals:
        progress(int, int, str): ``(current, total, message)`` — importer step.
        import_finished(str, dict): ``(source_id, meta)`` — emitted once on
            success. ``meta`` carries ``entry_count``, ``source_name``, and
            ``format``.
        failed(str): error message, including cancellation.

    The completion signal is named ``import_finished`` to avoid collision with
    ``QThread.finished``, which the codebase uses for cleanup wiring.
    """

    # current, total, message
    progress = pyqtSignal(int, int, str)
    # source_id, meta dict (entry_count, source_name, format)
    import_finished = pyqtSignal(str, dict)
    # error message
    failed = pyqtSignal(str)

    def __init__(
        self,
        runner: Callable[[Callable[[int, int, str], None], Callable[[], bool]], Any],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner

    @classmethod
    def for_source(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str | None = None,
    ) -> FrequencyImportWorker:
        """Build a worker that imports a frequency source file."""

        def runner(
            progress_fn: Callable[[int, int, str], None],
            cancel_fn: Callable[[], bool],
        ) -> Any:
            return import_frequency_source(
                input_path,
                dest_root,
                source_id=source_id,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )

        return cls(runner)

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/failed accordingly."""
        try:
            result = self._runner(
                lambda cur, total, msg: self.progress.emit(cur, total, msg),
                # is_cancelled is a property on the base class; wrap to a callable
                lambda: self.is_cancelled,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "source_id", "")),
                "format": getattr(result, "format", ""),
                "skipped_malformed": getattr(result, "skipped_malformed", 0),
                "converted_to_ranks": getattr(result, "converted_to_ranks", False),
            }
            self.import_finished.emit(result.source_id, meta)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            logger.exception("FrequencyImportWorker unhandled exception")
            self.failed.emit(str(exc))
