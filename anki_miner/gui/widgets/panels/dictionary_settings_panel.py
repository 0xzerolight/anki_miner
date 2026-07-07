"""Dictionary settings panel."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import ChainEntry
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


def _on_rmtree_error(func, path, _exc_info):
    """rmtree onerror handler: clear the read-only bit then retry once.

    Windows refuses to delete read-only files; Yomitan zip extractions sometimes
    inherit that attribute. Clearing S_IWRITE and re-invoking the failing op
    (unlink / rmdir) lets the walk continue. Any other failure re-raises.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def _robust_rmtree(target: Path, *, retries: int = 3, delay_s: float = 0.1) -> None:
    """rmtree with Windows-aware retry.

    Two failure modes seen on Win11: read-only file attributes (handled inline by
    ``_on_rmtree_error``) and transient ``[WinError 32] file in use`` from sqlite
    read-only handles still being released by GC. The retry loop absorbs the
    second case best-effort; final failure surfaces to the caller as the last
    OSError so the UI can show the same dialog as before.
    """
    last_exc: OSError | None = None
    for _ in range(retries):
        try:
            shutil.rmtree(target, onerror=_on_rmtree_error)
            return
        except OSError as e:
            last_exc = e
            time.sleep(delay_s)
    assert last_exc is not None
    raise last_exc


class _ChainRow(QWidget):
    """One row in the chain list: checkbox + label + format badge + count."""

    toggled = pyqtSignal()

    def __init__(
        self,
        entry: ChainEntry,
        display_name: str,
        format_label: str,
        count: int,
        *,
        stale: bool = False,
    ):
        super().__init__()
        self.entry = entry
        self.stale = stale
        self.reimport_button: QPushButton | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(entry.enabled)
        self.checkbox.stateChanged.connect(lambda _s: self.toggled.emit())
        layout.addWidget(self.checkbox)

        label_text = f"⚠ {display_name}" if stale else display_name
        name_label = QLabel(label_text)
        layout.addWidget(name_label, 1)

        if stale:
            self.stale_label = QLabel(self.tr("<i> — re-import to refresh</i>"))
            self.stale_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(self.stale_label)

        if format_label:
            badge = QLabel(format_label)
            if entry.kind == "jisho":
                badge.setStyleSheet("color: #d97706; font-size: 10px;")
            else:
                badge.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(badge)
        if count:
            count_label = QLabel(tr_format(self.tr("%1 entries"), f"{count:,}"))
            count_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(count_label)

        if stale:
            self.reimport_button = QPushButton(self.tr("Re-import"))
            layout.addWidget(self.reimport_button)

    def get_enabled(self) -> bool:
        return self.checkbox.isChecked()


class DictionarySettingsPanel(FormPanel):
    """Reorderable chain of dictionary providers."""

    add_dict_requested = pyqtSignal()
    reimport_jmdict_requested = pyqtSignal()
    reimport_dict_requested = pyqtSignal(str)
    reimport_all_requested = pyqtSignal()
    rescan_requested = pyqtSignal()
    chain_changed = pyqtSignal()
    # Emitted once a dictionary has been successfully removed from both the
    # in-memory chain and disk. Distinct from ``chain_changed`` so the settings
    # tab can persist the new chain to gui_config.json immediately — a delete is
    # destructive and asymmetric with reorder/toggle, which the user may still
    # be experimenting with.
    dictionary_removed = pyqtSignal()

    def __init__(self, dicts_root: Path, parent=None):
        super().__init__("Dictionary Settings", parent=parent)
        self._dicts_root = dicts_root
        self._chain: list[ChainEntry] = []
        # Cached registry; refreshed on demand instead of per UI tick. Each
        # construction scans every dict's meta table — needlessly slow on
        # network mounts when the user is just reordering rows.
        self._registry: DictionaryRegistry | None = None
        # Optional callback invoked before destructive remove to ask the rest
        # of the app to close cached sqlite handles (Issue #30, Win11 lock).
        # Returns True on success, False if a mining run is in flight.
        self._release_callback: Callable[[], bool] | None = None
        # Guard: registry scan deferred to first showEvent so it does not run
        # on the GUI thread before the window paints (OVH-053).
        self._scanned: bool = False
        # Set while an off-thread registry scan is running so overlapping
        # scans / removes don't stack (OVH disk-scan-off-thread).
        self._scan_in_flight: bool = False
        # Set when a rescan is requested while one is already in flight. The
        # in-flight worker captured the pre-request disk state, so dropping the
        # request would leave the panel showing stale data after an import. On
        # scan completion we re-dispatch a single fresh scan instead. A boolean
        # (not a counter) — one trailing scan reads the latest disk state, so
        # collapsing N pending requests into one re-dispatch cannot loop.
        self._rescan_pending: bool = False
        self._setup_fields()

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        """Trigger the first registry scan when the panel becomes visible.

        Defers DictionaryRegistry.load() off the app startup / first-paint
        path (OVH-053).  Subsequent showEvent calls are no-ops; explicit
        refreshes (refresh_registry, set_dicts_root, set_chain) call
        _rebuild_list directly and bypass this guard.
        """
        super().showEvent(event)
        if not self._scanned:
            self._scanned = True
            self._scan_and_render_async()

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the pre-remove resource-release hook.

        See ``remove()``. Injected by app.py at startup so the panel can call
        ``MainWindow.release_dictionary_resources`` without importing it.
        """
        self._release_callback = cb

    def request_resource_release(self) -> bool:
        """Public proxy so siblings (e.g. settings_tab's re-import handler)
        can ask the rest of the app to close cached sqlite handles without
        reaching into ``_release_callback`` directly (Issue #32).

        Returns ``True`` when no callback is wired or the callback succeeded;
        ``False`` when the callback refused (typically a mining run is in
        flight, see ``MainWindow.release_dictionary_resources``).
        """
        if self._release_callback is None:
            return True
        return self._release_callback()

    def set_dicts_root(self, dicts_root: Path) -> None:
        """Update the dicts root (e.g. after a config save) and invalidate caches."""
        self._dicts_root = dicts_root
        self._registry = None
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

    def refresh_registry(self) -> None:
        """Force a registry rescan. Call after an import finishes.

        The disk scan runs off the GUI thread; the row list re-renders once it
        completes (OVH disk-scan-off-thread).
        """
        self._registry = None
        self._scanned = True
        self._scan_and_render_async()

    def _scan_and_render_async(self) -> None:
        """Scan the registry off-thread (if not cached) then render the rows.

        When the registry is already cached this is a synchronous render — no
        worker is spawned — so callers that supplied meta directly (tests,
        set_chain) keep their immediate behavior. Otherwise a ``Loading…``
        placeholder shows while ``DictionaryRegistry.load()`` runs on a worker.
        """
        if self._registry is not None or not self._scanned:
            # Either cached, or not yet allowed to scan (pre-first-show).
            self._rebuild_list()
            return
        if self._scan_in_flight:
            # A scan is already running against the pre-request disk state.
            # Mark a rescan so the done/error callback re-dispatches once the
            # current scan finishes (otherwise an import's refresh is lost).
            self._rescan_pending = True
            return
        self._scan_in_flight = True
        self._show_loading_placeholder()

        dicts_root = self._dicts_root

        def _scan() -> DictionaryRegistry:
            registry = DictionaryRegistry(dicts_root)
            registry.load()
            return registry

        run_off_thread(self, _scan, self._on_scan_done, self._on_scan_error)

    def _on_scan_done(self, registry: object) -> None:
        self._scan_in_flight = False
        # The worker returns the loaded registry; run_off_thread carries the
        # result as ``object``, so narrow it back to the registry type.
        self._registry = cast("DictionaryRegistry", registry)
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("Dictionary registry scan failed: %s", msg)
        # Render whatever we have (rows without metadata) so the panel isn't
        # stuck on the Loading placeholder.
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _redispatch_pending_scan(self) -> None:
        """Re-run one scan if a rescan was requested while one was in flight.

        Drops the now-stale cached registry so the trailing scan reads the
        latest disk state. Single-shot: the flag is cleared before dispatch, so
        only the rescans requested *during* this dispatch can queue another.
        """
        if not self._rescan_pending:
            return
        self._rescan_pending = False
        self._registry = None
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
            placeholder = QListWidgetItem(self.tr("Loading…"))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        finally:
            self._list.setUpdatesEnabled(True)

    def _set_reorder_controls_enabled(self, enabled: bool) -> None:
        """Toggle the move-up/down + remove buttons together."""
        self._up_btn.setEnabled(enabled)
        self._down_btn.setEnabled(enabled)
        self._remove_btn.setEnabled(enabled)

    def set_per_row_reimport_enabled(self, enabled: bool) -> None:
        """Toggle every stale-row Re-import button.

        Prevents a user from launching a second per-row import while
        another is in flight — clobbering ``_active_import_worker`` would
        orphan the first worker.
        """
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if isinstance(widget, _ChainRow) and widget.reimport_button is not None:
                widget.reimport_button.setEnabled(enabled)

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

        self._reset_dicts_root_btn = QPushButton(self.tr("Reset to default"))
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
        )

        self.add_section(self.tr("Active Dictionaries"))
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel(self.tr("Top entry fills the MainDefinition field.")))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        self._add_btn = QPushButton(self.tr("+ Add Dictionary…"))
        self._add_btn.clicked.connect(self.add_dict_requested.emit)
        buttons.addWidget(self._add_btn)

        self._reimport_btn = QPushButton(self.tr("Reimport All"))
        self._reimport_btn.clicked.connect(self.reimport_all_requested.emit)
        buttons.addWidget(self._reimport_btn)

        self._restore_btn = QPushButton(self.tr("Restore from Disk"))
        self._restore_btn.setToolTip(
            self.tr(
                "Re-add dictionaries found in the storage folder that aren't in the "
                "list above (e.g. after a settings reset). No re-import needed."
            )
        )
        self._restore_btn.clicked.connect(self.rescan_requested.emit)
        buttons.addWidget(self._restore_btn)

        self._up_btn = QPushButton("↑")
        self._up_btn.setToolTip(self.tr("Move up in priority"))
        self._up_btn.clicked.connect(lambda: self.move_up(self._list.currentRow()))
        buttons.addWidget(self._up_btn)

        self._down_btn = QPushButton("↓")
        self._down_btn.setToolTip(self.tr("Move down in priority"))
        self._down_btn.clicked.connect(lambda: self.move_down(self._list.currentRow()))
        buttons.addWidget(self._down_btn)

        self._remove_btn = QPushButton(self.tr("Remove"))
        self._remove_btn.clicked.connect(lambda: self.remove(self._list.currentRow()))
        buttons.addWidget(self._remove_btn)

        layout.addLayout(buttons)
        self.add_field("", container)

        # Pitch accent: file selector only. Activation is resource-driven —
        # importing a pitch file turns the feature on (config.pitch_active);
        # there is no separate on/off checkbox.
        self.add_section(self.tr("Pitch Accent"))
        self.pitch_accent_selector = FileSelector(
            label="",
            file_mode=True,
            file_filter="Pitch accent (*.csv *.tsv *.txt *.zip);;All Files (*)",
            placeholder=self.tr("Select pitch accent CSV/TSV or Yomitan zip..."),
            default_dir=ANKI_MINER_HOME,
        )
        self.add_field(
            self.tr("Pitch Accent File"),
            self.pitch_accent_selector,
            helper=self.tr(
                "CSV/TSV with columns (reading, kanji, pattern), or a "
                "Yomitan-format pitch zip (e.g. Kanjium, NHK). Yomitan zips "
                "are imported into ~/.anki_miner/pitch_accent.csv on Save."
            ),
        )

        # Frequency sources now live in their own Settings → Frequency tab
        # (multi-source additive chain). The old single-file picker that used
        # to sit here was removed.

        self.add_stretch()

    def set_chain(self, chain: tuple[ChainEntry, ...]) -> None:
        self._chain = list(chain)
        self._rebuild_list()

    def get_chain(self) -> tuple[ChainEntry, ...]:
        # Sync enabled flags from row widgets
        out: list[ChainEntry] = []
        for i, entry in enumerate(self._chain):
            row = self._row_widget(i)
            enabled = row.get_enabled() if row is not None else entry.enabled
            out.append(ChainEntry(kind=entry.kind, dict_id=entry.dict_id, enabled=enabled))
        return tuple(out)

    def move_up(self, index: int) -> None:
        if index <= 0 or index >= len(self._chain):
            return
        # Capture current enabled state before rebuild
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

    def remove(self, index: int) -> None:
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if entry.kind == "jisho":
            return  # Jisho can be disabled but not removed

        # Resolve display name + on-disk folder for the confirm prompt and
        # the actual rmtree. dict_id is the folder name under dicts_root.
        dict_id = entry.dict_id
        registry = self._registry
        meta = registry.get(dict_id) if (registry is not None and dict_id) else None
        display = meta.source_name if meta else (dict_id or "(missing)")
        dict_dir = (self._dicts_root / dict_id) if dict_id else None

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
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Drop sqlite handles before rmtree. On Windows the index.sqlite file
        # stays locked while any DefinitionService still holds its read-only
        # connection, and the retry loop in _robust_rmtree can't unblock that
        # — only an explicit provider.close() can (Issue #30).
        if self._release_callback is not None and not self._release_callback():
            QMessageBox.warning(
                self,
                self.tr("Remove failed"),
                self.tr("A mining run is in progress. Stop it before removing dictionaries."),
            )
            return

        # Capture the post-remove chain on the GUI thread (reads row widgets)
        # BEFORE dispatching the disk delete off-thread — the worker must touch
        # no widgets.
        new_chain = list(self.get_chain())
        del new_chain[index]

        if dict_dir is None or not dict_dir.exists():
            # Nothing to delete on disk; finish synchronously.
            self._finalize_remove(new_chain)
            return

        # The rmtree (with its sleep-backed retry loop) runs off the GUI thread.
        # The Remove button + list are disabled while it runs and re-enabled in
        # the done/error callbacks. The release callback already ran above (on
        # the GUI thread) so sqlite handles are dropped before the delete.
        self._remove_btn.setEnabled(False)
        self._list.setEnabled(False)
        target = dict_dir

        def _delete() -> None:
            _robust_rmtree(target)

        run_off_thread(
            self,
            _delete,
            lambda _r: self._on_remove_done(new_chain),
            lambda msg: self._on_remove_error(target, msg),
        )

    def _on_remove_done(self, new_chain: list[ChainEntry]) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        self._finalize_remove(new_chain)

    def _on_remove_error(self, dict_dir: Path, msg: str) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        logger.error("Failed to delete dictionary folder %s: %s", dict_dir, msg)
        QMessageBox.warning(
            self,
            self.tr("Remove failed"),
            tr_format(self.tr("Could not delete %1:\n%2\n\nThe dictionary was not removed."), dict_dir, msg),
        )

    def _finalize_remove(self, new_chain: list[ChainEntry]) -> None:
        """Commit the chain mutation + rescan after a successful disk delete."""
        self._chain = new_chain
        # Disk state changed — drop cached scan so the next render reflects the
        # missing folder (and a re-add of the same id won't show stale meta).
        self._registry = None
        self._scan_and_render_async()
        self.chain_changed.emit()
        self.dictionary_removed.emit()

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click a dictionary row to re-import or remove it.

        Reuses the stale-row re-import signals so the same handler
        (`DictionaryImportFlow.reimport_dict`) drives the import flow
        regardless of entry point. Jisho rows have no menu — the online
        fallback can't be re-imported. Missing meta (dict files vanished from
        disk) also skip because we can't decide between yomitan and jmdict
        dispatch.
        """
        item = self._list.itemAt(pos)
        if item is None:
            return
        index = self._list.row(item)
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if entry.kind == "jisho" or entry.dict_id is None:
            return
        registry = self._registry
        meta = registry.get(entry.dict_id) if registry is not None else None
        if meta is None:
            return

        menu = QMenu(self._list)
        reimport_action = menu.addAction(self.tr("Re-import…"))
        remove_action = menu.addAction(self.tr("Remove"))
        viewport = self._list.viewport()
        global_pos = viewport.mapToGlobal(pos) if viewport is not None else self._list.mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if chosen is reimport_action:
            if meta.format == "jmdict":
                self.reimport_jmdict_requested.emit()
            else:
                self.reimport_dict_requested.emit(entry.dict_id)
        elif chosen is remove_action:
            self.remove(index)

    def _row_widget(self, index: int) -> _ChainRow | None:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, _ChainRow) else None

    def _rebuild_list(self) -> None:
        # Suspend repaints across clear+populate so the reorder ↑↓ buttons
        # don't flash on each rebuild. clear() destroys the previous row
        # widgets (and their signal connections), so there is no duplicate
        # handler risk.
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            # Render-only: the disk scan is owned by _scan_and_render_async,
            # which runs DictionaryRegistry.load() off the GUI thread and only
            # then calls back here with self._registry populated. Before first
            # show (OVH-053) self._registry is None and rows render without
            # metadata — a safe no-content state since the list is never visible
            # until the Settings tab is opened.
            registry = self._registry  # may be None before first show / scan
            for entry in self._chain:
                meta: DictMeta | None = None
                if entry.kind == "indexed":
                    meta = registry.get(entry.dict_id) if (registry is not None and entry.dict_id) else None
                    display = meta.source_name if meta else (entry.dict_id or "(missing)")
                    fmt = meta.format if meta else "missing"
                    count = meta.entry_count if meta else 0
                else:
                    display = self.tr("Jisho (online fallback)")
                    fmt = self.tr("⚠ rate-limited, slower")
                    count = 0
                stale = meta is not None and not meta.schema_ok
                row = _ChainRow(entry, display, fmt, count, stale=stale)
                row.toggled.connect(self.chain_changed.emit)
                if stale and row.reimport_button is not None and meta is not None:
                    # JMdict per-row Re-import fires the existing global signal so
                    # users land in the same import flow regardless of where they
                    # clicked. Other formats use the new per-dict signal.
                    if meta.format == "jmdict":
                        row.reimport_button.clicked.connect(self.reimport_jmdict_requested.emit)
                    else:
                        dict_id = meta.dict_id
                        row.reimport_button.clicked.connect(
                            lambda _checked=False, d=dict_id: self.reimport_dict_requested.emit(d)
                        )
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
            # Real rows are back: restore the controls the loading placeholder disabled.
            self._set_reorder_controls_enabled(True)
