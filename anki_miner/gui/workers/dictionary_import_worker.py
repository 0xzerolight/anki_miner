"""QThread worker that wraps the dictionary importers with progress + cancel.

Runs an importer (Yomitan zip or JMdict XML) off the GUI thread and surfaces
progress, completion, and failure as Qt signals. Cancellation is delegated to
the importer via its ``cancel_check`` callback, which is wired to the base
class's thread-safe ``is_cancelled`` flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.dictionary.importers.jmdict_importer import import_jmdict_xml
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip


@dataclass(frozen=True)
class _Job:
    """Closure-bound importer call. ``runner`` receives ``(progress_fn, cancel_fn)``."""

    runner: Callable[[Callable[[int, int, str], None], Callable[[], bool]], Any]


class DictionaryImportWorker(CancellableWorker):
    """Imports a dictionary in the background.

    Signals:
        progress(int, int, str): ``(current, total, message)`` — importer step.
        finished(str, dict): ``(dict_id, meta)`` — emitted once on success.
            ``meta`` carries ``entry_count`` and ``source_name``.
        failed(str): error message, including cancellation.

    Note: ``finished`` and ``failed`` shadow QThread's built-in ``finished``
    signal with explicit signatures expected by the GUI. PyQt's signal
    machinery resolves the override correctly; do not rename to "match" the
    base class.
    """

    # current, total, message
    progress = pyqtSignal(int, int, str)
    # dict_id, meta dict (entry_count, source_name)
    finished = pyqtSignal(str, dict)
    # error message
    failed = pyqtSignal(str)

    def __init__(self, job: _Job, parent: Any = None) -> None:
        super().__init__(parent)
        self._job = job

    @classmethod
    def for_yomitan(
        cls,
        zip_path: Path,
        dest_root: Path,
        overwrite: bool = False,
    ) -> DictionaryImportWorker:
        """Build a worker that imports a Yomitan-format zip."""

        def runner(
            progress_fn: Callable[[int, int, str], None],
            cancel_fn: Callable[[], bool],
        ) -> Any:
            return import_yomitan_zip(
                zip_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )

        return cls(_Job(runner=runner))

    @classmethod
    def for_jmdict(cls, xml_path: Path, dest_root: Path) -> DictionaryImportWorker:
        """Build a worker that imports JMdict XML."""

        def runner(
            progress_fn: Callable[[int, int, str], None],
            cancel_fn: Callable[[], bool],
        ) -> Any:
            return import_jmdict_xml(
                xml_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )

        return cls(_Job(runner=runner))

    def run(self) -> None:
        """Run the importer and emit progress/finished/failed accordingly."""
        try:
            result = self._job.runner(
                lambda cur, total, msg: self.progress.emit(cur, total, msg),
                # is_cancelled is a property on the base class; wrap to a callable
                lambda: self.is_cancelled,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "dict_id", "")),
            }
            self.finished.emit(result.dict_id, meta)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            self.failed.emit(str(exc))
