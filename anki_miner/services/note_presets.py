"""Published field maps for the three note types most Japanese miners use.

Lapis, Kiku and Senren all ship a fixed, documented field list, so the mapping
Settings -> Anki asks for is knowable without querying AnkiConnect and without
guessing from field names. Each preset carries three things the keyword matcher
in ``anki_settings_panel.auto_map_fields`` cannot:

* the exact names, including the ones the heuristic misses (``PitchCategories``
  is plural; ``MiscInfo`` matches no ``source`` keyword),
* ``pitch_category_format`` -- all three read categories as romaji, while the
  config default is ``"jp"``,
* the card-type marker field names -- Senren calls them ``sentenceCard`` /
  ``audioCard`` and has no click or word+sentence card at all.

Field names are copied byte-exact from upstream and are case-sensitive:

* Lapis   -- ``donkuri/lapis``, ``build/anki_fields.yaml`` (generates the apkg)
* Kiku    -- ``youyoumu/kiku``, ``apps/docs/mds/installation.md`` field table
  (``Tags`` / ``CardID`` in its ``AnkiFields`` TS type are Anki template
  specials, not note fields -- ``merge-context.ts`` strips them, and the
  installation table lists neither)
* Senren  -- ``BrenoAqua/Senren``, ``docs/yomitan.md`` field table

This module is deliberately leaf: no imports from ``config`` or ``gui``, so the
settings panel and the setup wizard can both read it. The ``Literal`` aliases
below duplicate the ones on ``AnkiMinerConfig`` for that reason;
``test_note_presets.py`` pins them in step with the config's ``anki_fields``
and ``card_type_marker_fields`` keys, so adding a key without answering it here
fails the suite.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

CardTypeId = Literal["", "word_and_sentence", "click", "sentence", "audio"]

# Lapis, verbatim from build/anki_fields.yaml.
_LAPIS_SIGNATURE = frozenset(
    {
        "Expression",
        "ExpressionFurigana",
        "ExpressionReading",
        "ExpressionAudio",
        "SelectionText",
        "MainDefinition",
        "DefinitionPicture",
        "Sentence",
        "SentenceFurigana",
        "SentenceAudio",
        "Picture",
        "Glossary",
        "Hint",
        "IsWordAndSentenceCard",
        "IsClickCard",
        "IsSentenceCard",
        "IsAudioCard",
        "PitchPosition",
        "PitchCategories",
        "Frequency",
        "FreqSort",
        "MiscInfo",
    }
)

# Kiku is Lapis plus two fields (added in its v2.0.0). Detection must therefore
# try Kiku first -- see preset_for_field_names.
_KIKU_SIGNATURE = _LAPIS_SIGNATURE | {"RelatedExpression", "SentenceTranslation"}

_SENREN_SIGNATURE = frozenset(
    {
        "word",
        "reading",
        "sentence",
        "sentenceFurigana",
        "sentenceTranslation",
        "sentenceCard",
        "audioCard",
        "notes",
        "selectionText",
        "definition",
        "wordAudio",
        "sentenceAudio",
        "picture",
        "glossary",
        "hint",
        "pitchAccents",
        "pitchPositions",
        "pitchCategories",
        "frequencies",
        "freqSort",
        "miscInfo",
        "dictionaryPreference",
    }
)

# Lapis and Kiku share every name anki_miner writes except Kiku's
# SentenceTranslation (the secondary-subtitle field); Kiku's RelatedExpression
# has no anki_miner counterpart.
_JPMN_FIELDS: Mapping[str, str] = {
    "word": "Expression",
    "sentence": "Sentence",
    "definition": "MainDefinition",
    "glossary": "Glossary",
    "picture": "Picture",
    "audio": "SentenceAudio",
    "expression_audio": "ExpressionAudio",
    "expression_furigana": "ExpressionFurigana",
    "expression_reading": "ExpressionReading",
    "sentence_furigana": "SentenceFurigana",
    # Neither note type has a sentence-reading field.
    "sentence_reading": "",
    "pitch_position": "PitchPosition",
    "pitch_category": "PitchCategories",
    # Both draw the pitch graph themselves from PitchPosition; there is no
    # field to put our rendered SVG or overline text in.
    "pitch_graph": "",
    "pitch_text": "",
    "frequency": "Frequency",
    "frequency_sort": "FreqSort",
    # MiscInfo is the "where did this come from" slot (Yomitan writes
    # {document-title} there); Lapis renders it at the bottom of the back.
    "source": "MiscInfo",
    # Lapis has no translation field; Kiku overrides below.
    "sentence_translation": "",
}

_JPMN_MARKERS: Mapping[str, str] = {
    "word_and_sentence": "IsWordAndSentenceCard",
    "click": "IsClickCard",
    "sentence": "IsSentenceCard",
    "audio": "IsAudioCard",
}

_JPMN_CARD_TYPES: tuple[CardTypeId, ...] = ("", "word_and_sentence", "click", "sentence", "audio")


@dataclass(frozen=True)
class NotePreset:
    """One community note type's published field map and card settings.

    Attributes:
        id: Stable identifier stored as combo item data. Never shown.
        name: Display name, and the note type's own name in Anki.
        url: Upstream project page, for the helper text / tooltip.
        fields: ``anki_fields`` key -> note field name. ``""`` means the note
            type has no field for that data, which is a real answer, not a gap.
        pitch_category_format: What the note type's CSS/JS expects to read.
        card_type_marker_fields: All four ``card_type_marker_fields`` keys ->
            marker field name, ``""`` where the note type has no such card.
        supported_card_types: Card type ids this note type can render, always
            including ``""`` (the disabled state).
        signature: Every field name the note type ships. Used for detection,
            and a superset of ``fields``' non-empty values, so a matched preset
            can never map a field the note type lacks.
    """

    id: str
    name: str
    url: str
    fields: Mapping[str, str]
    pitch_category_format: Literal["jp", "romaji"]
    card_type_marker_fields: Mapping[str, str]
    supported_card_types: tuple[CardTypeId, ...]
    signature: frozenset[str]


LAPIS = NotePreset(
    id="lapis",
    name="Lapis",
    url="https://github.com/donkuri/lapis",
    fields=_JPMN_FIELDS,
    # back.html matches PitchCategories against
    # (heiban|atamadaka|nakadaka|odaka|kifuku).
    pitch_category_format="romaji",
    card_type_marker_fields=_JPMN_MARKERS,
    supported_card_types=_JPMN_CARD_TYPES,
    signature=_LAPIS_SIGNATURE,
)

KIKU = NotePreset(
    id="kiku",
    name="Kiku",
    url="https://github.com/youyoumu/kiku",
    fields={**_JPMN_FIELDS, "sentence_translation": "SentenceTranslation"},
    pitch_category_format="romaji",
    card_type_marker_fields=_JPMN_MARKERS,
    supported_card_types=_JPMN_CARD_TYPES,
    signature=_KIKU_SIGNATURE,
)

SENREN = NotePreset(
    id="senren",
    name="Senren",
    url="https://github.com/BrenoAqua/Senren",
    fields={
        "word": "word",
        "sentence": "sentence",
        "definition": "definition",
        "glossary": "glossary",
        "picture": "picture",
        "audio": "sentenceAudio",
        "expression_audio": "wordAudio",
        # The word is rendered bare with the reading in <rt>; there is no
        # expression-furigana field to fill.
        "expression_furigana": "",
        "expression_reading": "reading",
        "sentence_furigana": "sentenceFurigana",
        "sentence_reading": "",
        "pitch_position": "pitchPositions",
        "pitch_category": "pitchCategories",
        # pitchAccents sits inside the word's <rt> and is styled through
        # .pronunciation-mora / .pronunciation-mora-line -- Yomitan pitch TEXT
        # markup, which is what render_pitch_text_field emits. There is no
        # .pronunciation-graph rule anywhere in the template, so no graph slot.
        "pitch_graph": "",
        "pitch_text": "pitchAccents",
        "frequency": "frequencies",
        "frequency_sort": "freqSort",
        "source": "miscInfo",
        "sentence_translation": "sentenceTranslation",
    },
    pitch_category_format="romaji",
    card_type_marker_fields={
        # Senren has no click or word+sentence card.
        "word_and_sentence": "",
        "click": "",
        "sentence": "sentenceCard",
        "audio": "audioCard",
    },
    supported_card_types=("", "sentence", "audio"),
    signature=_SENREN_SIGNATURE,
)

#: Display order: most widely used first.
NOTE_PRESETS: tuple[NotePreset, ...] = (LAPIS, KIKU, SENREN)


def preset_by_id(preset_id: object) -> NotePreset | None:
    """Return the preset with this id, or None. Accepts combo item data."""
    for preset in NOTE_PRESETS:
        if preset.id == preset_id:
            return preset
    return None


def preset_for_note_type_name(name: str) -> NotePreset | None:
    """Return the preset whose note type is named ``name`` (case-insensitive).

    Exact match only: "Lapis-modified" is a fork whose fields we have not seen,
    so it gets no preset from its name. Field-set detection still covers it.
    """
    normalized = name.strip().lower()
    if not normalized:
        return None
    for preset in NOTE_PRESETS:
        if preset.name.lower() == normalized:
            return preset
    return None


def preset_for_field_names(field_names: Iterable[str]) -> NotePreset | None:
    """Recognize a note type from the field names AnkiConnect reported.

    A preset matches when every field it ships is present, so a fork that ADDS
    fields still matches the note type it forked. Candidates are tried
    largest-signature-first because Kiku is a strict superset of Lapis and the
    more specific answer has to win.
    """
    available = set(field_names)
    for preset in sorted(NOTE_PRESETS, key=lambda item: -len(item.signature)):
        if preset.signature <= available:
            return preset
    return None
