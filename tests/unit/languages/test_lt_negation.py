"""A negated Lithuanian verb fronts its positive verb, read off the dictionary (PLLT-02).

``lt_core_news_sm`` keeps the ne- in the lemma with ``Polarity=Neg`` (``Negaliu`` -> ``negalėti``) or leaves the
surface (``nepasakei``). wty-lt-en files five negated verbs as lemma rows that only say "negative form of X"
(``negalėti``) and has no row at all for every other one, so the card either duplicated the positive verb or was
never made. The rows below are wty-lt-en 2026.08.29's own, as the importer renders them, cut to their first glosses.

The parser is module-scoped: the autouse conftest fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.lt.parser import NegatedVerbPass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingUnit


def _lemma(*glosses: str) -> tuple[str, str]:
    items = "".join(f'<li class="gloss-sc-li"><div class="gloss-sc-div">{gloss}</div></li>' for gloss in glosses)
    content = (
        '<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div">'
        '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Etymology"><summary class="gloss-sc-summary" data-sc-content="summary-entry">'
        'Etymology</summary><div class="gloss-sc-div" data-sc-content="Etymology-content">ne- + x</div></details>'
        f'</div></div><ol class="gloss-sc-ol" data-sc-content="glosses">{items}</ol></div></li>'
    )
    return content, "v"


def _form(*targets: str) -> tuple[str, str]:
    if len(targets) == 1:
        return f'<li class="gloss-item"><div class="gloss-content">{targets[0]}</div></li>', "non-lemma"
    items = "".join(f'<li class="gloss-sc-li">{target}</li>' for target in targets)
    return f'<li class="gloss-item"><div class="gloss-content"><ul>{items}</ul></div></li>', "non-lemma"


ROWS: dict[str, list[tuple[str, str]]] = {
    "negalėti": [_lemma("negative form of galėti; to not be able", "to be ill")],
    "nežinoti": [_lemma("negative form of žinoti; to not know")],
    "nebūti": [_lemma("negative form of būti; to not be")],
    "nekęsti": [_lemma("to hate")],
    "negali": [_form("negalėti")],
    "neturėti": [_form("turėti")],
    "nekenčiu": [_form("nekęsti")],
    "galiu": [_form("galėti"), _form("galis")],
    "ateis": [_form("atei̇̃ti", "ateiti")],
    "pasakei": [_form("pasakyti")],
    "turėtum": [_form("turėti")],
    "kentė": [_form("kęsti")],
    "galėti": [_lemma("to be able")],
    "žinoti": [_lemma("to know")],
    "būti": [_lemma("to be")],
    "ateiti": [_lemma("to come")],
    "pasakyti": [_lemma("to say")],
    "turėti": [_lemma("to have")],
    "kęsti": [_lemma("to endure")],
}


def _lookup(keys: list[str]) -> dict[str, list[tuple[str, str]]]:
    return {key: ROWS[key] for key in keys if key in ROWS}


@pytest.fixture(scope="module")
def parser():
    return get_profile("lt").create_parser(switch_language(AnkiMinerConfig(), "lt"), form_lookup=_lookup)


def _fronts(parser, line: str) -> dict[str, str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="t")], False)
    return {word.surface: word.mined_form for word in words}


@pytest.mark.parametrize(
    ("line", "surface", "front"),
    [
        ("Negaliu dabar kalbėti.", "Negaliu", "galėti"),  # negalėti's row is "negative form of galėti"
        ("Negali būti!", "Negali", "galėti"),  # negali's row names negalėti, itself only a negative form
        ("Nežinau, ką daryti.", "Nežinau", "žinoti"),
        ("Manęs nebuvo namuose.", "nebuvo", "būti"),
        ("Neturiu laiko.", "Neturiu", "turėti"),
        ("Bijau, kad jis neateis.", "neateis", "ateiti"),  # no row for neateisti: ateis names ateiti
        ("Kodėl man nieko nepasakei?", "nepasakei", "pasakyti"),
        ("Tu neturėtum tiek daug gerti.", "neturėtum", "turėti"),
    ],
)
def test_a_negated_verb_fronts_its_positive_verb(parser, line, surface, front):
    assert _fronts(parser, line)[surface] == front


def test_a_ne_verb_with_a_sense_of_its_own_keeps_it(parser):
    """``nekęsti`` (to hate) is ne- + ``kęsti`` (to endure), but its row is a sense, not a negative form."""
    assert _fronts(parser, "Aš jo nekenčiu.")["nekenčiu"] == "nekęsti"


def test_a_negated_verb_the_dictionary_cannot_resolve_keeps_its_front(parser):
    assert _fronts(parser, "Aš nepažįstu šito žmogaus.")["nepažįstu"] == "nepažįstu"


def _token(surface: str, lemma: str, pos1: str = "VERB", morph: str = "Polarity=Neg") -> LanguageToken:
    return LanguageToken(surface, pos1, lemma=lemma, morph=morph)


def test_the_pass_strips_only_a_negative_form_row_naming_the_rest_of_the_lemma():
    tokens = [
        _token("Negaliu", "negalėti"),
        _token("nekentė", "nekęsti"),
        _token("negalėjo", "negalėti", morph=""),  # no Polarity=Neg: the tagger did not call it negated
        _token("negalėti", "negalėti", pos1="NOUN"),
    ]
    out = NegatedVerbPass()(tokens, None, _lookup)
    assert [token.feature.lemma for token in out] == ["galėti", "nekęsti", "negalėti", "negalėti"]


def test_the_pass_is_a_no_op_without_a_dictionary():
    token = _token("Negaliu", "negalėti")
    assert NegatedVerbPass()([token], None, None)[0].feature.lemma == "negalėti"
