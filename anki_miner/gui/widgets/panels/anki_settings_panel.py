"""Anki configuration settings panel."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import FormPanel, StatusBadge, make_label_fit_text
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.services.dictionary.card_style_presets import PRESETS


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
    fetch_fields_requested = pyqtSignal()
    apply_styling_requested = pyqtSignal()
    remove_styling_requested = pyqtSignal()

    # Dynamically created by _add_labeled_field_with_button via setattr
    deck_input: QLineEdit
    note_type_input: QLineEdit
    deck_sync_button: ModernButton
    notetype_sync_button: ModernButton

    def __init__(self, parent=None):
        """Initialize the Anki settings panel."""
        super().__init__("Anki Configuration", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Connection status badge
        self.connection_status = StatusBadge("AnkiConnect", status="checking", clickable=False)
        self.add_widget(self.connection_status)

        # AnkiConnect URL
        self.ankiconnect_url_input = QLineEdit()
        self.ankiconnect_url_input.setPlaceholderText("http://localhost:8765")
        self.add_field(
            self.tr("AnkiConnect URL"),
            self.ankiconnect_url_input,
            helper=self.tr("Default http://localhost:8765. Change if AnkiConnect uses a different port."),
        )

        # Card tags
        self.anki_tags_input = QLineEdit()
        self.add_field(
            self.tr("Card tags"),
            self.anki_tags_input,
            helper=self.tr("Space-separated tags applied to every mined card. Leave blank for no tags."),
        )

        # Test connection button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.test_connection_button = ModernButton(self.tr("Test Connection"), variant="secondary")
        self.test_connection_button.setToolTip(self.tr("Anki must be running with AnkiConnect installed."))
        self.test_connection_button.clicked.connect(self._on_test_connection)
        button_layout.addWidget(self.test_connection_button)

        self.add_layout(button_layout)

        # Deck name with sync button
        self._add_labeled_field_with_button(
            label_text=self.tr("Deck Name"),
            input_widget_name="deck_input",
            placeholder=self.tr("Enter deck name..."),
            tooltip="",
            button_name="deck_sync_button",
            button_tooltip=self.tr("Sync deck list from Anki"),
            button_callback=self._on_deck_sync,
            helper_text=self.tr("Target deck for new cards."),
        )

        # Deck status
        self.deck_status = QLabel()
        self.deck_status.setObjectName("validation-status")
        self.add_widget(self.deck_status)

        # Note type with sync button
        self._add_labeled_field_with_button(
            label_text=self.tr("Note Type"),
            input_widget_name="note_type_input",
            placeholder=self.tr("Enter note type name..."),
            tooltip="",
            button_name="notetype_sync_button",
            button_tooltip=self.tr("Sync note type list from Anki"),
            button_callback=self._on_notetype_sync,
            helper_text=self.tr("Anki note type whose fields you'll map below."),
        )

        # Note type status
        self.notetype_status = QLabel()
        self.notetype_status.setObjectName("validation-status")
        self.add_widget(self.notetype_status)

        # Card Field Mappings section
        self.add_section(self.tr("Card Field Mappings"))

        # Helper text for card fields
        card_fields_helper = QLabel(self.tr("Map data to note fields (names must match exactly). Blank = skip."))
        card_fields_helper.setObjectName("helper-text")
        card_fields_helper.setWordWrap(True)
        self.add_widget(card_fields_helper)

        # Fetch fields from note type button
        fetch_layout = QHBoxLayout()
        fetch_layout.addStretch()
        self.fetch_fields_button = ModernButton(self.tr("Fetch Fields from Note Type"), variant="secondary")
        self.fetch_fields_button.setToolTip(
            self.tr("Query AnkiConnect for the note type's field names and auto-map them")
        )
        self.fetch_fields_button.clicked.connect(self._on_fetch_fields)
        fetch_layout.addWidget(self.fetch_fields_button)
        self.add_layout(fetch_layout)

        # Expression field (word)
        self.expression_field_input = QLineEdit()
        self.expression_field_input.setPlaceholderText("Expression")
        self.add_field(
            self.tr("Expression Field"), self.expression_field_input, helper=self.tr("Stores the mined Japanese word.")
        )

        # Sentence field
        self.sentence_field_input = QLineEdit()
        self.sentence_field_input.setPlaceholderText("Sentence")
        self.add_field(
            self.tr("Sentence Field"),
            self.sentence_field_input,
            helper=self.tr("Stores the example sentence from the subtitle."),
        )

        # Definition field
        self.definition_field_input = QLineEdit()
        self.definition_field_input.setPlaceholderText("MainDefinition")
        self.add_field(
            self.tr("Definition Field"),
            self.definition_field_input,
            helper=self.tr("Stores the English definition from the dictionary chain."),
        )

        # Glossary field (second definition slot — receives concatenated hits
        # from every enabled dictionary; Senren-toggle compatible).
        self.glossary_field_input = QLineEdit()
        self.glossary_field_input.setPlaceholderText("Glossary")
        self.add_field(
            self.tr("Glossary Field"),
            self.glossary_field_input,
            helper=self.tr("Concatenated hits from every enabled dictionary as Yomitan HTML."),
        )

        # Picture field
        self.picture_field_input = QLineEdit()
        self.picture_field_input.setPlaceholderText("Picture")
        self.add_field(self.tr("Picture Field"), self.picture_field_input, helper=self.tr("Stores the screenshot."))

        # Audio field
        self.audio_field_input = QLineEdit()
        self.audio_field_input.setPlaceholderText("SentenceAudio")
        self.add_field(
            self.tr("Audio Field"), self.audio_field_input, helper=self.tr("Stores the sentence audio clip.")
        )

        # Expression audio field (Issue #73). Field-name presence is the on/off
        # switch (like Frequency/Pitch) — leave blank to disable. Sources are
        # ordered under Audio settings (packs first, JapanesePod101 fallback).
        self.expression_audio_field_input = QLineEdit()
        self.expression_audio_field_input.setPlaceholderText("ExpressionAudio")
        self.add_field(
            self.tr("Expression Audio Field"),
            self.expression_audio_field_input,
            helper=self.tr(
                "Stores the word pronunciation audio clip; leave blank to disable. "
                "Sources are configured under Audio settings."
            ),
        )

        # Expression Furigana field
        self.expression_furigana_field_input = QLineEdit()
        self.expression_furigana_field_input.setPlaceholderText("ExpressionFurigana")
        self.add_field(
            self.tr("Expression Furigana Field"),
            self.expression_furigana_field_input,
            helper=self.tr("Stores the expression with furigana readings."),
        )

        # Expression Reading field (plain kana)
        self.expression_reading_field_input = QLineEdit()
        self.expression_reading_field_input.setPlaceholderText("ExpressionReading")
        self.add_field(
            self.tr("Expression Reading Field"),
            self.expression_reading_field_input,
            helper=self.tr("Stores the expression as plain kana."),
        )

        # Sentence Furigana field
        self.sentence_furigana_field_input = QLineEdit()
        self.sentence_furigana_field_input.setPlaceholderText("SentenceFurigana")
        self.add_field(
            self.tr("Sentence Furigana Field"),
            self.sentence_furigana_field_input,
            helper=self.tr("Stores the sentence with furigana readings."),
        )

        # Sentence Reading field (plain kana)
        self.sentence_reading_field_input = QLineEdit()
        self.sentence_reading_field_input.setPlaceholderText("SentenceReading")
        self.add_field(
            self.tr("Sentence Reading Field"),
            self.sentence_reading_field_input,
            helper=self.tr("Stores the sentence as plain kana."),
        )

        # Auxiliary Data Fields section
        self.add_section(self.tr("Auxiliary Data Fields"))

        auxiliary_helper = QLabel(self.tr("Need pitch_accent.csv / frequency.csv in ~/.anki_miner/. Blank = skip."))
        auxiliary_helper.setObjectName("helper-text")
        auxiliary_helper.setWordWrap(True)
        self.add_widget(auxiliary_helper)

        # Pitch Position field
        self.pitch_position_field_input = QLineEdit()
        self.pitch_position_field_input.setPlaceholderText("PitchPosition")
        self.add_field(
            self.tr("Pitch Position Field"),
            self.pitch_position_field_input,
            helper=self.tr("Stores the numeric pitch drop position."),
        )

        # Pitch Category field
        self.pitch_category_field_input = QLineEdit()
        self.pitch_category_field_input.setPlaceholderText("PitchCategory")
        self.add_field(
            self.tr("Pitch Category Field"),
            self.pitch_category_field_input,
            helper=self.tr("Stores the pitch category label."),
        )

        # Pitch Category format (jp vs romaji)
        self.pitch_category_format_combo = QComboBox()
        self.pitch_category_format_combo.addItem(self.tr("Japanese (平板/頭高/中高/尾高/起伏)"), "jp")
        self.pitch_category_format_combo.addItem(self.tr("Romaji (heiban/atamadaka/nakadaka/odaka/kifuku)"), "romaji")
        self.add_field(
            self.tr("Pitch Category Format"),
            self.pitch_category_format_combo,
            helper=self.tr("Romaji matches Yomitan/Lapis CSS; Japanese for legacy notes."),
        )

        # Frequency field
        self.frequency_field_input = QLineEdit()
        self.frequency_field_input.setPlaceholderText("Frequency")
        self.add_field(
            self.tr("Frequency Field"), self.frequency_field_input, helper=self.tr("Stores the word frequency rank.")
        )

        # Source field
        self.source_field_input = QLineEdit()
        self.source_field_input.setPlaceholderText("Source")
        self.add_field(
            self.tr("Source Field"),
            self.source_field_input,
            helper=self.tr("Stores the show/episode and timestamp the word came from. Blank = skip."),
        )

        # Card Styling section (Issue #44)
        self.add_section(self.tr("Card Styling"))

        styling_helper = QLabel(
            self.tr(
                '"Apply to Note Type" writes a managed CSS block via AnkiConnect (never touches your own CSS; '
                '"Remove" reverts cleanly). Custom CSS is appended after the selected preset.'
            )
        )
        styling_helper.setObjectName("helper-text")
        styling_helper.setWordWrap(True)
        self.add_widget(styling_helper)

        preset_label = QLabel(self.tr("Card style preset:"))
        preset_label.setObjectName("field-label")
        make_label_fit_text(preset_label)
        self.add_widget(preset_label)

        self.card_style_preset_combo = QComboBox()
        for preset in PRESETS:
            self.card_style_preset_combo.addItem(preset.display_name, preset.id)
        self.card_style_preset_combo.setToolTip(
            self.tr("Pick a bundled preset; your custom CSS below is appended after it.")
        )
        self.add_widget(self.card_style_preset_combo)

        css_label = QLabel(self.tr("Custom CSS:"))
        css_label.setObjectName("field-label")
        make_label_fit_text(css_label)
        self.add_widget(css_label)

        self.custom_css_edit = QPlainTextEdit()
        self.custom_css_edit.setPlaceholderText(
            "/* Appended after the selected preset. Example: */\n"
            '[data-sc-content|="example-sentence"] { display: none; }'
        )
        mono_font = QFont("monospace")
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        mono_font.setPixelSize(FONT_SIZES.small)
        self.custom_css_edit.setToolTip(
            self.tr(
                "Published Yomitan/Jitendex snippets work verbatim. "
                "Re-import a dictionary to refresh its data-sc-* hooks on older entries."
            )
        )
        self.custom_css_edit.setFont(mono_font)
        self.custom_css_edit.setMinimumHeight(120)
        self.custom_css_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.add_widget(self.custom_css_edit)

        styling_button_layout = QHBoxLayout()
        styling_button_layout.addStretch()
        self.apply_styling_button = ModernButton(self.tr("Apply to Note Type"), variant="primary")
        self.apply_styling_button.setToolTip(self.tr("Write the managed CSS block into the note type via AnkiConnect"))
        self.apply_styling_button.clicked.connect(self._on_apply_styling)
        styling_button_layout.addWidget(self.apply_styling_button)
        self.remove_styling_button = ModernButton(self.tr("Remove Anki Miner Styles"), variant="secondary")
        self.remove_styling_button.setToolTip(self.tr("Strip Anki Miner's managed CSS block from the note type"))
        self.remove_styling_button.clicked.connect(self._on_remove_styling)
        styling_button_layout.addWidget(self.remove_styling_button)
        self.add_layout(styling_button_layout)

        self.styling_status = QLabel()
        self.styling_status.setObjectName("validation-status")
        self.add_widget(self.styling_status)

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
        """Add a labeled input + inline sync button as a single compact form row.

        The input and button are wrapped in a container widget so the whole pair
        sits in one ``add_field`` row (label beside control), matching the other
        densified settings panels. Helper text becomes the field's hover tooltip.

        Args:
            label_text: Label text (no colon; ``add_field`` appends it)
            input_widget_name: Attribute name for the input widget
            placeholder: Placeholder text for input
            tooltip: Tooltip for input
            button_name: Attribute name for the button
            button_tooltip: Tooltip for button
            button_callback: Callback for button click
            helper_text: Optional helper text shown as a tooltip on the field
        """
        # Container for input + button
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        # Input
        input_widget = QLineEdit()
        input_widget.setPlaceholderText(placeholder)
        # Put the helper on the input itself: add_field sets it on the wrapping
        # container, but the input + button cover the container with zero margins
        # and Qt tooltips don't propagate to children, so the container tooltip
        # is unreachable on hover. Fall back to the explicit tooltip when given.
        input_widget.setToolTip(tooltip or helper_text)
        row.addWidget(input_widget, 1)
        setattr(self, input_widget_name, input_widget)

        # Sync button
        sync_button = ModernButton("", variant="ghost")
        sync_button.clicked.connect(button_callback)
        sync_button.setToolTip(button_tooltip)
        sync_button.setMaximumWidth(40)
        row.addWidget(sync_button)
        setattr(self, button_name, sync_button)

        self.add_field(label_text, container, helper=helper_text)

    def _on_deck_sync(self) -> None:
        """Handle deck sync button click."""
        self.set_deck_status(None, self.tr("Syncing deck list..."))
        self.deck_sync_requested.emit()

    def _on_notetype_sync(self) -> None:
        """Handle note type sync button click."""
        self.set_notetype_status(None, self.tr("Syncing note type list..."))
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
            "connected": ("success", self.tr("Connected to AnkiConnect")),
            "disconnected": ("error", self.tr("Not connected to AnkiConnect")),
            "checking": ("checking", self.tr("Checking connection...")),
            "unknown": ("info", self.tr("Connection status unknown")),
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
            self.deck_status.setText(message or self.tr("Checking..."))
            self.deck_status.setProperty("status", "checking")
        elif exists:
            self.deck_status.setText(message or self.tr("Deck exists"))
            self.deck_status.setProperty("status", "success")
        else:
            self.deck_status.setText(message or self.tr("Deck not found"))
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
            self.notetype_status.setText(message or self.tr("Checking..."))
            self.notetype_status.setProperty("status", "checking")
        elif exists:
            self.notetype_status.setText(message or self.tr("Note type exists"))
            self.notetype_status.setProperty("status", "success")
        else:
            self.notetype_status.setText(message or self.tr("Note type not found"))
            self.notetype_status.setProperty("status", "error")

        if style := self.notetype_status.style():
            style.unpolish(self.notetype_status)
            style.polish(self.notetype_status)

    def _on_fetch_fields(self) -> None:
        """Handle fetch fields button click."""
        self.fetch_fields_requested.emit()

    def populate_from_field_list(self, field_names: list[str]) -> None:
        """Auto-map fetched field names to the card field inputs.

        Tries to match fetched field names to known data types using
        common naming patterns.

        Args:
            field_names: List of field names from AnkiConnect
        """
        # Mapping of data keys to input widgets and keywords to match
        mapping = {
            "word": (self.expression_field_input, ["expression", "word", "vocab"]),
            "sentence": (self.sentence_field_input, ["sentence", "context", "example"]),
            "definition": (
                self.definition_field_input,
                ["definition", "meaning", "maindefinition"],
            ),
            "glossary": (
                self.glossary_field_input,
                ["glossary", "definitions", "dictionary"],
            ),
            "picture": (self.picture_field_input, ["picture", "image", "screenshot", "photo"]),
            "audio": (self.audio_field_input, ["audio", "sound", "sentenceaudio"]),
            "expression_audio": (
                self.expression_audio_field_input,
                ["expressionaudio", "wordaudio"],
            ),
            "expression_furigana": (
                self.expression_furigana_field_input,
                ["expressionfurigana", "wordfurigana"],
            ),
            "expression_reading": (
                self.expression_reading_field_input,
                ["expressionreading", "wordreading", "reading"],
            ),
            "sentence_furigana": (
                self.sentence_furigana_field_input,
                ["sentencefurigana", "contextfurigana"],
            ),
            "sentence_reading": (
                self.sentence_reading_field_input,
                ["sentencereading", "contextreading"],
            ),
            "pitch_position": (
                self.pitch_position_field_input,
                ["pitchposition", "pitchaccent", "pitch"],
            ),
            "pitch_category": (
                self.pitch_category_field_input,
                ["pitchcategory", "accenttype", "accentcategory"],
            ),
            "frequency": (
                self.frequency_field_input,
                ["frequency", "freq", "rank", "frequencyrank"],
            ),
            "source": (
                self.source_field_input,
                ["source", "origin"],
            ),
        }

        for _key, (widget, keywords) in mapping.items():
            for field_name in field_names:
                if field_name.lower().replace(" ", "").replace("_", "") in [kw.lower() for kw in keywords]:
                    widget.setText(field_name)
                    break

    # Getters for card field values
    def get_card_fields(self) -> dict:
        """Get the card field mappings.

        Returns:
            Dictionary mapping data types to Anki field names.
            Empty string values mean "skip this field during card creation".
        """
        return {
            "word": self.expression_field_input.text().strip(),
            "sentence": self.sentence_field_input.text().strip(),
            "definition": self.definition_field_input.text().strip(),
            "glossary": self.glossary_field_input.text().strip(),
            "picture": self.picture_field_input.text().strip(),
            "audio": self.audio_field_input.text().strip(),
            "expression_audio": self.expression_audio_field_input.text().strip(),
            "expression_furigana": self.expression_furigana_field_input.text().strip(),
            "expression_reading": self.expression_reading_field_input.text().strip(),
            "sentence_furigana": self.sentence_furigana_field_input.text().strip(),
            "sentence_reading": self.sentence_reading_field_input.text().strip(),
            "pitch_position": self.pitch_position_field_input.text().strip(),
            "pitch_category": self.pitch_category_field_input.text().strip(),
            "frequency": self.frequency_field_input.text().strip(),
            "source": self.source_field_input.text().strip(),
        }

    def set_card_fields(self, fields: Mapping[str, str]) -> None:
        """Set the card field mappings.

        Args:
            fields: Dictionary mapping data types to Anki field names
        """
        self.expression_field_input.setText(fields.get("word", "Expression"))
        self.sentence_field_input.setText(fields.get("sentence", "Sentence"))
        self.definition_field_input.setText(fields.get("definition", "MainDefinition"))
        self.glossary_field_input.setText(fields.get("glossary", ""))
        self.picture_field_input.setText(fields.get("picture", "Picture"))
        self.audio_field_input.setText(fields.get("audio", "SentenceAudio"))
        self.expression_audio_field_input.setText(fields.get("expression_audio", ""))
        self.expression_furigana_field_input.setText(fields.get("expression_furigana", "ExpressionFurigana"))
        self.expression_reading_field_input.setText(fields.get("expression_reading", ""))
        self.sentence_furigana_field_input.setText(fields.get("sentence_furigana", "SentenceFurigana"))
        self.sentence_reading_field_input.setText(fields.get("sentence_reading", ""))
        self.pitch_position_field_input.setText(fields.get("pitch_position", ""))
        self.pitch_category_field_input.setText(fields.get("pitch_category", ""))
        self.frequency_field_input.setText(fields.get("frequency", ""))
        self.source_field_input.setText(fields.get("source", ""))

    def get_pitch_category_format(self) -> Literal["jp", "romaji"]:
        """Return the selected pitch category format ("jp" or "romaji")."""
        value = self.pitch_category_format_combo.currentData()
        if value == "romaji":
            return "romaji"
        return "jp"

    def set_pitch_category_format(self, value: str) -> None:
        """Select the pitch category format dropdown by value."""
        target = cast(Literal["jp", "romaji"], value if value in ("jp", "romaji") else "jp")
        index = self.pitch_category_format_combo.findData(target)
        if index >= 0:
            self.pitch_category_format_combo.setCurrentIndex(index)

    # === Card Styling (Issue #44) ===
    def _on_apply_styling(self) -> None:
        """Handle the Apply-to-note-type button click."""
        self.set_styling_status(None, self.tr("Applying styles to note type..."))
        self.apply_styling_requested.emit()

    def _on_remove_styling(self) -> None:
        """Handle the Remove-styles button click."""
        self.set_styling_status(None, self.tr("Removing styles from note type..."))
        self.remove_styling_requested.emit()

    def set_styling_status(self, ok: bool | None, message: str = "") -> None:
        """Update the card-styling status line (None=working, True=ok, False=error)."""
        if ok is None:
            self.styling_status.setText(message or self.tr("Working..."))
            self.styling_status.setProperty("status", "checking")
        elif ok:
            self.styling_status.setText(message or self.tr("Done"))
            self.styling_status.setProperty("status", "success")
        else:
            self.styling_status.setText(message or self.tr("Failed"))
            self.styling_status.setProperty("status", "error")

        if style := self.styling_status.style():
            style.unpolish(self.styling_status)
            style.polish(self.styling_status)

    def set_styling_buttons_enabled(self, enabled: bool) -> None:
        """Enable/disable both styling action buttons (during an in-flight worker)."""
        self.apply_styling_button.setEnabled(enabled)
        self.remove_styling_button.setEnabled(enabled)

    def get_card_style_preset(self) -> str:
        """Return the selected preset id (falls back to default)."""
        data = self.card_style_preset_combo.currentData()
        return data if isinstance(data, str) and data else "default"

    def set_card_style_preset(self, value: str) -> None:
        """Select the preset combo by id, falling back to default."""
        index = self.card_style_preset_combo.findData(value)
        if index < 0:
            index = self.card_style_preset_combo.findData("default")
        if index >= 0:
            self.card_style_preset_combo.setCurrentIndex(index)

    def get_custom_css(self) -> str:
        """Return the user's custom CSS text."""
        return self.custom_css_edit.toPlainText()

    def set_custom_css(self, value: str) -> None:
        """Set the custom CSS editor contents."""
        self.custom_css_edit.setPlainText(value)

    # === Simple field accessors (OVH-020) ===

    def get_deck_name(self) -> str:
        """Return the deck name."""
        return self.deck_input.text()

    def set_deck_name(self, value: str) -> None:
        """Set the deck name field."""
        self.deck_input.setText(value)

    def get_note_type(self) -> str:
        """Return the note type name."""
        return self.note_type_input.text()

    def set_note_type(self, value: str) -> None:
        """Set the note type field."""
        self.note_type_input.setText(value)

    def get_ankiconnect_url(self) -> str:
        """Return the AnkiConnect URL."""
        return self.ankiconnect_url_input.text()

    def set_ankiconnect_url(self, value: str) -> None:
        """Set the AnkiConnect URL field."""
        self.ankiconnect_url_input.setText(value)

    def get_anki_tags(self) -> str:
        """Return the card tags string."""
        return self.anki_tags_input.text()

    def set_anki_tags(self, value: str) -> None:
        """Set the card tags field."""
        self.anki_tags_input.setText(value)

    def set_fetch_fields_button_enabled(self, enabled: bool) -> None:
        """Enable or disable the Fetch Fields button."""
        self.fetch_fields_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        self.set_deck_name(config.anki_deck_name)
        self.set_note_type(config.anki_note_type)
        self.set_ankiconnect_url(config.ankiconnect_url)
        self.set_anki_tags(config.anki_tags)
        self.set_card_fields(config.anki_fields)
        self.set_card_style_preset(config.card_style_preset)
        self.set_custom_css(config.custom_card_css)
        self.set_pitch_category_format(config.pitch_category_format)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab._on_save_clicked` as part of the contribute fold.
        ``anki_word_field`` is derived from ``anki_fields["word"]`` (same logic as
        before — keeps the two in sync).
        """
        fields = self.get_card_fields()
        return replace(
            config,
            anki_deck_name=self.get_deck_name(),
            anki_note_type=self.get_note_type(),
            ankiconnect_url=self.get_ankiconnect_url(),
            anki_tags=self.get_anki_tags(),
            anki_fields=fields,
            anki_word_field=fields.get("word", "Expression"),
            card_style_preset=self.get_card_style_preset(),
            custom_card_css=self.get_custom_css(),
            pitch_category_format=self.get_pitch_category_format(),
        )
