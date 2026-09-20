"""zh script gate, key folding, mined-form policy and lookup ladder."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import bound_mined_form, get_profile
from anki_miner.languages.switching import switch_language
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


class TestCharacterSetFront:
    @pytest.fixture(autouse=True)
    def _opencc(self) -> None:
        pytest.importorskip("opencc")

    def test_simplified_converts_a_traditional_front(self):
        assert ZhMinedFormPolicy("simplified").mined_form("n", "", "頭髮", "頭髮") == "头发"

    def test_simplified_keeps_an_ambiguous_front(self):
        assert ZhMinedFormPolicy("simplified").mined_form("n", "", "麵", "麵") == "麵"

    def test_traditional_converts_a_simplified_front(self):
        assert ZhMinedFormPolicy("traditional").mined_form("n", "", "这里", "这里") == "這裡"

    def test_for_config_binds_the_configured_character_set(self):
        config = switch_language(AnkiMinerConfig(), "zh")
        bound = bound_mined_form(get_profile("zh"), dataclasses.replace(config, script_variant="traditional"))
        assert bound.mined_form("n", "", "头发", "头发") == "頭髮"

    def test_an_unbindable_policy_is_returned_as_is(self):
        profile = get_profile("ja")
        assert bound_mined_form(profile, AnkiMinerConfig()) is profile.mined_form


def test_candidates_are_variants_with_a_zero_condition_mask(fake_variants):
    fake_variants({"银行": ["银行", "銀行"]})
    assert ZhLookupStrategy().candidates("银行", "", None) == [("銀行", 0)]


def test_the_query_word_is_never_re_emitted(fake_variants):
    fake_variants({"中文": ["中文"]})
    assert ZhLookupStrategy().candidates("中文", "中文", "vs") == []


class TestTaiwanSpellingIsACandidate:
    """The spelling ``to_script`` writes on the card must be queryable (spec 10.1).

    Generic s2t is the only traditional candidate the ladder used to emit, so a
    traditional-keyed resource indexed under the Taiwan spelling the app itself
    produces (為什麼, 觀眾, 接著) was missed on every lookup.
    """

    @pytest.fixture(autouse=True)
    def _opencc(self) -> None:
        pytest.importorskip("opencc")

    @pytest.mark.parametrize(("term", "taiwan"), [("为什么", "為什麼"), ("观众", "觀眾"), ("接着", "接著")])
    def test_term_variants_offer_the_taiwan_spelling(self, term, taiwan):
        assert taiwan in ZhDictKeyFolding().term_variants(term)

    @pytest.mark.parametrize(("term", "taiwan"), [("为什么", "為什麼"), ("观众", "觀眾"), ("接着", "接著")])
    def test_lookup_candidates_offer_the_taiwan_spelling(self, term, taiwan):
        assert (taiwan, 0) in ZhLookupStrategy().candidates(term, "", None)

    def test_candidates_hold_each_spelling_once(self):
        candidates = ZhLookupStrategy().candidates("银行", "", None)
        assert [c for c, _ in candidates] == list(dict.fromkeys(c for c, _ in candidates))


def _cedict_row(*glosses: str) -> str:
    """One CC-CEDICT row's stored ``content``, shaped as the importer renders it."""
    items = "".join(f'<li class="gloss-sc-li">{gloss}</li>' for gloss in glosses)
    return (
        '<li class="gloss-item"><div class="gloss-content">'
        '<ul class="gloss-sc-ul" data-sc-cccedict="definition">'
        f"{items}</ul></div></li>"
    )


class TestSenseRank:
    """Rows that state no sense of their own rank after the rows that do."""

    @pytest.mark.parametrize(
        "gloss",
        [
            "surname Gan",
            "variant of 乾|干[gān]",
            "old variant of 乾|干[gān]",
            "(old) variant of 款[kuǎn]",
            "archaic variant of 蒸[zhēng]",
            "erhua variant of 一個勁|一个劲[yīgèjìn]",
            "erhua form of 今兒|今儿[jīnr]",
            "Japanese variant of 圓|圆",
            "see 基友[jīyǒu]",
            "see also 西皮[xīpí]",
            "used in 㐖毒[xiédú]",
        ],
    )
    def test_a_row_that_only_points_elsewhere_is_demoted(self, gloss):
        assert ZhDictKeyFolding().sense_rank(_cedict_row(gloss)) == 1

    @pytest.mark.parametrize(
        "gloss",
        [
            "dry",
            "to pay back; to return",
            "see you next time",
            "see through (a person, scheme, trick etc)",
            "used in place names",
            "abbr. for 三自愛國教會|三自爱国教会[sānzìàiguójiàohuì], Three-Self Patriotic Movement",
        ],
    )
    def test_a_row_that_states_a_sense_keeps_its_rank(self, gloss):
        assert ZhDictKeyFolding().sense_rank(_cedict_row(gloss)) == 0

    def test_a_surname_beside_a_real_sense_keeps_its_rank(self):
        assert ZhDictKeyFolding().sense_rank(_cedict_row("surname Wang", "king")) == 0

    def test_every_gloss_must_point_elsewhere(self):
        assert ZhDictKeyFolding().sense_rank(_cedict_row("variant of 乾|干[gān]", "surname Gan")) == 1

    def test_content_with_no_gloss_items_keeps_its_rank(self):
        """A dictionary this predicate cannot read is left where the index put it."""
        assert ZhDictKeyFolding().sense_rank("<div>bank</div>") == 0
