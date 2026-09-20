"""The Indonesian deinflection ladder (spec C.5 rule table): one case per row group, the order and the cap."""

from __future__ import annotations

import pytest

from anki_miner.languages.id.colloquial import ID_COLLOQUIAL
from anki_miner.languages.id.rules import (
    MAX_CANDIDATES,
    active_form,
    deinflection_candidates,
    reduplication_candidates,
    vowel_variants,
)


def ladder(word: str) -> list[str]:
    return deinflection_candidates(word, ID_COLLOQUIAL.get)


def first_hit(word: str, headwords: set[str]) -> str | None:
    """What ``DefinitionService`` keeps: the first candidate the dictionary knows."""
    return next((candidate for candidate in ladder(word) if candidate in headwords), None)


#: (word, a candidate the ladder must offer), one or more per row group; the Sastrawi-verified
#: expectations of spec C.5 plus the colloquial layer Sastrawi leaves untouched.
CASES = [
    # N0 colloquial table
    ("beliin", "membelikan"), ("gimana", "bagaimana"), ("pake", "memakai"),
    # R1-R3 reduplication
    ("buku-buku", "buku"), ("orang-orangan", "orang"), ("anak-anaknya", "anak"), ("berlari-lari", "lari"),
    ("sebaik-baiknya", "baik"), ("buku2", "buku-buku"), ("buku2nya", "buku"),
    # S1 particles, S2 clitics
    ("bukunya", "buku"), ("bukankah", "bukan"), ("rumahku", "rumah"), ("pergilah", "pergi"),
    # S3 suffixes
    ("belikan", "beli"), ("tulisan", "tulis"), ("datangi", "datang"),
    # P1-P11 prefixes with nasal recovery
    ("membeli", "beli"), ("mengambil", "ambil"), ("mengirim", "kirim"), ("menulis", "tulis"), ("mencari", "cari"),
    ("menyapu", "sapu"), ("memukul", "pukul"), ("melihat", "lihat"), ("mengecat", "cat"), ("mengetik", "ketik"),
    ("pembeli", "beli"), ("penulis", "tulis"), ("penyanyi", "nyanyi"), ("berjalan", "jalan"), ("bekerja", "kerja"),
    ("belajar", "ajar"), ("terbeli", "beli"),
    # P12-P15 di- ku- ke- se-: a passive first offers its active form; a glued preposition its noun
    ("dibeli", "membeli"), ("kubeli", "membeli"), ("dirumah", "rumah"),
    # confixes
    ("pembelian", "beli"), ("kerusakan", "rusak"), ("keadaban", "adab"), ("perjalanan", "jalan"),
    # C1-C5 colloquial prefixes, ke- for ter-
    ("ngambil", "ambil"), ("ngirim", "kirim"), ("nyari", "cari"), ("nulis", "tulis"), ("mukul", "pukul"),
    ("ngecat", "cat"), ("kejepit", "terjepit"),
    # V1-V2 vowel variants
    ("bener", "benar"), ("simpen", "simpan"), ("pake", "pakai"), ("kalo", "kalau"), ("abis", "habis"),
    ("tau", "tahu"), ("liat", "lihat"),
]  # fmt: skip


@pytest.mark.parametrize(("word", "expected"), CASES)
def test_every_row_group_offers_its_expected_candidate(word, expected):
    candidates = ladder(word)
    assert expected in candidates
    assert word not in candidates and len(candidates) == len(set(candidates)) <= MAX_CANDIDATES


@pytest.mark.parametrize(
    ("word", "headwords", "hit"),
    [
        ("mengertilah", {"mengerti", "erti"}, "mengerti"),  # never stem past the Wiktionary lemma
        ("membelikannya", {"membelikan", "membeli", "beli", "ikan"}, "membelikan"),
        ("dibeli", {"membeli", "beli"}, "membeli"),
        ("beliin", {"membelikan", "belikan", "beli"}, "membelikan"),
        ("kerusakan", {"rusa", "rusak"}, "rusak"),
        ("dirumah", {"rumah"}, "rumah"),
        ("bukunya", {"buku"}, "buku"),
        ("buku2", {"buku-buku", "buku"}, "buku-buku"),
    ],
)
def test_the_first_dictionary_hit_is_the_fewest_steps(word, headwords, hit):
    assert first_hit(word, headwords) == hit


def test_unequal_halves_are_never_split():
    """R4: ``sayur-mayur`` is looked up whole or not at all."""
    assert reduplication_candidates("sayur-mayur") == []
    assert ladder("sayur-mayur") == []


def test_a_disallowed_confix_is_not_stripped():
    """``be-...-i`` never combines: ``berbaiki`` offers ``berbaik`` and ``baiki``, never ``baik``."""
    candidates = ladder("berbaiki")
    assert "baiki" in candidates and "baik" not in candidates


def test_the_ladder_is_capped():
    """26 uncapped candidates; 12 of the 50,000 hermitdave words exceed the cap."""
    assert len(ladder("diperintah")) == MAX_CANDIDATES == 20


@pytest.mark.parametrize(
    ("root", "active"),
    [("beli", "membeli"), ("kirim", "mengirim"), ("tulis", "menulis"), ("sapu", "menyapu"), ("pukul", "memukul"),
     ("ambil", "mengambil"), ("cari", "mencari"), ("lihat", "melihat"), ("ganti", "mengganti"), ("", "")],
)  # fmt: skip
def test_the_active_form_assimilates_the_nasal(root, active):
    assert active_form(root) == active


def test_vowel_variants_are_the_last_rung():
    assert vowel_variants("bener") == ["benar"]
    assert ladder("tau")[-1] == "tahu" and ladder("kalo") == ["kalau"]


def test_a_non_indonesian_word_yields_only_harmless_spellings():
    assert "食べた" not in ladder("食べた")
