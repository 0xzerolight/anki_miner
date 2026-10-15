"""F-01: the known-words DB remembers which cached words came from a reviewed card.

The cache serves two sets: every card (the mining exclusion, unchanged) and the
reviewed ones (what Readability and i+1 count as known when the Word Filters box
is on). One additive ``reviewed`` column carries the second.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from anki_miner.services.known_word_db import KnownWordDB

_OLD_SCHEMA = (
    "CREATE TABLE known_words (lemma TEXT PRIMARY KEY, source TEXT DEFAULT 'anki', "
    "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)


def _db(tmp_path: Path, name: str = "known_words.db", language: str = "ja") -> KnownWordDB:
    db = KnownWordDB(tmp_path / name, language=language)
    db.initialize()
    return db


def _columns(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(known_words)")}


def test_a_new_database_has_the_reviewed_column(tmp_path):
    _db(tmp_path)

    assert "reviewed" in _columns(tmp_path / "known_words.db")


def test_an_old_database_gains_the_column_and_keeps_its_rows(tmp_path):
    path = tmp_path / "known_words.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(_OLD_SCHEMA)
        conn.execute("INSERT INTO known_words (lemma, source) VALUES ('猫', 'anki'), ('犬', 'user')")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

    db = KnownWordDB(path)
    db.initialize()

    assert "reviewed" in _columns(path)
    assert db.get_known_words() == {"猫", "犬"}
    assert db.get_reviewed_words() == set()


def test_mark_reviewed_flags_only_rows_that_exist(tmp_path):
    db = _db(tmp_path)
    db.add_words({"猫", "犬"}, source="anki")

    assert db.mark_reviewed({"猫", "鳥"}) == 1
    assert db.get_reviewed_words() == {"猫"}
    assert db.get_known_words() == {"猫", "犬"}


def test_marking_twice_counts_once(tmp_path):
    db = _db(tmp_path)
    db.add_words({"猫"}, source="anki")

    assert db.mark_reviewed({"猫"}) == 1
    assert db.mark_reviewed({"猫"}) == 0


def test_a_user_mark_keeps_the_review_flag(tmp_path):
    """add_user_known_words upgrades source only (ON CONFLICT ... SET source)."""
    db = _db(tmp_path)
    db.add_words({"猫"}, source="anki")
    db.mark_reviewed({"猫"})

    db.add_words({"猫"}, source="user")

    assert db.get_words_by_source("user") == {"猫"}
    assert db.get_reviewed_words() == {"猫"}


def test_rebuild_drops_cached_flags_with_their_rows(tmp_path):
    """clear(preserve_user=True) deletes source != 'user': a cache rebuild forgets both."""
    db = _db(tmp_path)
    db.add_words({"猫"}, source="anki")
    db.add_words({"犬"}, source="user")
    db.mark_reviewed({"猫", "犬"})

    db.clear(preserve_user=True)

    assert db.get_reviewed_words() == {"犬"}


def test_a_write_through_another_instance_refreshes_the_memo(tmp_path):
    db = _db(tmp_path)
    db.add_words({"猫"}, source="anki")
    assert db.get_reviewed_words() == set()

    KnownWordDB(tmp_path / "known_words.db").mark_reviewed({"猫"})

    assert db.get_reviewed_words() == {"猫"}


def test_a_file_without_the_column_reads_as_nothing_reviewed(tmp_path):
    path = tmp_path / "known_words.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(_OLD_SCHEMA)
        conn.execute("INSERT INTO known_words (lemma) VALUES ('猫')")
        conn.commit()

    db = KnownWordDB(path)

    assert db.get_reviewed_words() == set()
    assert db.mark_reviewed({"猫"}) == 0


def test_marks_use_the_language_fold(tmp_path):
    db = _db(tmp_path, "known_words.zh.db", language="zh")
    db.add_words({"頭髮"}, source="anki")

    assert db.mark_reviewed({"頭髮"}) == 1
    assert db.get_reviewed_words() == {db.normalize_key("頭髮")}
