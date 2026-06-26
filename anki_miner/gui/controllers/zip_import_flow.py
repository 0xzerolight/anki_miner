"""Modal Yomitan zip → CSV import flow (pitch accent).

Extracted from ``SettingsTab`` (T-66). Owns the worker lifecycle, the modal
``QProgressDialog`` + ``QEventLoop`` scaffold, the ``.pending`` staging file,
and the deferred-promotion closure; the tab keeps its thin per-flow wrapper
(selector + labels + decline fallback) and the save-time commit ordering.

Frequency previously shared this engine but now has its own multi-source
import flow (:mod:`anki_miner.gui.controllers.frequency_import_flow`), so this
flow only ever drives the pitch importer.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker
from anki_miner.services.pitch_accent import YomitanPitchImportResult
from anki_miner.utils.i18n import tr_format


class YomitanCsvLabels(NamedTuple):
    """User-facing strings for the pitch-accent zip import flow.

    Everything else in :meth:`ZipImportFlow.run_modal_zip_import` (the
    QProgressDialog + QEventLoop scaffold, the overwrite guard, the staged
    ``.pending`` write, cancel suppression) is label-agnostic.
    """

    progress: str  # QProgressDialog label, e.g. "Importing pitch accent dictionary…"
    overwrite_title: str  # overwrite-confirm dialog title
    failure_title: str  # import-failure warning title
    success_title: str  # post-import information dialog title


class ZipImportFlow:
    """Runs modal Yomitan CSV zip imports and stages their results.

    Owned by ``SettingsTab``; ``parent`` is the tab widget, used only as the
    Qt parent for dialogs and the local event loop so modality and lifetime
    behave exactly as they did when this code lived on the tab.
    """

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        # Long-lived worker reference; the pitch worker is a QThread and would
        # be destroyed mid-run if it fell out of scope before joining. The
        # pitch flow stores it here.
        self._active_pitch_worker: YomitanCsvImportWorker | None = None
        # Deferred CSV-promotion closure (T-10). A pitch zip import stages its
        # CSV to a sibling ``.pending`` file; the promotion (os.replace into
        # place + selector update + success dialog) is held here and run by
        # commit_pending_csv_imports() once the import has passed.
        self._pending_pitch_commit: Callable[[], None] | None = None

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        Returns the CSV import worker so ``SettingsTab.iter_close_workers`` can
        chain it into the single ``BackgroundTaskController._join_worker_for_close``
        policy (cancel + bounded grace join + laggard deferral). A ``None``
        entry (idle flow) is filtered by ``_join_worker_for_close``.
        """
        return (self._active_pitch_worker,)

    def run_modal_zip_import(
        self,
        *,
        selector: FileSelector,
        dest_name: str,
        worker_factory: Callable[[Path, Path], YomitanCsvImportWorker],
        worker_slot_attr: str,
        commit_slot_attr: str,
        decline_fallback: Path,
        labels: YomitanCsvLabels,
    ) -> Path | None:
        """Resolve a Yomitan pitch-accent selector path for persistence.

        Engine behind ``SettingsTab._resolve_pitch_accent_path``. Parametrized
        by the user-facing ``labels``, the importer fn bound into the
        ``YomitanCsvImportWorker``, and which ``self`` slots hold the
        live-worker GC reference (``worker_slot_attr``) and the
        deferred-promotion closure (``commit_slot_attr``). (Frequency now has
        its own multi-source import flow and no longer uses this engine.)

        If ``selector`` points at a Yomitan zip, the importer runs in
        ``worker_factory`` (a background QThread driven by a modal
        ``QProgressDialog`` + a local ``QEventLoop`` so this method stays
        blocking for the caller), staging its CSV to a sibling ``.pending``
        file under ``ANKI_MINER_HOME / dest_name``. The destructive promotion
        (os.replace into place, selector update, success dialog) is stored on
        ``self.<commit_slot_attr>`` and run by
        :meth:`commit_pending_csv_imports` only once *every* import in the
        save has passed (T-10) — so a later failure can never leave the
        existing CSV half-overwritten. CSV/TSV paths pass through unchanged.

        Returns:
            * ``Path("")`` if no path is selected.
            * The original CSV/TSV path if not a zip.
            * The destination CSV path on successful import (promotion deferred).
            * ``decline_fallback`` if the user declined to overwrite an existing
              CSV (other settings still save).
            * ``None`` if the import failed or was cancelled (caller aborts
              the whole save).
        """
        # No staged promotion unless a zip import succeeds below.
        setattr(self, commit_slot_attr, None)

        raw = selector.get_path()
        if not raw:
            return Path("")
        if not raw.lower().endswith(".zip"):
            return Path(raw)

        zip_path = Path(raw)
        dest_csv = ANKI_MINER_HOME / dest_name
        pending_csv = dest_csv.with_suffix(dest_csv.suffix + ".pending")

        # Overwrite guard. atomic_write_csv only protects against mid-write
        # failures, not intentional clobbering of a user's existing CSV.
        if dest_csv.exists() and dest_csv.stat().st_size > 0:
            reply = QMessageBox.question(
                self._parent,
                labels.overwrite_title,
                tr_format(
                    QCoreApplication.translate(
                        "ZipImportFlow", "%1 already exists and will be replaced.\n\nContinue with import?"
                    ),
                    dest_csv,
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                # User opted out of THIS setting; let the rest of the save
                # commit with the existing path unchanged.
                return decline_fallback

        dlg = QProgressDialog(
            labels.progress, QCoreApplication.translate("ZipImportFlow", "Cancel"), 0, 100, self._parent
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        # Import into the staging file, never directly over dest_csv.
        worker = worker_factory(zip_path, pending_csv)
        setattr(self, worker_slot_attr, worker)  # keep alive across QThread lifetime

        result_holder: dict[str, object] = {}
        loop = QEventLoop(self._parent)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(res: object) -> None:
            result_holder["ok"] = res
            loop.quit()

        def on_failed(err: str) -> None:
            result_holder["err"] = err
            loop.quit()

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)

        worker.start()
        loop.exec()
        dlg.close()
        worker.wait()  # join thread before next save might construct a new worker

        if "err" in result_holder:
            err_msg = str(result_holder["err"])
            if "cancel" not in err_msg.lower():
                QMessageBox.warning(self._parent, labels.failure_title, err_msg)
            # Drop the staging file so a failed import leaves nothing behind.
            pending_csv.unlink(missing_ok=True)
            return None

        result = result_holder["ok"]
        # Only the pitch importer drives this flow now; it exposes the
        # entry_count / source_name / skipped_display_only attributes used below.
        assert isinstance(result, YomitanPitchImportResult)

        def _commit() -> None:
            os.replace(pending_csv, dest_csv)
            # Reflect the imported path back into the UI so subsequent saves
            # don't re-trigger the import every time the user clicks Save.
            selector.set_path(str(dest_csv))
            skipped_note = (
                tr_format(
                    QCoreApplication.translate("ZipImportFlow", " (skipped %1 display-only entries)"),
                    f"{result.skipped_display_only:,}",
                )
                if result.skipped_display_only
                else ""
            )
            QMessageBox.information(
                self._parent,
                labels.success_title,
                tr_format(
                    QCoreApplication.translate("ZipImportFlow", "Imported %1 entries from '%2'."),
                    f"{result.entry_count:,}",
                    result.source_name,
                )
                + skipped_note,
            )

        setattr(self, commit_slot_attr, _commit)
        return dest_csv

    def commit_pending_csv_imports(self) -> None:
        """Promote any staged pitch CSV import and clear it.

        Called once the pitch resolver has succeeded. Runs the deferred commit
        closure — os.replace(``.pending`` → final), selector update, success
        dialog — then resets the slot so a later save starts clean.
        """
        pitch_commit = self._pending_pitch_commit
        self._pending_pitch_commit = None
        if pitch_commit is not None:
            pitch_commit()
