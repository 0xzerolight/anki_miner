"""The Portuguese profile: data, variety, wiring, catalogue and registration (hard-requires spaCy + pt_core_news_sm)."""

from __future__ import annotations

import dataclasses
import tomllib
import unicodedata
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.utils import service_factory
from anki_miner.languages import AVAILABLE_LANGUAGES, SCRIPT_VARIANT_IDS
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.pt import pt_normalize
from anki_miner.languages.pt.catalog import PT_CATALOG
from anki_miner.languages.pt.morphology import PT_ABBREVIATIONS, PT_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.sentence_tts_fetcher import GoogleSentenceTtsFetcher

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "pt" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["pt"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[pt]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("pt")
    assert (profile.code, profile.display_name, profile.english_name) == ("pt", "Português", "Portuguese")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"por", "pt", "portuguese"})
    assert profile.asr_language == "pt" and profile.wiktionary_code == ""
    assert profile.captions.primary == "pt" and profile.captions.orig_codes == ("pt-orig",)
    assert profile.captions.codes == ("pt", "pt-BR", "pt-PT") and profile.captions.audio_pattern == "^pt(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "regional_variants", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == PT_EXCLUDED_SUBTYPES == ()
    assert profile.sentence_rules.abbreviations == PT_ABBREVIATIONS
    assert profile.smoke_sentence == "O estudante leu um livro interessante ontem."


@pytest.mark.parametrize(("variant", "voice"), [("br", "pt"), ("pt", "pt-PT"), ("", "pt")])
def test_the_variety_picks_the_google_voice(variant, voice):
    config = dataclasses.replace(switch_language(AnkiMinerConfig(), "pt"), script_variant=variant)
    assert get_profile("pt").audio.resolved_gtts_lang(config) == voice


def test_both_google_legs_follow_the_variety():
    config = dataclasses.replace(
        switch_language(AnkiMinerConfig(), "pt"),
        script_variant="pt",
        expression_audio_chain=(AudioSourceEntry(kind="googletts"),),
        reading_tts_enabled=True,
        reading_tts_google_enabled=True,
    )
    words = service_factory.create_expression_audio_fetcher(config)._fetchers
    sentences = service_factory._build_sentence_audio_fetcher(config)._fetchers
    assert [f._gtts_lang for f in words if isinstance(f, GoogleTranslateAudioFetcher)] == ["pt-PT"]
    assert [f._gtts_lang for f in sentences if isinstance(f, GoogleSentenceTtsFetcher)] == ["pt-PT"]


def test_audio_speaks_the_front_with_namespaced_stems():
    audio = get_profile("pt").audio
    assert (audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == ("googletts_pt", "sentencetts_pt")
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("dar", "") == "dar"


def test_scoped_defaults_start_brazilian_with_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "pt")
    assert config.language == "pt" and config.script_variant == "br"
    assert get_profile("pt").scoped_defaults["script_variant"] in SCRIPT_VARIANT_IDS
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.downloader_subtitle_langs == "pt,pt-BR,pt-PT"


def test_normalize_turns_no_break_spaces_and_soft_hyphens_into_plain_text():
    assert pt_normalize("O compu\u00adtador\u00a0novo") == "O computador novo"
    assert pt_normalize(unicodedata.normalize("NFD", "A canção é bonita.")) == "A canção é bonita."
    assert get_profile("pt").normalize is pt_normalize


def test_known_word_fronts_meet_the_mined_lemma():
    fold = get_profile("pt").dedup_fold
    assert fold is not None
    assert fold("o livro") == fold("Livro") == "livro"
    assert fold("levantar-se") == fold("levantar") == "levantar"
    assert fold("A casa.") == "casa"


def test_the_parser_is_the_spaced_factory():
    profile = get_profile("pt")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "pt"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_offers_wiktionary_and_one_frequency_list_per_variety():
    by_id = {spec.id: spec for spec in PT_CATALOG}
    assert set(by_id) == {"wty-pt-en", "opensubtitles-pt-br", "opensubtitles-pt"}
    dictionary = by_id["wty-pt-en"]
    assert dictionary.kind == "dict" and dictionary.variant == ""
    assert (
        dictionary.url
        == "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/pt/en/wty-pt-en.zip"
    )
    brazil, portugal = by_id["opensubtitles-pt-br"], by_id["opensubtitles-pt"]
    assert (brazil.variant, portugal.variant) == ("br", "pt")
    assert brazil.url.endswith("/content/2018/pt_br/pt_br_50k.txt") and portugal.url.endswith(
        "/content/2018/pt/pt_50k.txt"
    )
    assert brazil.kind == portugal.kind == "freq" and brazil.lemmatise and portugal.lemmatise
    assert all("CC BY-SA 4.0" in spec.license_note for spec in PT_CATALOG)
    assert get_profile("pt").catalog == PT_CATALOG
