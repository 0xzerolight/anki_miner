"""SK-09 (ruling R24): Kindle vocab.db and Lute/LWT term exports as known-words imports.

The Kindle schema is the one WordDumb documents (docs/contributing/
vocabulary_builder.rst): WORDS(id, word, stem, lang, category, timestamp,
profileid) with category 100 = mastered, and LOOKUPS(... word_key ...).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from anki_miner.services.known_words_import import (
    FORMAT_KEYS,
    KnownWordsImportError,
    parse_known_words_file,
)


def _kindle(tmp_path: Path, rows: list[tuple[str, str, str, str, int]]) -> Path:
    path = tmp_path / "vocab.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE WORDS (id TEXT PRIMARY KEY NOT NULL, word TEXT, stem TEXT, lang TEXT, "
            "category INTEGER DEFAULT 0, timestamp INTEGER DEFAULT 0, profileid TEXT)"
        )
        conn.execute(
            "CREATE TABLE LOOKUPS (id TEXT PRIMARY KEY NOT NULL, word_key TEXT, book_key TEXT, dict_key TEXT, "
            "pos TEXT, usage TEXT, timestamp INTEGER DEFAULT 0)"
        )
        conn.executemany("INSERT INTO WORDS (id, word, stem, lang, category) VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()
    return path


_ROWS = [
    ("ja:食べた", "食べた", "食べる", "ja", 100),
    ("ja:猫", "猫", "猫", "ja", 0),
    ("ja:走る", "走る", "", "ja-JP", 100),
    ("en:cats", "cats", "cat", "en", 100),
]


def test_the_new_format_keys_are_listed():
    assert "kindle_vocab" in FORMAT_KEYS
    assert FORMAT_KEYS[-1] == "generic"


def test_kindle_imports_mastered_words_of_the_mining_language(tmp_path):
    result = parse_known_words_file(_kindle(tmp_path, _ROWS), language="ja")

    assert result.format_key == "kindle_vocab"
    assert result.words == frozenset({"食べる", "走る"})
    assert result.total_entries == 3


def test_kindle_without_a_language_keeps_every_language(tmp_path):
    result = parse_known_words_file(_kindle(tmp_path, _ROWS))

    assert result.words == frozenset({"食べる", "走る", "cat"})
    assert result.total_entries == 4


def test_kindle_with_no_mastered_word_in_the_language_says_so(tmp_path):
    with pytest.raises(KnownWordsImportError) as caught:
        parse_known_words_file(_kindle(tmp_path, _ROWS), language="de")

    assert caught.value.reason == "no_known_words"
    assert caught.value.format_key == "kindle_vocab"


def test_another_sqlite_file_is_not_recognized(tmp_path):
    path = tmp_path / "other.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER)")
        conn.commit()

    with pytest.raises(KnownWordsImportError) as caught:
        parse_known_words_file(path, language="ja")

    assert caught.value.reason == "unrecognized"
