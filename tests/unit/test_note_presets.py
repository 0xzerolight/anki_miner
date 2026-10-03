"""Tests for the published note-type presets and their detection."""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.note_presets import (
    NOTE_PRESETS,
    preset_by_id,
    preset_for_field_names,
    preset_for_note_type_name,
)

LAPIS_FIELDS = [
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
]
KIKU_FIELDS = [*LAPIS_FIELDS, "RelatedExpression", "SentenceTranslation"]
SENREN_FIELDS = [
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
]


def test_presets_are_listed_in_display_order():
    assert [preset.id for preset in NOTE_PRESETS] == ["lapis", "kiku", "senren"]


def test_every_preset_covers_every_config_field_key():
    """A new anki_fields key must be answered by every preset, even with ""."""
    expected = set(AnkiMinerConfig().anki_fields)
    for preset in NOTE_PRESETS:
        assert set(preset.fields) == expected, preset.id


def test_preset_field_values_exist_on_the_note_type():
    """Every mapped name is in the signature, so a matched preset never maps a
    field the note type lacks."""
    for preset in NOTE_PRESETS:
        mapped = {value for value in preset.fields.values() if value}
        assert mapped <= preset.signature, preset.id


def test_lapis_maps_what_the_keyword_heuristic_misses():
    lapis = preset_by_id("lapis")
    assert lapis is not None
    assert lapis.fields["pitch_category"] == "PitchCategories"
    assert lapis.fields["source"] == "MiscInfo"
    assert lapis.fields["frequency_sort"] == "FreqSort"
    assert lapis.pitch_category_format == "romaji"


def test_lapis_has_no_pitch_render_or_sentence_reading_fields():
    lapis = preset_by_id("lapis")
    assert lapis is not None
    assert lapis.fields["pitch_graph"] == ""
    assert lapis.fields["pitch_text"] == ""
    assert lapis.fields["sentence_reading"] == ""


def test_kiku_reuses_the_lapis_names():
    lapis, kiku = preset_by_id("lapis"), preset_by_id("kiku")
    assert lapis is not None and kiku is not None
    # Kiku is Lapis plus SentenceTranslation; every other name is shared.
    without = lambda fields: {k: v for k, v in fields.items() if k != "sentence_translation"}  # noqa: E731
    assert without(kiku.fields) == without(lapis.fields)
    assert lapis.fields["sentence_translation"] == ""  # Lapis has no such field
    assert kiku.fields["sentence_translation"] == "SentenceTranslation"


def test_no_preset_has_a_language_field():
    for preset in NOTE_PRESETS:
        assert preset.fields["language"] == "", preset.id


def test_senren_maps_its_translation_field():
    senren = preset_by_id("senren")
    assert senren is not None
    assert senren.fields["sentence_translation"] == "sentenceTranslation"


def test_senren_maps_pitch_text_and_word_audio():
    senren = preset_by_id("senren")
    assert senren is not None
    assert senren.fields["pitch_text"] == "pitchAccents"
    assert senren.fields["pitch_graph"] == ""
    assert senren.fields["expression_audio"] == "wordAudio"
    assert senren.fields["expression_reading"] == "reading"
    assert senren.fields["expression_furigana"] == ""
    assert senren.fields["source"] == "miscInfo"


def test_senren_has_only_sentence_and_audio_card_types():
    senren = preset_by_id("senren")
    assert senren is not None
    assert senren.card_type_marker_fields["sentence"] == "sentenceCard"
    assert senren.card_type_marker_fields["audio"] == "audioCard"
    assert senren.card_type_marker_fields["click"] == ""
    assert senren.card_type_marker_fields["word_and_sentence"] == ""
    assert set(senren.supported_card_types) == {"", "sentence", "audio"}


def test_every_preset_supports_the_disabled_card_type():
    for preset in NOTE_PRESETS:
        assert "" in preset.supported_card_types, preset.id


def test_every_preset_answers_all_four_marker_keys():
    """set_card_type_marker_fields defaults a MISSING key back to the JPMN
    name, so a preset that omits one would silently keep IsClickCard."""
    expected = set(AnkiMinerConfig().card_type_marker_fields)
    for preset in NOTE_PRESETS:
        assert set(preset.card_type_marker_fields) == expected, preset.id


def test_detection_prefers_kiku_over_lapis():
    """Kiku is a superset of Lapis; the more specific match must win."""
    kiku = preset_for_field_names(KIKU_FIELDS)
    lapis = preset_for_field_names(LAPIS_FIELDS)
    senren = preset_for_field_names(SENREN_FIELDS)
    assert kiku is not None and kiku.id == "kiku"
    assert lapis is not None and lapis.id == "lapis"
    assert senren is not None and senren.id == "senren"


def test_detection_tolerates_extra_fields():
    """A fork that adds a field is still the note type it forked."""
    matched = preset_for_field_names([*LAPIS_FIELDS, "MyOwnField"])
    assert matched is not None and matched.id == "lapis"


def test_detection_returns_none_for_a_stranger():
    assert preset_for_field_names(["Front", "Back"]) is None
    assert preset_for_field_names(LAPIS_FIELDS[:-1]) is None


def test_note_type_name_lookup_is_case_insensitive():
    lapis = preset_for_note_type_name("lapis")
    senren = preset_for_note_type_name("  Senren ")
    assert lapis is not None and lapis.id == "lapis"
    assert senren is not None and senren.id == "senren"
    assert preset_for_note_type_name("Lapis-modified") is None
    assert preset_for_note_type_name("") is None


# ---------------------------------------------------------------------------
# fill_note_type_fields: the one "Fill in automatically" pass (D13)
# ---------------------------------------------------------------------------

from anki_miner.languages.profile import CardFieldSpec  # noqa: E402
from anki_miner.services.note_presets import (  # noqa: E402
    FIELD_KEYWORDS,
    LAPIS,
    NoteTypeFill,
    auto_map_fields,
    fill_note_type_fields,
)


def test_fill_recognises_lapis_and_returns_its_whole_map():
    fill = fill_note_type_fields(LAPIS_FIELDS, allow_presets=True)

    assert isinstance(fill, NoteTypeFill)
    assert fill.preset is LAPIS
    assert dict(fill.fields) == dict(LAPIS.fields)


def test_fill_without_presets_runs_the_keyword_pass():
    fill = fill_note_type_fields(LAPIS_FIELDS, allow_presets=False)

    assert fill.preset is None
    assert dict(fill.fields) == auto_map_fields(LAPIS_FIELDS)
    assert set(fill.fields) == set(FIELD_KEYWORDS)


def test_fill_falls_back_to_keywords_for_an_unknown_note_type():
    fill = fill_note_type_fields(["Word", "Sentence", "Meaning"], allow_presets=True)

    assert fill.preset is None
    assert fill.fields["word"] == "Word"
    assert fill.fields["sentence"] == "Sentence"
    assert fill.fields["definition"] == "Meaning"


def test_fill_maps_extra_language_fields_without_reusing_a_claimed_field():
    specs = (
        CardFieldSpec(key="expression_pinyin", capability="pinyin", placeholder="Pinyin"),
        CardFieldSpec(key="reading_paiboon", capability="thai", placeholder="Reading"),
    )

    fill = fill_note_type_fields(["Hanzi", "Sentence", "Pinyin", "Reading"], allow_presets=False, extra_specs=specs)

    assert fill.extra_fields == {"expression_pinyin": "Pinyin"}
    # "Reading" is already expression_reading's, so the reading_paiboon spec gets nothing.
    assert fill.fields["expression_reading"] == "Reading"


def test_the_panel_module_still_re_exports_the_keyword_helpers():
    from anki_miner.gui.widgets.panels import anki_settings_panel as panel_module

    assert panel_module._FIELD_KEYWORDS is FIELD_KEYWORDS
    assert panel_module.auto_map_fields is auto_map_fields


# ---------------------------------------------------------------------------
# Profile placeholders against the Anki Miner Note field names
# ---------------------------------------------------------------------------

from anki_miner.languages.registry import get_profile  # noqa: E402
from anki_miner.services.note_presets import auto_map_profile_fields  # noqa: E402
from tests.unit.amn_fields import AMN_FIELDS  # noqa: E402


def test_hebrew_pos_maps_to_part_of_speech():
    specs = get_profile("he").extra_card_fields
    extra = auto_map_profile_fields(AMN_FIELDS, specs, auto_map_fields(AMN_FIELDS).values())
    assert extra["pos"] == "PartOfSpeech"


def test_thai_paiboon_maps_to_romanization():
    specs = get_profile("th").extra_card_fields
    extra = auto_map_profile_fields(AMN_FIELDS, specs, auto_map_fields(AMN_FIELDS).values())
    assert extra["reading_paiboon"] == "Romanization"
    assert extra["classifier"] == "Classifier"
