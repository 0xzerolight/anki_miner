"""Audio pack import orchestration (add / per-row reimport).

Mirrors :class:`~anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportFlow`.
Owns the :class:`~anki_miner.gui.workers.import_worker.ImportWorker`
lifecycle and every dialog in the import flows.  The tab keeps the panel
widgets, the signal wiring, and the narrow chain persist
(``_persist_audio_chain_change``), injected here as callables so the
dependency stays one-way: tab → controller → workers/services.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.controllers.import_flow_common import (
    ModalImportFlowMixin,
    _begin_import_trace,
    _ChainedImportResult,
    _log_import_persist,
    _log_import_picker_enter,
    _log_import_picker_return,
)
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.chain_settings_panel_base import MutationToken
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.audio_packs.formats import scan_importable_packs
from anki_miner.services.audio_packs.importer import derive_pack_id
from anki_miner.services.audio_packs.storage import read_meta
from anki_miner.utils.i18n import tr_format

# Upstream source priority for newly imported packs inserted into the chain.
# Lower index = higher priority (queried first).  Keys are canonical pack_ids
# as returned by _derive_pack_id (which maps canonical folder names such as
# "nhk16_files" → "nhk16", "forvo_files" → "forvo", etc.).
# Unknown pack_ids sort after all known ones (stable).
_PACK_PRIORITY: dict[str, int] = {
    "nhk16": 0,
    "shinmeikai8": 1,
    "forvo": 2,
    "jpod": 3,
    "jpod_alternate": 4,
}


class AudioPackImportFlow(ModalImportFlowMixin):
    """Drives audio pack directory imports for the Settings → Audio panel.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab).
        panel: The audio pack settings panel (chain state, registry refresh).
        get_config: Returns the tab's *current* config.
        persist_chain: The tab's narrow chain persist
            (``SettingsTab._persist_audio_chain_change``) — saves a chain
            mutation to disk and notifies listeners without running the full
            Save pipeline.
    """

    def __init__(
        self,
        parent: QWidget,
        panel: AudioPackSettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[tuple[AudioSourceEntry, ...]], None],
        notify_config_changed: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        self._notify_config_changed = notify_config_changed
        # Long-lived worker reference: ImportWorker is a QThread and would be
        # destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []
        self._mutation_token: MutationToken | None = None

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        Returns active and retained import workers so ``SettingsTab.iter_close_workers``
        can chain it into the single ``BackgroundTaskController._join_worker_for_close``
        policy (cancel + bounded grace join + laggard deferral).  A ``None``
        entry (idle flow) is filtered by ``_join_worker_for_close``.
        """
        return self._iter_import_workers()

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Acquire/release the panel token that gates every mutation control."""
        if enabled:
            token = self._mutation_token
            self._mutation_token = None
            if token is not None:
                self._panel.release(token)
        elif self._mutation_token is None:
            self._mutation_token = self._panel.hold_mutation("import")

    def _begin_mutation(self, kind: str) -> bool:
        if self._mutation_token is not None or not self._panel.prepare_for_mutation():
            return False
        self._mutation_token = self._panel.hold_mutation(kind)
        return True

    def _chain_with_new_packs_inserted(self, new_pack_ids: list[str]) -> tuple[AudioSourceEntry, ...]:
        """Return current chain with new packs inserted above first enabled jpod101.

        Priority order: newly imported packs are placed ABOVE the first enabled
        jpod101 entry (or appended at the end if none), preserving upstream
        priority order among the batch itself.  Existing entries are preserved
        and any duplicate pack_id is removed before re-insertion so a re-added
        pack appears at the priority slot rather than duplicating.
        """
        current = list(self._panel.get_chain())

        # Remove any pre-existing entries with the same pack_id so a re-added
        # pack takes the new priority slot rather than appearing twice.
        current = [e for e in current if e.kind == "jpod101" or e.pack_id not in new_pack_ids]

        new_entries = [AudioSourceEntry(kind="pack", pack_id=pid, enabled=True) for pid in new_pack_ids]

        # Find the first enabled jpod101 entry to insert before it.
        insert_idx: int | None = None
        for i, entry in enumerate(current):
            if entry.kind == "jpod101" and entry.enabled:
                insert_idx = i
                break

        if insert_idx is not None:
            current[insert_idx:insert_idx] = new_entries
        else:
            current.extend(new_entries)

        return tuple(current)

    def add_pack(
        self,
        *,
        _scan_result: tuple[str, list[tuple[Path, str]]] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Prompt for a directory and import all detectable audio packs in it."""
        trace_id = _trace_id or _begin_import_trace("audio pack add")
        if _scan_result is None:
            if not self._begin_mutation("add"):
                return
            picker_started = _log_import_picker_enter(trace_id, "audio pack folder")

            def _on_picked(chosen_dir: str) -> None:
                _log_import_picker_return(trace_id, "audio pack folder", picker_started, chosen_dir)
                if not chosen_dir:
                    self._set_import_buttons_enabled(True)
                    return

                def _on_done(result: object) -> None:
                    assert isinstance(result, list)
                    self.add_pack(_scan_result=(chosen_dir, result), _trace_id=trace_id)

                def _on_error(message: str) -> None:
                    self._set_import_buttons_enabled(True)
                    self._report_import_issue(
                        QCoreApplication.translate("AudioPackImportFlow", "That folder could not be scanned."),
                        message,
                    )

                self._run_latest_scan(
                    lambda is_cancelled: scan_importable_packs(
                        Path(chosen_dir),
                        cancel_check=is_cancelled,
                    ),
                    _on_done,
                    _on_error,
                    pass_cancel_check=True,
                )

            file_dialogs.pick_directory(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Choose audio pack folder"),
                resolve_start_dir(None, file_mode=False),
                on_done=_on_picked,
            )
            return

        chosen_dir, packs = _scan_result
        # Sort by upstream source priority so completion order = priority order
        # and _chain_with_new_packs_inserted preserves the correct sequence.
        # Unknown pack_ids land after all known ones (stable sort).
        packs.sort(key=lambda pd_fmt: _PACK_PRIORITY.get(derive_pack_id(pd_fmt[0].name), len(_PACK_PRIORITY)))
        if not packs:
            self._report_import_issue(
                tr_format(
                    QCoreApplication.translate(
                        "AudioPackImportFlow",
                        "No recognisable audio packs were found in:\n%1\n\n"
                        "Supported formats: AJT (index.json + media/), NHK16 (entries.json + audio/), "
                        "Forvo (speaker subdirectories), JPod legacy ({reading} - {expression} stems).",
                    ),
                    chosen_dir,
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        # Import all detected packs sequentially using the same chained
        # state-machine pattern as DictionaryImportFlow.reimport_all.
        dest_root = self._get_config().audio_packs_root

        def make_worker(job: tuple[Path, str]) -> ImportWorker:
            pack_dir, _format = job
            return ImportWorker.for_pack(pack_dir, dest_root)

        def format_label(
            index: int,
            total: int,
            job: tuple[Path, str],
            message: str | None,
        ) -> str:
            pack_dir, _format = job
            label = tr_format(
                QCoreApplication.translate("AudioPackImportFlow", "Pack %1 of %2: %3"),
                index,
                total,
                pack_dir.name,
            )
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[tuple[Path, str]]) -> None:
            imported = [pack_id for _job, pack_id, _meta in result.successes]
            errors = [(job[0].name, message) for job, message in result.failures]

            if imported:
                new_chain = self._chain_with_new_packs_inserted(imported)
                self._panel.refresh_registry()
                self._panel.set_chain(new_chain)
                _log_import_persist(trace_id, "start")
                self._persist_chain(new_chain)
                _log_import_persist(trace_id, "done")

            if len(result.successes) == 1 and not result.failures and not result.cancelled:
                # Single pack — no summary needed; the registry refresh is feedback enough.
                return

            # Multi-pack batch: show summary dialog.
            lines: list[str] = []
            if imported:
                lines.append(
                    tr_format(
                        QCoreApplication.translate("AudioPackImportFlow", "Imported %1 audio pack(s):"),
                        len(imported),
                    )
                )
                lines.extend(f"  • {pid}" for pid in imported)
            if errors:
                if lines:
                    lines.append("")
                lines.append(QCoreApplication.translate("AudioPackImportFlow", "Failed:"))
                lines.extend(f"  • {name}: {msg}" for name, msg in errors)
            if result.cancelled:
                if lines:
                    lines.append("")
                lines.append(QCoreApplication.translate("AudioPackImportFlow", "Cancelled before remaining packs."))

            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Audio Packs Added"),
                "\n".join(lines) or QCoreApplication.translate("AudioPackImportFlow", "Done."),
            )

        def on_finished_error(
            exc: Exception,
            _result: _ChainedImportResult[tuple[Path, str]],
        ) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow",
                    "The import finished, but the settings could not be updated.",
                ),
                str(exc),
            )

        self._run_chained_imports(
            jobs=packs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            cancelling_label=QCoreApplication.translate("AudioPackImportFlow", "Cancelling…"),
            determinate=False,
            join_noun="audio pack import worker",
            failure_summary=QCoreApplication.translate("AudioPackImportFlow", "The audio pack could not be imported."),
            missing_result_message=QCoreApplication.translate(
                "AudioPackImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    def add_android_db(self) -> None:
        """Prompt for and register a local-audio-yomichan ``android.db`` file."""
        if not self._begin_mutation("add-android-db"):
            return
        trace_id = _begin_import_trace("android audio database add")
        picker_started = _log_import_picker_enter(trace_id, "android audio database")
        file_dialogs.pick_open_file(
            self._parent,
            QCoreApplication.translate("AudioPackImportFlow", "Choose Android audio database"),
            resolve_start_dir(None, file_mode=True),
            QCoreApplication.translate(
                "AudioPackImportFlow", "Android database (*.db);;SQLite database (*.sqlite *.sqlite3)"
            ),
            on_done=lambda chosen: self._add_android_db_picked(trace_id, picker_started, chosen),
        )

    def _add_android_db_picked(self, trace_id: str, picker_started: float, chosen: str) -> None:
        _log_import_picker_return(trace_id, "android audio database", picker_started, chosen)
        if not chosen:
            self._set_import_buttons_enabled(True)
            return
        try:
            worker = ImportWorker.for_android_audio_db(Path(chosen), self._get_config().audio_packs_root)
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(pack_id: str, meta: dict) -> None:
            new_chain = self._chain_with_new_packs_inserted([pack_id])
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            _log_import_persist(trace_id, "start")
            self._persist_chain(new_chain)
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Android Audio Database Added"),
                tr_format(
                    QCoreApplication.translate("AudioPackImportFlow", "Registered %1 (%2 entries)."),
                    pack_id,
                    f"{meta.get('entry_count', 0):,}",
                ),
            )

        def on_success_error(exc: Exception) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow", "The import finished, but the settings could not be updated."
                ),
                str(exc),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("AudioPackImportFlow", "Registering Android audio database…"),
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            determinate=False,
            join_noun="Android audio database import worker",
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "The Android audio database could not be added."
            ),
            refusal_message=QCoreApplication.translate(
                "AudioPackImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("AudioPackImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "AudioPackImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
            on_success_error=on_success_error,
        )

    def reimport_pack(self, pack_id: str) -> None:
        """Prompt for the pack's source directory and run explicit repair.

        Fixes moved-folder scenarios: the user picks the new location and the
        importer preserves the pack_id. Invalid old slots are quarantined
        before no-clobber promotion and restored if repair fails.
        """
        if not self._begin_mutation("reimport"):
            return
        trace_id = _begin_import_trace("audio pack reimport")
        index_path = self._get_config().audio_packs_root / pack_id / "index.sqlite"
        try:
            is_android_db = read_meta(index_path).get("format") == "android_db"
        except (OSError, ValueError, sqlite3.Error):
            is_android_db = False
        if is_android_db:
            picker_started = _log_import_picker_enter(trace_id, "android audio database")
            file_dialogs.pick_open_file(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Choose Android audio database to re-import"),
                resolve_start_dir(None, file_mode=True),
                QCoreApplication.translate(
                    "AudioPackImportFlow", "Android database (*.db);;SQLite database (*.sqlite *.sqlite3)"
                ),
                on_done=lambda chosen: self._reimport_android_db_picked(pack_id, trace_id, picker_started, chosen),
            )
            return
        picker_started = _log_import_picker_enter(trace_id, "audio pack folder")
        file_dialogs.pick_directory(
            self._parent,
            QCoreApplication.translate("AudioPackImportFlow", "Choose audio pack folder to re-import"),
            resolve_start_dir(None, file_mode=False),
            on_done=lambda chosen: self._reimport_pack_picked(pack_id, trace_id, picker_started, chosen),
        )

    def _reimport_android_db_picked(self, pack_id: str, trace_id: str, picker_started: float, chosen: str) -> None:
        """Replace an Android-database registration while preserving ``pack_id``."""
        _log_import_picker_return(trace_id, "android audio database", picker_started, chosen)
        if not chosen:
            self._set_import_buttons_enabled(True)
            return
        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            return
        try:
            worker = ImportWorker.for_android_audio_db(
                Path(chosen),
                self._get_config().audio_packs_root,
                pack_id=pack_id,
                overwrite=True,
            )
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(imported_id: str, _meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Android Audio Database Re-imported"),
                tr_format(
                    QCoreApplication.translate("AudioPackImportFlow", "Re-imported %1 successfully."), imported_id
                ),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("AudioPackImportFlow", "Re-importing Android audio database…"),
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            determinate=False,
            join_noun="Android audio database import worker",
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "The Android audio database could not be re-imported."
            ),
            refusal_message=QCoreApplication.translate(
                "AudioPackImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("AudioPackImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "AudioPackImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    def _reimport_pack_picked(self, pack_id: str, trace_id: str, picker_started: float, chosen_dir: str) -> None:
        """Repair ``pack_id`` from the folder ``reimport_pack``'s picker returned."""
        _log_import_picker_return(trace_id, "audio pack folder", picker_started, chosen_dir)
        if not chosen_dir:
            self._set_import_buttons_enabled(True)
            return

        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        try:
            worker = ImportWorker.for_pack_repair(
                Path(chosen_dir),
                self._get_config().audio_packs_root,
                pack_id=pack_id,
            )
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(imported_id: str, _meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Audio Pack Re-imported"),
                tr_format(
                    QCoreApplication.translate("AudioPackImportFlow", "Re-imported %1 successfully."), imported_id
                ),
            )

        # Busy/indeterminate bar (determinate=False) like add_pack — the pack
        # importer reports only progress messages, no percentage granularity.
        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("AudioPackImportFlow", "Re-importing audio pack…"),
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            determinate=False,
            join_noun="audio pack import worker",
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "The audio pack could not be re-imported."
            ),
            refusal_message=QCoreApplication.translate(
                "AudioPackImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("AudioPackImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "AudioPackImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )
