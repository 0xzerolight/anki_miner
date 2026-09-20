"""The Vietnamese profile: data, wiring, catalogue and registration (hard-requires underthesea)."""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.morphology import SpacedMinedForm
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.vi import catalog as vi_catalog
from anki_miner.languages.vi.catalog import VI_CATALOG
from anki_miner.languages.vi.keys import VI_KEYS, vi_fold_term
from anki_miner.languages.vi.morphology import VietnameseLookup, VietnameseNamePass
from anki_miner.languages.vi.pos import VI_ALLOWED_POS, VI_POS_LABELS
from anki_miner.languages.vi.script import (
    VI_SENTENCE_RULES,
    VI_SUBTITLE_REGEX,
    VietnameseScript,
    is_vietnamese_word,
    vi_normalize,
)


def test_registration():
    assert "vi" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_the_profile_wiring():
    profile = get_profile("vi")
    assert (profile.code, profile.display_name, profile.english_name) == ("vi", "Tiếng Việt", "Vietnamese")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, VietnameseLookup)
    assert isinstance(profile.script, VietnameseScript)
    assert profile.dict_keys is VI_KEYS and profile.dedup_fold is vi_fold_term
    assert profile.normalize is vi_normalize and profile.sentence_rules is VI_SENTENCE_RULES
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1258")
    assert profile.audio_track_codes == frozenset({"vie", "vi", "vietnamese"})
    assert profile.asr_language == "vi" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("vi", ("vi",), ("vi-orig",))
    assert captions.audio_pattern == "^vi(-|$)" and captions.bare_fallback is True
    assert profile.pos_defaults.allowed_pos == VI_ALLOWED_POS
    assert profile.pos_defaults.excluded_subtypes == ("stopword", "name")
    assert profile.pos_defaults.labels == VI_POS_LABELS
    assert profile.content_style is SPACED_CONTENT_STYLE
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.smoke_sentence == "Hôm nay trời đẹp quá."
    assert "wiktionary_audio" not in profile.capabilities and "vi_ipa" not in profile.capabilities
    assert profile.capabilities == frozenset({"hanviet"})


def test_one_key_for_both_tone_styles_and_every_case():
    fold = get_profile("vi").dedup_fold
    assert fold is not None
    assert fold("Hoà bình") == fold("hòa bình") == fold("HÒA BÌNH") == "hòa bình"
    assert fold(fold("Hoà bình")) == fold("Hoà bình")


def test_word_audio_is_google_vi_and_speaks_the_front():
    audio = get_profile("vi").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "vi",
        "googletts_vi",
        "sentencetts_vi",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("hòa bình", "") == "hòa bình"
    assert audio.candidates is not None


def test_scoped_defaults_turn_on_the_vietnamese_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "vi")
    assert config.language == "vi" and config.allowed_pos == VI_ALLOWED_POS
    assert config.excluded_subtypes == ("stopword", "name")
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == VI_SUBTITLE_REGEX
    assert config.downloader_subtitle_langs == "vi"
    assert [entry.kind for entry in config.expression_audio_chain] == ["googletts"]
    assert config.anki_fields["expression_furigana"] == "" and config.anki_deck_name == "Anki Miner"


def test_the_parser_carries_the_name_pass_and_the_token_gate():
    profile = get_profile("vi")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "vi"))
    assert parser.normalize is vi_normalize
    assert parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, VietnameseNamePass)
    assert parser._inclusion_rule.script_gate is is_vietnamese_word


def test_the_catalogue_ships_wiktionary():
    by_id = {spec.id: spec for spec in VI_CATALOG}
    assert set(by_id) == {"wty-vi-en"}
    dictionary = by_id["wty-vi-en"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/vi/en/wty-vi-en.zip"
    )
    assert "CC BY-SA 4.0" in dictionary.license_note
    doc = vi_catalog.__doc__ or ""
    assert "VNEDICT" in doc and "CC BY 3.0" in doc and "Leipzig" in doc
    assert get_profile("vi").catalog == VI_CATALOG
