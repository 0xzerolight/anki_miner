"""Hungarian known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

A Latin gate cannot tell Hungarian from English or German: those decks' fronts
land in the Hungarian known-words set unless the decks are excluded (the
first-switch checklist). The fake AnkiConnect ignores ``-deck:`` negations, so
the exclusion itself is pinned on the query.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.switching import switch_language
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "hu")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_english_and_german_decks_and_folds_hungarian_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Magyar", "a ház", "Az Alma", "egy űrhajó", "elolvas")
    _seed(service, "English", "the dog")
    _seed(service, "Deutsch", "der Hund")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"ház", "alma", "űrhajó", "elolvas"} <= vocab  # S3: a/az/egy fold at the Anki boundary
    assert {"the dog", "der hund"} <= vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.hu.db"
    db = KnownWordDB(db_path, language="hu")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"ház", "alma", "űrhajó"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English", "Deutsch")))
    query = service._build_vocab_query()
    assert '-deck:"English"' in query and '-deck:"Deutsch"' in query
