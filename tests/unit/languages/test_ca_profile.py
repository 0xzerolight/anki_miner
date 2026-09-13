"""The Catalan profile: data, wiring, catalogue and registration (hard-requires spaCy + ca_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.capabilities import CAPABILITIES, search
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.ca.catalog import CA_CATALOG
from anki_miner.languages.ca.morphology import CA_ABBREVIATIONS, ca_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "ca" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["ca"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[ca]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("ca")
    assert (profile.code, profile.display_name, profile.english_name) == ("ca", "Català", "Catalan")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is ca_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"cat", "ca", "catalan"})
    assert profile.asr_language == "ca" and profile.wiktionary_code == ""
    assert profile.captions.primary == "ca" and profile.captions.codes == ("ca", "ca-ES")
    assert profile.captions.orig_codes == ("ca-orig",) and profile.captions.audio_pattern == "^ca(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (item brief)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender",)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "L'estudiant va llegir un llibre interessant ahir."
    assert profile.sentence_rules.abbreviations == CA_ABBREVIATIONS


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("ca").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "ca",
        "googletts_ca",
        "sentencetts_ca",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("llibre", "") == "llibre"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "ca")
    assert config.language == "ca"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.downloader_subtitle_langs == "ca"


def test_a_deck_front_with_its_article_meets_the_mined_lemma():
    fold = get_profile("ca").dedup_fold
    assert fold is not None
    assert fold("el llibre") == fold("llibre") == "llibre"
    assert fold("L'home") == fold("home") == "home"


def test_the_lookup_ladder_restores_a_degraded_interpunct_before_the_hyphen_parts():
    """Order (en contract item 2): surface · surface casefold · rung(lemma, surface) · hyphen parts of the lemma."""
    lookup = get_profile("ca").lookup
    assert lookup.candidates("col-legi", "col-legi", None) == [("col·legi", 0), ("col", 0), ("legi", 0)]
    assert lookup.candidates("colegi", "colegi", None) == [("col·legi", 0)]
    # CA-2: lemma and surface differ; the lemma's restored spelling (the full entry) precedes the surface's (a stub)
    assert lookup.candidates("colegi", "Colegis", None) == [
        ("Colegis", 0),
        ("colegis", 0),
        ("col·legi", 0),
        ("Col·legis", 0),
    ]
    assert lookup.candidates("llibres", "", None) == []  # the PROBE word shape: nothing to vary, never itself


def test_the_parser_is_the_spaced_factory():
    profile = get_profile("ca")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "ca"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in CA_CATALOG}
    assert set(by_id) == {"wty-ca-en", "opensubtitles-ca"}
    dictionary, frequency = by_id["wty-ca-en"], by_id["opensubtitles-ca"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ca/en/wty-ca-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ca/ca_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in CA_CATALOG)
    assert get_profile("ca").catalog == CA_CATALOG


def test_settings_search_finds_catalan():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("catalan")
    assert {"catalan", "ca"} <= set(capability.keywords)
