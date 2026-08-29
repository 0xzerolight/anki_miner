"""The registered Korean profile is complete and Japanese-free."""

import dataclasses

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import available_languages, get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS


def test_ko_is_registered_with_a_native_display_name():
    profile = get_profile("ko")
    assert "ko" in AVAILABLE_LANGUAGES
    assert "ko" in available_languages()
    assert profile.code == "ko"
    assert profile.display_name == "한국어"


def test_capabilities_are_the_korean_set_only():
    caps = get_profile("ko").capabilities
    assert caps == frozenset({"hangul_filters", "hanja"})
    assert not {"pitch", "furigana", "kana_filters", "name_wordsets", "deinflection"} & caps


def test_media_and_caption_parameters():
    profile = get_profile("ko")
    assert profile.audio_track_codes == frozenset({"kor", "ko", "korean"})
    assert profile.asr_language == "ko"
    assert profile.captions.primary == "ko"
    assert profile.captions.codes == ("ko",)
    assert profile.captions.orig_codes == ("ko-orig",)
    assert profile.captions.audio_pattern == "^ko(-|$)"
    assert profile.captions.bare_fallback is True
    assert profile.import_encodings == ("utf-8-sig", "cp949")


def test_lookup_takes_three_arguments_and_returns_conditions_pairs():
    lookup = get_profile("ko").lookup
    assert lookup.candidates("먹다", "먹", None) == [("먹", 0)]
    assert lookup.candidates("학생", "학생", None) == []
    assert lookup.candidates("학생", "", None) == []


def test_content_style_and_sentence_rules():
    profile = get_profile("ko")
    assert profile.content_style.font_role == "ko"
    assert profile.content_style.families
    assert profile.content_style.wrap("학생이 밥을 먹었다") == "학생이 밥을 먹었다"
    assert profile.sentence_rules.space_aware is True
    assert profile.normalize("학생") == "학생"


def test_scoped_defaults_cover_every_scoped_field_with_no_japanese_values():
    defaults = get_profile("ko").scoped_defaults
    assert set(defaults) == set(LANGUAGE_SCOPED_FIELDS)
    assert defaults["dictionary_chain"] == ()
    assert defaults["pitch_chain"] == ()
    assert defaults["excluded_wordsets"] == ()
    assert defaults["downloader_subtitle_langs"] == "ko"
    assert defaults["allowed_pos"] == get_profile("ko").pos_defaults.allowed_pos
    assert defaults["excluded_subtypes"] == ("MAJ", "NNB")
    assert defaults["script_variant"] == ""
    assert defaults["reading_tone_color"] is False
    assert "jmdict" not in repr(defaults).lower()


def test_scoped_defaults_apply_on_a_first_switch():
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.switching import switch_language

    switched = switch_language(AnkiMinerConfig(), "ko")
    assert switched.language == "ko"
    assert switched.dictionary_chain == ()
    assert switched.allowed_pos == get_profile("ko").pos_defaults.allowed_pos
    assert switched.anki_fields["word"] == "Expression"
    assert switched.anki_fields["expression_furigana"] == ""
    assert dataclasses.is_dataclass(switched)


def test_first_switch_lands_on_a_deck_ankiconnect_accepts():
    """A blank deck name fails every add: AnkiConnect rejects "" as a deck."""
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.switching import switch_language

    switched = switch_language(AnkiMinerConfig(), "ko")
    assert switched.anki_deck_name == "Anki Miner"
    # Parity with zh: "Anki Miner" is the generic default, not a ja-specific one.
    assert switched.anki_deck_name == switch_language(AnkiMinerConfig(), "zh").anki_deck_name
    # The ja note type ("Lapis") IS ja-specific, so ko ships empty like zh.
    assert switched.anki_note_type == ""
    assert switch_language(AnkiMinerConfig(), "zh").anki_note_type == ""
