"""Dictionary import orchestration (add / per-row reimport / JMdict / Reimport All).

Extracted from ``SettingsTab`` (T-66). Owns the ``ImportWorker``
lifecycles and every dialog in the import flows — including the Reimport-All
chained state machine and its predecessor deferral (T-09). The tab keeps the
panel widgets, the signal wiring, and the narrow chain persist
(``_persist_chain_change``), injected here as callables so the dependency
stays one-way: tab → controller → workers/services.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig, ChainEntry
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
from anki_miner.gui.widgets.panels import DictionarySettingsPanel
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.dictionary.importers.yomitan_importer import derive_dict_id_from_zip, read_yomitan_title
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
from anki_miner.services.dictionary.storage import read_meta
from anki_miner.services.dictionary.superseded import strip_date_bracket
from anki_miner.services.resource_catalog import CATALOG_DICT_SLOT_IDS, LEGACY_DICT_SLOT_IDS
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Slots whose on-disk id is pinned (stable, not title-derived): current catalog
# dicts plus former catalog dicts existing users still have installed.
_PINNED_DICT_SLOT_IDS = CATALOG_DICT_SLOT_IDS | LEGACY_DICT_SLOT_IDS


class DictionaryImportFlow(ModalImportFlowMixin):
    """Drives dictionary zip/XML imports for the Settings → Dictionary panel.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab),
            preserving modality and ``findChild`` discoverability.
        panel: The dictionary settings panel (chain state, registry refresh,
            resource-release hook, import-trigger buttons).
        get_config: Returns the tab's *current* config (it is reassigned on
            every save/persist, so a snapshot would go stale).
        persist_chain: The tab's narrow chain persist
            (``SettingsTab._persist_chain_change``) — saves a chain mutation
            to disk and notifies listeners without running the full Save
            pipeline.
        notify_config_changed: Re-emits ``config_changed`` with the current
            config so cached DefinitionService instances rebuild after a
            reimport rewrites an index in place (no chain change).
    """

    def __init__(
        self,
        parent: QWidget,
        panel: DictionarySettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[tuple[ChainEntry, ...]], None],
        notify_config_changed: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        self._notify_config_changed = notify_config_changed
        # Long-lived worker reference; ImportWorker is a QThread and would be
        # destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        Returns active and retained import workers so ``SettingsTab.iter_close_workers``
        can chain it into the single
        ``BackgroundTaskController._join_worker_for_close`` policy (cancel +
        bounded grace join + laggard deferral).  A ``None`` entry (idle flow) is
        filtered by ``_join_worker_for_close``.
        """
        return self._iter_import_workers()

    def _import_notes(self, meta: dict) -> str:
        """Trailing note about malformed-skipped entries and media warnings.

        Empty when the import was clean; otherwise a blank-line-separated block
        appended to the success dialog so a drastically-reduced or media-lossy
        import is visible to the user (plan 4.7/4.8) rather than silent. Full
        per-file media warnings are also logged.
        """
        notes: list[str] = []
        skipped = meta.get("skipped_malformed", 0)
        if skipped:
            notes.append(
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Skipped %1 malformed entries."),
                    f"{skipped:,}",
                )
            )
        media_warnings = meta.get("media_warnings") or []
        if media_warnings:
            notes.append(
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "%1 media file(s) could not be imported."),
                    f"{len(media_warnings):,}",
                )
            )
            for warning in media_warnings:
                logger.warning("Dictionary media skipped: %s", warning)
        return ("\n\n" + "\n".join(notes)) if notes else ""

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle import-trigger buttons. Prevents overlapping import workers."""
        self._panel._add_btn.setEnabled(enabled)
        self._panel._reimport_btn.setEnabled(enabled)
        self._panel._restore_btn.setEnabled(enabled)
        self._panel.set_per_row_reimport_enabled(enabled)

    def _with_dict_at_top(self, dict_id: str) -> tuple[ChainEntry, ...]:
        """Return the current chain with ``dict_id`` placed (or moved) to the top."""
        chain = list(self._panel.get_chain())
        chain = [e for e in chain if not (e.kind == "indexed" and e.dict_id == dict_id)]
        chain.insert(0, ChainEntry(kind="indexed", dict_id=dict_id, enabled=True))
        return tuple(chain)

    def add_dict(self) -> None:
        """Prompt for a Yomitan zip and run the import worker."""
        trace_id = _begin_import_trace("dictionary add")
        picker_started = _log_import_picker_enter(trace_id, "dictionary zip")
        zip_path_str, _ = file_dialogs.get_open_file_name(
            self._parent,
            QCoreApplication.translate("DictionaryImportFlow", "Choose Yomitan dictionary zip"),
            resolve_start_dir(None, file_mode=True, default_dir=self._get_config().dicts_root),
            QCoreApplication.translate("DictionaryImportFlow", "Yomitan zip (*.zip)"),
        )
        _log_import_picker_return(trace_id, "dictionary zip", picker_started, zip_path_str)
        if not zip_path_str:
            return

        worker = ImportWorker.for_yomitan(Path(zip_path_str), self._get_config().dicts_root)

        def on_success(dict_id: str, meta: dict) -> None:
            new_chain = self._with_dict_at_top(dict_id)
            # New dict folder on disk — invalidate the panel's cached registry
            # scan so the row picks up the entry_count + source_name.
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            _log_import_persist(trace_id, "start")
            self._persist_chain(new_chain)
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary added"),
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Imported %1 (%2 entries)"),
                    dict_id,
                    f"{meta.get('entry_count', 0):,}",
                )
                + self._import_notes(meta),
            )

        def on_success_error(exc: Exception) -> None:
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Configuration Update Failed"),
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow",
                        "Import completed, but the configuration update failed: %1",
                    ),
                    str(exc),
                ),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("DictionaryImportFlow", "Importing dictionary…"),
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_title=QCoreApplication.translate("DictionaryImportFlow", "Import Failed"),
            refusal_message=QCoreApplication.translate(
                "DictionaryImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
            on_success_error=on_success_error,
        )

    def _catalog_slot_base_matches(self, slot_id: str, zip_path: Path) -> bool:
        """True when ``zip_path`` is a newer, same-dictionary copy of catalog slot.

        Compares the picked zip's title base against the existing slot's stored
        ``source_name`` base (both stripped of a trailing ``[YYYY-MM-DD]`` tag,
        both required to have carried one). This lets a fresh Jitendex whose id
        derives to a new dated id (JMdict → ``jmdict-<newdate>-...``, legacy
        Jitendex → ``jitendex-org-<newdate>``) re-import into the pinned
        ``jmdict-english``/``jitendex`` slot while still rejecting an
        unrelated dictionary. Any read
        failure (bad zip, missing/corrupt slot index) → False (reject, safe).
        """
        try:
            zip_title = read_yomitan_title(zip_path)
        except Exception:  # noqa: BLE001 — surfaced to the user as a slot mismatch
            return False
        db = self._get_config().dicts_root / slot_id / "index.sqlite"
        if not db.exists():
            return False
        try:
            existing_name = read_meta(db).get("source_name", "")
        except Exception:  # noqa: BLE001 — corrupt/locked slot index → reject
            return False
        zip_base, zip_had = strip_date_bracket(zip_title)
        cur_base, cur_had = strip_date_bracket(existing_name)
        return zip_had and cur_had and zip_base == cur_base

    def reimport_dict(
        self,
        slot_id: str,
        *,
        _scan_result: tuple[Path, str, bool] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Prompt for a matching Yomitan zip and re-import into an existing slot.

        Slot identity is preserved by validating that the chosen zip's derived
        `dict_id` equals ``slot_id`` before invoking the importer with
        ``overwrite=True``. Picking a different zip would orphan the stale slot
        and silently create a new one — we abort with a warning instead.
        """
        trace_id = _trace_id or _begin_import_trace("dictionary reimport")
        if _scan_result is None:
            picker_started = _log_import_picker_enter(trace_id, "dictionary zip")
            zip_path_str, _ = file_dialogs.get_open_file_name(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Choose Yomitan dictionary zip"),
                resolve_start_dir(None, file_mode=True, default_dir=self._get_config().dicts_root),
                QCoreApplication.translate("DictionaryImportFlow", "Yomitan zip (*.zip)"),
            )
            _log_import_picker_return(trace_id, "dictionary zip", picker_started, zip_path_str)
            if not zip_path_str:
                return

            zip_path = Path(zip_path_str)
            self._set_import_buttons_enabled(False)

            def _scan() -> tuple[Path, str, bool]:
                derived_id = derive_dict_id_from_zip(zip_path)
                base_matches = (
                    derived_id != slot_id
                    and slot_id in _PINNED_DICT_SLOT_IDS
                    and self._catalog_slot_base_matches(slot_id, zip_path)
                )
                return zip_path, derived_id, base_matches

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_dict(slot_id, _scan_result=result, _trace_id=trace_id)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                QMessageBox.warning(
                    self._parent,
                    QCoreApplication.translate("DictionaryImportFlow", "Invalid Zip"),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        zip_path, derived_id, base_matches = _scan_result
        self._set_import_buttons_enabled(True)

        # A catalog (or former-catalog) slot is pinned (its on-disk id is a
        # stable id like "jmdict-english" or the legacy "jitendex",
        # not the title-derived one), so a fresh copy of the SAME dictionary
        # legitimately derives a different id (the title embeds a new date). Accept
        # it when its title base matches the existing slot's, but still reject a
        # genuinely different dictionary — otherwise picking the wrong zip would
        # silently overwrite the slot with unrelated content.
        if derived_id != slot_id and not base_matches:
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Zip does not match slot"),
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow",
                        "This zip is for '%1', but you are re-importing '%2'. Pick the matching zip.",
                    ),
                    derived_id,
                    slot_id,
                ),
            )
            return

        # Drop sqlite handles before the importer renames the dict folder.
        # On Windows the rename fails with "Access denied" while any
        # DefinitionService still holds its read-only connection open
        # (Issue #32). The remove flow uses the same hook (Issue #30).
        if not self._panel.request_resource_release():
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Re-import Blocked"),
                QCoreApplication.translate(
                    "DictionaryImportFlow", "A mining run is in progress. Stop it before re-importing dictionaries."
                ),
            )
            return

        # Pin the existing slot so a same-dictionary zip with a newer date
        # rebuilds in place (dict_id=slot_id is a no-op for non-catalog slots,
        # where derived_id already equals slot_id).
        worker = ImportWorker.for_yomitan(zip_path, self._get_config().dicts_root, overwrite=True, dict_id=slot_id)

        def on_success(dict_id: str, meta: dict) -> None:
            # Refresh registry so the stale-flag warning clears on the row.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary re-imported"),
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Re-imported %1 (%2 entries)"),
                    dict_id,
                    f"{meta.get('entry_count', 0):,}",
                )
                + self._import_notes(meta),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("DictionaryImportFlow", "Re-importing dictionary…"),
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_title=QCoreApplication.translate("DictionaryImportFlow", "Re-import Failed"),
            refusal_message=QCoreApplication.translate(
                "DictionaryImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    def reimport_jmdict(self) -> None:
        """Reimport JMdict from the configured XML path."""
        trace_id = _begin_import_trace("JMdict reimport")
        xml = self._get_config().jmdict_path
        if not xml.exists():
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "JMdict not found"),
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow", "No JMdict XML at %1. Download from EDRDG and place it there."
                    ),
                    xml,
                ),
            )
            return

        # Drop sqlite handles before the importer renames the dict folder
        # (Issue #32 — same root cause as #30). Without this, the rename
        # at yomitan_importer.py:215 fails with "Access denied" on Windows.
        if not self._panel.request_resource_release():
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Re-import Blocked"),
                QCoreApplication.translate(
                    "DictionaryImportFlow", "A mining run is in progress. Stop it before re-importing dictionaries."
                ),
            )
            return

        worker = ImportWorker.for_jmdict(xml, self._get_config().dicts_root)

        def on_success(_dict_id: str, _meta: dict) -> None:
            # Re-render chain so the (refreshed) entry count is reflected.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("DictionaryImportFlow", "Reimporting JMdict…"),
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_title=QCoreApplication.translate("DictionaryImportFlow", "Reimport Failed"),
            refusal_message=QCoreApplication.translate(
                "DictionaryImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    def reimport_all(
        self,
        *,
        only_ids: frozenset[str] | None = None,
        _scan_result: tuple[list[tuple[str, str, str, Path]], list[str]] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Reimport dictionaries in the chain from their saved sources.

        For each indexed ChainEntry, dispatch based on format:
        - jmdict format → reimport from ``config.jmdict_path`` (the XML stays
          on disk between sessions, no copy needed).
        - yomitan format → reimport from ``<dicts_root>/<dict_id>/source.zip``
          if present.

        ``only_ids`` scopes upgrade repair to dictionary IDs found stale by the
        startup scan. ``None`` preserves the manual Reimport All behavior,
        including disabled chain entries. Missing-source reporting follows the
        same scope.

        Dicts with no saved source are skipped and surfaced in the final
        summary dialog. The user can seed them via the per-row stale-reimport
        button (which prompts for the zip and now persists it on success).

        Runs sequentially: each worker's native finish chains the next dispatch
        so a single ApplicationModal QProgressDialog tracks the whole batch.
        Per-dict failures accumulate into ``errors`` and don't abort the
        loop. ``config_changed`` is emitted once at the end so cached
        DefinitionService instances rebuild a single time.
        """
        trace_id = _trace_id or _begin_import_trace("dictionary reimport all")
        if _scan_result is None:
            config = self._get_config()
            chain = self._panel.get_chain()
            self._set_import_buttons_enabled(False)

            def _scan() -> tuple[list[tuple[str, str, str, Path]], list[str]]:
                registry = DictionaryRegistry(config.dicts_root)
                registry.load()
                jobs: list[tuple[str, str, str, Path]] = []
                missing_legacy: list[str] = []
                for entry in chain:
                    if entry.kind != "indexed" or entry.dict_id is None:
                        continue
                    if only_ids is not None and entry.dict_id not in only_ids:
                        continue
                    meta = registry.get(entry.dict_id)
                    if meta is None:
                        missing_legacy.append(entry.dict_id)
                        continue
                    if meta.format == "jmdict":
                        if config.jmdict_path.exists():
                            jobs.append(("jmdict", meta.dict_id, meta.source_name, config.jmdict_path))
                        else:
                            missing_legacy.append(meta.source_name)
                        continue
                    source_zip = config.dicts_root / meta.dict_id / "source.zip"
                    if source_zip.exists():
                        jobs.append(("yomitan", meta.dict_id, meta.source_name, source_zip))
                    else:
                        missing_legacy.append(meta.source_name)
                return jobs, missing_legacy

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_all(only_ids=only_ids, _scan_result=result, _trace_id=trace_id)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                QMessageBox.warning(
                    self._parent,
                    QCoreApplication.translate("DictionaryImportFlow", "Scan Failed"),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        jobs, missing_legacy = _scan_result
        self._set_import_buttons_enabled(True)

        if not jobs:
            if missing_legacy:
                body = QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "No dictionaries with saved sources were found.\n\n"
                    "Skipped (no saved source — right-click a dictionary row → Re-import… to seed):\n",
                ) + "\n".join(f"  • {n}" for n in missing_legacy)
            else:
                body = QCoreApplication.translate("DictionaryImportFlow", "No dictionaries in the chain.")
            QMessageBox.information(
                self._parent, QCoreApplication.translate("DictionaryImportFlow", "Nothing to reimport"), body
            )
            return

        # Drop sqlite handles before any worker touches the dict folders.
        # On Windows the importer's directory rename fails with "Access
        # denied" while a DefinitionService still holds its read-only
        # connection open (Issue #32; same hook as the remove flow in #30).
        if not self._panel.request_resource_release():
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Re-import Blocked"),
                QCoreApplication.translate(
                    "DictionaryImportFlow", "A mining run is in progress. Stop it before re-importing dictionaries."
                ),
            )
            return

        def make_worker(job: tuple[str, str, str, Path]) -> ImportWorker:
            kind, dict_id, _display, source_path = job
            if kind == "jmdict":
                return ImportWorker.for_jmdict(source_path, self._get_config().dicts_root)
            # Pin the existing slot id so a saved source whose title embeds a
            # changing release date (e.g. Jitendex) rebuilds the index in the
            # SAME folder instead of forking a new date-named dir — which would
            # orphan the chained slot and permanently wedge the stale-schema
            # pre-run gate (it could never clear the old slot).
            return ImportWorker.for_yomitan(source_path, self._get_config().dicts_root, overwrite=True, dict_id=dict_id)

        def format_label(
            index: int,
            total: int,
            job: tuple[str, str, str, Path],
            message: str | None,
        ) -> str:
            _kind, _dict_id, display, _source_path = job
            label = tr_format(
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary %1 of %2: %3"),
                index,
                total,
                display,
            )
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[tuple[str, str, str, Path]]) -> None:
            # One refresh + one config_changed for the whole batch so
            # DefinitionService rebuilds once, not N times.
            _log_import_persist(trace_id, "start")
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

            reimported = [job[2] for job, _dict_id, _meta in result.successes]
            errors = [(job[2], message) for job, message in result.failures]

            lines: list[str] = []
            if reimported:
                lines.append(
                    tr_format(
                        QCoreApplication.translate("DictionaryImportFlow", "Reimported %1 dictionary/dictionaries:"),
                        len(reimported),
                    )
                )
                lines.extend(f"  • {n}" for n in reimported)
            if missing_legacy:
                if lines:
                    lines.append("")
                lines.append(
                    QCoreApplication.translate(
                        "DictionaryImportFlow",
                        "Skipped (no saved source — right-click a dictionary row → Re-import… to seed):",
                    )
                )
                lines.extend(f"  • {n}" for n in missing_legacy)
            if errors:
                if lines:
                    lines.append("")
                lines.append(QCoreApplication.translate("DictionaryImportFlow", "Failed:"))
                lines.extend(f"  • {name}: {msg}" for name, msg in errors)
            if result.cancelled:
                if lines:
                    lines.append("")
                lines.append(
                    QCoreApplication.translate("DictionaryImportFlow", "Cancelled before remaining dictionaries.")
                )

            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Reimport All"),
                "\n".join(lines) or QCoreApplication.translate("DictionaryImportFlow", "Done."),
            )

        def on_finished_error(
            exc: Exception,
            _result: _ChainedImportResult[tuple[str, str, str, Path]],
        ) -> None:
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Configuration Update Failed"),
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow",
                        "Import completed, but the configuration update failed: %1",
                    ),
                    str(exc),
                ),
            )

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_title=QCoreApplication.translate("DictionaryImportFlow", "Reimport Failed"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    def restore_unlisted(self, *, _scan_result: list[DictMeta] | None = None) -> None:
        """Re-add on-disk dictionaries that are absent from the chain config.

        Recovers dicts present in ``dicts_root`` (with a valid, current-schema
        index.sqlite) but missing from ``config.dictionary_chain`` — for example
        after a config reset that overwrote ``gui_config.json``.  No re-import
        is performed; the indexes already exist on disk and only the chain config
        needs updating.
        """
        if _scan_result is None:
            config = self._get_config()
            panel_config = replace(config, dictionary_chain=self._panel.get_chain())
            self._set_import_buttons_enabled(False)

            def _scan() -> list[DictMeta]:
                registry = DictionaryRegistry(config.dicts_root)
                registry.load()
                return registry.unlisted(panel_config)

            def _on_done(result: object) -> None:
                assert isinstance(result, list)
                self.restore_unlisted(_scan_result=result)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                QMessageBox.warning(
                    self._parent,
                    QCoreApplication.translate("DictionaryImportFlow", "Scan Failed"),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        orphans = _scan_result
        self._set_import_buttons_enabled(True)

        if not orphans:
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Nothing to restore"),
                QCoreApplication.translate("DictionaryImportFlow", "All on-disk dictionaries are already listed."),
            )
            return

        body = (
            QCoreApplication.translate(
                "DictionaryImportFlow", "Found dictionaries on disk that aren't in your list:\n\n"
            )
            + "\n".join(f"  • {m.source_name}" for m in orphans)
            + "\n\n"
            + QCoreApplication.translate("DictionaryImportFlow", "Add them to the dictionary list?")
        )
        reply = QMessageBox.question(
            self._parent,
            QCoreApplication.translate("DictionaryImportFlow", "Restore from Disk"),
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        chain = list(self._panel.get_chain())
        new_entries = [ChainEntry(kind="indexed", dict_id=m.dict_id, enabled=True) for m in orphans]
        # Insert before the first jisho entry so the online fallback stays last.
        # The UI only ever creates one jisho row; "first jisho wins" is fine.
        insert_at = next((i for i, e in enumerate(chain) if e.kind == "jisho"), len(chain))
        new_chain = tuple(chain[:insert_at] + new_entries + chain[insert_at:])

        self._panel.refresh_registry()
        self._panel.set_chain(new_chain)
        self._persist_chain(new_chain)
