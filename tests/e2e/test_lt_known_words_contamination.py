"""Lithuanian known-word ingestion through the real AnkiService scan (S15 contamination, D-1 fold).

Lithuanian is Latin-script, so a Latvian or Polish deck is NOT excluded by the script gate — the answer is the
per-language database (``known_words.lt.db``) plus the user's own deck exclusions. What the fold does add: a
deck front written with stress marks (``knygà``) meets the mined ``knyga``.
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
    base = switch_language(AnkiMinerConfig(), "lt")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_stressed_deck_fronts_fold_onto_the_mined_words(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Lietuvių", "kny\u0300ga", "Stalas", "žmogus")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    fold = get_profile("lt").dedup_fold
    assert fold is not None
    assert {fold("knyga"), fold("stalas"), fold("žmogus")} <= vocab
    assert "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.lt.db"


def test_an_excluded_deck_is_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Latviešu",)))
    assert '-deck:"Latviešu"' in service._build_vocab_query()
