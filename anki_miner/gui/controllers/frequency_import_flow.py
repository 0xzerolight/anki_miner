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
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.controllers.import_flow_common import (
    ModalImportFlowMixin,
    _begin_import_trace,
    _log_import_persist,
    _log_import_picker_enter,
    _log_import_picker_return,
)
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels.chain_settings_panel_base import MutationToken
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.source_importer import FREQUENCY_SOURCE_SUFFIXES
from anki_miner.utils.i18n import tr_format


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

        A ``None`` entry (idle flow) is filtered by
        ``BackgroundTaskController._join_worker_for_close``.
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
        if not self._begin_mutation("add"):
            return
        trace_id = _begin_import_trace("frequency add")
        picker_started = _log_import_picker_enter(trace_id, "frequency source")
        file_dialogs.pick_open_file(
            self._parent,
            QCoreApplication.translate("FrequencyImportFlow", "Choose frequency source"),
            resolve_start_dir(None, file_mode=True),
            self._source_picker_filter(),
            on_done=lambda chosen: self._add_source_picked(trace_id, picker_started, chosen),
        )

    def _add_source_picked(self, trace_id: str, picker_started: float, chosen: str) -> None:
        """Import the file ``add_source``'s picker returned as a new source."""
        _log_import_picker_return(trace_id, "frequency source", picker_started, chosen)
        if not chosen:
            self._set_import_buttons_enabled(True)
            return

        try:
            worker = ImportWorker.for_source(Path(chosen), self._get_config().freqs_root, overwrite=False)
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(source_id: str, meta: dict) -> None:
            new_chain = self._chain_with_new_source_appended(source_id)
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            _log_import_persist(trace_id, "start")
            self._persist_chain(new_chain)
            _log_import_persist(trace_id, "done")
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

        def on_success_error(exc: Exception) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "FrequencyImportFlow",
                    "The import finished, but the settings could not be updated.",
                ),
                str(exc),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("FrequencyImportFlow", "Importing frequency source…"),
            cancel_label=QCoreApplication.translate("FrequencyImportFlow", "Cancel"),
            determinate=False,
            join_noun="frequency import worker",
            failure_summary=QCoreApplication.translate(
                "FrequencyImportFlow", "The frequency source could not be imported."
            ),
            refusal_message=QCoreApplication.translate(
                "FrequencyImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("FrequencyImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "FrequencyImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
            on_success_error=on_success_error,
        )

    def reimport_source(
        self,
        source_id: str,
        *,
        _scan_result: tuple[Path, Path | None, str | None] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Re-import an existing source into the same id.

        The importer copied the original input alongside the index as
        ``source.<ext>`` on first import, so a re-import can re-run without the
        user re-picking the file. If that copy is gone (older import / moved
        folder), prompt the user to re-pick.
        """
        trace_id = _trace_id or _begin_import_trace("frequency reimport")
        if _scan_result is None:
            if not self._begin_mutation("reimport"):
                return
            dest_root = self._get_config().freqs_root
            source_dir = dest_root / source_id

            def _scan() -> tuple[Path, Path | None, str | None]:
                source_file = self._find_source_copy(source_dir)
                existing_name: str | None = source_id
                try:
                    stored_name = storage.read_meta(source_dir / "index.sqlite").get("source_name")
                except Exception:  # noqa: BLE001 — corrupt metadata must not strand a saved source
                    pass
                else:
                    if isinstance(stored_name, str):
                        existing_name = stored_name
                return dest_root, source_file, existing_name

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_source(source_id, _scan_result=result, _trace_id=trace_id)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(
                    QCoreApplication.translate("FrequencyImportFlow", "That folder could not be scanned."),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        dest_root, source_file, existing_name = _scan_result
        if source_file is None:
            # No persisted copy — ask, then rejoin the shared tail from the
            # callback. The picker no longer blocks, so the rest of this flow
            # cannot simply fall through to it.
            picker_started = _log_import_picker_enter(trace_id, "frequency source")

            def _on_picked(chosen: str) -> None:
                _log_import_picker_return(trace_id, "frequency source", picker_started, chosen)
                if not chosen:
                    self._set_import_buttons_enabled(True)
                    return
                self._continue_reimport(source_id, trace_id, dest_root, Path(chosen), existing_name)

            file_dialogs.pick_open_file(
                self._parent,
                QCoreApplication.translate("FrequencyImportFlow", "Choose frequency source to re-import"),
                resolve_start_dir(None, file_mode=True),
                self._source_picker_filter(),
                on_done=_on_picked,
            )
            return

        self._continue_reimport(source_id, trace_id, dest_root, source_file, existing_name)

    def _continue_reimport(
        self,
        source_id: str,
        trace_id: str,
        dest_root: Path,
        source_file: Path,
        existing_name: str | None,
    ) -> None:
        """Repair ``source_id`` from ``source_file``.

        Shared tail of :meth:`reimport_source`: reached directly when the
        persisted ``source.<ext>`` copy exists, and from the picker callback
        when the user had to re-pick.
        """
        # Preserve the existing display name across reimport. Corrupt or missing
        # metadata falls back to the stable source id instead of the persisted
        # copy's generic "source" stem.
        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "FrequencyImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        try:
            worker = ImportWorker.for_source_repair(
                source_file,
                dest_root,
                source_id=source_id,
                source_name=existing_name or source_id,
            )
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(imported_id: str, meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
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
            failure_summary=QCoreApplication.translate(
                "FrequencyImportFlow", "The frequency source could not be re-imported."
            ),
            refusal_message=QCoreApplication.translate(
                "FrequencyImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("FrequencyImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "FrequencyImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    @staticmethod
    def _find_source_copy(source_dir: Path) -> Path | None:
        """Return the persisted ``source.<ext>`` original input, if present."""
        for suffix in FREQUENCY_SOURCE_SUFFIXES:
            candidate = source_dir / ("source" + suffix)
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _source_picker_filter() -> str:
        suffix_globs = " ".join(f"*{suffix}" for suffix in FREQUENCY_SOURCE_SUFFIXES)
        return tr_format(
            QCoreApplication.translate("FrequencyImportFlow", "Frequency source (%1);;All Files (*)"),
            suffix_globs,
        )
