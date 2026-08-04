"""Logging-contract tests for :mod:`anki_miner.services.anki_service`."""

import logging
from dataclasses import replace
from unittest.mock import MagicMock, patch

from anki_miner.models import CardPayload, MediaData
from anki_miner.services.anki_service import AnkiService

_LOGGER = "anki_miner.services.anki_service"


def _mock_response(result=None):
    """Create an AnkiConnect-shaped response."""
    response = MagicMock()
    response.json.return_value = {"result": result, "error": None}
    return response


def test_build_vocab_query_logs_resolved_query(test_config, caplog):
    service = AnkiService(replace(test_config, excluded_decks=("RTK",)))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        query = service._build_vocab_query()

    record = next(record for record in caplog.records if record.getMessage().startswith("Anki vocab query:"))
    assert f"query={query}" in record.getMessage()
    assert record.levelno == logging.DEBUG
    assert record.name == _LOGGER


def test_find_notes_logs_count_without_note_id_list(test_config, caplog):
    service = AnkiService(test_config)
    note_ids = [918273645, 564738291]

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=note_ids),
        ),
    ):
        assert service.find_notes("deck:Mining") == note_ids

    record = next(record for record in caplog.records if record.getMessage().startswith("Anki find notes done:"))
    assert "notes=2" in record.getMessage()
    assert str(note_ids) not in "\n".join(item.getMessage() for item in caplog.records)
    assert record.levelno == logging.DEBUG
    assert record.name == _LOGGER


def test_delete_notes_logs_info_count(test_config, caplog):
    service = AnkiService(test_config)

    with (
        caplog.at_level(logging.INFO, logger=_LOGGER),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=None),
        ),
    ):
        assert service.delete_notes([101, 202]) == 2

    record = next(record for record in caplog.records if record.getMessage().startswith("Anki delete notes done:"))
    assert "notes=2" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == _LOGGER


def test_create_cards_batch_logs_result_summary(test_config, make_tokenized_word, caplog):
    service = AnkiService(test_config)
    payload = CardPayload(
        word=make_tokenized_word(lemma="mine"),
        media=MediaData(),
        definition="definition",
    )

    with (
        caplog.at_level(logging.INFO, logger=_LOGGER),
        patch.object(service, "_store_media_files_batch", return_value=set()),
        patch.object(service, "_upload_dict_media_batch"),
        patch.object(service, "_probe_duplicates", return_value=[False]),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=[303]),
        ),
    ):
        assert service.create_cards_batch([payload]) == [303]

    record = next(record for record in caplog.records if record.getMessage().startswith("Anki create cards done:"))
    assert "created=1" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == _LOGGER
