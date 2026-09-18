"""Greek known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

Unlike every Latin-script language, the Greek gate tells its decks apart: a Latin- or
Cyrillic-script deck contributes nothing to the Greek known-words set. The fake AnkiConnect ignores
``-deck:`` negations, so the exclusion itself is pinned on the query.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.anki_service import AnkiService

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "el")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_the_greek_gate_rejects_other_scripts_and_folds_greek_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Ελληνικά", "το βιβλίο", "Η Οδός", "ένας φίλος")
    _seed(service, "Español", "hola")
    _seed(service, "Русский", "книга")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    fold = get_profile("el").dedup_fold
    assert fold is not None
    assert {fold("βιβλίο"), fold("οδός"), fold("φίλος")} <= vocab
    assert not {"hola", "книга", "日本語"} & vocab
    assert resolve_known_words_db_path(config).name == "known_words.el.db"


def test_an_excluded_deck_is_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Ελληνικά (παλιό)",)))
    assert '-deck:"Ελληνικά (παλιό)"' in service._build_vocab_query()
