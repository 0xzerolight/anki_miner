"""Ukrainian text rules (plan P3, P4, P8, P10): normalize, the apostrophe fold, keys, sentences, SDH."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages.uk.abbreviations import UK_ABBREVIATIONS
from anki_miner.languages.uk.morphology import (
    UK_DEDUP_FOLD,
    UK_KEYS,
    UK_SENTENCE_RULES,
    UK_SUBTITLE_REGEX,
    UK_TAG_CHAR_MAP,
    StressedHeadwordReading,
    apostrophe_blind,
    canonical_apostrophes,
    uk_normalize,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

ACUTE = "\N{COMBINING ACUTE ACCENT}"
GRAVE = "\N{COMBINING GRAVE ACCENT}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"
MODAP = "\N{MODIFIER LETTER APOSTROPHE}"
NBSP = "\N{NO-BREAK SPACE}"
SHY = "\N{SOFT HYPHEN}"


def test_normalize_drops_stress_nbsp_and_soft_hyphens():
    assert uk_normalize(f"чита{ACUTE}ла{NBSP}кни{ACUTE}жку") == "читала книжку"
    assert uk_normalize(f"книж{SHY}ка") == "книжка"
    assert uk_normalize(f"сього{GRAVE}дні") == "сьогодні"


def test_normalize_is_idempotent_and_keeps_a_latin_accent():
    once = uk_normalize(f"Він чита{ACUTE}в café")
    assert once == uk_normalize(once) == "Він читав café"


def test_normalize_keeps_every_ukrainian_letter_and_the_apostrophe():
    """The card sentence keeps the author's own apostrophe; only the tagging copy folds it (P3)."""
    line = f"м{RSQUO}яч ґедзь їжак єнот ідея"
    assert uk_normalize(line) == line


@pytest.mark.parametrize(
    "spelling", ["м'яч", f"м{RSQUO}яч", f"м{MODAP}яч", "м`яч", "М'ЯЧ", f"м{ACUTE}'яч", f"М{RSQUO}ЯЧ"]
)
def test_term_keys_fold_case_stress_and_every_apostrophe(spelling):
    assert UK_KEYS.fold_term(spelling) == "м'яч"


def test_the_term_fold_is_idempotent():
    once = UK_KEYS.fold_term(f"Здоро{ACUTE}в{RSQUO}я")
    assert once == UK_KEYS.fold_term(once) == "здоров'я"


def test_reading_keys_are_nfc_only_and_keep_their_stress():
    """P8: only 12 readings in the whole of wty-uk-en are one-vowel-with-acute, and dropping that
    acute changes the ambiguous count by 0, so uk does NOT copy ru's fold_reading override."""
    assert UK_KEYS.fold_reading(f"кни{ACUTE}жка") == f"кни{ACUTE}жка"
    # A one-vowel reading keeps its mark too, which is exactly where ru's override would have fired.
    assert UK_KEYS.fold_reading(f"кі{ACUTE}т") == f"кі{ACUTE}т"
    assert UK_KEYS.fold_reading(None) is None


def test_the_comparison_fold_meets_every_spelling_of_a_front():
    assert UK_DEDUP_FOLD(f"М{RSQUO}яч.") == UK_DEDUP_FOLD("м'яч") == "м'яч"
    assert UK_DEDUP_FOLD(f"кни{ACUTE}жка") == UK_DEDUP_FOLD("КНИЖКА") == "книжка"


def test_the_fold_keeps_yo_where_russians_would_drop_it():
    """uk has no ё rule: the letter is foreign to the language and must not silently merge words."""
    assert UK_DEDUP_FOLD("Ёлка") == "ёлка"


def test_the_reading_support_owns_the_fields_and_answers_nothing():
    assert StressedHeadwordReading().word_reading(object()) == ""


def test_the_tagging_map_targets_the_ascii_apostrophe():
    assert set(UK_TAG_CHAR_MAP) == {RSQUO, MODAP, "`"}
    assert set(UK_TAG_CHAR_MAP.values()) == {"'"}
    assert all(len(key) == 1 and len(value) == 1 for key, value in UK_TAG_CHAR_MAP.items())


def test_the_repair_policies_are_the_tagging_map_in_function_form():
    """P6a: one source of truth - the analyser and the identity test see the same canonical mark."""
    assert canonical_apostrophes(f"М{RSQUO}яча") == "М'яча"
    assert apostrophe_blind(f"М{RSQUO}яча") == "м'яча"
    assert canonical_apostrophes("м'яча") == "м'яча"


@pytest.mark.parametrize(
    "text,sentences",
    [
        ("Він прийшов. Вона пішла.", ["Він прийшов.", "Вона пішла."]),
        ("Проф. Шевченко читав лекцію. Усі слухали.", ["Проф. Шевченко читав лекцію.", "Усі слухали."]),
        ("Він живе на вул. Хрещатик. Це центр.", ["Він живе на вул. Хрещатик.", "Це центр."]),
        ("Це мій кіт. Він спить.", ["Це мій кіт.", "Він спить."]),
        ("Він сказав: «Привіт». Потім пішов.", ["Він сказав: «Привіт».", "Потім пішов."]),
    ],
)
def test_sentence_rules(text, sentences):
    assert split_sentences(text, rules=UK_SENTENCE_RULES) == sentences


def test_a_low_quote_holds_its_sentences_together():
    """Ukrainian nests „…“ inside «…»; the left double quote closes rather than opens."""
    text = "Він сказав: «Це „правда. Справді“ так». Усі мовчали."
    assert split_sentences(text, rules=UK_SENTENCE_RULES) == ["Він сказав: «Це „правда. Справді“ так».", "Усі мовчали."]


@pytest.mark.parametrize(
    "cue,kept",
    [
        ("ІВАН: Відчини двері!", "Відчини двері!"),
        ("ОЛЕНА ПЕТРІВНА: Я вдома.", "Я вдома."),
        ("ҐУДЗИК: Привіт.", "Привіт."),
        ("ЄВГЕН: Добре.", "Добре."),
        # The bracket and music presets leave the whitespace they sat in; the parser flattens it.
        ("[стук] Відчини двері!", " Відчини двері!"),
        ("♪♪ Пісня ♪♪", " Пісня "),
        ("Він сказав: привіт", "Він сказав: привіт"),
    ],
)
def test_the_sdh_default_strips_ukrainian_speaker_labels(cue, kept):
    """R11: Є І Ї Ґ sit outside U+0410-042F, so the shared Latin and Russian classes cannot match them."""
    assert re.sub(UK_SUBTITLE_REGEX, "", cue) == kept


def test_the_subtitle_regex_carries_no_inline_flags():
    """A global flag not at position 0 is a hard re.error once the presets are |-joined."""
    assert "(?i)" not in UK_SUBTITLE_REGEX and "(?m)" not in UK_SUBTITLE_REGEX
    re.compile(UK_SUBTITLE_REGEX)


def test_the_abbreviation_set_is_spacys_dotted_stems_minus_real_words():
    """Derived, never hand-edited (P10): re-derive it here so a spaCy bump is caught."""
    import pymorphy3
    from spacy.lang.uk.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    morph = pymorphy3.MorphAnalyzer(lang="uk")
    word_dot = re.compile(r"[^\W\d_]+\.")
    cyrillic = {
        key[:-1]
        for key in TOKENIZER_EXCEPTIONS
        if word_dot.fullmatch(key) and any(0x0400 <= ord(char) <= 0x04FF for char in key)
    }
    real_words = {stem for stem in cyrillic if any(parse.is_known for parse in morph.parse(stem))}
    assert frozenset(cyrillic - real_words) == UK_ABBREVIATIONS
    # The seven pymorphy3 knows as ordinary words are exactly what the pruning exists to drop.
    assert real_words == {"г", "м", "мкр", "наб", "оз", "пл", "ст"}
    assert "обл" in UK_ABBREVIATIONS
    assert frozenset(stem.casefold() for stem in UK_ABBREVIATIONS) == UK_ABBREVIATIONS
