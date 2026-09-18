"""Polish known-word ingestion through the real AnkiService scan (S15 contamination, S3 + się fold).

A Latin gate cannot tell Polish from Czech or Slovak: their decks land in the Polish known-words set unless
excluded. The fake AnkiConnect ignores ``-deck:`` negations, so the exclusion itself is pinned on the query.
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
    base = switch_language(AnkiMinerConfig(), "pl")
    return replace(base, ankiconnect_url=fake_anki.url, known_words_db_path=home / "known_words.db", **overrides)


def _seed(service: AnkiService, deck: str, *expressions: str) -> None:
    service.add_notes_raw(
        [
            {"deckName": deck, "modelName": "Basic", "fields": {"Expression": e, "Meaning": "x"}, "tags": []}
            for e in expressions
        ]
    )


def test_a_latin_gate_ingests_slavic_decks_and_folds_polish_fronts(fake_anki, isolated_home):
    config = _config(fake_anki, isolated_home)
    service = AnkiService(config)
    _seed(service, "Polski", "bać się", "uczyć się", "książka", "Łódź")
    _seed(service, "Čeština", "kniha")
    _seed(service, "Slovenčina", "dom")
    _seed(service, "English", "the dog")
    _seed(service, "Japanese", "日本語")

    vocab = service.get_existing_vocabulary()

    assert {"bać", "uczyć", "książka", "łódź"} <= vocab  # the się fold (P6) and the shared casefold
    # the contamination the first-switch checklist exists for; "the dog" keeps its article because
    # Polish has none, so pl_dedup_fold drops no leading word
    assert {"kniha", "dom", "the dog"} <= vocab
    assert "日本語" not in vocab
    assert resolve_known_words_db_path(config).name == "known_words.pl.db"


def test_the_fold_only_drops_sie_after_another_word(fake_anki, isolated_home):
    """``się`` alone is a front in its own right; the fold must not empty it."""
    fold = get_profile("pl").dedup_fold
    assert fold is not None
    assert fold("bać się") == "bać" and fold("się") == "się"


def test_excluded_decks_are_negated_in_the_scan_query(fake_anki, isolated_home):
    service = AnkiService(_config(fake_anki, isolated_home, excluded_decks=("Čeština", "Slovenčina", "English")))
    query = service._build_vocab_query()
    assert '-deck:"Čeština"' in query and '-deck:"Slovenčina"' in query and '-deck:"English"' in query
