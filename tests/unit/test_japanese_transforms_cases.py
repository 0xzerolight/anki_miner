"""Table-driven deinflection spec ported from Yomitan.

Runs the ~1300-case fixture in ``tests/unit/data/japanese_transforms_cases.py``
against the real ported engine + Japanese rule table, mirroring upstream's
``testLanguageTransformer`` harness: valid categories require the term be
reachable with the exact condition and reversed-reason chain; ``valid: False``
categories require it be UNreachable (incorrect chains, short-causative→passive
blocks). One parametrized node per category keeps collection cheap.
"""

from __future__ import annotations

import pytest

from anki_miner.services.deinflection import (
    Deinflector,
    conditions_match,
    get_japanese_deinflector,
)
from tests.unit.data.japanese_transforms_cases import CATEGORIES


def has_term_reasons(
    deinflector: Deinflector,
    source: str,
    expected_term: str,
    expected_condition_name: str | None,
    expected_reasons: list[str] | None,
) -> bool:
    """Port of ``hasTermReasons`` in Yomitan
    ``test/fixtures/language-transformer-test.js`` (commit e2ed450).

    True iff some ``transform(source)`` result reaches ``expected_term`` while
    (when given) its conditions match ``expected_condition_name``'s flags and
    its reason chain equals ``expected_reasons``. Upstream builds the trace
    newest-first; this port appends oldest-first, so the trace is reversed
    before comparison to recover upstream reason order.
    """
    for result in deinflector.transform(source):
        if result.text != expected_term:
            continue
        if expected_condition_name is not None:
            expected_flags = deinflector.condition_flags(expected_condition_name)
            if not conditions_match(result.conditions, expected_flags):
                continue
        if expected_reasons is not None:
            actual_reasons = [frame[0] for frame in reversed(result.trace)]
            if actual_reasons != expected_reasons:
                continue
        return True
    return False


def _describe(case: dict) -> str:
    parts = [f'{case["source"]} -> {case["term"]!r}']
    if case["rule"] is not None:
        parts.append(f'rule={case["rule"]!r}')
    if case["reasons"] is not None:
        parts.append(f'reasons={case["reasons"]!r}')
    return " ".join(parts)


@pytest.mark.parametrize(
    "category",
    CATEGORIES,
    ids=[f'{i}:{c["category"]}' for i, c in enumerate(CATEGORIES)],
)
def test_deinflection_category(category: dict) -> None:
    deinflector = get_japanese_deinflector()
    valid = category["valid"]
    failures = []
    for case in category["tests"]:
        has = has_term_reasons(
            deinflector,
            case["source"],
            case["term"],
            case["rule"],
            case["reasons"],
        )
        if has is not valid:
            verb = "did not reach" if valid else "unexpectedly reached"
            failures.append(f"{verb}: {_describe(case)}")
    message = (
        f'category {category["category"]!r} (valid={valid}): '
        f"{len(failures)} case(s) failed:\n  " + "\n  ".join(failures)
    )
    assert not failures, message


def test_case_table_shape() -> None:
    """Guard the fixture: nonempty, well-formed, and the expected census."""
    assert len(CATEGORIES) == 30
    assert sum(len(c["tests"]) for c in CATEGORIES) == 1314
    for category in CATEGORIES:
        assert isinstance(category["valid"], bool)
        assert category["tests"], f'empty category {category["category"]!r}'
        for case in category["tests"]:
            assert case.keys() == {"term", "source", "rule", "reasons"}
            assert isinstance(case["term"], str) and case["term"]
            assert isinstance(case["source"], str) and case["source"]
            assert case["rule"] is None or isinstance(case["rule"], str)
            assert case["reasons"] is None or isinstance(case["reasons"], list)
