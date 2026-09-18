"""Danish data for the spaCy substrate (no model): particles, articles, quotes, normalise, abbreviations."""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, NORDIC_DIALOGUE_DASH_PATTERN
from anki_miner.languages.da.abbreviations import DA_ABBREVIATION_ADDITIONS, DA_ABBREVIATION_DROPS, DA_ABBREVIATIONS
from anki_miner.languages.da.morphology import (
    DA_ALLOWED_POS,
    DA_ARTICLE_MAP,
    DA_CLOSERS,
    DA_EXCLUDED_SUBTYPES,
    DA_GRAMMAR_SOURCES,
    DA_LEADING_WORDS,
    DA_MODEL_PACKAGE,
    DA_OPENERS,
    DA_SEPARABLE_VERB_DEPS,
    DA_SUBTITLE_REGEX,
    da_normalize,
    danish_particle_candidates,
)
from anki_miner.languages.token import LanguageToken


def test_the_pos_gate_is_upos_only():
    assert DA_MODEL_PACKAGE == "da_core_news_sm"
    assert DA_ALLOWED_POS == ("ADJ", "ADV", "NOUN", "VERB") and DA_EXCLUDED_SUBTYPES == ()


def test_only_the_dedicated_particle_arc_is_taken():
    # The nb/nl shape. advmod/advmod:lmod would join 50 more verbs and silently demote ~170 more adverbs (DA5).
    assert frozenset({"compound:prt"}) == DA_SEPARABLE_VERB_DEPS


def test_the_join_puts_the_particle_after_the_verb_as_wiktionary_keys_it():
    token = LanguageToken("gav", "VERB", "", "give")
    token.feature.particle = "op"
    assert danish_particle_candidates(token) == ["give op"]  # never opgive, which is another verb


def test_articles_sources_and_leading_words():
    assert dict(DA_ARTICLE_MAP) == {"masc": "en", "fem": "en", "common": "en", "neut": "et"}
    assert DA_GRAMMAR_SOURCES == ("head", "chips", "morph")
    assert frozenset({"en", "et", "at"}) == DA_LEADING_WORDS


def test_the_subtitle_regex_keeps_the_latin_parts_and_takes_an_unspaced_speaker_dash():
    for part in LATIN_SUBTITLE_REGEX.split("|")[:-1]:
        assert part in DA_SUBTITLE_REGEX
    # The shared Nordic rule itself, never a third copy of nb's literal (DA21).
    assert DA_SUBTITLE_REGEX.endswith(NORDIC_DIALOGUE_DASH_PATTERN)
    pattern = re.compile(DA_SUBTITLE_REGEX)
    assert pattern.sub("", "-Kom her.") == "Kom her."
    assert pattern.sub("", "- Kom her.") == "Kom her."
    assert pattern.sub("", "[musik] Han løb.") == " Han løb."
    # A mid-sentence dash and a number range are punctuation the card sentence keeps.
    assert pattern.sub("", "Hun sagde – vist nok – ja.") == "Hun sagde – vist nok – ja."
    assert pattern.sub("", "Fra 10-12 i dag.") == "Fra 10-12 i dag."


def test_quotes_pair_the_danish_way():
    assert {"„", "»", "(", "[", "{"} <= DA_OPENERS and {"“", "«", ")", "]", "}"} <= DA_CLOSERS
    assert not {"«", "“", '"'} & DA_OPENERS and not {"»", "„", '"'} & DA_CLOSERS


def test_normalize_composes_spaces_nbsp_and_drops_soft_hyphens():
    assert da_normalize(unicodedata.normalize("NFD", "blå æble")) == "blå æble"
    assert da_normalize("ud\u00adm\u00e6r\u00adket\u00a0godt") == "udm\u00e6rket godt"
    assert da_normalize("Aarhus og Århus") == "Aarhus og Århus"


def test_abbreviations_are_spacy_minus_the_word_keys_plus_day_numbers():
    from spacy.lang.da.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and any(c.isalpha() for c in text)
    }
    assert (seeded - DA_ABBREVIATION_DROPS) | DA_ABBREVIATION_ADDITIONS == DA_ABBREVIATIONS
    assert seeded >= DA_ABBREVIATION_DROPS and not DA_ABBREVIATION_ADDITIONS & seeded
    assert all("." not in key for key in DA_ABBREVIATION_DROPS)
    assert {str(day) for day in range(1, 32)} | {"f.kr", "e.kr"} == DA_ABBREVIATION_ADDITIONS
    kept = {"bl.a", "f.eks", "dvs", "osv", "kl", "kr", "ca", "nr", "d", "hr", "dr", "mia", "pga", "evt", "ift"}
    assert kept <= DA_ABBREVIATIONS
    words = {"i", "man", "vær", "tv", "red", "jan", "do", "it", "eng", "rest", "regn", "vind"}
    assert not words & DA_ABBREVIATIONS
    assert all(key == key.casefold() for key in DA_ABBREVIATIONS)
