"""Vietnamese known-word ingestion through the real AnkiService scan (S15 contamination, one fold).

Vietnamese is Latin-script: an English deck is kept out only by the text gate (a Vietnamese letter,
or ≥60 % of ASCII words in the 200-syllable table). What gets through is the documented collision:
a one-word English front that is also a stripped Vietnamese syllable (``ten`` = tên). The fold makes a
new-style deck front (``Hoà bình``) meet the mined old-style word.
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
    base = switch_language(AnkiMinerConfig(), "vi")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_an_english_deck_stays_out_but_for_the_documented_collision(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Tiếng Việt", "Hoà bình", "bác sĩ", "đẹp đẽ", "khong biet")
    _seed(service, "English", "the dog", "apple", "I love you", "ten")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    fold = get_profile("vi").dedup_fold
    assert fold is not None
    assert {fold("hòa bình"), fold("bác sĩ"), fold("đẹp đẽ"), fold("khong biet")} <= vocab
    assert not {"the dog", "apple", "i love you", "日本語"} & vocab
    assert "ten" in vocab  # S15's documented collision: a stripped syllable (tên)
    assert resolve_known_words_db_path(config).name == "known_words.vi.db"


def test_an_excluded_deck_is_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("English",)))
    assert '-deck:"English"' in service._build_vocab_query()
