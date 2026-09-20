"""Thai known-word ingestion through the real AnkiService scan (S15 contamination).

Thai's gate is a script gate, so a Latin deck cannot contaminate the set the way it does for a
Latin-script language -- but a Thai deck's own fronts still have to survive the mark rules the th
profile ships: a trailing paiyannoi is part of the headword, a bare repetition mark is not a word,
and the dedup fold strips the zero-width word breaks an e-book front may carry.
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

ZWSP = "\N{ZERO WIDTH SPACE}"


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "th")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_the_thai_script_gate_ingests_thai_and_rejects_everything_else(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "ไทย", "อากาศ", "กรุงเทพฯ", "สวัสดี" + ZWSP + "ครับ")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")
    _seed(service, "Marks", "ๆ", "๒๕๖๗")  # a bare repetition mark and Thai digits are not words

    vocab = service.get_existing_vocabulary()

    assert {"อากาศ", "กรุงเทพฯ"} <= vocab  # the abbreviation mark stays on its headword
    assert "สวัสดีครับ" in vocab  # the dedup fold strips the zero-width word break
    assert "the dog" not in vocab  # a Latin deck cannot reach a Thai script gate
    assert "日本語" not in vocab
    assert "ๆ" not in vocab and "๒๕๖๗" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name == "known_words.th.db"
    db = KnownWordDB(db_path, language="th")
    db.initialize()
    db.sync_with_anki(vocab)
    assert {"อากาศ", "กรุงเทพฯ"} <= db.get_known_words()


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English",)))
    assert '-deck:"English"' in service._build_vocab_query()
