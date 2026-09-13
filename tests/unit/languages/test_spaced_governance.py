"""The shared spaCy field keys and capabilities are admitted once, before any language declares them."""

from __future__ import annotations

from anki_miner.gui.widgets.panels.anki_settings_panel import _HOOK_FIELD_ROW_TEXTS
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD, NOUN_PLURAL_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GRAMMAR_FIELDS
from tests.unit.languages.test_language_contract import CAPABILITY_VOCABULARY, EXTRA_HOOK_FIELDS

SPECS = (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD, NOUN_PLURAL_FIELD)


def test_every_shared_spec_key_and_capability_is_admitted():
    assert {spec.key for spec in SPECS} <= EXTRA_HOOK_FIELDS
    assert {spec.capability for spec in SPECS} | {"lemmatised_frequency"} <= CAPABILITY_VOCABULARY
    assert set(GRAMMAR_FIELDS) | {"pos"} == {spec.key for spec in SPECS}


def test_every_shared_spec_has_a_translatable_row():
    for spec in SPECS:
        label, helper = _HOOK_FIELD_ROW_TEXTS[spec.key]
        assert label.endswith("Field") and helper.endswith("Blank = skip.")
