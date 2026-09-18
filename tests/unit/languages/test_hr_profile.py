"""The Croatian profile: data, wiring, catalogue, registration, the pack and the smoke leg.

Hard-requires spaCy and ``hr_core_news_sm`` (the mining language's own suite, no ``importorskip``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui import app as app_module
from anki_miner.gui.capabilities import CAPABILITIES, search
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.hr.catalog import HR_CATALOG
from anki_miner.languages.hr.morphology import (
    HR_ABBREVIATIONS,
    HR_EXCLUDED_SUBTYPES,
    HR_SUBTITLE_REGEX,
    hr_normalize,
    hr_tone_fold,
)
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
DJAK = "\u0111ak"


def test_registration_and_the_extra():
    assert "hr" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["hr"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[hr]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("hr")
    assert (profile.code, profile.display_name, profile.english_name) == ("hr", "Hrvatski", "Croatian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is hr_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    assert profile.audio_track_codes == frozenset({"hrv", "scr", "hr", "croatian"})
    assert profile.captions.primary == "hr" and profile.captions.codes == ("hr",)
    assert profile.captions.orig_codes == ("hr-orig",) and profile.captions.audio_pattern == "^hr(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (DECIDED 6)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == HR_EXCLUDED_SUBTYPES
    assert profile.smoke_sentence == "Student je ju\u010der pro\u010ditao zanimljivu knjigu."
    assert profile.sentence_rules.abbreviations == HR_ABBREVIATIONS
    assert profile.dict_keys.fold_term("Knjige") == "knjige"  # NFC + casefold, nothing else


def test_only_the_dictionary_speaks_serbo_croatian():
    """R28: no hr tree exists in wty, so the dictionary is sh - and nothing else is."""
    profile = get_profile("hr")
    assert profile.wiktionary_code == "sh"
    assert profile.asr_language == "hr" and profile.audio.gtts_lang == "hr"
    assert profile.audio.custom_fetcher_language == "hr"
    assert [spec.id for spec in profile.catalog if "sh" in spec.id] == ["wty-sh-en"]
    assert "/hr/" in next(spec.url for spec in profile.catalog if spec.kind == "freq")


def test_the_grammar_hook_carries_gender_and_aspect_with_the_tone_fold():
    profile = get_profile("hr")
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    hook = profile.render_hooks[1]
    assert hook.field_names() == ("noun_gender", "aspect_pair")
    assert hook._partner_fold is hr_tone_fold  # the wty head line writes tone marks Croatian never does
    assert not hook._article_map  # Croatian has no articles
    assert not hook._animacy_labels  # animacy is out of hr's three extra fields


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("hr").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "hr",
        "googletts_hr",
        "sentencetts_hr",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("knjiga", "") == "knjiga"


def test_scoped_defaults_turn_on_the_croatian_filters():
    config = switch_language(AnkiMinerConfig(), "hr")
    assert config.language == "hr"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == HR_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == HR_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["aspect_pair"] == ""
    assert config.downloader_subtitle_langs == "hr"


def test_switching_away_and_back_leaks_nothing():
    japanese = switch_language(switch_language(AnkiMinerConfig(), "hr"), "ja")
    assert japanese.language == "ja" and japanese.excluded_subtypes != HR_EXCLUDED_SUBTYPES
    croatian = switch_language(japanese, "hr")
    assert croatian.excluded_subtypes == HR_EXCLUDED_SUBTYPES and croatian.language == "hr"


def test_the_dedup_fold_has_no_leading_word_table():
    fold = get_profile("hr").dedup_fold
    assert fold is not None
    assert fold("Knjiga") == "knjiga"
    assert fold("ta knjiga") == "ta knjiga"  # S3 is empty: Croatian has no articles to drop


def test_the_lookup_ladder_is_the_surface_then_its_casefold():
    lookup = get_profile("hr").lookup
    assert lookup.candidates("knjiga", "Knjige", None) == [("Knjige", 0), ("knjige", 0)]
    assert lookup.candidates("knjige", "", None) == []  # no dj-to-d-bar rung (E.2.8, R34)


def test_the_parser_carries_the_short_infinitive_pass():
    profile = get_profile("hr")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "hr"))
    assert parser.normalize is profile.normalize
    assert parser._token_post_pass is not None and parser._compound_matcher is None


def test_normalisation_keeps_the_dje_letter_end_to_end():
    profile = get_profile("hr")
    assert profile.normalize("\u0110ak") == "\u0110ak"
    assert profile.dict_keys.fold_term("\u0110ak") == DJAK


def test_the_catalogue_ships_the_serbo_croatian_dictionary_and_a_lemmatised_list():
    by_id = {spec.id: spec for spec in HR_CATALOG}
    assert set(by_id) == {"wty-sh-en", "opensubtitles-hr"}
    dictionary, frequency = by_id["wty-sh-en"], by_id["opensubtitles-hr"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/sh/en/wty-sh-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hr/hr_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in HR_CATALOG)
    assert get_profile("hr").catalog == HR_CATALOG


def test_the_model_pack_is_the_generated_manifest():
    pack = load_pack("hr")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "hr_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == "393ad455bba13536e6fb257bce2b5ef0253f41c1fb5249d8c16dde6cf116d196"
    assert pack.approx_download_mb == 14  # ceil(13,187,227 B / 1e6): the generator's rule


def test_settings_search_finds_croatian():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("croatian") and capability in search("Hrvatski")
    assert {"croatian", "hrvatski", "hr"} <= set(capability.keywords)


def test_the_hr_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("hr") == 0
    assert "BUNDLED_SMOKE_PASS: language hr" in capsys.readouterr().out
