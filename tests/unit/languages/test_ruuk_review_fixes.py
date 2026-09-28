"""Russian/Ukrainian review fixes (RUUK-03, -04, -05): verbs tagged NOUN/PROPN/ADJ, uk adverbs
fronting another word, and uk stress read off the lemma row's own head line.

The tokenizer cases run the REAL ru_core_news_sm / uk_core_news_sm + pymorphy3, module-scoped:
conftest resets the tagger cache per test, and reloading a model per case would cost minutes.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.uk.morphology import lemma_row_headword, lemma_row_stress

A = "\N{COMBINING ACUTE ACCENT}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


@pytest.fixture(scope="module")
def ru_tagger():
    from anki_miner.languages.ru.tokenizer import build_tagger

    return build_tagger()


@pytest.fixture(scope="module")
def uk_tagger():
    from anki_miner.languages.uk.tokenizer import build_tagger

    return build_tagger()


def _token(tagger, text: str, surface: str) -> tuple[str, str]:
    token = next(t for t in tagger(text) if t.surface == surface)
    return token.feature.pos1, token.feature.lemma


# RUUK-03: a dialogue-initial imperative the model reads as a noun, a name or an adjective.


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Подожди, я сейчас вернусь.", "Подожди", "подождать"),  # was a masculine NOUN
        ("Открой окно, здесь душно.", "Открой", "открыть"),  # was a feminine NOUN
        ("Смотри, какая собака!", "Смотри", "смотреть"),  # was PROPN: no card at all
        ("Извини, я опоздал.", "Извини", "извинить"),  # was PROPN: no card at all
    ],
)
def test_a_russian_imperative_is_a_verb(ru_tagger, text, surface, lemma):
    assert _token(ru_tagger, text, surface) == ("VERB", lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Відчини вікно, тут душно.", "Відчини", "відчинити"),  # was a feminine NOUN
        ("Перестань, будь ласка.", "Перестань", "перестати"),  # was a neuter NOUN
        ("Закрий двері.", "Закрий", "закрити"),  # was an ADJ
    ],
)
def test_a_ukrainian_imperative_is_a_verb(uk_tagger, text, surface, lemma):
    assert _token(uk_tagger, text, surface) == ("VERB", lemma)


def test_the_retagged_verb_carries_the_verbs_morph(ru_tagger):
    token = next(t for t in ru_tagger("Подожди, я сейчас вернусь.") if t.surface == "Подожди")
    assert {"Aspect=Perf", "Mood=Imp", "VerbForm=Fin"} <= set(token.morph.split("|"))
    assert "Gender" not in token.morph


# RUUK-04: the lookup lemmatiser fronts another word's normal form.


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Можна я сяду поруч?", "Можна", "можна"),  # was можний
        ("Варто спробувати ще раз.", "Варто", "варто"),  # was варта (guard, sentry)
        ("Я уже все знаю.", "уже", "уже"),  # was уж (defined 'Ужгород')
    ],
)
def test_a_ukrainian_adverb_fronts_itself(uk_tagger, text, surface, lemma):
    assert _token(uk_tagger, text, surface) == ("ADV", lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Говори громче!", "громче", "громкий"),  # fixture ru36
        ("Раньше здесь был лес.", "Раньше", "ранний"),  # the model marks no Degree=Cmp here; the parse does
        ("Почему ты раньше не сказал?", "раньше", "ранний"),
    ],
)
def test_a_russian_comparative_still_fronts_its_adjective(ru_tagger, text, surface, lemma):
    assert _token(ru_tagger, text, surface) == ("ADV", lemma)


@pytest.mark.parametrize(
    "text,surface,lemma",
    [
        ("Он глуп.", "глуп", "глупый"),  # a masculine short form is the adjective, not an adverb
        ("Она была смешна и наивна.", "смешна", "смешной"),  # ... and so is a feminine one
    ],
)
def test_a_russian_short_adjective_tagged_adv_keeps_its_adjective(ru_tagger, text, surface, lemma):
    assert _token(ru_tagger, text, surface) == ("ADV", lemma)


def test_a_russian_adverb_the_analyser_knows_only_as_a_short_neuter_fronts_itself(ru_tagger):
    text = "Мать пытливо посмотрела на неё."
    assert _token(ru_tagger, text, "пытливо") == ("ADV", "пытливо")  # was пытливый


# RUUK-05: uk stress from the lemma row's head line, never another lexeme's form row.


def _lemma_row(head: str, tags: str = "adv") -> tuple[str, str]:
    """A wty-uk-en lemma row as the importer renders it, trimmed to its Grammar head line."""
    return (
        '<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div">'
        '<details class="gloss-sc-details" data-sc-content="details-entry-Grammar">'
        '<summary class="gloss-sc-summary" data-sc-content="summary-entry">Grammar</summary>'
        f'<div class="gloss-sc-div" data-sc-content="Grammar-content">{head}</div></details></div>'
        '<ol class="gloss-sc-ol" data-sc-content="glosses"><li class="gloss-sc-li">'
        '<div class="gloss-sc-div">now</div></li></ol></div></li>',
        tags,
    )


def _form_row(target: str) -> tuple[str, str]:
    return f'<li class="gloss-item"><div class="gloss-content">{target}</div></li>', "non-lemma"


#: The real зараз rows of wty-uk-en 2026.09.19: the adverb's lemma row prints за́раз; the one filled
#: form row is зараза's genitive plural зара́з.
ROWS = {
    "зараз": [_lemma_row(f"за{A}раз • (záraz)"), _form_row("зараза")],
    "надія": [
        _lemma_row(f"наді{A}я • (nadíja) f inan (genitive наді{A}ї)", "n fem inanim"),
        _lemma_row(f"Наді{A}я • (Nadíja) f pers (genitive Наді{A}ї)", "name fem"),
    ],
    "замок": [
        _lemma_row(f"за{A}мок • (zámok) m inan", "n inanim masc"),
        _lemma_row(f"замо{A}к • (zamók) m inan", "n inanim masc"),
    ],
    "м'яч": [_lemma_row("м&#x27;яч • (mʺjač) m inan (genitive м&#x27;яча́)", "n inanim masc")],
    "будь ласка": [_lemma_row(f"будь ла{A}ска • (budʹ láska)", "intj")],
    "адам": [_lemma_row(f"Ада{A}м • (Adám) m pers", "name masc"), _form_row("ада")],
    "читала": [_form_row("читати")],
    "варто": [_lemma_row(f"ва{A}рто • (várto)(+ dative case (optional))")],
    "дуже": [_lemma_row(f"ду{A}же • (dúže)"), _lemma_row(f"дуже • (duže) (ду{A}же)")],
    "дім": [_lemma_row("дім • (dim) m inan (genitive до́му)", "n inanim masc")],
}
FORM_ROW_READINGS = {"зараз": [f"зара{A}з"], "адам": [f"а{A}дам"], "читала": [f"чита{A}ла"]}


def _rows(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
    folded = {term: term.lower().replace(RSQUO, "'") for term in terms}
    return {term: ROWS[key] for term, key in folded.items() if key in ROWS}


class _Readings:
    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    def __call__(self, terms: list[str]) -> dict[str, list[str]]:
        self.asked.append(terms)
        return {term: FORM_ROW_READINGS[term] for term in terms if term in FORM_ROW_READINGS}


def test_a_lemma_row_head_line_beats_another_lexemes_form_row():
    readings = _Readings()
    probe = lemma_row_stress(readings, _rows)
    assert probe(["зараз"]) == {"зараз": [f"за{A}раз"]}
    assert readings.asked == []  # the form row's зара́з is never asked for


def test_a_term_with_no_lemma_row_keeps_its_form_rows_reading():
    readings = _Readings()
    probe = lemma_row_stress(readings, _rows)
    assert probe(["читала", "зараз"]) == {"читала": [f"чита{A}ла"], "зараз": [f"за{A}раз"]}
    assert readings.asked == [["читала"]]


def test_the_name_row_never_stresses_the_noun():
    probe = lemma_row_stress(_Readings(), _rows)
    assert probe(["надія", "Надія"]) == {"надія": [f"наді{A}я"], "Надія": [f"Наді{A}я"]}


def test_a_lemma_row_that_does_not_spell_the_term_leaves_it_blank_not_form_stressed():
    """адам has only the name's lemma row: blank beats the form row's а́дам (a form of ада)."""
    probe = lemma_row_stress(_Readings(), _rows)
    assert probe(["адам"]) == {}


def test_two_lemma_rows_answer_both_stresses_and_s24_blanks_them():
    probe = lemma_row_stress(_Readings(), _rows)
    assert probe(["замок"]) == {"замок": [f"за{A}мок", f"замо{A}к"]}


def test_the_head_line_matches_across_apostrophes_and_words():
    probe = lemma_row_stress(_Readings(), _rows)
    assert probe([f"м{RSQUO}яч", "будь ласка"]) == {f"м{RSQUO}яч": ["м'яч"], "будь ласка": [f"будь ла{A}ска"]}


def test_a_bracket_glued_to_the_romanisation_ends_the_headword():
    assert lemma_row_stress(_Readings(), _rows)(["варто"]) == {"варто": [f"ва{A}рто"]}


def test_an_unmarked_head_yields_to_a_marked_one_but_stands_alone():
    """дуже's second lemma row prints no stress; a one-syllable дім has none to print."""
    probe = lemma_row_stress(_Readings(), _rows)
    assert probe(["дуже", "дім"]) == {"дуже": [f"ду{A}же"], "дім": ["дім"]}


def test_a_row_without_a_head_line_reads_nothing():
    assert lemma_row_headword(_form_row("зараза")[0], "зараз") == ""


def test_the_parser_reads_lemma_rows_when_the_factory_wires_them():
    """service_factory hands every parser both probes; uk composes them into its S24 lookup."""
    readings = _Readings()
    parser = get_profile("uk").create_parser(
        switch_language(AnkiMinerConfig(), "uk"), reading_lookup=readings, form_lookup=_rows
    )
    assert parser._reading_lookup(["зараз"]) == {"зараз": [f"за{A}раз"]}
    assert readings.asked == []
