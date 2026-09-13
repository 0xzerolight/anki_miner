"""The Italian profile: data, wiring, catalogue and registration (hard-requires spaCy + it_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.it import catalog as it_catalog
from anki_miner.languages.it.catalog import IT_CATALOG
from anki_miner.languages.it.morphology import IT_ABBREVIATIONS, IT_EXCLUDED_SUBTYPES, AttestedLemmaPass
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "it" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["it"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[it]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("it")
    assert (profile.code, profile.display_name, profile.english_name) == ("it", "Italiano", "Italian")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"ita", "it", "italian"})
    assert profile.asr_language == "it" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "it",
        ("it",),
        ("it-orig",),
    )
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "noun_article", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, NOUN_ARTICLE_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender", "noun_article")
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == IT_EXCLUDED_SUBTYPES == ("BN",)
    assert profile.smoke_sentence == "Il gatto dorme sulla sedia."


def test_the_lookup_ladder_carries_the_enclitic_rung():
    candidates = get_profile("it").lookup.candidates("fammare", "fammi", None)
    assert candidates == [("fammi", 0), ("fam", 0), ("fare", 0)]


def test_audio_speaks_the_front_through_google():
    audio = get_profile("it").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "it",
        "googletts_it",
        "sentencetts_it",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("mangiare", "") == "mangiare"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "it")
    assert config.language == "it" and config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.excluded_subtypes == ("BN",)
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert {key: config.anki_fields[key] for key in ("pos", "noun_gender", "noun_article")} == dict.fromkeys(
        ("pos", "noun_gender", "noun_article"), ""
    )
    assert config.downloader_subtitle_langs == "it"


def test_a_known_word_front_with_an_article_meets_the_mined_lemma():
    fold = get_profile("it").dedup_fold
    assert fold is not None
    assert fold("l'acqua") == fold("acqua") == "acqua" and fold("Il gatto") == "gatto"
    assert fold(fold("L’Acqua")) == fold("L’Acqua") == "acqua"


def test_sentences_know_the_italian_abbreviations():
    assert get_profile("it").sentence_rules.abbreviations == IT_ABBREVIATIONS


def test_the_parser_is_the_spaced_factory_with_the_attested_lemma_pass():
    profile = get_profile("it")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "it"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, AttestedLemmaPass)


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in IT_CATALOG}
    assert set(by_id) == {"wty-it-en", "opensubtitles-it"}
    dictionary, frequency = by_id["wty-it-en"], by_id["opensubtitles-it"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/it/en/wty-it-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert (
        frequency.url == "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/it/it_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in IT_CATALOG)
    assert "CC BY-NC-SA 3.0" in (it_catalog.__doc__ or "") and "wty-it-it" in (it_catalog.__doc__ or "")
    assert get_profile("it").catalog == IT_CATALOG
