"""Russian text rules (plan D3, D7-D9): normalize, dictionary keys, the folded reading probe,
sentence rules, the SDH speaker rule and the abbreviation set."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages._spaced.keys import folded_reading_lookup
from anki_miner.languages.ru.abbreviations import RU_ABBREVIATIONS
from anki_miner.languages.ru.morphology import (
    RU_DEDUP_FOLD,
    RU_KEYS,
    RU_SENTENCE_RULES,
    RU_SUBTITLE_REGEX,
    StressedHeadwordReading,
    ru_normalize,
    strip_stress,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

ACUTE = "\N{COMBINING ACUTE ACCENT}"
IE_GRAVE = "ѐ"  # ѐ: CYRILLIC SMALL LETTER IE WITH GRAVE


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        (f"Он чита{ACUTE}л кни{ACUTE}гу.", "Он читал книгу."),
        ("ёжик и йогурт", "ёжик и йогурт"),  # yo and short i are letters, not stress
        ("Ёлка", "Ёлка"),
        (f"{IE_GRAVE}тот", "етот"),  # ie-with-grave loses the grave
        ("кафе café", "кафе café"),  # a Latin accent is not Russian stress
    ],
)
def test_strip_stress(written, plain):
    assert strip_stress(written) == plain
    assert strip_stress(plain) == plain  # idempotent


def test_normalize_drops_stress_nbsp_and_soft_hyphens():
    assert ru_normalize(f"Кни\N{SOFT HYPHEN}га\N{NO-BREAK SPACE}интере{ACUTE}сная.") == "Книга интересная."
    assert ru_normalize("Книга интересная.") == "Книга интересная."


@pytest.mark.parametrize("spelling", ["Ёлка", "ёлка", "елка", "ЕЛКА", f"ё{ACUTE}лка"])
def test_term_keys_fold_case_stress_and_yo(spelling):
    assert RU_KEYS.fold_term(spelling) == "елка"


def test_reading_keys_keep_stress_but_drop_a_monosyllables_mark():
    assert RU_KEYS.fold_reading(f"кни{ACUTE}га") == f"кни{ACUTE}га"
    assert RU_KEYS.fold_reading(f"до{ACUTE}м") == "дом"
    assert RU_KEYS.fold_reading("ёж") == "ёж"
    assert RU_KEYS.fold_reading("чёрный") == "чёрный"  # yo is never folded in a reading
    assert RU_KEYS.fold_reading(None) is None


def test_the_comparison_fold_meets_every_spelling_of_a_front():
    assert RU_DEDUP_FOLD("Ёлка.") == RU_DEDUP_FOLD("ёлка") == RU_DEDUP_FOLD(f"е{ACUTE}лка") == "елка"
    assert RU_DEDUP_FOLD(RU_DEDUP_FOLD("Чёрный!")) == RU_DEDUP_FOLD("Чёрный!")


def test_the_folded_probe_asks_stored_keys_and_answers_the_callers_spellings():
    calls: list[list[str]] = []

    def lookup(terms: list[str]) -> dict[str, list[str]]:
        calls.append(terms)
        return {"черный": ["чёрный"], "книга": [f"кни{ACUTE}га"]}

    probe = folded_reading_lookup(lookup, RU_KEYS.fold_term)
    assert probe(["чёрный", "Черный", "книга", "замок"]) == {
        "чёрный": ["чёрный"],
        "Черный": ["чёрный"],
        "книга": [f"кни{ACUTE}га"],
    }
    assert calls == [["черный", "книга", "замок"]]


def test_the_reading_support_owns_the_fields_and_answers_nothing():
    assert StressedHeadwordReading().word_reading(object()) == ""


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Он сказал: «Привет». Потом ушёл.", 2),
        ("Я не знаю, т.е. не уверен. Ладно.", 2),
        ("Это мой кот. Он спит.", 2),
        ("В 2024 г. мы переехали. Всё.", 2),
    ],
)
def test_sentence_rules(text, sentences):
    assert len(split_sentences(text, rules=RU_SENTENCE_RULES)) == sentences


def test_a_low_quote_holds_its_sentences_together():
    """The low quote opens and the left double quote closes it, so the pair holds one sentence."""
    text = "Она сказала: „Привет. Как дела?“ Он кивнул."
    assert split_sentences(text, rules=RU_SENTENCE_RULES) == [text]
    assert {"«", "„"} <= RU_SENTENCE_RULES.openers and "“" not in RU_SENTENCE_RULES.openers
    assert {"»", "“"} <= RU_SENTENCE_RULES.closers


@pytest.mark.parametrize(
    ("cue", "kept"),
    [
        ("ИВАН: [стук] Открой дверь!", "Открой дверь!"),
        ("МАША ПЕТРОВА: Привет.", "Привет."),
        ("♪ Ля-ля ♪ (смеётся) Хорошо.", "Ля-ля Хорошо."),
        ("- Привет. - Пока.", "Привет. Пока."),
    ],
)
def test_the_sdh_default_strips_cyrillic_speaker_labels(cue, kept):
    assert " ".join(re.sub(RU_SUBTITLE_REGEX, "", cue).split()) == kept


def test_the_abbreviation_set_is_spacys_dotted_stems_minus_real_words():
    """Re-derived (the lt precedent): every dotted Cyrillic stem of spaCy's ru tokenizer exceptions that
    pymorphy3 does not know as an ordinary word. A known word is left out so the rule pruning un-glues it
    from a sentence-final dot."""
    import pymorphy3
    from spacy.lang.ru import Russian

    analyzer = pymorphy3.MorphAnalyzer(lang="ru")
    cyrillic = re.compile(r"^[а-яё][а-яё.\-]*$")
    stems = {
        part[:-1].casefold()
        for key in Russian().tokenizer.rules
        if "." in key
        for part in key.split()
        if part.endswith(".") and cyrillic.match(part[:-1].casefold())
    }

    def is_word(stem: str) -> bool:
        return "." not in stem and any(
            parse.is_known and "Abbr" not in parse.tag and "LATN" not in parse.tag for parse in analyzer.parse(stem)
        )

    assert frozenset(stem for stem in stems if not is_word(stem)) == RU_ABBREVIATIONS
    assert {"кот", "зам", "им", "с", "у", "к"}.isdisjoint(RU_ABBREVIATIONS)
    assert {"т.е", "т.д", "г", "ул", "руб", "см", "др"} <= RU_ABBREVIATIONS
