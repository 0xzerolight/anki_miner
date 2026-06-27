"""Frequency sources settings panel.

Reorderable chain of additive frequency sources, mirroring
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`.
Replaces the old single-file "Frequency List" picker: the user adds, reorders,
enables/disables, and removes multiple frequency rank lists, each backed by a
per-source ``index.sqlite`` under ``config.freqs_root/<source_id>/``.

A global "Enable Frequency Data" checkbox gates the whole feature (the
service factory still reads ``config.use_frequency_data``); its state is exposed
to the settings tab via :attr:`use_frequency_checkbox`.
"""

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

from anki_miner.config import FreqEntry
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


# Windows-lock robustness helpers — duplicated from audio_pack_settings_panel.py
# (same pattern, deliberate copy rather than cross-panel import per the
# panels' deliberate-decoupling precedent).
def _on_rmtree_error(func, path, _exc_info):
    """rmtree onerror handler: clear the read-only bit then retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def _robust_rmtree(target: Path, *, retries: int = 3, delay_s: float = 0.1) -> None:
    """rmtree with Windows-aware retry (read-only attrs + transient locks)."""
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


class _RegistryView:
    """Uniform meta-lookup interface used by the panel internally.

    Wraps either a live ``FrequencySourceRegistry`` or a pre-built
    ``dict[str, FreqSourceMeta]`` injected by callers of
    ``set_chain(registry_meta=...)`` (tests). Panel code always calls
    ``.get()`` / ``.load()`` through this shim.
    """

    def __init__(self, source: FrequencySourceRegistry | dict[str, FreqSourceMeta]) -> None:
        self._source = source

    def load(self) -> None:
        if isinstance(self._source, FrequencySourceRegistry):
            self._source.load()

    def get(self, source_id: str) -> FreqSourceMeta | None:
        if isinstance(self._source, FrequencySourceRegistry):
            return self._source.get(source_id)
        return self._source.get(source_id)


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
    ):
        super().__init__()
        self.entry = entry
        self.missing = missing
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(entry.enabled)
        self.checkbox.stateChanged.connect(lambda _s: self.toggled.emit())
        layout.addWidget(self.checkbox)

        name_label = QLabel(display_name)
        layout.addWidget(name_label, 1)

        if format_label:
            badge = QLabel(format_label)
            badge.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(badge)

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


class FrequencySettingsPanel(FormPanel):
    """Reorderable chain of additive frequency sources."""

    add_source_requested = pyqtSignal()
    reimport_source_requested = pyqtSignal(str)
    chain_changed = pyqtSignal()
    # Emitted once a source has been successfully removed from both the
    # in-memory chain and disk. Distinct from ``chain_changed`` so the settings
    # tab can persist the new chain immediately — a delete is destructive and
    # asymmetric with reorder/toggle.
    source_removed = pyqtSignal()

    def __init__(self, freqs_root: Path, parent=None):
        super().__init__("Frequency Sources", parent=parent)
        self._freqs_root = freqs_root
        self._chain: list[FreqEntry] = []
        # Cached registry view; refreshed on demand instead of per UI tick.
        self._view: _RegistryView | None = None
        # Guard: registry scan deferred to first showEvent so it does not run
        # on the GUI thread before the window paints (mirrors audio/dict).
        self._scanned: bool = False
        # Set while an off-thread registry scan is running so overlapping
        # scans / removes don't stack (OVH disk-scan-off-thread).
        self._scan_in_flight: bool = False
        # Optional callback invoked before destructive remove to ask the rest
        # of the app to close cached sqlite handles. Returns True on success.
        # Defaults to no-op (frequency providers are rebuilt per run, not held
        # open like the definition service), but kept for API parity + Windows
        # robustness should that ever change.
        self._release_callback: Callable[[], bool] | None = None
        self._setup_fields()

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        """Trigger the first registry scan when the panel becomes visible."""
        super().showEvent(event)
        if not self._scanned:
            self._scanned = True
            self._scan_and_render_async()

    def set_release_callback(self, cb: Callable[[], bool] | None) -> None:
        """Wire the pre-remove resource-release hook (see ``remove()``)."""
        self._release_callback = cb

    def refresh_registry(self) -> None:
        """Force a registry rescan. Call after an import finishes.

        The disk scan runs off the GUI thread (OVH disk-scan-off-thread).
        """
        self._view = None
        self._scanned = True
        self._scan_and_render_async()

    def _scan_and_render_async(self) -> None:
        """Scan the registry off-thread (if not cached) then render the rows.

        When ``_view`` is already cached (e.g. ``set_chain(registry_meta=...)``)
        this renders synchronously with no worker. Otherwise a ``Loading…``
        placeholder shows while ``FrequencySourceRegistry.load()`` runs on a
        worker thread.
        """
        if self._view is not None or not self._scanned:
            self._rebuild_list()
            return
        if self._scan_in_flight:
            return
        self._scan_in_flight = True
        self._show_loading_placeholder()

        freqs_root = self._freqs_root

        def _scan() -> _RegistryView:
            registry = FrequencySourceRegistry(freqs_root)
            registry.load()
            return _RegistryView(registry)

        run_off_thread(self, _scan, self._on_scan_done, self._on_scan_error)

    def _on_scan_done(self, view: object) -> None:
        self._scan_in_flight = False
        self._view = cast("_RegistryView", view)
        self._rebuild_list()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("Frequency registry scan failed: %s", msg)
        self._rebuild_list()

    def _show_loading_placeholder(self) -> None:
        """Render a single disabled 'Loading…' row while a scan is in flight."""
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            placeholder = QListWidgetItem(self.tr("Loading…"))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        finally:
            self._list.setUpdatesEnabled(True)

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
        self._up_btn.setToolTip(self.tr("Move up (breaks rank ties first)"))
        self._up_btn.clicked.connect(lambda: self.move_up(self._list.currentRow()))
        buttons.addWidget(self._up_btn)

        self._down_btn = QPushButton("↓")
        self._down_btn.setToolTip(self.tr("Move down"))
        self._down_btn.clicked.connect(lambda: self.move_down(self._list.currentRow()))
        buttons.addWidget(self._down_btn)

        self._remove_btn = QPushButton(self.tr("Remove"))
        self._remove_btn.clicked.connect(lambda: self.remove(self._list.currentRow()))
        buttons.addWidget(self._remove_btn)

        layout.addLayout(buttons)
        self.add_field("", container)

        # Global feature toggle. Kept alongside the chain because the service
        # factory still gates on config.use_frequency_data; the settings tab
        # reads this checkbox into that field on Save.
        self.use_frequency_checkbox = QCheckBox(self.tr("Enable Frequency Data"))
        self.add_field(
            "",
            self.use_frequency_checkbox,
            helper=self.tr("Enable to display word frequency rank on cards."),
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
            self._view = _RegistryView(registry_meta)
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

    def move_up(self, index: int) -> None:
        if index <= 0 or index >= len(self._chain):
            return
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

        source_id = entry.source_id
        meta = self._view.get(source_id) if (self._view is not None and source_id) else None
        display = meta.source_name if meta else (source_id or "(missing)")
        source_dir = (self._freqs_root / source_id) if source_id else None

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
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Drop any cached sqlite handles before rmtree (Windows lock safety).
        # No-op unless a release callback is wired.
        if self._release_callback is not None and not self._release_callback():
            QMessageBox.warning(
                self,
                self.tr("Remove failed"),
                self.tr("A mining run is in progress. Stop it before removing frequency sources."),
            )
            return

        # Capture the post-remove chain on the GUI thread (reads row widgets)
        # BEFORE dispatching the disk delete off-thread.
        new_chain = list(self.get_chain())
        del new_chain[index]

        if source_dir is None or not source_dir.exists():
            self._finalize_remove(new_chain)
            return

        # The rmtree (sleep-backed retry loop) runs off the GUI thread; the
        # Remove button + list are disabled while it runs.
        self._remove_btn.setEnabled(False)
        self._list.setEnabled(False)
        target = source_dir

        def _delete() -> None:
            _robust_rmtree(target)

        run_off_thread(
            self,
            _delete,
            lambda _r: self._on_remove_done(new_chain),
            lambda msg: self._on_remove_error(target, msg),
        )

    def _on_remove_done(self, new_chain: list[FreqEntry]) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        self._finalize_remove(new_chain)

    def _on_remove_error(self, source_dir: Path, msg: str) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        logger.error("Failed to delete frequency source folder %s: %s", source_dir, msg)
        QMessageBox.warning(
            self,
            self.tr("Remove failed"),
            tr_format(self.tr("Could not delete %1:\n%2\n\nThe frequency source was not removed."), source_dir, msg),
        )

    def _finalize_remove(self, new_chain: list[FreqEntry]) -> None:
        """Commit the chain mutation + rescan after a successful disk delete."""
        self._chain = new_chain
        # Disk state changed — drop cached view so next render rescans.
        self._view = None
        self._scan_and_render_async()
        self.chain_changed.emit()
        self.source_removed.emit()

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click a source row to re-import or remove it."""
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

    def _row_widget(self, index: int) -> _FreqRow | None:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, _FreqRow) else None

    def _rebuild_list(self) -> None:
        self._list.setUpdatesEnabled(False)
        try:
            # clear() destroys the previous row widgets (and their signal
            # connections), so there is no duplicate-handler risk.
            self._list.clear()
            # Render-only: the disk scan is owned by _scan_and_render_async,
            # which runs FrequencySourceRegistry.load() off the GUI thread and
            # only then calls back here with self._view populated. Before first
            # show self._view is None and rows render without source metadata —
            # a safe no-content state since the list is never visible until the
            # Settings tab is opened.
            view = self._view  # may be None before first show / scan
            for entry in self._chain:
                meta = view.get(entry.source_id) if (view is not None and entry.source_id) else None
                # A chain entry whose source folder is gone (or schema-mismatched
                # so build_sources would drop it) is "missing" — prompt re-import.
                missing = view is not None and (meta is None or not meta.schema_ok)
                display = meta.source_name if meta else (entry.source_id or "(missing)")
                fmt = _FORMAT_LABELS.get(meta.format, meta.format) if meta else ""
                count = meta.entry_count if meta else 0
                row = _FreqRow(entry, display, fmt, count, missing=missing)
                row.toggled.connect(self.chain_changed.emit)
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
