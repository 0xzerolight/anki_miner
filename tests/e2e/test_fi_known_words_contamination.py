"""Finnish known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

A Latin gate cannot tell Finnish from Swedish, Estonian or English: those decks' fronts land in the Finnish
known-words set unless the decks are excluded (the first-switch checklist). Estonian is the close relative whose
spelling overlaps most. The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "fi")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_swedish_estonian_and_english_decks_and_folds_finnish_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Suomi", "Kirja", "talo.", "Äiti")
    _seed(service, "Svenska", "huset")
    _seed(service, "Eesti", "raamat")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"kirja", "talo", "äiti"} <= vocab  # S3: case and trailing punctuation fold at the Anki boundary
    assert {"huset", "raamat", "the dog"} <= vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.fi.db"
    db = KnownWordDB(db_path, language="fi")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"kirja", "talo", "äiti"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Svenska", "Eesti", "English")))
    query = service._build_vocab_query()
    assert '-deck:"Svenska"' in query and '-deck:"Eesti"' in query and '-deck:"English"' in query
