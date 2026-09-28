"""th lookup ladder rungs for mai yamok words."""

from __future__ import annotations

import pytest

from anki_miner.languages.th.support import ThaiLookupStrategy


@pytest.mark.parametrize("word", ["จริงๆ", "ช้าๆ", "บ่อยๆ", "ค่อยๆ", "ง่ายๆ", "เก่าๆ"])
def test_a_mai_yamok_token_looks_up_the_spaced_headword_then_its_base(word):
    # newmm emits จริงๆ as one token; wty-th-en spells the headword with the
    # Royal Institute space (จริง ๆ), and its base จริง is a headword too.
    base = word[:-1]
    assert ThaiLookupStrategy().candidates(word, word, None) == [(base + " ๆ", 0), (base, 0)]


def test_a_spaced_query_is_not_spaced_twice():
    assert ThaiLookupStrategy().candidates("จริง ๆ", "", None) == [("จริง", 0)]


def test_a_bare_mai_yamok_has_no_rung():
    assert ThaiLookupStrategy().candidates("ๆ", "", None) == []
