"""Colloquial spellings that are also wty-id-en headwords lead with the colloquial sense (ruling ID-02).

``tau`` ``kalo`` ``liat`` ``abis`` are the everyday spellings of tahu / kalau / lihat / habis, and wty-id-en
also files each as a headword of its own (the Greek letter, a bamboo sieve, clayey soil, the abyssal zone). The
lookup hits, so the variant ladder never runs; the colloquial sense is the row's form pointer, which the
profile's ``sense_rank`` puts first so it survives beside the lemma row and is spliced into the target's meaning.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.id.colloquial import ID_COLLOQUIAL, ID_COLLOQUIAL_CORE, ID_COLLOQUIAL_HOMOGRAPHS
from anki_miner.languages.id.morphology import is_stopword
from anki_miner.languages.id.render import FormalFormHook
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.storage import (
    DictRow,
    bulk_insert,
    create_index,
    lookup,
    lookup_many,
    lookup_with_rules,
    open_readonly,
)
from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry

HOMOGRAPHS = {"tau": "tahu", "kalo": "kalau", "liat": "lihat", "abis": "habis"}


def _form_of(term: str, target: str) -> DictRow:
    return DictRow(
        term=term,
        reading=None,
        content=render_glossary_entry([[target, ["alternative", "colloquial"]]]),
        tags="non-lemma",
    )


def _lemma(term: str, gloss: str, tags: str) -> DictRow:
    return DictRow(term=term, reading=None, content=f"<div>{gloss}</div>", tags=tags)


#: The shapes of wty-id-en 2026.09.19: the unrelated headword's lemma row, then the colloquial pointer.
_ROWS = [
    _lemma("tau", "the letter Τ/τ in the Greek alphabet", "n"),
    _form_of("tau", "tahu"),
    _form_of("tau", "tahu"),
    _lemma("tahu", "to know", "v"),
    _lemma("tahu", "tofu", "n"),
    _lemma("liat", "rubbery; clayey (of soil)", "adj"),
    _lemma("liat", "clay", "n uncount"),
    _form_of("liat", "lihat"),
    _lemma("lihat", "to see", "v"),
    _lemma("abis", "abyssal zone", "n"),
    _form_of("abis", "habis"),
    _lemma("habis", "finished", "adj"),
    _lemma("rumah", "house", "n"),
]


@pytest.fixture
def conn(tmp_path: Path):
    keys = get_profile("id").dict_keys
    db = tmp_path / "d.sqlite"
    create_index(db)
    bulk_insert(db, _ROWS, keys=keys)
    connection = open_readonly(db)
    yield connection
    connection.close()


def _glosses(rows) -> list[str]:
    return [row[0] for row in rows]


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("tau", ["<div>to know</div>", "<div>tofu</div>", "<div>the letter Τ/τ in the Greek alphabet</div>"]),
        ("liat", ["<div>to see</div>", "<div>rubbery; clayey (of soil)</div>", "<div>clay</div>"]),
        ("abis", ["<div>finished</div>", "<div>abyssal zone</div>"]),
    ],
)
def test_the_colloquial_sense_leads_and_the_headword_follows(conn: sqlite3.Connection, word, expected):
    keys = get_profile("id").dict_keys
    assert _glosses(lookup(conn, word, keys=keys, pos="WORD")) == expected
    assert _glosses(lookup_many(conn, [(word, None)], keys=keys, pos={word: "WORD"})[word]) == expected
    assert _glosses(lookup_with_rules(conn, word, keys=keys)) == expected


def test_a_word_without_a_colloquial_pointer_is_unchanged(conn: sqlite3.Connection):
    keys = get_profile("id").dict_keys
    assert _glosses(lookup(conn, "tahu", keys=keys, pos="WORD")) == ["<div>to know</div>", "<div>tofu</div>"]
    assert _glosses(lookup(conn, "rumah", keys=keys, pos="WORD")) == ["<div>house</div>"]


def test_the_rank_promotes_only_a_pointer_to_a_homograph_target():
    keys = get_profile("id").dict_keys
    pointer = render_glossary_entry([["tahu", ["alternative"]]])
    other = render_glossary_entry([["rumah", ["alternative"]]])
    assert keys.sense_rank(pointer, "non-lemma", "WORD") < keys.sense_rank("<div>x</div>", "n", "WORD")
    assert keys.sense_rank(other, "non-lemma", "WORD") == keys.sense_rank("<div>x</div>", "n", "WORD")
    # a lemma row whose text happens to be a target is not a pointer
    assert keys.sense_rank(pointer, "n", "WORD") == keys.sense_rank("<div>x</div>", "n", "WORD")


def test_the_four_rows_are_curated_core_rows_and_fill_formal():
    assert dict(ID_COLLOQUIAL_HOMOGRAPHS) == HOMOGRAPHS
    assert ID_COLLOQUIAL_HOMOGRAPHS.items() <= ID_COLLOQUIAL_CORE.items() <= ID_COLLOQUIAL.items()
    config = AnkiMinerConfig()
    for front, formal in HOMOGRAPHS.items():
        assert FormalFormHook().render(SimpleNamespace(mined_form=front), config=config) == {"formal_form": formal}


def test_kalo_joins_the_stopword_tier_with_kalau_and_the_others_stay_vocabulary():
    assert is_stopword("kalo")
    assert not any(is_stopword(front) for front in ("tau", "liat", "abis"))
