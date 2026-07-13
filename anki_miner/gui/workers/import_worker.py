"""QThread worker that wraps the on-disk resource importers with progress + cancel.

One worker for every "import a file/dir into an on-disk index" flow: a Yomitan
dictionary zip, JMdict XML, a frequency source, or an audio pack. Each domain's
``for_*`` factory builds a ``runner`` closure that drives its importer and
returns ``(resource_id, meta)``; :meth:`ImportWorker.run` executes it off the
GUI thread and surfaces progress, completion, cancellation, and failure as Qt
signals. Cancellation is delegated to the importer via its ``cancel_check``
callback, wired to the base class's thread-safe ``is_cancelled`` flag.

The Yomitan meta-bank → CSV importer (pitch accent) keeps its own
:class:`~anki_miner.gui.workers.yomitan_csv_import_worker.YomitanCsvImportWorker`:
its completion payload is a typed result *object*, not the ``(id, meta)``
contract here, and its constructor takes a pre-bound importer fn rather than a
runner — so unifying it would only obscure both.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.audio_packs.importer import import_audio_pack
from anki_miner.services.dictionary.importers.jmdict_importer import import_jmdict_xml
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.frequency.source_importer import import_frequency_source

logger = logging.getLogger(__name__)

# The runner drives one importer off the GUI thread. It receives an
# ``(current, total, message)`` progress emitter and a cancel predicate, and
# returns ``(resource_id, meta)`` — ``resource_id`` is the on-disk slot id the
# flow chains/pins on, ``meta`` the domain-specific keys the success dialog
# reads (entry_count, source_name, …). Building the meta inside the runner is
# what keeps the worker itself domain-agnostic.
ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]
Runner = Callable[[ProgressFn, CancelFn], "tuple[str, dict[str, Any]]"]


class ImportWorker(CancellableWorker):
    """Imports a dictionary / frequency source / audio pack in the background.

    Signals:
        progress(int, int, str): ``(current, total, message)`` — importer step.
            Importers whose native progress callback is a single string (audio
            pack) adapt to the triplet inside their ``for_*`` runner closure.
        import_finished(str, dict): ``(resource_id, meta)`` — emitted once on
            success. ``meta`` carries the domain's keys; the flow's success
            dialog reads them.
        cancelled(): emitted (in place of ``failed``) when the run aborts
            because the user cancelled — a distinct signal so callers suppress
            the error dialog explicitly instead of substring-matching the error
            text.
        failed(str): error message for a genuine failure (never fired on
            cancel).

    The completion signal is named ``import_finished`` to avoid collision with
    ``QThread.finished``, which the codebase uses for cleanup wiring.
    """

    # current, total, message
    progress = pyqtSignal(int, int, str)
    # resource_id, meta dict
    import_finished = pyqtSignal(str, dict)
    # emitted instead of failed when the user cancelled
    cancelled = pyqtSignal()
    # error message
    failed = pyqtSignal(str)

    def __init__(self, runner: Runner, parent: Any = None) -> None:
        super().__init__(parent)
        self._runner = runner

    @classmethod
    def for_yomitan(
        cls,
        zip_path: Path,
        dest_root: Path,
        overwrite: bool = False,
        dict_id: str | None = None,
    ) -> ImportWorker:
        """Build a worker that imports a Yomitan-format dictionary zip.

        ``dict_id`` pins the on-disk slot (see ``import_yomitan_zip``); re-import
        flows pass the existing slot id so a title with a changing date rebuilds
        the index in place instead of forking a new folder.
        """

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_yomitan_zip(
                zip_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
                overwrite=overwrite,
                dict_id=dict_id,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "dict_id", "")),
                "skipped_malformed": getattr(result, "skipped_malformed", 0),
                "media_warnings": list(getattr(result, "media_warnings", ())),
            }
            return result.dict_id, meta

        return cls(runner)

    @classmethod
    def for_jmdict(cls, xml_path: Path, dest_root: Path) -> ImportWorker:
        """Build a worker that imports JMdict XML."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_jmdict_xml(
                xml_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "dict_id", "")),
                "skipped_malformed": getattr(result, "skipped_malformed", 0),
                "media_warnings": list(getattr(result, "media_warnings", ())),
            }
            return result.dict_id, meta

        return cls(runner)

    @classmethod
    def for_source(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str | None = None,
        source_name: str | None = None,
    ) -> ImportWorker:
        """Build a worker that imports a frequency source file.

        ``source_name`` is forwarded so reimport can preserve the existing
        display name (see ``import_frequency_source``).
        """

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_frequency_source(
                input_path,
                dest_root,
                source_id=source_id,
                source_name=source_name,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "source_id", "")),
                "format": getattr(result, "format", ""),
                "skipped_malformed": getattr(result, "skipped_malformed", 0),
                "converted_to_ranks": getattr(result, "converted_to_ranks", False),
                "is_categorical": getattr(result, "is_categorical", False),
            }
            return result.source_id, meta

        return cls(runner)

    @classmethod
    def for_pack(
        cls,
        pack_dir: Path,
        dest_root: Path,
        *,
        pack_id: str | None = None,
        overwrite: bool = False,
    ) -> ImportWorker:
        """Build a worker that imports an audio pack directory.

        The audio pack importer reports progress as a single human-readable
        string; the runner adapts it to the ``(cur, total, msg)`` triplet the
        worker emits (indeterminate cur/total — the flow shows only the label).
        """

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_audio_pack(
                pack_dir,
                dest_root,
                pack_id=pack_id,
                progress=lambda msg: progress_fn(0, 0, msg),
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "pack_id", "")),
                "format": getattr(result, "format", ""),
            }
            return result.pack_id, meta

        return cls(runner)

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/cancelled/failed."""
        try:
            resource_id, meta = self._runner(
                lambda cur, total, msg: self.progress.emit(cur, total, msg),
                # is_cancelled is a property on the base class; wrap to a callable
                lambda: self.is_cancelled,
            )
            self.import_finished.emit(resource_id, meta)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            # A cancel aborts the importer with an exception too; route it to the
            # distinct ``cancelled`` signal so callers never confuse it with a
            # genuine error whose message merely contains the word "cancel".
            if self.check_cancelled():
                self.cancelled.emit()
            else:
                logger.exception("ImportWorker unhandled exception")
                self.failed.emit(str(exc))
