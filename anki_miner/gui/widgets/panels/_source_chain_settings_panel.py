"""Shared middle base for the Frequency and Pitch Accent chain panels.

Both panels edit the same kind of chain: ``source_id`` entries, each backed by
a managed ``<root>/<source_id>/`` slot holding an ``index.sqlite`` that can be
rebuilt from the copy saved at import time. This class owns everything the two
panels share: the maintenance actions, the root setter, the row spec, the
per-row repair button, the remove hooks and the right-click menu.

Each subclass keeps what differs: the registry class, the slot kind, the
format labels, the entry type, the remove confirmations (the D24 QMessageBox
ledger keys them per panel) and every user-facing string. The strings arrive
through :class:`_SourceChainPanelLabels` and ``_ChainPanelStrings``, both built
by the subclass with ``self.tr(...)``, so each literal keeps its panel's
translation context. This module makes no ``tr()`` call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget

from anki_miner.gui.widgets.base import ScreenIssue
from anki_miner.gui.widgets.panels.chain_priority_list import ChainRowSpec, ChainSourceRow
from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
    ChainListLabels,
    ChainSettingsPanelBase,
    _RegistryView,
)
from anki_miner.services._sqlite_index import StoreFamily, prove_owned_slot, resolve_managed_slot
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.robust_fs import RmtreeOutcome, robust_rmtree


def _robust_rmtree(target: Path) -> RmtreeOutcome:
    """Panel-local seam for post-commit cleanup."""
    return robust_rmtree(target, mode="outcome")


@dataclass(frozen=True)
class _SourceChainPanelLabels:
    """Per-panel translated strings for :class:`_SourceChainSettingsPanel`.

    Built in each subclass with ``self.tr(...)``, like ``_ChainPanelStrings``:
    the base reads already-translated text and never calls ``tr()`` itself.
    """

    section: str
    restore: str
    restore_tooltip: str
    reimport_all: str
    reimport_all_tooltip: str
    chain: ChainListLabels
    #: ``tr_format`` template; ``%1`` is the digit-grouped entry count.
    entries: str
    enabled: str
    #: ``tr_format`` template; ``%1`` is the source's display name.
    enable: str
    #: ``tr_format`` template; ``%1`` is the source's display name.
    enable_or_disable: str
    #: The stale row's repair button.
    repair: str
    stale_warning: str
    missing_warning: str
    resources_in_use: str
    menu_reimport: str
    menu_remove: str


class _SourceChainSettingsPanel(ChainSettingsPanelBase):
    """Reorderable chain of re-importable ``source_id`` sources."""

    add_source_requested = pyqtSignal()
    reimport_source_requested = pyqtSignal(str)
    reimport_all_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    # --- Class-level knobs the subclass sets ---
    #: Registry class: built with the root and loaded off the GUI thread.
    _REGISTRY_CLS: ClassVar[type[Any]]
    #: Slot kind that ``prove_owned_slot`` checks before a recursive delete.
    _SLOT_KIND: ClassVar[StoreFamily]
    #: Human-readable format labels keyed by the importer's ``format`` value.
    _FORMAT_LABELS: ClassVar[dict[str, str]]

    _labels: _SourceChainPanelLabels

    def __init__(self, title: str, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(title, parent=parent)
        self._source_root = root
        # Optional callback invoked before destructive replacement/removal to
        # ask the rest of the app to close cached sqlite handles.
        self._release_callback: Callable[[], bool] | None = None

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the resource-release hook used by reimport and remove."""
        self._release_callback = cb

    def request_resource_release(self) -> bool:
        """Ask the app to close cached resource handles before replacement."""
        if self._release_callback is None:
            return True
        return self._release_callback()

    def set_source_root(self, root: Path) -> None:
        """Update the storage root (e.g. after a config swap) and invalidate caches.

        Mirrors ``DictionarySettingsPanel.set_dicts_root``. Without it a config
        carrying a different root leaves this panel scanning the old root for
        the rest of the session, and the destructive remove flow resolves
        ``resolve_managed_slot`` against the wrong directory. This panel has no
        storage-folder selector, so there is nothing to re-sync.
        """
        if root == self._source_root:
            # _load_config runs after every auto-save commit that touches a
            # non-external field, and the root is the same almost every time.
            # Rescanning anyway would flash a "Loading…" placeholder and take a
            # hold_mutation("scan") token (disabling Add) on every settings edit.
            return
        self._source_root = root
        self._view = None
        # Root changed → cached scan is stale; rescan off-thread (no-op before
        # first show, where _scanned is still False).
        self._scan_and_render_async()

    def _setup_fields(self) -> None:
        labels = self._labels
        self.add_section(labels.section)
        self._restore_btn = QAction(labels.restore, self)
        self._restore_btn.setToolTip(labels.restore_tooltip)
        self._restore_btn.triggered.connect(lambda _checked=False: self.restore_requested.emit())
        self._reimport_btn = QAction(labels.reimport_all, self)
        self._reimport_btn.setToolTip(labels.reimport_all_tooltip)
        self._reimport_btn.triggered.connect(lambda _checked=False: self.reimport_all_requested.emit())
        container = self._build_chain_container(
            labels.chain,
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
        chain: tuple[Any, ...],
        registry_meta: dict[str, Any] | None = None,
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

    def _build_view(self) -> _RegistryView:
        registry = self._REGISTRY_CLS(self._source_root)
        registry.load()
        return _RegistryView(registry.get)

    def _row_spec(self, entry: Any, view: _RegistryView | None) -> ChainRowSpec:
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
        labels = self._labels
        metadata: tuple[str, ...] = ()
        tooltip = ""
        if meta is not None:
            extra, tooltip = self._extra_row_metadata(meta)
            metadata = (
                self._FORMAT_LABELS.get(meta.format, meta.format),
                *extra,
                tr_format(labels.entries, f"{meta.entry_count:,}"),
            )
        return ChainRowSpec(
            entry=entry,
            title=display,
            metadata=metadata,
            metadata_tooltip=tooltip,
            enabled_text=labels.enabled,
            enabled_accessible_text=tr_format(labels.enable, display),
            enabled_tooltip=tr_format(labels.enable_or_disable, display),
            warning=self._row_warning(stale=stale, absent=absent),
            repair_text=labels.repair if stale else "",
        )

    def _extra_row_metadata(self, meta: Any) -> tuple[tuple[str, ...], str]:
        """Metadata between the format and the entry count, and its tooltip.

        Default: none. The frequency panel marks word-based sources here.
        """
        return (), ""

    def _row_warning(self, *, stale: bool, absent: bool) -> str:
        if stale:
            return self._labels.stale_warning
        if absent:
            return self._labels.missing_warning
        return ""

    def _connect_row_repair(self, row: ChainSourceRow) -> None:
        if row.repair_button is None:
            return
        source_id = row.entry.source_id
        if not source_id:
            return
        row.repair_button.clicked.connect(lambda _checked=False, s=source_id: self.reimport_source_requested.emit(s))

    def _entry_display_name(self, entry: Any) -> str:
        source_id = entry.source_id
        meta = self._view.get(source_id) if (self._view is not None and source_id) else None
        return meta.source_name if meta else (source_id or "(missing)")

    def _entry_disk_dir(self, entry: Any) -> Path | None:
        if not entry.source_id:
            return None
        try:
            return resolve_managed_slot(self._source_root, entry.source_id)
        except ValueError:
            return None

    def _owns_entry_disk_dir(self, entry: Any, target: Path) -> bool:
        return bool(entry.source_id) and prove_owned_slot(target.parent, entry.source_id, self._SLOT_KIND)

    def _acquire_release_for_remove(self) -> bool:
        # Drop any cached sqlite handles before rmtree (Windows lock safety).
        # No-op unless a release callback is wired.
        if not self.request_resource_release():
            self.show_screen_issue(ScreenIssue(summary=self._labels.resources_in_use))
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
        reimport_action = menu.addAction(self._labels.menu_reimport)
        remove_action = menu.addAction(self._labels.menu_remove)
        viewport = self._list.viewport()
        global_pos = viewport.mapToGlobal(pos) if viewport is not None else self._list.mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if chosen is reimport_action:
            self.reimport_source_requested.emit(entry.source_id)
        elif chosen is remove_action:
            self.remove(index)
