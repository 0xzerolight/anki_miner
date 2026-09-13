"""The French tokenizer: rule edits and closed-class retags (stubs) over the real fr_core_news_sm.

Hard-requires spaCy and fr_core_news_sm, like the zh tokenizer tests: a skipped suite is not a gate.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.fr import tokenizer
from anki_miner.languages.fr.morphology import fold_apostrophes
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger


def _stub(surface: str, pos1: str, lemma: str) -> LanguageToken:
    return LanguageToken(surface, pos1, lemma=lemma)


def test_hyphen_clitics_become_pronouns_with_or_without_a_curly_apostrophe():
    tokens = tokenizer.retag_french_tokens(
        [
            _stub("-elle", "ADJ", "-ell"),
            _stub("-toi", "NOUN", "-toi"),
            _stub("-t’en", "PROPN", "-t'en"),
            _stub("-LUI", "PROPN", "-LUI"),
        ]
    )
    assert [(t.feature.pos1, t.feature.lemma) for t in tokens] == [
        ("PRON", "elle"),
        ("PRON", "toi"),
        ("PRON", "t'en"),
        ("PRON", "lui"),
    ]


def test_titles_fixed_tokens_and_ne():
    tokens = tokenizer.retag_french_tokens(
        [
            _stub("Mme", "NOUN", "mme"),
            _stub("MLLE", "NOUN", "mlle"),  # an all-caps cue keeps its upper-case surface
            _stub("m", "NOUN", "m"),  # the metre, not a title: case-sensitive table
            _stub("C’est-à-dire", "ADJ", "c'est-à-dir"),
            _stub("n’", "ADV", "ne"),
            _stub("pas", "ADV", "pas"),
        ]
    )
    assert [(t.feature.pos1, t.feature.lemma) for t in tokens] == [
        ("X", "mme"),
        ("X", "mlle"),
        ("NOUN", "m"),
        ("ADV", "c'est-à-dire"),
        ("PART", "ne"),
        ("ADV", "pas"),
    ]


def test_fold_apostrophes_is_one_to_one():
    assert fold_apostrophes("aujourd’hui ʼ ‘ ´") == "aujourd'hui ' ' '"


@pytest.fixture(scope="module")
def french():
    return tokenizer.build_tagger()


def _tags(tagger, line: str) -> list[tuple[str, str, str]]:
    return [(t.surface, t.feature.pos1, t.feature.lemma) for t in tagger(line)]


def test_build_returns_a_locked_tagger_without_the_parser(french):
    assert isinstance(french, LockedTagger)
    assert french.nlp.pipe_names == ["tok2vec", "morphologizer", "attribute_ruler", "lemmatizer"]


@pytest.mark.parametrize(
    "line",
    [
        "L’homme qu’il a vu.",
        "Donne-le-moi, va-t'en et parle-lui.",
        "Attention\u202f: le train va bientôt partir\u00a0!",
        "LE CHAT DORT SUR LA CHAISE.",
    ],
)
def test_surfaces_cover_the_line_verbatim(french, line):
    assert "".join(t.surface for t in french(line)) == "".join(line.split())


def test_the_article_l_lemmatises_to_le(french):
    """Item must-resolve 1: l' is DET lemma le; before a verb it is PRON lemma le."""
    assert ("L'", "DET", "le") in _tags(french, "L'homme est là.")
    assert ("l'", "PRON", "le") in _tags(french, "Le long voyage l'a fatigué.")


def test_curly_elisions_tag_through_the_folded_copy(french):
    assert _tags(french, "L’homme qu’il a vu.")[:3] == [
        ("L’", "DET", "le"),
        ("homme", "NOUN", "homme"),
        ("qu’", "PRON", "que"),
    ]
    by_surface = {
        surface: (pos, lemma) for surface, pos, lemma in _tags(french, "Je n’ai pas d’argent, lorsqu’il pleut.")
    }
    assert by_surface["n’"] == ("PART", "ne")
    assert by_surface["d’"] == ("ADP", "de")
    assert by_surface["lorsqu’"] == ("SCONJ", "lorsque")


def test_hyphen_compounds_stay_whole(french):
    surfaces = [t.surface for t in french("On part en week-end avec un tee-shirt et du sang-froid.")]
    assert {"week-end", "tee-shirt", "sang-froid"} <= set(surfaces)


@pytest.mark.parametrize(
    "compound", ["chef-d'œuvre", "chef-d'oeuvre", "Chefs-d'œuvre", "main-d'oeuvre", "trompe-l'œil", "trompe-l'oeil"]
)
def test_no_elision_split_after_a_hyphen_d_or_l(french, compound):
    """IMPLEMENT fix 1: the guard covers every spelling, not a word list."""
    assert compound in [t.surface for t in french(f"C'est un {compound} et l'homme qu'il a vu est d'accord.")]


def test_elisions_elsewhere_still_split(french):
    surfaces = [t.surface for t in french("C'est un chef-d'oeuvre et l'homme qu'il a vu est d'accord.")]
    assert {"C'", "l'", "homme", "qu'", "d'", "accord"} <= set(surfaces)


def test_title_case_exceptions_stay_whole(french):
    assert ("Rendez-vous", "NOUN", "rendez-vous") in _tags(french, "Rendez-vous demain à midi.")
    assert [t.surface for t in french("Celui-ci est à moi.")][0] == "Celui-ci"


def test_clitic_chains_split_and_no_clitic_is_vocabulary(french):
    tags = _tags(french, "Donne-le-moi, va-t'en et parle-lui.")
    assert [surface for surface, _, _ in tags][:3] == ["Donne", "-le", "-moi"]
    clitics = [(surface, pos, lemma) for surface, pos, lemma in tags if surface.startswith("-")]
    assert clitics == [
        ("-le", "PRON", "le"),
        ("-moi", "PRON", "moi"),
        ("-t'en", "PRON", "t'en"),
        ("-lui", "PRON", "lui"),
    ]


def test_abbreviations_are_not_vocabulary_but_pruned_words_are(french):
    by_surface = {surface: pos for surface, pos, _ in _tags(french, "M. Dupont et Mme Martin voient le Dr Bernard.")}
    assert (by_surface["M."], by_surface["Mme"], by_surface["Dr"]) == ("X", "X", "X")
    assert ("oct.", "X", "oct.") in _tags(french, "Le 3 oct. il pleut.")
    assert ("etc.", "X", "etc.") in _tags(french, "Voici la liste, etc.")
    assert ("sept", "NUM", "sept") in _tags(french, "Il en a sept.")  # sept. (month) pruned: sept is not in the S8 set
    assert ("vol", "NOUN", "vol") in _tags(french, "C'est un vol.")
    assert ("MME", "X", "mme") in _tags(french, "MME DUPONT EST LÀ.")


def test_c_est_a_dire_is_one_adverb_in_either_apostrophe(french):
    assert ("c'est-à-dire", "ADV", "c'est-à-dire") in _tags(french, "Il habite au sous-sol, c'est-à-dire en bas.")
    assert ("C’est-à-dire", "ADV", "c'est-à-dire") in _tags(french, "C’est-à-dire que je suis fatigué.")


def test_the_abbreviation_c_a_d_keeps_its_dot_and_is_not_vocabulary(french):
    """IMPLEMENT fix 2: spaCy has no exception for c.-à-d.; split, it mined as c.-à-d ADJ."""
    tags = _tags(french, "Il habite au sous-sol, c.-à-d. en bas.")
    assert [(surface, pos) for surface, pos, _ in tags if surface.startswith("c.")] == [("c.-à-d.", "X")]
    assert [(surface, pos) for surface, pos, _ in _tags(french, "C.-à-d. que je suis fatigué.")][0] == ("C.-à-d.", "X")


def test_participles_follow_their_tag(french):
    assert ("fatigué", "ADJ", "fatigué") in _tags(french, "Il est très fatigué après le travail.")
    assert ("fatigué", "VERB", "fatiguer") in _tags(french, "Le long voyage l'a fatigué.")


def test_an_all_caps_cue_is_tagged_lowercased(french):
    tags = _tags(french, "LE CHAT DORT SUR LA CHAISE.")
    assert ("CHAT", "NOUN", "chat") in tags and ("CHAISE", "NOUN", "chaise") in tags
    assert all(pos != "PROPN" for _, pos, _ in tags)


def test_the_model_has_no_fine_tags(french):
    lines = [
        "Le chat dort sur la chaise.",
        "Je n’ai pas d’argent, lorsqu’il pleut.",
        "Donne-le-moi, va-t'en et parle-lui.",
    ]
    assert {t.feature.pos2 for line in lines for t in french(line)} == {""}
