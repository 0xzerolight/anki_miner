"""Anki configuration settings panel."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, cast

from PyQt6.QtCore import QCoreApplication, Qt, pyqtSignal
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
from anki_miner.gui.utils.language_gate import apply_language_gate, field_row_widgets
from anki_miner.gui.widgets.base import FormPanel, StatusBadge
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.note_presets import (
    NOTE_PRESETS,
    NotePreset,
    preset_by_id,
    preset_for_note_type_name,
)
from anki_miner.utils.i18n import tr_format

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
    # The plurals are the names Lapis / Kiku / Senren actually ship. Those three
    # are matched exactly by note_presets; this table is what a FORK of one of
    # them falls back to, so it has to know the same spellings.
    "pitch_position": ["pitchposition", "pitchpositions", "pitchaccent", "pitch"],
    "pitch_category": ["pitchcategory", "pitchcategories", "accenttype", "accentcategory"],
    "pitch_graph": ["pitchgraph", "pitchsvg"],
    "pitch_text": ["pitchtext", "pitchaccents"],
    "frequency": ["frequency", "frequencies", "freq", "rank", "frequencyrank"],
    "frequency_sort": ["freqsort", "frequencysort"],
    "source": ["source", "origin", "miscinfo"],
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


def select_or_insert(combo: QComboBox, name: str, *, known: bool = True) -> None:
    """Select ``name`` in ``combo``, inserting it first if it isn't listed.

    The deck / note-type combos are non-editable, so a value that is not an
    item cannot be displayed — and ``currentText()`` would then read back ""
    which the settings auto-save would persist over the user's real config.
    Loading runs before any AnkiConnect fetch and may run with Anki closed, so
    "not listed" is the normal startup case, not an error.

    An empty ``name`` clears the selection (``setCurrentIndex(-1)``) — the only
    way to express "nothing chosen" on a strict combo, and what the
    fetch-fields guard checks for.

    ``known=False`` tags the inserted entry with a tooltip so a phantom is
    distinguishable from a name that really exists. Only the tooltip: an
    item ForegroundRole renders nowhere here (Qt never applies it to the
    CLOSED combo, and common.qss sets an explicit colour on
    ``QComboBox QAbstractItemView`` that wins in the popup). The visible
    signal is the red status line under the combo, driven by the refresh.
    """
    if not name:
        combo.setCurrentIndex(-1)
        return
    index = combo.findText(name)
    if index < 0:
        combo.addItem(name)
        index = combo.findText(name)
        if not known:
            combo.setItemData(
                index,
                QCoreApplication.translate(
                    "AnkiSettingsPanel",
                    "Not in Anki — mining will fail until you pick a real one or create it in Anki.",
                ),
                Qt.ItemDataRole.ToolTipRole,
            )
    combo.setCurrentIndex(index)


class AnkiSettingsPanel(FormPanel):
    """Panel for Anki connection and configuration settings.

    Provides:
    - Deck name dropdown with refresh button
    - Note type dropdown with refresh button
    - AnkiConnect URL configuration
    - Connection status indicator
    - Test connection button
    - Card field mappings

    Signals:
        deck_sync_requested: Emitted when deck sync is requested
        notetype_sync_requested: Emitted when note type sync is requested
        test_connection_requested: Emitted when connection test is requested
    """

    ANCHOR_NAMESPACE = "anki"

    deck_sync_requested = pyqtSignal()
    notetype_sync_requested = pyqtSignal()
    test_connection_requested = pyqtSignal()
    fetch_fields_requested = pyqtSignal()

    # Dynamically created by _add_labeled_field_with_button via setattr
    deck_combo: QComboBox
    notetype_combo: QComboBox
    preset_combo: QComboBox
    deck_sync_button: ModernButton
    notetype_sync_button: ModernButton
    preset_apply_button: ModernButton

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
        # Every capability contributor extends this list; Stage 2B adds the
        # non-ja rows to the same one. A second assignment would drop these
        # pairs, so this is the only place it is bound.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []

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

        # Deck name with refresh button
        self._add_labeled_field_with_button(
            anchor="deck_name",
            label_text=self.tr("Deck Name"),
            input_widget_name="deck_combo",
            placeholder=self.tr("Select a deck…"),
            tooltip="",
            button_name="deck_sync_button",
            button_tooltip=self.tr("Reload the deck list from Anki"),
            button_callback=self._on_deck_sync,
            helper_text=self.tr("Target deck for new cards."),
        )

        # Deck status
        self.deck_status = QLabel()
        self.deck_status.setObjectName("validation-status")
        self.add_widget(self.deck_status)

        # Note type with refresh button
        self._add_labeled_field_with_button(
            anchor="note_type",
            label_text=self.tr("Note Type"),
            input_widget_name="notetype_combo",
            placeholder=self.tr("Select a note type…"),
            tooltip="",
            button_name="notetype_sync_button",
            button_tooltip=self.tr("Reload the note type list from Anki"),
            button_callback=self._on_notetype_sync,
            helper_text=self.tr("Anki note type whose fields you'll map below."),
        )

        # Clear a stale not-in-Anki warning as soon as the user acts on it.
        # _repopulate blocks signals, so only a real user selection fires these.
        self.deck_combo.currentIndexChanged.connect(self._on_deck_selection_changed)
        self.notetype_combo.currentIndexChanged.connect(self._on_notetype_selection_changed)

        # Note type status
        self.notetype_status = QLabel()
        self.notetype_status.setObjectName("validation-status")
        self.add_widget(self.notetype_status)
        self.ankiconnect_url_input.textChanged.connect(lambda _text: self._clear_status(self.notetype_status))

        # Note-type preset. Lapis / Kiku / Senren publish fixed field names, so
        # their mapping is knowable without asking Anki — and it carries three
        # things auto-map cannot: the names the keyword table misses
        # (PitchCategories, MiscInfo), romaji pitch categories, and Senren's own
        # marker field names. Same combo + button row as Deck / Note Type above,
        # so the panel gains a row, not a new kind of control.
        self._add_labeled_field_with_button(
            anchor="note_type_preset",
            label_text=self.tr("Preset"),
            input_widget_name="preset_combo",
            placeholder=self.tr("Select a preset…"),
            tooltip="",
            button_name="preset_apply_button",
            button_text=self.tr("Apply"),
            button_tooltip=self.tr("Fill every mapping below from this note type's published field names"),
            button_callback=self._on_apply_preset,
            helper_text=self.tr(
                "Lapis, Kiku and Senren ship fixed field names. Applying overwrites the mappings below."
            ),
        )
        for preset in NOTE_PRESETS:
            self.preset_combo.addItem(preset.name, preset.id)
        self.preset_combo.setCurrentIndex(-1)

        # Preset status
        self.preset_status = QLabel()
        self.preset_status.setObjectName("validation-status")
        self.preset_status.setWordWrap(True)
        self.add_widget(self.preset_status)

        # Auto-Map Fields button — prominent, immediately below the Note Type row
        self.fetch_fields_button = ModernButton(self.tr("Auto-Map Fields from Note Type"), variant="primary")
        self.fetch_fields_button.setToolTip(
            self.tr("Query AnkiConnect for this note type's fields and fill the mappings below automatically.")
        )
        self.fetch_fields_button.clicked.connect(self._on_fetch_fields)
        self.add_widget(self.fetch_fields_button)

        # The three combo+button rows are read as one column. Their labels are
        # different words ("Refresh", "Refresh", "Apply") and so are their
        # natural widths, which would stagger both the button edges and the
        # combos beside them. Widen them all to the widest.
        self._align_row_buttons(self.deck_sync_button, self.notetype_sync_button, self.preset_apply_button)

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

        # Chinese measure word / classifier. The canonical zh schema gives it a
        # named field; ja and ko never see the row and never write the key.
        self.measure_word_field_input = QLineEdit()
        self.measure_word_field_input.setPlaceholderText("MeasureWord")
        self.add_field(
            self.tr("Measure Word Field"),
            self.measure_word_field_input,
            helper=self.tr("Stores the classifier parsed from the dictionary entry. Blank = skip."),
        )

        # Chinese pinyin reading of the word. Same rule as the row above: the
        # mapped name is the on/off switch, so with no row the render hook can
        # never reach a note.
        self.expression_pinyin_field_input = QLineEdit()
        self.expression_pinyin_field_input.setPlaceholderText("Pinyin")
        self.add_field(
            self.tr("Pinyin Field"),
            self.expression_pinyin_field_input,
            helper=self.tr("Stores the word's pinyin reading, tone-coloured when that is on. Blank = skip."),
        )

        # Traditional spelling of a word mined in simplified (and the reverse).
        self.expression_traditional_field_input = QLineEdit()
        self.expression_traditional_field_input.setPlaceholderText("Traditional")
        self.add_field(
            self.tr("Traditional Field"),
            self.expression_traditional_field_input,
            helper=self.tr("Stores the word in the other script variant, when it differs. Blank = skip."),
        )

        # Korean hanja. ja and zh never see the row and never write the key.
        self.hanja_field_input = QLineEdit()
        self.hanja_field_input.setPlaceholderText("Hanja")
        self.add_field(
            self.tr("Hanja Field"),
            self.hanja_field_input,
            helper=self.tr("Stores the hanja characters contained in the word. Blank = skip."),
        )

        # Auxiliary Data Fields section
        self.add_section(self.tr("Auxiliary Data Fields"))

        auxiliary_helper = QLabel(self.tr("Pitch fields need a source in Settings → Pitch Accent. Blank = skip."))
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
                "For JP Mining Note-style note types: an “x” in a marker field selects " "how each mined card renders."
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
        # One logical setting: the four marker names are edited together and
        # search should land on the group, not on an individual name box.
        self.add_widget(
            self.card_type_names_group,
            anchor="card_type_marker_fields",
            anchor_focus=self.card_type_word_and_sentence_input,
            anchor_text=lambda: (self.card_type_names_group.title(),),
        )

        # Language-gated rows. Each row contributes its label too, so a hidden
        # field never leaves a dangling caption behind. The Auxiliary Data
        # Fields heading stays: frequency and source live under it as well.
        self._language_gate_pairs.extend(
            (w, "furigana")
            for field in (
                self.expression_furigana_field_input,
                self.sentence_furigana_field_input,
            )
            for w in field_row_widgets(self, field)
        )
        self._language_gate_pairs.extend(
            (w, "pitch")
            for field in (
                self.pitch_position_field_input,
                self.pitch_category_field_input,
                self.pitch_category_format_combo,
                self.pitch_graph_field_input,
                self.pitch_text_field_input,
            )
            for w in field_row_widgets(self, field)
        )
        self._language_gate_pairs.extend(
            (w, "measure_word") for w in field_row_widgets(self, self.measure_word_field_input)
        )
        self._language_gate_pairs.extend(
            (w, "pinyin") for w in field_row_widgets(self, self.expression_pinyin_field_input)
        )
        self._language_gate_pairs.extend(
            (w, "script_variants") for w in field_row_widgets(self, self.expression_traditional_field_input)
        )
        self._language_gate_pairs.extend((w, "hanja") for w in field_row_widgets(self, self.hanja_field_input))

        self.add_stretch()

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
        *,
        anchor: str,
        button_text: str = "",
    ) -> None:
        """Add a labeled dropdown + inline refresh button as one compact form row.

        The input and button are wrapped in a container widget so the whole pair
        sits in one ``add_field`` row (label beside control), matching the other
        densified settings panels. Helper text becomes the field's hover tooltip.

        Args:
            anchor: Stable settings-search anchor name. Required because the
                row's widget is a throwaway container with no panel attribute
                to derive an id from.
            label_text: Label text (no colon; ``add_field`` appends it)
            input_widget_name: Attribute name for the input widget
            placeholder: Placeholder text for input
            tooltip: Tooltip for input
            button_name: Attribute name for the button
            button_tooltip: Tooltip for button
            button_callback: Callback for button click
            helper_text: Optional helper text shown as a tooltip on the field
            button_text: Button label, defaulting to "Refresh". The row was
                built for the two combos that reload a list from Anki; a row
                whose button does something else has to say so.
        """
        # Container for input + button
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)

        # Input. Strict (non-editable) on purpose: these two names must match
        # Anki exactly, and the list is authoritative — see select_or_insert.
        input_widget = QComboBox()
        input_widget.setEditable(False)
        input_widget.setPlaceholderText(placeholder)
        input_widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        # Pair the policy with a minimum length (as header_widget and
        # single_episode_tab do) or the row collapses when the list is empty.
        input_widget.setMinimumContentsLength(20)
        # Large collections: a strict combo loses the old line edit's "type a
        # fragment" affordance; sorted() in _repopulate plus Qt's prefix
        # keyboardSearch is the mitigation. NOTE setMaxVisibleItems is ignored
        # by styles where SH_ComboBox_Popup is true (macOS).
        input_widget.setMaxVisibleItems(20)
        # Put the helper on the input itself: add_field sets it on the wrapping
        # container, but the input + button cover the container with zero margins
        # and Qt tooltips don't propagate to children, so the container tooltip
        # is unreachable on hover. Fall back to the explicit tooltip when given.
        input_widget.setToolTip(tooltip or helper_text)
        row.addWidget(input_widget, 1)
        setattr(self, input_widget_name, input_widget)

        # Refresh button. LABELLED, not the empty ghost it used to be: with a
        # strict combo this button is the only way back from an empty list, so
        # an invisible 40px hit box is a dead end (open Settings with Anki
        # closed, start Anki, and there is nothing to click — showEvent's fetch
        # is one-shot). Wording and variant match the wizard's deck/note-type
        # Refresh so the two surfaces read the same.
        sync_button = ModernButton(button_text or self.tr("Refresh"), variant="secondary")
        sync_button.clicked.connect(button_callback)
        sync_button.setToolTip(button_tooltip)
        row.addWidget(sync_button)
        setattr(self, button_name, sync_button)

        self.add_field(
            label_text,
            container,
            helper=helper_text,
            anchor=anchor,
            anchor_focus=input_widget,
        )

    @staticmethod
    def _align_row_buttons(*buttons: ModernButton) -> None:
        """Give every combo-row button the width of the widest one."""
        widest = max(button.sizeHint().width() for button in buttons)
        for button in buttons:
            button.setMinimumWidth(widest)

    def _on_deck_sync(self) -> None:
        """Handle deck refresh button click.

        Writes no status: ``AnkiProbeController.refresh_name_lists`` is the
        single owner of both status lines and sets them on entry. Writing here
        too would set the same message twice per click.
        """
        self.deck_sync_requested.emit()

    def _on_notetype_sync(self) -> None:
        """Handle note type refresh button click (status owned by the refresh)."""
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

    # === Note-type preset ===

    def _on_apply_preset(self) -> None:
        """Apply the selected preset, or say why nothing happened."""
        preset = preset_by_id(self.preset_combo.currentData())
        if preset is None:
            self._set_preset_status(False, self.tr("Pick a preset first."))
            return
        self.apply_note_type_preset(preset)

    def apply_note_type_preset(self, preset: NotePreset) -> None:
        """Overwrite every mapping this panel owns with ``preset``'s names.

        Writes more than the field rows on purpose: all three presets read
        pitch categories as romaji (the config default is Japanese), and Senren
        names its markers sentenceCard / audioCard. A card type the preset has
        no marker for is reset to None, because an empty marker would silently
        stop stamping and a wrong one would fail the pre-run field check.

        The note type name is filled only when nothing is selected. A user on
        "Lapis-modified" who applies the Lapis preset wants the field names, not
        to be moved onto a different note type.
        """
        merged = dict(self._loaded_fields)
        merged.update(preset.fields)
        self.set_card_fields(merged)
        self.set_pitch_category_format(preset.pitch_category_format)
        self.set_card_type_marker_fields(preset.card_type_marker_fields)
        if self.get_card_type() not in preset.supported_card_types:
            self.set_card_type("")
        if not self.get_note_type():
            self.set_note_type(preset.name)
        mapped = sum(1 for value in preset.fields.values() if value)
        self._set_preset_status(
            True,
            tr_format(
                self.tr("Applied %1 — %2 field mappings, romaji pitch categories."),
                preset.name,
                str(mapped),
            ),
        )

    def _set_preset_status(self, ok: bool, message: str) -> None:
        """Write the preset row's status line and repolish its colour."""
        self.preset_status.setText(message)
        self.preset_status.setProperty("status", "success" if ok else "error")
        if style := self.preset_status.style():
            style.unpolish(self.preset_status)
            style.polish(self.preset_status)

    def _sync_preset_to_note_type(self) -> None:
        """Preselect the preset whose note type is the one now chosen.

        Only ever selects — never clears. Landing on "Lapis-modified" after
        "Lapis" leaves the Lapis preset picked, which is the useful default for
        a fork.
        """
        preset = preset_for_note_type_name(self.get_note_type())
        if preset is None:
            return
        index = self.preset_combo.findData(preset.id)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

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
        # Language-scoped keys are contributed only while their row is on screen
        # (or the mapping already carried them). Keeps a ja anki_fields
        # byte-identical instead of seeding it with an empty zh key.
        for key, widget in (
            ("measure_word", self.measure_word_field_input),
            ("expression_pinyin", self.expression_pinyin_field_input),
            ("expression_traditional", self.expression_traditional_field_input),
            ("hanja", self.hanja_field_input),
        ):
            if widget.isVisibleTo(self) or key in self._loaded_fields:
                owned[key] = widget.text().strip()
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
        self.measure_word_field_input.setText(fields.get("measure_word", ""))
        self.expression_pinyin_field_input.setText(fields.get("expression_pinyin", ""))
        self.expression_traditional_field_input.setText(fields.get("expression_traditional", ""))
        self.hanja_field_input.setText(fields.get("hanja", ""))
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
        """Return the selected deck name ("" when nothing is selected)."""
        return self.deck_combo.currentText()

    def set_deck_name(self, value: str) -> None:
        """Select ``value``; insert it when Anki hasn't listed it, "" clears."""
        select_or_insert(self.deck_combo, value, known=False)

    def get_note_type(self) -> str:
        """Return the selected note type name ("" when nothing is selected)."""
        return self.notetype_combo.currentText()

    def set_note_type(self, value: str) -> None:
        """Select ``value``; insert it when Anki hasn't listed it, "" clears."""
        select_or_insert(self.notetype_combo, value, known=False)
        self._sync_preset_to_note_type()

    def set_available_decks(self, names: list[str]) -> None:
        """Repopulate the deck list, preserving the current selection.

        An empty ``names`` (Anki closed, fetch failed) is a no-op: clearing
        would drop the user's saved deck. A selection Anki no longer reports is
        re-inserted, tagged as a phantom so it does not pass for a real deck.
        """
        self._repopulate(self.deck_combo, names)

    def set_available_note_types(self, names: list[str]) -> None:
        """Repopulate the note type list, preserving the current selection."""
        self._repopulate(self.notetype_combo, names)

    def _on_deck_selection_changed(self) -> None:
        """Clear the not-in-Anki warning once the user picks a real deck.

        Without this the red "Deck 'X' is not in Anki — pick one below."
        written by the list refresh stays on screen after the user has done
        exactly what it asked, which reads as "still broken". Only clears —
        it never invents a success message, since this panel does not know
        the fetched list; the refresh owns that.
        """
        index = self.deck_combo.currentIndex()
        if index >= 0 and not self.deck_combo.itemData(index, Qt.ItemDataRole.ToolTipRole):
            self._clear_status(self.deck_status)

    def _on_notetype_selection_changed(self) -> None:
        """Clear the not-in-Anki warning once the user picks a real note type."""
        index = self.notetype_combo.currentIndex()
        if index >= 0 and not self.notetype_combo.itemData(index, Qt.ItemDataRole.ToolTipRole):
            self._clear_status(self.notetype_status)
        self._sync_preset_to_note_type()

    @staticmethod
    def _clear_status(label: QLabel) -> None:
        """Blank a validation-status label and drop its colour property."""
        label.setText("")
        label.setProperty("status", "")
        if style := label.style():
            style.unpolish(label)
            style.polish(label)

    @staticmethod
    def _repopulate(combo: QComboBox, names: list[str]) -> None:
        if not names:
            return
        current = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(sorted(names))
            select_or_insert(combo, current, known=current in names)
        finally:
            combo.blockSignals(False)

    def get_ankiconnect_url(self) -> str:
        """Return the AnkiConnect URL."""
        return self.ankiconnect_url_input.text().strip()

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

        Both status lines are cleared first, because the message they carry
        belongs to the selection that was on screen before this load, not to
        the one being loaded. A settings import or profile switch can name a
        deck this collection does not have — ``set_deck_name`` inserts it as a
        phantom, and ``_on_deck_selection_changed`` deliberately stays silent
        for exactly that case (it only clears when the new item has no
        phantom tooltip) — so a green "5 decks loaded" would sit above a
        combo showing a deck that will fail the run. The refresh owns writing
        a message; nothing here invents one.
        """
        self._clear_status(self.deck_status)
        self._clear_status(self.notetype_status)
        self._clear_status(self.preset_status)
        self.set_deck_name(config.anki_deck_name)
        self.set_note_type(config.anki_note_type)
        self.set_ankiconnect_url(config.ankiconnect_url)
        self.set_anki_tags(config.anki_tags)
        self.set_card_fields(config.anki_fields)
        self.set_pitch_category_format(config.pitch_category_format)
        self.set_card_type(config.card_type)
        self.set_card_type_marker_fields(config.card_type_marker_fields)
        apply_language_gate(self._language_gate_pairs, get_profile(config_language(config)).capabilities)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.
        """
        fields = self.get_card_fields()
        return replace(
            config,
            anki_deck_name=self.get_deck_name(),
            anki_note_type=self.get_note_type(),
            ankiconnect_url=self.get_ankiconnect_url(),
            anki_tags=self.get_anki_tags(),
            anki_fields=fields,
            pitch_category_format=self.get_pitch_category_format(),
            card_type=cast(
                Literal["", "word_and_sentence", "click", "sentence", "audio"],
                self.get_card_type(),
            ),
            card_type_marker_fields=self.get_card_type_marker_fields(),
        )
