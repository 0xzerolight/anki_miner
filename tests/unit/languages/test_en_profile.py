"""The English profile: data, wiring, catalogue and registration (hard-requires spaCy + en_core_web_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import POS_FIELD
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.en.catalog import EN_CATALOG
from anki_miner.languages.en.morphology import EN_ABBREVIATIONS, EN_EXCLUDED_SUBTYPES, EN_LEADING_WORDS
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]

#: Entries that are not in spaCy's English tokenizer exceptions (D15).
ADDITIONS = frozenset({"etc", "u.s", "u.k", "capt", "lt", "sgt", "col", "sr"})
#: Ordinary words (or bare letters) a state/title abbreviation list would turn into non-terminators.
NEVER = frozenset(
    {
        "a",
        "i",
        "ill",
        "miss",
        "mass",
        "wash",
        "id",
        "ind",
        "la",
        "mo",
        "ore",
        "del",
        "co",
        "mar",
        "dec",
        "gen",
        "b",
        "x",
    }
)


def test_registration_and_the_extra():
    assert "en" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES  # wave leads append after en
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["en"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[en]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("en")
    assert (profile.code, profile.display_name, profile.english_name) == ("en", "English", "English")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"eng", "en", "english"})
    assert profile.asr_language == "en" and profile.wiktionary_code == ""
    assert profile.captions.primary == "en" and profile.captions.orig_codes == ("en-orig",)
    assert profile.captions.codes == ("en", "en-US", "en-GB", "en-CA", "en-AU")
    assert profile.capabilities == frozenset({"pos_tag", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD,)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook]
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == EN_EXCLUDED_SUBTYPES == ()


def test_audio_speaks_the_front_through_google():
    audio = get_profile("en").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "en",
        "googletts_en",
        "sentencetts_en",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("go", "") == "go"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "en")
    assert config.language == "en"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.downloader_subtitle_langs == "en"


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("en").dedup_fold
    assert fold is not None
    assert fold("to go") == fold("go") == "go"
    assert fold("The dog.") == fold("dog")
    assert frozenset({"a", "an", "the", "to"}) == EN_LEADING_WORDS


def test_abbreviations_come_from_spacy_minus_real_words():
    from spacy.lang.en.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    spacy_keys = {text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and len(text) > 1}
    assert EN_ABBREVIATIONS - spacy_keys <= ADDITIONS
    assert not EN_ABBREVIATIONS & NEVER
    assert {"mr", "mrs", "ms", "dr", "jr", "sr", "st", "vs", "etc", "e.g", "i.e", "a.m", "p.m"} <= EN_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in EN_ABBREVIATIONS)
    assert get_profile("en").sentence_rules.abbreviations == EN_ABBREVIATIONS


def test_the_parser_is_the_spaced_factory():
    profile = get_profile("en")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "en"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in EN_CATALOG}
    assert set(by_id) == {"wty-en-en", "opensubtitles-en"}
    dictionary, frequency = by_id["wty-en-en"], by_id["opensubtitles-en"]
    assert dictionary.kind == "dict" and dictionary.url.endswith("/dict/en/en/wty-en-en.zip")
    assert dictionary.url.startswith("https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/")
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url.endswith("/content/2018/en/en_50k.txt")
    assert all("CC BY-SA 4.0" in spec.license_note for spec in EN_CATALOG)
    assert get_profile("en").catalog == EN_CATALOG
