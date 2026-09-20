"""Turkish known-word ingestion through the real AnkiService scan (S15 contamination, S3 Turkish fold).

A Latin gate cannot tell Turkish from English: an English deck's fronts land in the Turkish known-words set unless
the deck is excluded (the first-switch checklist). The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion
itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "tr")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_an_english_deck_and_folds_turkish_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Türkçe", "Kitap", "IŞIK", "İstanbul", "okumak!")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"kitap", "ışık", "istanbul", "okumak"} <= vocab  # S3: the Turkish fold at the Anki boundary
    assert "işik" not in vocab  # what a locale-blind fold would have stored for IŞIK
    assert "the dog" in vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.tr.db"
    db = KnownWordDB(db_path, language="tr")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"kitap", "ışık", "istanbul"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English",)))
    assert '-deck:"English"' in service._build_vocab_query()
