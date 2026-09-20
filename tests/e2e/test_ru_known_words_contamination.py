"""Russian known-word ingestion through the real AnkiService scan (S15 contamination, the ru dedup fold).

A Cyrillic gate cannot tell Russian from Bulgarian or Serbian: their decks land in the Russian known-words
set unless excluded. The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion itself is pinned
on the query.
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

ACUTE = "\N{COMBINING ACUTE ACCENT}"


def _config(fake_anki, home, **overrides) -> AnkiMinerConfig:
    base = switch_language(AnkiMinerConfig(), "ru")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_cyrillic_gate_ingests_other_cyrillic_decks_and_folds_russian_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Русский", f"кни{ACUTE}га", "Ёлка", "читать")
    _seed(service, "Български", "ъгъл")
    _seed(service, "Српски", "кућа")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"книга", "елка", "читать"} <= vocab  # stress, ё and case folded (RU_DEDUP_FOLD)
    assert {"ъгъл", "кућа"} <= vocab  # S15: the Cyrillic gate cannot tell ru from bg or sr
    assert "the dog" not in vocab and "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.ru.db"


def test_the_fold_normalises_stress_yo_and_case(fake_anki, isolated_home):
    """The same fold the scan applies, pinned on its three jobs at once."""
    fold = get_profile("ru").dedup_fold
    assert fold is not None
    assert fold(f"кни{ACUTE}га") == fold("Книга") == "книга"
    assert fold("Ёлка") == fold("елка") == "елка"


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Български", "Српски")))
    query = service._build_vocab_query()
    assert '-deck:"Български"' in query and '-deck:"Српски"' in query
