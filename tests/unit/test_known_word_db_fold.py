"""S3: the known-words cache keys on the language's comparison fold."""

from __future__ import annotations

import sqlite3
import unicodedata

from anki_miner.services.known_word_db import KnownWordDB, add_user_known_words
from tests.unit.languages.stub_registry import register_stub_profile


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def test_japanese_keys_are_normalize_lemma_verbatim(tmp_path):
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    nfd = unicodedata.normalize("NFD", "学校")
    db.add_words({nfd}, source="anki")

    assert db.normalize_key(nfd) == unicodedata.normalize("NFC", nfd)
    with sqlite3.connect(tmp_path / "known_words.db") as conn:
        assert conn.execute("SELECT lemma, source FROM known_words").fetchall() == [("学校", "anki")]


def test_a_non_folding_language_keeps_case(tmp_path):
    """``dedup_fold=None`` is NFC only: ja/ko/zh rows never change spelling."""
    db = KnownWordDB(tmp_path / "known_words.ko.db", language="ko")
    db.initialize()
    db.add_words({"Hund"}, source="anki")

    assert db.normalize_key("Hund") == "Hund"
    assert db.get_known_words() == {"Hund"}


def test_a_folding_language_stores_and_matches_folded_keys(tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "zh", dedup_fold=_fold)
    db = KnownWordDB(tmp_path / "known_words.zh.db", language="zh")
    db.initialize()
    db.add_words({"Hund"}, source="anki")

    assert db.get_known_words() == {"hund"}
    assert db.sync_with_anki({"HUND"}) == (0, 1)
    assert db.add_words_with_receipt({"HUND"}, source="mined") == set()
    assert db.remove_words({"HuNd"}) == 1


def test_the_migration_folds_rows_written_before_it(tmp_path, monkeypatch):
    """An unmigrated file adopts the language's key once, merging fold-equal rows."""
    register_stub_profile(monkeypatch, "zh", dedup_fold=_fold)
    path = tmp_path / "known_words.zh.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE known_words (lemma TEXT PRIMARY KEY, source TEXT DEFAULT 'anki', "
            "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.executemany(
            "INSERT INTO known_words (lemma, source, added_at) VALUES (?, ?, ?)",
            [("Katze", "anki", "2026-01-02"), ("KATZE", "user", "2026-01-01")],
        )

    KnownWordDB(path, language="zh").initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT lemma, source, added_at FROM known_words").fetchall() == [
            ("katze", "user", "2026-01-01")
        ]


def test_the_curator_commit_folds_too(tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "zh", dedup_fold=_fold)
    path = tmp_path / "known_words.zh.db"

    assert add_user_known_words(path, {"Katze", "KATZE"}, language="zh") == 1
    assert KnownWordDB(path, language="zh").get_words_by_source("user") == {"katze"}


def test_construction_resolves_nothing(tmp_path, monkeypatch):
    """The fold is looked up on first use, so building the DB stays I/O- and registry-free."""
    import anki_miner.languages.registry as registry

    def boom(code):
        raise AssertionError("resolved at construction")

    monkeypatch.setattr(registry, "get_profile", boom)
    KnownWordDB(tmp_path / "x.db", language="zh")
