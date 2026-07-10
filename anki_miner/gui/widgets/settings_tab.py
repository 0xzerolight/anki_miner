"""Settings tab with category organization using extracted panels."""

import dataclasses
import os
import re
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry, FreqEntry
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.controllers.frequency_import_flow import FrequencyImportFlow
from anki_miner.gui.controllers.zip_import_flow import YomitanCsvLabels, ZipImportFlow
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    AudioPackSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    FrequencySettingsPanel,
    MediaSettingsPanel,
    UISettingsPanel,
    YouTubeSettingsPanel,
)
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel
from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker
from anki_miner.services.expression_audio_fetcher import purge_miss_markers
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent import import_yomitan_pitch_zip
from anki_miner.utils.i18n import tr_format

# Debounce for the Settings auto-save: a burst of edits coalesces into one
# commit this many ms after the last change. Long enough to not commit per
# keystroke, short enough that settings apply near-immediately.
_AUTOSAVE_DEBOUNCE_MS = 1000


@runtime_checkable
class _SavePathPanel(Protocol):
    """Structural interface for panels that participate in the Save round-trip.

    Implemented by :class:`AnkiSettingsPanel`, :class:`MediaSettingsPanel`,
    :class:`FilteringSettingsPanel`, and :class:`YouTubeSettingsPanel`.
    """

    def load_from_config(self, config: AnkiMinerConfig) -> None: ...

    def contribute(self, config: AnkiMinerConfig) -> AnkiMinerConfig: ...


class SettingsTab(QWidget):
    """Settings tab with category organization.

    Uses extracted panel components for cleaner architecture.
    Each category (Anki, Media, Dictionary, Filtering, YouTube, UI) has its
    own panel.

    Signals:
        validation_requested: Emitted when validation should be triggered
        config_changed: Emitted when configuration is saved (passes new config)
        ytdlp_update_requested: Emitted when the YouTube panel's "Update yt-dlp
            now" button is clicked (manual, forced).
        asr_download_requested: Emitted when the Subtitles panel's "Download
            model" button is clicked. Carries the selected model name.
        alass_download_requested: Emitted when the Subtitles panel's "Download
            alass" button is clicked.
        cuda_pack_download_requested: Emitted when the Subtitles panel's
            "Download GPU acceleration" button is clicked.
        vad_pack_download_requested: Emitted when the Subtitles panel's
            "Download silence removal" button is clicked.
        vulkan_model_download_requested: Emitted when the Subtitles panel's
            "Download Vulkan model" button is clicked. Carries the selected
            acoustic model name.
    """

    validation_requested = pyqtSignal()
    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig
    ytdlp_update_requested = pyqtSignal()
    asr_download_requested = pyqtSignal(str)  # Emits model name
    alass_download_requested = pyqtSignal()
    cuda_pack_download_requested = pyqtSignal()
    vad_pack_download_requested = pyqtSignal()
    vulkan_model_download_requested = pyqtSignal(str)  # Emits model name

    # Fields written OUTSIDE the Settings Save path (theme selector, update
    # banner, first-run flags).  An update_config call that touches ONLY these
    # fields must NOT reload the panel widgets — that would destroy unsaved edits
    # the user has made in the Settings tab (OVH-007).
    _EXTERNAL_ONLY_FIELDS: frozenset[str] = frozenset(
        {
            "theme",
            "theme_favorites",
            "ui_font_scale",
            "ui_language",
            "skipped_update_version",
            "last_known_version",
            "first_run_shortcut_done",
            "first_run_setup_done",
        }
    )

    def __init__(self, config: AnkiMinerConfig, parent=None):
        """Initialize the settings tab.

        Args:
            config: Current configuration
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.config = config
        # True between a manual "Update yt-dlp now" click and its result, so the
        # shared result signal can surface a dialog on the manual path only.
        self._ytdlp_manual_pending = False
        # Auto-save guards. _loading suppresses edit signals fired by
        # programmatic widget repopulation (_load_config, selector re-syncs);
        # _committing suppresses re-entry when the debounce fires while a
        # commit is already in flight (the pitch zip import spins a nested
        # modal event loop that can let the timer fire mid-commit).
        self._loading = False
        self._committing = False
        self._setup_ui()
        # Controllers (T-66) own worker lifecycles + dialogs; the tab keeps
        # widgets, signal wiring, and config assembly. Dependency is one-way:
        # tab → controller → workers/services (tab-owned collaboration points
        # are injected as callables).
        # Modal pitch/frequency zip import engine: owns the import workers,
        # the ``.pending`` staging files, and the deferred-promotion closures;
        # the tab keeps the per-flow wrappers + save-time ordering.
        self._zip_import_flow = ZipImportFlow(self)
        # Dictionary add/reimport orchestration, incl. the Reimport-All
        # chained state machine and its predecessor-join (T-09).
        self._dict_import_flow = DictionaryImportFlow(
            parent=self,
            panel=self.dictionary_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # Audio pack add/reimport orchestration.
        self._audio_pack_import_flow = AudioPackImportFlow(
            parent=self,
            panel=self.audio_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_audio_chain_change,
        )
        # Frequency source add/reimport orchestration.
        self._frequency_import_flow = FrequencyImportFlow(
            parent=self,
            panel=self.frequency_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_frequency_chain_change,
        )
        # AnkiConnect probe workers (fetch fields / fetch decks / styling);
        # their live handles surface through iter_close_workers (T-12).
        self._anki_probe = AnkiProbeController(
            parent=self,
            anki_panel=self.anki_panel,
            filtering_panel=self.filtering_panel,
            get_config=lambda: self.config,
        )
        # Ordered list of panels that participate in the Save round-trip.
        # _load_config calls load_from_config on each; _on_save_clicked folds
        # contribute() over them.  Dictionary/audio chain panels and the UI
        # panel are intentionally excluded — they persist via their own signals.
        self._save_panels: list[_SavePathPanel] = [
            self.anki_panel,
            self.media_panel,
            self.filtering_panel,
            self.youtube_panel,
            self.subtitles_panel,
        ]
        self._connect_signals()
        self._load_config()

        # Debounced auto-save: any edit in the save-path panels restarts this
        # timer; expiry commits everything at once. Created and wired AFTER the
        # initial _load_config so construction can't arm it.
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_AUTOSAVE_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._commit_settings)
        self._wire_edit_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Category tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("settings-tabs")

        # Create panels using extracted components
        self.anki_panel = AnkiSettingsPanel()
        self.media_panel = MediaSettingsPanel()
        self.dictionary_panel = DictionarySettingsPanel(self.config.dicts_root)
        self.audio_panel = AudioPackSettingsPanel(self.config.audio_packs_root)
        self.frequency_panel = FrequencySettingsPanel(self.config.freqs_root)
        self.filtering_panel = FilteringSettingsPanel()
        self.youtube_panel = YouTubeSettingsPanel()
        self.subtitles_panel = SubtitlesSettingsPanel()
        self.ui_panel = UISettingsPanel(self.config.themes_root, self.config.ui_zoom, self.config.ui_language)

        # Add tabs with scroll areas for each panel. Stable string keys are
        # captured into _subtab_index so callers (MainWindow.reveal_capability,
        # the Find a Feature browser, theme shortcuts) can jump to a sub-tab by
        # key rather than a hard-coded index or the translated label.
        self._subtab_index: dict[str, int] = {
            "anki": self.tab_widget.addTab(self._wrap_in_scroll_area(self.anki_panel), self.tr("Anki")),
            "media": self.tab_widget.addTab(self._wrap_in_scroll_area(self.media_panel), self.tr("Media")),
            "dictionaries": self.tab_widget.addTab(
                self._wrap_in_scroll_area(self.dictionary_panel), self.tr("Dictionaries")
            ),
            "audio": self.tab_widget.addTab(self._wrap_in_scroll_area(self.audio_panel), self.tr("Audio")),
            "frequency": self.tab_widget.addTab(self._wrap_in_scroll_area(self.frequency_panel), self.tr("Frequency")),
            "filtering": self.tab_widget.addTab(self._wrap_in_scroll_area(self.filtering_panel), self.tr("Filtering")),
            "youtube": self.tab_widget.addTab(self._wrap_in_scroll_area(self.youtube_panel), self.tr("YouTube")),
            "subtitles": self.tab_widget.addTab(self._wrap_in_scroll_area(self.subtitles_panel), self.tr("Subtitles")),
            "ui": self.tab_widget.addTab(self._wrap_in_scroll_area(self.ui_panel), self.tr("UI")),
        }
        # Retained: _on_settings_subtab_changed and open_ui_subtab key off the
        # UI index; reading it from the map keeps a single source of truth.
        self._ui_subtab_index = self._subtab_index["ui"]
        # Reset the theme preview baseline when the user navigates away from the
        # UI tab so a later visit reverts to their last-chosen theme, not session
        # start.
        self.tab_widget.currentChanged.connect(self._on_settings_subtab_changed)

        layout.addWidget(self.tab_widget)

        # Updates row — single top-level toggle, no panel needed for one checkbox.
        self.check_for_updates_checkbox = QCheckBox(self.tr("Check for updates on startup"))
        self.check_for_updates_checkbox.setToolTip(
            self.tr("When enabled, Anki Miner queries GitHub for new releases on launch.")
        )
        layout.addWidget(self.check_for_updates_checkbox)

        # Action buttons at bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)
        button_layout.addStretch()

        self.reset_button = ModernButton(self.tr("Reset to Defaults"), variant="secondary")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.reset_button.setToolTip(self.tr("Reset all settings to default values (Ctrl+R)"))
        button_layout.addWidget(self.reset_button)

        self.save_button = ModernButton(self.tr("Save Settings"), variant="primary")
        self.save_button.clicked.connect(self._on_save_clicked)
        self.save_button.setToolTip(self.tr("Save settings to disk (Ctrl+S)"))
        button_layout.addWidget(self.save_button)

        # Inline, non-modal save confirmation (replaces the old "Settings Saved"
        # popup). Flashed by _flash_save_status() and auto-cleared by a timer.
        self.save_status_label = QLabel("")
        self.save_status_label.setObjectName("settings-save-status")
        button_layout.addWidget(self.save_status_label)

        self._save_status_timer = QTimer(self)
        self._save_status_timer.setSingleShot(True)
        self._save_status_timer.timeout.connect(lambda: self.save_status_label.setText(""))

        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def _connect_signals(self) -> None:
        """Connect panel signals to tab handlers."""
        # Anki panel signals
        self.anki_panel.deck_sync_requested.connect(self.validation_requested.emit)
        self.anki_panel.notetype_sync_requested.connect(self.validation_requested.emit)
        self.anki_panel.test_connection_requested.connect(self.validation_requested.emit)
        self.anki_panel.fetch_fields_requested.connect(self._anki_probe.fetch_fields)

        # Dictionary panel signals — wire Add/Reimport to the import flow
        # controller, which owns the worker dialogs (T-66).
        self.dictionary_panel.add_dict_requested.connect(self._dict_import_flow.add_dict)
        self.dictionary_panel.reimport_jmdict_requested.connect(self._dict_import_flow.reimport_jmdict)
        self.dictionary_panel.reimport_dict_requested.connect(self._dict_import_flow.reimport_dict)
        self.dictionary_panel.reimport_all_requested.connect(self._dict_import_flow.reimport_all)
        self.dictionary_panel.rescan_requested.connect(self._dict_import_flow.restore_unlisted)
        # Persist chain immediately after reorder/toggle or destructive remove.
        # Use a NARROW persist of just the chain — NOT the full Save pipeline
        # (T-08): _on_save_clicked has unrelated early-return aborts (bad
        # dicts_root, missing cookies file, invalid regex, pitch/freq import
        # failure), any of which would skip persisting a removal and leave the
        # deleted dict_id orphaned — the exact Issue #30 bug this wiring
        # prevents — while its success path silently commits every panel's
        # unsaved edits.
        # Removal also emits chain_changed (dictionary_settings_panel.py:498,
        # before dictionary_removed at 499), so this single wiring covers it;
        # wiring dictionary_removed too would persist the same chain twice.
        # dictionary_removed stays unconnected until a consumer needs the
        # removal-specific notification (OVH-032).
        self.dictionary_panel.chain_changed.connect(
            lambda: self._persist_chain_change(self.dictionary_panel.get_chain())
        )

        # Audio panel signals — wire Add/Reimport to the import flow controller.
        self.audio_panel.add_pack_requested.connect(self._audio_pack_import_flow.add_pack)
        self.audio_panel.reimport_pack_requested.connect(self._audio_pack_import_flow.reimport_pack)
        # Persist chain immediately after reorder/toggle or destructive remove.
        # Removal also emits chain_changed (after pack deletion succeeds), so
        # this single wiring covers it; wiring pack_removed too would persist
        # the same chain twice. pack_removed stays unconnected until a
        # consumer needs the removal-specific notification.
        self.audio_panel.chain_changed.connect(lambda: self._persist_audio_chain_change(self.audio_panel.get_chain()))
        self.audio_panel.retry_missing_audio_requested.connect(self._on_retry_missing_audio)
        # Sentence-TTS toggles persist immediately, like the chain above.
        self.audio_panel.reading_tts_changed.connect(self._persist_reading_tts_change)

        # Frequency panel signals — wire Add/Reimport to the import flow.
        self.frequency_panel.add_source_requested.connect(self._frequency_import_flow.add_source)
        self.frequency_panel.reimport_source_requested.connect(self._frequency_import_flow.reimport_source)
        # Persist chain immediately after reorder/toggle or destructive remove.
        # Removal also emits chain_changed (after source deletion succeeds), so
        # this single wiring covers it; wiring source_removed too would persist
        # the same chain twice. source_removed stays unconnected until a
        # consumer needs the removal-specific notification.
        self.frequency_panel.chain_changed.connect(
            lambda: self._persist_frequency_chain_change(self.frequency_panel.get_chain())
        )

        # Filtering panel: excluded-decks picker + known-words cache rebuild (Issue #38).
        self.filtering_panel.fetch_decks_requested.connect(self._anki_probe.fetch_decks)
        self.filtering_panel.rebuild_known_words_requested.connect(self._on_rebuild_known_words)
        self.filtering_panel.manage_known_words_requested.connect(self._on_manage_known_words)

        # UI panel persists immediately on any change (live-preview model).
        self.ui_panel.state_changed.connect(self._on_theme_state_changed)
        self.ui_panel.font_scale_changed.connect(self._on_font_scale_changed)
        self.ui_panel.zoom_changed.connect(self._on_zoom_changed)
        self.ui_panel.language_changed.connect(self._on_language_changed)

        # YouTube panel: manual "Update yt-dlp now" → re-emit to MainWindow
        # (app.py routes it to background_tasks.start_ytdlp_update(force=True)).
        self.youtube_panel.update_ytdlp_requested.connect(self._on_ytdlp_update_clicked)

        # Subtitles panel: "Download model" / "Download alass" → re-emit to
        # MainWindow (or caller), which owns the background download workers.
        self.subtitles_panel.asr_download_requested.connect(self._on_asr_download_clicked)
        self.subtitles_panel.alass_download_requested.connect(self._on_alass_download_clicked)
        self.subtitles_panel.cuda_pack_download_requested.connect(self._on_cuda_pack_download_clicked)
        self.subtitles_panel.vad_pack_download_requested.connect(self._on_vad_pack_download_clicked)
        self.subtitles_panel.vulkan_model_download_requested.connect(self._on_vulkan_download_clicked)

    def _wire_edit_signals(self) -> None:
        """Arm the auto-save debounce on any user edit in the save-path panels.

        Uses recursive ``findChildren`` — load-bearing: the FileSelectors
        embedded in the Filtering/YouTube panels (blacklist, whitelist,
        cookies) expose edits only through their NESTED QLineEdit; a
        direct-children walk would silently never auto-save those fields.
        Redundant arming (e.g. a spinbox's inner line edit) is harmless — the
        slot just restarts the timer. Programmatic repopulation is filtered by
        the ``_loading`` guard, not here.
        """
        from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QSpinBox

        panels: tuple[QWidget, ...] = (
            self.anki_panel,
            self.media_panel,
            self.filtering_panel,
            self.youtube_panel,
            self.subtitles_panel,
        )
        for panel in panels:
            for line_edit in panel.findChildren(QLineEdit):
                line_edit.textChanged.connect(self._on_settings_edited)
            for checkbox in panel.findChildren(QCheckBox):
                checkbox.toggled.connect(self._on_settings_edited)
            for spinbox in panel.findChildren(QSpinBox):
                spinbox.valueChanged.connect(self._on_settings_edited)
            for double_spinbox in panel.findChildren(QDoubleSpinBox):
                double_spinbox.valueChanged.connect(self._on_settings_edited)
            for combo in panel.findChildren(QComboBox):
                combo.currentIndexChanged.connect(self._on_settings_edited)
            for list_widget in panel.findChildren(QListWidget):
                # Excluded-decks list mutates via Add/Remove buttons, so the
                # widget itself has no edit signal — watch its model instead.
                model = list_widget.model()
                if model is not None:
                    model.rowsInserted.connect(self._on_settings_edited)
                    model.rowsRemoved.connect(self._on_settings_edited)

        # Fields outside the save panels that commit through the same path.
        self.check_for_updates_checkbox.toggled.connect(self._on_settings_edited)
        self.dictionary_panel.dicts_root_selector.path_changed.connect(self._on_settings_edited)
        self.dictionary_panel.pitch_accent_selector.path_changed.connect(self._on_settings_edited)

    def _on_settings_edited(self, *_args) -> None:
        """Restart the auto-save debounce on a user edit (no-op while loading)."""
        if self._loading:
            return
        self._debounce_timer.start()

    def _on_ytdlp_update_clicked(self) -> None:
        """Mark the next yt-dlp result as user-initiated, then request the update.

        The manual path may surface a message box on failure; the auto (startup)
        path must stay silent. The flag distinguishes them on the shared result
        signal — see :meth:`set_ytdlp_status_from_result`.
        """
        self._ytdlp_manual_pending = True
        self.youtube_panel.set_ytdlp_status(self.tr("Updating yt-dlp…"))
        self.ytdlp_update_requested.emit()

    def _on_asr_download_clicked(self, model_name: str) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        The wiring (SettingsTab → caller → AsrModelDownloadWorker) mirrors the
        ytdlp_update_requested pattern: the tab updates its own status label and
        re-emits; the download itself is owned by the caller (MainWindow /
        background_tasks).
        """
        self.subtitles_panel.set_model_status(self.tr("Downloading…"))
        self.asr_download_requested.emit(model_name)

    def _on_alass_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_asr_download_clicked`: the download itself is owned by
        the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_alass_status(self.tr("Downloading…"))
        self.alass_download_requested.emit()

    def _on_cuda_pack_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_alass_download_clicked`: the download itself is owned
        by the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_cuda_pack_status(self.tr("Downloading…"))
        self.cuda_pack_download_requested.emit()

    def _on_vad_pack_download_clicked(self) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_cuda_pack_download_clicked`: the download itself is
        owned by the caller (MainWindow / background_tasks).
        """
        self.subtitles_panel.set_vad_pack_status(self.tr("Downloading…"))
        self.vad_pack_download_requested.emit()

    def _on_vulkan_download_clicked(self, model_name: str) -> None:
        """Set a pending status and re-emit so the caller can start the download.

        Mirrors :meth:`_on_vad_pack_download_clicked`: the download itself is
        owned by the caller (MainWindow / background_tasks). Carries the selected
        acoustic model name through to the wiring.
        """
        self.subtitles_panel.set_vulkan_status(self.tr("Downloading…"))
        self.vulkan_model_download_requested.emit(model_name)

    def set_asr_model_status(self, text: str) -> None:
        """Forward an ASR model download status line to the Subtitles panel."""
        self.subtitles_panel.set_model_status(text)

    def set_alass_status(self, text: str) -> None:
        """Forward an alass download status line to the Subtitles panel."""
        self.subtitles_panel.set_alass_status(text)

    def set_cuda_pack_status(self, text: str) -> None:
        """Forward a GPU-pack download status line to the Subtitles panel."""
        self.subtitles_panel.set_cuda_pack_status(text)

    def set_vad_pack_status(self, text: str) -> None:
        """Forward a VAD-pack download status line to the Subtitles panel."""
        self.subtitles_panel.set_vad_pack_status(text)

    def set_vulkan_status(self, text: str) -> None:
        """Forward a Vulkan model download status line to the Subtitles panel."""
        self.subtitles_panel.set_vulkan_status(text)

    def set_ytdlp_status(self, text: str) -> None:
        """Forward a yt-dlp updater status line to the YouTube panel."""
        self.youtube_panel.set_ytdlp_status(text)

    def set_ytdlp_status_from_result(self, result: object) -> None:
        """Update the YouTube panel status from a yt-dlp update result.

        Always refreshes the status line. On a user-initiated (manual) trigger,
        also pops a warning dialog for ``failed`` / ``unavailable``; the auto
        startup path stays silent (no-nag).
        """
        message = getattr(result, "message", "") or ""
        action = getattr(result, "action", "")
        self.youtube_panel.set_ytdlp_status(message)

        manual = getattr(self, "_ytdlp_manual_pending", False)
        self._ytdlp_manual_pending = False
        if manual and action in ("failed", "unavailable"):
            QMessageBox.warning(
                self,
                self.tr("yt-dlp update"),
                message or self.tr("Could not update yt-dlp. Check your connection and retry."),
            )

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Ctrl+S: Save settings
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save_clicked)

        # Ctrl+R: Reset to defaults
        reset_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        reset_shortcut.activated.connect(self._on_reset_clicked)

    def _wrap_in_scroll_area(self, widget: QWidget) -> QScrollArea:
        """Wrap a widget in a scrollable container.

        Args:
            widget: Widget to wrap

        Returns:
            QScrollArea containing the widget
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(widget)
        return scroll_area

    def _load_config(self) -> None:
        """Load current configuration into UI.

        Save-path panels (Anki, Media, Filtering, YouTube) are loaded via the
        symmetric ``load_from_config`` contract so each panel owns its fields
        in one place (OVH-019).  Dictionary/audio chain panels and the
        top-level update checkbox persist via their own paths and are handled
        directly here.

        Runs under the ``_loading`` guard: the setText/setChecked/setValue
        calls below fire the same change signals user edits do, and must not
        arm the auto-save debounce (a reload would otherwise commit itself).
        """
        self._loading = True
        try:
            # Save-path panels — each owns its field list.
            for panel in self._save_panels:
                panel.load_from_config(self.config)

            # Dictionary chain (not part of the Save round-trip — persisted
            # immediately via chain_changed / _persist_chain_change).
            self.dictionary_panel.set_dicts_root(self.config.dicts_root)
            self.dictionary_panel.set_chain(self.config.dictionary_chain)

            # Audio source chain (same — immediate persist via its own signal).
            self.audio_panel.set_chain(self.config.expression_audio_chain)
            self.audio_panel.set_reading_tts(
                self.config.reading_tts_enabled,
                self.config.reading_tts_google_enabled,
                self.config.reading_tts_papago_enabled,
            )

            # Frequency source chain lives in the Frequency tab; the chain persists
            # immediately via its own signal. Frequency activation is derived from an
            # enabled source being present (config.frequency_active) — no toggle. The
            # max-rank threshold is owned by filtering_panel and already loaded above.
            self.frequency_panel.set_chain(self.config.frequency_chain)

            # Pitch accent settings — file selector lives in the Dictionaries tab.
            # Activation is derived from the file being present (config.pitch_active).
            self.dictionary_panel.pitch_accent_selector.set_path(str(self.config.pitch_accent_path))

            # Update settings — standalone checkbox outside all panels.
            self.check_for_updates_checkbox.setChecked(self.config.check_for_updates)

            self.ui_panel.set_language(self.config.ui_language)
        finally:
            self._loading = False

    def open_subtab(self, key: str) -> None:
        """Switch the settings sub-tab to the one named by ``key``.

        ``key`` is a stable identifier from
        :data:`anki_miner.gui.capabilities.SETTINGS_SUBTABS` (e.g. ``"filtering"``,
        ``"anki"``). Unknown keys are ignored so a stale caller can't crash the UI.
        """
        index = self._subtab_index.get(key)
        if index is not None:
            self.tab_widget.setCurrentIndex(index)

    def trigger_reimport_all(self) -> None:
        """Run the Dictionary → Reimport All flow (4.0 migration prompt hook).

        Public entry point the startup schema-staleness prompt calls after the
        user opts to reimport now. Delegates to the same
        ``DictionaryImportFlow.reimport_all`` the panel button drives, so the
        one-click migration and the manual path stay identical.
        """
        self.open_subtab("dictionaries")
        self._dict_import_flow.reimport_all()

    def open_ui_subtab(self) -> None:
        """Switch the settings sub-tab to UI (language, zoom, text size, themes).

        Thin wrapper over :meth:`open_subtab` kept because MainWindow's
        ``_settings_tab_index`` uses this method name as the capability marker
        that identifies the Settings tab, and the 'All themes…' header sentinel
        calls it directly.
        """
        self.open_subtab("ui")

    def _on_settings_subtab_changed(self, index: int) -> None:
        """Reset the UI panel's theme preview baseline when leaving its sub-tab."""
        if index != self._ui_subtab_index:
            self.ui_panel.reset_baseline()

    def _on_theme_state_changed(self, active: str, favorites: tuple) -> None:
        """Forward Themes panel changes through ``config_changed``.

        The Themes panel writes through Theme directly (live preview); this
        slot mirrors the change into ``self.config`` and re-emits so the
        existing ``config_changed`` → ``MainWindow.update_config`` chain
        persists to ``gui_config.json`` without duplicate logic.
        """
        self.config = replace(self.config, theme=active, theme_favorites=tuple(favorites))
        self.config_changed.emit(self.config)

    def _on_font_scale_changed(self, scale: float) -> None:
        """Fold the Themes panel font-scale change into the config and persist.

        Mirrors :meth:`_on_theme_state_changed`: the Themes panel writes
        through Theme directly (live preview); this slot reflects the new
        scale into ``self.config`` and re-emits so the existing
        ``config_changed`` → ``MainWindow.update_config`` chain persists
        ``ui_font_scale`` to ``gui_config.json``.
        """
        self.config = replace(self.config, ui_font_scale=scale)
        self.config_changed.emit(self.config)

    def _on_zoom_changed(self, zoom: float) -> None:
        """Persist a whole-UI zoom change immediately (applies on next launch).

        Zoom is injected as QT_SCALE_FACTOR before QApplication is built, so
        unlike font scale there is no live restyle — the Themes panel reveals a
        restart note and this slot only folds ``ui_zoom`` into the config.
        """
        self.config = replace(self.config, ui_zoom=zoom)
        self.config_changed.emit(self.config)

    def _on_language_changed(self, language: str) -> None:
        """Persist a UI-language change immediately (applies on next launch)."""
        self.config = replace(self.config, ui_language=language)
        self.config_changed.emit(self.config)

    def commit_settings(self, skip_zip_import: bool = False) -> None:
        """Commit the save-path panels into the config and emit ``config_changed``.

        Public entry point for tests and the close flush; the auto-save
        debounce timer drives :meth:`_commit_settings` directly.
        """
        self._commit_settings(skip_zip_import=skip_zip_import)

    def flush_pending_settings(self) -> None:
        """Commit a pending debounced edit immediately (close-time flush).

        MainWindow.closeEvent calls this at its very TOP — before the
        background-task shutdown fan-out reaches :meth:`shutdown` (which stops
        the timer, so a later flush would see an inactive timer and no-op),
        and before the deferred-close path returns without ever reaching the
        final save. Committing here routes through config_changed →
        MainWindow.update_config, which writes gui_config.json and updates
        MainWindow.config, so both close paths persist the edit.

        ``skip_zip_import=True`` keeps the modal pitch-zip import (nested
        event loop + dialogs) out of closeEvent: non-pitch fields commit and
        an unconfirmed .zip selection is dropped — the next launch repaints
        the selector from the persisted config.
        """
        if self._debounce_timer.isActive():
            self._debounce_timer.stop()
            self._commit_settings(skip_zip_import=True)

    def _commit_settings(self, skip_zip_import: bool = False) -> None:
        """Debounced auto-save commit with per-field validation.

        Unlike the old Save-button flow (modal warning + whole-save abort),
        an invalid field must NOT block unrelated edits: under silent
        auto-save a stale invalid value (deleted cookies.txt, bad regex —
        both gates fire even when untouched) would otherwise stop EVERY
        setting from persisting, with only a small label as evidence. Each
        failing field keeps its last-good value from ``self.config``, the
        rest commits, and a sticky inline warning names what was kept.
        """
        if self._committing:
            # The debounce fired while a commit is in flight (the pitch zip
            # import spins a nested modal event loop) — retry afterwards.
            self._debounce_timer.start()
            return
        self._committing = True
        try:
            # This commit consumes whatever edits armed the timer — cancel a
            # still-pending expiry so it can't fire a redundant second commit.
            self._debounce_timer.stop()
            self._commit_settings_locked(skip_zip_import)
        finally:
            self._committing = False

    def _commit_settings_locked(self, skip_zip_import: bool) -> None:
        # If the user just re-enabled startup checks (False -> True), clear any
        # previously skipped version so a fresh check runs next launch.
        was_enabled = self.config.check_for_updates
        now_enabled = self.check_for_updates_checkbox.isChecked()
        skipped_update_version = self.config.skipped_update_version
        if now_enabled and not was_enabled:
            skipped_update_version = ""

        # Human-readable names of fields whose edit was kept back (last-good
        # value re-used) because validation failed. Drives the sticky warning.
        kept_back: list[str] = []

        # Validate dictionary storage folder (Issue #45). Only enforced when
        # the user has changed the path — reuse-of-current always passes so a
        # transiently-unavailable mount (external SSD) doesn't block other
        # unrelated edits from saving.
        new_dicts_root = self.dictionary_panel.get_dicts_root()
        if new_dicts_root != self.config.dicts_root and (
            not new_dicts_root.is_dir() or not os.access(new_dicts_root, os.W_OK)
        ):
            kept_back.append(self.tr("dictionary folder (Dictionaries)"))
            new_dicts_root = self.config.dicts_root

        # Fold: each panel's contribute() returns a new frozen config with its
        # own fields applied.  Panels outside the Save round-trip (dictionary /
        # audio / frequency chain, themes) are handled separately below.
        new_config = self.config
        for panel in self._save_panels:
            new_config = panel.contribute(new_config)

        # Validate the YouTube cookies file (Issue #62). yt-dlp would otherwise
        # fail mid-fetch with a cryptic message; catch a bad path up front.
        # An empty field is valid (no cookies file).
        cookies_file = self.youtube_panel.get_cookies_file()
        if cookies_file and not Path(cookies_file).is_file():
            kept_back.append(self.tr("cookies file (YouTube)"))
            new_config = replace(new_config, youtube_cookies_file=self.config.youtube_cookies_file)

        # Validate subtitle regex filter so we never persist a pattern that
        # crashes the parser. Only validated when the filter is enabled; an
        # unchecked invalid pattern is harmless. The toggle+pattern pair is
        # kept back together — committing "enabled" with the old pattern (or
        # a new pattern disabled) would not match what the user sees.
        subtitle_regex = self.filtering_panel.get_subtitle_regex_filter()
        use_subtitle_regex = self.filtering_panel.get_use_subtitle_regex_filter()
        if use_subtitle_regex and subtitle_regex:
            try:
                re.compile(subtitle_regex)
            except re.error:
                kept_back.append(self.tr("subtitle regex (Filtering)"))
                new_config = replace(
                    new_config,
                    subtitle_regex_filter=self.config.subtitle_regex_filter,
                    use_subtitle_regex_filter=self.config.use_subtitle_regex_filter,
                )

        new_config = replace(
            new_config,
            # Dictionary chain — chain is the single source of truth now.
            dictionary_chain=self.dictionary_panel.get_chain(),
            # Dictionary storage folder (Issue #45). Validated above; reuse of
            # current value passes through unchanged.
            dicts_root=new_dicts_root,
            # Pitch accent settings — pitch_accent_path is overwritten below
            # with the resolver's result once the staged import commits.
            pitch_accent_path=self.config.pitch_accent_path,
            # Frequency source chain — persisted immediately via the frequency
            # panel's signals, but also included here for sync.
            frequency_chain=self.frequency_panel.get_chain(),
            # Audio source chain — persisted immediately via chain_changed, but
            # also included in the full Save so it is always in sync.
            expression_audio_chain=self.audio_panel.get_chain(),
            # Sentence-TTS toggles — same immediate-persist + full-Save sync.
            reading_tts_enabled=self.audio_panel.get_reading_tts()[0],
            reading_tts_google_enabled=self.audio_panel.get_reading_tts()[1],
            reading_tts_papago_enabled=self.audio_panel.get_reading_tts()[2],
            # Update settings
            check_for_updates=now_enabled,
            skipped_update_version=skipped_update_version,
        )

        # Pitch-zip import (modal: nested event loop + dialogs). Runs LAST so
        # any validation above happens before we touch pitch_accent.csv on
        # disk; the resolver only STAGES to a ``.pending`` sibling and the
        # destructive promotion happens in _commit_pending_csv_imports. None
        # means the import failed OR was cancelled — keep the last-good path
        # and commit the rest (the user already saw the error dialog, if any).
        # Skipped entirely on the close flush (never spin a modal loop inside
        # closeEvent); an unconfirmed .zip pick is dropped there.
        if not skip_zip_import:
            # The whole block runs under the _loading guard: the import flow's
            # commit closure and the re-sync below both set_path the selector,
            # which re-emits path_changed and must not re-arm the debounce.
            self._loading = True
            try:
                resolved_pitch_path = self._resolve_pitch_accent_path()
                if resolved_pitch_path is None:
                    resolved_pitch_path = self.config.pitch_accent_path
                else:
                    # Pitch import validated (or passthrough) — promote the
                    # staged CSV and run its UI feedback now.
                    self._commit_pending_csv_imports()
                new_config = replace(new_config, pitch_accent_path=resolved_pitch_path)

                # Re-sync the selector to whatever was actually committed.
                # Covers cancel/failure (resolver returned None) AND
                # overwrite-decline (resolver returns the fallback path, not
                # None): without this the declined .zip stays in the selector
                # and every later commit re-pops the overwrite modal.
                selector = self.dictionary_panel.pitch_accent_selector
                if selector.get_path() != str(resolved_pitch_path):
                    selector.set_path(str(resolved_pitch_path))
            finally:
                self._loading = False

        # Sync the dictionary panel to the committed root (T-07). Done here —
        # alongside the config assignment, after every validation/import has
        # passed — so the panel never points at a root the save then aborted on.
        # Without this the panel keeps scanning/rmtree-targeting the OLD root
        # until restart: refresh_registry() renders fresh imports as "(missing)"
        # and remove() deletes from the wrong directory. Under the _loading
        # guard: set_dicts_root re-emits the selector's path_changed.
        if new_dicts_root != self.config.dicts_root:
            self._loading = True
            try:
                self.dictionary_panel.set_dicts_root(new_dicts_root)
            finally:
                self._loading = False

        # Emit signal to notify listeners of config change
        self.config = new_config
        self.config_changed.emit(new_config)
        if kept_back:
            # Sticky (no auto-clear): stays visible until the next fully-valid
            # commit replaces it with the ✓ flash.
            self._save_status_timer.stop()
            self.save_status_label.setText(tr_format(self.tr("⚠ Saved — kept previous: %1"), ", ".join(kept_back)))
        else:
            self._flash_save_status(self.tr("✓ Saved"))

    def _on_save_clicked(self) -> None:
        """Handle save button click (delegates to the auto-save commit)."""
        self.commit_settings()

    def _flash_save_status(self, text: str) -> None:
        """Show a transient, non-modal confirmation beside the Save button.

        Restarts the auto-clear timer on each call so repeated saves keep the
        message visible for the full duration.
        """
        self.save_status_label.setText(text)
        self._save_status_timer.stop()
        self._save_status_timer.start(2500)

    def _resolve_pitch_accent_path(self) -> Path | None:
        """Resolve the pitch-accent selector for persistence (see the engine).

        Thin wrapper over :meth:`ZipImportFlow.run_modal_zip_import`; see it
        for the full staging/deferred-promotion contract and the meaning of
        each return.
        """
        return self._zip_import_flow.run_modal_zip_import(
            selector=self.dictionary_panel.pitch_accent_selector,
            dest_name="pitch_accent.csv",
            worker_factory=partial(YomitanCsvImportWorker, import_yomitan_pitch_zip),
            worker_slot_attr="_active_pitch_worker",
            commit_slot_attr="_pending_pitch_commit",
            decline_fallback=self.config.pitch_accent_path,
            labels=YomitanCsvLabels(
                progress=self.tr("Importing pitch accent dictionary…"),
                overwrite_title=self.tr("Overwrite Pitch Accent File?"),
                failure_title=self.tr("Pitch Accent Import Failed"),
                success_title=self.tr("Pitch accent dictionary imported"),
            ),
        )

    def _commit_pending_csv_imports(self) -> None:
        """Promote any staged pitch CSV import (delegates to the flow)."""
        self._zip_import_flow.commit_pending_csv_imports()

    def _on_reset_clicked(self) -> None:
        """Handle reset button click."""
        reply = QMessageBox.question(
            self,
            self.tr("Reset Settings"),
            self.tr("Reset all settings to defaults?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from anki_miner.config import create_default_config

            self.config = create_default_config()
            self._load_config()
            self.config_changed.emit(self.config)
            self._flash_save_status(self.tr("✓ Reset to defaults"))

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration from external source.

        Skips reloading the panel widgets when the incoming config differs from
        the current one ONLY in externally-managed fields (theme, font scale,
        first-run flags, update-banner fields — see ``_EXTERNAL_ONLY_FIELDS``).
        This preserves unsaved edits the user has made in the Settings tab when,
        for example, a theme change arrives via config_refreshed (OVH-007).

        Genuinely panel-relevant changes (e.g. JMdict migration updates
        dicts_root) still trigger the full reload.

        Args:
            config: New configuration to load
        """
        changed = {
            f.name for f in dataclasses.fields(config) if getattr(config, f.name) != getattr(self.config, f.name)
        }
        self.config = config
        if not changed or changed <= self._EXTERNAL_ONLY_FIELDS:
            # No panel-relevant field changed: either identical config (no-op
            # refresh) or every diff is in the externally-managed allowlist.
            # Skip reload to preserve in-progress widget edits.
            return
        self._load_config()

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        Chains the three AnkiConnect probe workers (T-66) with the active
        import workers from all three import flows (OVH-004, 059, 060) so
        ``BackgroundTaskController._join_worker_for_close`` sees every live
        Settings-tab QThread.  ``None`` entries (idle flows) are filtered
        by ``_join_worker_for_close``.
        """
        return (
            *self._anki_probe.iter_close_workers(),
            *self._dict_import_flow.iter_close_workers(),
            *self._audio_pack_import_flow.iter_close_workers(),
            *self._frequency_import_flow.iter_close_workers(),
            *self._zip_import_flow.iter_close_workers(),
        )

    def shutdown(self) -> None:
        """Cancel every running AnkiConnect worker (cancel only, no wait).

        Explicit-teardown entry point mirroring the YouTube tab; delegates to
        :class:`AnkiProbeController`, which owns the workers (T-66).

        Also stops the auto-save debounce so an armed timer can never fire
        into a torn-down widget (the pytest-qt ``_drain_qt_deletes`` segfault
        class). Pending edits are persisted by ``flush_pending_settings``,
        which MainWindow.closeEvent runs BEFORE the shutdown fan-out reaches
        this method.
        """
        self._debounce_timer.stop()
        self._anki_probe.shutdown()

    # === Expose panel inputs for backward compatibility ===

    @property
    def deck_input(self):
        """Get deck input widget."""
        return self.anki_panel.deck_input

    @property
    def note_type_input(self):
        """Get note type input widget."""
        return self.anki_panel.note_type_input

    @property
    def ankiconnect_url_input(self):
        """Get AnkiConnect URL input widget."""
        return self.anki_panel.ankiconnect_url_input

    @property
    def audio_padding_spinbox(self):
        """Get audio padding spinbox widget."""
        return self.media_panel.audio_padding_spinbox

    @property
    def screenshot_offset_spinbox(self):
        """Get screenshot offset spinbox widget."""
        return self.media_panel.screenshot_offset_spinbox

    @property
    def max_workers_spinbox(self):
        """Get max workers spinbox widget."""
        return self.media_panel.max_workers_spinbox

    # === Dictionary chain persistence ===

    def _persist_chain_change(self, new_chain: tuple[ChainEntry, ...]) -> None:
        """Save a chain mutation to disk and notify listeners.

        Called after a successful import (or a panel reorder/remove) so the
        freshly imported dictionary is reachable on the very next lookup —
        without requiring a manual Save. Without this, the dict folder exists on
        disk but is absent from dictionary_chain in gui_config, i.e. invisible to
        DictionaryRegistry.build_provider_chain.

        A chain change alters which dictionaries' scoped CSS is embedded in the
        per-card ``<style>`` block, but that block is assembled per-episode at
        card-creation time (``EpisodeProcessor._phase5_create``), so nothing
        needs to sync to Anki here.
        """
        new_config = replace(self.config, dictionary_chain=new_chain)
        self.config = new_config
        self.config_changed.emit(new_config)

    def _persist_audio_chain_change(self, new_chain: tuple[AudioSourceEntry, ...]) -> None:
        """Save an audio chain mutation to disk and notify listeners.

        Called after a successful audio pack import or a destructive remove so
        the freshly-imported pack is reachable on the very next lookup without
        requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, expression_audio_chain=new_chain)
        self.config = new_config
        self.config_changed.emit(new_config)

    def _persist_reading_tts_change(self) -> None:
        """Save the sentence-TTS toggles immediately (no Save click needed)."""
        enabled, google_on, papago_on = self.audio_panel.get_reading_tts()
        new_config = replace(
            self.config,
            reading_tts_enabled=enabled,
            reading_tts_google_enabled=google_on,
            reading_tts_papago_enabled=papago_on,
        )
        self.config = new_config
        self.config_changed.emit(new_config)

    def _on_retry_missing_audio(self) -> None:
        """Clear JPod101 ``.miss`` markers so absent words are re-tried next run.

        Replaces the old folklore of deleting the ``audio_cache`` dir by hand.
        The unlink sweep runs off the GUI thread (run_off_thread convention);
        the removed count is confirmed in a dialog on completion.
        """
        cache_dir = ANKI_MINER_HOME / "audio_cache" / "jpod101"
        self.audio_panel.set_retry_missing_enabled(False)
        run_off_thread(
            self,
            lambda: purge_miss_markers(cache_dir),
            self._on_retry_missing_audio_done,
            self._on_retry_missing_audio_error,
        )

    def _on_retry_missing_audio_done(self, removed: object) -> None:
        """Re-enable the button and report how many markers were cleared."""
        self.audio_panel.set_retry_missing_enabled(True)
        count = removed if isinstance(removed, int) else 0
        QMessageBox.information(
            self,
            self.tr("Retry missing expression audio"),
            tr_format(
                self.tr("Cleared %1 missing-audio marker(s). Those words will be re-tried on the next mining run."),
                count,
            ),
        )

    def _on_retry_missing_audio_error(self, msg: str) -> None:
        """Re-enable the button and surface an unexpected sweep failure."""
        self.audio_panel.set_retry_missing_enabled(True)
        QMessageBox.warning(
            self,
            self.tr("Retry missing expression audio"),
            tr_format(self.tr("Could not clear the markers: %1"), msg),
        )

    def _persist_frequency_chain_change(self, new_chain: tuple[FreqEntry, ...]) -> None:
        """Save a frequency chain mutation to disk and notify listeners.

        Called after a successful frequency-source import or a destructive
        remove so the freshly-imported source is reachable on the very next
        run without requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, frequency_chain=new_chain)
        self.config = new_config
        self.config_changed.emit(new_config)

    # === Known words handlers (Issues #38 / #42) ===

    def _on_rebuild_known_words(self) -> None:
        """Clear the local known-words cache after user confirmation.

        The cache is additive (see :class:`KnownWordDB`), so removing a deck's
        words after it was already synced requires a full rebuild. The next
        mining run re-syncs from Anki with the current exclusions applied.
        """
        confirm = QMessageBox.question(
            self,
            self.tr("Rebuild Known Words DB"),
            self.tr(
                "Clear the local known-words cache? It will re-sync from Anki on the "
                "next mining run, applying your current deck exclusions. Words you "
                "added yourself from the Word Curator are kept."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            db = KnownWordDB(self.config.known_words_db_path)
            db.initialize()
            # Preserve the user-curated ignore list (Issue #42); only the
            # Anki-synced rows are rebuilt from Anki on the next run.
            removed = db.clear(preserve_user=True)
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the user
            QMessageBox.warning(
                self, self.tr("Rebuild Known Words DB"), tr_format(self.tr("Could not clear the cache: %1"), e)
            )
            return

        QMessageBox.information(
            self,
            self.tr("Rebuild Known Words DB"),
            tr_format(self.tr("Cleared %1 cached word(s). The cache will rebuild on the next run."), removed),
        )

    def _on_manage_known_words(self) -> None:
        """Open the Manage Known Words dialog (Issue #42)."""
        from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog

        try:
            db = KnownWordDB(self.config.known_words_db_path)
            KnownWordsManagerDialog(db, self).exec()
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the user
            QMessageBox.warning(
                self, self.tr("Manage Known Words"), tr_format(self.tr("Could not open the known words list: %1"), e)
            )
