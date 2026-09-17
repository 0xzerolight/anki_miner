"""Norwegian Bokmål known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

A Latin gate cannot tell Norwegian from Swedish, Danish or English: those decks' fronts land in the Norwegian
known-words set unless the decks are excluded (the first-switch checklist). Swedish ``en bok`` even folds to the
same ``bok``. The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "nb")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_swedish_danish_and_english_decks_and_folds_norwegian_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Norsk", "ei jente", "Å gå", "et hus")
    _seed(service, "Svenska", "ett äpple", "att gå")
    _seed(service, "Dansk", "en bog")
    _seed(service, "English", "the book")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"jente", "gå", "hus"} <= vocab  # S3: the en/ei/et/å fold at the Anki boundary
    # the contamination the first-switch checklist exists for
    assert {"ett äpple", "att gå", "bog", "the book"} <= vocab
    assert "日本語" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.nb.db"
    db = KnownWordDB(db_path, language="nb")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"jente", "gå", "hus"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Svenska", "Dansk")))
    query = service._build_vocab_query()
    assert '-deck:"Svenska"' in query and '-deck:"Dansk"' in query
