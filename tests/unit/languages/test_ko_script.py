"""Korean script gate, normalisation, sentence rules, folding, mining gate."""

import inspect
import unicodedata

from anki_miner.languages.ko import script as ko_script
from anki_miner.languages.token import LanguageToken
from anki_miner.services.morphology import TokenInclusionRule
from anki_miner.services.subtitle_parser import SubtitleParserService


def test_contains_target_script_accepts_hangul_and_hanja():
    s = ko_script.KoreanScript()
    assert s.contains_target_script("학생")
    assert s.contains_target_script("ㄱㄴ")
    assert s.contains_target_script(unicodedata.normalize("NFD", "학"))
    assert s.contains_target_script("漢字")
    assert s.contains_target_script("樂")  # compatibility hanja 樂 (락)
    assert not s.contains_target_script("hello world")
    assert not s.contains_target_script("ひらがな")


def test_filter_options_bind_to_the_two_scoped_script_booleans():
    options = ko_script.KoreanScript().filter_options()
    assert [o.option_id for o in options] == ["hangul_only", "hanja_containing"]
    assert [o.config_field for o in options] == [
        "exclude_hiragana_only_words",
        "exclude_katakana_only_words",
    ]
    assert all(o.label for o in options)


def test_matches_classifies_hangul_only_and_hanja_containing():
    s = ko_script.KoreanScript()
    assert s.matches("hangul_only", "학생")
    assert not s.matches("hangul_only", "學生")
    assert s.matches("hanja_containing", "學生")
    assert s.matches("hanja_containing", "樂")
    assert not s.matches("hanja_containing", "학생")
    assert not s.matches("unknown_option", "학생")


def test_normalize_composes_jamo_to_syllables():
    decomposed = unicodedata.normalize("NFD", "학생")
    assert decomposed != "학생"
    assert ko_script.ko_normalize(decomposed) == "학생"


def test_sentence_rules_carry_cjk_and_ascii_terminators_and_are_space_aware():
    rules = ko_script.KO_SENTENCE_RULES
    assert {".", "?", "!"} <= rules.terminators
    assert {"。", "？", "！"} <= rules.terminators
    assert "…" in rules.ellipses
    assert "「" in rules.openers and "」" in rules.closers
    assert rules.space_aware is True


def test_dict_keys_fold_to_nfc_and_preserve_none():
    keys = ko_script.KoreanDictKeys()
    assert keys.fold_term(unicodedata.normalize("NFD", "학생")) == "학생"
    assert keys.fold_reading(None) is None
    assert keys.fold_reading(unicodedata.normalize("NFD", "궁물")) == "궁물"


def test_homograph_mask_is_rule_a_only_with_the_same_content_carve_out():
    keys = ko_script.KoreanDictKeys()
    rows = [("사과", "apology"), ("사과", "apple"), ("砂果", "apology")]
    assert keys.homograph_keep_mask("사과", rows) == [True, True, True]
    rows = [("사과", "apple"), ("沙果", "a different gloss")]
    assert keys.homograph_keep_mask("사과", rows) == [True, False]
    # No term-exact row and no ja kana ladder: nothing is dropped.
    assert keys.homograph_keep_mask("사과", [("沙果", "x")], "먹다") == [True]


def test_script_gate_defaults_to_none_so_the_japanese_path_is_unchanged():
    rule = TokenInclusionRule(allowed_pos=frozenset({"NN"}), excluded_subtypes=frozenset())
    assert rule.script_gate is None
    assert rule.should_include(LanguageToken("학생", "NN", "NNG", "학생", "")) is False
    param = inspect.signature(SubtitleParserService.__init__).parameters["script_gate"]
    assert param.default is None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_script_gate_lets_hangul_content_words_through():
    rule = TokenInclusionRule(
        allowed_pos=frozenset({"NN"}),
        excluded_subtypes=frozenset(),
        script_gate=ko_script.KoreanScript().contains_target_script,
    )
    assert rule.should_include(LanguageToken("학생", "NN", "NNG", "학생", "")) is True
    assert rule.should_include(LanguageToken("hello", "NN", "NNG", "hello", "")) is False
