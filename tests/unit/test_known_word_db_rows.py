"""WP-06: the known-words DB lists every row for the browser (word, source, date added)."""

from __future__ import annotations

from anki_miner.services.known_word_db import KnownWordDB, KnownWordRow


def test_rows_carry_their_source_and_date_in_word_order(tmp_path):
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    db.add_words({"猫"}, source="anki")
    db.add_words({"犬"}, source="user")
    db.add_words({"走る"}, source="mined")

    rows = db.list_rows()

    assert [(row.lemma, row.source) for row in rows] == [("犬", "user"), ("猫", "anki"), ("走る", "mined")]
    assert all(isinstance(row, KnownWordRow) for row in rows)
    assert all(len(row.added_at) == 19 and row.added_at[4] == "-" for row in rows)


def test_a_missing_file_lists_nothing_and_creates_nothing(tmp_path):
    path = tmp_path / "never.db"

    assert KnownWordDB(path).list_rows() == []
    assert not path.exists()
