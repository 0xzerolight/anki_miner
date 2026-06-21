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


# === Card styling (Issue #44 / auto-sync) ===


def test_styling_combo_lists_off_first(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.card_style_preset_combo.itemData(0) == "off"
    assert panel.card_style_preset_combo.itemText(0) == "Off"


def test_styling_apply_remove_buttons_removed(qtbot):
    """The Apply/Remove buttons and their signals are gone (auto-sync replaces them)."""
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "apply_styling_button")
    assert not hasattr(panel, "remove_styling_button")
    assert not hasattr(panel, "apply_styling_requested")
    assert not hasattr(panel, "remove_styling_requested")


def test_styling_preset_get_set_unknown_falls_back_to_off(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_style_preset("minimal")
    assert panel.get_card_style_preset() == "minimal"
    panel.set_card_style_preset("does-not-exist")
    assert panel.get_card_style_preset() == "off"


def test_off_disables_custom_css_box(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_style_preset("minimal")
    assert panel.custom_css_edit.isEnabled()
    panel.set_card_style_preset("off")
    assert not panel.custom_css_edit.isEnabled()


def test_programmatic_set_does_not_mark_user_touched(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.set_card_style_preset("minimal")
    panel.set_custom_css(".x{}")
    assert panel.is_styling_user_touched() is False


def test_user_combo_change_marks_touched(qtbot):
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.reset_styling_user_touched()
    # Simulate a real user pick (the signal fires for non-blocked changes).
    idx = panel.card_style_preset_combo.findData("minimal")
    panel.card_style_preset_combo.setCurrentIndex(idx)
    assert panel.is_styling_user_touched() is True


def test_load_from_config_resets_touched(qtbot):
    from anki_miner.config import create_default_config

    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    idx = panel.card_style_preset_combo.findData("minimal")
    panel.card_style_preset_combo.setCurrentIndex(idx)  # user edit -> touched
    assert panel.is_styling_user_touched() is True

    panel.load_from_config(create_default_config())
    assert panel.is_styling_user_touched() is False
    # Default config is Off, so the CSS box is greyed.
    assert panel.get_card_style_preset() == "off"
    assert not panel.custom_css_edit.isEnabled()
