"""F-01: what counts as known when scoring text (Readability, i+1), and its fallback."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from unittest.mock import MagicMock

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.known_word_db import KnownWordDB, collect_knowledge_forms, collect_known_forms

_LOGGER = "anki_miner.services.known_word_db"
_ON = AnkiMinerConfig(known_from_reviewed_cards=True, use_known_words_db=True)


def _db(tmp_path) -> KnownWordDB:
    db = KnownWordDB(tmp_path / "known_words.db")
    db.initialize()
    db.add_words({"猫", "犬"}, source="anki")
    db.add_words({"鳥"}, source="user")
    db.mark_reviewed({"猫"})
    return db


def test_off_it_is_the_every_card_set_and_never_asks_for_reviews(tmp_path):
    db = _db(tmp_path)
    config = replace(_ON, known_from_reviewed_cards=False)
    fetch = MagicMock(return_value=set())

    known = collect_knowledge_forms(db, config, {"魚"}, fetch)

    assert known == collect_known_forms(db, config, {"魚"})
    fetch.assert_not_called()


def test_on_only_reviewed_cards_the_user_list_and_reviewed_cache_rows_count(tmp_path):
    db = _db(tmp_path)

    known = collect_knowledge_forms(db, _ON, {"猫", "犬", "魚", "亀"}, lambda: {"亀"})

    assert known == {"亀", "鳥", "猫"}


def test_on_without_the_cache_the_db_contributes_only_the_user_list(tmp_path):
    db = _db(tmp_path)

    known = collect_knowledge_forms(db, replace(_ON, use_known_words_db=False), {"猫"}, lambda: {"亀"})

    assert known == {"亀", "鳥"}


def test_a_failed_review_query_falls_back_to_every_card_with_a_warning(tmp_path, caplog):
    db = _db(tmp_path)

    def fetch() -> set[str]:
        raise AnkiConnectionError("Anki accepted the connection but did not answer within 30s")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        known = collect_knowledge_forms(db, _ON, {"魚"}, fetch)

    assert known == collect_known_forms(db, _ON, {"魚"})
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Known from reviews unavailable:")]
    assert len(lines) == 1
    assert "fallback=every_card" in lines[0]
    assert "AnkiConnectionError" in lines[0]


def test_mining_records_the_reviews_and_readability_does_not(tmp_path):
    db = _db(tmp_path)

    collect_knowledge_forms(db, _ON, {"猫", "犬"}, lambda: {"犬"})
    assert db.get_reviewed_words() == {"猫"}

    collect_knowledge_forms(db, _ON, {"猫", "犬"}, lambda: {"犬"}, record_reviews=True)
    assert db.get_reviewed_words() == {"猫", "犬"}


def test_no_db_is_the_reviewed_set(tmp_path):
    assert collect_knowledge_forms(None, _ON, {"猫"}, lambda: {"亀"}) == {"亀"}


def test_a_locked_db_degrades_to_what_could_be_read():
    db = MagicMock(spec=KnownWordDB)
    db.is_available.return_value = True
    db.get_words_by_source.return_value = {"鳥"}
    db.get_reviewed_words.side_effect = sqlite3.OperationalError("database is locked")

    known = collect_knowledge_forms(db, _ON, {"猫"}, lambda: {"亀"})

    assert known == {"亀", "鳥"}
