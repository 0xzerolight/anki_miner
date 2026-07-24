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

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import FreqEntry
from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
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


class _FreqRow(QWidget):
    """One row in the chain list: checkbox + name + format badge + count + missing badge."""

    toggled = pyqtSignal()

    def __init__(
        self,
        entry: FreqEntry,
        display_name: str,
        format_label: str,
        count: int,
        *,
        missing: bool = False,
        is_categorical: bool = False,
    ):
        super().__init__()
        self.entry = entry
        self.missing = missing
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.checkbox = QCheckBox()
        # Text-less toggle: without an accessible name a screen reader announces
        # only "check box", and the source it belongs to is conveyed purely by
        # the sibling QLabel. 11 such toggles were unnamed across the 4 chain panels.
        self.checkbox.setAccessibleName(tr_format(self.tr("Enable %1"), display_name))
        self.checkbox.setToolTip(tr_format(self.tr("Enable or disable %1"), display_name))
        self.checkbox.setChecked(entry.enabled)
        self.checkbox.stateChanged.connect(lambda _s: self.toggled.emit())
        layout.addWidget(self.checkbox)

        name_label = QLabel(display_name)
        layout.addWidget(name_label, 1)

        if format_label:
            badge = QLabel(format_label)
            badge.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(badge)

        if is_categorical:
            # Word-based sources hold level labels (N5/Basic) shown on the card
            # but excluded from the frequency-rank cutoff — flag that here.
            word_based = QLabel(self.tr("word-based"))
            word_based.setStyleSheet("color: gray; font-size: 10px;")
            word_based.setToolTip(self.tr("Level labels are shown on the card but not used for frequency filtering."))
            layout.addWidget(word_based)

        if count:
            count_label = QLabel(tr_format(self.tr("%1 entries"), f"{count:,}"))
            count_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(count_label)

        if missing:
            missing_label = QLabel(self.tr("⚠ missing — re-import"))
            missing_label.setStyleSheet("color: #d97706; font-size: 10px;")
            layout.addWidget(missing_label)

    def get_enabled(self) -> bool:
        return self.checkbox.isChecked()


class FrequencySettingsPanel(ChainSettingsPanelBase):
    """Reorderable chain of additive frequency sources."""

    add_source_requested = pyqtSignal()
    reimport_source_requested = pyqtSignal(str)

    _ROW_CLASS = _FreqRow
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
            remove_failed_title=self.tr("Remove failed"),
            could_not_delete_template=self.tr("Could not delete %1:\n%2\n\nThe frequency source was not removed."),
            files_left_title=self.tr("Files left untouched"),
            files_left_template=self.tr(
                "The chain entry was removed, but files at %1 were left untouched because "
                "the folder could not be proven to belong to Anki Miner."
            ),
            intact_failure_template=self.tr("Could not remove %1:\n%2\n\nThe files are intact. Try again."),
            partial_failure_template=self.tr(
                "Could not complete removal of %1:\n%2\n\nThe files were partially changed. "
                "Re-import or repair this frequency source before retrying."
            ),
            config_pending_failure_template=self.tr(
                "Could not restore %1 after its configuration update failed:\n%2\n\n"
                "The files are no longer in the installed location; a configuration update "
                "is pending. Restart Anki Miner before retrying."
            ),
            post_save_warning_template=self.tr(
                "Removal of %1 was saved, but Anki Miner could not refresh it:\n%2\n\n"
                "The removal was saved and will remain after restart."
            ),
            cleanup_pending_template=self.tr(
                "%1 was removed, but its tombstone at %2 could not be deleted:\n%3\n\n"
                "The removal is saved; cleanup is pending and will be retried at startup."
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

    def _setup_fields(self) -> None:
        self.add_section(self.tr("Active Frequency Sources"))
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(
            QLabel(
                self.tr(
                    "Sources are layered additively — the best (lowest) rank across all "
                    "enabled sources wins. Top entry breaks ties first."
                )
            )
        )

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        self._add_btn = QPushButton(self.tr("+ Add Source…"))
        self._add_btn.clicked.connect(self.add_source_requested.emit)
        buttons.addWidget(self._add_btn)

        self._up_btn = QPushButton("↑")
        self._up_btn.setAccessibleName(self.tr("Move up"))
        self._up_btn.setToolTip(self.tr("Move up (breaks rank ties first)"))
        self._up_btn.clicked.connect(lambda: self.move_up(self._list.currentRow()))
        buttons.addWidget(self._up_btn)

        self._down_btn = QPushButton("↓")
        self._down_btn.setAccessibleName(self.tr("Move down"))
        self._down_btn.setToolTip(self.tr("Move down"))
        self._down_btn.clicked.connect(lambda: self.move_down(self._list.currentRow()))
        buttons.addWidget(self._down_btn)

        self._remove_btn = QPushButton(self.tr("Remove"))
        self._remove_btn.clicked.connect(lambda: self.remove(self._list.currentRow()))
        buttons.addWidget(self._remove_btn)

        layout.addLayout(buttons)
        self.add_field("", container)
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
        else:
            # Invalidate so _rebuild_list will scan on demand.
            self._view = None
        self._rebuild_list()

    def get_chain(self) -> tuple[FreqEntry, ...]:
        out: list[FreqEntry] = []
        for i, entry in enumerate(self._chain):
            row = self._row_widget(i)
            enabled = row.get_enabled() if row is not None else entry.enabled
            out.append(FreqEntry(source_id=entry.source_id, enabled=enabled))
        return tuple(out)

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        self._add_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _build_view(self) -> _RegistryView:
        registry = FrequencySourceRegistry(self._freqs_root)
        registry.load()
        return _RegistryView(registry.get)

    def _make_row(self, entry: FreqEntry, view: _RegistryView | None) -> QWidget:
        meta = view.get(entry.source_id) if (view is not None and entry.source_id) else None
        # A chain entry whose source folder is gone (or schema-mismatched so
        # build_sources would drop it) is "missing" — prompt re-import.
        missing = view is not None and (meta is None or not meta.schema_ok)
        display = meta.source_name if meta else (entry.source_id or "(missing)")
        fmt = _FORMAT_LABELS.get(meta.format, meta.format) if meta else ""
        count = meta.entry_count if meta else 0
        is_categorical = meta.is_categorical if meta else False
        row = _FreqRow(entry, display, fmt, count, missing=missing, is_categorical=is_categorical)
        row.toggled.connect(self._on_row_toggled)
        return row

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

    def _confirm_remove(self, display: str) -> bool:
        reply = QMessageBox.question(
            self,
            self.tr("Remove frequency source"),
            tr_format(
                self.tr(
                    "Remove '%1' from the frequency chain?\n\nOnly the index files are deleted.\n"
                    "This cannot be undone. You would need to re-import to use this source again."
                ),
                display,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _acquire_release_for_remove(self) -> bool:
        # Drop any cached sqlite handles before rmtree (Windows lock safety).
        # No-op unless a release callback is wired.
        if not self.request_resource_release():
            QMessageBox.warning(
                self,
                self.tr("Remove failed"),
                self.tr(
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again."
                ),
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
