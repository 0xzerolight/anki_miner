"""Croatian known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

The sh dictionary answers Serbian and Bosnian too, and the Latin gate cannot tell the three apart: a
Latin-script Serbian or Bosnian deck lands in the Croatian known-words set unless it is excluded. A
Cyrillic Serbian deck is the one case the script gate does catch. The fake AnkiConnect ignores ``-deck:``
negations, so the exclusion itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "hr")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_cyrillic_serbian_deck_cannot_reach_the_croatian_set(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Hrvatski", "knjiga", "Stol", "\u0111ak")
    _seed(service, "\u0421\u0440\u043f\u0441\u043a\u0438", "\u043a\u045a\u0438\u0433\u0430")
    _seed(service, "Japanese", "\u65e5\u672c\u8a9e")

    vocab = service.get_existing_vocabulary()

    fold = get_profile("hr").dedup_fold
    assert fold is not None
    assert {fold("knjiga"), fold("stol"), fold("\u0111ak")} <= vocab
    assert not {"\u043a\u045a\u0438\u0433\u0430", "\u65e5\u672c\u8a9e"} & vocab
    assert resolve_known_words_db_path(config).name == "known_words.hr.db"


def test_a_latin_serbian_or_bosnian_deck_needs_the_exclusion(fake_anki, isolated_home):
    """What the script gate cannot do: sr-Latn and bs words are Latin, so only excluded_decks keeps them out."""
    service = AnkiService(_config(fake_anki, isolated_home))
    _seed(service, "Hrvatski", "knjiga")
    _seed(service, "Srpski (latinica)", "hleb")
    _seed(service, "Bosanski", "kahva")

    assert {"hleb", "kahva"} <= service.get_existing_vocabulary()

    scoped = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Srpski (latinica)", "Bosanski")))
    query = scoped._build_vocab_query()
    assert '-deck:"Srpski (latinica)"' in query and '-deck:"Bosanski"' in query
