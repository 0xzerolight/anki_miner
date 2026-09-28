"""The wty row rank of CasefoldDictKeys: the token's own part of speech leads, proper-name rows trail.

wty gives every row score 0 and sequence 0, so without a rank the import id orders a word's rows,
and the casefolded key files a place name or a surname under the common word (SHARED-02).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.storage import (
    DictRow,
    bulk_insert,
    create_index,
    lookup,
    lookup_many,
    open_readonly,
)

KEYS = CasefoldDictKeys()
DE_KEYS = get_profile("de").dict_keys
ROW = "<li>any content</li>"


def _rank(tags: str, pos: str | None) -> int:
    return KEYS.sense_rank(ROW, tags, pos)


@pytest.mark.parametrize("tags", ["name", "name neut", "name 🇬🇧 obs sl", "prop-n", "surn"])
@pytest.mark.parametrize("pos", ["NOUN", "VERB", "ADJ", "ADV", None])
def test_a_proper_name_row_trails_a_common_row(tags, pos):
    assert _rank(tags, pos) > _rank("n", pos)
    assert _rank(tags, pos) > _rank("non-lemma", pos)


def test_a_proper_noun_token_keeps_its_name_rows_in_place():
    assert _rank("name", "PROPN") == _rank("n", "PROPN") == _rank("adj", "PROPN")


@pytest.mark.parametrize("tags", ["name neut no-pl prop-n", "name dated neut no-pl prop-n", "name fem no-pl"])
@pytest.mark.parametrize("pos", ["NOUN", "VERB", "ADJ", "ADV", "PROPN", None])
def test_a_no_plural_name_row_ranks_as_a_noun_row(tags, pos):
    """wty-de-en files a language's name as ``name neut no-pl prop-n``: de Russisch is a noun (E2E-1-01)."""
    assert _rank(tags, pos) == _rank("n", pos)


def test_a_country_row_still_trails_a_noun_row():
    """Countries, places and people carry no ``no-pl``: their rows stay demoted."""
    assert _rank("name neut prop-n", "NOUN") > _rank("n neut strong", "NOUN")
    assert _rank("name neut prop-n", "NOUN") > _rank("adj", "NOUN")


@pytest.mark.parametrize(
    "pos, own, other",
    [
        ("VERB", "v", "n"),
        ("VERB", "v weak", "masc n strong"),
        ("NOUN", "n", "v"),
        ("NOUN", "n neut strong", "adj"),
        ("ADV", "adv", "adj"),
        ("ADV", "adv arch dialect", "n"),
    ],
)
def test_a_row_of_the_token_pos_leads(pos, own, other):
    assert _rank(own, pos) < _rank(other, pos)


def test_an_interjection_row_counts_as_a_noun():
    """spaCy tags thanks-words NOUN: fr "merci" must keep "thank you" ahead of "mercy"."""
    assert _rank("intj", "NOUN") == _rank("n", "NOUN")


def test_adjectives_are_not_promoted():
    """ADJ promotion made more cards worse than better (fr "neuf heures" -> "brand new")."""
    assert _rank("adj", "ADJ") == _rank("num", "ADJ") == _rank("n", "ADJ")


def test_only_the_first_tag_names_the_part_of_speech():
    assert _rank("masc n strong", "NOUN") == _rank("adj", "NOUN")
    assert _rank("non-lemma n", "NOUN") == _rank("adj", "NOUN")


def test_form_of_rows_are_left_among_the_other_rows():
    """Reorder only: a non-lemma row is neither promoted nor demoted here."""
    for pos in ("NOUN", "VERB", "ADV", None):
        assert _rank("non-lemma", pos) == _rank("pron", pos)


def test_no_token_leaves_only_the_name_demotion():
    assert _rank("n", None) == _rank("v", None) == _rank("adv", None)
    assert _rank("name", None) > _rank("n", None)


@pytest.mark.parametrize("code", ["en", "de", "nl", "sv", "fr", "it", "es", "pt", "ru", "pl", "tr", "ro"])
def test_every_wty_latin_and_cyrillic_profile_ranks_its_rows(code):
    assert isinstance(get_profile(code).dict_keys, CasefoldDictKeys)


def _wty(term: str, *rows: tuple[str, str]) -> list[DictRow]:
    """wty's shape: one row per part of speech, all at score 0 and sequence 0, in import order."""
    return [
        DictRow(term=term, reading=None, content=f"<div>{gloss}</div>", tags=tags, sequence=0) for tags, gloss in rows
    ]


@pytest.fixture(scope="module")
def wty_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("wty") / "index.sqlite"
    create_index(db)
    bulk_insert(
        db,
        [
            *_wty("airport", ("name", "A census-designated place"), ("n", "An airfield")),
            *_wty("komen", ("name neut", "Comines (a city in Belgium)"), ("v", "to come")),
            *_wty("essen", ("v strong", "to eat"), ("n neut strong", "eating; meal; food")),
            *_wty("merci", ("intj", "thank you"), ("n fem", "mercy")),
            *_wty("adam", ("name", "a male given name"), ("n", "man")),
            *_wty("pick", ("n", "A pickaxe"), ("v", "To choose"), ("name", "A surname"), ("non-lemma n", "pick 'em")),
        ],
    )
    return db


@pytest.mark.parametrize(
    "word, pos, lead",
    [
        ("airport", "NOUN", "An airfield"),
        ("komen", "VERB", "to come"),
        ("essen", "NOUN", "eating; meal; food"),
        ("essen", "VERB", "to eat"),
        ("merci", "NOUN", "thank you"),
        ("adam", "NOUN", "man"),
        ("adam", "PROPN", "a male given name"),
        ("pick", "VERB", "To choose"),
        ("pick", None, "A pickaxe"),
    ],
)
def test_the_card_opens_on_the_right_row(wty_db, word, pos, lead):
    conn = open_readonly(wty_db)
    try:
        rows = lookup_many(conn, [(word, word)], pos={word: pos} if pos else None, keys=KEYS)[word]
        assert rows == lookup(conn, word, word, pos=pos, keys=KEYS)
    finally:
        conn.close()
    assert rows[0][0] == f"<div>{lead}</div>"


#: wty-de-en's rows under two language names, in their import order (E2E-1-01).
DE_LANGUAGE_ROWS = {
    "Spanisch": (
        ("adj", "Spanish"),
        ("name neut no-pl prop-n", "the Spanish language"),
        ("n neut no-pl strong", "Ruy Lopez (chess opening)"),
    ),
    "Russisch": (
        ("name neut no-pl prop-n", "Russian (language)"),
        ("n neut no-pl sl strong", "thighing, intercrural sex"),
        ("adj reltnl", "Russian"),
    ),
}


@pytest.fixture(scope="module")
def de_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("wty-de") / "index.sqlite"
    create_index(db)
    bulk_insert(db, [row for term, rows in DE_LANGUAGE_ROWS.items() for row in _wty(term, *rows)], keys=DE_KEYS)
    return db


@pytest.mark.parametrize(
    "word, pos, lead",
    [
        ("Spanisch", "NOUN", "the Spanish language"),
        ("Russisch", "NOUN", "Russian (language)"),
        ("spanisch", "ADJ", "Spanish"),
    ],
)
def test_a_german_language_noun_opens_on_the_language(de_db, word, pos, lead):
    conn = open_readonly(de_db)
    try:
        rows = lookup_many(conn, [(word, None)], pos={word: pos}, keys=DE_KEYS)[word]
        assert rows == lookup(conn, word, None, pos=pos, keys=DE_KEYS)
    finally:
        conn.close()
    assert rows[0][0] == f"<div>{lead}</div>"
    assert sorted(content for content, _tags, _seq in rows) == sorted(
        f"<div>{gloss}</div>" for _tags, gloss in DE_LANGUAGE_ROWS[word.capitalize()]
    )


def test_rows_are_only_reordered_never_dropped(wty_db):
    """The rank drops nothing: both paths lose only the form row, which a lemma row shadows (storage)."""
    conn = open_readonly(wty_db)
    try:
        ranked = lookup(conn, "pick", None, pos="VERB", keys=KEYS)
        plain = lookup(conn, "pick", None)  # no rank: the import order
    finally:
        conn.close()
    assert sorted(ranked) == sorted(plain)
    assert [content for content, _tags, _seq in ranked] == [
        "<div>To choose</div>",
        "<div>A pickaxe</div>",
        "<div>A surname</div>",
    ]
