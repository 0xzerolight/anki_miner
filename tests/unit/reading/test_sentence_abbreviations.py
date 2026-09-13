"""S8: abbreviation dots and ASCII ellipses do not end a sentence."""

from __future__ import annotations

from anki_miner.languages.ko.script import KO_SENTENCE_RULES
from anki_miner.languages.profile import SentenceRules
from anki_miner.services.reading.sentence_splitter import split_sentences


def _rules(*abbreviations: str) -> SentenceRules:
    return SentenceRules(
        terminators=frozenset(".!?"),
        ellipses=frozenset("…"),
        openers=frozenset('([{"“«¿¡'),
        closers=frozenset(')]}"”»'),
        space_aware=True,
        abbreviations=frozenset(abbreviations),
    )


def test_abbreviations_default_to_empty():
    assert SentenceRules(frozenset("."), frozenset(), frozenset(), frozenset()).abbreviations == frozenset()
    assert KO_SENTENCE_RULES.abbreviations == frozenset()


def test_dr_does_not_end_the_sentence():
    assert split_sentences("Dr. Smith paid 3.14 today.", rules=_rules("dr")) == ["Dr. Smith paid 3.14 today."]


def test_dotted_abbreviation_is_one_key():
    assert split_sentences("z.B. heute.", rules=_rules("z.b")) == ["z.B. heute."]


def test_multi_token_abbreviation_enters_by_each_token():
    assert split_sentences("p. ej. esto.", rules=_rules("p", "ej")) == ["p. ej. esto."]


def test_ascii_ellipsis_does_not_end_the_sentence():
    assert split_sentences("wait... what", rules=_rules("dr")) == ["wait... what"]


def test_an_opener_before_the_abbreviation_is_ignored():
    assert split_sentences("(Dr. Smith) left.", rules=_rules("dr")) == ["(Dr. Smith) left."]


def test_an_ordinary_word_still_ends_the_sentence():
    assert split_sentences("The end. Next one.", rules=_rules("dr")) == ["The end.", "Next one."]


def test_empty_set_keeps_the_pre_stage_splits():
    """The eu-stub and ko behaviour, verbatim (D3)."""
    assert split_sentences("Dr. Smith paid 3.14 today. He left!", rules=_rules()) == [
        "Dr.",
        "Smith paid 3.14 today.",
        "He left!",
    ]
    assert split_sentences("wait... what", rules=_rules()) == ["wait...", "what"]
    assert split_sentences("잠깐... 뭐야?", rules=KO_SENTENCE_RULES) == ["잠깐...", "뭐야?"]


def test_japanese_default_is_untouched():
    assert split_sentences("Dr. Smith paid 3.14 today. He left!") == ["Dr. Smith paid 3.14 today. He left!"]
