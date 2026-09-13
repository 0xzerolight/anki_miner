"""Dutch known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

A Latin gate cannot tell Dutch from English or German: those decks' fronts land in
the Dutch known-words set unless the decks are excluded (the first-switch
checklist). The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion
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
    base = switch_language(AnkiMinerConfig(), "nl")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_english_and_german_decks_and_folds_dutch_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Nederlands", "het boek", "De Hond", "zich vergissen")
    _seed(service, "English", "the dog")
    _seed(service, "Deutsch", "der Hund")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"boek", "hond", "vergissen"} <= vocab  # S3: the de/het/zich fold at the Anki boundary
    assert {"the dog", "der hund"} <= vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.nl.db"
    db = KnownWordDB(db_path, language="nl")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"boek", "hond", "vergissen"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English", "Deutsch")))
    query = service._build_vocab_query()
    assert '-deck:"English"' in query and '-deck:"Deutsch"' in query
