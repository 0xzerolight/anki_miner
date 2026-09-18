"""The Polish profile: data, wiring, catalogue, registration and the in-process smoke leg (hard-requires the model)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui import app as app_module
from anki_miner.gui.capabilities import CAPABILITIES, search
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript, nfc_normalize
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.pl.catalog import PL_CATALOG
from anki_miner.languages.pl.morphology import (
    PL_EXCLUDED_SUBTYPES,
    PL_SENTENCE_RULES,
    PL_SUBTITLE_REGEX,
    pl_dedup_fold,
)
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "pl" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["pl"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[pl]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("pl")
    assert (profile.code, profile.display_name, profile.english_name) == ("pl", "Polski", "Polish")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is nfc_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    assert profile.audio_track_codes == frozenset({"pol", "pl", "polish"})
    assert profile.asr_language == "pl" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "pl",
        ("pl",),
        ("pl-orig",),
    )
    assert profile.captions.audio_pattern == "^pl(-|$)" and profile.captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (DECIDED 6)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender",)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == PL_EXCLUDED_SUBTYPES
    assert profile.sentence_rules == PL_SENTENCE_RULES
    assert profile.content_style is SPACED_CONTENT_STYLE
    assert profile.dedup_fold is pl_dedup_fold
    assert profile.smoke_sentence == "Student przeczytał wczoraj ciekawą książkę."


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("pl").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "pl",
        "googletts_pl",
        "sentencetts_pl",
    )
    assert audio.custom_fetcher_language == "pl" and audio.papago_speaker is None
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("książka", "") == "książka"


def test_scoped_defaults_carry_the_nkjp_table_and_the_polish_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "pl")
    assert config.language == "pl" and config.downloader_subtitle_langs == "pl"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == PL_EXCLUDED_SUBTYPES
    # R11: the shared preset's capital class has no Ą Ć Ę Ł Ń Ś Ź Ż, so pl ships its own (P19).
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == PL_SUBTITLE_REGEX
    assert config.subtitle_regex_filter != LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.anki_fields["expression_furigana"] == ""


def test_the_lookup_ladder_is_the_shared_latin_one():
    lookup = get_profile("pl").lookup
    assert lookup.candidates("książka", "Książki", None) == [("Książki", 0), ("książki", 0)]
    assert lookup.candidates("biało-czerwony", "Biało-czerwona", None) == [
        ("Biało-czerwona", 0),
        ("biało-czerwona", 0),
        ("biało", 0),
        ("czerwony", 0),
    ]
    assert lookup.candidates("książki", "", None) == []  # the PROBE word shape: nothing to vary, never itself


def test_the_parser_is_the_spaced_factory_without_a_post_pass():
    profile = get_profile("pl")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "pl"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in PL_CATALOG}
    assert set(by_id) == {"wty-pl-en", "opensubtitles-pl"}
    dictionary, frequency = by_id["wty-pl-en"], by_id["opensubtitles-pl"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/pl/en/wty-pl-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/pl/pl_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in PL_CATALOG)
    assert get_profile("pl").catalog == PL_CATALOG


def test_settings_search_finds_polish():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("polish") and capability in search("Polski")
    assert {"polish", "polski", "pl"} <= set(capability.keywords)


def test_the_pl_smoke_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("pl") == 0
    assert "BUNDLED_SMOKE_PASS: language pl" in capsys.readouterr().out
