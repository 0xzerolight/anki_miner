"""Anki configuration settings panel."""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Literal, cast

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.profile import CardFieldSpec
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.note_presets import FIELD_KEYWORDS as _FIELD_KEYWORDS  # noqa: F401 - re-exported
from anki_miner.services.note_presets import (
    NotePreset,
    NoteTypeFill,
    auto_map_fields,  # noqa: F401 - re-exported for setup_wizard/pages.py
    auto_map_profile_fields,  # noqa: F401 - re-exported for setup_wizard/pages.py
    fill_note_type_fields,
)

logger = logging.getLogger(__name__)

#: tr-context for the profile-derived card-field rows. Spelled explicitly
#: because those labels reach the loop as data: ``self.tr(label)`` would work at
#: runtime, but pylupdate parses the source rather than running it, so only the
#: literals below reach a catalogue.
_TR_CONTEXT = "AnkiSettingsPanel"

#: ``CardFieldSpec.key`` -> (label, helper) for every extra card field a
#: registered profile declares. Carried verbatim from the hand-built rows this
#: table replaced, context included, so the existing catalogue entries keep
#: matching. A key missing here falls back to a title-cased key at runtime and
#: fails ``test_hook_field_mapping_rows``: the fallback is readable English but
#: is never extracted, so it can never be translated.
_HOOK_FIELD_ROW_TEXTS: dict[str, tuple[str, str]] = {
    "measure_word": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Measure Word Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the classifier parsed from the dictionary entry. Blank = skip.",
        ),
    ),
    "expression_pinyin": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Pinyin Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the word's pinyin reading. Blank = skip.",
        ),
    ),
    "expression_traditional": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Traditional Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the word in the other script variant, when it differs. Blank = skip.",
        ),
    ),
    "hanja": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Hanja Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the hanja characters contained in the word. Blank = skip.",
        ),
    ),
    "hanviet": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Hán Việt Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the Chinese characters a Sino-Vietnamese word comes from, read from the dictionary entry. "
            "Blank = skip.",
        ),
    ),
    "pos": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Part of Speech Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the word's part of speech (noun, verb, adjective, adverb). Blank = skip.",
        ),
    ),
    "noun_gender": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Gender Field"),
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Stores a noun's grammatical gender. Blank = skip."),
    ),
    "noun_article": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Article Field"),
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Stores the article that goes with a noun. Blank = skip."),
    ),
    "noun_plural": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Plural Field"),
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Stores a noun's plural form from the dictionary entry. Blank = skip."),
    ),
    "aspect_pair": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Aspect Pair Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores a verb's aspect, plus its aspect partner when the dictionary names one. Blank = skip.",
        ),
    ),
    # Shared by the root-and-pattern languages (ruling R-ROOT), so the label names no language.
    "root": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Root Field"),
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Stores the word's root, from the dictionary entry. Blank = skip."),
    ),
    "affixes": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Affixes Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the prefixes and suffixes around the root, from the dictionary entry. Blank = skip.",
        ),
    ),
    "formal_form": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Formal Form Field"),
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Stores the standard spelling of a colloquial word. Blank = skip."),
    ),
    # Arabic (spec C.1): the dictionary's grammar line and the clitic split of the word you saw.
    "expression_grammar": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Grammar Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the dictionary's grammar line: gender and plurals, or a verb's form and verbal noun. Blank = skip.",
        ),
    ),
    "clitic_segmentation": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Segmentation Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores how the word you saw splits into prefixes, stem and suffixes. Blank = skip.",
        ),
    ),
    "reading_paiboon": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Reading Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the Paiboon reading parsed from the dictionary entry. Blank = skip.",
        ),
    ),
    "classifier": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Classifier Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the noun classifier stated by the dictionary entry. Blank = skip.",
        ),
    ),
    # Persian (spec C.2): the dictionary romanisation, the colloquial spelling and a verb's present stem.
    "reading_romanized": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Romanization Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the word's Latin spelling, from the dictionary entry. Blank = skip.",
        ),
    ),
    "colloquial_form": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Colloquial Form Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the everyday spelling the line used, when the front is the standard one. Blank = skip.",
        ),
    ),
    "present_stem": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Present Stem Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the stem a verb's present-tense forms are built on. Blank = skip.",
        ),
    ),
    # Cantonese (spec F.1). measure_word is zh's key and already has its row.
    "expression_jyutping": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Jyutping Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the jyutping reading. Blank = skip.",
        ),
    ),
    # Hebrew (spec F.2): the dictionary's own romanisation and a verb's binyan. Its root, gender,
    # plural and part of speech reuse the rows above.
    "transliteration": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Transliteration Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the word's Latin spelling, from the dictionary entry. Blank = skip.",
        ),
    ),
    "binyan": (
        QT_TRANSLATE_NOOP("AnkiSettingsPanel", "Binyan Field"),
        QT_TRANSLATE_NOOP(
            "AnkiSettingsPanel",
            "Stores the verb pattern the dictionary names, such as pa'al or hif'il. Blank = skip.",
        ),
    ),
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


def profile_card_field_specs() -> tuple[CardFieldSpec, ...]:
    """Every extra card field any registered profile declares, deduped by key.

    Resolved from the registry rather than from ``available_mining_languages``:
    that one drops a language whose engine is missing, and a config already set
    to it would then gate rows into view that were never built. First
    declaration of a key wins, so two languages sharing one logical field share
    one row (and one anchor id) instead of colliding.
    """
    specs: dict[str, CardFieldSpec] = {}
    for code in AVAILABLE_LANGUAGES:
        try:
            profile = get_profile(code)
        except (LookupError, ValueError, ImportError) as exc:
            logger.debug("No profile for %r while building card-field rows: %s", code, exc)
            continue
        for spec in profile.extra_card_fields:
            specs.setdefault(spec.key, spec)
    return tuple(specs.values())


def hook_field_row_text(key: str) -> tuple[str, str]:
    """The translated (label, helper) naming one extra card field.

    Public because Card Backfill offers the same fields and must call them what
    this panel calls them — one wording per field, one catalogue entry. A key
    with no entry falls back to its title-cased self, readable English that
    pylupdate never sees (``test_every_declared_card_field_has_row_texts``
    fails on one) and an empty helper.
    """
    label, helper = _HOOK_FIELD_ROW_TEXTS.get(key, ("", ""))
    return (
        QCoreApplication.translate(_TR_CONTEXT, label) if label else key.replace("_", " ").title(),
        QCoreApplication.translate(_TR_CONTEXT, helper) if helper else "",
    )


class AnkiSettingsPanel(FormPanel):
    """Panel for the Anki target, the connection and the card field mappings.

    Provides (C03, UI/UX audit 2026-09-29): Deck, Note Type and Card tags
    first; then one connection row, "Anki: ● Connected [Refresh]", whose
    Refresh re-tests the connection and reloads both lists; at most one status
    line, shown only when something is wrong; the AnkiConnect URL last; then
    the card field mappings.

    Signals:
        test_connection_requested: Refresh asks for a validation sweep.
        name_lists_requested: Refresh asks for the deck and note type lists.
        fetch_fields_requested: The field-mapping fill button was clicked.
    """

    ANCHOR_NAMESPACE = "anki"

    test_connection_requested = pyqtSignal()
    name_lists_requested = pyqtSignal()
    fetch_fields_requested = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the Anki settings panel."""
        super().__init__(self.tr("Cards & Anki"), parent=parent)
        # Snapshot of the anki_fields mapping last loaded via set_card_fields.
        # get_card_fields() folds its owned inputs over this so keys the panel
        # doesn't expose (future/opt-in keys set via gui_config.json) survive a
        # Save round-trip instead of being wiped.
        self._loaded_fields: dict[str, str] = {}
        # Whether "Fill in automatically" may apply Lapis, Kiku or Senren, the
        # Japanese note types (the "note_presets" capability); Anki Miner Note
        # applies in every language. Refreshed on every load_from_config.
        self._presets_allowed = True
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Every capability contributor extends this list; Stage 2B adds the
        # non-ja rows to the same one. A second assignment would drop these
        # pairs, so this is the only place it is bound.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []

        # Deck and note type first: they are what a user comes here to set
        # (C03). Strict combos: these two names must match Anki exactly, and the
        # list is authoritative -- see select_or_insert.
        self.deck_combo = self._make_name_combo(self.tr("Select a deck…"))
        self.add_field(
            self.tr("Deck Name"),
            self.deck_combo,
            helper=self.tr("Target deck for new cards."),
            anchor="deck_name",
        )
        self.notetype_combo = self._make_name_combo(self.tr("Select a note type…"))
        self.add_field(
            self.tr("Note Type"),
            self.notetype_combo,
            helper=self.tr("Anki note type whose fields you'll map below."),
            anchor="note_type",
        )
        # Clear a stale not-in-Anki warning as soon as the user acts on it.
        # _repopulate blocks signals, so only a real user selection fires these.
        self.deck_combo.currentIndexChanged.connect(self._on_deck_selection_changed)
        self.notetype_combo.currentIndexChanged.connect(self._on_notetype_selection_changed)

        self.anki_tags_input = QLineEdit()
        self.add_field(
            self.tr("Card tags"),
            self.anki_tags_input,
            helper=self.tr("Space-separated tags applied to every mined card. Leave blank for no tags."),
        )

        # One connection row (C03). Refresh replaces Test Connection and the two
        # list Refresh buttons: it re-tests AnkiConnect and reloads both lists,
        # which is what a user who has just started Anki wants either way.
        self.connection_status = StatusBadge("AnkiConnect", status="checking", clickable=False)
        self.refresh_button = ModernButton(self.tr("Refresh"), variant="secondary")
        self.refresh_button.setToolTip(
            self.tr(
                "Check the connection to Anki again and reload the deck and note type lists. "
                "Anki must be running with AnkiConnect installed."
            )
        )
        self.refresh_button.clicked.connect(self._on_refresh)
        connection_row = QWidget()
        connection_layout = QHBoxLayout(connection_row)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.setSpacing(SPACING.xs)
        connection_layout.addWidget(self.connection_status)
        connection_layout.addWidget(self.refresh_button)
        connection_layout.addStretch()
        self.add_field(
            self.tr("Anki"),
            connection_row,
            anchor="anki_connection",
            anchor_focus=self.refresh_button,
            # Untranslated vocabulary users type (like LEGACY_DESTINATION_TERMS):
            # the button this row replaced was called "Test Connection".
            anchor_text=lambda: (self.refresh_button.text(), self.refresh_button.toolTip(), "Test Connection"),
        )

        # The one status line (C03): the most important problem, or nothing.
        # "3 deck(s) loaded" and "Loading…" are gone -- a line that only ever
        # confirms things trains the eye to skip the line that matters.
        self._status_parts: dict[str, str] = {"connection": "", "deck": "", "notetype": ""}
        self.anki_status = QLabel()
        self.anki_status.setObjectName("validation-status")
        self.anki_status.setProperty("status", "error")
        self.anki_status.setWordWrap(True)
        self.anki_status.setVisible(False)
        self.add_widget(self.anki_status)

        # AnkiConnect URL last: almost nobody changes it.
        self.ankiconnect_url_input = QLineEdit()
        self.ankiconnect_url_input.setPlaceholderText("http://127.0.0.1:8765")
        self.add_field(
            self.tr("AnkiConnect URL"),
            self.ankiconnect_url_input,
            helper=self.tr("Default http://127.0.0.1:8765. Change if AnkiConnect uses a different port."),
        )
        # Every problem on either line described the previous address.
        self.ankiconnect_url_input.textChanged.connect(lambda _text: self._clear_status_parts())
        self.ankiconnect_url_input.textChanged.connect(lambda _text: self.set_fill_status(None, ""))

        # Card Field Mappings section. One secondary "Fill in automatically" in
        # its heading (D13) replaces the Preset row and the full-width Auto-Map
        # bar: it reads the note type's fields, applies Lapis / Kiku / Senren /
        # Anki Miner Note when their fields are all there, and otherwise maps by
        # keyword.
        self.fetch_fields_button = ModernButton(self.tr("Fill in automatically"), variant="secondary")
        self.fetch_fields_button.setToolTip(
            self.tr(
                "Read this note type's fields from Anki and fill every mapping below. "
                "Lapis, Kiku, Senren and Anki Miner Note are recognised and filled completely."
            )
        )
        self.fetch_fields_button.clicked.connect(self._on_fetch_fields)
        self.add_section(self.tr("Card Field Mappings"), trailing=self.fetch_fields_button)

        # The fill result: which note type was recognised, or how many fields
        # were mapped and cleared. Hidden until a fill has run.
        self.fill_status = QLabel()
        self.fill_status.setObjectName("validation-status")
        self.fill_status.setWordWrap(True)
        self.fill_status.setVisible(False)
        self.add_widget(self.fill_status)

        # Helper text for card fields
        card_fields_helper = QLabel(self.tr("Map data to note fields (names must match exactly). Blank = skip."))
        card_fields_helper.setObjectName("helper-text")
        card_fields_helper.setWordWrap(True)
        self.add_widget(card_fields_helper)

        # Expression field (word)
        self.expression_field_input = QLineEdit()
        self.expression_field_input.setPlaceholderText("Expression")
        self.add_field(
            self.tr("Expression Field"), self.expression_field_input, helper=self.tr("Stores the mined word.")
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
            helper=self.tr("Stores the first definition found in your dictionaries."),
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

        # Expression Reading field. Unannotated reading, whatever the language
        # writes one in: kana for ja, pinyin for zh.
        self.expression_reading_field_input = QLineEdit()
        self.expression_reading_field_input.setPlaceholderText("ExpressionReading")
        self.add_field(
            self.tr("Expression Reading Field"),
            self.expression_reading_field_input,
            helper=self.tr("Stores the expression's plain reading."),
        )

        # Sentence Furigana field
        self.sentence_furigana_field_input = QLineEdit()
        self.sentence_furigana_field_input.setPlaceholderText("SentenceFurigana")
        self.add_field(self.tr("Sentence Furigana Field"), self.sentence_furigana_field_input)

        # Sentence Reading field — same, for the whole line.
        self.sentence_reading_field_input = QLineEdit()
        self.sentence_reading_field_input.setPlaceholderText("SentenceReading")
        self.add_field(
            self.tr("Sentence Reading Field"),
            self.sentence_reading_field_input,
            helper=self.tr("Stores the sentence's plain reading."),
        )

        # One row per extra card field the registered profiles declare (Korean
        # hanja, the three Chinese ones). Derived rather than hand-written: the
        # mapped field NAME is each key's on/off switch, so a declared field
        # with no row here is a feature nobody can turn on. Every row is gated
        # on its spec's capability, so a language that cannot fill the key never
        # sees the row and never writes the key.
        self._hook_field_inputs: dict[str, QLineEdit] = {}
        self._hook_field_specs = profile_card_field_specs()
        for spec in self._hook_field_specs:
            label, helper = hook_field_row_text(spec.key)
            field_input = QLineEdit()
            field_input.setPlaceholderText(spec.placeholder)
            # setattr, not a local: the anchor id and the attribute the tests
            # and any deep link address the row by are both ``<key>_field_input``.
            setattr(self, f"{spec.key}_field_input", field_input)
            self.add_field(
                label,
                field_input,
                helper=helper,
                # Loop-built, so pass the id the attribute would have derived.
                anchor=f"{spec.key}_field_input",
            )
            self._hook_field_inputs[spec.key] = field_input
            self._language_gate_pairs.extend((w, spec.capability) for w in field_row_widgets(self, field_input))

        # Tone colouring for the pinyin/jyutping readings above (T10): sits
        # right beside the rows it colours instead of a separate page.
        self.reading_tone_color_checkbox = QCheckBox(self.tr("Colour the reading by tone"))
        # The hook writes an inline style, never a class (languages/zh/render.py),
        # so a tooltip promising a class sends the user off to write CSS that can
        # neither match nor win.
        self.reading_tone_color_checkbox.setToolTip(self.tr("Colours each syllable of the reading by its tone."))
        self.add_field("", self.reading_tone_color_checkbox)
        self._language_gate_pairs.extend(
            (w, "tone_color") for w in field_row_widgets(self, self.reading_tone_color_checkbox)
        )

        # "Extra Fields" (C16); the old heading stays a search synonym.
        self.add_section(self.tr("Extra Fields"), synonyms=("Auxiliary Data Fields",))

        # Paired with "pitch" below: the heading stays for frequency and source,
        # but a language with no pitch rows must not be told to go and configure
        # a source for them.
        self._auxiliary_helper = QLabel(self.tr("Pitch fields need a source in Settings → Pitch Accent. Blank = skip."))
        self._auxiliary_helper.setObjectName("helper-text")
        self._auxiliary_helper.setWordWrap(True)
        self.add_widget(self._auxiliary_helper)

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

        # Translation field (secondary-language subtitles, F7)
        self.sentence_translation_field_input = QLineEdit()
        self.sentence_translation_field_input.setPlaceholderText("SentenceTranslation")
        self.add_field(
            self.tr("Translation Field"),
            self.sentence_translation_field_input,
            helper=self.tr(
                "Stores the secondary-language subtitle line for the sentence "
                "(Video -> Single, with secondary subtitles enabled under Sentences). Blank = skip."
            ),
        )

        # Language field (the card's BCP-47 tag). Every language writes one, so
        # the row is never gated.
        self.language_field_input = QLineEdit()
        self.language_field_input.setPlaceholderText("Language")
        self.add_field(
            self.tr("Language Field"),
            self.language_field_input,
            helper=self.tr(
                "Stores the card's language tag (ja, zh-Hans, de, …) for note types "
                "that set fonts or hyphenation by language. Blank = skip."
            ),
        )

        # Card Type section. Some note types render a card differently depending
        # on which marker field holds an "x" (JP Mining Note is the one the four
        # default names come from). The mechanism is language-agnostic, so the
        # section is never gated. The dropdown is the only visible control by
        # default; the editable field names hide in a collapsible group for the
        # rare fork that renames them.
        self.add_section(self.tr("Card Type"))

        card_type_helper = QLabel(
            self.tr("Note types with marker fields render each mined card by which field holds an “x”.")
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

        # Card Creation section (T10, moved from Word Filters). Language-agnostic,
        # so the row is never gated.
        self.add_section(self.tr("Card Creation"))

        self.strict_card_order_checkbox = QCheckBox(self.tr("Create cards in order of appearance"))
        self.add_field(
            "",
            self.strict_card_order_checkbox,
            helper=self.tr(
                "Adds cards to Anki in the order the words appear in the media, instead of "
                "the order their media finished extracting. Overrides the whitelist's "
                "force-include ordering and any column sort in the Word Curator."
            ),
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
        self._language_gate_pairs.append((self._auxiliary_helper, "pitch"))

        self.add_stretch()

    @staticmethod
    def _make_name_combo(placeholder: str) -> QComboBox:
        """A strict deck / note-type combo, sized so an empty list keeps its width."""
        combo = QComboBox()
        combo.setEditable(False)
        combo.setPlaceholderText(placeholder)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        # Pair the policy with a minimum length (as header_widget and
        # single_episode_tab do) or the row collapses when the list is empty.
        combo.setMinimumContentsLength(20)
        # Large collections: sorted() in _repopulate plus Qt's prefix keyboard
        # search stand in for typing a fragment. setMaxVisibleItems is ignored
        # where SH_ComboBox_Popup is true (macOS).
        combo.setMaxVisibleItems(20)
        return combo

    def _on_refresh(self) -> None:
        """Re-test the connection and reload both lists (C03)."""
        self.set_connection_status("checking")
        self.test_connection_requested.emit()
        self.name_lists_requested.emit()

    def set_connection_status(self, status: str) -> None:
        """Update the connection badge, and the status line when Anki is unreachable.

        Args:
            status: connected, disconnected, checking or unknown.
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
        unreachable = self.tr("Anki isn't reachable. Start Anki (with AnkiConnect) and press Refresh.")
        self._set_status_part("connection", unreachable if status == "disconnected" else "")

    def set_deck_status(self, exists: bool | None, message: str = "") -> None:
        """Report the deck check. Only a failure (``False``) is ever shown (C03)."""
        self._set_status_part("deck", (message or self.tr("Deck not found")) if exists is False else "")

    def set_notetype_status(self, exists: bool | None, message: str = "") -> None:
        """Report the note type check. Only a failure (``False``) is ever shown (C03)."""
        self._set_status_part("notetype", (message or self.tr("Note type not found")) if exists is False else "")

    def _set_status_part(self, key: str, text: str) -> None:
        self._status_parts[key] = text
        self._render_status()

    def _clear_status_parts(self, *keys: str) -> None:
        """Blank the named problems (all three when none are named) and re-render."""
        for key in keys or tuple(self._status_parts):
            self._status_parts[key] = ""
        self._render_status()

    def _render_status(self) -> None:
        """Show the most important problem -- connection, then deck, then note type -- or nothing."""
        text = next(
            (self._status_parts[key] for key in ("connection", "deck", "notetype") if self._status_parts[key]),
            "",
        )
        self.anki_status.setText(text)
        self.anki_status.setVisible(bool(text))

    def _on_fetch_fields(self) -> None:
        """Handle fetch fields button click."""
        self.fetch_fields_requested.emit()

    # === "Fill in automatically" ===

    def apply_note_type_preset(self, preset: NotePreset) -> None:
        """Overwrite every mapping this panel owns with ``preset``'s names (the fill's preset path).

        Writes more than the field rows on purpose: every preset reads
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

    def _visible_hook_specs(self) -> list[CardFieldSpec]:
        """The extra card-field rows the active language shows (Pinyin, Hanja, ...)."""
        return [spec for spec in self._hook_field_specs if self._hook_field_inputs[spec.key].isVisibleTo(self)]

    def fill_from_field_list(self, field_names: list[str]) -> tuple[NotePreset | None, int]:
        """ "Fill in automatically" (D13): preset when recognised, else the keyword pass.

        Returns:
            ``(preset, 0)`` when a preset was applied, else ``(None, cleared)``
            where ``cleared`` counts mappings blanked because the note type has
            no such field.
        """
        fill = fill_note_type_fields(
            field_names, allow_presets=self._presets_allowed, extra_specs=self._visible_hook_specs()
        )
        if fill.preset is not None:
            self.apply_note_type_preset(fill.preset)
            for key, match in fill.extra_fields.items():
                self._hook_field_inputs[key].setText(match)
            return fill.preset, 0
        return None, self._apply_keyword_fill(field_names, fill)

    def populate_from_field_list(self, field_names: list[str]) -> int:
        """Auto-map fetched field names to the card field inputs.

        Keyword pass only; the fill button calls :meth:`fill_from_field_list`.

        Tries to match fetched field names to known data types using
        common naming patterns, then gives the rows the active mining language
        declares (Pinyin, Hanja, …) the same pass against their own spelling.
        Whatever is left pointing at a field this note type does not have is
        blanked: matching alone left the old language's names (Expression,
        MainDefinition, …) sitting in the rows, red and unfixable from here.

        Args:
            field_names: List of field names from AnkiConnect

        Returns:
            How many mappings were cleared because the note type has no such
            field. The caller reports it — a silent blank reads as data loss.
        """
        fill = fill_note_type_fields(field_names, allow_presets=False, extra_specs=self._visible_hook_specs())
        return self._apply_keyword_fill(field_names, fill)

    def _apply_keyword_fill(self, field_names: list[str], fill: NoteTypeFill) -> int:
        """Write a keyword-pass proposal: matched rows only, then clear stale ones."""
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
            "sentence_translation": self.sentence_translation_field_input,
            "language": self.language_field_input,
        }
        # Only overwrite a widget when a field actually matched -- an empty
        # result leaves the existing value untouched (exact prior behaviour).
        for key, widget in widget_map.items():
            if fill.fields.get(key):
                widget.setText(fill.fields[key])
        for key, match in fill.extra_fields.items():
            self._hook_field_inputs[key].setText(match)
        return self._clear_missing_mappings(field_names, (*widget_map.values(), *self._hook_field_inputs.values()))

    def set_fill_status(self, ok: bool | None, message: str) -> None:
        """Show the fill result under the Card Field Mappings heading ("" hides it)."""
        self.fill_status.setText(message)
        self.fill_status.setProperty("status", "checking" if ok is None else ("success" if ok else "error"))
        self.fill_status.setVisible(bool(message))
        if style := self.fill_status.style():
            style.unpolish(self.fill_status)
            style.polish(self.fill_status)

    def _clear_missing_mappings(self, field_names: Iterable[str], widgets: Iterable[QLineEdit]) -> int:
        """Blank every shown mapping naming a field this note type lacks.

        Deliberately narrower than the wizard's sanitiser, which walks all of
        ``config.anki_fields``. Three things are left alone because none of them
        is something the fetched list can speak for: the ``_loaded_fields``
        passthrough (keys a user set by hand in ``gui_config.json``, which
        :meth:`get_card_fields` promises survive a Save), rows the language gate
        has hidden (a ja user's stored zh mappings), and the marker fields of
        the card types that are not the active one.
        """
        known = set(field_names)
        stale = [
            widget
            for widget in widgets
            if widget.isVisibleTo(self) and widget.text().strip() and widget.text().strip() not in known
        ]
        marker = self._card_type_inputs.get(self.get_card_type())
        # Not visibility-tested: the marker inputs live in a collapsed group, and
        # the active card type stamps its field on every card whether or not the
        # user has expanded it.
        if marker is not None and marker.text().strip() and marker.text().strip() not in known:
            stale.append(marker)
        for widget in stale:
            widget.setText("")
        return len(stale)

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
            "sentence_translation": self.sentence_translation_field_input.text().strip(),
            "language": self.language_field_input.text().strip(),
        }
        # Language-scoped keys are contributed only while their row is on screen
        # (or the mapping already carried them). Keeps a ja anki_fields
        # byte-identical instead of seeding it with an empty zh key.
        for key, widget in self._hook_field_inputs.items():
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
        for key, widget in self._hook_field_inputs.items():
            widget.setText(fields.get(key, ""))
        self.pitch_position_field_input.setText(fields.get("pitch_position", ""))
        self.pitch_category_field_input.setText(fields.get("pitch_category", ""))
        self.pitch_graph_field_input.setText(fields.get("pitch_graph", ""))
        self.pitch_text_field_input.setText(fields.get("pitch_text", ""))
        self.frequency_field_input.setText(fields.get("frequency", ""))
        self.frequency_sort_field_input.setText(fields.get("frequency_sort", ""))
        self.source_field_input.setText(fields.get("source", ""))
        self.sentence_translation_field_input.setText(fields.get("sentence_translation", ""))
        self.language_field_input.setText(fields.get("language", ""))

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

    # === Card Creation (T10, moved from Word Filters) ===

    def get_strict_card_order(self) -> bool:
        """Return whether strict card-creation order is enabled."""
        return self.strict_card_order_checkbox.isChecked()

    def set_strict_card_order(self, value: bool) -> None:
        """Set the strict card-order checkbox."""
        self.strict_card_order_checkbox.setChecked(value)

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
            self._clear_status_parts("deck")

    def _on_notetype_selection_changed(self) -> None:
        """Clear the not-in-Anki warning once the user picks a real note type."""
        index = self.notetype_combo.currentIndex()
        if index >= 0 and not self.notetype_combo.itemData(index, Qt.ItemDataRole.ToolTipRole):
            self._clear_status_parts("notetype")

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
        """Enable or disable the "Fill in automatically" button."""
        self.fetch_fields_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.

        The deck and note-type problems are cleared first, because the message they carry
        belongs to the selection that was on screen before this load, not to
        the one being loaded. A settings import or profile switch can name a
        deck this collection does not have — ``set_deck_name`` inserts it as a
        phantom, and ``_on_deck_selection_changed`` deliberately stays silent
        for exactly that case (it only clears when the new item has no
        phantom tooltip) — so a green "5 decks loaded" would sit above a
        combo showing a deck that will fail the run. The refresh owns writing
        a message; nothing here invents one.
        """
        self._clear_status_parts("deck", "notetype")
        self.set_fill_status(None, "")
        self.set_deck_name(config.anki_deck_name)
        self.set_note_type(config.anki_note_type)
        self.set_ankiconnect_url(config.ankiconnect_url)
        self.set_anki_tags(config.anki_tags)
        self.set_card_fields(config.anki_fields)
        self.set_pitch_category_format(config.pitch_category_format)
        self.set_card_type(config.card_type)
        self.set_card_type_marker_fields(config.card_type_marker_fields)
        self.set_strict_card_order(config.strict_card_order)
        self.reading_tone_color_checkbox.setChecked(config.reading_tone_color)
        capabilities = get_profile(config_language(config)).capabilities
        self._presets_allowed = "note_presets" in capabilities
        apply_language_gate(self._language_gate_pairs, capabilities)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.
        """
        fields = self.get_card_fields()
        updated = replace(
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
            strict_card_order=self.get_strict_card_order(),
        )
        # Language-scoped: only written while the row is on screen, same
        # rationale as the hook fields above (see FilteringSettingsPanel's twin
        # comment before T10 moved this row here).
        if self.reading_tone_color_checkbox.isVisibleTo(self):
            updated = replace(updated, reading_tone_color=self.reading_tone_color_checkbox.isChecked())
        return updated
