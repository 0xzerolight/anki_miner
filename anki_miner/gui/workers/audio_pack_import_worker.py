"""QThread worker that wraps the audio pack importer with progress + cancel.

Runs the audio pack importer off the GUI thread and surfaces progress,
completion, and failure as Qt signals. Cancellation is delegated to the
importer via its ``cancel_check`` callback, wired to the base class's
thread-safe ``is_cancelled`` flag.

Note: the audio pack importer's progress callback takes a single string
(unlike the dictionary importer's ``(int, int, str)`` triplet), so the
``progress`` signal here is ``(str,)``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.audio_packs.importer import import_audio_pack

logger = logging.getLogger(__name__)


class AudioPackImportWorker(CancellableWorker):
    """Imports an audio pack directory in the background.

    Signals:
        progress(str): Human-readable status message from the importer.
        import_finished(str, dict): ``(pack_id, meta)`` — emitted once on success.
            ``meta`` carries ``entry_count``, ``source_name``, and ``format``.
        failed(str): error message, including cancellation.

    The completion signal is named ``import_finished`` to avoid collision with
    ``QThread.finished``, which the codebase uses for cleanup wiring.
    """

    # single-string progress message
    progress = pyqtSignal(str)
    # pack_id, meta dict (entry_count, source_name, format)
    import_finished = pyqtSignal(str, dict)
    # error message
    failed = pyqtSignal(str)

    def __init__(
        self,
        runner: Callable[[Callable[[str], None], Callable[[], bool]], Any],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner

    @classmethod
    def for_pack(
        cls,
        pack_dir: Path,
        dest_root: Path,
        *,
        pack_id: str | None = None,
        overwrite: bool = False,
    ) -> AudioPackImportWorker:
        """Build a worker that imports an audio pack directory."""

        def runner(
            progress_fn: Callable[[str], None],
            cancel_fn: Callable[[], bool],
        ) -> Any:
            return import_audio_pack(
                pack_dir,
                dest_root,
                pack_id=pack_id,
                progress=progress_fn,
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )

        return cls(runner)

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/failed accordingly."""
        try:
            result = self._runner(
                lambda msg: self.progress.emit(msg),
                # is_cancelled is a property on the base class; wrap to a callable
                lambda: self.is_cancelled,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "pack_id", "")),
                "format": getattr(result, "format", ""),
            }
            self.import_finished.emit(result.pack_id, meta)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            logger.exception("AudioPackImportWorker unhandled exception")
            self.failed.emit(str(exc))
