"""The Hungarian profile: data, wiring, catalogue and registration (hard-requires spaCy + hu_core_news_md)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import POS_FIELD
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages.hu.abbreviations import HU_ABBREVIATIONS
from anki_miner.languages.hu.catalog import HU_CATALOG
from anki_miner.languages.hu.morphology import HU_SUBTITLE_REGEX, hungarian_preverb_candidates
from anki_miner.languages.hu.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tagger():
    """Built once: the autouse conftest fixture clears the tagger cache around every test."""
    return build_tagger()


def test_registration_and_the_extra():
    assert "hu" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["hu"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[hu]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("hu")
    assert (profile.code, profile.display_name, profile.english_name) == ("hu", "Magyar", "Hungarian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is nfc_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    assert profile.audio_track_codes == frozenset({"hun", "hu", "hungarian"})
    assert profile.asr_language == "hu" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("hu", ("hu",), ("hu-orig",))
    assert captions.audio_pattern == "^hu(-|$)" and captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD,)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook]
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "A diák tegnap elolvasott egy érdekes könyvet."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_the_lookup_ladder_ends_with_the_preverb_less_verb():
    lookup = get_profile("hu").lookup
    assert lookup.candidates("elolvas", "elolvasta", None) == [("elolvasta", 0), ("olvas", 0)]
    assert lookup.candidates("ház", "Házakban", None) == [("Házakban", 0), ("házakban", 0)]


def test_sentence_rules_carry_the_abbreviations_and_the_hungarian_quotes():
    rules = get_profile("hu").sentence_rules
    assert rules.abbreviations == HU_ABBREVIATIONS and rules.space_aware is True
    assert {"„", "»"} <= rules.openers and {"”", "«"} <= rules.closers
    assert "«" not in rules.openers and "»" not in rules.closers


def test_audio_speaks_the_front_through_google():
    audio = get_profile("hu").audio
    stems = (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix)
    assert stems == ("hu", "googletts_hu", "sentencetts_hu")
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("elolvas", "") == "elolvas"


def test_scoped_defaults_turn_on_the_hungarian_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "hu")
    assert config.language == "hu" and config.downloader_subtitle_langs == "hu"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == HU_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == ""


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("hu").dedup_fold
    assert fold is not None
    assert fold("a ház") == fold("Ház") == "ház" and fold("az alma") == "alma"
    assert fold("ŰRHAJÓ") == "űrhajó" != fold("urhajo")


def test_the_tagger_stashes_preverbs_demotes_the_clitic_and_splits_sentence_final_words(tagger):
    assert tagger.nlp.pipe_names == [
        "tok2vec", "tagger", "morphologizer", "lookup_lemmatizer", "trainable_lemmatizer", "parser"
    ]  # fmt: skip
    features = {token.surface: token.feature for token in tagger("Nem olvasta el a könyvet.")}
    assert features["olvasta"].particle == "el" and features["el"].pos1 == "PART"
    assert ("-e", "PART") in [(t.surface, t.feature.pos1) for t in tagger("Tudod-e, hol van?")]
    assert [(t.surface, t.feature.pos1) for t in tagger("Ez egy jó út.")][-2:] == [("út", "NOUN"), (".", "PUNCT")]
    assert ("Dr.", "X") in [(t.surface, t.feature.pos1) for t in tagger("Dr. Kovács késett.")]


def test_the_parser_joins_preverbs_through_the_listed_candidates():
    profile = get_profile("hu")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "hu"))
    assert parser.normalize is profile.normalize and parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, SeparableVerbPass)
    assert parser._token_post_pass._candidates is hungarian_preverb_candidates


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in HU_CATALOG}
    assert set(by_id) == {"wty-hu-en", "opensubtitles-hu"}
    dictionary, frequency = by_id["wty-hu-en"], by_id["opensubtitles-hu"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/hu/en/wty-hu-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hu/hu_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in HU_CATALOG)
    assert get_profile("hu").catalog == HU_CATALOG
