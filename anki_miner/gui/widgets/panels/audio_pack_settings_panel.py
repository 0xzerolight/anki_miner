"""Audio pack settings panel."""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.gui.widgets.panels.chain_settings_panel_base import (
    ChainSettingsPanelBase,
    _ChainPanelStrings,
    _RegistryView,
)
from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
from anki_miner.utils.i18n import tr_format


# Windows-lock robustness helpers — duplicated across the chain panels (same
# pattern, deliberate copy rather than cross-panel import per audio_packs
# deliberate-decoupling precedent; kept module-local so the panel tests'
# ``shutil`` / ``_robust_rmtree`` monkeypatch seams resolve here).
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


class AudioPackSettingsPanel(ChainSettingsPanelBase):
    """Reorderable chain of expression audio sources."""

    add_pack_requested = pyqtSignal()
    reimport_pack_requested = pyqtSignal(str)
    # Emitted when the user asks to clear JPod101 .miss markers so absent words
    # are re-tried next run. The settings tab owns the actual unlink sweep (it
    # holds the audio_cache path); the panel only surfaces the affordance.
    retry_missing_audio_requested = pyqtSignal()
    # Emitted when any sentence-TTS control (master / provider checkbox)
    # changes; the settings tab persists the three reading_tts_* bools.
    reading_tts_changed = pyqtSignal()

    _ROW_CLASS = _PackRow
    _SCAN_ERROR_LABEL = "Audio pack registry scan failed"
    _REMOVE_ERROR_NOUN = "audio pack index folder"

    def __init__(self, packs_root: Path, parent=None):
        super().__init__("Audio Pack Settings", parent=parent)
        self._packs_root = packs_root
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            remove_failed_title=self.tr("Remove failed"),
            could_not_delete_template=self.tr("Could not delete %1:\n%2\n\nThe audio pack was not removed."),
        )
        self._setup_fields()

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

        # Sentence TTS for reading sources (manga/novels). Deliberately simpler
        # than the chain editor above: fixed 2-provider order (Google first),
        # the checkboxes only select membership; the master flag is the opt-in.
        self.add_section(self.tr("Sentence Audio (Reading Sources)"))
        tts_container = QWidget()
        tts_layout = QVBoxLayout(tts_container)
        tts_layout.setContentsMargins(0, 0, 0, 0)

        tts_blurb = QLabel(
            self.tr(
                "Generate spoken sentence audio for cards mined from manga and books "
                "(these have no source audio). Sentence text is sent to the selected "
                "online services."
            )
        )
        tts_blurb.setWordWrap(True)
        tts_layout.addWidget(tts_blurb)

        self._reading_tts_checkbox = QCheckBox(self.tr("Generate TTS sentence audio"))
        self._reading_tts_checkbox.toggled.connect(self._on_reading_tts_toggled)
        tts_layout.addWidget(self._reading_tts_checkbox)

        provider_row = QVBoxLayout()
        provider_row.setContentsMargins(24, 0, 0, 0)
        self._reading_tts_google = QCheckBox(self.tr("Google Translate TTS (tried first)"))
        self._reading_tts_google.toggled.connect(self._on_reading_tts_provider_toggled)
        provider_row.addWidget(self._reading_tts_google)
        self._reading_tts_papago = QCheckBox(self.tr("Naver Papago (fallback)"))
        self._reading_tts_papago.toggled.connect(self._on_reading_tts_provider_toggled)
        provider_row.addWidget(self._reading_tts_papago)
        tts_layout.addLayout(provider_row)

        # Master ON + both providers OFF is silently inactive at mining time;
        # surface why instead of leaving the user guessing.
        self._reading_tts_hint = QLabel(self.tr("Select at least one service."))
        self._reading_tts_hint.setWordWrap(True)
        self._reading_tts_hint.setVisible(False)
        tts_layout.addWidget(self._reading_tts_hint)

        self.add_field("", tts_container)
        self._sync_reading_tts_enabled_states()
        self.add_stretch()

    def _on_reading_tts_toggled(self, _checked: bool) -> None:
        self._sync_reading_tts_enabled_states()
        self.reading_tts_changed.emit()

    def _on_reading_tts_provider_toggled(self, _checked: bool) -> None:
        self._sync_reading_tts_enabled_states()
        self.reading_tts_changed.emit()

    def _sync_reading_tts_enabled_states(self) -> None:
        """Grey provider boxes when the master is off; show the no-provider hint."""
        master_on = self._reading_tts_checkbox.isChecked()
        self._reading_tts_google.setEnabled(master_on)
        self._reading_tts_papago.setEnabled(master_on)
        both_off = not (self._reading_tts_google.isChecked() or self._reading_tts_papago.isChecked())
        self._reading_tts_hint.setVisible(master_on and both_off)

    def set_reading_tts(self, enabled: bool, google_on: bool, papago_on: bool) -> None:
        """Load the three reading_tts_* config bools into the controls (no signals)."""
        for box, value in (
            (self._reading_tts_checkbox, enabled),
            (self._reading_tts_google, google_on),
            (self._reading_tts_papago, papago_on),
        ):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._sync_reading_tts_enabled_states()

    def get_reading_tts(self) -> tuple[bool, bool, bool]:
        """Return (master enabled, google enabled, papago enabled)."""
        return (
            self._reading_tts_checkbox.isChecked(),
            self._reading_tts_google.isChecked(),
            self._reading_tts_papago.isChecked(),
        )

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
            self._view = _RegistryView(registry_meta.get)
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

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _build_view(self) -> _RegistryView:
        registry = AudioPackRegistry(self._packs_root)
        registry.load()
        return _RegistryView(registry.packs.get)

    def _make_row(self, entry: AudioSourceEntry, view: _RegistryView | None) -> QWidget:
        display, fmt, count, dir_missing = self._describe_entry(entry, view)
        row = _PackRow(entry, display, fmt, count, dir_missing=dir_missing)
        row.toggled.connect(self._on_row_toggled)
        return row

    def _is_protected_entry(self, entry: AudioSourceEntry) -> bool:
        # default built-in online sources can be disabled but not removed
        return entry.kind in ("jpod101", "googletts")

    def _handle_diskless_remove(self, entry: AudioSourceEntry, index: int) -> bool:
        if entry.kind == "pack":
            return False
        # User-added online source (custom): nothing on disk to delete, so drop
        # it directly with no destructive-confirmation dialog.
        new_chain = list(self.get_chain())
        del new_chain[index]
        self._chain = new_chain
        self._rebuild_list()
        self.chain_changed.emit()
        return True

    def _entry_display_name(self, entry: AudioSourceEntry) -> str:
        return self._describe_entry(entry, self._view)[0]

    def _entry_disk_dir(self, entry: AudioSourceEntry) -> Path | None:
        return (self._packs_root / entry.pack_id) if entry.pack_id else None

    def _confirm_remove(self, display: str) -> bool:
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
        return reply == QMessageBox.StandardButton.Yes

    def _rmtree_dir(self, target: Path) -> None:
        _robust_rmtree(target)

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
