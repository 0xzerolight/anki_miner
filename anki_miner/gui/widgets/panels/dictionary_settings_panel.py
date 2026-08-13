"""Dictionary settings panel."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QWidget,
)

from anki_miner.config import ChainEntry
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.gui.widgets.panels.chain_priority_list import ChainRowSpec, ChainSourceRow
from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
    ChainListLabels,
    ChainSettingsPanelBase,
    _ChainPanelStrings,
)
from anki_miner.services._sqlite_index import prove_owned_slot, resolve_managed_slot
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.robust_fs import RmtreeOutcome, robust_rmtree


def _robust_rmtree(target: Path) -> RmtreeOutcome:
    """Panel-local seam for post-commit cleanup."""
    return robust_rmtree(target, mode="outcome")


class DictionarySettingsPanel(ChainSettingsPanelBase):
    """Reorderable chain of dictionary providers."""

    add_dict_requested = pyqtSignal()
    reimport_jmdict_requested = pyqtSignal()
    reimport_dict_requested = pyqtSignal(str)
    reimport_all_requested = pyqtSignal()
    rescan_requested = pyqtSignal()

    ANCHOR_NAMESPACE = "dictionaries"

    _SCAN_ERROR_LABEL = "Dictionary registry scan failed"
    _REMOVE_ERROR_NOUN = "dictionary folder"

    def __init__(self, dicts_root: Path, parent=None):
        super().__init__("Dictionary Settings", parent=parent)
        self._dicts_root = dicts_root
        # Optional callback invoked before destructive remove to ask the rest of
        # the app to close cached sqlite handles (Issue #30, Win11 lock).
        # Returns True on success, False if a mining run is in flight.
        self._release_callback: Callable[[], bool] | None = None
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            retry_label=self.tr("Retry"),
            scan_failed_summary=self.tr("Installed dictionaries could not be checked."),
            files_left_summary=self.tr(
                "The dictionary was removed from the chain, but its files were left in place "
                "because the folder could not be proven to belong to Anki Miner."
            ),
            intact_failure_summary=self.tr("%1 could not be removed. Its files are intact — try again."),
            partial_failure_summary=self.tr(
                "%1 was only partly removed. Re-import or repair this dictionary before retrying."
            ),
            config_pending_failure_summary=self.tr(
                "%1 could not be restored after its settings update failed. " "Restart Anki Miner before retrying."
            ),
            post_save_summary=self.tr(
                "%1 was removed, but Anki Miner could not refresh it. "
                "The removal is saved and will remain after a restart."
            ),
            cleanup_pending_summary=self.tr(
                "%1 was removed, but its leftover folder could not be deleted. " "Cleanup will be retried at startup."
            ),
        )
        self._setup_fields()

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the pre-remove resource-release hook.

        See ``_acquire_release_for_remove``. Injected by app.py at startup so the
        panel can call ``MainWindow.release_dictionary_resources`` without
        importing it.
        """
        self._release_callback = cb

    def request_resource_release(self) -> bool:
        """Public proxy so siblings (e.g. settings_tab's re-import handler)
        can ask the rest of the app to close cached sqlite handles without
        reaching into ``_release_callback`` directly (Issue #32).

        Returns ``True`` when no callback is wired or the callback succeeded;
        ``False`` when the callback refused because indexed resources are in
        use (see ``MainWindow.release_dictionary_resources``).
        """
        if self._release_callback is None:
            return True
        return self._release_callback()

    def set_dicts_root(self, dicts_root: Path) -> None:
        """Update the dicts root (e.g. after a config save) and invalidate caches."""
        self._dicts_root = dicts_root
        self._view = None
        # Keep the storage-folder selector in sync when config is reloaded
        # externally (e.g. after Reset to Defaults or a programmatic
        # update_config call). Guarded because _setup_fields runs after
        # __init__'s call chain may invoke this method indirectly.
        if hasattr(self, "dicts_root_selector"):
            self.dicts_root_selector.set_path(str(dicts_root))
        # Root changed → cached scan is stale; rescan off-thread (no-op before
        # first show, where _scanned is still False).
        self._scan_and_render_async()

    def get_dicts_root(self) -> Path:
        """Return the path currently displayed in the storage-folder selector.

        Falls back to the panel's last-known ``_dicts_root`` when the selector
        is empty so the save flow never accidentally collapses the field to
        ``Path("")``.
        """
        raw = self.dicts_root_selector.get_path()
        return Path(raw) if raw else self._dicts_root

    def _on_reset_dicts_root(self) -> None:
        """Reset the storage selector to the default ``ANKI_MINER_HOME / dicts``.

        Only repopulates the visible field — the change isn't persisted until
        the user clicks Save in the Settings tab.
        """
        self.dicts_root_selector.set_path(str(ANKI_MINER_HOME / "dicts"))

    def set_per_row_reimport_enabled(self, enabled: bool) -> None:
        """Toggle every stale-row Re-import button.

        Prevents a user from launching a second per-row import while
        another is in flight — clobbering ``_active_import_worker`` would
        orphan the first worker.
        """
        self._set_row_repair_enabled(enabled)

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        self.dicts_root_selector.setEnabled(enabled)
        self._reset_dicts_root_btn.setEnabled(enabled)
        self._add_btn.setEnabled(enabled)
        self._reimport_btn.setEnabled(enabled)
        self._restore_btn.setEnabled(enabled)

    def _setup_fields(self) -> None:
        # Storage folder picker — first so it sits above the dictionary chain.
        # Issue #45: lets users move ``dicts/`` off the home partition (e.g. to
        # an external SSD) without manually symlinking ``~/.anki_miner/dicts``.
        storage_container = QWidget()
        storage_layout = QHBoxLayout(storage_container)
        storage_layout.setContentsMargins(0, 0, 0, 0)

        self.dicts_root_selector = FileSelector(
            label="",
            file_mode=False,
            placeholder=self.tr("Select dictionary storage folder..."),
            default_dir=ANKI_MINER_HOME / "dicts",
        )
        self.dicts_root_selector.set_path(str(self._dicts_root))
        storage_layout.addWidget(self.dicts_root_selector, 1)

        self._reset_dicts_root_btn = ModernButton(self.tr("Reset to default"), variant="secondary")
        self._reset_dicts_root_btn.clicked.connect(self._on_reset_dicts_root)
        # FileSelector is two rows tall (input+Browse, then status caption); top-
        # align so Reset lines up with the Browse button in the top row, not the
        # HBox's default vertical center.
        storage_layout.addWidget(self._reset_dicts_root_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self.add_field(
            self.tr("Storage Folder"),
            storage_container,
            helper=self.tr(
                "Where indexed dictionaries are stored. Existing dictionaries at "
                "the old location are not moved automatically."
            ),
            anchor="storage_folder",
            anchor_focus=self.dicts_root_selector,
        )

        self.add_section(self.tr("Active Dictionaries"))

        self._reimport_btn = ModernButton(self.tr("Reimport All"), variant="secondary")
        self._reimport_btn.clicked.connect(self.reimport_all_requested.emit)

        self._restore_btn = ModernButton(self.tr("Restore from Disk"), variant="secondary")
        self._restore_btn.setToolTip(
            self.tr(
                "Re-add dictionaries found in the storage folder that aren't in the "
                "list above (e.g. after a settings reset). No re-import needed."
            )
        )
        self._restore_btn.clicked.connect(self.rescan_requested.emit)

        container = self._build_chain_container(
            ChainListLabels(
                explanation=self.tr(
                    "Tried top to bottom — the first dictionary with an entry for a word "
                    "wins and fills MainDefinition."
                ),
                add=self.tr("Add dictionary…"),
                remove=self.tr("Remove dictionary"),
                remove_tooltip=self.tr("Remove the selected dictionary and delete its files"),
                move_up=self.tr("Move up"),
                move_up_tooltip=self.tr("Move up in priority"),
                move_down=self.tr("Move down"),
                move_down_tooltip=self.tr("Move down in priority"),
            ),
            extra_actions=(self._reimport_btn, self._restore_btn),
        )
        self._add_btn.clicked.connect(self.add_dict_requested.emit)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)

        # One stable anchor for the whole chain. Row widgets are rebuilt on every
        # scan and reorder, so search must never bind to them (D13).
        self.add_field(
            "",
            container,
            anchor="chain",
            anchor_focus=self._list,
            anchor_text=lambda: (
                self._explanation_label.text(),
                self._add_btn.text(),
                self._restore_btn.text(),
            ),
        )

        # Pitch accent sources now live in their own Settings → Pitch Accent
        # tab (multi-source first-hit-wins chain), like the frequency sources
        # below. The old single-file picker that used to sit here was removed.

        # Frequency sources now live in their own Settings → Frequency tab
        # (multi-source additive chain). The old single-file picker that used
        # to sit here was removed.

        self.add_stretch()

    def set_chain(self, chain: tuple[ChainEntry, ...]) -> None:
        self._chain = list(chain)
        self._rebuild_list()

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _entry_with_enabled(self, entry: ChainEntry, enabled: bool) -> ChainEntry:
        return ChainEntry(kind=entry.kind, dict_id=entry.dict_id, enabled=enabled)

    def _build_view(self) -> DictionaryRegistry:
        registry = DictionaryRegistry(self._dicts_root)
        registry.load()
        return registry

    def _row_spec(self, entry: ChainEntry, view: DictionaryRegistry | None) -> ChainRowSpec:
        meta: DictMeta | None = None
        warning = ""
        metadata: tuple[str, ...]
        if entry.kind == "indexed":
            meta = view.get(entry.dict_id) if (view is not None and entry.dict_id) else None
            display = meta.source_name if meta else (entry.dict_id or "(missing)")
            if meta is not None:
                # Zero entries is a fact about an installed dictionary; unknown
                # metadata is the absence of one, so it stays off the row.
                metadata = (meta.format, tr_format(self.tr("%1 entries"), f"{meta.entry_count:,}"))
            else:
                metadata = (self.tr("not installed"),)
                warning = self.tr("⚠ missing — re-import")
        else:
            display = self.tr("Jisho (online fallback)")
            metadata = (self.tr("online"),)
            warning = self.tr("⚠ rate-limited, slower")
        stale = meta is not None and not meta.schema_ok
        if stale:
            warning = self.tr("⚠ re-import to refresh")
        return ChainRowSpec(
            entry=entry,
            title=display,
            metadata=metadata,
            enabled_text=self.tr("Enabled"),
            enabled_accessible_text=tr_format(self.tr("Enable %1"), display),
            enabled_tooltip=tr_format(self.tr("Enable or disable %1"), display),
            warning=warning,
            repair_text=self.tr("Re-import") if stale else "",
        )

    def _connect_row_repair(self, row: ChainSourceRow) -> None:
        if row.repair_button is None:
            return
        dict_id = row.entry.dict_id
        if not dict_id:
            return
        row.repair_button.clicked.connect(lambda _checked=False, d=dict_id: self.reimport_dict_requested.emit(d))

    def _is_protected_entry(self, entry: ChainEntry) -> bool:
        return entry.kind == "jisho"  # Jisho can be disabled but not removed

    def _entry_display_name(self, entry: ChainEntry) -> str:
        dict_id = entry.dict_id
        registry = self._view
        meta = registry.get(dict_id) if (registry is not None and dict_id) else None
        return meta.source_name if meta else (dict_id or "(missing)")

    def _entry_disk_dir(self, entry: ChainEntry) -> Path | None:
        if not entry.dict_id:
            return None
        try:
            return resolve_managed_slot(self._dicts_root, entry.dict_id)
        except ValueError:
            return None

    def _owns_entry_disk_dir(self, entry: ChainEntry, target: Path) -> bool:
        dict_id = entry.dict_id
        return dict_id is not None and prove_owned_slot(target.parent, dict_id, "dictionary")

    def _confirm_remove(self, display: str) -> bool:
        reply = QMessageBox.question(
            self,
            self.tr("Remove dictionary"),
            tr_format(
                self.tr(
                    "Remove '%1' and delete its files from disk?\n\nThis cannot be undone. You would need to reimport from the source zip."
                ),
                display,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _confirm_chain_only_remove(self, display: str) -> bool:
        reply = QMessageBox.question(
            self,
            self.tr("Remove dictionary"),
            tr_format(
                self.tr(
                    "Remove '%1' from the dictionary list?\n\nFiles on disk will be left untouched because the folder could not be proven to belong to Anki Miner."
                ),
                display,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _acquire_release_for_remove(self) -> bool:
        # Drop sqlite handles before rmtree. On Windows the index.sqlite file
        # stays locked while any DefinitionService still holds its read-only
        # connection, and the retry loop in _robust_rmtree can't unblock that —
        # only an explicit provider.close() can (Issue #30).
        if self._release_callback is not None and not self._release_callback():
            self.show_screen_issue(
                ScreenIssue(
                    summary=self.tr(
                        "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                        "Wait for the active task to finish and try again."
                    )
                )
            )
            return False
        return True

    def _rmtree_dir(self, target: Path) -> RmtreeOutcome:
        return _robust_rmtree(target)

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click a dictionary row to re-import or remove it.

        Reuses the stale-row re-import signals so the same handler
        (`DictionaryImportFlow.reimport_dict`) drives the import flow regardless
        of entry point. Jisho rows have no menu — the online fallback can't be
        re-imported. The controller selects a recoverable source from the slot
        id even when registry metadata is missing or corrupt.
        """
        # While an async scan is in flight the list shows a single disabled
        # "Loading…" placeholder, not real rows. Resolving a right-click through
        # self._chain then targets an arbitrary real dictionary the user never
        # clicked — and Remove would rmtree it. Bail, mirroring the frequency
        # panel's identical guard.
        if self._scan_in_flight or self.has_active_mutation():
            return
        item = self._list.itemAt(pos)
        if item is None:
            return
        index = self._list.row(item)
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if entry.kind == "jisho" or entry.dict_id is None:
            return
        menu = QMenu(self._list)
        reimport_action = menu.addAction(self.tr("Re-import…"))
        remove_action = menu.addAction(self.tr("Remove"))
        viewport = self._list.viewport()
        global_pos = viewport.mapToGlobal(pos) if viewport is not None else self._list.mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if chosen is reimport_action:
            self.reimport_dict_requested.emit(entry.dict_id)
        elif chosen is remove_action:
            self.remove(index)
