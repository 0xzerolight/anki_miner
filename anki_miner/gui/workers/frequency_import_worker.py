"""QThread worker that wraps the Yomitan frequency importer with progress + cancel.

Runs ``import_yomitan_freq_zip`` off the GUI thread and surfaces progress,
completion, and failure as Qt signals. Mirrors :class:`DictionaryImportWorker`
so the two import flows stay symmetric for future maintainers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.frequency import import_yomitan_freq_zip


class FrequencyImportWorker(CancellableWorker):
    """Imports a Yomitan-format frequency zip into a CSV in the background.

    Signals:
        progress(int, int, str): ``(current, total, message)`` — emitted per
            ``term_meta_bank_*.json`` file processed.
        import_finished(object): emitted once on success with the
            :class:`YomitanFreqImportResult`. Typed as ``object`` to dodge
            Qt's signal-type registration limitations for arbitrary dataclasses.
        failed(str): error message. Cancellation surfaces here with a
            message containing the word "cancelled" so callers can suppress
            the user-facing error dialog.

    The completion signal is named ``import_finished`` (not ``finished``) to
    avoid colliding with ``QThread.finished``, which the codebase uses for
    cleanup wiring.
    """

    progress = pyqtSignal(int, int, str)
    import_finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, zip_path: Path, dest_csv: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self._zip_path = zip_path
        self._dest_csv = dest_csv

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/failed accordingly."""
        try:
            result = import_yomitan_freq_zip(
                self._zip_path,
                self._dest_csv,
                progress=lambda cur, total, msg: self.progress.emit(cur, total, msg),
                cancel_check=lambda: self.is_cancelled,
            )
            self.import_finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            self.failed.emit(str(exc))
