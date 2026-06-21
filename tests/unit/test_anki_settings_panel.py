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
