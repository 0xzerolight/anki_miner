"""Frequency-source import orchestration (add / per-row reimport).

Mirrors :class:`~anki_miner.gui.controllers.audio_pack_import_flow.AudioPackImportFlow`.
Owns the :class:`~anki_miner.gui.workers.frequency_import_worker.FrequencyImportWorker`
lifecycle and every dialog in the import flows. The settings tab keeps the panel
widgets, the signal wiring, and the narrow chain persist (injected here as a
callable so the dependency stays one-way: tab → controller → workers/services).

Unlike the audio chain there is no fixed priority insertion point: frequency
sources are purely additive, so a freshly imported source is simply *appended*
(enabled) to the chain — the user reorders later if they care about tie-breaks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.run_off_thread import join_worker
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.workers.frequency_import_worker import FrequencyImportWorker
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Suffixes the per-source dir may hold for the persisted original input,
# checked in order when locating the file to re-import from.
_SOURCE_COPY_SUFFIXES = (".zip", ".csv", ".tsv", ".txt")

# Bounded join for the predecessor import worker before its reference is
# dropped. A stuck worker must never freeze the GUI thread; on timeout we log
# and proceed (mirrors ``MiningTabBase._teardown_previous_run``).
_IMPORT_JOIN_TIMEOUT_MS = 5000


class FrequencyImportFlow:
    """Drives frequency-source imports for the Settings → Frequency panel.

    Plain (non-Qt) class mirroring
    :class:`~anki_miner.gui.controllers.audio_pack_import_flow.AudioPackImportFlow`:
    owns the import-worker lifecycle and every dialog; user feedback is the
    in-flow progress dialog + success/failure message boxes.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab).
        panel: The frequency settings panel (chain state, registry refresh).
        get_config: Returns the tab's *current* config.
        persist_chain: The tab's narrow chain persist — saves a chain mutation
            to disk and notifies listeners without running the full Save
            pipeline.
    """

    def __init__(
        self,
        parent: QWidget,
        panel: FrequencySettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[tuple[FreqEntry, ...]], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        # Long-lived worker reference: FrequencyImportWorker is a QThread and
        # would be destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: FrequencyImportWorker | None = None

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        A ``None`` entry (idle flow) is filtered by
        ``BackgroundTaskController._join_worker_for_close``.
        """
        return (self._active_import_worker,)

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle the add-trigger button. Prevents overlapping import workers."""
        self._panel._add_btn.setEnabled(enabled)

    def _chain_with_new_source_appended(self, source_id: str) -> tuple[FreqEntry, ...]:
        """Return the current chain with ``source_id`` appended (enabled).

        Any pre-existing entry with the same source_id is removed first so a
        re-added source moves to the end rather than duplicating.
        """
        current = [e for e in self._panel.get_chain() if e.source_id != source_id]
        current.append(FreqEntry(source_id=source_id, enabled=True))
        return tuple(current)

    def add_source(self) -> None:
        """Prompt for a frequency file and import it as a new source."""
        chosen, _ = QFileDialog.getOpenFileName(
            self._parent,
            QCoreApplication.translate("FrequencyImportFlow", "Choose frequency source"),
            resolve_start_dir(None, file_mode=True),
            QCoreApplication.translate("FrequencyImportFlow", "Frequency source (*.zip *.csv *.tsv);;All Files (*)"),
        )
        if not chosen:
            return

        dest_root = self._get_config().freqs_root
        dlg = QProgressDialog(
            QCoreApplication.translate("FrequencyImportFlow", "Importing frequency source…"),
            QCoreApplication.translate("FrequencyImportFlow", "Cancel"),
            0,
            0,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = FrequencyImportWorker.for_source(Path(chosen), dest_root)
        prev = self._active_import_worker
        if not join_worker(prev, timeout_ms=_IMPORT_JOIN_TIMEOUT_MS):
            logger.warning(
                "Lingering frequency import worker did not stop within %d ms; replacing it anyway",
                _IMPORT_JOIN_TIMEOUT_MS,
            )
        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            if total:
                dlg.setMaximum(total)
                dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(source_id: str, meta: dict) -> None:
            dlg.close()
            self._set_import_buttons_enabled(True)
            new_chain = self._chain_with_new_source_appended(source_id)
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            self._persist_chain(new_chain)
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Frequency Source Added"),
                tr_format(
                    QCoreApplication.translate("FrequencyImportFlow", "Imported %1 entries from '%2'."),
                    f"{meta.get('entry_count', 0):,}",
                    meta.get("source_name", source_id),
                ),
            )

        def on_failed(err: str) -> None:
            dlg.close()
            self._set_import_buttons_enabled(True)
            if "cancel" not in err.lower():
                QMessageBox.warning(
                    self._parent,
                    QCoreApplication.translate("FrequencyImportFlow", "Import Failed"),
                    err,
                )

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def reimport_source(self, source_id: str) -> None:
        """Re-import an existing source into the same id.

        The importer copied the original input alongside the index as
        ``source.<ext>`` on first import, so a re-import can re-run without the
        user re-picking the file. If that copy is gone (older import / moved
        folder), prompt the user to re-pick.
        """
        dest_root = self._get_config().freqs_root
        source_file = self._find_source_copy(dest_root / source_id)
        if source_file is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Choose frequency source to re-import"),
                resolve_start_dir(None, file_mode=True),
                QCoreApplication.translate(
                    "FrequencyImportFlow", "Frequency source (*.zip *.csv *.tsv);;All Files (*)"
                ),
            )
            if not chosen:
                return
            source_file = Path(chosen)

        dlg = QProgressDialog(
            QCoreApplication.translate("FrequencyImportFlow", "Re-importing frequency source…"),
            QCoreApplication.translate("FrequencyImportFlow", "Cancel"),
            0,
            0,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = FrequencyImportWorker.for_source(source_file, dest_root, source_id=source_id)
        prev = self._active_import_worker
        if not join_worker(prev, timeout_ms=_IMPORT_JOIN_TIMEOUT_MS):
            logger.warning(
                "Lingering frequency import worker did not stop within %d ms; replacing it anyway",
                _IMPORT_JOIN_TIMEOUT_MS,
            )
        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            if total:
                dlg.setMaximum(total)
                dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(imported_id: str, meta: dict) -> None:
            dlg.close()
            self._set_import_buttons_enabled(True)
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Frequency Source Re-imported"),
                tr_format(
                    QCoreApplication.translate("FrequencyImportFlow", "Re-imported %1 successfully."),
                    imported_id,
                ),
            )

        def on_failed(err: str) -> None:
            dlg.close()
            self._set_import_buttons_enabled(True)
            if "cancel" not in err.lower():
                QMessageBox.warning(
                    self._parent,
                    QCoreApplication.translate("FrequencyImportFlow", "Re-import Failed"),
                    err,
                )

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    @staticmethod
    def _find_source_copy(source_dir: Path) -> Path | None:
        """Return the persisted ``source.<ext>`` original input, if present."""
        for suffix in _SOURCE_COPY_SUFFIXES:
            candidate = source_dir / ("source" + suffix)
            if candidate.is_file():
                return candidate
        return None
