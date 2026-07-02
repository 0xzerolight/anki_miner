"""Tests for the Yomitan deinflection engine port (services/deinflection.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.services.deinflection import (
    _MAX_RESULTS,
    Deinflector,
    build_condition_flags,
    conditions_match,
    find_highlight_end,
    get_japanese_deinflector,
)

# Synthetic mini-table exercising the engine mechanics without fugashi or
# the full Japanese rule table.
_CONDITIONS = {
    "a": {"isDictionaryForm": True},
    "b": {"isDictionaryForm": True},
    "p": {"isDictionaryForm": False, "subConditions": ["a", "b"]},
}

_TRANSFORMS = [
    {
        "id": "T1",
        "rules": [
            {"type": "suffix", "inflected": "xx", "deinflected": "y", "conditionsIn": [], "conditionsOut": ["a"]},
        ],
    },
    {
        "id": "T2",
        "rules": [
            {"type": "suffix", "inflected": "y", "deinflected": "z", "conditionsIn": ["a"], "conditionsOut": ["b"]},
        ],
    },
    {
        "id": "T3",
        "rules": [
            {"type": "suffix", "inflected": "y", "deinflected": "q", "conditionsIn": ["b"], "conditionsOut": ["a"]},
        ],
    },
]


def _make() -> Deinflector:
    return Deinflector(_CONDITIONS, _TRANSFORMS)


class TestConditionFlags:
    def test_leaves_get_distinct_bits_and_parent_ors_children(self):
        flags = build_condition_flags(_CONDITIONS)
        assert flags["a"] == 1
        assert flags["b"] == 2
        assert flags["p"] == flags["a"] | flags["b"]

    def test_more_than_32_leaf_conditions_raises(self):
        conditions = {f"c{i}": {"isDictionaryForm": True} for i in range(33)}
        with pytest.raises(ValueError):
            build_condition_flags(conditions)

    def test_unresolvable_subcondition_raises(self):
        conditions = {"p": {"isDictionaryForm": False, "subConditions": ["missing"]}}
        with pytest.raises(ValueError):
            build_condition_flags(conditions)

    def test_conditions_match_zero_is_wildcard(self):
        assert conditions_match(0, 0)
        assert conditions_match(0, 4)
        assert conditions_match(3, 2)
        assert not conditions_match(1, 2)


class TestTransformBFS:
    def test_identity_result_always_present(self):
        results = _make().transform("wxx")
        assert any(r.text == "wxx" and r.conditions == 0 for r in results)

    def test_chain_applies_rules_and_propagates_conditions(self):
        deinflector = _make()
        flags_a = deinflector.condition_flags("a")
        flags_b = deinflector.condition_flags("b")
        results = deinflector.transform("wxx")
        # T1: wxx -> wy [a]; then T2: wy -> wz [b]
        assert any(r.text == "wy" and r.conditions == flags_a for r in results)
        assert any(r.text == "wz" and r.conditions == flags_b for r in results)

    def test_conditions_gate_blocks_non_overlapping_rule(self):
        # Path wxx -> wy carries conditions [a]; T3 requires [b] so wq must
        # not be reachable from wxx...
        results = _make().transform("wxx")
        assert not any(r.text == "wq" for r in results)
        # ...but from the wildcard seed wy (conditions 0) T3 applies.
        results = _make().transform("wy")
        assert any(r.text == "wq" for r in results)

    def test_cycle_detection_terminates(self):
        transforms = [
            {
                "id": "SELF",
                "rules": [
                    {
                        "type": "suffix",
                        "inflected": "y",
                        "deinflected": "y",
                        "conditionsIn": [],
                        "conditionsOut": ["a"],
                    },
                ],
            },
        ]
        deinflector = Deinflector(_CONDITIONS, transforms)
        results = deinflector.transform("wy")
        # Identity + exactly one application; the second is the same
        # (transform, rule, text) frame and is skipped as a cycle.
        assert len(results) == 2

    def test_whole_word_rule_matches_exact_text_only(self):
        transforms = [
            {
                "id": "WW",
                "rules": [
                    {
                        "type": "wholeWord",
                        "inflected": "abc",
                        "deinflected": "d",
                        "conditionsIn": [],
                        "conditionsOut": ["a"],
                    },
                ],
            },
        ]
        deinflector = Deinflector(_CONDITIONS, transforms)
        assert any(r.text == "d" for r in deinflector.transform("abc"))
        assert not any(r.text == "d" for r in deinflector.transform("xabc"))

    def test_long_chain_stays_far_below_backstop(self):
        # Ten chained steps A->B->...->K with disjoint suffixes: linear
        # growth, nowhere near the backstop.
        transforms = [
            {
                "id": f"STEP{i}",
                "rules": [
                    {
                        "type": "suffix",
                        "inflected": chr(ord("A") + i),
                        "deinflected": chr(ord("A") + i + 1),
                        # First step matches via the conditions==0 wildcard
                        # seed; later steps chain on the "a" overlap.
                        "conditionsIn": ["a"],
                        "conditionsOut": ["a"],
                    },
                ],
            }
            for i in range(10)
        ]
        deinflector = Deinflector(_CONDITIONS, transforms)
        results = deinflector.transform("wA")
        assert any(r.text == "wK" for r in results)
        assert len(results) < _MAX_RESULTS / 4

    def test_backstop_caps_runaway_table(self):
        # Digit-rewriting rules re-enter each other on every new text
        # (never the same trace frame), so only the backstop terminates
        # the BFS: it must stop expanding gracefully, not raise.
        transforms = [
            {
                "id": f"STEP{i}",
                "rules": [
                    {
                        "type": "suffix",
                        "inflected": str(i),
                        "deinflected": str(i + 1),
                        "conditionsIn": ["a"],
                        "conditionsOut": ["a"],
                    },
                ],
            }
            for i in range(10)
        ]
        deinflector = Deinflector(_CONDITIONS, transforms)
        results = deinflector.transform("w0")
        assert len(results) <= _MAX_RESULTS + 1

    def test_empty_inflected_form_rejected_at_build(self):
        transforms = [
            {
                "id": "BAD",
                "rules": [
                    {"type": "suffix", "inflected": "", "deinflected": "x", "conditionsIn": [], "conditionsOut": ["a"]},
                ],
            },
        ]
        with pytest.raises(ValueError):
            Deinflector(_CONDITIONS, transforms)

    def test_bucketing_matches_brute_force(self):
        deinflector = _make()
        all_rules = [(t["id"], i, r) for t in _TRANSFORMS for i, r in enumerate(t["rules"])]

        def brute_force(source: str) -> set[tuple[str, int]]:
            # Naive re-implementation without last-char bucketing.
            flags = build_condition_flags(_CONDITIONS)

            def mask(names):
                out = 0
                for n in names:
                    out |= flags[n]
                return out

            results: list[tuple[str, int, tuple]] = [(source, 0, ())]
            index = 0
            while index < len(results):
                text, conds, trace = results[index]
                index += 1
                if not text:
                    continue
                for tid, ridx, raw in all_rules:
                    if conds != 0 and not (conds & mask(raw["conditionsIn"])):
                        continue
                    if raw["type"] == "suffix":
                        if not text.endswith(raw["inflected"]):
                            continue
                        new_text = text[: len(text) - len(raw["inflected"])] + raw["deinflected"]
                    else:
                        if text != raw["inflected"]:
                            continue
                        new_text = raw["deinflected"]
                    frame = (tid, ridx, text)
                    if frame in trace:
                        continue
                    results.append((new_text, mask(raw["conditionsOut"]), trace + (frame,)))
            return {(text, conds) for text, conds, _ in results}

        for source in ("wxx", "wy", "xxy", "zzz", "xxxx"):
            engine = {(r.text, r.conditions) for r in deinflector.transform(source)}
            assert engine == brute_force(source), source


class TestMaskForCtype:
    def test_known_ctype_prefixes_map_to_condition_flags(self):
        conditions = {
            "v1": {"isDictionaryForm": True},
            "v5": {"isDictionaryForm": True},
            "vs": {"isDictionaryForm": True},
            "vk": {"isDictionaryForm": True},
            "vz": {"isDictionaryForm": True},
            "adj-i": {"isDictionaryForm": True},
        }
        deinflector = Deinflector(conditions, [])
        assert deinflector.mask_for_ctype("五段-カ行") == deinflector.condition_flags("v5")
        assert deinflector.mask_for_ctype("上一段-ア行") == deinflector.condition_flags("v1")
        assert deinflector.mask_for_ctype("下一段-バ行") == deinflector.condition_flags("v1")
        assert deinflector.mask_for_ctype("サ行変格") == deinflector.condition_flags("vs")
        assert deinflector.mask_for_ctype("カ行変格") == deinflector.condition_flags("vk")
        assert deinflector.mask_for_ctype("形容詞") == deinflector.condition_flags("adj-i")

    def test_unknown_or_missing_ctype_returns_zero(self):
        deinflector = _make()
        assert deinflector.mask_for_ctype("助動詞-タ") == 0
        assert deinflector.mask_for_ctype(None) == 0

    def test_magicmock_ctype_treated_as_absent(self):
        # MagicMock auto-vivifies truthy attributes; isinstance(str) must gate.
        deinflector = _make()
        assert deinflector.mask_for_ctype(MagicMock()) == 0


class TestJapaneseTableIntegrity:
    """Guards the mechanically generated table against transcription drift.

    Counts pinned to upstream commit e2ed450c2f11a591922822e77f008e70a87daf0c
    (materialized rules after generator expansion, never literal call sites).
    """

    def test_materialized_counts_match_pinned_upstream(self):
        from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS

        deinflector = get_japanese_deinflector()
        assert deinflector.transform_count == 54
        assert deinflector.rule_count == 834
        assert len(CONDITIONS) == 22
        whole_word = [r for t in TRANSFORMS for r in t["rules"] if r["type"] == "wholeWord"]
        # The special-honorific -masu helper generates exactly these.
        assert len(whole_word) == 8

    def test_all_suffixes_are_literal_nonempty_strings(self):
        from anki_miner.services.japanese_transforms import TRANSFORMS

        regex_meta = set("\\^$.|?*+()[]{}")
        for transform in TRANSFORMS:
            for rule in transform["rules"]:
                assert rule["inflected"], transform["id"]
                assert not (set(rule["inflected"]) & regex_meta), rule["inflected"]

    def test_generator_helper_rules_present(self):
        from anki_miner.services.japanese_transforms import TRANSFORMS

        rules = {(t["id"], r["inflected"], r["deinflected"]) for t in TRANSFORMS for r in t["rules"]}
        # irregularVerbSuffixInflections outputs (iku/godan-u-special/fu-verb).
        assert ("-た", "行った", "行く") in rules
        assert ("-た", "問うた", "問う") in rules
        # specialHonorificMasuInflections outputs (whole-word).
        assert ("-ます", "いらっしゃいます", "いらっしゃる") in rules
        assert ("-ます", "くださいます", "くださる") in rules


def _reaches(candidate: str, target: str, mask: int) -> bool:
    deinflector = get_japanese_deinflector()
    return any(r.text == target and (mask == 0 or (r.conditions & mask) != 0) for r in deinflector.transform(candidate))


class TestJapaneseChainVectors:
    """Chain vectors verified against the real upstream engine at the pinned
    commit (differential run: identical result sets on all of these)."""

    @pytest.mark.parametrize(
        ("candidate", "target", "condition"),
        [
            ("蒔いた", "蒔く", "v5"),
            ("食べた", "食べる", "v1"),
            ("泳いで", "泳ぐ", "v5"),
            ("買って", "買う", "v5"),
            ("死んだ", "死ぬ", "v5"),
            ("高かった", "高い", "adj-i"),
            ("高くなかった", "高い", "adj-i"),
            ("勉強しました", "勉強する", "vs"),
            ("来た", "来る", "vk"),
            # Upstream reaches 来る via BOTH the generic (v1|v5) path and the
            # explicit kanji-来 vk rule; acceptance needs only the vk hit.
            ("来なかった", "来る", "vk"),
            ("した", "する", "vs"),
            ("食べています", "食べる", "v1"),
            ("泳いでいた", "泳ぐ", "v5"),
            ("行かなかった", "行く", "v5"),
            ("蒔いたら", "蒔く", "v5"),
            ("行ったり", "行く", "v5"),
            ("行きませんでした", "行く", "v5"),
            ("食べさせられたくなかった", "食べる", "v1"),
            ("行こう", "行く", "v5"),
            ("食べろ", "食べる", "v1"),
            ("食べちゃった", "食べる", "v1"),
            ("食べてしまった", "食べる", "v1"),
            ("食べておく", "食べる", "v1"),
            # Irregular-helper vectors.
            ("行った", "行く", "v5"),
            ("問うた", "問う", "v5"),
            ("いらっしゃいます", "いらっしゃる", "v5"),
            ("くださいます", "くださる", "v5"),
            # Kanji-orthBase words deinflect kanji-inclusive: they DO extend.
            ("無かった", "無い", "adj-i"),
            ("居なかった", "居る", "v1"),
            ("判った", "判る", "v5"),
        ],
    )
    def test_chain_reaches_target_under_mask(self, candidate, target, condition):
        deinflector = get_japanese_deinflector()
        mask = deinflector.condition_flags(condition)
        assert mask != 0
        assert _reaches(candidate, target, mask)

    @pytest.mark.parametrize(
        ("candidate", "target"),
        [
            # Upstream has no てみる/ていく/てくる or benefactive rules: the
            # lemma is NOT reachable from the full complex (span stops at the
            # shorter て-form candidate instead).
            ("見てみる", "見る"),
            ("食べていく", "食べる"),
            ("食べてくる", "食べる"),
            ("買ってくれた", "買う"),
        ],
    )
    def test_non_rule_auxiliaries_do_not_reach_target(self, candidate, target):
        assert not _reaches(candidate, target, 0)

    def test_ctype_mask_rejects_cross_conjugation_coincidence(self):
        # した reaches する only with vs conditions; a v5 mask (as if the
        # mined token were a godan verb) must reject that chain.
        deinflector = get_japanese_deinflector()
        assert not _reaches("した", "する", deinflector.condition_flags("v5"))

    def test_long_chain_stays_under_backstop_on_real_table(self):
        deinflector = get_japanese_deinflector()
        results = deinflector.transform("食べさせられたくなかった")
        assert len(results) < _MAX_RESULTS / 4


def _tok(surface, pos1=None, lemma=None, orth_base=None, ctype=None):
    return SimpleNamespace(
        surface=surface,
        feature=SimpleNamespace(pos1=pos1, lemma=lemma, orthBase=orth_base, cType=ctype),
    )


class TestFindHighlightEnd:
    def test_verb_extends_over_auxiliary(self):
        text = "種を蒔いた"
        tokens = [
            _tok("種", "名詞"),
            _tok("を", "助詞"),
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 2, 4, tokens[2]) == 5

    def test_window_stops_at_punctuation(self):
        text = "種を蒔いた。"
        tokens = [
            _tok("種", "名詞"),
            _tok("を", "助詞"),
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("た", "助動詞"),
            _tok("。", "補助記号"),
        ]
        assert find_highlight_end(text, tokens, 2, 4, tokens[2]) == 5

    def test_window_stops_at_whitespace_gap(self):
        text = "蒔い た"
        tokens = [
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 2

    def test_window_stops_at_noun_tail(self):
        # 食べたことがある: こと is a pure-hiragana 名詞 — the POS stop must
        # end the window after た.
        text = "食べたことがある"
        tokens = [
            _tok("食べ", "動詞", lemma="食べる", orth_base="食べる", ctype="下一段-バ行"),
            _tok("た", "助動詞"),
            _tok("こと", "名詞"),
            _tok("が", "助詞"),
            _tok("ある", "動詞", lemma="有る"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 3

    def test_window_cap_thirteen_chars(self):
        text = "蒔い" + "た" * 20
        tokens = [_tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行")]
        tokens += [_tok("た", "助動詞") for _ in range(20)]
        # The candidate walk is capped at tok_end + 13; among the bounded
        # candidates only 蒔いた (the first た) deinflects to 蒔く, so the
        # longest VALID candidate wins — no crash, no overrun, no
        # over-extension into the たた... run.
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 3

    def test_no_chain_falls_back_to_tok_end(self):
        text = "蒔いよ"
        tokens = [
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("よ", "助詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 2

    def test_orthbase_divergence_extends_via_orthbase(self):
        # 判った: unidic lemma is 分かる (different orthography); orthBase
        # 判る is the reachable target.
        text = "判った"
        tokens = [
            _tok("判っ", "動詞", lemma="分かる", orth_base="判る", ctype="五段-ラ行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 3

    def test_kanji_orthbase_adjective_extends(self):
        # Kanji-written 無かった extends: the chain deinflects the
        # kanji-inclusive surface to 無い (== orthBase).
        text = "無かった"
        tokens = [
            _tok("無かっ", "形容詞", lemma="無い", orth_base="無い", ctype="形容詞"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 3, tokens[0]) == 4

    def test_full_aux_chain_teiru_extends_fully(self):
        text = "海で泳いでいた"
        tokens = [
            _tok("海", "名詞"),
            _tok("で", "助詞"),
            _tok("泳い", "動詞", lemma="泳ぐ", orth_base="泳ぐ", ctype="五段-ガ行"),
            _tok("で", "助詞"),
            _tok("い", "動詞", lemma="居る", orth_base="いる", ctype="上一段-ア行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 2, 4, tokens[2]) == 7

    def test_benefactive_stops_at_te_form(self):
        # No てくれる rule upstream: longest valid candidate is 買って.
        text = "買ってくれた"
        tokens = [
            _tok("買っ", "動詞", lemma="買う", orth_base="買う", ctype="五段-ワア行"),
            _tok("て", "助詞"),
            _tok("くれ", "動詞", lemma="くれる", orth_base="くれる", ctype="下一段-ラ行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 3

    def test_non_verb_pos_short_circuits(self):
        text = "刑務所だ"
        tokens = [_tok("刑務所", "名詞"), _tok("だ", "助動詞")]
        assert find_highlight_end(text, tokens, 0, 3, tokens[0]) == 3

    def test_invalid_offsets_fall_back(self):
        token = _tok("蒔い", "動詞", lemma="蒔く")
        assert find_highlight_end("蒔いた", [token], 4, 2, token) == 2

    def test_magicmock_multi_token_line_falls_back(self):
        # First token carries real strings; the tail token is a bare
        # MagicMock whose surface auto-vivifies — the candidate walk must
        # degrade to tok_end, not raise or extend.
        verb = _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行")
        assert find_highlight_end("蒔いた", [verb, MagicMock()], 0, 2, verb) == 2

    def test_magicmock_token_features_fall_back(self):
        # Fully mocked mined token: pos1 is a Mock -> immediate short-circuit.
        text = "蒔いた"
        assert find_highlight_end(text, [MagicMock()], 0, 2, MagicMock()) == 2
