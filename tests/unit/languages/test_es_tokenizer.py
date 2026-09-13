"""Spanish tagger: es_core_news_sm through build_spacy_tagger plus the verb-lemma repair post-pass (es plan D1). Real model."""

from __future__ import annotations

import pytest

from anki_miner.languages.es.tokenizer import SpanishVerbRepair, build_tagger
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


@pytest.fixture(scope="module")
def repair(tagger):
    return SpanishVerbRepair(tagger.nlp.get_pipe("lemmatizer"))


def _features(tagger, line: str) -> dict[str, tuple[str, str]]:
    return {token.surface: (token.feature.pos1, token.feature.lemma) for token in tagger(line)}


#: (tag, surface, model lemma) -> (tag, front). The model lemmas are ones es_core_news_sm really emits (the lemma of a
#: form can differ by context: verte comes back as verte or verter).
REPAIRED = [
    (("VERB", "levantarse", "levantar él"), ("VERB", "levantar")),  # pronoun tail of lemmatize_verb_pron
    (("VERB", "comprarlo", "comprar él"), ("VERB", "comprar")),
    (("VERB", "levantarme", "levantarmar"), ("VERB", "levantar")),  # infinitive + clitic
    (("VERB", "irme", "irmar"), ("VERB", "ir")),
    (("VERB", "decírmelo", "decírmelir"), ("VERB", "decir")),  # the written accent of a two-clitic chain
    (("VERB", "verte", "verte"), ("VERB", "ver")),
    (("VERB", "verte", "verter"), ("VERB", "ver")),  # the shape outranks an attested but wrong lemma
    (("NOUN", "sentarnos", "sentarno"), ("VERB", "sentar")),  # mis-tagged noun
    (("NOUN", "explicárselo", "explicárselo"), ("VERB", "explicar")),
    (("PROPN", "Levantarse", "Levantarse"), ("VERB", "levantar")),
    (("VERB", "oírme", "oir yo"), ("VERB", "oír")),  # an -ír infinitive keeps its own accent
    (("NOUN", "diciéndoselo", "diciéndoselo"), ("VERB", "decir")),  # gerund rule
    (("VERB", "mirándome", "mirándome"), ("VERB", "mirar")),
    (("VERB", "durmiéndose", "dormir él"), ("VERB", "dormir")),
    (("VERB", "dámelo", "dámelir"), ("VERB", "dar")),  # imperative rule
    (("VERB", "dame", "damar"), ("VERB", "dar")),  # irregular one-syllable imperative
    (("VERB", "dime", "dime"), ("VERB", "decir")),
    (("VERB", "hazlo", "hacer él"), ("VERB", "hacer")),
    (("VERB", "vete", "vetir"), ("VERB", "ir")),
    (("VERB", "cállate", "cállatir"), ("VERB", "callar")),
    (("VERB", "siéntate", "siéntatir"), ("VERB", "sentar")),  # tú + te: -a stem is an -ar verb
    (("VERB", "siéntese", "siéntese"), ("VERB", "sentar")),  # usted + se: -e stem is an -ar verb
    (("VERB", "créeme", "créeme"), ("VERB", "creer")),
    (("NOUN", "tráemelo", "tráemelo"), ("VERB", "traer")),
    (("PROPN", "Mírame", "Mírame"), ("VERB", "mirar")),
    (("VERB", "dijiste", "dijistir"), ("VERB", "decir")),  # unique attested infinitive over every verb rule
    (("VERB", "llamaste", "llamastir"), ("VERB", "llamar")),
    (("AUX", "estábamos", "estár"), ("AUX", "estar")),
    (("VERB", "hablaremos", "hablarar"), ("VERB", "hablar")),
    (("VERB", "come", "comar"), ("VERB", "comer")),
    # ES-2: the model drops the accent of the -eír/-oír infinitives
    (("VERB", "riendo", "reir"), ("VERB", "reír")),
    (("VERB", "riéndose", "riéndose"), ("VERB", "reír")),
    (("VERB", "reímos", "reímos"), ("VERB", "reír")),
    (("VERB", "oímos", "oímos"), ("VERB", "oír")),
    (("VERB", "freído", "freer"), ("VERB", "freír")),
    (("VERB", "ríe", "reír"), ("VERB", "reír")),
]

UNTOUCHED = [
    ("VERB", "come", "comer"),
    ("NOUN", "tomate", "tomate"),  # no written accent: a noun, not tómate
    ("NOUN", "hermanos", "hermano"),
    ("ADJ", "poderosos", "poderoso"),  # os never repeats
    ("NOUN", "cántaros", "cántaro"),  # an accented one-clitic infinitive shape is not Spanish
    ("NOUN", "médicos", "médico"),
    ("NOUN", "débiles", "débil"),
    ("PROPN", "Vela", "Vela"),  # the irregular-imperative table never applies to a nominal
    ("PROPN", "Carla", "Carla"),
    ("NOUN", "perla", "perla"),
    ("ADV", "después", "después"),
    ("X", "a.m", "a.m"),  # the shared dotted-abbreviation rule already set X
]


@pytest.mark.parametrize(("given", "expected"), REPAIRED, ids=[f"{given[1]}-{given[2]}" for given, _ in REPAIRED])
def test_the_repair_fronts_the_attested_infinitive(repair, given, expected):
    assert repair.repair(*given) == expected


@pytest.mark.parametrize("given", UNTOUCHED, ids=[given[1] for given in UNTOUCHED])
def test_the_repair_leaves_everything_else_alone(repair, given):
    assert repair.repair(*given) == (given[0], given[2])


def test_the_pass_rewrites_tokens_in_place_and_is_memoised(repair):
    tokens = [LanguageToken("dámelo", "VERB", lemma="dámelir"), LanguageToken("ahora", "ADV", lemma="ahora")]
    assert repair(tokens) is tokens
    assert [(t.feature.pos1, t.feature.lemma) for t in tokens] == [("VERB", "dar"), ("ADV", "ahora")]
    assert repair.repair("VERB", "dámelo", "dámelir") == repair.repair("VERB", "dámelo", "dámelir")


def test_an_unbound_repair_refuses_to_run():
    with pytest.raises(RuntimeError, match="bind"):
        SpanishVerbRepair()([LanguageToken("dame", "VERB", lemma="damar")])


def test_build_returns_a_locked_tagger_without_the_parser(tagger):
    assert isinstance(tagger, LockedTagger)
    assert tagger.nlp.pipe_names == ["tok2vec", "morphologizer", "attribute_ruler", "lemmatizer"]


def test_real_lines_carry_repaired_fronts(tagger):
    assert _features(tagger, "Por favor, dámelo ahora mismo.")["dámelo"] == ("VERB", "dar")
    assert _features(tagger, "Es difícil explicárselo a los niños.")["explicárselo"] == ("VERB", "explicar")
    assert _features(tagger, "Hay que levantarse temprano para trabajar.")["levantarse"] == ("VERB", "levantar")
    assert _features(tagger, "¿Qué me dijiste ayer por la tarde?")["dijiste"] == ("VERB", "decir")
    assert _features(tagger, "¿De qué te estás riendo?")["riendo"] == ("VERB", "reír")


@pytest.mark.parametrize(
    ("line", "dotted"),
    [
        ("La Sra. López y la Srta. Pérez llegaron tarde.", {"Sra.", "Srta."}),
        ("Salimos a las 10 a.m. mañana.", {"a.m"}),
        ("Llegué a las 3 p. m. del lunes.", {"p.", "m."}),
        ("Por ejemplo, p. ej. esto funciona.", {"p.", "ej."}),
    ],
)
def test_dotted_abbreviations_are_x_through_the_shared_rule(tagger, line, dotted):
    features = _features(tagger, line)
    assert dotted <= set(features) and all(features[surface][0] == "X" for surface in dotted)


def test_curly_and_straight_apostrophes_tag_alike(tagger):
    def shape(line: str) -> list[tuple[str, str, str]]:
        return [(t.surface.replace("’", "'"), t.feature.pos1, t.feature.lemma) for t in tagger(line)]

    assert shape("No sé na’ de eso.") == shape("No sé na' de eso.")


def test_compounds_stay_whole_and_a_glued_dash_splits(tagger):
    assert "hispano-americano" in {t.surface for t in tagger("Es un escritor hispano-americano.")}
    assert {"so", "—", "quizá"} <= {t.surface for t in tagger("Pues so—quizá mañana.")}


@pytest.mark.parametrize("line", ["¡Cállate un momento y escucha!", "Es un acuerdo franco-alemán.", "¡NO LO SÉ!"])
def test_surfaces_cover_the_line(tagger, line):
    assert "".join(token.surface for token in tagger(line)) == line.replace(" ", "")
