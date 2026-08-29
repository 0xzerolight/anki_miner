"""zh script gate, key folding, mined-form policy and lookup ladder."""

from __future__ import annotations

import pytest

from anki_miner.languages.zh import support
from anki_miner.languages.zh.support import (
    ZhDictKeyFolding,
    ZhLookupStrategy,
    ZhMinedFormPolicy,
    ZhScriptSupport,
)


@pytest.fixture
def fake_variants(monkeypatch):
    """Replace OpenCC with a table, so these tests never need the zh extra."""

    def _install(mapping: dict[str, list[str]]) -> None:
        monkeypatch.setattr(support, "variant_candidates", lambda term: mapping.get(term, [term]))

    return _install


def test_zh_has_no_script_filter_options():
    """zh hides the settings script-filter section entirely (spec 3.2)."""
    script = ZhScriptSupport()
    assert script.filter_options() == ()
    assert script.matches("hiragana_only", "汉字") is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [("汉字", True), ("这是一句话", True), ("hello", False), ("", False), ("𠀀", True)],
)
def test_contains_target_script_is_han_only(text, expected):
    assert ZhScriptSupport().contains_target_script(text) is expected


def test_fold_term_is_nfc():
    assert ZhDictKeyFolding().fold_term("兀") == "兀"


def test_fold_reading_preserves_none_and_case_folds_pinyin():
    folding = ZhDictKeyFolding()
    assert folding.fold_reading(None) is None
    assert folding.fold_reading("Zhōng Guó") == "zhōng guó"
    assert folding.fold_reading("nī hǎo") == "nī hǎo"


def test_the_import_side_and_the_query_side_fold_identically():
    """The symmetry the whole scheme rests on: one function, both directions."""
    folding = ZhDictKeyFolding()
    stored = folding.fold_reading("Yín Háng")  # importer writes this key
    queried = folding.fold_reading("yín háng")  # lookup asks for this one
    assert stored == queried


def test_rule_a_drops_reading_only_homographs():
    rows = [("行", "row/line"), ("形", "shape")]
    assert ZhDictKeyFolding().homograph_keep_mask("行", rows) == [True, False]


def test_rule_a_keeps_a_reading_only_row_with_the_same_gloss():
    rows = [("行", "row/line"), ("珩", "row/line")]
    assert ZhDictKeyFolding().homograph_keep_mask("行", rows) == [True, True]


def test_no_term_exact_row_keeps_everything():
    rows = [("珩", "a gem"), ("形", "shape")]
    assert ZhDictKeyFolding().homograph_keep_mask("行", rows) == [True, True]


def test_the_lemma_argument_is_accepted_and_ignored():
    """Arity parity with ja; zh has no Rule A' tier."""
    rows = [("珩", "a gem")]
    assert ZhDictKeyFolding().homograph_keep_mask("行", rows, lemma="珩") == [True]


def test_mined_form_is_identity_on_the_surface():
    policy = ZhMinedFormPolicy()
    assert policy.mined_form("n", "", "", "银行") == "银行"
    assert policy.mined_form("v", "吃", "吃", "吃", None) == "吃"


def test_candidates_are_variants_with_a_zero_condition_mask(fake_variants):
    fake_variants({"银行": ["银行", "銀行"]})
    assert ZhLookupStrategy().candidates("银行", "", None) == [("銀行", 0)]


def test_the_query_word_is_never_re_emitted(fake_variants):
    fake_variants({"中文": ["中文"]})
    assert ZhLookupStrategy().candidates("中文", "中文", "vs") == []
