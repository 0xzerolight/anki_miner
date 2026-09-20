"""The Thai profile: registration, sentence rules, captions, encodings, smoke line."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.languages.th.pos import TH_ALLOWED_POS, TH_EXCLUDED_SUBTYPES


def _config_codes():
    from anki_miner.config.config import _LANGUAGE_CODES

    return _LANGUAGE_CODES


def test_th_is_available():
    assert "th" in AVAILABLE_LANGUAGES
    # A drift between the two tuples fails HERE with a readable diff, as well as
    # in tests/unit/test_config_language.py.
    assert list(AVAILABLE_LANGUAGES) == list(_config_codes())


def test_profile_identity():
    profile = get_profile("th")
    assert profile.code == "th"
    assert profile.display_name == "ไทย"
    assert profile.english_name == "Thai"
    assert profile.reading is None
    assert profile.sentence_annotator is None


def test_sentence_rules_do_not_treat_the_space_as_a_terminator():
    rules = get_profile("th").sentence_rules
    assert rules.terminators == frozenset("!?")
    assert rules.ellipses == frozenset("…")
    assert rules.space_aware is False
    assert rules.abbreviations == frozenset()
    # `split_on_whitespace` is S9: the FIELD does not exist on SentenceRules yet.
    # Task 9 adds it, sets it in build_profile and asserts it here.


def test_encodings_are_utf8_then_cp874():
    assert get_profile("th").import_encodings == ("utf-8-sig", "cp874")


def test_audio_track_codes_cover_the_iso_639_pair():
    assert get_profile("th").audio_track_codes == frozenset({"tha", "th", "thai"})


def test_captions_request_thai_and_accept_the_bare_code():
    captions = get_profile("th").captions
    assert captions.primary == "th"
    assert captions.codes == ("th",)
    assert captions.orig_codes == ("th-orig",)
    assert captions.audio_pattern == "^th(-|$)"
    assert captions.bare_fallback is True


def test_asr_language_and_smoke_sentence():
    profile = get_profile("th")
    assert profile.asr_language == "th"
    assert profile.smoke_sentence == "วันนี้อากาศดีมาก"


def test_scoped_defaults_cover_every_scoped_field():
    defaults = get_profile("th").scoped_defaults
    assert set(defaults) == set(LANGUAGE_SCOPED_FIELDS)
    assert defaults["allowed_pos"] == TH_ALLOWED_POS
    assert defaults["excluded_subtypes"] == TH_EXCLUDED_SUBTYPES
    assert defaults["anki_deck_name"] == "Anki Miner"
    assert defaults["anki_note_type"] == ""
    assert defaults["downloader_subtitle_langs"] == "th"


def test_switching_to_th_keeps_the_config_frozen_and_scoped():
    config = switch_language(AnkiMinerConfig(), "th")
    assert config.language == "th"
    assert config.allowed_pos == TH_ALLOWED_POS
    with_replacement = dataclasses.replace(config, language="th")
    assert with_replacement.language == "th"


def test_parser_builds_and_mines_the_smoke_sentence():
    from anki_miner.models.reading import ReadingUnit

    profile = get_profile("th")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "th"))
    units = [ReadingUnit(text=profile.smoke_sentence, index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)
    assert {"วันนี้", "อากาศ"} <= {word.mined_form for word in words}
