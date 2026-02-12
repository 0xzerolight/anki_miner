"""Anki configuration settings panel."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import FormPanel, StatusBadge, make_label_fit_text
from anki_miner.gui.widgets.enhanced import ModernButton


class AnkiSettingsPanel(FormPanel):
    """Panel for Anki connection and configuration settings.

    Provides:
    - Deck name input with sync button
    - Note type input with sync button
    - AnkiConnect URL configuration
    - Connection status indicator
    - Test connection button
    - Card field mappings

    Signals:
        deck_sync_requested: Emitted when deck sync is requested
        notetype_sync_requested: Emitted when note type sync is requested
        test_connection_requested: Emitted when connection test is requested
    """

    deck_sync_requested = pyqtSignal()
    notetype_sync_requested = pyqtSignal()
    test_connection_requested = pyqtSignal()

    # Dynamically created by _add_labeled_field_with_button via setattr
    deck_input: QLineEdit
    note_type_input: QLineEdit
    deck_sync_button: ModernButton
    notetype_sync_button: ModernButton

    def __init__(self, parent=None):
        """Initialize the Anki settings panel."""
        super().__init__("Anki Configuration", icon="card", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Connection status badge
        self.connection_status = StatusBadge("AnkiConnect", status="checking", clickable=False)
        self.add_widget(self.connection_status)

        self.add_spacing(SPACING.xs)

        # AnkiConnect URL
        self.ankiconnect_url_input = QLineEdit()
        self.ankiconnect_url_input.setPlaceholderText("http://localhost:8765")
        self.ankiconnect_url_input.setToolTip("URL of AnkiConnect API endpoint")
        self.add_field(
            "AnkiConnect URL",
            self.ankiconnect_url_input,
            helper="Default is http://localhost:8765. Change if AnkiConnect is on a different port",
        )

        # Test connection button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.test_connection_button = ModernButton(
            "Test Connection", icon="success", variant="secondary"
        )
        self.test_connection_button.clicked.connect(self._on_test_connection)
        self.test_connection_button.setToolTip("Test connection to AnkiConnect")
        button_layout.addWidget(self.test_connection_button)

        self.add_layout(button_layout)

        # General helper text
        helper = QLabel("Make sure Anki is running with AnkiConnect installed")
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        self.add_widget(helper)

        self.add_spacing(SPACING.sm)

        # Deck name with sync button
        self._add_labeled_field_with_button(
            label_text="Deck Name:",
            input_widget_name="deck_input",
            placeholder="Enter deck name...",
            tooltip="Name of the Anki deck where cards will be created",
            button_name="deck_sync_button",
            button_tooltip="Sync deck list from Anki",
            button_callback=self._on_deck_sync,
            helper_text="Target deck where Anki cards will be created",
        )

        # Deck status
        self.deck_status = QLabel()
        self.deck_status.setObjectName("validation-status")
        self.add_widget(self.deck_status)

        self.add_spacing(SPACING.xs)

        # Note type with sync button
        self._add_labeled_field_with_button(
            label_text="Note Type:",
            input_widget_name="note_type_input",
            placeholder="Enter note type name...",
            tooltip="Name of the Anki note type to use",
            button_name="notetype_sync_button",
            button_tooltip="Sync note type list from Anki",
            button_callback=self._on_notetype_sync,
            helper_text="Anki note type that contains your card fields",
        )

        # Note type status
        self.notetype_status = QLabel()
        self.notetype_status.setObjectName("validation-status")
        self.add_widget(self.notetype_status)

        self.add_spacing(SPACING.sm)

        # Card Field Mappings section
        self.add_section("Card Field Mappings")

        # Helper text for card fields
        card_fields_helper = QLabel(
            "Map your data to Anki note fields. " "Field names must match your note type exactly."
        )
        card_fields_helper.setObjectName("helper-text")
        card_fields_helper.setWordWrap(True)
        self.add_widget(card_fields_helper)

        self.add_spacing(SPACING.xs)

        # Expression field (word)
        self.expression_field_input = QLineEdit()
        self.expression_field_input.setPlaceholderText("Expression")
        self.expression_field_input.setToolTip("Anki field for the Japanese word/expression")
        self._add_simple_field(
            "Expression Field",
            self.expression_field_input,
            "Anki field that stores the Japanese word",
        )

        # Sentence field
        self.sentence_field_input = QLineEdit()
        self.sentence_field_input.setPlaceholderText("Sentence")
        self.sentence_field_input.setToolTip("Anki field for the example sentence")
        self._add_simple_field(
            "Sentence Field",
            self.sentence_field_input,
            "Anki field that stores the example sentence from the video",
        )

        # Definition field
        self.definition_field_input = QLineEdit()
        self.definition_field_input.setPlaceholderText("MainDefinition")
        self.definition_field_input.setToolTip("Anki field for the word definition")
        self._add_simple_field(
            "Definition Field",
            self.definition_field_input,
            "Anki field that stores the English definition",
        )

        # Picture field
        self.picture_field_input = QLineEdit()
        self.picture_field_input.setPlaceholderText("Picture")
        self.picture_field_input.setToolTip("Anki field for the screenshot")
        self._add_simple_field(
            "Picture Field", self.picture_field_input, "Anki field that stores the video screenshot"
        )

        # Audio field
        self.audio_field_input = QLineEdit()
        self.audio_field_input.setPlaceholderText("SentenceAudio")
        self.audio_field_input.setToolTip("Anki field for the audio clip")
        self._add_simple_field(
            "Audio Field", self.audio_field_input, "Anki field that stores the sentence audio clip"
        )

        # Expression Furigana field
        self.expression_furigana_field_input = QLineEdit()
        self.expression_furigana_field_input.setPlaceholderText("ExpressionFurigana")
        self.expression_furigana_field_input.setToolTip("Anki field for expression with furigana")
        self._add_simple_field(
            "Expression Furigana Field",
            self.expression_furigana_field_input,
            "Anki field that stores the expression with furigana reading",
        )

        # Sentence Furigana field
        self.sentence_furigana_field_input = QLineEdit()
        self.sentence_furigana_field_input.setPlaceholderText("SentenceFurigana")
        self.sentence_furigana_field_input.setToolTip("Anki field for sentence with furigana")
        self._add_simple_field(
            "Sentence Furigana Field",
            self.sentence_furigana_field_input,
            "Anki field that stores the sentence with furigana readings",
        )

    def _add_labeled_field_with_button(
        self,
        label_text: str,
        input_widget_name: str,
        placeholder: str,
        tooltip: str,
        button_name: str,
        button_tooltip: str,
        button_callback,
        helper_text: str = "",
    ) -> None:
        """Add a labeled field with an inline button.

        Args:
            label_text: Label text (including colon)
            input_widget_name: Attribute name for the input widget
            placeholder: Placeholder text for input
            tooltip: Tooltip for input
            button_name: Attribute name for the button
            button_tooltip: Tooltip for button
            button_callback: Callback for button click
            helper_text: Optional helper text below the field
        """
        # Label
        label = QLabel(label_text)
        label.setObjectName("field-label")
        make_label_fit_text(label)
        self.add_widget(label)

        # Container for input + button
        container = QHBoxLayout()
        container.setSpacing(SPACING.xs)

        # Input
        input_widget = QLineEdit()
        input_widget.setPlaceholderText(placeholder)
        input_widget.setToolTip(tooltip)
        container.addWidget(input_widget, 1)
        setattr(self, input_widget_name, input_widget)

        # Sync button
        sync_button = ModernButton("", icon="refresh", variant="ghost")
        sync_button.clicked.connect(button_callback)
        sync_button.setToolTip(button_tooltip)
        sync_button.setMaximumWidth(40)
        container.addWidget(sync_button)
        setattr(self, button_name, sync_button)

        self.add_layout(container)

        # Helper text
        if helper_text:
            helper = QLabel(helper_text)
            helper.setObjectName("helper-text")
            helper.setWordWrap(True)
            helper_font = QFont()
            helper_font.setPixelSize(FONT_SIZES.small)
            helper.setFont(helper_font)
            self.add_widget(helper)

    def _add_simple_field(
        self, label_text: str, input_widget: QLineEdit, helper_text: str = ""
    ) -> None:
        """Add a simple labeled field to the main layout.

        Args:
            label_text: Label text (will add colon automatically)
            input_widget: The QLineEdit widget to add
            helper_text: Optional helper text below the field
        """
        # Label
        label = QLabel(f"{label_text}:")
        label.setObjectName("field-label")
        make_label_fit_text(label)
        self.add_widget(label)

        # Input widget
        self.add_widget(input_widget)

        # Helper text
        if helper_text:
            helper = QLabel(helper_text)
            helper.setObjectName("helper-text")
            helper.setWordWrap(True)
            helper_font = QFont()
            helper_font.setPixelSize(FONT_SIZES.small)
            helper.setFont(helper_font)
            self.add_widget(helper)

    def _on_deck_sync(self) -> None:
        """Handle deck sync button click."""
        self.set_deck_status(None, "Syncing deck list...")
        self.deck_sync_requested.emit()

    def _on_notetype_sync(self) -> None:
        """Handle note type sync button click."""
        self.set_notetype_status(None, "Syncing note type list...")
        self.notetype_sync_requested.emit()

    def _on_test_connection(self) -> None:
        """Handle test connection button click."""
        self.set_connection_status("checking")
        self.test_connection_requested.emit()

    def set_connection_status(self, status: str) -> None:
        """Update the connection status.

        Args:
            status: Status string (connected, disconnected, checking, unknown)
        """
        status_map = {
            "connected": ("success", "Connected to AnkiConnect"),
            "disconnected": ("error", "Not connected to AnkiConnect"),
            "checking": ("checking", "Checking connection..."),
            "unknown": ("info", "Connection status unknown"),
        }
        badge_status, text = status_map.get(status, ("info", "Unknown"))
        self.connection_status.set_name(text.split(" to ")[0] if " to " in text else text)
        self.connection_status.set_status(badge_status, text)

    def set_deck_status(self, exists: bool | None, message: str = "") -> None:
        """Update the deck validation status.

        Args:
            exists: Whether the deck exists (None for checking)
            message: Status message
        """
        if exists is None:
            icon = IconProvider.get_icon("progress")
            self.deck_status.setText(f"{icon} {message or 'Checking...'}")
            self.deck_status.setProperty("status", "checking")
        elif exists:
            icon = IconProvider.get_icon("success")
            self.deck_status.setText(f"{icon} {message or 'Deck exists'}")
            self.deck_status.setProperty("status", "success")
        else:
            icon = IconProvider.get_icon("error")
            self.deck_status.setText(f"{icon} {message or 'Deck not found'}")
            self.deck_status.setProperty("status", "error")

        if style := self.deck_status.style():
            style.unpolish(self.deck_status)
            style.polish(self.deck_status)

    def set_notetype_status(self, exists: bool | None, message: str = "") -> None:
        """Update the note type validation status.

        Args:
            exists: Whether the note type exists (None for checking)
            message: Status message
        """
        if exists is None:
            icon = IconProvider.get_icon("progress")
            self.notetype_status.setText(f"{icon} {message or 'Checking...'}")
            self.notetype_status.setProperty("status", "checking")
        elif exists:
            icon = IconProvider.get_icon("success")
            self.notetype_status.setText(f"{icon} {message or 'Note type exists'}")
            self.notetype_status.setProperty("status", "success")
        else:
            icon = IconProvider.get_icon("error")
            self.notetype_status.setText(f"{icon} {message or 'Note type not found'}")
            self.notetype_status.setProperty("status", "error")

        if style := self.notetype_status.style():
            style.unpolish(self.notetype_status)
            style.polish(self.notetype_status)

    # Getters for card field values
    def get_card_fields(self) -> dict:
        """Get the card field mappings.

        Returns:
            Dictionary mapping data types to Anki field names
        """
        return {
            "word": self.expression_field_input.text() or "Expression",
            "sentence": self.sentence_field_input.text() or "Sentence",
            "definition": self.definition_field_input.text() or "MainDefinition",
            "picture": self.picture_field_input.text() or "Picture",
            "audio": self.audio_field_input.text() or "SentenceAudio",
            "expression_furigana": self.expression_furigana_field_input.text()
            or "ExpressionFurigana",
            "sentence_furigana": self.sentence_furigana_field_input.text() or "SentenceFurigana",
        }

    def set_card_fields(self, fields: dict) -> None:
        """Set the card field mappings.

        Args:
            fields: Dictionary mapping data types to Anki field names
        """
        self.expression_field_input.setText(fields.get("word", "Expression"))
        self.sentence_field_input.setText(fields.get("sentence", "Sentence"))
        self.definition_field_input.setText(fields.get("definition", "MainDefinition"))
        self.picture_field_input.setText(fields.get("picture", "Picture"))
        self.audio_field_input.setText(fields.get("audio", "SentenceAudio"))
        self.expression_furigana_field_input.setText(
            fields.get("expression_furigana", "ExpressionFurigana")
        )
        self.sentence_furigana_field_input.setText(
            fields.get("sentence_furigana", "SentenceFurigana")
        )
