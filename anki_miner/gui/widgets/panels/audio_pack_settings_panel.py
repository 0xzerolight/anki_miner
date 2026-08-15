"""Audio pack settings panel."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.utils.config_commit import ConfigCommitResult
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons, primary_action_shortcut
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
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
from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
from anki_miner.utils import robust_fs
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.robust_fs import RmtreeOutcome, robust_rmtree

shutil = robust_fs.shutil


def _robust_rmtree(target: Path) -> RmtreeOutcome:
    """Panel-local seam for post-commit cleanup."""
    return robust_rmtree(target, mode="outcome")


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
        # The URL template is a text field, so Return must stay text entry
        # rather than confirming the dialog (D49); Ctrl+Enter confirms, and only
        # when the entry is actually valid — the same gate the OK button uses.
        disown_default_buttons(self)
        primary_action_shortcut(self, self._accept_if_valid)
        add_min_max_buttons(self)

    def _accept_if_valid(self) -> None:
        """Confirm only when OK would be clickable, so Ctrl+Enter can't bypass validation."""
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None and ok_button.isEnabled():
            self.accept()

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
    add_android_db_requested = pyqtSignal()
    reimport_pack_requested = pyqtSignal(str)
    restore_requested = pyqtSignal()
    # Emitted when the user asks to clear JPod101 .miss markers so absent words
    # are re-tried next run. The settings tab owns the actual unlink sweep (it
    # holds the audio_cache path); the panel only surfaces the affordance.
    retry_missing_audio_requested = pyqtSignal()
    # Emitted when any sentence-TTS control (master / provider checkbox)
    # changes; the settings tab persists the three reading_tts_* bools.
    reading_tts_changed = pyqtSignal()

    ANCHOR_NAMESPACE = "audio"

    _SCAN_ERROR_LABEL = "Audio pack registry scan failed"
    _REMOVE_ERROR_NOUN = "audio pack index folder"

    def __init__(self, packs_root: Path, parent=None):
        super().__init__("Audio Pack Settings", parent=parent)
        self._packs_root = packs_root
        self._release_callback: Callable[[], bool] | None = None
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            retry_label=self.tr("Retry"),
            scan_failed_summary=self.tr("Installed audio packs could not be checked."),
            files_left_summary=self.tr(
                "The audio pack was removed from the chain, but its files were left in place "
                "because the folder could not be proven to belong to Anki Miner."
            ),
            intact_failure_summary=self.tr("%1 could not be removed. Its files are intact — try again."),
            partial_failure_summary=self.tr(
                "%1 was only partly removed. Re-import or repair this audio pack before retrying."
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
        """Wire the pre-remove resource-release hook."""
        self._release_callback = cb

    def request_resource_release(self) -> bool:
        """Ask the app to close cached resource handles before replacement."""
        if self._release_callback is None:
            return True
        return self._release_callback()

    def set_packs_root(self, packs_root: Path) -> None:
        """Update the packs root (e.g. after a config swap) and invalidate caches.

        Mirrors ``DictionarySettingsPanel.set_dicts_root``. Without it a config
        carrying a different ``audio_packs_root`` leaves this panel scanning the
        old root for the rest of the session, and the destructive remove flow
        resolves ``resolve_managed_slot`` against the wrong directory. This
        panel has no storage-folder selector, so there is nothing to re-sync.
        """
        if packs_root == self._packs_root:
            # _load_config runs after every auto-save commit that touches a
            # non-external field, and the root is the same almost every time.
            # Rescanning anyway would flash a "Loading…" placeholder and take a
            # hold_mutation("scan") token (disabling Add) on every settings edit.
            return
        self._packs_root = packs_root
        self._view = None
        # Root changed → cached scan is stale; rescan off-thread (no-op before
        # first show, where _scanned is still False).
        self._scan_and_render_async()

    def _setup_fields(self) -> None:
        self.add_section(self.tr("Active Audio Sources"))

        # Cache-hygiene: clear the record of words JPod101 had no audio for so
        # they are re-requested on the next run (replaces deleting the cache dir
        # by hand). The unlink sweep is dispatched by the settings tab.
        self._retry_missing_btn = ModernButton(self.tr("Retry missing audio"), variant="secondary")
        self._retry_missing_btn.setToolTip(self.tr("Re-try words JapanesePod101 had no audio for on the next run"))
        self._retry_missing_btn.clicked.connect(self.retry_missing_audio_requested.emit)

        self._restore_btn = ModernButton(self.tr("Restore from Disk"), variant="secondary")
        self._restore_btn.setToolTip(
            self.tr(
                "Re-add audio packs found in the storage folder that aren't in the list above. No re-import needed."
            )
        )
        self._restore_btn.clicked.connect(self.restore_requested.emit)

        container = self._build_chain_container(
            ChainListLabels(
                explanation=self.tr(
                    "Sources are tried top to bottom — the first one that has audio " "for a word wins."
                ),
                add=self.tr("Add audio source…"),
                remove=self.tr("Remove audio source"),
                remove_tooltip=self.tr("Remove the selected audio source"),
                move_up=self.tr("Move up"),
                move_up_tooltip=self.tr("Move up in priority"),
                move_down=self.tr("Move down"),
                move_down_tooltip=self.tr("Move down in priority"),
            ),
            extra_actions=(self._restore_btn, self._retry_missing_btn),
        )
        # Two ways in, one control: a second primary button beside the first
        # would say the app has two equally-important task actions here (D41).
        self._add_menu = QMenu(self._add_btn)
        self._add_pack_action = QAction(self.tr("Audio Pack…"), self._add_menu)
        self._add_pack_action.triggered.connect(lambda _checked=False: self.add_pack_requested.emit())
        self._add_menu.addAction(self._add_pack_action)
        self._add_android_db_action = QAction(self.tr("Android Audio Database…"), self._add_menu)
        self._add_android_db_action.triggered.connect(lambda _checked=False: self.add_android_db_requested.emit())
        self._add_menu.addAction(self._add_android_db_action)
        self._add_online_action = QAction(self.tr("Online Source…"), self._add_menu)
        self._add_online_action.triggered.connect(lambda _checked=False: self._on_add_online_source())
        self._add_menu.addAction(self._add_online_action)
        self._add_btn.setMenu(self._add_menu)
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
                self._add_pack_action.text(),
                self._add_android_db_action.text(),
                self._add_online_action.text(),
                self._restore_btn.text(),
                self._retry_missing_btn.text(),
            ),
        )

        # Sentence TTS for reading sources (manga/novels). Deliberately simpler
        # than the chain editor above: fixed 2-provider order (Google first),
        # the checkboxes only select membership; the master flag is the opt-in.
        self.add_section(self.tr("Sentence Audio (Reading Sources)"))
        tts_container = QWidget()
        tts_layout = QVBoxLayout(tts_container)
        tts_layout.setContentsMargins(0, 0, 0, 0)

        tts_blurb = QLabel(
            self.tr(
                "Add spoken audio to cards from manga and books, which have no source "
                "audio. Sentence text is sent to the selected online services."
            )
        )
        tts_blurb.setObjectName("helper-text")
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

        # The master toggle and its two providers are one logical setting; index
        # all three captions so searching a provider name still lands here.
        self.add_field(
            "",
            tts_container,
            anchor="reading_tts",
            anchor_focus=self._reading_tts_checkbox,
            anchor_text=lambda: (
                self._reading_tts_checkbox.text(),
                self._reading_tts_google.text(),
                self._reading_tts_papago.text(),
                tts_blurb.text(),
            ),
        )
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

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        self._add_btn.setEnabled(enabled)
        self._restore_btn.setEnabled(enabled)

    def set_chain(
        self,
        chain: tuple[AudioSourceEntry, ...],
        registry_meta: dict[str, AudioPackMeta] | None = None,
    ) -> None:
        self._chain = list(chain)
        if registry_meta is not None:
            # Caller pre-supplied meta; use it directly, no disk scan needed.
            self._view = _RegistryView(registry_meta.get)
        self._rebuild_list()

    def _entry_with_enabled(self, entry: AudioSourceEntry, enabled: bool) -> AudioSourceEntry:
        return AudioSourceEntry(kind=entry.kind, pack_id=entry.pack_id, url=entry.url, enabled=enabled)

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
        if not self.prepare_for_mutation():
            return
        token = self.hold_mutation("add-online-source")
        try:
            dialog = _AddSourceDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.add_source_entry(
                AudioSourceEntry(
                    kind=dialog.selected_kind(),  # type: ignore[arg-type]
                    url=dialog.url_value(),
                    enabled=True,
                )
            )
        finally:
            self.release(token)

    def _describe_entry(
        self, entry: AudioSourceEntry, view: _RegistryView | None
    ) -> tuple[str, str, int | None, bool, bool]:
        """Return display, format, count, missing-dir, and stale-schema state.

        The count is ``None`` when there is no count to state — an online source
        has no entries to count, and a pack the registry knows nothing about has
        none that can be trusted. Zero is a fact; ``None`` is its absence.
        """
        if entry.kind == "pack":
            meta = view.get(entry.pack_id) if (view is not None and entry.pack_id) else None
            return (
                meta.source if meta else (entry.pack_id or "(missing)"),
                meta.format if meta else "",
                meta.entry_count if meta else None,
                meta is not None and not meta.source_available,
                meta is not None and not meta.schema_ok,
            )
        if entry.kind == "googletts":
            return self.tr("Google Translate (synthetic TTS)"), "online", None, False, False
        if entry.kind in ("custom", "custom_json"):
            label = self.tr("Custom JSON") if entry.kind == "custom_json" else self.tr("Custom URL")
            return (f"{label}: {entry.url}" if entry.url else label), "custom", None, False, False
        # jpod101 (built-in online)
        return self.tr("JapanesePod101 (online)"), "online", None, False, False

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _build_view(self) -> _RegistryView:
        registry = AudioPackRegistry(self._packs_root)
        registry.load()
        return _RegistryView(registry.packs.get)

    def _row_spec(self, entry: AudioSourceEntry, view: _RegistryView | None) -> ChainRowSpec:
        display, fmt, count, dir_missing, schema_stale = self._describe_entry(entry, view)
        pack_missing = (
            entry.kind == "pack" and view is not None and (entry.pack_id is None or view.get(entry.pack_id) is None)
        )
        metadata: tuple[str, ...] = (fmt,) if fmt else ()
        if count is not None:
            metadata = (*metadata, tr_format(self.tr("%1 entries"), f"{count:,}"))
        if schema_stale:
            warning = self.tr("⚠ re-import required (app upgrade)")
        elif pack_missing:
            warning = self.tr("⚠ pack missing — re-import")
        elif dir_missing:
            warning = self.tr("⚠ folder missing — re-import")
        else:
            warning = ""
        return ChainRowSpec(
            entry=entry,
            title=display,
            metadata=metadata,
            enabled_text=self.tr("Enabled"),
            enabled_accessible_text=tr_format(self.tr("Enable %1"), display),
            enabled_tooltip=tr_format(self.tr("Enable or disable %1"), display),
            warning=warning,
            repair_text=self.tr("Re-import") if pack_missing and entry.pack_id else "",
        )

    def _connect_row_repair(self, row: ChainSourceRow) -> None:
        if row.repair_button is None or not row.entry.pack_id:
            return
        row.repair_button.clicked.connect(
            lambda _checked=False, pack_id=row.entry.pack_id: self.reimport_pack_requested.emit(pack_id)
        )

    def _is_protected_entry(self, entry: AudioSourceEntry) -> bool:
        # default built-in online sources can be disabled but not removed
        return entry.kind in ("jpod101", "googletts")

    def _handle_diskless_remove(self, entry: AudioSourceEntry, index: int) -> bool:
        if entry.kind == "pack":
            return False
        # User-added online source (custom): nothing on disk to delete, but the
        # chain still crosses the same durable commit boundary as a pack.
        display = self._entry_display_name(entry)
        chain = self.get_chain()
        new_chain = (*chain[:index], *chain[index + 1 :])
        if self._remove_chain_commit is None:
            self._chain = list(new_chain)
            self.chain_changed.emit()
            result = ConfigCommitResult.committed()
        else:
            try:
                result = self._remove_chain_commit(new_chain)
            except Exception as error:
                result = ConfigCommitResult.pre_save_failure(error)
            if result.persisted:
                self._chain = list(new_chain)
        if not result.persisted:
            msg = self._error_text(result)
            self.show_screen_issue(
                ScreenIssue(
                    summary=tr_format(
                        self.tr("Removal of %1 was not saved. The source is unchanged — try again."),
                        display,
                    ),
                    details=f"{display}: {msg}",
                )
            )
            return True
        self._rebuild_list()
        if not result.refreshed:
            self._warn_post_save_failure(display, self._error_text(result))
        return True

    def _entry_display_name(self, entry: AudioSourceEntry) -> str:
        return self._describe_entry(entry, self._view)[0]

    def _entry_disk_dir(self, entry: AudioSourceEntry) -> Path | None:
        if not entry.pack_id:
            return None
        try:
            return resolve_managed_slot(self._packs_root, entry.pack_id)
        except ValueError:
            return None

    def _owns_entry_disk_dir(self, entry: AudioSourceEntry, target: Path) -> bool:
        pack_id = entry.pack_id
        return pack_id is not None and prove_owned_slot(target.parent, pack_id, "audio")

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

    def _confirm_chain_only_remove(self, display: str) -> bool:
        reply = QMessageBox.question(
            self,
            self.tr("Remove audio pack"),
            tr_format(
                self.tr(
                    "Remove '%1' from the audio chain?\n\nIndex files on disk will be left untouched because the folder could not be proven to belong to Anki Miner."
                ),
                display,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _acquire_release_for_remove(self) -> bool:
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
        """Right-click a pack row to re-import it.

        Built-in online rows (jpod101, googletts) have no menu — they can't be re-imported.
        """
        if self._scan_in_flight or self.has_active_mutation():
            return
        item = self._list.itemAt(pos)
        if item is None:
            return
        index = self._list.row(item)
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if entry.kind in ("jpod101", "googletts") or entry.pack_id is None:
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
