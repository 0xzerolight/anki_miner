"""Tests for AnkiSettingsPanel — Glossary field wiring."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.services.note_presets import preset_by_id


def test_deck_and_notetype_are_strict_combos(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert isinstance(panel.deck_combo, QComboBox)
    assert isinstance(panel.notetype_combo, QComboBox)
    assert not panel.deck_combo.isEditable()
    assert not panel.notetype_combo.isEditable()


def test_refresh_buttons_are_visible_and_labelled(qtbot):
    """A strict combo makes Refresh the only way back from an empty list.

    These were empty ghost buttons — an invisible hit box. With a QLineEdit you
    could still type the name, so it merely looked odd; with a strict combo,
    open Settings while Anki is closed and there is nothing clickable to
    recover with (the first-show fetch is one-shot).
    """
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    for button in (panel.deck_sync_button, panel.notetype_sync_button):
        assert button.text().strip()
        assert button.toolTip().strip()
        # An explicit cap here would elide the label back into nothing.
        assert button.maximumWidth() >= button.sizeHint().width()


def test_saved_value_survives_when_anki_is_unreachable(qtbot):
    """Loading a config with Anki closed must not blank the saved names."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("JP::Mining")
    panel.set_note_type("Lapis")
    assert panel.get_deck_name() == "JP::Mining"
    assert panel.get_note_type() == "Lapis"


def test_refreshing_the_list_keeps_the_current_selection(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("JP::Mining")
    panel.set_available_decks(["Default", "JP::Mining", "JP::Sentences"])
    assert panel.get_deck_name() == "JP::Mining"
    assert panel.deck_combo.count() == 3


def test_refreshing_keeps_a_selection_absent_from_anki(qtbot):
    """A saved deck Anki no longer has stays selected rather than vanishing."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("Deleted Deck")
    panel.set_available_decks(["Default"])
    assert panel.get_deck_name() == "Deleted Deck"
    assert panel.deck_combo.count() == 2


def test_a_phantom_entry_is_marked(qtbot):
    """A name Anki does not have carries the not-in-Anki tooltip; a real one does not."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("Ghost")
    panel.set_available_decks(["Default"])
    ghost = panel.deck_combo.findText("Ghost")
    real = panel.deck_combo.findText("Default")
    assert panel.deck_combo.itemData(ghost, Qt.ItemDataRole.ToolTipRole)
    assert panel.deck_combo.itemData(real, Qt.ItemDataRole.ToolTipRole) is None
    # The item text is the config value and must stay byte-exact.
    assert panel.deck_combo.itemText(ghost) == "Ghost"


def test_empty_fetch_leaves_the_current_selection_alone(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("JP::Mining")
    panel.set_available_decks([])
    assert panel.get_deck_name() == "JP::Mining"


def test_repeated_loads_do_not_duplicate_items(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("JP::Mining")
    panel.set_deck_name("JP::Mining")
    assert panel.deck_combo.count() == 1


def test_picking_a_real_deck_clears_the_not_in_anki_warning(qtbot):
    """The warning must not outlive the problem it describes."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_deck_name("Ghost")
    panel.set_available_decks(["Default", "JP::Mining"])
    panel.set_deck_status(False, "Deck 'Ghost' is not in Anki — pick one below.")
    panel.deck_combo.setCurrentIndex(panel.deck_combo.findText("JP::Mining"))
    assert panel.deck_status.text() == ""


def test_setting_an_empty_name_clears_the_selection(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_note_type("Lapis")
    panel.set_note_type("")
    assert panel.get_note_type() == ""


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
        # Custom CSS field removed — lock against reintroduction.
        "custom_css_edit",
        "get_custom_css",
        "set_custom_css",
    ):
        assert not hasattr(panel, attr)


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


def test_load_from_config_clears_a_status_from_the_previous_selection(qtbot, test_config):
    """A settings import or profile switch must not leave a stale green count.

    ``set_deck_name`` inserts a name Anki does not report as a phantom, and
    ``_on_deck_selection_changed`` stays silent for exactly that case, so the
    count written by the last refresh would sit above a deck that will fail
    the run.
    """
    from dataclasses import replace

    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_available_decks(["Default", "JP::Mining"])
    panel.set_available_note_types(["Lapis"])
    panel.set_deck_status(True, "2 decks loaded")
    panel.set_notetype_status(True, "1 note type loaded")

    panel.load_from_config(replace(test_config, anki_deck_name="JP::Old", anki_note_type="Ghost"))

    assert panel.deck_status.text() == ""
    assert panel.notetype_status.text() == ""


# ---------------------------------------------------------------------------
# Note-type preset row
# ---------------------------------------------------------------------------


def test_preset_row_is_a_strict_combo_with_the_three_note_types(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert isinstance(panel.preset_combo, QComboBox)
    assert not panel.preset_combo.isEditable()
    ids = [panel.preset_combo.itemData(index) for index in range(panel.preset_combo.count())]
    assert ids == ["lapis", "kiku", "senren"]
    # Nothing preselected: applying is a deliberate act.
    assert panel.preset_combo.currentIndex() == -1
    assert panel.preset_apply_button.text().strip()
    assert panel.preset_apply_button.toolTip().strip()


def test_applying_lapis_fills_the_mapping_and_the_pitch_format(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_pitch_category_format("jp")

    panel.apply_note_type_preset(preset_by_id("lapis"))

    fields = panel.get_card_fields()
    assert fields["word"] == "Expression"
    assert fields["pitch_category"] == "PitchCategories"
    assert fields["source"] == "MiscInfo"
    assert fields["frequency_sort"] == "FreqSort"
    assert fields["sentence_reading"] == ""
    assert panel.get_pitch_category_format() == "romaji"
    assert panel.get_card_type_marker_fields()["sentence"] == "IsSentenceCard"
    assert panel.preset_status.text().strip()


def test_applying_senren_rewrites_the_markers_and_drops_an_unsupported_card_type(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_type("click")

    panel.apply_note_type_preset(preset_by_id("senren"))

    markers = panel.get_card_type_marker_fields()
    assert markers["sentence"] == "sentenceCard"
    assert markers["audio"] == "audioCard"
    assert markers["click"] == ""
    # Senren has no click card, so the selection cannot survive.
    assert panel.get_card_type() == ""
    assert panel.get_card_fields()["pitch_text"] == "pitchAccents"


def test_applying_a_preset_keeps_a_supported_card_type(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_type("sentence")

    panel.apply_note_type_preset(preset_by_id("senren"))

    assert panel.get_card_type() == "sentence"


def test_applying_a_preset_preserves_keys_the_panel_does_not_own(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"word": "Expression", "future_key": "Whatever"})

    panel.apply_note_type_preset(preset_by_id("lapis"))

    assert panel.get_card_fields()["future_key"] == "Whatever"


def test_apply_with_nothing_selected_reports_and_changes_nothing(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_fields({"word": "MyWord"})
    panel.preset_combo.setCurrentIndex(-1)

    panel.preset_apply_button.click()

    assert panel.get_card_fields()["word"] == "MyWord"
    assert panel.preset_status.text().strip()
    assert panel.preset_status.property("status") == "error"


def test_choosing_a_known_note_type_preselects_its_preset(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_available_note_types(["Lapis", "Senren", "Basic"])

    panel.set_note_type("Senren")

    assert panel.preset_combo.currentData() == "senren"


def test_an_unknown_note_type_leaves_the_preset_selection_alone(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_available_note_types(["Lapis", "Lapis-modified"])

    panel.set_note_type("Lapis")
    panel.set_note_type("Lapis-modified")

    assert panel.preset_combo.currentData() == "lapis"


def test_apply_fills_an_empty_note_type_but_never_overwrites_one(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_note_type("")

    panel.apply_note_type_preset(preset_by_id("kiku"))
    assert panel.get_note_type() == "Kiku"

    panel.set_note_type("Lapis-modified")
    panel.apply_note_type_preset(preset_by_id("kiku"))
    assert panel.get_note_type() == "Lapis-modified"


def test_auto_map_matches_the_plural_names_the_real_note_types_use():
    """The keyword pass is the fallback for FORKS of Lapis / Kiku / Senren."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields

    mapped = auto_map_fields(["PitchCategories", "MiscInfo", "pitchPositions", "frequencies", "pitchAccents"])

    assert mapped["pitch_category"] == "PitchCategories"
    assert mapped["source"] == "MiscInfo"
    assert mapped["pitch_position"] == "pitchPositions"
    assert mapped["frequency"] == "frequencies"
    assert mapped["pitch_text"] == "pitchAccents"


def test_auto_map_still_prefers_the_singular_when_both_exist():
    from anki_miner.gui.widgets.panels.anki_settings_panel import auto_map_fields

    mapped = auto_map_fields(["PitchPosition", "pitchPositions"])

    assert mapped["pitch_position"] == "PitchPosition"
