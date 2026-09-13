"""German known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

The Latin gate cannot tell German from English: an English deck's words land in
the German known-words set unless the deck is excluded. The fake AnkiConnect
ignores ``-deck:`` negations, so the exclusion itself is pinned on the query.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.switching import switch_language
from anki_miner.services.anki_service import AnkiService

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "de")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_an_english_deck_and_folds_german_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Deutsch", "der Hund", "sich freuen", "Straße")
    _seed(service, "English", "house")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"hund", "freuen", "strasse"} <= vocab  # S3: article, reflexive and ß fold at the Anki boundary
    assert "house" in vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.de.db"


def test_an_excluded_deck_is_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English",)))
    assert '-deck:"English"' in service._build_vocab_query()
