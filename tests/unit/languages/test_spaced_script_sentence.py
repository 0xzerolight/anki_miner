"""The Latin script gate and the Latin sentence rules."""

from __future__ import annotations

import unicodedata

from anki_miner.languages._spaced.script import LatinScript, is_latin_letter, nfc_normalize
from anki_miner.languages._spaced.sentence import LATIN_TERMINATORS, sentence_rules
from anki_miner.services.reading.sentence_splitter import split_sentences


def test_normalize_composes_and_changes_nothing_else():
    assert nfc_normalize(unicodedata.normalize("NFD", "Café – don’t")) == "Café – don’t"


def test_latin_letters_include_accented_and_extended_forms():
    assert all(is_latin_letter(c) for c in "azAZéÉßøłŁșțŀǆ")
    assert not any(is_latin_letter(c) for c in "1-'.食한ыα")


def test_the_gate_needs_one_latin_letter():
    script = LatinScript()
    assert script.filter_options() == ()
    assert script.matches("anything", "word") is False
    assert script.contains_target_script("e-mail")
    assert not script.contains_target_script("3.14")
    assert not script.contains_target_script("日本語")


def test_rules_are_space_aware_ascii_terminators():
    rules = sentence_rules()
    assert rules.space_aware is True
    assert rules.terminators == LATIN_TERMINATORS and "." in rules.terminators
    assert "'" not in rules.openers | rules.closers  # an apostrophe is not a quote
    assert rules.abbreviations == frozenset()


def test_abbreviations_keep_a_title_inside_the_sentence():
    text = "Dr. Smith paid 3.14 today. Then he left."
    assert split_sentences(text, rules=sentence_rules()) == ["Dr.", "Smith paid 3.14 today.", "Then he left."]
    assert split_sentences(text, rules=sentence_rules(frozenset({"dr"}))) == [
        "Dr. Smith paid 3.14 today.",
        "Then he left.",
    ]
