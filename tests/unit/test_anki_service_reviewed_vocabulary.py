"""F-01: the knowledge query sits beside the exclusion query and never replaces it."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import patch

import pytest

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.anki_service import AnkiService

_LOGGER = "anki_miner.services.anki_service"


def _service(config, monkeypatch, answers: dict[str, set[str]]):
    """An AnkiService whose scan answers by query; returns (service, queries seen)."""
    service = AnkiService(config)
    queries: list[str] = []

    def fake_collect(query: str, *, warn_empty: bool = True) -> set[str]:
        queries.append(query)
        answer = answers[query]
        if isinstance(answer, Exception):
            raise answer
        return set(answer)

    monkeypatch.setattr(service, "_collect_first_field_forms", fake_collect)
    return service, queries


def test_the_reviewed_query_adds_only_the_not_new_clause(test_config):
    service = AnkiService(test_config)

    assert service._build_reviewed_query() == "deck:* -is:new"


def test_the_reviewed_query_keeps_the_deck_exclusions(test_config):
    service = AnkiService(replace(test_config, excluded_decks=("Core 2k",)))

    assert service._build_reviewed_query() == 'deck:* -deck:"Core 2k" -is:new'


def test_a_suspended_but_reviewed_card_still_counts_as_known(test_config):
    """Ruling C1: many users suspend cards they know, so suspension never un-knows a word."""
    query = AnkiService(test_config)._build_reviewed_query()

    assert "is:suspended" not in query


def test_the_exclusion_query_is_unchanged(test_config):
    assert AnkiService(test_config)._build_vocab_query() == "deck:*"


def test_reviewed_vocabulary_scans_once_and_caches(test_config, monkeypatch):
    service, queries = _service(test_config, monkeypatch, {"deck:* -is:new": {"猫"}})

    assert service.get_reviewed_vocabulary() == {"猫"}
    assert service.get_reviewed_vocabulary() == {"猫"}
    assert queries == ["deck:* -is:new"]


def test_invalidation_forgets_the_reviewed_set_too(test_config, monkeypatch):
    service, queries = _service(test_config, monkeypatch, {"deck:* -is:new": {"猫"}})
    service.get_reviewed_vocabulary()

    service.invalidate_existing_vocabulary_cache()
    service.get_reviewed_vocabulary()

    assert len(queries) == 2


def test_a_failure_propagates(test_config, monkeypatch):
    service, _ = _service(
        test_config, monkeypatch, {"deck:* -is:new": AnkiConnectionError("Anki sent an unusable reply.")}
    )

    with pytest.raises(AnkiConnectionError):
        service.get_reviewed_vocabulary()


def test_an_empty_reviewed_scan_does_not_claim_an_empty_collection(test_config, caplog):
    service = AnkiService(test_config)

    with (
        patch("anki_miner.services.anki_service.post_action", return_value=[]),
        caplog.at_level(logging.WARNING, logger=_LOGGER),
    ):
        assert service.get_reviewed_vocabulary() == set()

    assert not [r for r in caplog.records if "No notes found" in r.getMessage()]


def test_an_empty_collection_still_warns_on_the_every_card_scan(test_config, caplog):
    service = AnkiService(test_config)

    with (
        patch("anki_miner.services.anki_service.post_action", return_value=[]),
        caplog.at_level(logging.WARNING, logger=_LOGGER),
    ):
        service._collect_first_field_forms("deck:*")

    assert [r for r in caplog.records if "No notes found" in r.getMessage()]
