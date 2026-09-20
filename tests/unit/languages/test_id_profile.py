"""The Indonesian profile: data, wiring, catalogue and registration (no engine, no pack, no extra)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.capabilities import CAPABILITIES, search
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.morphology import SpacedMinedForm
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript, nfc_normalize
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.id import AFFIXES_FIELD, FORMAL_FORM_FIELD, ID_SENTENCE_RULES, ROOT_FIELD
from anki_miner.languages.id.catalog import ID_CATALOG
from anki_miner.languages.id.morphology import (
    ID_ABBREVIATIONS,
    ID_EXCLUDED_SUBTYPES,
    IndonesianDictKeys,
    IndonesianLookupStrategy,
)
from anki_miner.languages.id.render import FormalFormHook, RootAffixHook
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]


def test_registration_without_an_extra_or_a_pack():
    assert "id" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert "id" not in extras and not any("[id]" in dep for dep in extras["languages"])
    assert load_pack("id") is None


def test_the_profile_wiring():
    profile = get_profile("id")
    assert (profile.code, profile.display_name, profile.english_name) == ("id", "Bahasa Indonesia", "Indonesian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, IndonesianLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, IndonesianDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None and profile.unavailable_reason is None
    assert profile.normalize is nfc_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"ind", "id", "in", "indonesian"})
    assert not {"ms", "may", "msa", "zsm"} & profile.audio_track_codes  # a Malay dub is not Indonesian
    assert profile.asr_language == "id" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "id",
        ("id",),
        ("id-orig",),
    )
    assert profile.captions.audio_pattern == "^id(-|$)" and profile.captions.bare_fallback is True
    assert profile.capabilities == frozenset({"word_root", "indonesian_affixes", "indonesian_register"})
    assert "lemmatised_frequency" not in profile.capabilities and "wiktionary_audio" not in profile.capabilities
    assert profile.extra_card_fields == (ROOT_FIELD, AFFIXES_FIELD, FORMAL_FORM_FIELD)
    assert ROOT_FIELD.capability == "word_root" and ROOT_FIELD.placeholder == "Root"  # ruling R-ROOT
    assert [type(hook) for hook in profile.render_hooks] == [RootAffixHook, FormalFormHook]
    assert profile.pos_defaults.allowed_pos == ("WORD",) and profile.pos_defaults.excluded_subtypes == ("stopword",)
    assert profile.sentence_rules == ID_SENTENCE_RULES and ID_SENTENCE_RULES.abbreviations == ID_ABBREVIATIONS
    assert profile.content_style is SPACED_CONTENT_STYLE
    assert profile.smoke_sentence == "Saya sedang membaca buku di rumah."


def test_the_dedup_fold_is_the_key_fold_plus_trailing_punctuation():
    fold = get_profile("id").dedup_fold
    assert fold is not None
    assert fold("Rumah") == fold("rumah.") == "rumah" and fold("Mengérti") == "mengerti"


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("id").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "id",
        "googletts_id",
        "sentencetts_id",
    )
    assert audio.custom_fetcher_language == "id" and audio.papago_speaker is None
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("membeli", "") == "membeli"


def test_scoped_defaults_carry_the_word_class_and_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "id")
    assert config.language == "id" and config.downloader_subtitle_langs == "id"
    assert config.allowed_pos == ("WORD",) and config.excluded_subtypes == ID_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert {config.anki_fields[key] for key in ("root", "affixes", "formal_form")} == {""}  # the name is the switch
    assert config.anki_fields["expression_furigana"] == "" and config.anki_fields["sentence_furigana"] == ""


def test_the_parser_is_the_spaced_factory_without_a_post_pass():
    profile = get_profile("id")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "id"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_an_in_app_frequency_list():
    by_id = {spec.id: spec for spec in ID_CATALOG}
    assert set(by_id) == {"wty-id-en", "opensubtitles-id"}
    dictionary, frequency = by_id["wty-id-en"], by_id["opensubtitles-id"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/id/en/wty-id-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/id/id_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in ID_CATALOG)
    assert get_profile("id").catalog == ID_CATALOG


def test_settings_search_finds_indonesian():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("indonesian") and capability in search("Bahasa Indonesia")
    assert {"indonesian", "bahasa indonesia", "id"} <= set(capability.keywords)
