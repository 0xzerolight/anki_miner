"""The Danish profile: data, wiring, catalogue and registration (hard-requires spaCy + da_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.da.abbreviations import DA_ABBREVIATIONS
from anki_miner.languages.da.catalog import DA_CATALOG
from anki_miner.languages.da.morphology import (
    DA_ARTICLE_MAP,
    DA_SUBTITLE_REGEX,
    da_normalize,
    danish_particle_candidates,
)
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "da" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["da"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[da]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("da")
    assert (profile.code, profile.display_name, profile.english_name) == ("da", "Dansk", "Danish")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is da_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"dan", "da", "danish"})
    assert profile.asr_language == "da" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("da", ("da",), ("da-orig",))
    assert captions.audio_pattern == "^da(-|$)" and captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "noun_article", "noun_gender", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    grammar = profile.render_hooks[1]
    assert grammar.field_names() == ("noun_article", "noun_gender")
    assert grammar._article_map == DA_ARTICLE_MAP and grammar._sources == ("head", "chips", "morph")
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Den studerende læste en interessant bog i går."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_sentence_rules_carry_the_abbreviations_and_the_danish_quotes():
    rules = get_profile("da").sentence_rules
    assert rules.abbreviations == DA_ABBREVIATIONS and rules.space_aware is True
    assert {"»", "„"} <= rules.openers and {"«", "“"} <= rules.closers
    assert "«" not in rules.openers


def test_audio_speaks_the_front_through_google():
    audio = get_profile("da").audio
    stems = (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix)
    assert stems == ("da", "googletts_da", "sentencetts_da")
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("stå op", "") == "stå op"


def test_scoped_defaults_turn_on_the_latin_sdh_filter_and_the_danish_gate():
    config = switch_language(AnkiMinerConfig(), "da")
    assert config.language == "da" and config.downloader_subtitle_langs == "da"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    # DA21: Danish subtitles write the speaker dash unspaced, so da carries its own S10 default.
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == DA_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == config.anki_fields["noun_article"] == config.anki_fields["noun_gender"] == ""


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("da").dedup_fold
    assert fold is not None
    assert fold("en bog") == fold("Bog") == "bog"
    assert fold("et hus") == "hus" and fold("at stå op") == "stå op"
    assert fold("en") == "en"


def test_the_tagger_relemmatises_capitals_stashes_particles_and_splits_sentence_final_words():
    tagger = get_tagger("da")
    # Ruling S2 variant R, opted in: the model leaves a cue-initial definite as its own lemma.
    assert [(t.surface, t.feature.lemma) for t in tagger("Huset er stort.")][0] == ("Huset", "hus")
    assert [(t.surface, t.feature.lemma) for t in tagger("Børnene legede.")][0] == ("Børnene", "barn")
    # ... and never touches a lemma the model already inflected down (the deleted Task 2 broke these).
    assert [(t.surface, t.feature.lemma) for t in tagger("Pigerne løb hjem.")][0] == ("Pigerne", "pige")
    assert [(t.surface, t.feature.lemma) for t in tagger("Husk at låse døren.")][0] == ("Husk", "huske")
    features = {t.surface: t.feature for t in tagger("Hun gav op efter en time.")}
    assert features["gav"].particle == "op" and features["op"].pos1 == "PART"
    assert [(t.surface, t.feature.pos1) for t in tagger("Det ved man.")][-2:] == [("man", "PRON"), (".", "PUNCT")]
    assert ("bl.a.", "X") in [(t.surface, t.feature.pos1) for t in tagger("Han kom bl.a. hjem.")]


def test_the_parser_joins_particle_verbs_after_the_verb():
    profile = get_profile("da")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "da"))
    assert parser.normalize is profile.normalize and parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, SeparableVerbPass)
    assert parser._token_post_pass._candidates is danish_particle_candidates


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in DA_CATALOG}
    assert set(by_id) == {"wty-da-en", "opensubtitles-da"}
    dictionary, frequency = by_id["wty-da-en"], by_id["opensubtitles-da"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/da/en/wty-da-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/da/da_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in DA_CATALOG)
    assert get_profile("da").catalog == DA_CATALOG
