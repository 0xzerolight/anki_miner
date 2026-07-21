"""Frequency-source import orchestration (add / per-row reimport).

Mirrors :class:`~anki_miner.gui.controllers.audio_pack_import_flow.AudioPackImportFlow`.
Owns the :class:`~anki_miner.gui.workers.import_worker.ImportWorker`
lifecycle and every dialog in the import flows. The settings tab keeps the panel
widgets, the signal wiring, and the narrow chain persist (injected here as a
callable so the dependency stays one-way: tab → controller → workers/services).

Unlike the audio chain there is no fixed priority insertion point: frequency
sources are purely additive, so a freshly imported source is simply *appended*
(enabled) to the chain — the user reorders later if they care about tie-breaks.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.controllers.import_flow_common import ModalImportFlowMixin
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.frequency import storage
from anki_miner.utils.i18n import tr_format

# Suffixes the per-source dir may hold for the persisted original input,
# checked in order when locating the file to re-import from.
_SOURCE_COPY_SUFFIXES = (".zip", ".csv", ".tsv", ".txt")


class FrequencyImportFlow(ModalImportFlowMixin):
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
        # Long-lived worker reference: ImportWorker is a QThread and would be
        # destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        A ``None`` entry (idle flow) is filtered by
        ``BackgroundTaskController._join_worker_for_close``.
        """
        return self._iter_import_workers()

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle the add-trigger button. Prevents overlapping import workers."""
        self._panel._add_btn.setEnabled(enabled)

    @staticmethod
    def _categorical_note(meta: dict) -> str:
        """Note appended to the add/re-import success message for a word-based
        source, so the changed (excluded-from-rank-filtering) behavior isn't silent."""
        if not meta.get("is_categorical"):
            return ""
        return QCoreApplication.translate(
            "FrequencyImportFlow",
            " This is a word-based source; its level labels show on the card but don't affect frequency-rank filtering.",
        )

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

        worker = ImportWorker.for_source(Path(chosen), self._get_config().freqs_root)

        def on_success(source_id: str, meta: dict) -> None:
            new_chain = self._chain_with_new_source_appended(source_id)
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            self._persist_chain(new_chain)
            skipped = meta.get("skipped_malformed", 0)
            skipped_note = (
                tr_format(
                    QCoreApplication.translate("FrequencyImportFlow", " (skipped %1 malformed entries)"),
                    f"{skipped:,}",
                )
                if skipped
                else ""
            )
            converted_note = (
                QCoreApplication.translate(
                    "FrequencyImportFlow",
                    " This is an occurrence-based source; its counts were converted to ranks.",
                )
                if meta.get("converted_to_ranks")
                else ""
            )
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Frequency Source Added"),
                tr_format(
                    QCoreApplication.translate("FrequencyImportFlow", "Imported %1 entries from '%2'."),
                    f"{meta.get('entry_count', 0):,}",
                    meta.get("source_name", source_id),
                )
                + skipped_note
                + converted_note
                + self._categorical_note(meta),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("FrequencyImportFlow", "Importing frequency source…"),
            cancel_label=QCoreApplication.translate("FrequencyImportFlow", "Cancel"),
            determinate=False,
            join_noun="frequency import worker",
            failure_title=QCoreApplication.translate("FrequencyImportFlow", "Import Failed"),
            on_success=on_success,
        )

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

        # Preserve the existing display name across reimport: without this the
        # CSV path re-derives the name from the generic "source.csv" persisted
        # copy's stem and collapses the label to "source". Read the authoritative
        # SQLite meta (not the sidecar); None for a zip / missing index is fine.
        existing_name = storage.read_meta(dest_root / source_id / "index.sqlite").get("source_name")
        worker = ImportWorker.for_source(source_file, dest_root, source_id=source_id, source_name=existing_name)

        def on_success(imported_id: str, meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Frequency Source Re-imported"),
                tr_format(
                    QCoreApplication.translate("FrequencyImportFlow", "Re-imported %1 successfully."),
                    imported_id,
                )
                + self._categorical_note(meta),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("FrequencyImportFlow", "Re-importing frequency source…"),
            cancel_label=QCoreApplication.translate("FrequencyImportFlow", "Cancel"),
            determinate=False,
            join_noun="frequency import worker",
            failure_title=QCoreApplication.translate("FrequencyImportFlow", "Re-import Failed"),
            on_success=on_success,
        )

    @staticmethod
    def _find_source_copy(source_dir: Path) -> Path | None:
        """Return the persisted ``source.<ext>`` original input, if present."""
        for suffix in _SOURCE_COPY_SUFFIXES:
            candidate = source_dir / ("source" + suffix)
            if candidate.is_file():
                return candidate
        return None
