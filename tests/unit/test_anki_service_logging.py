"""Logging-contract tests for :mod:`anki_miner.services.anki_service`."""

import logging
from collections import Counter
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
    assert "failed_words=-" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == _LOGGER


def test_create_cards_batch_names_the_words_addnotes_refused(test_config, make_tokenized_word, caplog):
    """A partial run names the refused words, and asks Anki nothing extra."""
    service = AnkiService(test_config)
    payloads = [
        CardPayload(word=make_tokenized_word(lemma=form), media=MediaData(), definition="definition")
        for form in ("会う", "見る")
    ]

    with (
        caplog.at_level(logging.INFO, logger=_LOGGER),
        patch.object(service, "_store_media_files_batch", return_value=set()),
        patch.object(service, "_upload_dict_media_batch"),
        patch.object(service, "_probe_duplicates", return_value=[False, False]),
        patch.object(service, "_explain_null_slots") as explain,
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=[404, None]),
        ),
    ):
        assert service.create_cards_batch(payloads) == [404]

    message = next(r for r in caplog.records if r.getMessage().startswith("Anki create cards done:")).getMessage()
    assert "created=1" in message
    assert f"failed_words={payloads[1].word.mined_form}" in message
    explain.assert_not_called()


def test_create_cards_batch_reports_why_when_nothing_was_created(test_config, make_tokenized_word, caplog):
    """A run that creates nothing carries AnkiConnect's own refusal reasons."""
    service = AnkiService(test_config)
    payloads = [
        CardPayload(word=make_tokenized_word(lemma=form), media=MediaData(), definition="definition")
        for form in ("会う", "見る")
    ]

    with (
        caplog.at_level(logging.INFO, logger=_LOGGER),
        patch.object(service, "_store_media_files_batch", return_value=set()),
        patch.object(service, "_upload_dict_media_batch"),
        patch.object(service, "_probe_duplicates", return_value=[False, False]),
        patch.object(
            service,
            "_explain_null_slots",
            return_value=Counter({"cannot create note because it is a duplicate": 2}),
        ),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=[None, None]),
        ),
    ):
        assert service.create_cards_batch(payloads) == []

    message = next(r for r in caplog.records if r.getMessage().startswith("Anki create cards done:")).getMessage()
    assert "created=0" in message
    assert f"failed_words={payloads[0].word.mined_form}" in message
    assert "2xcannot create note because it is a duplicate" in message


def test_explain_null_slots_never_raises_and_counts_reasons(test_config, caplog):
    """The diagnostic probe is best-effort: a broken probe logs, never propagates."""
    service = AnkiService(test_config)

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            return_value=_mock_response(result=[{"canAdd": False, "error": "empty first field"}]),
        ),
    ):
        assert service._explain_null_slots([{"deckName": "D"}]) == {"empty first field": 1}

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER),
        patch(
            "anki_miner.services._ankiconnect.requests.post",
            side_effect=RuntimeError("probe exploded"),
        ),
    ):
        assert service._explain_null_slots([{"deckName": "D"}]) == {}
