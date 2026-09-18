"""The Croatian tokenizer on the real model: fine tags, the capitalised-lemma opt-in, apostrophes."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.tokenizer import load_spacy_model
from anki_miner.languages.hr.morphology import HR_EXCLUDED_SUBTYPES, HR_MODEL_PACKAGE
from anki_miner.languages.hr.tokenizer import build_tagger
from anki_miner.services.tagger import LockedTagger

PIPELINE = ["tok2vec", "tagger", "morphologizer", "lemmatizer", "attribute_ruler"]
KEPT_CATEGORIES = ("A", "Nc", "R", "Vm")


@pytest.fixture(scope="module")
def croatian():
    return build_tagger()


@pytest.fixture(scope="module")
def raw():
    return load_spacy_model(HR_MODEL_PACKAGE, keep_parser=False)


def _tokens(tagger, line):
    return {token.surface: token for token in tagger(line)}


def test_the_model_runs_without_ner_senter_or_the_parser(raw):
    assert raw.pipe_names == PIPELINE


def test_build_tagger_is_locked_and_reads_the_fine_tags(croatian):
    assert isinstance(croatian, LockedTagger)
    tokens = _tokens(croatian, "Knjige su na stolu.")
    assert (tokens["Knjige"].feature.lemma, tokens["Knjige"].feature.pos2) == ("knjiga", "Ncfpn")
    assert (tokens["stolu"].feature.lemma, tokens["stolu"].feature.pos2) == ("stol", "Ncmsl")
    assert tokens["Knjige"].morph.startswith("Case=")  # pos2 and morph are both live


def test_the_capitalised_lemma_repair_is_live(croatian):
    """Variant R: the line-initial word lemmatises as a mid-sentence one does, and POS is never touched."""
    assert _tokens(croatian, "Ukazuje na problem.")["Ukazuje"].feature.lemma == "ukazivati"
    morat = _tokens(croatian, "Morat \u0107u i\u0107i ku\u0107i.")["Morat"]
    assert (morat.feature.lemma, morat.feature.pos1) == ("morat", "VERB")  # lowered here, repaired by the parser


def test_a_short_infinitive_keeps_its_raw_lemma_for_the_parser_pass(croatian):
    vidjet = _tokens(croatian, "Vidjet \u0107emo sutra.")["Vidjet"]
    assert (vidjet.feature.lemma, vidjet.feature.pos1) == ("vidjet", "VERB")


def test_the_dje_letter_reaches_the_tagger_intact(croatian):
    tokens = _tokens(croatian, "\u0110ak \u010dita knjigu.")
    assert (tokens["\u0110ak"].feature.lemma, tokens["\u0110ak"].feature.pos2) == ("\u0111ak", "Ncmsn")


def test_both_apostrophe_shapes_tag_alike(croatian):
    curly = [(t.feature.pos1, t.feature.pos2) for t in croatian("Ko\u2019 si ti?")]
    straight = [(t.feature.pos1, t.feature.pos2) for t in croatian("Ko' si ti?")]
    assert curly == straight


def test_the_fine_tag_table_blocks_what_the_sentences_really_carry(croatian):
    """The table's effect on real output, not a second copy of its derivation rule: the ordinal numerals and
    the abbreviation residuals are blocked, the ordinary words are not."""
    tokens = _tokens(croatian, "Ro\u0111en je 5. svibnja 1990. godine.")
    tokens.update(_tokens(croatian, "Dr. Horvat je rekao npr. da do\u0111emo itd."))
    blocked = {"5": "Mdo", "1990": "Mdo", "Dr": "Y", "npr": "Qo", "itd": "Y"}
    for surface, tag in blocked.items():
        assert tokens[surface].feature.pos2 == tag
        assert tag in HR_EXCLUDED_SUBTYPES
    for surface in ("svibnja", "godine", "rekao", "Ro\u0111en"):
        assert tokens[surface].feature.pos2 not in HR_EXCLUDED_SUBTYPES
        assert tokens[surface].feature.pos2.startswith(KEPT_CATEGORIES)
