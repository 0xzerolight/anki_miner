"""Anki configuration settings panel."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base import FormPanel, StatusBadge
from anki_miner.gui.widgets.enhanced import ModernButton

# Keywords used by populate_from_field_list to auto-map Anki field names.
# Each key is a card data type; the list is lowercase/stripped patterns that
# a field name must match (after lowercasing and removing spaces/underscores).
# Exported at module level so setup wizards and future callers can reuse the
# same sets without duplication.
_FIELD_KEYWORDS: dict[str, list[str]] = {
    "word": ["expression", "word", "vocab"],
    "sentence": ["sentence", "context", "example"],
    "definition": ["definition", "meaning", "maindefinition"],
    "glossary": ["glossary", "definitions", "dictionary"],
    "picture": ["picture", "image", "screenshot", "photo"],
    "audio": ["audio", "sound", "sentenceaudio"],
    "expression_audio": ["expressionaudio", "wordaudio"],
    "expression_furigana": ["expressionfurigana", "wordfurigana"],
    "expression_reading": ["expressionreading", "wordreading", "reading"],
    "sentence_furigana": ["sentencefurigana", "contextfurigana"],
    "sentence_reading": ["sentencereading", "contextreading"],
    "pitch_position": ["pitchposition", "pitchaccent", "pitch"],
    "pitch_category": ["pitchcategory", "accenttype", "accentcategory"],
    "frequency": ["frequency", "freq", "rank", "frequencyrank"],
    "frequency_sort": ["freqsort", "frequencysort"],
    "source": ["source", "origin"],
}

# JP Mining Note card-type marker ids → default field names. Mirrors the
# AnkiMinerConfig.card_type_marker_fields default factory; duplicated here (like
# set_card_fields' "Expression"/"Sentence" literals) to prefill the inputs
# without importing the config factory at widget-construction time.
_CARD_TYPE_MARKER_DEFAULTS: dict[str, str] = {
    "word_and_sentence": "IsWordAndSentenceCard",
    "click": "IsClickCard",
    "sentence": "IsSentenceCard",
    "audio": "IsAudioCard",
}


def auto_map_fields(field_names: list[str]) -> dict[str, str]:
    """Map Anki field names to card data keys via :data:`_FIELD_KEYWORDS`.

    Pure (Qt-free) so the setup wizard and the settings panel share one
    matching algorithm. For every key in ``_FIELD_KEYWORDS``, returns the first
    field name (in ``field_names`` order) that matches after lowercasing and
    removing spaces/underscores; unmatched keys map to ``""``.

    Args:
        field_names: Field names fetched from AnkiConnect.

    Returns:
        ``{field_key: matched_field_name_or_""}`` for every key in
        ``_FIELD_KEYWORDS``.
    """
    mapping: dict[str, str] = {}
    for key, keywords in _FIELD_KEYWORDS.items():
        normalized = [kw.lower() for kw in keywords]
        matched = ""
        for field_name in field_names:
            if field_name.lower().replace(" ", "").replace("_", "") in normalized:
                matched = field_name
                break
        mapping[key] = matched
    return mapping


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

    # Dynamically created by _add_labeled_field_with_button via setattr
    deck_input: QLineEdit
    note_type_input: QLineEdit
    deck_sync_button: ModernButton
    notetype_sync_button: ModernButton

    def __init__(self, parent=None):
        """Initialize the Anki settings panel."""
        super().__init__("Anki Configuration", parent=parent)
        # Snapshot of the anki_fields mapping last loaded via set_card_fields.
        # get_card_fields() folds its owned inputs over this so keys the panel
        # doesn't expose (future/opt-in keys set via gui_config.json) survive a
        # Save round-trip instead of being wiped.
        self._loaded_fields: dict[str, str] = {}
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

        # Auto-Map Fields button — prominent, immediately below the Note Type row
        self.fetch_fields_button = ModernButton(self.tr("Auto-Map Fields from Note Type"), variant="primary")
        self.fetch_fields_button.setToolTip(
            self.tr("Query AnkiConnect for this note type's fields and fill the mappings below automatically.")
        )
        self.fetch_fields_button.clicked.connect(self._on_fetch_fields)
        self.add_widget(self.fetch_fields_button)

        # Card Field Mappings section
        self.add_section(self.tr("Card Field Mappings"))

        # Helper text for card fields
        card_fields_helper = QLabel(self.tr("Map data to note fields (names must match exactly). Blank = skip."))
        card_fields_helper.setObjectName("helper-text")
        card_fields_helper.setWordWrap(True)
        self.add_widget(card_fields_helper)

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
        self.add_field(self.tr("Picture Field"), self.picture_field_input)

        # Audio field
        self.audio_field_input = QLineEdit()
        self.audio_field_input.setPlaceholderText("SentenceAudio")
        self.add_field(self.tr("Audio Field"), self.audio_field_input)

        # Expression audio field (Issue #73). Field-name presence is the on/off
        # switch (like Frequency/Pitch) — leave blank to disable. Sources are
        # ordered under Audio settings (packs first, JapanesePod101 fallback).
        self.expression_audio_field_input = QLineEdit()
        self.expression_audio_field_input.setPlaceholderText("ExpressionAudio")
        self.add_field(
            self.tr("Expression Audio Field"),
            self.expression_audio_field_input,
            helper=self.tr("Word pronunciation audio; blank disables. Configure sources under Audio settings."),
        )

        # Expression Furigana field
        self.expression_furigana_field_input = QLineEdit()
        self.expression_furigana_field_input.setPlaceholderText("ExpressionFurigana")
        self.add_field(self.tr("Expression Furigana Field"), self.expression_furigana_field_input)

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
        self.add_field(self.tr("Sentence Furigana Field"), self.sentence_furigana_field_input)

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

        auxiliary_helper = QLabel(self.tr("Need pitch_accent.csv in ~/.anki_miner/. Blank = skip."))
        auxiliary_helper.setObjectName("helper-text")
        auxiliary_helper.setWordWrap(True)
        self.add_widget(auxiliary_helper)

        # Pitch Position field
        self.pitch_position_field_input = QLineEdit()
        self.pitch_position_field_input.setPlaceholderText("PitchPosition")
        self.add_field(self.tr("Pitch Position Field"), self.pitch_position_field_input)

        # Pitch Category field
        self.pitch_category_field_input = QLineEdit()
        self.pitch_category_field_input.setPlaceholderText("PitchCategory")
        self.add_field(self.tr("Pitch Category Field"), self.pitch_category_field_input)

        # Pitch Category format (jp vs romaji)
        self.pitch_category_format_combo = QComboBox()
        self.pitch_category_format_combo.addItem(self.tr("Japanese (平板/頭高/中高/尾高/起伏)"), "jp")
        self.pitch_category_format_combo.addItem(self.tr("Romaji (heiban/atamadaka/nakadaka/odaka/kifuku)"), "romaji")
        self.add_field(
            self.tr("Pitch Category Format"),
            self.pitch_category_format_combo,
            helper=self.tr("Romaji matches Yomitan/Lapis CSS; Japanese for legacy notes."),
        )

        # Rendered pitch fields (6.3). Default blank = feature off.
        self.pitch_graph_field_input = QLineEdit()
        self.pitch_graph_field_input.setPlaceholderText("PitchGraph")
        self.add_field(
            self.tr("Pitch Graph Field"),
            self.pitch_graph_field_input,
            helper=self.tr("Stores the SVG pitch accent graph (Yomitan-style)."),
        )

        self.pitch_text_field_input = QLineEdit()
        self.pitch_text_field_input.setPlaceholderText("PitchText")
        self.add_field(
            self.tr("Pitch Text Field"),
            self.pitch_text_field_input,
            helper=self.tr("Stores the overline-annotated pitch reading (Yomitan-style)."),
        )

        # Frequency field (per-source breakdown of every ranked source)
        self.frequency_field_input = QLineEdit()
        self.frequency_field_input.setPlaceholderText("Frequency")
        self.add_field(
            self.tr("Frequency Field"),
            self.frequency_field_input,
            helper=self.tr("Stores the per-source frequency breakdown (all sources)."),
        )

        # Frequency Sort field (single min rank as a bare number, for sorting)
        self.frequency_sort_field_input = QLineEdit()
        self.frequency_sort_field_input.setPlaceholderText("FrequencySort")
        self.add_field(
            self.tr("Frequency Sort Field"),
            self.frequency_sort_field_input,
            helper=self.tr("Stores the single frequency rank used for sorting (one number)."),
        )

        # Source field
        self.source_field_input = QLineEdit()
        self.source_field_input.setPlaceholderText("Source")
        self.add_field(
            self.tr("Source Field"),
            self.source_field_input,
            helper=self.tr("Stores the show/episode and timestamp the word came from. Blank = skip."),
        )

        # Card Type section. JP Mining Note-style note types render a card
        # differently depending on which marker field holds an "x". The dropdown
        # is the only visible control by default; the editable field names hide
        # in a collapsible group for the rare fork that renames them.
        self.add_section(self.tr("Card Type"))

        card_type_helper = QLabel(
            self.tr(
                "For JP Mining Note-style note types: stamp an “x” into a marker field so every mined "
                "card renders as the chosen type. Leave “None” if your note type has no such fields."
            )
        )
        card_type_helper.setObjectName("helper-text")
        card_type_helper.setWordWrap(True)
        self.add_widget(card_type_helper)

        self.card_type_combo = QComboBox()
        self.card_type_combo.addItem(self.tr("None (disabled)"), "")
        self.card_type_combo.addItem(self.tr("Word + Sentence"), "word_and_sentence")
        self.card_type_combo.addItem(self.tr("Click"), "click")
        self.card_type_combo.addItem(self.tr("Sentence"), "sentence")
        self.card_type_combo.addItem(self.tr("Audio"), "audio")
        self.add_field(
            self.tr("Default Card Type"),
            self.card_type_combo,
            helper=self.tr("Which marker field gets the “x”. None leaves cards untouched."),
        )

        # Collapsible marker-field-name editors. The QGroupBox checkbox toggles
        # the inner body's visibility (Qt's checkable group only disables, not
        # hides), so the four rows stay hidden until a power user expands them.
        self.card_type_names_group = QGroupBox(self.tr("Customize marker field names"))
        self.card_type_names_group.setCheckable(True)
        self.card_type_names_group.setChecked(False)
        group_layout = QVBoxLayout(self.card_type_names_group)
        self._card_type_names_body = QWidget()
        body_form = QFormLayout(self._card_type_names_body)
        body_form.setContentsMargins(0, 0, 0, 0)

        self.card_type_word_and_sentence_input = QLineEdit(_CARD_TYPE_MARKER_DEFAULTS["word_and_sentence"])
        self.card_type_click_input = QLineEdit(_CARD_TYPE_MARKER_DEFAULTS["click"])
        self.card_type_sentence_input = QLineEdit(_CARD_TYPE_MARKER_DEFAULTS["sentence"])
        self.card_type_audio_input = QLineEdit(_CARD_TYPE_MARKER_DEFAULTS["audio"])
        self._card_type_inputs: dict[str, QLineEdit] = {
            "word_and_sentence": self.card_type_word_and_sentence_input,
            "click": self.card_type_click_input,
            "sentence": self.card_type_sentence_input,
            "audio": self.card_type_audio_input,
        }
        body_form.addRow(self.tr("Word + Sentence:"), self.card_type_word_and_sentence_input)
        body_form.addRow(self.tr("Click:"), self.card_type_click_input)
        body_form.addRow(self.tr("Sentence:"), self.card_type_sentence_input)
        body_form.addRow(self.tr("Audio:"), self.card_type_audio_input)

        group_layout.addWidget(self._card_type_names_body)
        self._card_type_names_body.setVisible(False)
        self.card_type_names_group.toggled.connect(self._card_type_names_body.setVisible)
        self.add_widget(self.card_type_names_group)

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
            "connected": ("success", self.tr("Connected"), self.tr("Connected to AnkiConnect")),
            "disconnected": ("error", self.tr("Not connected"), self.tr("Not connected to AnkiConnect")),
            "checking": ("checking", self.tr("Checking..."), self.tr("Checking connection...")),
            "unknown": ("info", self.tr("Unknown"), self.tr("Connection status unknown")),
        }
        badge_status, name, text = status_map.get(
            status, ("info", self.tr("Unknown"), self.tr("Connection status unknown"))
        )
        self.connection_status.set_name(name)
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
        # Map each data key to its input widget; the matching algorithm lives in
        # the module-level pure helper so the setup wizard reuses it verbatim.
        widget_map = {
            "word": self.expression_field_input,
            "sentence": self.sentence_field_input,
            "definition": self.definition_field_input,
            "glossary": self.glossary_field_input,
            "picture": self.picture_field_input,
            "audio": self.audio_field_input,
            "expression_audio": self.expression_audio_field_input,
            "expression_furigana": self.expression_furigana_field_input,
            "expression_reading": self.expression_reading_field_input,
            "sentence_furigana": self.sentence_furigana_field_input,
            "sentence_reading": self.sentence_reading_field_input,
            "pitch_position": self.pitch_position_field_input,
            "pitch_category": self.pitch_category_field_input,
            "pitch_graph": self.pitch_graph_field_input,
            "pitch_text": self.pitch_text_field_input,
            "frequency": self.frequency_field_input,
            "frequency_sort": self.frequency_sort_field_input,
            "source": self.source_field_input,
        }

        # Only overwrite a widget when a field actually matched — an empty result
        # leaves the existing value untouched (exact prior behavior).
        mapped = auto_map_fields(field_names)
        for key, widget in widget_map.items():
            if mapped.get(key):
                widget.setText(mapped[key])

    # Getters for card field values
    def get_card_fields(self) -> dict:
        """Get the card field mappings.

        Returns:
            Dictionary mapping data types to Anki field names.
            Empty string values mean "skip this field during card creation".
            Keys the panel doesn't own (present in the last-loaded mapping but
            not exposed as inputs) are preserved so a Save never wipes an
            opt-in/future key a user set via gui_config.json.
        """
        owned = {
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
            "pitch_graph": self.pitch_graph_field_input.text().strip(),
            "pitch_text": self.pitch_text_field_input.text().strip(),
            "frequency": self.frequency_field_input.text().strip(),
            "frequency_sort": self.frequency_sort_field_input.text().strip(),
            "source": self.source_field_input.text().strip(),
        }
        return {**self._loaded_fields, **owned}

    def set_card_fields(self, fields: Mapping[str, str]) -> None:
        """Set the card field mappings.

        Args:
            fields: Dictionary mapping data types to Anki field names
        """
        # Snapshot so get_card_fields() can preserve any keys not owned here.
        self._loaded_fields = dict(fields)
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
        self.pitch_graph_field_input.setText(fields.get("pitch_graph", ""))
        self.pitch_text_field_input.setText(fields.get("pitch_text", ""))
        self.frequency_field_input.setText(fields.get("frequency", ""))
        self.frequency_sort_field_input.setText(fields.get("frequency_sort", ""))
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

    # === Card Type marker (JP Mining Note) ===
    def get_card_type(self) -> str:
        """Return the selected card-type id ("" when disabled)."""
        value = self.card_type_combo.currentData()
        return value if isinstance(value, str) else ""

    def set_card_type(self, value: str) -> None:
        """Select the card-type dropdown by id, falling back to "" (disabled)."""
        index = self.card_type_combo.findData(value)
        if index < 0:
            index = self.card_type_combo.findData("")
        if index >= 0:
            self.card_type_combo.setCurrentIndex(index)

    def get_card_type_marker_fields(self) -> dict[str, str]:
        """Return the four marker field names keyed by card-type id."""
        return {key: widget.text().strip() for key, widget in self._card_type_inputs.items()}

    def set_card_type_marker_fields(self, mapping: Mapping[str, str]) -> None:
        """Populate the four marker-name inputs, defaulting any missing key."""
        for key, widget in self._card_type_inputs.items():
            widget.setText(mapping.get(key, _CARD_TYPE_MARKER_DEFAULTS[key]))

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
        self.set_pitch_category_format(config.pitch_category_format)
        self.set_card_type(config.card_type)
        self.set_card_type_marker_fields(config.card_type_marker_fields)

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
            pitch_category_format=self.get_pitch_category_format(),
            card_type=cast(
                Literal["", "word_and_sentence", "click", "sentence", "audio"],
                self.get_card_type(),
            ),
            card_type_marker_fields=self.get_card_type_marker_fields(),
        )
