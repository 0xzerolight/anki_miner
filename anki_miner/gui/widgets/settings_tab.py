"""Settings tab with category organization using extracted panels."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    MediaSettingsPanel,
)


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
        layout.setSpacing(SPACING.md)
        layout.setContentsMargins(20, 20, 20, 20)

        # Category tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("settings-tabs")

        # Create panels using extracted components
        self.anki_panel = AnkiSettingsPanel()
        self.media_panel = MediaSettingsPanel()
        self.dictionary_panel = DictionarySettingsPanel()
        self.filtering_panel = FilteringSettingsPanel()

        # Add tabs with scroll areas for each panel
        self.tab_widget.addTab(
            self._wrap_in_scroll_area(self.anki_panel), f"{IconProvider.get_icon('card')} Anki"
        )
        self.tab_widget.addTab(
            self._wrap_in_scroll_area(self.media_panel), f"{IconProvider.get_icon('video')} Media"
        )
        self.tab_widget.addTab(
            self._wrap_in_scroll_area(self.dictionary_panel),
            f"{IconProvider.get_icon('word')} Dictionary",
        )
        self.tab_widget.addTab(
            self._wrap_in_scroll_area(self.filtering_panel),
            f"{IconProvider.get_icon('filter')} Filtering",
        )

        layout.addWidget(self.tab_widget)

        # Action buttons at bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)
        button_layout.addStretch()

        self.reset_button = ModernButton("Reset to Defaults", icon="refresh", variant="secondary")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.reset_button.setToolTip("Reset all settings to default values (Ctrl+R)")
        button_layout.addWidget(self.reset_button)

        self.save_button = ModernButton("Save Settings", icon="save", variant="primary")
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

        # Dictionary settings
        self.dictionary_panel.jmdict_selector.set_path(str(self.config.jmdict_path))
        self.dictionary_panel.use_offline_checkbox.setChecked(self.config.use_offline_dict)

        # Pitch accent settings
        self.dictionary_panel.pitch_accent_selector.set_path(str(self.config.pitch_accent_path))
        self.dictionary_panel.use_pitch_accent_checkbox.setChecked(self.config.use_pitch_accent)

        # Filtering settings
        self.filtering_panel.min_length_spinbox.setValue(self.config.min_word_length)

        # Frequency settings
        self.filtering_panel.frequency_selector.set_path(str(self.config.frequency_list_path))
        self.filtering_panel.use_frequency_checkbox.setChecked(self.config.use_frequency_data)
        self.filtering_panel.max_frequency_spinbox.setValue(self.config.max_frequency_rank)

    def _on_save_clicked(self) -> None:
        """Handle save button click."""
        # Create updated config from all panels
        new_config = replace(
            self.config,
            # Anki settings
            anki_deck_name=self.anki_panel.deck_input.text(),
            anki_note_type=self.anki_panel.note_type_input.text(),
            ankiconnect_url=self.anki_panel.ankiconnect_url_input.text(),
            anki_fields=self.anki_panel.get_card_fields(),
            # Media settings
            audio_padding=self.media_panel.audio_padding_spinbox.value(),
            screenshot_offset=self.media_panel.screenshot_offset_spinbox.value(),
            max_parallel_workers=self.media_panel.max_workers_spinbox.value(),
            # Dictionary settings
            jmdict_path=(
                Path(self.dictionary_panel.jmdict_selector.get_path())
                if self.dictionary_panel.jmdict_selector.get_path()
                else Path("")
            ),
            use_offline_dict=self.dictionary_panel.use_offline_checkbox.isChecked(),
            # Pitch accent settings
            pitch_accent_path=(
                Path(self.dictionary_panel.pitch_accent_selector.get_path())
                if self.dictionary_panel.pitch_accent_selector.get_path()
                else Path("")
            ),
            use_pitch_accent=self.dictionary_panel.use_pitch_accent_checkbox.isChecked(),
            # Filtering settings
            min_word_length=self.filtering_panel.min_length_spinbox.value(),
            # Frequency settings
            frequency_list_path=(
                Path(self.filtering_panel.frequency_selector.get_path())
                if self.filtering_panel.frequency_selector.get_path()
                else Path("")
            ),
            use_frequency_data=self.filtering_panel.use_frequency_checkbox.isChecked(),
            max_frequency_rank=self.filtering_panel.max_frequency_spinbox.value(),
        )

        # Emit signal to notify listeners of config change
        self.config = new_config
        self.config_changed.emit(new_config)
        QMessageBox.information(
            self,
            "Settings Saved",
            f"{IconProvider.get_icon('success')} Settings have been saved successfully",
        )

    def _on_reset_clicked(self) -> None:
        """Handle reset button click."""
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            f"{IconProvider.get_icon('warning')} Are you sure you want to reset all settings to defaults?",
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
                f"{IconProvider.get_icon('success')} Settings have been reset to defaults",
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

    @property
    def jmdict_selector(self):
        """Get JMdict file selector widget."""
        return self.dictionary_panel.jmdict_selector

    @property
    def use_offline_checkbox(self):
        """Get use offline checkbox widget."""
        return self.dictionary_panel.use_offline_checkbox

    @property
    def min_length_spinbox(self):
        """Get min length spinbox widget."""
        return self.filtering_panel.min_length_spinbox
