"""Arabic known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

The Arabic gate cannot tell Arabic from Persian (or Urdu): a Persian deck's fronts land in the Arabic
known-words set unless the deck is excluded (the first-switch checklist). A vocalised Arabic front and
the mined unvocalised lemma are one word (``dedup_fold`` = the key fold). The fake AnkiConnect ignores
``-deck:`` negations, so the exclusion itself is pinned on the query. Every Arabic, Persian and
Japanese literal is a ``\\uXXXX`` escape: a raw RTL run reorders on screen inside a source line.
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
    base = switch_language(AnkiMinerConfig(), "ar")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_an_arabic_gate_ingests_a_persian_deck_and_folds_vocalised_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(
        service,
        "Arabic",
        "\u0643\u0650\u062a\u064e\u0627\u0628\u064c",
        "\u0645\u062f\u0631\u0633\u0629",
        "\u0630\u064e\u0647\u064e\u0628\u064e",
    )  # kitaabun, madrasa, dhahaba
    _seed(
        service, "Persian", "\u06a9\u062a\u0627\u0628\u062e\u0627\u0646\u0647", "\u0645\u06cc\u200c\u0631\u0648\u0645"
    )  # ketaabkhaane, mi-ravam (with a ZWNJ)
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "\u65e5\u672c\u8a9e")

    vocab = service.get_existing_vocabulary()

    # S3: tashkeel folded at the Anki boundary.
    assert {"\u0643\u062a\u0627\u0628", "\u0645\u062f\u0631\u0633\u0629", "\u0630\u0647\u0628"} <= vocab
    # S15: the contamination the first-switch checklist exists for; the ZWNJ folds out of the key.
    assert {"\u06a9\u062a\u0627\u0628\u062e\u0627\u0646\u0647", "\u0645\u06cc\u0631\u0648\u0645"} <= vocab
    assert "the dog" not in vocab and "\u65e5\u672c\u8a9e" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.ar.db"
    db = KnownWordDB(db_path, language="ar")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"\u0643\u062a\u0627\u0628", "\u0645\u062f\u0631\u0633\u0629", "\u0630\u0647\u0628"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Persian",)))
    assert '-deck:"Persian"' in service._build_vocab_query()
