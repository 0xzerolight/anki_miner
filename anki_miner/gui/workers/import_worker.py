"""QThread worker that wraps the on-disk resource importers with progress + cancel.

One worker for every "import a file/dir into an on-disk index" flow: a Yomitan
dictionary zip, JMdict XML, a frequency source, a pitch accent source, or an
audio pack. Each domain's ``for_*`` factory builds a ``runner`` closure that
drives its importer and returns ``(resource_id, meta)``; :meth:`ImportWorker.run`
executes it off the GUI thread and surfaces progress, completion, cancellation,
and failure as Qt signals. Cancellation is delegated to the importer via its
``cancel_check`` callback, wired to the base class's thread-safe
``is_cancelled`` flag.
"""

from __future__ import annotations

import logging
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.audio_packs.importer import import_android_audio_db, import_audio_pack, repair_audio_pack
from anki_miner.services.dictionary.importers.jmdict_importer import import_jmdict_xml, repair_jmdict_xml
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip, repair_yomitan_zip
from anki_miner.services.frequency.source_importer import import_frequency_source, repair_frequency_source
from anki_miner.services.pitch_accent.source_importer import import_pitch_source, repair_pitch_source

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

    def __init__(self, runner: Runner, parent: Any = None, *, source_path: Path | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._source_path = source_path
        self._trace_id: str | None = None

    def set_trace_id(self, trace_id: str) -> None:
        """Attach the GUI flow correlation id before :meth:`start`."""
        self._trace_id = trace_id

    def _log_trace_input(self) -> None:
        """Log source size from the worker thread, never from the GUI picker."""
        if self._trace_id is None or self._source_path is None:
            return
        try:
            source_stat = self._source_path.stat()
        except OSError:
            size: int | str = "unknown"
        else:
            size = source_stat.st_size if S_ISREG(source_stat.st_mode) else "n/a"
        logger.info(
            "Import trace %s worker input suffix=%s size_bytes=%s",
            self._trace_id,
            self._source_path.suffix.lower() or "<none>",
            size,
        )

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

        return cls(runner, source_path=zip_path)

    @classmethod
    def for_yomitan_repair(
        cls,
        zip_path: Path,
        dest_root: Path,
        *,
        dict_id: str,
    ) -> ImportWorker:
        """Build a worker for explicit source-first repair of one dictionary slot."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = repair_yomitan_zip(
                zip_path,
                dest_root,
                dict_id=dict_id,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "skipped_malformed": result.skipped_malformed,
                "media_warnings": list(result.media_warnings),
            }
            return result.dict_id, meta

        return cls(runner, source_path=zip_path)

    @classmethod
    def for_jmdict(
        cls,
        xml_path: Path,
        dest_root: Path,
        *,
        overwrite: bool = True,
    ) -> ImportWorker:
        """Build a worker that imports JMdict XML."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_jmdict_xml(
                xml_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )
            meta: dict[str, Any] = {
                "entry_count": getattr(result, "entry_count", 0),
                "source_name": getattr(result, "source_name", getattr(result, "dict_id", "")),
                "skipped_malformed": getattr(result, "skipped_malformed", 0),
                "media_warnings": list(getattr(result, "media_warnings", ())),
            }
            return result.dict_id, meta

        return cls(runner, source_path=xml_path)

    @classmethod
    def for_jmdict_repair(
        cls,
        xml_path: Path,
        dest_root: Path,
    ) -> ImportWorker:
        """Build a worker for explicit repair of the fixed JMdict slot."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = repair_jmdict_xml(
                xml_path,
                dest_root,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.dict_id,
                "skipped_malformed": 0,
                "media_warnings": [],
            }
            return result.dict_id, meta

        return cls(runner, source_path=xml_path)

    @classmethod
    def for_source(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str | None = None,
        source_name: str | None = None,
        overwrite: bool = False,
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
                overwrite=overwrite,
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

        return cls(runner, source_path=input_path)

    @classmethod
    def for_source_repair(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str,
        source_name: str,
    ) -> ImportWorker:
        """Build a worker for explicit repair of one frequency slot."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = repair_frequency_source(
                input_path,
                dest_root,
                source_id=source_id,
                source_name=source_name,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "format": result.format,
                "skipped_malformed": result.skipped_malformed,
                "converted_to_ranks": result.converted_to_ranks,
                "is_categorical": result.is_categorical,
            }
            return result.source_id, meta

        return cls(runner, source_path=input_path)

    @classmethod
    def for_pitch_source(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str | None = None,
        source_name: str | None = None,
        overwrite: bool = False,
    ) -> ImportWorker:
        """Build a worker that imports a pitch accent source file.

        ``source_name`` is forwarded so reimport can preserve the existing
        display name (see ``import_pitch_source``).
        """

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_pitch_source(
                input_path,
                dest_root,
                source_id=source_id,
                source_name=source_name,
                progress=progress_fn,
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "format": result.format,
                "skipped_malformed": result.skipped_malformed,
            }
            return result.source_id, meta

        return cls(runner, source_path=input_path)

    @classmethod
    def for_pitch_source_repair(
        cls,
        input_path: Path,
        dest_root: Path,
        *,
        source_id: str,
        source_name: str,
    ) -> ImportWorker:
        """Build a worker for explicit repair of one pitch source slot."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = repair_pitch_source(
                input_path,
                dest_root,
                source_id=source_id,
                source_name=source_name,
                progress=progress_fn,
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "format": result.format,
                "skipped_malformed": result.skipped_malformed,
            }
            return result.source_id, meta

        return cls(runner, source_path=input_path)

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

        return cls(runner, source_path=pack_dir)

    @classmethod
    def for_android_audio_db(
        cls,
        db_path: Path,
        dest_root: Path,
        *,
        pack_id: str | None = None,
        overwrite: bool = False,
    ) -> ImportWorker:
        """Build a worker that registers a local-audio-yomichan ``android.db``."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = import_android_audio_db(
                db_path,
                dest_root,
                pack_id=pack_id,
                progress=lambda msg: progress_fn(0, 0, msg),
                cancel_check=cancel_fn,
                overwrite=overwrite,
            )
            return result.pack_id, {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "format": result.format,
            }

        return cls(runner, source_path=db_path)

    @classmethod
    def for_pack_repair(
        cls,
        pack_dir: Path,
        dest_root: Path,
        *,
        pack_id: str,
    ) -> ImportWorker:
        """Build a worker for explicit repair of one audio-pack slot."""

        def runner(progress_fn: ProgressFn, cancel_fn: CancelFn) -> tuple[str, dict[str, Any]]:
            result = repair_audio_pack(
                pack_dir,
                dest_root,
                pack_id=pack_id,
                progress=lambda msg: progress_fn(0, 0, msg),
                cancel_check=cancel_fn,
            )
            meta: dict[str, Any] = {
                "entry_count": result.entry_count,
                "source_name": result.source_name,
                "format": result.format,
            }
            return result.pack_id, meta

        return cls(runner, source_path=pack_dir)

    def run(self) -> None:
        """Run the importer and emit progress/import_finished/cancelled/failed."""
        self.log_start("ImportWorker")
        self._log_trace_input()
        try:
            resource_id, meta = self._runner(
                lambda cur, total, msg: self.progress.emit(cur, total, msg),
                # is_cancelled is a property on the base class; wrap to a callable
                lambda: self.is_cancelled,
            )
            self.import_finished.emit(resource_id, meta)
        except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
            # A cancel aborts the importer with an exception too; the guard
            # routes it to the distinct ``cancelled`` signal. The discriminator
            # is the OperationCancelled type, not the message text -- this used
            # to compare against the literal "Import cancelled", so sibling
            # cancels ("Download cancelled", "alass installation cancelled")
            # were logged as unhandled exceptions.
            self.report_failure(
                exc,
                context="ImportWorker",
                on_error=self.failed.emit,
                on_cancelled=self.cancelled.emit,
                # ``failed`` drives the import flow's terminal latch: swallowing
                # a post-promotion failure would leave the UI waiting forever.
                cancel_flag_suppresses_error=False,
            )
