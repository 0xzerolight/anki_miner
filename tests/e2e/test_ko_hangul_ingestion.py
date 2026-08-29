"""Korean known-word ingestion, through the real AnkiService collection scan.

Drives AnkiService against the loopback FakeAnkiConnect and feeds its result to
a real KnownWordDB. That is the only shape that can catch the `_JAPANESE_RE`
failure (spec 15, "Certain if unaddressed"): a test calling
contains_target_script directly would stay green while the ingestion path
remained hangul-blind.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.known_word_db import KnownWordDB

pytestmark = pytest.mark.network  # real loopback socket; suppresses the tripwire


def _config(language: str, fake_anki, home) -> AnkiMinerConfig:
    return replace(
        AnkiMinerConfig(),
        language=language,
        ankiconnect_url=fake_anki.url,
        known_words_db_path=home / "known_words.db",
    )


def _seed(service: AnkiService, model: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": "LangDeck", "modelName": model, "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_korean_notes_reach_the_ko_known_words_db(fake_anki, isolated_home) -> None:
    config = _config("ko", fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "KoNote", "학생", "漢字語", "ひらがな")

    vocab = service.get_existing_vocabulary()
    assert "학생" in vocab  # the scan gate is the profile's, not _JAPANESE_RE
    # Sino-Korean written in hanja is Korean; a hangul-only gate drops it.
    assert "漢字語" in vocab
    # Japanese kana must not be swept into a Korean collection scan.
    assert "ひらがな" not in vocab

    db_path = resolve_known_words_db_path(config)
    assert db_path.name != "known_words.db", "ko must not share the ja database file"
    db = KnownWordDB(db_path)
    db.initialize()  # creates the known_words table; every read/write needs it
    added, total = db.sync_with_anki(vocab)
    assert (added, total) == (2, 2)
    assert {"학생", "漢字語"} <= db.get_known_words()


def test_japanese_scan_and_database_path_are_unchanged(fake_anki, isolated_home) -> None:
    """Negative control from the other side: the ja gate still rejects hangul."""
    config = _config("ja", fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "JaNote", "日本語", "ひらがな", "학생")

    vocab = service.get_existing_vocabulary()
    assert {"日本語", "ひらがな"} <= vocab
    assert "학생" not in vocab
    assert resolve_known_words_db_path(config) == config.known_words_db_path
