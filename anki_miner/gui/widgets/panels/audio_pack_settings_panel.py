"""Audio pack settings panel."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


# Windows-lock robustness helpers — duplicated from dictionary_settings_panel.py
# (same pattern, deliberate copy rather than cross-panel import per audio_packs
# deliberate-decoupling precedent).
def _on_rmtree_error(func, path, _exc_info):
    """rmtree onerror handler: clear the read-only bit then retry once.

    Windows refuses to delete read-only files; sqlite-backed index dirs sometimes
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
    handles still being released by GC. The retry loop absorbs the second case
    best-effort; final failure surfaces to the caller as the last OSError so the
    UI can show the same dialog as before.
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


class _RegistryView:
    """Uniform meta-lookup interface used by the panel internally.

    Wraps either a live ``AudioPackRegistry`` (packs-dict lookup) or a
    pre-built ``dict[str, AudioPackMeta]`` injected by callers of
    ``set_chain(registry_meta=...)``.  Panel code always calls ``.get()``
    and ``.load()`` through this shim so neither branch leaks into the
    main class body.
    """

    def __init__(self, source: AudioPackRegistry | dict[str, AudioPackMeta]) -> None:
        self._source = source

    def load(self) -> None:
        if isinstance(self._source, AudioPackRegistry):
            self._source.load()

    def get(self, pack_id: str) -> AudioPackMeta | None:
        if isinstance(self._source, AudioPackRegistry):
            return self._source.packs.get(pack_id)
        return self._source.get(pack_id)


class _PackRow(QWidget):
    """One row in the chain list: checkbox + label + format badge + count + missing badge."""

    toggled = pyqtSignal()

    def __init__(
        self,
        entry: AudioSourceEntry,
        display_name: str,
        format_label: str,
        count: int,
        *,
        dir_missing: bool = False,
    ):
        super().__init__()
        self.entry = entry
        self.dir_missing = dir_missing
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

        if dir_missing:
            missing_label = QLabel(self.tr("⚠ folder missing — re-import"))
            missing_label.setStyleSheet("color: #d97706; font-size: 10px;")
            layout.addWidget(missing_label)

    def get_enabled(self) -> bool:
        return self.checkbox.isChecked()


class _AddSourceDialog(QDialog):
    """Prompt for a new online audio source: a kind + a URL template.

    Both kinds (``custom``/``custom_json``) require a URL template.
    """

    # (kind, English label). Labels go through self.tr at construction.
    _KINDS: list[tuple[str, str]] = [
        ("custom", "Custom URL (local-audio-yomichan / any audio URL)"),
        ("custom_json", "Custom JSON list (audioSourceList)"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Add Audio Source"))
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(self.tr("Source type:")))
        self._kind_combo = QComboBox()
        for kind, label in self._KINDS:
            self._kind_combo.addItem(self.tr(label), kind)
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addWidget(self._kind_combo)

        self._url_label = QLabel(self.tr("URL template (use {term} and {reading}):"))
        layout.addWidget(self._url_label)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://localhost:5050/?term={term}&reading={reading}")
        self._url_edit.textChanged.connect(self._update_ok_enabled)
        layout.addWidget(self._url_edit)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._on_kind_changed()
        add_min_max_buttons(self)

    def selected_kind(self) -> str:
        return str(self._kind_combo.currentData())

    def url_value(self) -> str | None:
        """The entered URL for custom kinds, else None."""
        if self.selected_kind() in ("custom", "custom_json"):
            return self._url_edit.text().strip()
        return None

    def _is_custom_kind(self) -> bool:
        return self.selected_kind() in ("custom", "custom_json")

    def _on_kind_changed(self) -> None:
        custom = self._is_custom_kind()
        self._url_label.setVisible(custom)
        self._url_edit.setVisible(custom)
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is None:
            return
        # Custom kinds need a non-empty URL.
        ok_button.setEnabled(bool(self._url_edit.text().strip()) if self._is_custom_kind() else True)


class AudioPackSettingsPanel(FormPanel):
    """Reorderable chain of expression audio sources."""

    add_pack_requested = pyqtSignal()
    reimport_pack_requested = pyqtSignal(str)
    chain_changed = pyqtSignal()
    # Emitted when the user asks to clear JPod101 .miss markers so absent words
    # are re-tried next run. The settings tab owns the actual unlink sweep (it
    # holds the audio_cache path); the panel only surfaces the affordance.
    retry_missing_audio_requested = pyqtSignal()
    # Emitted once a pack has been successfully removed from both the in-memory
    # chain and disk. Distinct from ``chain_changed`` so the settings tab can
    # persist the new chain immediately — a delete is destructive and
    # asymmetric with reorder/toggle.
    pack_removed = pyqtSignal()

    def __init__(self, packs_root: Path, parent=None):
        super().__init__("Audio Pack Settings", parent=parent)
        self._packs_root = packs_root
        self._chain: list[AudioSourceEntry] = []
        # Cached registry view; refreshed on demand instead of per UI tick.
        self._view: _RegistryView | None = None
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

        Defers AudioPackRegistry.load() off the app startup / first-paint
        path (OVH-053).  Subsequent showEvent calls are no-ops; explicit
        refreshes (refresh_registry, set_chain with pre-supplied meta)
        call _rebuild_list directly and bypass this guard.
        """
        super().showEvent(event)
        if not self._scanned:
            self._scanned = True
            self._scan_and_render_async()

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
        placeholder shows while ``AudioPackRegistry.load()`` runs on a worker
        thread.
        """
        if self._view is not None or not self._scanned:
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

        packs_root = self._packs_root

        def _scan() -> _RegistryView:
            registry = AudioPackRegistry(packs_root)
            registry.load()
            return _RegistryView(registry)

        run_off_thread(self, _scan, self._on_scan_done, self._on_scan_error)

    def _on_scan_done(self, view: object) -> None:
        self._scan_in_flight = False
        self._view = cast("_RegistryView", view)
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("Audio pack registry scan failed: %s", msg)
        self._rebuild_list()
        self._redispatch_pending_scan()

    def _redispatch_pending_scan(self) -> None:
        """Re-run one scan if a rescan was requested while one was in flight.

        Drops the now-stale cached view so the trailing scan reads the latest
        disk state. Single-shot: the flag is cleared before dispatch, so only
        rescans requested *during* this dispatch can queue another.
        """
        if not self._rescan_pending:
            return
        self._rescan_pending = False
        self._view = None
        self._scan_and_render_async()

    def _show_loading_placeholder(self) -> None:
        """Render a single disabled 'Loading…' row while a scan is in flight."""
        # No real rows during the scan: disable the reorder/remove controls
        # explicitly (they act on currentRow()); _rebuild_list re-enables them.
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

    def _setup_fields(self) -> None:
        self.add_section(self.tr("Active Audio Sources"))
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel(self.tr("Top entry is tried first.")))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_row_context_menu)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        self._add_btn = QPushButton(self.tr("+ Add Audio Pack…"))
        self._add_btn.clicked.connect(self.add_pack_requested.emit)
        buttons.addWidget(self._add_btn)

        self._add_online_btn = QPushButton(self.tr("+ Add Online Source…"))
        self._add_online_btn.setToolTip(self.tr("Add a custom audio URL source"))
        self._add_online_btn.clicked.connect(self._on_add_online_source)
        buttons.addWidget(self._add_online_btn)

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

        # Cache-hygiene: clear the record of words JPod101 had no audio for so
        # they are re-requested on the next run (replaces deleting the cache dir
        # by hand). The unlink sweep is dispatched by the settings tab.
        retry_row = QHBoxLayout()
        self._retry_missing_btn = QPushButton(self.tr("Retry missing expression audio"))
        self._retry_missing_btn.setToolTip(self.tr("Re-try words JapanesePod101 had no audio for on the next run"))
        self._retry_missing_btn.clicked.connect(self.retry_missing_audio_requested.emit)
        retry_row.addWidget(self._retry_missing_btn)
        retry_row.addStretch()
        layout.addLayout(retry_row)

        self.add_field("", container)
        self.add_stretch()

    def set_retry_missing_enabled(self, enabled: bool) -> None:
        """Enable/disable the retry button while its off-thread sweep runs."""
        self._retry_missing_btn.setEnabled(enabled)

    def set_chain(
        self,
        chain: tuple[AudioSourceEntry, ...],
        registry_meta: dict[str, AudioPackMeta] | None = None,
    ) -> None:
        self._chain = list(chain)
        if registry_meta is not None:
            # Caller pre-supplied meta; use it directly, no disk scan needed.
            self._view = _RegistryView(registry_meta)
        else:
            # Invalidate so _rebuild_list will scan on demand.
            self._view = None
        self._rebuild_list()

    def get_chain(self) -> tuple[AudioSourceEntry, ...]:
        out: list[AudioSourceEntry] = []
        for i, entry in enumerate(self._chain):
            row = self._row_widget(i)
            enabled = row.get_enabled() if row is not None else entry.enabled
            out.append(AudioSourceEntry(kind=entry.kind, pack_id=entry.pack_id, url=entry.url, enabled=enabled))
        return tuple(out)

    def add_source_entry(self, entry: AudioSourceEntry) -> None:
        """Append an online audio source to the chain and persist immediately.

        Reads the current enabled/order state off the row widgets first (via
        ``get_chain``) so an in-progress toggle isn't lost, appends *entry*, then
        emits ``chain_changed`` which the settings tab persists.
        """
        self._chain = [*self.get_chain(), entry]
        self._rebuild_list()
        self.chain_changed.emit()

    def _on_add_online_source(self) -> None:
        """Open the Add-Source dialog and append the chosen custom entry."""
        dialog = _AddSourceDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.add_source_entry(
            AudioSourceEntry(kind=dialog.selected_kind(), url=dialog.url_value(), enabled=True)  # type: ignore[arg-type]
        )

    def _describe_entry(self, entry: AudioSourceEntry, view: _RegistryView | None) -> tuple[str, str, int, bool]:
        """Return ``(display, format_label, entry_count, dir_missing)`` for a row."""
        if entry.kind == "pack":
            meta = view.get(entry.pack_id) if (view is not None and entry.pack_id) else None
            return (
                meta.source if meta else (entry.pack_id or "(missing)"),
                meta.format if meta else "",
                meta.entry_count if meta else 0,
                meta is not None and not meta.pack_dir_exists,
            )
        if entry.kind == "googletts":
            return self.tr("Google Translate (synthetic TTS)"), "online", 0, False
        if entry.kind in ("custom", "custom_json"):
            label = self.tr("Custom JSON") if entry.kind == "custom_json" else self.tr("Custom URL")
            return (f"{label}: {entry.url}" if entry.url else label), "custom", 0, False
        # jpod101 (built-in online)
        return self.tr("JapanesePod101 (online)"), "online", 0, False

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
        if entry.kind in ("jpod101", "googletts"):
            return  # default built-in online sources can be disabled but not removed

        if entry.kind != "pack":
            # User-added online source (custom): nothing on disk to delete,
            # so drop it directly with no destructive-confirmation dialog.
            new_chain = list(self.get_chain())
            del new_chain[index]
            self._chain = new_chain
            self._rebuild_list()
            self.chain_changed.emit()
            return

        pack_id = entry.pack_id
        display = self._describe_entry(entry, self._view)[0]
        pack_index_dir = (self._packs_root / pack_id) if pack_id else None

        reply = QMessageBox.question(
            self,
            self.tr("Remove audio pack"),
            tr_format(
                self.tr(
                    "Remove '%1' from the audio chain?\n\nOnly the index files are deleted — your original audio files are untouched.\nThis cannot be undone. You would need to re-import to use this pack again."
                ),
                display,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Capture the post-remove chain on the GUI thread (reads row widgets)
        # BEFORE dispatching the disk delete off-thread.
        new_chain = list(self.get_chain())
        del new_chain[index]

        if pack_index_dir is None or not pack_index_dir.exists():
            self._finalize_remove(new_chain)
            return

        # The rmtree (sleep-backed retry loop) runs off the GUI thread; the
        # Remove button + list are disabled while it runs.
        self._remove_btn.setEnabled(False)
        self._list.setEnabled(False)
        target = pack_index_dir

        def _delete() -> None:
            _robust_rmtree(target)

        run_off_thread(
            self,
            _delete,
            lambda _r: self._on_remove_done(new_chain),
            lambda msg: self._on_remove_error(target, msg),
        )

    def _on_remove_done(self, new_chain: list[AudioSourceEntry]) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        self._finalize_remove(new_chain)

    def _on_remove_error(self, pack_index_dir: Path, msg: str) -> None:
        self._remove_btn.setEnabled(True)
        self._list.setEnabled(True)
        logger.error("Failed to delete audio pack index folder %s: %s", pack_index_dir, msg)
        QMessageBox.warning(
            self,
            self.tr("Remove failed"),
            tr_format(self.tr("Could not delete %1:\n%2\n\nThe audio pack was not removed."), pack_index_dir, msg),
        )

    def _finalize_remove(self, new_chain: list[AudioSourceEntry]) -> None:
        """Commit the chain mutation + rescan after a successful disk delete."""
        self._chain = new_chain
        # Disk state changed — drop cached view so next render rescans.
        self._view = None
        self._scan_and_render_async()
        self.chain_changed.emit()
        self.pack_removed.emit()

    def _on_row_context_menu(self, pos: QPoint) -> None:
        """Right-click a pack row to re-import it.

        Built-in online rows (jpod101, googletts) have no menu — they can't be re-imported.
        """
        item = self._list.itemAt(pos)
        if item is None:
            return
        index = self._list.row(item)
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if entry.kind in ("jpod101", "googletts") or entry.pack_id is None:
            return
        # _view is always set after _rebuild_list; guard is belt-and-suspenders.
        meta = self._view.get(entry.pack_id) if self._view is not None else None
        if meta is None:
            return

        menu = QMenu(self._list)
        reimport_action = menu.addAction(self.tr("Re-import…"))
        remove_action = menu.addAction(self.tr("Remove"))
        viewport = self._list.viewport()
        global_pos = viewport.mapToGlobal(pos) if viewport is not None else self._list.mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if chosen is reimport_action:
            self.reimport_pack_requested.emit(entry.pack_id)
        elif chosen is remove_action:
            self.remove(index)

    def _row_widget(self, index: int) -> _PackRow | None:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, _PackRow) else None

    def _rebuild_list(self) -> None:
        self._list.setUpdatesEnabled(False)
        try:
            # clear() destroys the previous row widgets (and their signal connections), so there is no duplicate handler risk.
            self._list.clear()
            # Render-only: the disk scan is owned by _scan_and_render_async,
            # which runs AudioPackRegistry.load() off the GUI thread and only
            # then calls back here with self._view populated. Before first show
            # (OVH-053) self._view is None and rows render without pack metadata
            # — a safe no-content state since the list is never visible until
            # the Settings tab is opened.
            view = self._view  # may be None before first show / scan
            for entry in self._chain:
                display, fmt, count, dir_missing = self._describe_entry(entry, view)
                row = _PackRow(entry, display, fmt, count, dir_missing=dir_missing)
                row.toggled.connect(self.chain_changed.emit)
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
            # Real rows are back: restore the controls the placeholder disabled.
            self._set_reorder_controls_enabled(True)
