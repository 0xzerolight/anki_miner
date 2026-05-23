"""Dictionary settings panel."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
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
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta

logger = logging.getLogger(__name__)


def _on_rmtree_error(func, path, exc_info):
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
            self.stale_label = QLabel("<i> — re-import for new formatting</i>")
            self.stale_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(self.stale_label)

        if format_label:
            badge = QLabel(format_label)
            badge.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(badge)
        if count:
            count_label = QLabel(f"{count:,} entries")
            count_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(count_label)

        if stale:
            self.reimport_button = QPushButton("Re-import")
            layout.addWidget(self.reimport_button)

    def get_enabled(self) -> bool:
        return self.checkbox.isChecked()


class DictionarySettingsPanel(FormPanel):
    """Reorderable chain of dictionary providers."""

    add_dict_requested = pyqtSignal()
    reimport_jmdict_requested = pyqtSignal()
    reimport_dict_requested = pyqtSignal(str)
    reimport_all_requested = pyqtSignal()
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
        self._setup_fields()

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the pre-remove resource-release hook.

        See ``remove()``. Injected by app.py at startup so the panel can call
        ``MainWindow.release_dictionary_resources`` without importing it.
        """
        self._release_callback = cb

    def set_dicts_root(self, dicts_root: Path) -> None:
        """Update the dicts root (e.g. after a config save) and invalidate caches."""
        self._dicts_root = dicts_root
        self._registry = None
        self._rebuild_list()

    def refresh_registry(self) -> None:
        """Force a registry rescan. Call after an import finishes."""
        self._registry = DictionaryRegistry(self._dicts_root)
        self._registry.load()
        self._rebuild_list()

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
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Active Dictionaries (top = highest priority)"))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        self._add_btn = QPushButton("+ Add Dictionary…")
        self._add_btn.clicked.connect(self.add_dict_requested.emit)
        buttons.addWidget(self._add_btn)

        self._reimport_btn = QPushButton("Reimport All")
        self._reimport_btn.clicked.connect(self.reimport_all_requested.emit)
        buttons.addWidget(self._reimport_btn)

        self._up_btn = QPushButton("↑")
        self._up_btn.clicked.connect(lambda: self.move_up(self._list.currentRow()))
        buttons.addWidget(self._up_btn)

        self._down_btn = QPushButton("↓")
        self._down_btn.clicked.connect(lambda: self.move_down(self._list.currentRow()))
        buttons.addWidget(self._down_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(lambda: self.remove(self._list.currentRow()))
        buttons.addWidget(self._remove_btn)

        layout.addLayout(buttons)
        self.add_field("", container)

        # Pitch accent section unchanged
        self.add_section("Pitch Accent")
        self.pitch_accent_selector = FileSelector(
            label="", file_mode=True, placeholder="Select pitch accent file (CSV/TSV)..."
        )
        self.pitch_accent_selector.setToolTip("Path to pitch accent data file (CSV or TSV)")
        self.add_field(
            "Pitch Accent File",
            self.pitch_accent_selector,
            helper="CSV/TSV file with columns: reading, kanji, pattern",
        )
        self.use_pitch_accent_checkbox = QCheckBox("Enable Pitch Accent")
        self.use_pitch_accent_checkbox.setToolTip("Add pitch accent data to Anki cards")
        self.add_field(
            "",
            self.use_pitch_accent_checkbox,
            helper="Enable to look up and add pitch accent patterns to cards",
        )
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
            "Remove dictionary",
            f"Remove '{display}' and delete its files from disk?\n\n"
            "This cannot be undone. You would need to reimport from the source zip.",
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
                "Remove failed",
                "A mining run is in progress. Stop it before removing dictionaries.",
            )
            return

        if dict_dir is not None and dict_dir.exists():
            try:
                _robust_rmtree(dict_dir)
            except OSError as e:
                logger.error("Failed to delete dictionary folder %s: %s", dict_dir, e)
                QMessageBox.warning(
                    self,
                    "Remove failed",
                    f"Could not delete {dict_dir}:\n{e}\n\n" "The dictionary was not removed.",
                )
                return

        self._chain = list(self.get_chain())
        del self._chain[index]
        # Disk state changed — drop cached scan so the next rebuild reflects
        # the missing folder (and a re-add of the same id won't show stale meta).
        self._registry = None
        self._rebuild_list()
        self.chain_changed.emit()
        self.dictionary_removed.emit()

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click a dictionary row to re-import or remove it.

        Reuses the stale-row re-import signals so the handler in settings_tab
        (`_on_reimport_dict_clicked`) drives the import flow regardless of
        entry point. Jisho rows have no menu — the online fallback can't be
        re-imported. Missing meta (dict files vanished from disk) also skip
        because we can't decide between yomitan and jmdict dispatch.
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
        reimport_action = menu.addAction("Re-import…")
        remove_action = menu.addAction("Remove")
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
        self._list.clear()
        # Lazy-construct + cache. refresh_registry() invalidates after an
        # import. Repeated reorder/toggle ticks reuse the same scan.
        if self._registry is None:
            self._registry = DictionaryRegistry(self._dicts_root)
            self._registry.load()
        registry = self._registry
        for entry in self._chain:
            meta: DictMeta | None = None
            if entry.kind == "indexed":
                meta = registry.get(entry.dict_id) if entry.dict_id else None
                display = meta.source_name if meta else (entry.dict_id or "(missing)")
                fmt = meta.format if meta else "missing"
                count = meta.entry_count if meta else 0
            else:
                display = "Jisho (online fallback)"
                fmt = "online"
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
