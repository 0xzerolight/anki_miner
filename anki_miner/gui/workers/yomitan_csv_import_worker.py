"""QThread worker that wraps a Yomitan meta-bank → CSV importer with progress + cancel.

Runs an importer with the ``(zip_path, dest_csv, *, progress, cancel_check)``
signature off the GUI thread and surfaces progress, completion, and failure as
Qt signals. The importer is injected, so the worker stays format-agnostic; in
production it drives ``import_yomitan_pitch_zip`` (frequency moved to its own
multi-source flow).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker

logger = logging.getLogger(__name__)

# Importer signature: (zip_path, dest_csv, *, progress, cancel_check) -> result.
ImportFn = Callable[..., Any]


class YomitanCsvImportWorker(CancellableWorker):
    """Imports a Yomitan-format meta-bank zip into a CSV in the background.

    The importer is passed in (production wires ``import_yomitan_pitch_zip``),
    so the worker is not tied to a single format.

    Signals:
        progress(int, int, str): ``(current, total, message)`` — emitted per
            ``term_meta_bank_*.json`` file processed.
        import_finished(object): emitted once on success with the importer's
            result dataclass (e.g. ``YomitanPitchImportResult``). Typed as
            ``object`` to dodge Qt's signal-type registration limitations for
            arbitrary dataclasses.
        failed(str): error message. Cancellation surfaces here with a message
            containing the word "cancelled" so callers can suppress the
            user-facing error dialog.

    The completion signal is named ``import_finished`` (not ``finished``) to
    avoid colliding with ``QThread.finished``, which the codebase uses for
    cleanup wiring.
    """

    progress = pyqtSignal(int, int, str)
    import_finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        import_fn: ImportFn,
        zip_path: Path,
        dest_csv: Path,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._import_fn = import_fn
        self._zip_path = zip_path
        self._dest_csv = dest_csv

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/failed accordingly."""
        try:
            result = self._import_fn(
                self._zip_path,
                self._dest_csv,
                progress=lambda cur, total, msg: self.progress.emit(cur, total, msg),
                cancel_check=lambda: self.is_cancelled,
            )
            self.import_finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            logger.exception("YomitanCsvImportWorker unhandled exception")
            self.failed.emit(str(exc))
