"""A stashed particle loses its word class only when its join is chosen (NORD-05, DENL-05).

The tokenizer stashes every dependant on the language's particle arcs, in line order, and demotes none of them;
``SeparableVerbPass`` demotes the one particle whose join it takes. A join the dictionary cannot attest leaves every
dependant the class the tagger gave it: nl ``compound:prt`` arcs hang nouns and adjectives (``op prijs gesteld``),
which used to vanish from mining. An ``attested_only_deps`` arc (da ``advmod``) joins only when the dictionary
knows the result, never blindly without one.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.morphology import SeparableVerbPass
from anki_miner.languages._spaced.tokens import to_duck_tokens
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from tests.unit.languages.test_spaced_tokens import fake_doc

ADVERBIAL = frozenset({"advmod"})


def two_words(token) -> list[str]:
    return [f"{token.feature.lemma} {token.feature.particle}"]


def _stood_up(op_dep: str = "advmod"):
    """da ``Hun stod tidligt op``: two dependants on the verb's particle arcs, the adverb first."""
    text = "Hun stod tidligt op"
    rows = [
        ("Hun", "PRON", "PRON", "hun", "nsubj", 1),
        ("stod", "VERB", "VERB", "stå", "ROOT", 1),
        ("tidligt", "ADV", "ADV", "tidligt", "advmod", 1),
        ("op", "ADV", "ADV", "op", op_dep, 1),
    ]
    return to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"advmod", "compound:prt"}))


def test_every_dependant_is_stashed_in_line_order_and_none_is_demoted():
    tokens = _stood_up()
    head = tokens[1]
    assert [(p.text, p.token, p.dep) for p in head.feature.particles] == [
        ("tidligt", tokens[2], "advmod"),
        ("op", tokens[3], "advmod"),
    ]
    assert head.feature.particle == "tidligt"
    assert [t.feature.pos1 for t in tokens] == ["PRON", "VERB", "ADV", "ADV"]


def test_only_the_particle_whose_join_is_attested_is_demoted():
    tokens = _stood_up()
    SeparableVerbPass(candidates=two_words)(tokens, lambda words: set(words) & {"stå op"}, None)
    assert tokens[1].feature.lemma == "stå op"
    assert [t.feature.pos1 for t in tokens] == ["PRON", "VERB", "ADV", "PART"]
    assert not tokens[1].feature.particle and not tokens[1].feature.particles


def test_an_unattested_join_leaves_the_particle_its_own_class():
    """nl ``op prijs gesteld``: the model hangs the noun on ``compound:prt``; ``prijsstellen`` is no headword."""
    text = "wordt op prijs gesteld"
    rows = [
        ("wordt", "AUX", "WW", "worden", "aux:pass", 3),
        ("op", "ADP", "VZ", "op", "case", 2),
        ("prijs", "NOUN", "N", "prijs", "compound:prt", 3),
        ("gesteld", "VERB", "WW", "stellen", "ROOT", 3),
    ]
    tokens = to_duck_tokens(fake_doc(text, rows), text, particle_deps=frozenset({"compound:prt"}))
    SeparableVerbPass()(tokens, lambda words: set(), None)
    assert tokens[3].feature.lemma == "stellen" and tokens[2].feature.pos1 == "NOUN"


def test_without_a_dictionary_the_first_particle_with_a_candidate_joins():
    tokens = _stood_up(op_dep="compound:prt")
    SeparableVerbPass(candidates=lambda t: [] if t.feature.particle == "tidligt" else two_words(t))(tokens, None, None)
    assert tokens[1].feature.lemma == "stå op" and tokens[3].feature.pos1 == "PART"


def test_an_attested_only_arc_never_joins_without_a_dictionary():
    tokens = _stood_up()
    SeparableVerbPass(candidates=two_words, attested_only_deps=ADVERBIAL)(tokens, None, None)
    assert tokens[1].feature.lemma == "stå"
    assert [t.feature.pos1 for t in tokens] == ["PRON", "VERB", "ADV", "ADV"]
    assert not tokens[1].feature.particles


def test_an_attested_only_arc_is_skipped_for_the_dedicated_arc_without_a_dictionary():
    tokens = _stood_up(op_dep="compound:prt")
    SeparableVerbPass(candidates=two_words, attested_only_deps=ADVERBIAL)(tokens, None, None)
    assert tokens[1].feature.lemma == "stå op"
    assert [t.feature.pos1 for t in tokens] == ["PRON", "VERB", "ADV", "PART"]


def test_an_attested_only_arc_joins_when_the_dictionary_knows_the_verb():
    calls: list[list[str]] = []

    def attest(words: list[str]) -> set[str]:
        calls.append(words)
        return set(words) & {"stå op"}

    tokens = _stood_up()
    SeparableVerbPass(candidates=two_words, attested_only_deps=ADVERBIAL)(tokens, attest, None)
    assert tokens[1].feature.lemma == "stå op" and tokens[3].feature.pos1 == "PART"
    assert calls == [["stå tidligt", "stå op"]]


# --------------------------------------------------------------------------
# The real parsers
# --------------------------------------------------------------------------


#: Two wty-da-en headwords: the particle verbs the Danish sentences below carry.
DA_HEADWORDS = frozenset({"se ud", "give op"})


@pytest.fixture(scope="module")
def parsers():
    """Built together, once: the autouse conftest fixture clears the tagger cache around every test."""
    lookups = {
        "nl-empty": ("nl", lambda words: set()),
        "da-known": ("da", lambda words: set(words) & DA_HEADWORDS),
        "da-empty": ("da", lambda words: set()),
        "da-none": ("da", None),
    }
    return {
        name: get_profile(code).create_parser(switch_language(AnkiMinerConfig(), code), term_lookup=lookup)
        for name, (code, lookup) in lookups.items()
    }


def _fronts(parser, sentence: str) -> set[str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=sentence, index=0, location_label="t")], False)
    return {word.mined_form for word in words}


@pytest.mark.parametrize(
    ("sentence", "word"),
    [
        ("Enige voorzichtigheid wordt wel op prijs gesteld.", "prijs"),
        ("Er is geen enkel geval bekend waarbij doden vielen.", "bekend"),
    ],
)
def test_nl_a_noun_or_adjective_on_an_unattested_particle_arc_is_mined(parsers, sentence, word):
    assert word in _fronts(parsers["nl-empty"], sentence)


@pytest.mark.parametrize(
    ("name", "present", "absent"),
    [
        ("da-known", {"se ud"}, {"se", "ud"}),
        ("da-empty", {"se", "ud"}, {"se ud"}),
        ("da-none", {"se", "ud"}, {"se ud"}),
    ],
)
def test_da_an_adverbial_particle_joins_only_when_attested(parsers, name, present, absent):
    fronts = _fronts(parsers[name], "Du ser træt ud.")
    assert present <= fronts and not absent & fronts


def test_da_the_attested_particle_joins_past_the_adverbs_before_it_and_the_rest_stay_words(parsers):
    """``ikke`` (advmod), ``op`` (advmod:lmod), ``nu`` (advmod), ``fremme`` (compound:prt) all hang on ``Giv``."""
    fronts = _fronts(parsers["da-known"], "Giv ikke op nu, vi er næsten fremme!")
    assert "give op" in fronts and not {"op", "give"} & fronts
    assert {"nu", "fremme"} <= fronts
