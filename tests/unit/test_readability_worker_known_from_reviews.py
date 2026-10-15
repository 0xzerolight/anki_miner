"""F-01 in Readability: known % counts reviewed cards only when the Word Filters box is on."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from tests.unit.test_readability_worker import _FakeParser, _files, _parsed, _run, _services


def _measure(config, tmp_path, *, reviewed=None, reviewed_error=None):
    services = _services(config, _FakeParser({"a.srt": _parsed(("猫", "犬"))}), vocabulary={"猫", "犬"})
    if reviewed_error is not None:
        services.anki_service.get_reviewed_vocabulary = MagicMock(side_effect=reviewed_error)
    else:
        services.anki_service.get_reviewed_vocabulary = MagicMock(return_value=set(reviewed or ()))
    cap, _worker, _shared = _run(config, _files(tmp_path, "a.srt"), services)
    return cap["measured"][0][1], services


def test_off_every_card_counts_and_no_review_query_is_sent(qapp, tmp_path):
    stats, services = _measure(AnkiMinerConfig(), tmp_path, reviewed={"猫"})

    assert stats.new_words == frozenset()
    services.anki_service.get_reviewed_vocabulary.assert_not_called()


def test_on_only_reviewed_cards_count_as_known(qapp, tmp_path):
    stats, _ = _measure(AnkiMinerConfig(known_from_reviewed_cards=True), tmp_path, reviewed={"猫"})

    assert stats.new_words == frozenset({"犬"})


def test_a_failed_review_query_measures_against_every_card(qapp, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="anki_miner.services.known_word_db"):
        stats, _ = _measure(
            AnkiMinerConfig(known_from_reviewed_cards=True),
            tmp_path,
            reviewed_error=AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?"),
        )

    assert stats.new_words == frozenset()
    assert any(r.getMessage().startswith("Known from reviews unavailable:") for r in caplog.records)
