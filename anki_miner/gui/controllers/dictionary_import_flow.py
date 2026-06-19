"""Dictionary import orchestration (add / per-row reimport / JMdict / Reimport All).

Extracted from ``SettingsTab`` (T-66). Owns the ``DictionaryImportWorker``
lifecycles and every dialog in the import flows — including the Reimport-All
chained state machine and its predecessor-join (T-09). The tab keeps the
panel widgets, the signal wiring, and the narrow chain persist
(``_persist_chain_change``), injected here as callables so the dependency
stays one-way: tab → controller → workers/services.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QWidget

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels import DictionarySettingsPanel
from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker
from anki_miner.services.dictionary.importers.yomitan_importer import derive_dict_id_from_zip
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.utils.i18n import tr_format


class DictionaryImportFlow:
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
        # Long-lived worker reference; DictionaryImportWorker is a QThread and
        # would be destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: DictionaryImportWorker | None = None

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        Returns the active import worker so ``SettingsTab.iter_close_workers``
        can chain it into the single ``BackgroundTaskController._join_worker_for_close``
        policy (cancel + bounded grace join + laggard deferral).  A ``None``
        entry (idle flow) is filtered by ``_join_worker_for_close``.
        """
        return (self._active_import_worker,)

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
        zip_path_str, _ = QFileDialog.getOpenFileName(
            self._parent,
            QCoreApplication.translate("DictionaryImportFlow", "Choose Yomitan dictionary zip"),
            resolve_start_dir(None, file_mode=True, default_dir=self._get_config().dicts_root),
            QCoreApplication.translate("DictionaryImportFlow", "Yomitan zip (*.zip)"),
        )
        if not zip_path_str:
            return

        dest_root = self._get_config().dicts_root
        dlg = QProgressDialog(
            QCoreApplication.translate("DictionaryImportFlow", "Importing dictionary…"),
            QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            0,
            100,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_yomitan(Path(zip_path_str), dest_root)
        self._active_import_worker = worker  # keep alive across QThread lifetime
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_import_finished(dict_id: str, meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary added"),
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Imported %1 (%2 entries)"),
                    dict_id,
                    f"{meta.get('entry_count', 0):,}",
                ),
            )
            new_chain = self._with_dict_at_top(dict_id)
            # New dict folder on disk — invalidate the panel's cached registry
            # scan so the row picks up the entry_count + source_name.
            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            self._persist_chain(new_chain)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self._parent, QCoreApplication.translate("DictionaryImportFlow", "Import Failed"), err)
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_import_finished)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def reimport_dict(self, slot_id: str) -> None:
        """Prompt for a matching Yomitan zip and re-import into an existing slot.

        Slot identity is preserved by validating that the chosen zip's derived
        `dict_id` equals ``slot_id`` before invoking the importer with
        ``overwrite=True``. Picking a different zip would orphan the stale slot
        and silently create a new one — we abort with a warning instead.
        """
        zip_path_str, _ = QFileDialog.getOpenFileName(
            self._parent,
            QCoreApplication.translate("DictionaryImportFlow", "Choose Yomitan dictionary zip"),
            resolve_start_dir(None, file_mode=True, default_dir=self._get_config().dicts_root),
            QCoreApplication.translate("DictionaryImportFlow", "Yomitan zip (*.zip)"),
        )
        if not zip_path_str:
            return

        zip_path = Path(zip_path_str)
        try:
            derived_id = derive_dict_id_from_zip(zip_path)
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            QMessageBox.warning(
                self._parent, QCoreApplication.translate("DictionaryImportFlow", "Invalid Zip"), str(exc)
            )
            return

        if derived_id != slot_id:
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

        dest_root = self._get_config().dicts_root
        dlg = QProgressDialog(
            QCoreApplication.translate("DictionaryImportFlow", "Re-importing dictionary…"),
            QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            0,
            100,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_yomitan(zip_path, dest_root, overwrite=True)
        self._active_import_worker = worker  # keep alive across QThread lifetime
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(dict_id: str, meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary re-imported"),
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Re-imported %1 (%2 entries)"),
                    dict_id,
                    f"{meta.get('entry_count', 0):,}",
                ),
            )
            # Refresh registry so the stale-flag warning clears on the row.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            self._notify_config_changed()
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(
                self._parent, QCoreApplication.translate("DictionaryImportFlow", "Re-import Failed"), err
            )
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def reimport_jmdict(self) -> None:
        """Reimport JMdict from the configured XML path."""
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

        dest_root = self._get_config().dicts_root
        dlg = QProgressDialog(
            QCoreApplication.translate("DictionaryImportFlow", "Reimporting JMdict…"),
            QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            0,
            100,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_jmdict(xml, dest_root)
        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(dict_id: str, meta: dict) -> None:
            dlg.close()
            # Re-render chain so the (refreshed) entry count is reflected.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            self._notify_config_changed()
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(
                self._parent, QCoreApplication.translate("DictionaryImportFlow", "Reimport Failed"), err
            )
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def reimport_all(self) -> None:
        """Reimport every dictionary in the chain from its saved source.

        For each indexed ChainEntry, dispatch based on format:
        - jmdict format → reimport from ``config.jmdict_path`` (the XML stays
          on disk between sessions, no copy needed).
        - yomitan format → reimport from ``<dicts_root>/<dict_id>/source.zip``
          if present.

        Dicts with no saved source are skipped and surfaced in the final
        summary dialog. The user can seed them via the per-row stale-reimport
        button (which prompts for the zip and now persists it on success).

        Runs sequentially: each worker's ``on_done`` chains the next dispatch
        so a single ApplicationModal QProgressDialog tracks the whole batch.
        Per-dict failures accumulate into ``errors`` and don't abort the
        loop. ``config_changed`` is emitted once at the end so cached
        DefinitionService instances rebuild a single time.
        """
        # Fresh registry scan so we see source_name / format for the summary.
        registry = DictionaryRegistry(self._get_config().dicts_root)
        registry.load()

        # Job tuples: ("yomitan", dict_id, display_name, source_zip_path)
        #             ("jmdict",  dict_id, display_name, xml_path)
        jobs: list[tuple[str, str, str, Path]] = []
        missing_legacy: list[str] = []

        for entry in self._panel.get_chain():
            if entry.kind != "indexed" or entry.dict_id is None:
                continue
            meta = registry.get(entry.dict_id)
            if meta is None:
                missing_legacy.append(entry.dict_id)
                continue
            if meta.format == "jmdict":
                if self._get_config().jmdict_path.exists():
                    jobs.append(("jmdict", meta.dict_id, meta.source_name, self._get_config().jmdict_path))
                else:
                    missing_legacy.append(meta.source_name)
                continue
            # Yomitan and anything else with a saved zip
            source_zip = self._get_config().dicts_root / meta.dict_id / "source.zip"
            if source_zip.exists():
                jobs.append(("yomitan", meta.dict_id, meta.source_name, source_zip))
            else:
                missing_legacy.append(meta.source_name)

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

        dlg = QProgressDialog(
            QCoreApplication.translate("DictionaryImportFlow", "Reimporting dictionaries…"),
            QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            0,
            100,
            self._parent,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        self._set_import_buttons_enabled(False)

        # Mutable state shared across the chained closures.
        state: dict[str, object] = {
            "index": 0,
            "cancelled": False,
            "reimported": [],
            "errors": [],
        }

        def finish() -> None:
            dlg.close()
            # One refresh + one config_changed for the whole batch so
            # DefinitionService rebuilds once, not N times.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._notify_config_changed()
            self._set_import_buttons_enabled(True)

            reimported = state["reimported"]
            errors = state["errors"]
            assert isinstance(reimported, list)
            assert isinstance(errors, list)

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
            if state["cancelled"]:
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

        def launch_next() -> None:
            idx = state["index"]
            assert isinstance(idx, int)
            if state["cancelled"] or idx >= len(jobs):
                finish()
                return

            kind, dict_id, display, source_path = jobs[idx]
            dlg.setLabelText(
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Dictionary %1 of %2: %3"),
                    idx + 1,
                    len(jobs),
                    display,
                )
            )
            dlg.setMaximum(100)
            dlg.setValue(0)

            if kind == "jmdict":
                worker = DictionaryImportWorker.for_jmdict(source_path, self._get_config().dicts_root)
            else:
                worker = DictionaryImportWorker.for_yomitan(source_path, self._get_config().dicts_root, overwrite=True)
            # Join the predecessor before dropping its reference (T-09). This
            # closure runs inside the previous worker's queued finished slot,
            # emitted from run() just before the OS thread exits — so its
            # QThread may still be technically running. Reassigning
            # _active_import_worker without waiting drops the only reference to a
            # live, unparented QThread → "QThread: Destroyed while thread is
            # still running". wait() is at most microseconds from returning here.
            prev = self._active_import_worker
            if prev is not None and prev.isRunning():
                prev.wait()
            self._active_import_worker = worker

            def on_progress(cur: int, total: int, msg: str) -> None:
                dlg.setMaximum(total)
                dlg.setValue(cur)

            def on_done(_dict_id: str, _meta: dict) -> None:
                reimported = state["reimported"]
                assert isinstance(reimported, list)
                reimported.append(display)
                state["index"] = idx + 1
                launch_next()

            def on_failed(err: str) -> None:
                errors = state["errors"]
                assert isinstance(errors, list)
                errors.append((display, err))
                state["index"] = idx + 1
                launch_next()

            worker.progress.connect(on_progress)
            worker.import_finished.connect(on_done)
            worker.failed.connect(on_failed)
            worker.start()

        def on_cancel() -> None:
            state["cancelled"] = True
            worker = self._active_import_worker
            if worker is not None and worker.isRunning():
                worker.cancel()

        dlg.canceled.connect(on_cancel)
        launch_next()

    def restore_unlisted(self) -> None:
        """Re-add on-disk dictionaries that are absent from the chain config.

        Recovers dicts present in ``dicts_root`` (with a valid, current-schema
        index.sqlite) but missing from ``config.dictionary_chain`` — for example
        after a config reset that overwrote ``gui_config.json``.  No re-import
        is performed; the indexes already exist on disk and only the chain config
        needs updating.
        """
        registry = DictionaryRegistry(self._get_config().dicts_root)
        registry.load()
        # Compare against the panel's current chain (not the frozen config) so
        # that unsaved panel edits are respected — a dict the user just added via
        # set_chain is already "listed" even before Save is clicked.
        panel_config = replace(self._get_config(), dictionary_chain=self._panel.get_chain())
        orphans = registry.unlisted(panel_config)

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
