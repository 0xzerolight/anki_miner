"""Slovenian known-word ingestion through the real AnkiService scan (S15 contamination, S3 fold).

Slovene, Croatian and Bosnian are all Latin-script South Slavic languages with shared spellings, so
a Croatian deck lands in the Slovenian known-words set unless it is excluded; the Latin gate cannot
tell them apart. A Cyrillic Serbian deck is the one case the script gate does catch. The fake
AnkiConnect ignores ``-deck:`` negations, so the exclusion itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "sl")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_cyrillic_deck_cannot_reach_the_slovenian_set(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Slovenščina", "knjiga", "Miza", "žival")
    _seed(service, "Српски", "књига")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    fold = get_profile("sl").dedup_fold
    assert fold is not None
    assert {fold("knjiga"), fold("miza"), fold("žival")} <= vocab
    assert not {"књига", "日本語"} & vocab
    assert resolve_known_words_db_path(config).name == "known_words.sl.db"


def test_a_latin_croatian_or_bosnian_deck_needs_the_exclusion(fake_anki, isolated_home):
    """What the script gate cannot do: hr and bs words are Latin, so only excluded_decks keeps them out."""
    service = AnkiService(_config(fake_anki, isolated_home))
    _seed(service, "Slovenščina", "knjiga")
    _seed(service, "Hrvatski", "kruška")
    _seed(service, "Bosanski", "kahva")

    assert {"kruška", "kahva"} <= service.get_existing_vocabulary()

    scoped = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Hrvatski", "Bosanski")))
    query = scoped._build_vocab_query()
    assert '-deck:"Hrvatski"' in query and '-deck:"Bosanski"' in query
