"""The Lithuanian profile: data, wiring, catalogue and registration (hard-requires spaCy + lt_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.lt import catalog as lt_catalog
from anki_miner.languages.lt.catalog import LT_CATALOG
from anki_miner.languages.lt.morphology import LT_ABBREVIATIONS, LT_CLOSERS, LT_OPENERS, LT_SUBTITLE_REGEX, lt_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "lt" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["lt"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[lt]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("lt")
    assert (profile.code, profile.display_name, profile.english_name) == ("lt", "Lietuvių", "Lithuanian")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is lt_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1257")
    assert profile.audio_track_codes == frozenset({"lit", "lt", "lithuanian"})
    assert profile.asr_language == "lt" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "lt",
        ("lt",),
        ("lt-orig",),
    )
    assert profile.captions.audio_pattern == "^lt(-|$)" and profile.captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (DECIDED 6)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender",)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Knyga yra ant stalo."


def test_the_sentence_rules_are_latin_with_the_lithuanian_quotes():
    rules = get_profile("lt").sentence_rules
    assert rules.abbreviations == LT_ABBREVIATIONS
    assert rules.openers == LT_OPENERS and rules.closers == LT_CLOSERS
    assert rules.terminators == frozenset(".!?‼⁉⁇⁈") and rules.space_aware is True


def test_the_dictionary_keys_fold_stress_marks_off_both_ends():
    """D-1: the wty lemma rows are unstressed, so a stressed query or key meets the plain one."""
    keys = get_profile("lt").dict_keys
    assert keys.fold_term("kny\u0301ga") == keys.fold_term("Knyga") == "knyga"
    assert keys.fold_term("knyga") == "knyga"  # idempotent


def test_a_stressed_deck_front_meets_the_mined_word():
    fold = get_profile("lt").dedup_fold
    assert fold is not None
    assert fold("kny\u0303ga") == fold("knyga") == fold("Knyga.")
    assert fold(fold("knyga")) == fold("knyga")


def test_the_probe_ladder_offers_lemma_surface_and_casefold():
    lookup = get_profile("lt").lookup
    assert lookup.candidates("knygos", "", None) == []
    assert lookup.candidates("knyga", "Knygos", None) == [("Knygos", 0), ("knygos", 0)]


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("lt").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "lt",
        "googletts_lt",
        "sentencetts_lt",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("knyga", "") == "knyga"
    assert audio.candidates is not None


def test_scoped_defaults_turn_on_the_lithuanian_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "lt")
    assert config.language == "lt" and config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LT_SUBTITLE_REGEX
    assert {key: config.anki_fields[key] for key in ("pos", "noun_gender")} == {"pos": "", "noun_gender": ""}
    assert config.downloader_subtitle_langs == "lt"
    assert [entry.kind for entry in config.expression_audio_chain] == ["googletts"]


def test_the_parser_is_the_spaced_factory_with_no_post_pass():
    profile = get_profile("lt")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "lt"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None
    assert parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in LT_CATALOG}
    assert set(by_id) == {"wty-lt-en", "opensubtitles-lt"}
    dictionary, frequency = by_id["wty-lt-en"], by_id["opensubtitles-lt"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/lt/en/wty-lt-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert (
        frequency.url == "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/lt/lt_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in LT_CATALOG)
    doc = lt_catalog.__doc__ or ""
    assert "CC BY-SA 4.0" in doc and "Leipzig" in doc and "wty-lt-lt" in doc
    assert get_profile("lt").catalog == LT_CATALOG
