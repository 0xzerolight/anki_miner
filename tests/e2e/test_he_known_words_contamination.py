"""Hebrew known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

The Hebrew script gate cannot tell Hebrew from Yiddish: a Yiddish deck's fronts land in the Hebrew
known-words set unless the deck is excluded, which is exactly what the S15 first-switch deck
checklist exists for. A vocalised Hebrew front and the mined unvocalised lemma are one word
(``dedup_fold`` = the key fold).

Every deck name and front is read from ``tests/fixtures/he/contamination_decks.json``: ar wrote its
Arabic as backslash-u escapes, and the corrected LEAD-BRIEF section 3 says the Write tool destroys
those, so the data lives in a fixture and this module carries no Hebrew character at all.

``FakeAnkiConnect.findNotes`` IGNORES ``-deck:"..."`` negations (fake_ankiconnect.py:210-211), so
the exclusion is pinned on the QUERY that was sent, never on the vocabulary that came back -- the
other way round it would pass for the wrong reason.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.languages.he.script import he_fold
from anki_miner.languages.switching import switch_language
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire

DECKS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "he" / "contamination_decks.json").read_text(encoding="utf-8")
)


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "he")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def _seed_all(service: AnkiService) -> None:
    _seed(service, DECKS["hebrew_deck"], *DECKS["hebrew_fronts"])
    _seed(service, DECKS["yiddish_deck"], *DECKS["yiddish_fronts"])
    _seed(service, DECKS["english_deck"], *DECKS["english_fronts"])
    _seed(service, DECKS["japanese_deck"], *DECKS["japanese_fronts"])


def test_the_fixture_fronts_are_vocalised_so_the_fold_is_what_makes_them_meet():
    """Guards the test below from going vacuous on a fixture someone flattened."""
    for raw, folded in zip(DECKS["hebrew_fronts"], DECKS["hebrew_expected_keys"], strict=True):
        assert raw != folded, raw
        assert he_fold(raw) == folded


def test_a_hebrew_gate_ingests_a_yiddish_deck_and_folds_vocalised_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed_all(service)

    vocab = service.get_existing_vocabulary()

    # S3: niqqud folded at the Anki boundary, so a pointed deck front and a mined bare lemma meet.
    assert set(DECKS["hebrew_expected_keys"]) <= vocab
    # S15: the contamination the first-switch checklist exists for.
    assert set(DECKS["yiddish_expected_keys"]) <= vocab
    # Neither gate passes, so neither reaches the Hebrew set.
    assert not set(DECKS["english_fronts"]) & vocab
    assert not set(DECKS["japanese_fronts"]) & vocab


def test_the_known_words_db_is_the_hebrew_sibling(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed_all(service)

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.he.db"

    db = KnownWordDB(db_path, language="he")
    db.initialize()
    db.sync_with_anki(service.get_existing_vocabulary())
    known = db.get_known_words()
    assert set(DECKS["hebrew_expected_keys"]) <= known


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    """The fake ignores the negation, so the QUERY is what this pins (notes/004 item 3)."""
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=(DECKS["yiddish_deck"],)))
    query = service._build_vocab_query()
    assert f'-deck:"{DECKS["yiddish_deck"]}"' in query
    assert f'-deck:"{DECKS["hebrew_deck"]}"' not in query


def test_an_unexcluded_config_sends_no_negation(fake_anki, isolated_home):
    """The other half: the assertion above would pass on a query that always negates."""
    service = AnkiService(_config(fake_anki, isolated_home))
    assert "-deck:" not in service._build_vocab_query()
