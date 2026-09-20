"""Indonesian known-word ingestion through the real AnkiService scan (S15 contamination, plan D4).

A Latin gate cannot tell Indonesian from English: an English deck lands in the Indonesian known-words set
unless excluded, which is what the first-switch deck checklist is for. The fake AnkiConnect ignores
``-deck:`` negations, so the exclusion itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "id")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_an_english_deck_and_folds_indonesian_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Bahasa Indonesia", "Rumah", "membeli.", "Mengérti", "buku-buku")
    _seed(service, "English", "the dog", "house")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"rumah", "membeli", "mengerti", "buku-buku"} <= vocab  # casefold, trailing dot, marks (D4)
    assert {"the dog", "house"} <= vocab  # the contamination the first-switch checklist exists for
    assert "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.id.db"


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English",)))
    assert '-deck:"English"' in service._build_vocab_query()
