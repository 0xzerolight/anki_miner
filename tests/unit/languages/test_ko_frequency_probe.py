"""ko probe term tables — the fallback for a source that declares nothing."""

from __future__ import annotations

from anki_miner.services.frequency.mode_probe import (
    ASCENDING,
    DESCENDING,
    LESS_COMMON_TERMS,
    MORE_COMMON_TERMS,
    probe_direction,
)

_KO_MORE = MORE_COMMON_TERMS["ko"]
_KO_LESS = LESS_COMMON_TERMS["ko"]


def _is_hangul(term: str) -> bool:
    return all("가" <= ch <= "힣" for ch in term)


def test_ko_tables_are_registered_and_disjoint() -> None:
    # Predicates are listed at the lemma+다 granularity the ko miner produces
    # (languages/ko/morphology.py), which is also the NIKL headword granularity.
    assert {"하다", "있다", "되다", "없다", "같다", "보다"} <= set(_KO_MORE)
    assert {"노새", "자맥질", "여울", "두레박"} <= set(_KO_LESS)
    assert not set(_KO_MORE) & set(_KO_LESS)
    assert all(_is_hangul(t) for t in _KO_MORE + _KO_LESS)


def test_ko_terms_never_share_a_table_with_another_language() -> None:
    # ja and zh terms must never vote on a ko source, and vice versa.
    for table in (MORE_COMMON_TERMS, LESS_COMMON_TERMS):
        for code, terms in table.items():
            if code == "ko":
                continue
            assert not set(table["ko"]) & set(terms)


def test_ko_counts_probe_descending_and_ranks_ascending() -> None:
    counts = dict.fromkeys(_KO_MORE, (10_000,)) | dict.fromkeys(_KO_LESS, (5,))
    ranks = {t: (i + 1,) for i, t in enumerate(_KO_MORE)} | {t: (50_000 + i,) for i, t in enumerate(_KO_LESS)}
    assert probe_direction(lambda t: counts.get(t, ()), "ko") == DESCENDING
    assert probe_direction(lambda t: ranks.get(t, ()), "ko") == ASCENDING


def test_ko_terms_do_not_vote_in_the_ja_table() -> None:
    counts = dict.fromkeys(_KO_MORE, (10_000,)) | dict.fromkeys(_KO_LESS, (5,))
    assert probe_direction(lambda t: counts.get(t, ()), "ja") is None
