"""Tests for AnkiSettingsPanel — Glossary field wiring."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel


def test_glossary_field_get_set_roundtrip(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_fields(
        {
            "word": "Expression",
            "sentence": "Sentence",
            "definition": "MainDefinition",
            "glossary": "Glossary",
            "picture": "Picture",
            "audio": "SentenceAudio",
            "expression_furigana": "ExpressionFurigana",
            "expression_reading": "",
            "sentence_furigana": "SentenceFurigana",
            "sentence_reading": "",
            "pitch_position": "",
            "pitch_category": "",
            "frequency": "",
        }
    )

    out = panel.get_card_fields()
    assert out["glossary"] == "Glossary"


def test_glossary_field_default_blank(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    # Setter called with no glossary key — widget should default to "".
    panel.set_card_fields({})
    assert panel.get_card_fields()["glossary"] == ""


def test_populate_from_field_list_matches_glossary(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.populate_from_field_list(["Expression", "Sentence", "MainDefinition", "Glossary"])
    assert panel.get_card_fields()["glossary"] == "Glossary"


def test_get_card_fields_includes_source(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.source_field_input.setText("MySource")
    assert panel.get_card_fields()["source"] == "MySource"


def test_set_card_fields_populates_source(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"source": "MySource"})
    assert panel.source_field_input.text() == "MySource"


def test_source_field_get_set_roundtrip(qtbot):
    """Regression guard: source must survive set -> get (else dropped on save)."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"source": "Origin"})
    out = panel.get_card_fields()
    assert out["source"] == "Origin"


def test_source_field_default_blank(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({})
    assert panel.get_card_fields()["source"] == ""


def test_populate_from_field_list_matches_source(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.populate_from_field_list(["Expression", "Sentence", "Source"])
    assert panel.get_card_fields()["source"] == "Source"


def test_expression_audio_field_get_set_roundtrip(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"expression_audio": "ExpressionAudio"})
    assert panel.get_card_fields()["expression_audio"] == "ExpressionAudio"


def test_expression_audio_field_default_blank(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({})
    assert panel.get_card_fields()["expression_audio"] == ""


def test_populate_from_field_list_matches_expression_audio(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.populate_from_field_list(["Expression", "SentenceAudio", "ExpressionAudio"])
    fields = panel.get_card_fields()
    assert fields["expression_audio"] == "ExpressionAudio"
    # Sentence audio mapping must not be hijacked by the new keyword.
    assert fields["audio"] == "SentenceAudio"


def test_expression_audio_has_no_enable_checkbox(qtbot):
    """The dedicated enable checkbox was removed; the field name is the switch."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "expression_audio_checkbox")
    assert not hasattr(panel, "get_expression_audio_enabled")
    assert not hasattr(panel, "set_expression_audio_enabled")


def test_expression_audio_field_defaults_empty_and_roundtrips(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.get_card_fields()["expression_audio"] == ""
    panel.set_card_fields({"expression_audio": "ExpressionAudio"})
    assert panel.get_card_fields()["expression_audio"] == "ExpressionAudio"


# === Card styling (Issue #44) ===


def test_no_styling_buttons_or_checkbox(qtbot):
    """Card styling is self-contained per card — no note-type sync UI on the panel."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    for attr in (
        "apply_styling_button",
        "remove_styling_button",
        "apply_styling_requested",
        "remove_styling_requested",
        "manage_styling_checkbox",
    ):
        assert not hasattr(panel, attr)


def test_custom_css_always_editable(qtbot):
    """The Custom CSS box is always enabled — there is no manage toggle to gate it."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.custom_css_edit.isEnabled()


def test_custom_css_get_set_round_trip(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    css = '[data-sc-content|="example-sentence"] { display: none; }'
    panel.set_custom_css(css)
    assert panel.get_custom_css() == css


# ---------------------------------------------------------------------------
# Task 1: Auto-Map Fields button prominence + _FIELD_KEYWORDS constant
# ---------------------------------------------------------------------------


def test_fetch_fields_button_label(qtbot):
    """Button text must be the new 'Auto-Map Fields from Note Type' label."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.fetch_fields_button.text() == "Auto-Map Fields from Note Type"


def test_fetch_fields_button_variant_is_primary(qtbot):
    """Button must use the primary variant (objectName == 'primary')."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.fetch_fields_button.objectName() == "primary"


def test_fetch_fields_button_emits_signal(qtbot):
    """Clicking the button must still emit fetch_fields_requested after the move."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.fetch_fields_requested, timeout=1000):
        panel.fetch_fields_button.click()


def test_field_keywords_constant_importable():
    """_FIELD_KEYWORDS must be importable at module level."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import _FIELD_KEYWORDS  # noqa: PLC0415

    assert isinstance(_FIELD_KEYWORDS, dict)
    # Check a representative sample of keys and keywords
    assert "word" in _FIELD_KEYWORDS
    assert "expression" in _FIELD_KEYWORDS["word"]
    assert "sentence" in _FIELD_KEYWORDS
    assert "sentence" in _FIELD_KEYWORDS["sentence"]


def test_populate_from_field_list_uses_field_keywords(qtbot):
    """populate_from_field_list must still auto-map using the extracted constant."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    # Use names that match via _FIELD_KEYWORDS lookup
    panel.populate_from_field_list(
        [
            "Expression",
            "Sentence",
            "MainDefinition",
            "Glossary",
            "Picture",
            "SentenceAudio",
            "ExpressionAudio",
            "ExpressionFurigana",
            "ExpressionReading",
            "SentenceFurigana",
            "SentenceReading",
            "PitchPosition",
            "PitchCategory",
            "Frequency",
            "Source",
        ]
    )
    fields = panel.get_card_fields()
    assert fields["word"] == "Expression"
    assert fields["sentence"] == "Sentence"
    assert fields["definition"] == "MainDefinition"
    assert fields["glossary"] == "Glossary"
    assert fields["source"] == "Source"


# ---------------------------------------------------------------------------
# Task 3: pure auto_map_fields helper (Qt-free)
# ---------------------------------------------------------------------------


def test_auto_map_fields_importable_and_returns_all_keys():
    """auto_map_fields is a module-level pure function returning every key."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import (  # noqa: PLC0415
        _FIELD_KEYWORDS,
        auto_map_fields,
    )

    result = auto_map_fields([])
    assert set(result.keys()) == set(_FIELD_KEYWORDS.keys())
    # Nothing matched → every value is empty string.
    assert all(v == "" for v in result.values())


def test_auto_map_fields_matches_common_lapis_fields():
    """The helper maps a typical Lapis-style field list exactly like the old inline logic."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields  # noqa: PLC0415

    result = auto_map_fields(
        [
            "Expression",
            "Sentence",
            "MainDefinition",
            "Glossary",
            "Picture",
            "SentenceAudio",
            "ExpressionAudio",
            "ExpressionFurigana",
            "ExpressionReading",
            "SentenceFurigana",
            "SentenceReading",
            "PitchPosition",
            "PitchCategory",
            "Frequency",
            "Source",
        ]
    )
    assert result["word"] == "Expression"
    assert result["sentence"] == "Sentence"
    assert result["definition"] == "MainDefinition"
    assert result["glossary"] == "Glossary"
    assert result["picture"] == "Picture"
    assert result["audio"] == "SentenceAudio"
    assert result["expression_audio"] == "ExpressionAudio"
    assert result["source"] == "Source"


def test_auto_map_fields_normalizes_spaces_and_underscores_case_insensitive():
    """Matching strips spaces/underscores from the field name, case-insensitive.

    The WHOLE normalized field name must equal a keyword (exact prior behavior):
    "main_definition" → "maindefinition" matches; "pitch accent" → "pitchaccent"
    matches; an unrelated prefix like "my expression" → "myexpression" does NOT.
    """
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields  # noqa: PLC0415

    result = auto_map_fields(["EXPRESSION", "main_definition", "Pitch Accent"])
    assert result["word"] == "EXPRESSION"
    assert result["definition"] == "main_definition"
    assert result["pitch_position"] == "Pitch Accent"

    # A field name that is not exactly a keyword (after normalization) must not match.
    assert auto_map_fields(["my expression"])["word"] == ""


def test_auto_map_fields_first_match_wins_per_key():
    """First field that matches a key's keywords wins (loop order over field_names)."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields  # noqa: PLC0415

    # Both "Expression" and "Vocab" match the word key; the first in the list wins.
    result = auto_map_fields(["Expression", "Vocab"])
    assert result["word"] == "Expression"
    result2 = auto_map_fields(["Vocab", "Expression"])
    assert result2["word"] == "Vocab"


def test_auto_map_fields_does_not_hijack_sentence_audio_for_expression_audio():
    """SentenceAudio maps to audio, ExpressionAudio to expression_audio (no cross-talk)."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields  # noqa: PLC0415

    result = auto_map_fields(["SentenceAudio", "ExpressionAudio"])
    assert result["audio"] == "SentenceAudio"
    assert result["expression_audio"] == "ExpressionAudio"


def test_frequency_sort_field_get_set_roundtrip(qtbot):
    """Regression guard: frequency_sort must survive set -> get.

    The bug this guards: get_card_fields() omitting the key drops it from
    config.anki_fields on every Save, making the FreqSort field unreachable.
    """
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"frequency_sort": "FrequencySort"})
    out = panel.get_card_fields()
    assert out["frequency_sort"] == "FrequencySort"


def test_frequency_sort_field_default_blank(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({})
    assert panel.get_card_fields()["frequency_sort"] == ""


def test_frequency_sort_independent_of_frequency(qtbot):
    """The breakdown (frequency) and sort (frequency_sort) fields map separately."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"frequency": "Frequency", "frequency_sort": "FrequencySort"})
    out = panel.get_card_fields()
    assert out["frequency"] == "Frequency"
    assert out["frequency_sort"] == "FrequencySort"


def test_auto_map_fields_frequency_sort_no_collision():
    """FrequencySort maps to frequency_sort and does NOT hijack the frequency key."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields  # noqa: PLC0415

    result = auto_map_fields(["Frequency", "FrequencySort"])
    assert result["frequency"] == "Frequency"
    assert result["frequency_sort"] == "FrequencySort"
    # FreqSort alias also matches.
    assert auto_map_fields(["FreqSort"])["frequency_sort"] == "FreqSort"


def test_card_type_get_set_roundtrip(qtbot):
    """The card-type dropdown round-trips its id; an unknown id falls back to ''."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_type("audio")
    assert panel.get_card_type() == "audio"

    panel.set_card_type("nonsense")
    assert panel.get_card_type() == ""


def test_card_type_marker_fields_default_and_collapsed(qtbot):
    """Marker names prefill to JPMN defaults; the editor group starts collapsed."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    assert panel.get_card_type_marker_fields() == {
        "word_and_sentence": "IsWordAndSentenceCard",
        "click": "IsClickCard",
        "sentence": "IsSentenceCard",
        "audio": "IsAudioCard",
    }
    assert panel.card_type_names_group.isCheckable()
    assert not panel.card_type_names_group.isChecked()
    assert not panel._card_type_names_body.isVisible()


def test_card_type_marker_group_toggle_reveals_body(qtbot):
    """Checking the group reveals the marker-name editors."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.show()

    panel.card_type_names_group.setChecked(True)
    assert panel._card_type_names_body.isVisible()


def test_card_type_marker_fields_setter_defaults_missing_keys(qtbot):
    """A partial mapping fills only the given keys; the rest keep JPMN defaults."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_type_marker_fields({"click": "Custom"})
    out = panel.get_card_type_marker_fields()
    assert out["click"] == "Custom"
    assert out["audio"] == "IsAudioCard"


# ---------------------------------------------------------------------------
# Pitch graph/overline field inputs (shipped opt-in keys) and unknown-key
# preservation across a Save round-trip.
# ---------------------------------------------------------------------------

_NEW_FIELD_KEYS = (
    "pitch_graph",
    "pitch_text",
)


@pytest.mark.parametrize("key", _NEW_FIELD_KEYS)
def test_new_field_get_set_roundtrip(qtbot, key):
    """Each opt-in field key survives set_card_fields -> get_card_fields."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_fields({key: "MyField"})
    assert panel.get_card_fields()[key] == "MyField"


@pytest.mark.parametrize("key", _NEW_FIELD_KEYS)
def test_new_field_default_blank(qtbot, key):
    """Each opt-in field key defaults to "" when absent from the mapping."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_fields({})
    assert panel.get_card_fields()[key] == ""


@pytest.mark.parametrize("key", _NEW_FIELD_KEYS)
def test_new_field_has_labeled_input_widget(qtbot, key):
    """Every new key has a dedicated QLineEdit input on the panel."""
    from PyQt6.QtWidgets import QLineEdit  # noqa: PLC0415

    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    widget = getattr(panel, f"{key}_field_input")
    assert isinstance(widget, QLineEdit)


def test_get_card_fields_preserves_unknown_keys(qtbot):
    """Keys the panel does not own survive a set -> get round-trip.

    Regression guard: a user who opted into a future/unexposed key via
    gui_config.json must not have it wiped on the next Settings Save.
    """
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    panel.set_card_fields({"word": "Expression", "future_unknown_key": "SomeField"})
    out = panel.get_card_fields()
    assert out["future_unknown_key"] == "SomeField"
    # Owned keys still come from the inputs.
    assert out["word"] == "Expression"


def test_get_card_fields_no_unknown_keys_when_none_loaded(qtbot):
    """A fresh panel with no prior load contributes only owned keys."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)

    out = panel.get_card_fields()
    assert "future_unknown_key" not in out
    # Sanity: the owned + new keys are all present.
    for key in _NEW_FIELD_KEYS:
        assert key in out


def test_unknown_key_survives_full_save_round_trip(qtbot):
    """An opt-in key set in config survives load_from_config -> contribute (Save)."""
    from dataclasses import replace  # noqa: PLC0415

    from anki_miner.config import AnkiMinerConfig  # noqa: PLC0415

    base = AnkiMinerConfig()
    original = replace(base, anki_fields={**dict(base.anki_fields), "future_unknown_key": "SomeField"})

    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(original)
    result = panel.contribute(AnkiMinerConfig())

    assert result.anki_fields["future_unknown_key"] == "SomeField"
