"""The Norwegian Bokmål profile: data, wiring, catalogue and registration (hard-requires spaCy + nb_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.nb.abbreviations import NB_ABBREVIATIONS
from anki_miner.languages.nb.catalog import NB_CATALOG
from anki_miner.languages.nb.morphology import NB_SUBTITLE_REGEX, nb_normalize, norwegian_particle_candidates
from anki_miner.languages.nb.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def test_registration_and_the_extra():
    assert "nb" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["nb"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[nb]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("nb")
    assert (profile.code, profile.display_name, profile.english_name) == ("nb", "Norsk bokmål", "Norwegian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is nb_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.wiktionary_code == ""
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "noun_article", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender", "noun_article")
    assert profile.pos_defaults.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Studenten leste en interessant bok i går."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_sentence_rules_carry_the_abbreviations_and_the_guillemets():
    rules = get_profile("nb").sentence_rules
    assert rules.abbreviations == NB_ABBREVIATIONS and rules.space_aware is True
    assert "«" in rules.openers and "»" in rules.closers and '"' not in rules.openers


def test_scoped_defaults_turn_on_the_norwegian_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "nb")
    assert config.language == "nb"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == NB_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == config.anki_fields["noun_gender"] == config.anki_fields["noun_article"] == ""
    assert [entry.kind for entry in config.expression_audio_chain] == ["googletts"]


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("nb").dedup_fold
    assert fold is not None
    assert fold("en bok") == fold("Bok") == "bok"
    assert fold("ei jente") == "jente" and fold("et hus") == "hus" and fold("å gå") == "gå"
    assert fold("stå opp") == "stå opp" and fold("en") == "en"


def test_the_tagger_stashes_particles_and_splits_word_final_dots(tagger):
    features = {t.surface: t.feature for t in tagger("Jeg står opp klokka sju.")}
    assert features["står"].particle == "opp" and features["opp"].pos1 == "PART"
    assert [(t.surface, t.feature.pos1) for t in tagger("God jul.")] == [
        ("God", "ADJ"),
        ("jul", "NOUN"),
        (".", "PUNCT"),
    ]
    assert ("f.eks.", "X") in [(t.surface, t.feature.pos1) for t in tagger("Han snakker f.eks. tysk.")]
    assert all(t.feature.pos2 == "" for t in tagger("Studenten leste en interessant bok i går."))


def test_the_parser_joins_particle_verbs_written_apart():
    profile = get_profile("nb")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "nb"))
    assert parser.normalize is profile.normalize and parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, SeparableVerbPass)
    assert parser._token_post_pass._candidates is norwegian_particle_candidates


def test_the_catalogue_ships_wiktionary_and_the_norwegian_subtitle_list():
    by_id = {spec.id: spec for spec in NB_CATALOG}
    assert set(by_id) == {"wty-nb-en", "opensubtitles-no"}
    dictionary, frequency = by_id["wty-nb-en"], by_id["opensubtitles-no"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/nb/en/wty-nb-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert all("CC BY-SA 4.0" in spec.license_note for spec in NB_CATALOG)
    assert get_profile("nb").catalog == NB_CATALOG
