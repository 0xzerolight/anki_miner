"""Cantonese known-word ingestion through the real AnkiService scan (S15 contamination).

The documented risk, made explicit rather than fixed: yue's gate is a Han gate,
so a Mandarin deck IS ingested as known Cantonese unless the user excludes it.
S15's first-switch checklist is what carries that load -- every deck ticked
except the target -- and ``excluded_decks`` is already language-scoped. The
per-profile pre-tick predicate that would make the dialog self-explanatory is
deferred (spec section 9); ``yue/data/simplified_only.txt`` is the data it is
owed.

``network``-marked for the loopback socket only: this test downloads nothing.
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

CANTONESE = ("嘅", "睇咗", "好好睇", "喺邊度")
MANDARIN = ("电影", "他们", "知道")


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "yue")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_the_han_gate_ingests_colloquial_cantonese(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home))
    _seed(service, "Cantonese", *CANTONESE)
    _seed(service, "English", "the dog")
    _seed(service, "Hangul", "학생")

    vocab = service.get_existing_vocabulary()

    assert set(CANTONESE) <= vocab
    assert "the dog" not in vocab  # a Latin deck cannot reach a Han gate
    assert "학생" not in vocab


def test_a_mandarin_deck_IS_ingested_when_it_is_not_excluded(fake_anki, isolated_home):
    # Asserted because it is the BEHAVIOUR, not a bug: a Han script gate cannot
    # tell Cantonese from Mandarin, which is the S15 risk row for this language.
    service = AnkiService(_config(fake_anki, isolated_home))
    _seed(service, "Cantonese", *CANTONESE)
    _seed(service, "Mandarin", *MANDARIN)

    vocab = service.get_existing_vocabulary()

    assert set(MANDARIN) <= vocab


def test_excluding_the_mandarin_deck_negates_it_in_the_scan_query(fake_anki, isolated_home):
    """The exclusion is applied by Anki, in the query, not by us after the fact.

    Asserted on the QUERY: ``FakeAnkiConnect.findNotes`` tolerates but ignores
    ``-deck:"..."`` negations (``fake_ankiconnect.py:211-212``), so a vocabulary
    assertion here would be asserting the fake's behaviour, not Anki's.
    """
    config = _config(fake_anki, isolated_home, excluded_decks=("Mandarin",))
    service = AnkiService(config)
    _seed(service, "Cantonese", *CANTONESE)
    _seed(service, "Mandarin", *MANDARIN)

    query = service._build_vocab_query()
    assert '-deck:"Mandarin"' in query
    assert query.startswith("deck:*")  # whole collection, minus the exclusions
    # And the scan still works with the exclusion in place.
    assert set(CANTONESE) <= service.get_existing_vocabulary()


def test_the_known_words_db_is_the_yue_sibling(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Cantonese", *CANTONESE)

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.yue.db"

    db = KnownWordDB(db_path, language="yue")
    db.initialize()
    db.sync_with_anki(service.get_existing_vocabulary())
    assert set(CANTONESE) <= db.get_known_words()
