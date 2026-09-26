"""yue normalisation, folding, script gate, mined form and lookup ladder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.yue.normalize import fold_term_yue, normalize_yue
from anki_miner.languages.yue.support import (
    YueDictKeyFolding,
    YueLookupStrategy,
    YueMinedFormPolicy,
    YueScriptSupport,
)
from anki_miner.languages.yue.variants import HK_VARIANT_PAIRS

VARIANTS = Path(__file__).parents[2] / "fixtures" / "yue" / "hk_variants.jsonl"


def variant_rows():
    return [json.loads(line) for line in VARIANTS.read_text(encoding="utf-8").splitlines() if line]


def test_normalize_is_nfc_only():
    # chr(), not a literal: a compatibility ideograph and its NFC answer look
    # IDENTICAL on screen, and the pasted literal is what a reviewer reads.
    assert normalize_yue(chr(0xF9D1)) == chr(0x516D)  # U+F9D1 -> 六, a plain NFC fold
    assert normalize_yue("骨") == "骨"
    assert normalize_yue("ＡＢＣ") == "ＡＢＣ"  # width is NOT folded in the sentence


def test_fold_term_folds_radicals_and_fullwidth_latin_only():
    assert fold_term_yue(chr(0x2F00)) == chr(0x4E00)  # Kangxi radical U+2F00 -> ideograph U+4E00
    assert fold_term_yue("ＡＢＣ１２３") == "ABC123"
    assert fold_term_yue("㈱") == "㈱"  # enclosed CJK is NOT expanded
    assert fold_term_yue("｟") == "｟"  # the sentence rules list it as an opener
    assert fold_term_yue("戲") == "戲"


def test_fold_reading_casefolds_jyutping():
    folding = YueDictKeyFolding()
    assert folding.fold_reading("Hoeng1 Gong2") == "hoeng1 gong2"
    assert folding.fold_reading(None) is None


def test_dedup_fold_is_the_term_fold():
    folding = YueDictKeyFolding()
    assert folding.dedup_fold("ＡＢＣ") == folding.fold_term("ＡＢＣ") == "ABC"


def test_the_script_gate_is_han_membership():
    script = YueScriptSupport()
    assert script.filter_options() == ()
    assert script.matches("anything", "戲") is False
    assert script.contains_target_script("啲")
    assert script.contains_target_script("IQ題")  # a mixed token still passes; the dictionary stops it
    assert not script.contains_target_script("Netflix")
    assert not script.contains_target_script("2024")
    assert not script.contains_target_script("ＡＢＣ")
    assert not script.contains_target_script("，")


def test_the_mined_form_prefers_the_lemma():
    policy = YueMinedFormPolicy()
    # the offsets case: the spaced slice is the surface, the space-free word the front
    assert policy.mined_form("ADV", "", "今日", "今 日") == "今日"
    assert policy.mined_form("NOUN", "", "", "戲") == "戲"
    assert policy.mined_form("NOUN", "orth", "", "") == "orth"


def test_the_seven_hk_pairs_are_the_fixture_pairs():
    assert [list(pair) for pair in HK_VARIANT_PAIRS] == [row["pair"] for row in variant_rows()]


@pytest.mark.parametrize("row", variant_rows(), ids=lambda row: row["hk_spelling"])
def test_the_ladder_offers_each_spelling_the_other_way(row):
    ladder = YueLookupStrategy()
    assert (row["standard_spelling"], 0) in ladder.candidates(row["hk_spelling"], "", None)
    assert (row["hk_spelling"], 0) in ladder.candidates(row["standard_spelling"], "", None)


def test_the_ladder_offers_the_radical_normalised_form():
    assert ("一切", 0) in YueLookupStrategy().candidates(chr(0x2F00) + "切", "", None)


def test_the_ladder_never_offers_the_query_itself_and_is_empty_for_a_plain_word():
    assert YueLookupStrategy().candidates("睇咗", "", None) == []


def test_the_homograph_mask_is_rule_a_only():
    folding = YueDictKeyFolding()
    rows = [("戲", "a play"), ("戲劇", "drama")]
    assert folding.homograph_keep_mask("戲", rows) == [True, False]
    assert folding.homograph_keep_mask("無此詞", rows) == [True, True]
