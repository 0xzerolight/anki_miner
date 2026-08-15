"""Frequency sources settings panel.

Reorderable chain of additive frequency sources, mirroring
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`.
Replaces the old single-file "Frequency List" picker: the user adds, reorders,
enables/disables, and removes multiple frequency rank lists, each backed by a
per-source ``index.sqlite`` under ``config.freqs_root/<source_id>/``.

Frequency activation is resource-driven: adding an enabled source here turns the
feature on (``config.frequency_active``). There is no separate on/off checkbox.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtWidgets import (
    QMenu,
    QMessageBox,
)

from anki_miner.config import FreqEntry
from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels.chain_priority_list import ChainRowSpec, ChainSourceRow
from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
    ChainListLabels,
    ChainSettingsPanelBase,
    _ChainPanelStrings,
    _RegistryView,
)
from anki_miner.services._sqlite_index import prove_owned_slot, resolve_managed_slot
from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.robust_fs import RmtreeOutcome, robust_rmtree


def _robust_rmtree(target: Path) -> RmtreeOutcome:
    """Panel-local seam for post-commit cleanup."""
    return robust_rmtree(target, mode="outcome")


# Human-readable format labels keyed by the importer's ``format`` value.
_FORMAT_LABELS: dict[str, str] = {
    "yomitan-freq": "yomitan-freq",
    "csv": "csv",
}


class FrequencySettingsPanel(ChainSettingsPanelBase):
    """Reorderable chain of additive frequency sources."""

    add_source_requested = pyqtSignal()
    reimport_source_requested = pyqtSignal(str)
    reimport_all_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    ANCHOR_NAMESPACE = "frequency"

    _SCAN_ERROR_LABEL = "Frequency registry scan failed"
    _REMOVE_ERROR_NOUN = "frequency source folder"

    def __init__(self, freqs_root: Path, parent=None):
        super().__init__("Frequency Sources", parent=parent)
        self._freqs_root = freqs_root
        # Optional callback invoked before destructive replacement/removal to
        # ask the rest of the app to close cached sqlite handles.
        self._release_callback: Callable[[], bool] | None = None
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            retry_label=self.tr("Retry"),
            scan_failed_summary=self.tr("Installed frequency sources could not be checked."),
            files_left_summary=self.tr(
                "The frequency source was removed from the chain, but its files were left in place "
                "because the folder could not be proven to belong to Anki Miner."
            ),
            intact_failure_summary=self.tr("%1 could not be removed. Its files are intact — try again."),
            partial_failure_summary=self.tr(
                "%1 was only partly removed. Re-import or repair this frequency source before retrying."
            ),
            config_pending_failure_summary=self.tr(
                "%1 could not be restored after its settings update failed. Restart Anki Miner before retrying."
            ),
            post_save_summary=self.tr(
                "%1 was removed, but Anki Miner could not refresh it. "
                "The removal is saved and will remain after a restart."
            ),
            cleanup_pending_summary=self.tr(
                "%1 was removed, but its leftover folder could not be deleted. Cleanup will be retried at startup."
            ),
        )
        self._setup_fields()

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the resource-release hook used by reimport and remove."""
        self._release_callback = cb

    def request_resource_release(self) -> bool:
        """Ask the app to close cached resource handles before replacement."""
        if self._release_callback is None:
            return True
        return self._release_callback()

    def set_freqs_root(self, freqs_root: Path) -> None:
        """Update the freqs root (e.g. after a config swap) and invalidate caches.

        Mirrors ``DictionarySettingsPanel.set_dicts_root``. Without it a config
        carrying a different ``freqs_root`` leaves this panel scanning the old
        root for the rest of the session, and the destructive remove flow
        resolves ``resolve_managed_slot`` against the wrong directory. This
        panel has no storage-folder selector, so there is nothing to re-sync.
        """
        if freqs_root == self._freqs_root:
            # _load_config runs after every auto-save commit that touches a
            # non-external field, and the root is the same almost every time.
            # Rescanning anyway would flash a "Loading…" placeholder and take a
            # hold_mutation("scan") token (disabling Add) on every settings edit.
            return
        self._freqs_root = freqs_root
        self._view = None
        # Root changed → cached scan is stale; rescan off-thread (no-op before
        # first show, where _scanned is still False).
        self._scan_and_render_async()

    def _setup_fields(self) -> None:
        self.add_section(self.tr("Active Frequency Sources"))
        self._restore_btn = ModernButton(self.tr("Restore from Disk"), variant="secondary")
        self._restore_btn.setToolTip(
            self.tr(
                "Re-add frequency sources found in the storage folder that aren't in the list above. "
                "No re-import needed."
            )
        )
        self._restore_btn.clicked.connect(self.restore_requested.emit)
        self._reimport_btn = ModernButton(self.tr("Reimport All"), variant="secondary")
        self._reimport_btn.setToolTip(
            self.tr(
                "Rebuild every frequency source in the list from the copy saved when it was imported. "
                "Needed after an app upgrade changes the index format."
            )
        )
        self._reimport_btn.clicked.connect(self.reimport_all_requested.emit)
        container = self._build_chain_container(
            ChainListLabels(
                # Not the first-match sentence the other three chains carry:
                # frequency layers every enabled source, while order controls
                # only the source list rendered on cards.
                explanation=self.tr(
                    "Every enabled source counts: filtering uses the lowest rank, Frequency "
                    "Sort the harmonic mean. Order only sets the card's source list."
                ),
                add=self.tr("Add frequency source…"),
                remove=self.tr("Remove frequency source"),
                remove_tooltip=self.tr("Remove the selected frequency source"),
                move_up=self.tr("Move up"),
                move_up_tooltip=self.tr("Move up in the card's source list"),
                move_down=self.tr("Move down"),
                move_down_tooltip=self.tr("Move down"),
            ),
            extra_actions=(self._reimport_btn, self._restore_btn),
        )
        self._add_btn.clicked.connect(self.add_source_requested.emit)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)
        # One stable anchor for the whole chain; row widgets are transient (D13).
        self.add_field(
            "",
            container,
            anchor="chain",
            anchor_focus=self._list,
            anchor_text=lambda: (
                self._explanation_label.text(),
                self._add_btn.text(),
                self._reimport_btn.text(),
                self._restore_btn.text(),
            ),
        )
        self.add_stretch()

    def set_chain(
        self,
        chain: tuple[FreqEntry, ...],
        registry_meta: dict[str, FreqSourceMeta] | None = None,
    ) -> None:
        self._chain = list(chain)
        if registry_meta is not None:
            # Caller pre-supplied meta; use it directly, no disk scan needed.
            self._view = _RegistryView(registry_meta.get)
        self._rebuild_list()

    def set_per_row_reimport_enabled(self, enabled: bool) -> None:
        """Toggle every stale-row Re-import button.

        Prevents a second per-row import starting while one is in flight —
        clobbering the flow's active worker would orphan the first.
        """
        self._set_row_repair_enabled(enabled)

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        self._add_btn.setEnabled(enabled)
        self._reimport_btn.setEnabled(enabled)
        self._restore_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _entry_with_enabled(self, entry: FreqEntry, enabled: bool) -> FreqEntry:
        return FreqEntry(source_id=entry.source_id, enabled=enabled)

    def _build_view(self) -> _RegistryView:
        registry = FrequencySourceRegistry(self._freqs_root)
        registry.load()
        return _RegistryView(registry.get)

    def _row_spec(self, entry: FreqEntry, view: _RegistryView | None) -> ChainRowSpec:
        meta = view.get(entry.source_id) if (view is not None and entry.source_id) else None
        # Two different failures with two different repairs, so two different
        # rows. Stale = present on disk but schema-mismatched, which an app
        # upgrade caused and Re-import fixes from the saved copy, so the row
        # gets a button. Missing = the folder is gone, leaving nothing to
        # rebuild from; the row says so and offers no button that would only
        # open a file picker.
        stale = meta is not None and not meta.schema_ok
        absent = view is not None and meta is None
        display = meta.source_name if meta else (entry.source_id or "(missing)")
        metadata: tuple[str, ...] = ()
        tooltip = ""
        if meta is not None:
            metadata = (_FORMAT_LABELS.get(meta.format, meta.format),)
            if meta.is_categorical:
                # Word-based sources hold level labels (N5/Basic) shown on the
                # card but excluded from the frequency-rank cutoff.
                metadata = (*metadata, self.tr("word-based"))
                tooltip = self.tr("Level labels are shown on the card but not used for frequency filtering.")
            metadata = (*metadata, tr_format(self.tr("%1 entries"), f"{meta.entry_count:,}"))
        return ChainRowSpec(
            entry=entry,
            title=display,
            metadata=metadata,
            metadata_tooltip=tooltip,
            enabled_text=self.tr("Enabled"),
            enabled_accessible_text=tr_format(self.tr("Enable %1"), display),
            enabled_tooltip=tr_format(self.tr("Enable or disable %1"), display),
            warning=self._row_warning(stale=stale, absent=absent),
            repair_text=self.tr("Re-import") if stale else "",
        )

    def _row_warning(self, *, stale: bool, absent: bool) -> str:
        if stale:
            return self.tr("⚠ re-import required (app upgrade)")
        if absent:
            return self.tr("⚠ missing — re-import")
        return ""

    def _connect_row_repair(self, row: ChainSourceRow) -> None:
        if row.repair_button is None:
            return
        source_id = row.entry.source_id
        if not source_id:
            return
        row.repair_button.clicked.connect(lambda _checked=False, s=source_id: self.reimport_source_requested.emit(s))

    def _entry_display_name(self, entry: FreqEntry) -> str:
        source_id = entry.source_id
        meta = self._view.get(source_id) if (self._view is not None and source_id) else None
        return meta.source_name if meta else (source_id or "(missing)")

    def _entry_disk_dir(self, entry: FreqEntry) -> Path | None:
        if not entry.source_id:
            return None
        try:
            return resolve_managed_slot(self._freqs_root, entry.source_id)
        except ValueError:
            return None

    def _owns_entry_disk_dir(self, entry: FreqEntry, target: Path) -> bool:
        return bool(entry.source_id) and prove_owned_slot(target.parent, entry.source_id, "frequency")

    def _confirm_remove(self, display: str, *, body: str | None = None) -> bool:
        if body is None:
            body = self.tr(
                "Remove '%1' from the frequency chain?\n\nOnly the index files are deleted.\n"
                "This cannot be undone. You would need to re-import to use this source again."
            )
        reply = QMessageBox.question(
            self,
            self.tr("Remove frequency source"),
            tr_format(body, display),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _confirm_chain_only_remove(self, display: str) -> bool:
        return self._confirm_remove(
            display,
            body=self.tr(
                "Remove '%1' from the frequency chain?\n\n"
                "Index files on disk will be left untouched because the folder could not be proven "
                "to belong to Anki Miner."
            ),
        )

    def _acquire_release_for_remove(self) -> bool:
        # Drop any cached sqlite handles before rmtree (Windows lock safety).
        # No-op unless a release callback is wired.
        if not self.request_resource_release():
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
        """Right-click a source row to re-import or remove it."""
        # While an async scan is in flight the list shows a single disabled
        # "Loading…" placeholder, not real rows. Resolving a right-click through
        # self._chain then targets an arbitrary real source the user never
        # clicked — and Remove would rmtree it. Bail, mirroring the dictionary
        # panel's "meta is None → return" guard.
        if self._scan_in_flight or self.has_active_mutation():
            return
        item = self._list.itemAt(pos)
        if item is None:
            return
        index = self._list.row(item)
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if not entry.source_id:
            return

        menu = QMenu(self._list)
        reimport_action = menu.addAction(self.tr("Re-import…"))
        remove_action = menu.addAction(self.tr("Remove"))
        viewport = self._list.viewport()
        global_pos = viewport.mapToGlobal(pos) if viewport is not None else self._list.mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if chosen is reimport_action:
            self.reimport_source_requested.emit(entry.source_id)
        elif chosen is remove_action:
            self.remove(index)
