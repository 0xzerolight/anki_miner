"""S16: hermitdave's Slovenian word-count list is recognised as occurrence counts by its own sl terms."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

#: Ranks in hermitdave FrequencyWords content/2018/sl/sl_50k.txt (619,289 B, 100,952,811 occurrences).
COMMON_RANKS = {"je": 1, "ne": 2, "da": 3, "se": 4, "v": 5, "sem": 6, "to": 7, "in": 8, "si": 9, "kaj": 10}
RARE_RANKS = {
    "sidro": 15108, "daljnogled": 17794, "teleskop": 18428, "svetilnik": 18979, "panj": 19531,
    "glavnik": 21802, "kanu": 24497, "lopata": 35258, "jazbec": 40658, "mrož": 42113,
}  # fmt: skip


def test_the_tables_are_the_measured_terms():
    assert mode_probe.MORE_COMMON_TERMS["sl"] == list(COMMON_RANKS)
    assert sorted(mode_probe.LESS_COMMON_TERMS["sl"]) == sorted(RARE_RANKS)
    assert all(term == term.lower() for term in COMMON_RANKS | RARE_RANKS)
    assert not set(COMMON_RANKS) & set(RARE_RANKS)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 4_300_000 - rank for term, rank in COMMON_RANKS.items()}
    counts.update({(term, None): 900 - rank // 100 for term, rank in RARE_RANKS.items()})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "sl")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): rank for term, rank in (COMMON_RANKS | RARE_RANKS).items()}
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "sl")
    assert converted is False


def test_without_the_row_an_occurrence_list_falls_to_rank_based():
    """What the row fixes: with no sl terms nothing votes, so je (4,281,560 occurrences) would be
    stored as rank 4,281,560 and the catalogue's lemmatise=True would never run."""
    counts = {(term, None): 4_300_000 - rank for term, rank in COMMON_RANKS.items()}
    _rows, converted = source_importer._iter_rank_rows(counts, "", "xx")
    assert converted is False
