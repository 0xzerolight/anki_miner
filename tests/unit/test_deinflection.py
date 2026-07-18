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
    find_highlight_end_with_trace,
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
        # サ行変格 satisfies BOTH vs and vz: unidic tags じる/ずる verbs (感じる,
        # 信じる) as サ行変格 while the transform rules to 〜ずる carry vz.
        assert deinflector.mask_for_ctype("サ行変格") == (
            deinflector.condition_flags("vs") | deinflector.condition_flags("vz")
        )
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
        assert deinflector.rule_count == 833
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
    def test_jiru_zuru_verb_extends_full_span(self):
        # 感じた: unidic surface 感じ / cType サ行変格 / orthBase 感ずる. The
        # transform chain 感じた→感ずる carries vz, so the サ行変格 mask must
        # accept vz or the highlight stops at the 感じ stem (Bug J1).
        text = "感じた"
        tokens = [
            _tok("感じ", "動詞", lemma="感ずる", orth_base="感ずる", ctype="サ行変格"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end(text, tokens, 0, 2, tokens[0]) == 3

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


class TestFindHighlightEndWithTrace:
    """The chain returned alongside the end offset is in Yomitan attachment
    order (dictionary form outward), matching japanese-transforms.test.js."""

    def test_single_past_transform(self):
        text = "種を蒔いた"
        tokens = [
            _tok("種", "名詞"),
            _tok("を", "助詞"),
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("た", "助動詞"),
        ]
        assert find_highlight_end_with_trace(text, tokens, 2, 4, tokens[2]) == (5, ("-た",))

    def test_te_iru_past_chain_reads_dict_form_outward(self):
        text = "海で泳いでいた"
        tokens = [
            _tok("海", "名詞"),
            _tok("で", "助詞"),
            _tok("泳い", "動詞", lemma="泳ぐ", orth_base="泳ぐ", ctype="五段-ガ行"),
            _tok("で", "助詞"),
            _tok("い", "動詞", lemma="居る", orth_base="いる", ctype="上一段-ア行"),
            _tok("た", "助動詞"),
        ]
        end, chain = find_highlight_end_with_trace(text, tokens, 2, 4, tokens[2])
        assert end == 7
        assert chain == ("-て", "-いる", "-た")

    def test_no_extension_yields_empty_chain(self):
        # 蒔いよ: よ does not deinflect to 蒔く, so no rightward extension is
        # accepted and the chain is empty.
        text = "蒔いよ"
        tokens = [
            _tok("蒔い", "動詞", lemma="蒔く", orth_base="蒔く", ctype="五段-カ行"),
            _tok("よ", "助詞"),
        ]
        assert find_highlight_end_with_trace(text, tokens, 0, 2, tokens[0]) == (2, ())

    def test_non_verb_short_circuits_to_empty_chain(self):
        text = "刑務所だ"
        tokens = [_tok("刑務所", "名詞"), _tok("だ", "助動詞")]
        assert find_highlight_end_with_trace(text, tokens, 0, 3, tokens[0]) == (3, ())


class TestCommonPrefixLen:
    """``common_prefix_len`` — leading shared-character count."""

    def test_no_shared_prefix(self):
        from anki_miner.services.deinflection import common_prefix_len

        assert common_prefix_len("感じる", "泳ぐ") == 0

    def test_partial_shared_prefix(self):
        from anki_miner.services.deinflection import common_prefix_len

        # 感じる vs 感じた share the 感じ stem (2 chars) then diverge (る/た).
        assert common_prefix_len("感じる", "感じた") == 2
        # 感ずる vs 感じた share only 感.
        assert common_prefix_len("感ずる", "感じた") == 1

    def test_one_is_prefix_of_other(self):
        from anki_miner.services.deinflection import common_prefix_len

        assert common_prefix_len("感じ", "感じる") == 2
        assert common_prefix_len("感じる", "感じ") == 2

    def test_empty_string(self):
        from anki_miner.services.deinflection import common_prefix_len

        assert common_prefix_len("", "感じる") == 0
        assert common_prefix_len("感じる", "") == 0


def _lookup(*attested: str):
    """Fake offline term_lookup: attests exactly the given headwords."""
    attested_set = set(attested)
    return lambda terms: {t for t in terms if t in attested_set}


def _common(*common: str):
    """Fake term_common_lookup: reports exactly the given headwords common.

    Returns ``{term: bool}`` for the queried terms (an aware chain). Pass no args
    to mark every term uncommon; pass ``None`` directly to the resolver to model
    an UNAWARE chain (no commonness dict) — a distinct, byte-identical degrade.
    """
    common_set = set(common)
    return lambda terms: {t: t in common_set for t in terms}


class TestResolveDictionaryForm:
    """``resolve_dictionary_form`` — JMdict-anchored modern verb/adjective front.

    Drives the REAL deinflection table for candidate generation; the
    ``term_lookup`` is faked so attestation is deterministic and dict-free.
    """

    def _resolve(self, inflected_surface, orth_base, term_lookup, term_common_lookup=None):
        from anki_miner.services.deinflection import resolve_dictionary_form

        return resolve_dictionary_form(inflected_surface, orth_base, term_lookup, term_common_lookup)

    # --- Produces the modern じる form (asserts PRODUCED, not merely no-op). ---

    def test_kanjita_resolves_to_modern_jiru(self):
        # 感じた: orthBase 感ずる (archaic サ変). transform(感じた) yields 感じる
        # (v1) unmasked; it shares 感じ (prefix 2) with the surface, beating
        # 感ずる (prefix 1) → override.
        assert self._resolve("感じた", "感ずる", _lookup("感じる", "感ずる")) == "感じる"

    def test_ronjita_resolves_to_modern_jiru(self):
        assert self._resolve("論じた", "論ずる", _lookup("論じる", "論ずる")) == "論じる"

    def test_shinji_stem_resolves_to_modern_jiru(self):
        # 信じられない tokenizes to a bare 信じ verb stem (no rightward extension
        # is a valid candidate here); transform(信じ) reaches 信じる.
        assert self._resolve("信じ", "信ずる", _lookup("信じる", "信ずる")) == "信じる"

    def test_shojita_resolves_to_modern_jiru(self):
        assert self._resolve("生じた", "生ずる", _lookup("生じる", "生ずる")) == "生じる"

    # --- Existence gate, never entries.score; orth_base need not be attested. ---

    def test_override_when_only_modern_form_attested(self):
        # Existence gate: even if 感ずる is NOT attested, the attested 感じる
        # still wins the strictly-greater override.
        assert self._resolve("感じた", "感ずる", _lookup("感じる")) == "感じる"

    # --- Identity (the inflected surface itself) is excluded from candidates. ---

    def test_attested_inflected_surface_does_not_win(self):
        # 待った IS an attested JMdict headword ("matta!"), but it is the
        # inflected surface itself — excluding it lets 待つ win, so the card
        # front is the dictionary form, not the inflected string.
        assert self._resolve("待った", "待つ", _lookup("待つ", "待った")) == "待つ"

    def test_attested_te_form_surface_does_not_win(self):
        # 通じて is attested but is the surface identity; 通じる wins.
        assert self._resolve("通じて", "通ずる", _lookup("通じる", "通じて")) == "通じる"

    # --- Regression guards: orthBase already the longest prefix → no override. ---

    def test_kou_kept_when_orthbase_is_longest_prefix(self):
        assert self._resolve("乞うた", "乞う", _lookup("乞う")) == "乞う"

    def test_samayotta_kept(self):
        assert self._resolve("彷徨った", "彷徨う", _lookup("彷徨う")) == "彷徨う"

    def test_no_override_when_winner_prefix_not_strictly_greater(self):
        # 立った → 立つ: the attested 立つ shares only 立 (prefix 1) with the
        # surface, exactly as orthBase 立つ does — not STRICTLY greater, so the
        # orthBase is kept unchanged (it is already correct).
        assert self._resolve("立った", "立つ", _lookup("立つ")) == "立つ"

    # --- Safe degrade. ---

    def test_no_lookup_returns_orth_base_unchanged(self):
        assert self._resolve("感じた", "感ずる", None) == "感ずる"

    def test_no_attestation_returns_orth_base_unchanged(self):
        assert self._resolve("感じた", "感ずる", _lookup()) == "感ずる"

    def test_empty_inputs_return_orth_base(self):
        assert self._resolve("", "感ずる", _lookup("感じる")) == "感ずる"
        assert self._resolve("感じた", "", _lookup("感じる")) == ""

    # --- Commonness filter (U11): the override pool is narrowed to common heads. ---

    def test_commonness_filter_drops_rare_longer_prefix(self):
        # 呼ばれる deinflects to BOTH 呼ぶ and the classical 呼ばる (both attested).
        # 呼ばる shares the longer prefix and would override to junk; tagging only
        # 呼ぶ common drops 呼ばる from the pool → orthBase 呼ぶ is kept.
        assert self._resolve("呼ばれる", "呼ぶ", _lookup("呼ぶ", "呼ばる"), _common("呼ぶ")) == "呼ぶ"

    def test_commonness_filter_tataseru_keeps_tatsu(self):
        # 立たせる: 立たす (uncommon) would override; only 立つ is common → kept.
        assert self._resolve("立たせる", "立つ", _lookup("立つ", "立たす"), _common("立つ")) == "立つ"

    def test_commonness_filter_ike_keeps_iku(self):
        # 行け: 行ける (uncommon, longer prefix) would override; 行く common → kept.
        assert self._resolve("行け", "行く", _lookup("行く", "行ける"), _common("行く")) == "行く"

    def test_commonness_none_probe_is_byte_identical(self):
        # term_common_lookup=None (no aware dict) → the full attested pool, so the
        # rare 呼ばる wins the strictly-greater override exactly as pre-U11 (the
        # degrade under an unaware/absent commonness dict).
        assert self._resolve("呼ばれる", "呼ぶ", _lookup("呼ぶ", "呼ばる"), None) == "呼ばる"

    def test_commonness_unaware_lookup_degrades_like_none(self):
        # A probe that returns None (chain has no commonness-aware dict) degrades
        # identically to passing None — the junk longer-prefix form comes back.
        assert self._resolve("呼ばれる", "呼ぶ", _lookup("呼ぶ", "呼ばる"), lambda terms: None) == "呼ばる"

    def test_commonness_all_uncommon_falls_back_to_full_pool(self):
        # 詠じた → 詠じる (modern) is the only attested candidate but is NOT tagged
        # common. The pool must NOT empty: it falls back to the full attested set
        # so the rare-but-correct 詠じる still overrides the archaic 詠ずる orthBase.
        assert self._resolve("詠じた", "詠ずる", _lookup("詠じる"), _common()) == "詠じる"

    def test_commonness_filter_keeps_common_jiru_override(self):
        # Contract preserved: 感じて → 感じる when 感じる IS common (the じる/ずる
        # fix the filter must not break).
        assert self._resolve("感じて", "感ずる", _lookup("感じる", "感ずる"), _common("感じる")) == "感じる"


class TestConditionFlagsFromRules:
    """``condition_flags_from_rules`` — Yomitan POS-check flag mapping."""

    def test_empty_string_is_zero(self):
        from anki_miner.services.deinflection import condition_flags_from_rules

        assert condition_flags_from_rules("") == 0

    def test_single_known_name(self):
        from anki_miner.services.deinflection import condition_flags_from_rules, get_japanese_deinflector

        d = get_japanese_deinflector()
        assert condition_flags_from_rules("v1") == d.condition_flags("v1")

    def test_multiple_names_are_ored(self):
        from anki_miner.services.deinflection import condition_flags_from_rules, get_japanese_deinflector

        d = get_japanese_deinflector()
        assert condition_flags_from_rules("v5 adj-i") == (d.condition_flags("v5") | d.condition_flags("adj-i"))

    def test_unknown_name_contributes_zero(self):
        from anki_miner.services.deinflection import condition_flags_from_rules, get_japanese_deinflector

        d = get_japanese_deinflector()
        # "n"/"vt" are POS tags with no deinflection condition → 0, so only v1 counts.
        assert condition_flags_from_rules("n v1 vt") == d.condition_flags("v1")
