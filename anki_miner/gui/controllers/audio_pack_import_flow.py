"""Audio pack import orchestration (add / per-row reimport).

Mirrors :class:`~anki_miner.gui.controllers.dictionary_import_flow.DictionaryImportFlow`.
Owns the :class:`~anki_miner.gui.workers.import_worker.ImportWorker`
lifecycle and every dialog in the import flows.  The tab keeps the panel
widgets, the signal wiring, and the narrow chain persist
(``_persist_audio_chain_change``), injected here as callables so the
dependency stays one-way: tab → controller → workers/services.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, Qt, QTimer
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, insert_above_first_enabled_jpod101
from anki_miner.gui.controllers.import_flow_common import (
    ModalImportFlowMixin,
    _begin_import_trace,
    _ChainedImportResult,
    _log_import_persist,
    _log_import_picker_enter,
    _log_import_picker_return,
    _OnceCallback,
    format_batch_summary,
)
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.chain_settings_panel_base import MutationToken
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.languages.registry import config_language
from anki_miner.services._sqlite_index import language_kwarg, resolve_managed_slot, slot_language_kwarg
from anki_miner.services.audio_packs.formats import scan_importable_packs
from anki_miner.services.audio_packs.importer import derive_pack_id
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.audio_packs.storage import read_meta_cached
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Upstream source priority for newly imported packs inserted into the chain.
# Lower index = higher priority (queried first).  Keys are canonical pack_ids
# as returned by _derive_pack_id (which maps canonical folder names such as
# "nhk16_files" → "nhk16", "forvo_files" → "forvo", etc.).
# Unknown pack_ids sort after all known ones (stable).
#: One batch-reimport job: (kind, pack_id, display name, source path).
_PackJob = tuple[str, str, str, Path]

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
        return insert_above_first_enabled_jpod101(current, new_entries)

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

                # The scan can walk an entire pack tree per candidate folder —
                # over an hour on a large real-world pack — so it gets its own
                # busy dialog: indeterminate, live folder name, working Cancel.
                # Without it the panel is just disabled buttons for the
                # duration and users kill the app.
                scan_status: dict[str, str] = {}
                dlg = QProgressDialog(
                    QCoreApplication.translate("AudioPackImportFlow", "Scanning folder for audio packs…"),
                    QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
                    0,
                    0,
                    self._parent,
                )
                dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
                dlg.setAutoClose(False)
                dlg.setAutoReset(False)
                dlg.setMinimumDuration(0)
                label_timer = QTimer(dlg)
                label_timer.setInterval(500)

                def _tick() -> None:
                    name = scan_status.get("name")
                    if name:
                        dlg.setLabelText(
                            tr_format(
                                QCoreApplication.translate("AudioPackImportFlow", "Scanning %1 …"),
                                name,
                            )
                        )

                label_timer.timeout.connect(_tick)
                label_timer.start()
                dlg.show()
                closed = {"done": False}

                def _close_dialog() -> None:
                    if closed["done"]:
                        return
                    closed["done"] = True
                    label_timer.stop()
                    with contextlib.suppress(TypeError):
                        dlg.canceled.disconnect(_on_cancel)
                    dlg.close()
                    dlg.deleteLater()

                def _on_cancel() -> None:
                    logger.info("Import trace %s scan cancelled by user", trace_id)
                    self._cancel_active_scan()
                    _close_dialog()
                    self._set_import_buttons_enabled(True)

                dlg.canceled.connect(_on_cancel)

                def _on_done(result: object) -> None:
                    _close_dialog()
                    assert isinstance(result, list)
                    self.add_pack(_scan_result=(chosen_dir, result), _trace_id=trace_id)

                def _on_error(message: str) -> None:
                    _close_dialog()
                    self._set_import_buttons_enabled(True)
                    self._report_import_issue(
                        QCoreApplication.translate("AudioPackImportFlow", "That folder could not be scanned."),
                        message,
                    )

                self._run_latest_scan(
                    lambda is_cancelled: scan_importable_packs(
                        Path(chosen_dir),
                        cancel_check=is_cancelled,
                        # Runs on the scan thread; the GUI-side timer polls it.
                        progress=lambda name: scan_status.__setitem__("name", name),
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
        config = self._get_config()
        dest_root = config.audio_packs_root
        # A newly added pack is stamped for the language it is added under;
        # the reimport paths replay the slot's own stamp instead.
        add_language = language_kwarg(config_language(config))

        def make_worker(job: tuple[Path, str]) -> ImportWorker:
            pack_dir, _format = job
            return ImportWorker.for_pack(pack_dir, dest_root, **add_language)

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
            summary = format_batch_summary(
                [
                    (
                        tr_format(
                            QCoreApplication.translate("AudioPackImportFlow", "Imported %1 audio pack(s):"),
                            len(imported),
                        ),
                        [f"  • {pid}" for pid in imported],
                    ),
                    (
                        QCoreApplication.translate("AudioPackImportFlow", "Failed:"),
                        [f"  • {name}: {msg}" for name, msg in errors],
                    ),
                ],
                cancelled_note=(
                    QCoreApplication.translate("AudioPackImportFlow", "Cancelled before remaining packs.")
                    if result.cancelled
                    else None
                ),
                empty=QCoreApplication.translate("AudioPackImportFlow", "Done."),
            )
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Audio Packs Added"),
                summary,
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
            config = self._get_config()
            worker = ImportWorker.for_android_audio_db(
                Path(chosen),
                config.audio_packs_root,
                **language_kwarg(config_language(config)),
            )
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
        try:
            index_path = resolve_managed_slot(self._get_config().audio_packs_root, pack_id) / "index.sqlite"
            is_android_db = read_meta_cached(index_path).get("format") == "android_db"
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
        packs_root = self._get_config().audio_packs_root
        try:
            worker = ImportWorker.for_android_audio_db(
                Path(chosen),
                packs_root,
                pack_id=pack_id,
                overwrite=True,
                **slot_language_kwarg(packs_root / pack_id),
            )
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        self._run_pack_reimport(
            trace_id,
            worker,
            progress_label=QCoreApplication.translate("AudioPackImportFlow", "Re-importing Android audio database…"),
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "The Android audio database could not be re-imported."
            ),
            reimported_title=QCoreApplication.translate("AudioPackImportFlow", "Android Audio Database Re-imported"),
            join_noun="Android audio database import worker",
        )

    def _run_pack_reimport(
        self,
        trace_id: str,
        worker: ImportWorker,
        *,
        progress_label: str,
        failure_summary: str,
        reimported_title: str,
        join_noun: str,
    ) -> None:
        """Drive a reimport that rebuilds an existing slot in place.

        A reimport changes an index without changing the chain, so it persists
        nothing: one registry refresh plus one ``config_changed`` is what makes
        the rebuilt slot live.
        """

        def on_success(imported_id: str, _meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                reimported_title,
                tr_format(
                    QCoreApplication.translate("AudioPackImportFlow", "Re-imported %1 successfully."), imported_id
                ),
            )

        def on_success_error(exc: Exception) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow", "The import finished, but the settings could not be updated."
                ),
                str(exc),
            )

        # Busy/indeterminate bar (determinate=False) like add_pack — the pack
        # importer reports only progress messages, no percentage granularity.
        self._run_modal_import(
            worker=worker,
            progress_label=progress_label,
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            determinate=False,
            join_noun=join_noun,
            failure_summary=failure_summary,
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

        self._run_pack_reimport(
            trace_id,
            worker,
            progress_label=QCoreApplication.translate("AudioPackImportFlow", "Re-importing audio pack…"),
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "The audio pack could not be re-imported."
            ),
            reimported_title=QCoreApplication.translate("AudioPackImportFlow", "Audio Pack Re-imported"),
            join_noun="audio pack import worker",
        )

    # ------------------------------------------------------------------
    # Reimport all
    # ------------------------------------------------------------------

    def reimport_all(
        self,
        *,
        only_ids: frozenset[str] | None = None,
        on_complete: Callable[[], None] | None = None,
        _scan_result: tuple[list[_PackJob], list[str], bool] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Rebuild every chained pack from the source path its meta recorded.

        Audio is the one family that keeps no ``source.<ext>`` copy — the blobs
        stay in the user's own folder and an ``android.db`` is multi-gigabyte —
        so the importer records the external path instead and
        ``AudioPackMeta.source_available`` is what says whether it still
        resolves. A pack whose folder or database is gone is *reported*, never
        prompted for: a batch that stopped on a picker per pack would strand
        the user mid-upgrade. Those land in the summary pointing at the per-row
        Re-import…, which does prompt.

        ``only_ids`` scopes the batch to the ids the startup stale scan found,
        leaving the manual button's ``None`` to mean "everything in the chain",
        matching the other three families.

        Runs sequentially so one ApplicationModal progress dialog tracks the
        whole batch; per-pack failures accumulate rather than aborting the loop,
        and ``config_changed`` fires once at the end.

        ``on_complete`` fires exactly once on every terminal path, including the
        refusals and the nothing-to-do case. The startup prompt uses it to run
        one family after another.
        """
        done = _OnceCallback(on_complete)
        trace_id = _trace_id or _begin_import_trace("audio pack reimport all")
        if _scan_result is None:
            if not self._begin_mutation("reimport-all"):
                done()
                return
            packs_root = self._get_config().audio_packs_root
            chain = self._panel.get_chain()

            def _scan() -> tuple[list[_PackJob], list[str], bool]:
                registry = AudioPackRegistry(packs_root)
                registry.load()
                metas = registry.packs
                jobs: list[_PackJob] = []
                skipped: list[str] = []
                # True once any chain entry matches the requested scope, so the
                # nothing-to-do branch below can tell "the chain has no packs"
                # apart from "only_ids named a pack that isn't in the chain
                # anymore" (the startup stale scan found it, the user removed
                # it before repair ran). Irrelevant when only_ids is None
                # (unscoped — the manual button's "everything" case).
                only_ids_matched = only_ids is None
                for entry in chain:
                    if entry.kind != "pack" or not entry.pack_id:
                        continue
                    if only_ids is not None and entry.pack_id not in only_ids:
                        continue
                    only_ids_matched = True
                    meta = metas.get(entry.pack_id)
                    if meta is None:
                        skipped.append(entry.pack_id)
                        continue
                    display = meta.source or meta.pack_id
                    if not meta.source_available:
                        skipped.append(display)
                        continue
                    if meta.format == "android_db" and meta.source_db is not None:
                        jobs.append(("android_db", meta.pack_id, display, meta.source_db))
                    else:
                        jobs.append(("folder", meta.pack_id, display, meta.pack_dir))
                return jobs, skipped, only_ids_matched

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_all(
                    only_ids=only_ids,
                    on_complete=on_complete,
                    _scan_result=result,
                    _trace_id=trace_id,
                )

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(
                    QCoreApplication.translate("AudioPackImportFlow", "The audio pack folder could not be scanned."),
                    message,
                )
                done()

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        jobs, skipped, only_ids_matched = _scan_result

        if not jobs:
            if skipped:
                body = QCoreApplication.translate(
                    "AudioPackImportFlow",
                    "No audio packs eligible for automatic repair were found.\n\n"
                    "Skipped (source folder or database not found; use per-row Re-import…):\n",
                ) + "\n".join(f"  • {name}" for name in skipped)
            elif not only_ids_matched:
                body = QCoreApplication.translate(
                    "AudioPackImportFlow", "The selected audio pack is no longer in the chain."
                )
            else:
                body = QCoreApplication.translate("AudioPackImportFlow", "No audio packs in the chain.")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Nothing to reimport"),
                body,
            )
            self._set_import_buttons_enabled(True)
            done()
            return

        # Drop sqlite handles before any worker touches the pack folders. On
        # Windows the importer's directory rename fails with "Access denied"
        # while a service still holds its read-only connection open (Issue #32).
        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            done()
            return

        def make_worker(job: _PackJob) -> ImportWorker:
            kind, pack_id, _display, source_path = job
            packs_root = self._get_config().audio_packs_root
            if kind == "android_db":
                # Pin the slot id and overwrite: import_android_audio_db proves
                # ownership before replacing, which is the repair contract. It
                # re-registers the same external database — nothing is copied.
                return ImportWorker.for_android_audio_db(
                    source_path,
                    packs_root,
                    pack_id=pack_id,
                    overwrite=True,
                    **slot_language_kwarg(packs_root / pack_id),
                )
            return ImportWorker.for_pack_repair(
                source_path,
                packs_root,
                pack_id=pack_id,
            )

        def format_label(index: int, total: int, job: _PackJob, message: str | None) -> str:
            _kind, _pack_id, display, _source_path = job
            label = tr_format(
                QCoreApplication.translate("AudioPackImportFlow", "Audio pack %1 of %2: %3"),
                index,
                total,
                display,
            )
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[_PackJob]) -> None:
            # One refresh + one config_changed for the whole batch so cached
            # services rebuild once, not N times.
            _log_import_persist(trace_id, "start")
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

            reimported = [job[2] for job, _pack_id, _meta in result.successes]
            errors = [(job[2], message) for job, message in result.failures]
            summary = format_batch_summary(
                [
                    (
                        tr_format(
                            QCoreApplication.translate("AudioPackImportFlow", "Re-imported %1 audio pack(s):"),
                            len(reimported),
                        ),
                        [f"  • {name}" for name in reimported],
                    ),
                    (
                        QCoreApplication.translate(
                            "AudioPackImportFlow",
                            "Skipped (source folder or database not found; use per-row Re-import…):",
                        ),
                        [f"  • {name}" for name in skipped],
                    ),
                    (
                        QCoreApplication.translate("AudioPackImportFlow", "Failed:"),
                        [f"  • {name}: {message}" for name, message in errors],
                    ),
                ],
                cancelled_note=(
                    QCoreApplication.translate("AudioPackImportFlow", "Cancelled before the batch finished.")
                    if result.cancelled
                    else None
                ),
                empty=QCoreApplication.translate("AudioPackImportFlow", "Nothing to do."),
            )
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("AudioPackImportFlow", "Audio Packs Re-imported"),
                summary,
            )
            done()

        def on_finished_error(exc: Exception, _result: _ChainedImportResult[_PackJob]) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "AudioPackImportFlow", "The import finished, but the settings could not be updated."
                ),
                str(exc),
            )
            done()

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=QCoreApplication.translate("AudioPackImportFlow", "Cancel"),
            cancelling_label=QCoreApplication.translate("AudioPackImportFlow", "Cancelling…"),
            # Busy/indeterminate like add_pack — the pack importer reports
            # progress messages, not percentages.
            determinate=False,
            join_noun="audio pack import worker",
            failure_summary=QCoreApplication.translate(
                "AudioPackImportFlow", "Some audio packs could not be re-imported."
            ),
            missing_result_message=QCoreApplication.translate(
                "AudioPackImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )
