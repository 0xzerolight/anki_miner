"""vi review fixes: wty rows ranked by the token's VLSP tag, letter rows last.

Rows are wty-vi-en's own (revision 2026.09.19): tags, first gloss and etymology, shortened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    lookup,
    lookup_many,
    open_readonly,
    write_meta,
)

KEYS = get_profile("vi").dict_keys


def _content(gloss: str, etymology: str = "") -> str:
    """One wty-vi-en row as the importer renders it: an Etymology preamble, then the glosses."""
    preamble = (
        '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Etymology"><summary class="gloss-sc-summary" '
        'data-sc-content="summary-entry">Etymology</summary><div class="gloss-sc-div" '
        f'data-sc-content="Etymology-content">{etymology}</div></details></div>'
        if etymology
        else ""
    )
    return (
        f'<li class="gloss-item"><div class="gloss-content"><div class="gloss-sc-div">{preamble}</div>'
        '<ol class="gloss-sc-ol" data-sc-content="glosses"><li class="gloss-sc-li"><div class="gloss-sc-div">'
        f"{gloss}</div></li></ol></div></li>"
    )


#: term -> rows in wty-vi-en's import order: (tags, first gloss, etymology).
WTY_VI: dict[str, list[tuple[str, str, str]]] = {
    "là": [
        ("n", "fine silk", "Non-Sino-Vietnamese reading of Chinese 羅 (SV: la)."),
        ("v cop", "to be", "From Proto-Vietic *laː (“to work”). Doublet of làm."),
        ("conj", "Used to indicate something is bound to happen", "From Proto-Vietic *laː (“to work”)."),
        ("", "reduplicant of lạ, only used in là lạ", ""),
    ],
    "bị": [
        ("n", "big sack made from sedge", "Sino-Vietnamese word from 被."),
        ("v", "particle denoting the subject is negatively affected", "Sino-Vietnamese word from 被."),
    ],
    "sống": [
        ("n", "spine", "From Proto-Vietic *k-roːŋʔ (“back of the blade”)."),
        ("v", "to live", "From Proto-Vietic *k-roːŋʔ (“alive; raw”)."),
        ("adj", "living", "From Proto-Vietic *k-roːŋʔ (“alive; raw”)."),
    ],
    "bớt": [("n", "birthmark", ""), ("v", "to diminish, to cut down", "Cognate with Muong pớch.")],
    "ô": [
        ("char", "The 18th letter of the Vietnamese alphabet", "Borrowed from Portuguese ô."),
        ("n", "The name of the Latin script letter Ô/ô.", "Borrowed from Portuguese ô."),
        ("n", "umbrella", ""),
        ("adj", "black", "Sino-Vietnamese word from 烏 (“crow; black”)."),
        ("char", "The eighteenth letter of the Vietnamese alphabet, called ô", ""),
    ],
    "bờ": [("n", "shore, bank", ""), ("n", "The name of the Latin script letter B/b.", "")],
    "cái": [
        ("", "Indicates an inanimate, tangible thing", "Cognate with Muong Bi cảy."),
        ("n", "utensil for", "Cognate with Muong Bi cảy."),
        ("adj", "female", "From Proto-Vietic *-keːʔ (“woman; female”)."),
    ],
    "người": [
        ("n", "person; people", ""),
        ("", "indicates people, except infants", ""),
        ("name", "a surname", ""),
    ],
    "đồng": [
        ("n", "field", "Borrowed from Tai; compare Tày tô̱ng, Thai ทุ่ง (tûng), and Chinese 垌 (dòng)."),
        ("n", "medium (one who communicates with spirits)", "may also likely be a Sino-Vietnamese word from 童"),
        ("n", "copper", "Sino-Vietnamese word from 銅 (“copper”)."),
        ("name", "a unisex given name from Chinese", "."),
    ],
    "bố": [
        ("n", "father", "From Late Proto-Vietic *poːʔ (“father”), related to Chinese 父 (SV: phụ)."),
        ("n", "burlap; jute fabric", "Sino-Vietnamese word from 布. Doublet of vải."),
        ("pron", "I/me, your father", "From Late Proto-Vietic *poːʔ (“father”)."),
    ],
    "khi": [
        ("adv", "when", "Cognate with Muong Bi khây. Often compared to Chinese 期 (MC gi)."),
        ("v", "to slight, to despise", "Sino-Vietnamese word from 欺."),
    ],
    "lương": [
        ("adj dated", "non-Christian", "Sino-Vietnamese word from 良."),
        ("n", "victuals; food supplies; salary", "Sino-Vietnamese word from 糧 (“provisions”)."),
        ("name", "a surname", "Sino-Vietnamese word from 梁."),
    ],
    "bác sĩ": [("n", "doctor", "Sino-Vietnamese word from 博士.")],
}


@pytest.fixture(scope="module")
def wty_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("wty-vi") / "index.sqlite"
    create_index(db)
    bulk_insert(
        db,
        [
            DictRow(term=term, reading=None, content=_content(gloss, etymology), tags=tags, sequence=0)
            for term, rows in WTY_VI.items()
            for tags, gloss, etymology in rows
        ],
    )
    write_meta(db, {"schema_version": str(SCHEMA_VERSION), "source_name": "wty-vi-en"})
    return db


def _rows(db: Path, word: str, pos: str | None) -> list[tuple[str, str, int | None]]:
    conn = open_readonly(db)
    try:
        rows = lookup(conn, word, None, pos=pos, keys=KEYS)
        assert rows == lookup_many(conn, [(word, None)], pos={word: pos} if pos else None, keys=KEYS)[word]
    finally:
        conn.close()
    return rows


# --- the row rank: the token's word class leads, letter rows trail --------------------------------


@pytest.mark.parametrize(
    ("word", "pos", "lead"),
    [
        ("là", "V", "to be"),
        ("bị", "V", "particle denoting the subject is negatively affected"),
        ("sống", "V", "to live"),
        ("sống", "A", "living"),
        ("bớt", "V", "to diminish, to cut down"),
        ("ô", "N", "umbrella"),
        ("bờ", "N", "shore, bank"),
        ("cái", "Nc", "Indicates an inanimate, tangible thing"),
        ("cái", "N", "utensil for"),
        ("người", "Nc", "indicates people, except infants"),
        ("người", "N", "person; people"),
    ],
)
def test_the_card_opens_on_the_row_of_the_token_word_class(wty_db, word, pos, lead):
    """là/bị/sống/bớt opened on an unrelated noun row, ô on its letter row (THVI-05)."""
    assert f'<div class="gloss-sc-div">{lead}</div>' in _rows(wty_db, word, pos)[0][0]


def test_letter_rows_trail_even_with_no_token_in_hand(wty_db):
    """The variant fallback ranks with no POS: ô still opens on a word, never on a letter."""
    glosses = [content for content, _tags, _seq in _rows(wty_db, "ô", None)]
    assert "umbrella" in glosses[0]
    assert ["letter" in content for content in glosses] == [False, False, True, True, True]


def test_rows_are_only_reordered_never_dropped(wty_db):
    for word, rows in WTY_VI.items():
        for pos in ("V", "N", "A", "Nc", None):
            assert sorted(tags for _content, tags, _seq in _rows(wty_db, word, pos)) == sorted(t for t, _g, _e in rows)


@pytest.mark.parametrize("pos", ["V", "N", "A", "Nc", "Nu", "R", None])
def test_a_proper_name_row_trails_unless_the_token_is_a_proper_noun(pos):
    assert KEYS.sense_rank("<li>x</li>", "name", pos) > KEYS.sense_rank("<li>x</li>", "pron", pos)
    assert KEYS.sense_rank("<li>x</li>", "name", "Np") == KEYS.sense_rank("<li>x</li>", "n", "Np")


def test_a_tag_outside_the_map_ranks_no_row_ahead():
    """R (adverb), E, P ... are not mapped: only names and letters move."""
    for tags in ("n", "v", "adj", "adv", "", "non-lemma"):
        assert KEYS.sense_rank("<li>x</li>", tags, "R") == 1
