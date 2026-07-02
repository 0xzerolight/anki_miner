"""Tests for the Yomitan deinflection engine port (services/deinflection.py)."""

from unittest.mock import MagicMock

import pytest

from anki_miner.services.deinflection import (
    _MAX_RESULTS,
    Deinflector,
    build_condition_flags,
    conditions_match,
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
