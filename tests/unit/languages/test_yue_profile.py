"""The yue LanguageProfile, and the R32 contract with zh."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language


@pytest.fixture(scope="module")
def profile():
    return get_profile("yue")


def test_yue_is_appended_after_zh_and_never_inserted_before_it():
    """R32: yue joins the tuple at the end, so zh's position -- and its behaviour -- is untouched.

    This was ``AVAILABLE_LANGUAGES[-1] == "yue"`` while yue was the newest language. The tuple is
    append-only, so "last" stops being true the moment another language lands (he did); what the
    R32 contract actually needs is that yue sits AFTER zh, which is what is pinned now.
    """
    assert "yue" in AVAILABLE_LANGUAGES
    assert AVAILABLE_LANGUAGES.index("yue") > AVAILABLE_LANGUAGES.index("zh")


def test_the_identity_fields(profile):
    assert profile.code == "yue"
    assert profile.display_name == "廣東話"
    assert profile.english_name == "Cantonese"
    assert profile.smoke_sentence == "我今日睇咗一套好好睇嘅戲。"


def test_the_capabilities_are_the_three_yue_declares(profile):
    assert profile.capabilities == frozenset({"jyutping", "tone_color", "measure_word"})
    assert "pinyin" not in profile.capabilities
    assert "script_variants" not in profile.capabilities
    assert "wiktionary_audio" not in profile.capabilities  # owner decision 1: no Stage W


def test_the_extra_card_fields_are_the_hook_fields(profile):
    assert [spec.key for spec in profile.extra_card_fields] == ["measure_word", "expression_jyutping"]
    assert profile.extra_card_fields[1].raw_html is True


def test_the_encoding_ladder_puts_gb18030_before_big5hkscs(profile):
    # big5hkscs decodes gb18030 bytes without raising, so a Big5-first ladder
    # would silently steal a yue user's Mandarin subtitles.
    assert profile.import_encodings == ("utf-8-sig", "gb18030", "big5hkscs")


def test_the_audio_track_codes_leave_the_mandarin_codes_to_zh(profile):
    assert profile.audio_track_codes == frozenset({"yue", "yue-HK", "zh-yue", "cantonese"})
    assert profile.audio_track_codes.isdisjoint(get_profile("zh").audio_track_codes)


def test_the_caption_codes(profile):
    assert profile.captions.primary == "yue"
    assert profile.captions.codes == ("yue", "zh-HK", "zh-Hant-HK", "zh-Hant")
    assert profile.captions.orig_codes == ("yue-orig",)
    assert profile.captions.audio_pattern == "^(yue|zh-HK)(-|$)"
    assert profile.captions.bare_fallback is True


def test_the_sentence_rules_are_the_han_ones(profile):
    rules = profile.sentence_rules
    assert "。" in rules.terminators and "？" in rules.terminators
    assert rules.space_aware is False
    assert rules.split_on_whitespace is False
    assert rules.abbreviations == frozenset()


def test_asr_and_downloader_codes(profile):
    assert profile.asr_language == "yue"
    assert profile.scoped_defaults["downloader_subtitle_langs"] == "yue"


def test_tone_colour_is_on_and_the_deck_is_generic(profile):
    assert profile.scoped_defaults["reading_tone_color"] is True
    assert profile.scoped_defaults["anki_deck_name"] == "Anki Miner"
    assert profile.scoped_defaults["anki_note_type"] == ""
    assert profile.scoped_defaults["script_variant"] == ""


def test_switching_to_yue_leaves_a_usable_config():
    config = switch_language(AnkiMinerConfig(), "yue")
    assert config.language == "yue"
    assert config.allowed_pos == ("NOUN", "VERB", "ADJ", "ADV")


def test_there_is_no_sentence_annotator(profile):
    assert profile.sentence_annotator is None


def test_the_parser_factory_closes_the_compound_matcher_and_opens_the_script_gate(profile, monkeypatch):
    """S7: asserted on the KWARGS, not on ``parser._compound_matcher``.

    That attribute is None whenever no dictionary is wired
    (``subtitle_parser.py:574``), so a test against it would pass without the
    argument being passed at all.
    """
    from anki_miner.services import subtitle_parser as module

    seen: dict[str, object] = {}
    monkeypatch.setattr(module, "SubtitleParserService", lambda config, **kwargs: seen.update(kwargs))
    profile.create_parser(switch_language(AnkiMinerConfig(), "yue"))

    assert seen["compound_matching"] is False
    # ``==``, not ``is``: two attribute accesses build two DIFFERENT bound-method
    # objects for the same function (probed: ``is`` False, ``==`` True).
    assert seen["script_gate"] == profile.script.contains_target_script
    assert seen["normalize"] is profile.normalize  # a plain function: identity holds
    assert seen["reading_support"] is profile.reading  # the profile's own instance
    assert seen["sentence_annotation"] is False


def test_yue_imports_no_zh_engine():
    """Building the yue profile must not import jieba, pypinyin or opencc.

    R32: yue borrows ``ZhMeasureWordHook`` and nothing else from zh. That hook
    pulls ``zh.reading`` and ``zh.variants``, whose engine imports are
    function-local, so the edge is free -- but only while it stays that way.
    Run in a SUBPROCESS: another test in the session may already have imported
    the zh engine, which would make an in-process assertion vacuous.
    """
    code = (
        "import sys;"
        "import anki_miner.languages.yue as yue;"
        "yue.build_profile();"
        "leaked=[m for m in ('jieba','pypinyin','opencc') if m in sys.modules];"
        "assert not leaked, leaked;"
        "assert 'anki_miner.languages.zh.tokenizer' not in sys.modules;"
        "print('clean')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_the_only_zh_import_is_the_measure_word_hook():
    import anki_miner.languages.yue as yue_package

    package = Path(yue_package.__file__).parent
    lines = [
        line.strip()
        for path in sorted(package.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "languages.zh" in line and not line.lstrip().startswith("#")
    ]
    assert lines == ["from anki_miner.languages.zh.render import ZhMeasureWordHook"]
