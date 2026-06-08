"""Tests for AnkiSettingsPanel — Glossary field wiring."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel

# QApplication must exist before any widget is instantiated.
_app = QApplication.instance() or QApplication([])


def test_glossary_field_get_set_roundtrip():
    panel = AnkiSettingsPanel()

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


def test_glossary_field_default_blank():
    panel = AnkiSettingsPanel()
    # Setter called with no glossary key — widget should default to "".
    panel.set_card_fields({})
    assert panel.get_card_fields()["glossary"] == ""


def test_populate_from_field_list_matches_glossary():
    panel = AnkiSettingsPanel()
    panel.populate_from_field_list(["Expression", "Sentence", "MainDefinition", "Glossary"])
    assert panel.get_card_fields()["glossary"] == "Glossary"


def test_get_card_fields_includes_source():
    panel = AnkiSettingsPanel()
    panel.source_field_input.setText("MySource")
    assert panel.get_card_fields()["source"] == "MySource"


def test_set_card_fields_populates_source():
    panel = AnkiSettingsPanel()
    panel.set_card_fields({"source": "MySource"})
    assert panel.source_field_input.text() == "MySource"


def test_source_field_get_set_roundtrip():
    """Regression guard: source must survive set -> get (else dropped on save)."""
    panel = AnkiSettingsPanel()
    panel.set_card_fields({"source": "Origin"})
    out = panel.get_card_fields()
    assert out["source"] == "Origin"


def test_source_field_default_blank():
    panel = AnkiSettingsPanel()
    panel.set_card_fields({})
    assert panel.get_card_fields()["source"] == ""


def test_populate_from_field_list_matches_source():
    panel = AnkiSettingsPanel()
    panel.populate_from_field_list(["Expression", "Sentence", "Source"])
    assert panel.get_card_fields()["source"] == "Source"
