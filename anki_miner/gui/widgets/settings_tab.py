"""Settings tab with category organization using extracted panels."""

import re
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    MediaSettingsPanel,
    YouTubeSettingsPanel,
)
from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker


class SettingsTab(QWidget):
    """Settings tab with category organization.

    Uses extracted panel components for cleaner architecture.
    Each category (Anki, Media, Dictionary, Filtering) has its own panel.

    Signals:
        validation_requested: Emitted when validation should be triggered
        config_changed: Emitted when configuration is saved (passes new config)
    """

    validation_requested = pyqtSignal()
    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig

    def __init__(self, config: AnkiMinerConfig, parent=None):
        """Initialize the settings tab.

        Args:
            config: Current configuration
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.config = config
        self._setup_ui()
        self._connect_signals()
        self._load_config()

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
        self.dictionary_panel = DictionarySettingsPanel()
        self.filtering_panel = FilteringSettingsPanel()
        self.youtube_panel = YouTubeSettingsPanel()

        # Add tabs with scroll areas for each panel
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.anki_panel), "Anki")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.media_panel), "Media")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.dictionary_panel), "Dictionary")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.filtering_panel), "Filtering")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.youtube_panel), "YouTube")

        layout.addWidget(self.tab_widget)

        # Updates row — single top-level toggle, no panel needed for one checkbox.
        self.check_for_updates_checkbox = QCheckBox("Check for updates on startup")
        self.check_for_updates_checkbox.setToolTip(
            "When enabled, Anki Miner queries GitHub for new releases on launch."
        )
        layout.addWidget(self.check_for_updates_checkbox)

        # Action buttons at bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)
        button_layout.addStretch()

        self.reset_button = ModernButton("Reset to Defaults", variant="secondary")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.reset_button.setToolTip("Reset all settings to default values (Ctrl+R)")
        button_layout.addWidget(self.reset_button)

        self.save_button = ModernButton("Save Settings", variant="primary")
        self.save_button.clicked.connect(self._on_save_clicked)
        self.save_button.setToolTip("Save settings to disk (Ctrl+S)")
        button_layout.addWidget(self.save_button)

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

        # Dictionary panel signals — wire Add/Reimport to import worker dialogs.
        self.dictionary_panel.add_dict_requested.connect(self._on_add_dict_clicked)
        self.dictionary_panel.reimport_jmdict_requested.connect(self._on_reimport_jmdict_clicked)

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
        """Load current configuration into UI."""
        # Anki settings
        self.anki_panel.deck_input.setText(self.config.anki_deck_name)
        self.anki_panel.note_type_input.setText(self.config.anki_note_type)
        self.anki_panel.ankiconnect_url_input.setText(self.config.ankiconnect_url)

        # Anki card field mappings
        self.anki_panel.set_card_fields(self.config.anki_fields)

        # Media settings
        self.media_panel.audio_padding_spinbox.setValue(self.config.audio_padding)
        self.media_panel.screenshot_offset_spinbox.setValue(self.config.screenshot_offset)
        self.media_panel.max_workers_spinbox.setValue(self.config.max_parallel_workers)

        # Dictionary chain
        self.dictionary_panel.set_chain(self.config.dictionary_chain)

        # Pitch accent settings
        self.dictionary_panel.pitch_accent_selector.set_path(str(self.config.pitch_accent_path))
        self.dictionary_panel.use_pitch_accent_checkbox.setChecked(self.config.use_pitch_accent)

        # Frequency settings
        self.filtering_panel.frequency_selector.set_path(str(self.config.frequency_list_path))
        self.filtering_panel.use_frequency_checkbox.setChecked(self.config.use_frequency_data)
        self.filtering_panel.max_frequency_spinbox.setValue(self.config.max_frequency_rank)

        # Known words database settings
        self.filtering_panel.use_known_words_db_checkbox.setChecked(self.config.use_known_words_db)

        # Word list settings
        if self.config.blacklist_path:
            self.filtering_panel.blacklist_selector.set_path(str(self.config.blacklist_path))
        self.filtering_panel.use_blacklist_checkbox.setChecked(self.config.use_blacklist)
        if self.config.whitelist_path:
            self.filtering_panel.whitelist_selector.set_path(str(self.config.whitelist_path))
        self.filtering_panel.use_whitelist_checkbox.setChecked(self.config.use_whitelist)

        # Subtitle text filtering settings (Issue #8)
        self.filtering_panel.subtitle_regex_edit.setText(self.config.subtitle_regex_filter)
        self.filtering_panel.subtitle_replacement_edit.setText(
            self.config.subtitle_regex_replacement
        )
        self.filtering_panel.use_subtitle_regex_checkbox.setChecked(
            self.config.use_subtitle_regex_filter
        )

        # Deduplication settings
        self.filtering_panel.deduplicate_sentences_checkbox.setChecked(
            self.config.deduplicate_sentences
        )

        # i+1 sentence filter setting
        self.filtering_panel.use_i_plus_one_checkbox.setChecked(self.config.use_i_plus_one_filter)

        # YouTube settings
        self.youtube_panel.set_cookies_from_browser(self.config.youtube_cookies_from_browser)
        self.youtube_panel.set_max_duration_seconds(self.config.youtube_max_duration_s)

        # Update settings
        self.check_for_updates_checkbox.setChecked(self.config.check_for_updates)

    def _on_save_clicked(self) -> None:
        """Handle save button click."""
        # If the user just re-enabled startup checks (False -> True), clear any
        # previously skipped version so a fresh check runs next launch.
        was_enabled = self.config.check_for_updates
        now_enabled = self.check_for_updates_checkbox.isChecked()
        skipped_update_version = self.config.skipped_update_version
        if now_enabled and not was_enabled:
            skipped_update_version = ""

        # Validate subtitle regex filter before saving so we never persist a
        # pattern that crashes the parser. Only validate when the user has
        # enabled the filter; an unchecked invalid pattern is harmless.
        subtitle_regex = self.filtering_panel.subtitle_regex_edit.text()
        use_subtitle_regex = self.filtering_panel.use_subtitle_regex_checkbox.isChecked()
        if use_subtitle_regex and subtitle_regex:
            try:
                re.compile(subtitle_regex)
            except re.error as e:
                QMessageBox.warning(
                    self,
                    "Invalid Subtitle Regex",
                    f"The subtitle regex filter is not a valid pattern:\n\n{e}\n\n"
                    f"Fix the pattern or disable the filter before saving.",
                )
                return

        # Create updated config from all panels
        new_config = replace(
            self.config,
            # Anki settings
            anki_deck_name=self.anki_panel.deck_input.text(),
            anki_note_type=self.anki_panel.note_type_input.text(),
            ankiconnect_url=self.anki_panel.ankiconnect_url_input.text(),
            anki_fields=self.anki_panel.get_card_fields(),
            anki_word_field=self.anki_panel.get_card_fields().get("word", "Expression"),
            # Media settings
            audio_padding=self.media_panel.audio_padding_spinbox.value(),
            screenshot_offset=self.media_panel.screenshot_offset_spinbox.value(),
            max_parallel_workers=self.media_panel.max_workers_spinbox.value(),
            # Dictionary chain — chain is the single source of truth now
            dictionary_chain=self.dictionary_panel.get_chain(),
            # Pitch accent settings
            pitch_accent_path=(
                Path(self.dictionary_panel.pitch_accent_selector.get_path())
                if self.dictionary_panel.pitch_accent_selector.get_path()
                else Path("")
            ),
            use_pitch_accent=self.dictionary_panel.use_pitch_accent_checkbox.isChecked(),
            # Frequency settings
            frequency_list_path=(
                Path(self.filtering_panel.frequency_selector.get_path())
                if self.filtering_panel.frequency_selector.get_path()
                else Path("")
            ),
            use_frequency_data=self.filtering_panel.use_frequency_checkbox.isChecked(),
            max_frequency_rank=self.filtering_panel.max_frequency_spinbox.value(),
            # Known words database settings
            use_known_words_db=self.filtering_panel.use_known_words_db_checkbox.isChecked(),
            # Word list settings
            blacklist_path=(
                Path(self.filtering_panel.blacklist_selector.get_path())
                if self.filtering_panel.blacklist_selector.get_path()
                else None
            ),
            use_blacklist=self.filtering_panel.use_blacklist_checkbox.isChecked(),
            whitelist_path=(
                Path(self.filtering_panel.whitelist_selector.get_path())
                if self.filtering_panel.whitelist_selector.get_path()
                else None
            ),
            use_whitelist=self.filtering_panel.use_whitelist_checkbox.isChecked(),
            # Subtitle text filtering settings (Issue #8)
            subtitle_regex_filter=subtitle_regex,
            subtitle_regex_replacement=self.filtering_panel.subtitle_replacement_edit.text(),
            use_subtitle_regex_filter=use_subtitle_regex,
            # Deduplication settings
            deduplicate_sentences=self.filtering_panel.deduplicate_sentences_checkbox.isChecked(),
            # i+1 sentence filter setting
            use_i_plus_one_filter=self.filtering_panel.use_i_plus_one_checkbox.isChecked(),
            # YouTube settings
            youtube_cookies_from_browser=self.youtube_panel.get_cookies_from_browser(),
            youtube_max_duration_s=self.youtube_panel.get_max_duration_seconds(),
            # Update settings
            check_for_updates=now_enabled,
            skipped_update_version=skipped_update_version,
        )

        # Emit signal to notify listeners of config change
        self.config = new_config
        self.config_changed.emit(new_config)
        QMessageBox.information(
            self,
            "Settings Saved",
            "Settings have been saved successfully",
        )

    def _on_reset_clicked(self) -> None:
        """Handle reset button click."""
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            "Are you sure you want to reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from anki_miner.config import create_default_config

            self.config = create_default_config()
            self._load_config()
            self.config_changed.emit(self.config)
            QMessageBox.information(
                self,
                "Settings Reset",
                "Settings have been reset to defaults",
            )

    # === Status update methods (delegate to panels) ===

    def set_ankiconnect_status(self, connected: bool) -> None:
        """Set the AnkiConnect connection status.

        Args:
            connected: Whether AnkiConnect is connected
        """
        status = "connected" if connected else "disconnected"
        self.anki_panel.set_connection_status(status)

    def set_deck_status(self, exists: bool, message: str = "") -> None:
        """Set the deck validation status.

        Args:
            exists: Whether the deck exists
            message: Optional status message
        """
        self.anki_panel.set_deck_status(exists, message)

    def set_notetype_status(self, exists: bool, message: str = "") -> None:
        """Set the note type validation status.

        Args:
            exists: Whether the note type exists
            message: Optional status message
        """
        self.anki_panel.set_notetype_status(exists, message)

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration from external source.

        Args:
            config: New configuration to load
        """
        self.config = config
        self._load_config()

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

    # === Dictionary import handlers ===

    def _on_add_dict_clicked(self) -> None:
        """Prompt for a Yomitan zip and run the import worker."""
        zip_path_str, _ = QFileDialog.getOpenFileName(
            self, "Choose Yomitan dictionary zip", "", "Yomitan zip (*.zip)"
        )
        if not zip_path_str:
            return

        dest_root = Path.home() / ".anki_miner" / "dicts"
        dlg = QProgressDialog("Importing dictionary…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_yomitan(Path(zip_path_str), dest_root)
        self._active_import_worker = worker  # keep alive across QThread lifetime

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_import_finished(dict_id: str, meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self,
                "Dictionary added",
                f"Imported {dict_id} ({meta.get('entry_count', 0):,} entries)",
            )
            self.dictionary_panel.set_chain(self._with_dict_at_top(dict_id))

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "Import failed", err)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_import_finished)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _with_dict_at_top(self, dict_id: str) -> tuple[ChainEntry, ...]:
        """Return the current chain with ``dict_id`` placed (or moved) to the top."""
        chain = list(self.dictionary_panel.get_chain())
        chain = [e for e in chain if not (e.kind == "indexed" and e.dict_id == dict_id)]
        chain.insert(0, ChainEntry(kind="indexed", dict_id=dict_id, enabled=True))
        return tuple(chain)

    def _on_reimport_jmdict_clicked(self) -> None:
        """Reimport JMdict from the configured XML path."""
        xml = self.config.jmdict_path
        if not xml.exists():
            QMessageBox.warning(
                self,
                "JMdict not found",
                f"No JMdict XML at {xml}. Download from EDRDG and place it there.",
            )
            return

        dest_root = Path.home() / ".anki_miner" / "dicts"
        dlg = QProgressDialog("Reimporting JMdict…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_jmdict(xml, dest_root)
        self._active_import_worker = worker

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(dict_id: str, meta: dict) -> None:
            dlg.close()
            # Re-render chain so the (refreshed) entry count is reflected.
            self.dictionary_panel.set_chain(self.dictionary_panel.get_chain())

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "Reimport failed", err)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()
