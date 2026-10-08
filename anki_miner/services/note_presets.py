"""Published field maps for the three note types most Japanese miners use, and Anki Miner Note.

Lapis, Kiku and Senren all ship a fixed, documented field list, so the mapping
Settings -> Anki asks for is knowable without querying AnkiConnect and without
guessing from field names. Anki Miner Note is a Lapis-based note type for every
mining language, and the one preset a non-Japanese language may apply. Each
preset carries three things the keyword matcher (``auto_map_fields`` below)
cannot:

* the exact names, including the ones the heuristic misses (``PitchCategories``
  is plural; ``MiscInfo`` matches no ``source`` keyword),
* ``pitch_category_format`` -- all four read categories as romaji, while the
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
* Anki Miner Note -- ``0xzerolight/anki_miner_note``, ``build/fields.lock``

This module is deliberately leaf: no imports from ``config`` or ``gui``, so the
settings panel and the setup wizard can both read it. The ``Literal`` aliases
below duplicate the ones on ``AnkiMinerConfig`` for that reason;
``test_note_presets.py`` pins them in step with the config's ``anki_fields``
and ``card_type_marker_fields`` keys, so adding a key without answering it here
fails the suite.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # type-only: this module stays a leaf at runtime
    from anki_miner.languages.profile import CardFieldSpec

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

#: Every field Anki Miner Note v1 ships (its build/fields.lock; append-only, so a
#: later version still matches). Written out literally, the same 46 names as
#: tests/unit/amn_fields.py -- never derived from the test list. The first 22
#: are Lapis's, so detection must try it before Lapis (and Kiku, whose
#: RelatedExpression it lacks, can never shadow it).
_ANKI_MINER_NOTE_SIGNATURE = frozenset(
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
        "SentenceTranslation",
        "Language",
        # The per-language fields from here on are not in the preset's map:
        # each one is a profile placeholder, matched by auto_map_profile_fields
        # for the language that declares it.
        "Pinyin",
        "Jyutping",
        "Traditional",
        "MeasureWord",
        "Hanja",
        "HanViet",
        "Gender",
        "Article",
        "Plural",
        "PartOfSpeech",
        "AspectPair",
        "Root",
        "Binyan",
        "Transliteration",
        "Romanization",
        "Colloquial",
        "PresentStem",
        "Classifier",
        "Affixes",
        "Formal",
        "Grammar",
        "Segmentation",
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
    "language": "",
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
        every_language: Applies in every mining language. False for the
            Japanese note types, which ride the ``note_presets`` capability.
        bold_target_in_sentence: The note type's cards rely on the target word
            being bold in the sentence, so applying it also turns
            ``bold_target_in_sentence`` on.
    """

    id: str
    name: str
    url: str
    fields: Mapping[str, str]
    pitch_category_format: Literal["jp", "romaji"]
    card_type_marker_fields: Mapping[str, str]
    supported_card_types: tuple[CardTypeId, ...]
    signature: frozenset[str]
    every_language: bool = False
    bold_target_in_sentence: bool = False


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
        "language": "",
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

ANKI_MINER_NOTE = NotePreset(
    id="anki_miner_note",
    name="Anki Miner Note",
    url="https://github.com/0xzerolight/anki_miner_note",
    fields={**_JPMN_FIELDS, "sentence_translation": "SentenceTranslation", "language": "Language"},
    # Lapis's back script, unchanged.
    pitch_category_format="romaji",
    card_type_marker_fields=_JPMN_MARKERS,
    supported_card_types=_JPMN_CARD_TYPES,
    signature=_ANKI_MINER_NOTE_SIGNATURE,
    every_language=True,
    # The audio card hides the <b> word; the sentence and click cards cue it.
    bold_target_in_sentence=True,
)

#: Display order: most widely used first.
NOTE_PRESETS: tuple[NotePreset, ...] = (LAPIS, KIKU, SENREN, ANKI_MINER_NOTE)


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


def preset_for_field_names(field_names: Iterable[str], *, japanese_presets: bool = True) -> NotePreset | None:
    """Recognize a note type from the field names AnkiConnect reported.

    A preset matches when every field it ships is present, so a fork that ADDS
    fields still matches the note type it forked. Candidates are tried
    largest-signature-first because Kiku and Anki Miner Note are strict
    supersets of Lapis and the more specific answer has to win.

    ``japanese_presets=False`` leaves only the ``every_language`` presets as
    candidates, so a Lapis note type under another language matches nothing.
    """
    available = set(field_names)
    candidates = [preset for preset in NOTE_PRESETS if preset.every_language or japanese_presets]
    for preset in sorted(candidates, key=lambda item: -len(item.signature)):
        if preset.signature <= available:
            return preset
    return None


# ---------------------------------------------------------------------------
# "Fill in automatically" (D13, UI/UX audit 2026-09-29)
#
# One Qt-free pass shared by Settings -> Cards & Anki and the setup wizard, so
# the two can never map the same note type differently. It recognises Lapis,
# Kiku, Senren and Anki Miner Note by their field names (the preset carries
# what keywords cannot: exact plural names, romaji pitch categories, Senren's
# marker names) and otherwise runs the keyword table below. The first three
# apply in Japanese only; Anki Miner Note applies in every language, its
# per-language fields mapped by the profile placeholders. Moved here from
# gui/widgets/panels/anki_settings_panel.py, which re-exports the old names.
# ---------------------------------------------------------------------------

# Keywords used to auto-map Anki field names. Each key is a card data type; the
# list is lowercase/stripped patterns that a field name must match (after
# lowercasing and removing spaces/underscores).
FIELD_KEYWORDS: dict[str, list[str]] = {
    # The Chinese spellings are a subset of the word-field aliases in
    # services/expression_field.py (which also reads 简体, 簡體, 繁体, 繁體, 生词
    # and 詞語), and the two match differently: that one looks for an alias
    # anywhere inside the field name, this one wants the whole normalised name.
    # The traditional spellings stay out on purpose: zh carries a dedicated
    # expression_traditional key, and on a note type that lists Traditional
    # above Simplified it would silently become the card front.
    "word": ["expression", "word", "vocab", "hanzi", "simplified", "汉字", "漢字", "中文", "单词", "词语"],
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
    "sentence_translation": ["sentencetranslation", "translation", "sentencemeaning"],
    "language": ["language", "lang"],
}


def normalized_field_name(name: str) -> str:
    """The spelling both matchers compare on: lowercase, no spaces or underscores."""
    return name.lower().replace(" ", "").replace("_", "")


def auto_map_fields(field_names: Sequence[str]) -> dict[str, str]:
    """Map Anki field names to card data keys via :data:`FIELD_KEYWORDS`.

    Pure (Qt-free) so the setup wizard and the settings panel share one
    matching algorithm. For every key in ``FIELD_KEYWORDS``, returns the first
    field name (in ``field_names`` order) that matches after lowercasing and
    removing spaces/underscores; unmatched keys map to ``""``.

    Args:
        field_names: Field names fetched from AnkiConnect.

    Returns:
        ``{field_key: matched_field_name_or_""}`` for every key in
        ``FIELD_KEYWORDS``.
    """
    mapping: dict[str, str] = {}
    for key, keywords in FIELD_KEYWORDS.items():
        normalized = [kw.lower() for kw in keywords]
        matched = ""
        for field_name in field_names:
            if normalized_field_name(field_name) in normalized:
                matched = field_name
                break
        mapping[key] = matched
    return mapping


def auto_map_profile_fields(
    field_names: Sequence[str],
    specs: Sequence[CardFieldSpec],
    claimed: Iterable[str],
) -> dict[str, str]:
    """Map Anki field names to the card-field keys ``specs`` declares.

    The profile-declared rows match on their own spec's placeholder (or one of
    its ``aliases``; the first field in ``field_names`` order wins) instead of
    through :data:`FIELD_KEYWORDS`: a keyword entry there is stamped into
    every language's ``anki_fields`` by the wizard's sanitizer, which would
    seed an empty Pinyin key into a Japanese mapping. So the caller passes only
    the specs the active language actually shows — a hidden row contributes no
    key anyway.

    ``claimed`` is the field names :func:`auto_map_fields` already took: a
    placeholder spelled like a keyword field (say "Reading", which
    ``expression_reading`` matches) must not take that field too, because one
    Anki field cannot carry two logical keys.

    Args:
        field_names: Field names fetched from AnkiConnect.
        specs: The card-field specs to match, in priority order.
        claimed: Field names another key already holds.

    Returns:
        ``{spec_key: matched_field_name}`` for the specs that matched; a spec
        with no match is absent, never present as ``""``.
    """
    taken = {name for name in claimed if name}
    mapping: dict[str, str] = {}
    for spec in specs:
        spellings = {normalized_field_name(name) for name in (spec.placeholder, *spec.aliases)}
        match = next(
            (n for n in field_names if n not in taken and normalized_field_name(n) in spellings),
            "",
        )
        if match:
            mapping[spec.key] = match
            taken.add(match)
    return mapping


@dataclass(frozen=True)
class NoteTypeFill:
    """What "Fill in automatically" proposes for one note type's field names.

    Attributes:
        preset: The community note type recognised by its field names, or None.
            With a preset, ``fields`` is the preset's whole map (``""`` entries
            included, which are real answers) and the caller also applies the
            preset's pitch format, marker names and supported card types -- the
            same writes the old Preset -> Apply made -- and turns bold target
            words on when the preset's ``bold_target_in_sentence`` says so.
        fields: ``anki_fields`` key -> field name. Without a preset this is the
            keyword pass: every ``FIELD_KEYWORDS`` key, ``""`` where nothing
            matched.
        extra_fields: The active language's extra card fields (Pinyin, Hanja,
            ...) matched on their own spelling. A spec with no match is absent.
    """

    preset: NotePreset | None
    fields: Mapping[str, str]
    extra_fields: Mapping[str, str]


def fill_note_type_fields(
    field_names: Sequence[str],
    *,
    allow_presets: bool,
    extra_specs: Sequence[CardFieldSpec] = (),
) -> NoteTypeFill:
    """Propose every mapping for a note type from the field names Anki reported.

    Args:
        field_names: The note type's fields, from AnkiConnect ``modelFieldNames``.
        allow_presets: Whether the Japanese presets (Lapis, Kiku, Senren) are
            eligible, i.e. whether the active language carries the
            ``note_presets`` capability. Applying one elsewhere writes
            furigana and pitch mappings the language cannot fill, so callers
            pass ``"note_presets" in profile.capabilities``. Every-language
            presets (Anki Miner Note) are always eligible.
        extra_specs: The card-field specs the active language shows.

    Returns:
        The proposal. Callers decide how to write it: Settings overwrites the
        rows it shows, the wizard stages it on its working config.
    """
    preset = preset_for_field_names(field_names, japanese_presets=allow_presets)
    fields: Mapping[str, str] = dict(preset.fields) if preset is not None else auto_map_fields(field_names)
    extra = auto_map_profile_fields(field_names, extra_specs, fields.values())
    return NoteTypeFill(preset=preset, fields=fields, extra_fields=extra)
