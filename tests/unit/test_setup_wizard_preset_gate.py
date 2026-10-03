"""The wizard's note-type presets ride the same capability the Settings row does.

Lapis, Kiku and Senren are Japanese note types: their published field names map
``expression_furigana``, ``sentence_furigana``, ``pitch_position`` and
``pitch_category``. Applying one under a language that cannot fill those writes
mappings every run's field check then rejects — which is why Settings gates the
Preset row on ``note_presets``. The wizard reaches the presets from one place,
the fetch result, and must gate it. Anki Miner Note serves every language, so
the gate never holds it back.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.widgets.dialogs.setup_wizard import SetupWizard
from anki_miner.languages.switching import switch_language

# Lapis' published field list, trimmed to the names the preset and the keyword
# pass both care about; preset matching is by field-name set, not by length.
_LAPIS_FIELDS = [
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


@pytest.fixture
def notetype_page(qtbot, test_config):
    """Build a wizard on `code` and hand back its note-type page, primed."""

    def build(code):
        config = replace(test_config, ankiconnect_url="http://127.0.0.1:8765", anki_note_type="Lapis")
        wizard = SetupWizard(config if code == "ja" else switch_language(config, code))
        qtbot.addWidget(wizard)
        page = wizard.notetype_page
        page.select_note_type("Lapis", notify=False)
        page._fetched_note_types = ["Lapis"]
        page._field_names = list(_LAPIS_FIELDS)
        page._field_names_note_type = "Lapis"
        return wizard, page

    return build


class TestTheFetchResult:
    def test_japanese_applies_the_preset(self, notetype_page):
        wizard, page = notetype_page("ja")

        page._on_fields_fetched("Lapis", _LAPIS_FIELDS)

        config = wizard.working_config()
        assert config.anki_fields["pitch_category"] == "PitchCategories"
        assert config.pitch_category_format == "romaji"
        assert "Lapis" in page.mapping_summary.text()

    def test_chinese_is_left_to_the_keyword_pass(self, notetype_page):
        wizard, page = notetype_page("zh")

        page._on_fields_fetched("Lapis", _LAPIS_FIELDS)

        config = wizard.working_config()
        # The keyword pass is language-blind by design, so what separates it
        # from the preset is what lies OUTSIDE the field map: the romaji pitch
        # format the preset stages, and the summary naming the note type.
        assert config.pitch_category_format == "jp"
        assert config.anki_fields["word"] == "Expression"
        assert config.anki_fields["sentence"] == "Sentence"
        assert "Lapis" not in page.mapping_summary.text()

    def test_anki_miner_note_needs_no_capability(self, notetype_page):
        """Anki Miner Note serves every language, so the gate never holds it back."""
        from tests.unit.amn_fields import AMN_FIELDS

        wizard, page = notetype_page("zh")
        # The fixture's note type is called "Lapis"; detection reads the fields, not the name.
        page._field_names = list(AMN_FIELDS)

        page._on_fields_fetched("Lapis", AMN_FIELDS)

        assert wizard.working_config().pitch_category_format == "romaji"
        assert "Anki Miner Note" in page.mapping_summary.text()
