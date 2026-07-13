"""Shared base for the reorderable chain settings panels.

Hoists the verbatim-identical state machine shared by
:class:`~anki_miner.gui.widgets.panels.dictionary_settings_panel.DictionarySettingsPanel`,
:class:`~anki_miner.gui.widgets.panels.frequency_settings_panel.FrequencySettingsPanel`,
and
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`:
the lazy first-show registry scan, the off-thread rescan/redispatch dance, the
reorder (move up/down) and destructive-remove flows, the loading placeholder, and
the row-list rebuild.

Per-panel deltas stay subclass responsibilities via explicit hooks (see the
"Subclass hooks" section): the field layout (``_setup_fields``), the entry type
and its ``get_chain``/``set_chain`` marshalling, the off-thread registry factory
(``_build_view``), row construction (``_make_row``), the context menu, and the
remove-flow specifics (protected kinds, disk-less removal, confirm/release
dialogs). All user-facing strings stay bound to each subclass's own ``self.tr``
context — either textually inside a subclass method or, for the few literals the
hoisted slots need, via the :class:`_ChainPanelStrings` object each subclass
builds with ``self.tr(...)`` (the ``_ToolTabStrings`` precedent). The base itself
makes no ``tr()`` call, so extraction contexts never churn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QWidget,
)

from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ChainPanelStrings:
    """Per-panel translated labels consumed by the hoisted slots.

    Built in each subclass via ``self.tr(...)`` so every literal stays in that
    panel's tr-context (mirroring ``_ToolTabStrings`` in ``_tool_tab_base``).
    The base reads the already-translated strings — it never calls ``tr()``.
    """

    loading: str
    remove_failed_title: str
    # Already-translated ``tr_format`` template: "Could not delete %1:\n%2\n\n…".
    could_not_delete_template: str


class _RegistryView:
    """Uniform meta-lookup shim: ``get(id) -> meta | None``.

    Wraps a single getter callable so the frequency and audio panels can feed
    either a live registry (``registry.get`` / ``registry.packs.get``) or a
    pre-built ``dict`` injected by ``set_chain(registry_meta=...)`` (tests)
    through the same interface. The dictionary panel stores its
    ``DictionaryRegistry`` directly (it already exposes ``.get``) and does not
    need this shim.
    """

    def __init__(self, getter: Callable[[str], Any | None]) -> None:
        self._getter = getter

    def get(self, key: str) -> Any | None:
        return self._getter(key)


class ChainSettingsPanelBase(FormPanel):
    """State machine shared by the reorderable chain settings panels.

    See the module docstring. Subclasses provide the field layout, the entry
    type marshalling, and the remove/row/menu hooks; the base owns the scan and
    reorder/remove lifecycle.
    """

    # Persist-on-every-edit signal common to all three panels. The settings tab
    # wires this (narrow chain persist) — a destructive remove re-emits it too,
    # so no separate removal signal is needed (OVH-032 / T-08 / Issue #30).
    chain_changed = pyqtSignal()

    # --- Class-level knobs the subclass sets (declared for the type checker) ---
    _ROW_CLASS: ClassVar[type]
    # WARNING/ERROR log labels (English, not user-facing → not translated).
    _SCAN_ERROR_LABEL: ClassVar[str] = "Registry scan failed"
    _REMOVE_ERROR_NOUN: ClassVar[str] = "folder"

    # --- Instance attributes the subclass builds in _setup_fields ---
    _list: QListWidget
    _up_btn: QPushButton
    _down_btn: QPushButton
    _remove_btn: QPushButton
    _strings: _ChainPanelStrings

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent=parent)
        self._chain: list[Any] = []
        # Cached registry view (subclass-typed); refreshed on demand instead of
        # per UI tick. The dictionary panel stores a DictionaryRegistry; the
        # frequency/audio panels store a _RegistryView.
        self._view: Any | None = None
        # Guard: registry scan deferred to first showEvent so it does not run on
        # the GUI thread before the window paints (OVH-053).
        self._scanned: bool = False
        # Set while an off-thread registry scan is running so overlapping scans /
        # removes don't stack (OVH disk-scan-off-thread).
        self._scan_in_flight: bool = False
        # Set when a rescan is requested while one is already in flight. The
        # in-flight worker captured the pre-request disk state, so dropping the
        # request would leave the panel showing stale data after an import. On
        # scan completion we re-dispatch a single fresh scan instead. A boolean
        # (not a counter) — one trailing scan reads the latest disk state, so
        # collapsing N pending requests into one re-dispatch cannot loop.
        self._rescan_pending: bool = False

    # ------------------------------------------------------------------
    # First-show / refresh lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        """Trigger the first registry scan when the panel becomes visible.

        Defers the registry load off the app startup / first-paint path
        (OVH-053). Subsequent showEvent calls are no-ops; explicit refreshes
        (refresh_registry, set_chain) call _rebuild_list directly and bypass
        this guard.
        """
        super().showEvent(event)
        if not self._scanned:
            self._scanned = True
            self._scan_and_render_async()

    def refresh_registry(self) -> None:
        """Force a registry rescan. Call after an import finishes.

        The disk scan runs off the GUI thread; the row list re-renders once it
        completes (OVH disk-scan-off-thread).
        """
        self._view = None
        self._scanned = True
        self._scan_and_render_async()

    def _scan_and_render_async(self) -> None:
        """Scan the registry off-thread (if not cached) then render the rows.

        When the view is already cached this is a synchronous render — no worker
        is spawned — so callers that supplied meta directly (tests, set_chain)
        keep their immediate behavior. Otherwise a ``Loading…`` placeholder shows
        while the subclass ``_build_view`` runs on a worker thread.
        """
        if self._view is not None or not self._scanned:
            # Either cached, or not yet allowed to scan (pre-first-show).
            self._rebuild_list()
            return
        if self._scan_in_flight:
            # A scan is already running against the pre-request disk state. Mark
            # a rescan so the done/error callback re-dispatches once the current
            # scan finishes (otherwise an import's refresh is lost).
            self._rescan_pending = True
            return
        self._scan_in_flight = True
        self._show_loading_placeholder()
        run_off_thread(self, self._build_view, self._on_scan_done, self._on_scan_error)

    def _on_scan_done(self, view: object) -> None:
        self._scan_in_flight = False
        self._view = view
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("%s: %s", self._SCAN_ERROR_LABEL, msg)
        # Render whatever we have (rows without metadata) so the panel isn't
        # stuck on the Loading placeholder.
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _redispatch_pending_scan(self) -> None:
        """Re-run one scan if a rescan was requested while one was in flight.

        Drops the now-stale cached view so the trailing scan reads the latest
        disk state. Single-shot: the flag is cleared before dispatch, so only
        the rescans requested *during* this dispatch can queue another.
        """
        if not self._rescan_pending:
            return
        self._rescan_pending = False
        self._view = None
        self._scan_and_render_async()

    def _show_loading_placeholder(self) -> None:
        """Render a single disabled 'Loading…' row while a scan is in flight."""
        # No real rows exist during the scan, so disable the reorder/remove
        # controls explicitly (they act on currentRow(), which would otherwise
        # operate on a transient placeholder); _rebuild_list re-enables them.
        self._set_reorder_controls_enabled(False)
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            placeholder = QListWidgetItem(self._strings.loading)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        finally:
            self._list.setUpdatesEnabled(True)

    def _set_reorder_controls_enabled(self, enabled: bool) -> None:
        """Toggle the move-up/down + remove buttons together."""
        self._up_btn.setEnabled(enabled)
        self._down_btn.setEnabled(enabled)
        self._remove_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Reorder / toggle
    # ------------------------------------------------------------------

    def _on_row_toggled(self) -> None:
        """Fold the live checkbox states back into ``self._chain`` before emitting.

        ``_rebuild_list`` renders checkboxes from ``self._chain``, so an unguarded
        rescan would re-render a just-disabled row from the stale chain and the
        next commit would re-persist ``enabled=True``. Syncing here keeps
        ``_chain`` authoritative.
        """
        self._chain = list(self.get_chain())
        self.chain_changed.emit()

    def move_up(self, index: int) -> None:
        if index <= 0 or index >= len(self._chain):
            return
        # Capture current enabled state before rebuild.
        self._chain = list(self.get_chain())
        self._chain[index - 1], self._chain[index] = self._chain[index], self._chain[index - 1]
        self._rebuild_list()
        self._list.setCurrentRow(index - 1)
        self.chain_changed.emit()

    def move_down(self, index: int) -> None:
        if index < 0 or index >= len(self._chain) - 1:
            return
        self._chain = list(self.get_chain())
        self._chain[index + 1], self._chain[index] = self._chain[index], self._chain[index + 1]
        self._rebuild_list()
        self._list.setCurrentRow(index + 1)
        self.chain_changed.emit()

    # ------------------------------------------------------------------
    # Destructive remove
    # ------------------------------------------------------------------

    def remove(self, index: int) -> None:
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if self._is_protected_entry(entry):
            return  # built-in / online entry: can be disabled but not removed
        if self._handle_diskless_remove(entry, index):
            return  # subclass fully handled a source with nothing on disk

        # Resolve the display name + on-disk folder for the confirm prompt and
        # the actual rmtree.
        display = self._entry_display_name(entry)
        target_dir = self._entry_disk_dir(entry)

        if not self._confirm_remove(display):
            return  # user declined the destructive-remove confirmation

        # Give the subclass a chance to drop cached sqlite handles before rmtree
        # (Issue #30, Win11 lock). Returns False to abort (e.g. mining in flight).
        if not self._acquire_release_for_remove():
            return

        # Capture the post-remove chain on the GUI thread (reads row widgets)
        # BEFORE dispatching the disk delete off-thread — the worker touches no
        # widgets.
        new_chain = list(self.get_chain())
        del new_chain[index]

        if target_dir is None or not target_dir.exists():
            # Nothing to delete on disk; finish synchronously.
            self._finalize_remove(new_chain)
            return

        # The rmtree (with its sleep-backed retry loop) runs off the GUI thread.
        # The Remove button + list are disabled while it runs and re-enabled in
        # the done/error callbacks.
        self._remove_btn.setEnabled(False)
        self._list.setEnabled(False)
        target = target_dir

        run_off_thread(
            self,
            lambda: self._rmtree_dir(target),
            lambda _r: self._on_remove_done(new_chain),
            lambda msg: self._on_remove_error(target, msg),
        )

    def _on_remove_done(self, new_chain: list[Any]) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        self._finalize_remove(new_chain)

    def _on_remove_error(self, target_dir: Path, msg: str) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        logger.error("Failed to delete %s %s: %s", self._REMOVE_ERROR_NOUN, target_dir, msg)
        QMessageBox.warning(
            self,
            self._strings.remove_failed_title,
            tr_format(self._strings.could_not_delete_template, target_dir, msg),
        )

    def _finalize_remove(self, new_chain: list[Any]) -> None:
        """Commit the chain mutation + rescan after a successful disk delete."""
        self._chain = new_chain
        # Disk state changed — drop cached view so the next render reflects the
        # missing folder (and a re-add of the same id won't show stale meta).
        self._view = None
        self._scan_and_render_async()
        self.chain_changed.emit()

    # ------------------------------------------------------------------
    # Row list
    # ------------------------------------------------------------------

    def _row_widget(self, index: int) -> Any:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, self._ROW_CLASS) else None

    def _rebuild_list(self) -> None:
        # Suspend repaints across clear+populate so the reorder ↑↓ buttons don't
        # flash on each rebuild. clear() destroys the previous row widgets (and
        # their signal connections), so there is no duplicate-handler risk.
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            # Render-only: the disk scan is owned by _scan_and_render_async, which
            # runs the subclass registry load off the GUI thread and only then
            # calls back here with self._view populated. Before first show
            # (OVH-053) self._view is None and rows render without metadata — a
            # safe no-content state since the list is never visible until the
            # Settings tab is opened.
            view = self._view  # may be None before first show / scan
            for entry in self._chain:
                row = self._make_row(entry, view)
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
            # Real rows are back: restore the controls the loading placeholder
            # disabled.
            self._set_reorder_controls_enabled(True)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _setup_fields(self) -> None:
        """Build the panel's fields, including ``self._list`` and the reorder
        buttons (``_up_btn`` / ``_down_btn`` / ``_remove_btn``)."""
        raise NotImplementedError

    def get_chain(self) -> tuple[Any, ...]:
        """Return the chain with live checkbox states folded back in."""
        raise NotImplementedError

    def _build_view(self) -> Any:
        """Construct + load the registry view OFF the GUI thread (no widgets)."""
        raise NotImplementedError

    def _make_row(self, entry: Any, view: Any) -> QWidget:
        """Build a fully-wired row widget for *entry* (``toggled`` connected)."""
        raise NotImplementedError

    def _entry_display_name(self, entry: Any) -> str:
        """Human-readable name for the remove-confirmation prompt."""
        raise NotImplementedError

    def _entry_disk_dir(self, entry: Any) -> Path | None:
        """On-disk index folder to rmtree, or None when nothing is on disk."""
        raise NotImplementedError

    def _confirm_remove(self, display: str) -> bool:
        """Show the destructive-remove confirmation; return True to proceed."""
        raise NotImplementedError

    def _rmtree_dir(self, target: Path) -> None:
        """Delete *target* off the GUI thread (module-local ``_robust_rmtree``)."""
        raise NotImplementedError

    def _is_protected_entry(self, entry: Any) -> bool:
        """True for entries that can be disabled but never removed (default: none)."""
        return False

    def _handle_diskless_remove(self, entry: Any, index: int) -> bool:
        """Fully handle removal of an entry with nothing on disk.

        Return True when handled (skips the confirm/release/rmtree flow). Default:
        not handled.
        """
        return False

    def _acquire_release_for_remove(self) -> bool:
        """Drop cached sqlite handles before rmtree; return False to abort.

        Default: no-op success (the audio panel keeps nothing open).
        """
        return True
