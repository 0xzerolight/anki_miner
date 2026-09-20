"""S16: a hand-imported Indonesian word-count list votes with Indonesian probe terms (hermitdave id_50k, plan D8)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

COMMON = ["aku", "kau", "yang", "tidak", "ini", "itu", "dan", "dia", "di", "akan"]  # id_50k ranks 1-10
#: id_50k ranks 3,164-27,336 (perpustakaan ... cendekiawan), all present in the list.
RARE = [
    "cendekiawan",
    "mercusuar",
    "landak",
    "kunang-kunang",
    "teropong",
    "gerhana",
    "kerajinan",
    "sekutu",
    "perpustakaan",
    "belalang",
]


def test_the_indonesian_tables():
    assert mode_probe.MORE_COMMON_TERMS["id"] == COMMON
    assert mode_probe.LESS_COMMON_TERMS["id"] == RARE
    assert len(set(COMMON)) == len(set(RARE)) == 10 and not set(COMMON) & set(RARE)


def test_an_occurrence_list_is_detected_from_indonesian_terms():
    counts = {(term, None): 2_000_000 - i for i, term in enumerate(COMMON)}
    counts.update({(term, None): 10 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "id")
    assert converted is True


def test_a_rank_list_is_detected_from_indonesian_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(COMMON)}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "id")
    assert converted is False
